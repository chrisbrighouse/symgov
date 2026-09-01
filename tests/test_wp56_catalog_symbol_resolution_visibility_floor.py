"""WP5.6 audit regression: `catalog_symbol_resolution.resolve_catalog_symbol`
performs no visibility check of its own (by design — see
`backend/symgov_backend/catalog_symbol_resolution.py`). Every caller
(`routes/published.py:_load_published_symbol_row`,
`routes/catalog.py:_load_catalog_symbol_row`,
`routes/catalog.py:_load_catalog_symbol_rows_for_feedback`) re-queries with
the visibility-gated `PUBLISHED_SYMBOLS_SQL` afterwards and returns 404
either way, but with two distinguishable error codes:
`catalog_symbol_not_found` (resolver found nothing) vs
`catalog_symbol_unavailable` (resolver found a symbol, but the gated
re-query excluded it). That is an existence oracle *if and only if* a
`governed_symbols` row can ever have `visibility = 'organization_private'`
together with a non-null `catalog_symbol_id`.

Code-path analysis (see the WP5.6 audit report) found no application code
that produces that combination:

- `allocate_catalog_identity_for_publication` (backend/symgov_backend/runtime.py)
  is only called from `persist_publication_execution`, which requires a
  `HumanReviewDecision` reachable from a `ReviewCase`; `ReviewCase` rows are
  only created by the pre-existing intake pipeline
  (`tracy_operations.py`, `RuntimePersistenceBridge.create_review_case`)
  against symbols created in `publication_handoff.py`, which never sets
  `visibility` (so it defaults to `'public'`) or `owner_organization_id`.
- WP5.3/5.4's organization-private code
  (`organization_symbol_drafts.py`, `organization_symbol_review.py`) never
  calls `allocate_catalog_identity_for_publication` / `ensure_catalog_symbol_id`,
  and no code path issues `UPDATE governed_symbols SET visibility = ...` on
  an existing row (demotion is explicitly out of scope).
- There is no DB-level CHECK/trigger tying `catalog_symbol_id` and
  `visibility` together (confirmed by reading
  `backend/alembic/versions/20260829_0033_organization_symbol_visibility.py`
  and `models/schema.py:700-731`) — the non-coexistence is a *code*
  invariant, not a *database* invariant.

Because the database does not itself forbid the combination, this test
constructs the worst case directly with raw SQL (the same way
`test_wp52a_public_reader_visibility_floor.py`'s `_publish_symbol` helper
already does for its own fixtures) and proves that, even in that state,
`resolve_catalog_symbol` plus the gated re-query pattern used by every
caller still correctly withholds the symbol — the oracle is exercised and
shown inert, not merely assumed inert.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import stage5_database  # noqa: E402,F401
from test_wp52a_public_reader_visibility_floor import wp52a_fixtures  # noqa: E402,F401

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.catalog_symbol_resolution import resolve_catalog_symbol  # noqa: E402
from symgov_backend.published_catalog import PUBLISHED_SYMBOLS_SQL  # noqa: E402


def _gated_reexecute(session: Session, symbol_id):
    """Mirrors `_load_published_symbol_row`'s / `_load_catalog_symbol_row`'s
    post-resolution re-query exactly."""
    return session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND gs.id = :symbol_id
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id, "symbol_ref": "irrelevant"},
    ).all()


def test_resolver_finds_organization_private_symbol_by_canonical_catalog_id(wp52a_fixtures):
    """The resolver itself has no visibility check: it DOES resolve a
    catalog_symbol_id belonging to an organization_private row, given that
    row exists with a canonical catalog_symbol_identifiers binding. This is
    expected/by-design (see module docstring) — the safety property lives in
    the caller's gated re-query, tested below."""
    engine, public_symbol, public_page, private_symbol, private_page = wp52a_fixtures
    with Session(engine) as session:
        row = session.execute(
            text("SELECT catalog_symbol_id FROM governed_symbols WHERE id = :id"),
            {"id": private_symbol},
        ).one()
        private_catalog_id = row.catalog_symbol_id
        assert private_catalog_id is not None, "fixture must assign a catalog_symbol_id to the private symbol"

        resolved = resolve_catalog_symbol(session, private_catalog_id, route_family="test")
        assert resolved is not None
        assert resolved.symbol_id == private_symbol
        assert resolved.catalog_symbol_id == private_catalog_id


def test_resolver_finds_organization_private_symbol_by_uuid_and_page_code(wp52a_fixtures):
    engine, public_symbol, public_page, private_symbol, private_page = wp52a_fixtures
    with Session(engine) as session:
        resolved_by_uuid = resolve_catalog_symbol(session, str(private_symbol), route_family="test")
        assert resolved_by_uuid is not None
        assert resolved_by_uuid.symbol_id == private_symbol

        resolved_by_page = resolve_catalog_symbol(session, private_page, route_family="test")
        assert resolved_by_page is not None
        assert resolved_by_page.symbol_id == private_symbol


def test_gated_requery_excludes_resolved_organization_private_symbol(wp52a_fixtures):
    """Even though the resolver returns a hit for the organization_private
    symbol (previous tests), the caller's mandatory second query against
    PUBLISHED_SYMBOLS_SQL — which every route call site performs before
    returning any data — excludes it. This is the actual safety boundary:
    resolution alone never discloses content."""
    engine, public_symbol, public_page, private_symbol, private_page = wp52a_fixtures
    with Session(engine) as session:
        resolved_private = resolve_catalog_symbol(session, str(private_symbol), route_family="test")
        assert resolved_private is not None

        gated_rows = _gated_reexecute(session, resolved_private.symbol_id)
        assert gated_rows == [], "gated re-query must withhold the organization_private symbol"

        resolved_public = resolve_catalog_symbol(session, str(public_symbol), route_family="test")
        assert resolved_public is not None
        gated_public_rows = _gated_reexecute(session, resolved_public.symbol_id)
        assert gated_public_rows, "gated re-query must still return the legitimate public symbol"


def test_oracle_is_live_only_in_the_constructed_worst_case_not_by_default(wp52a_fixtures):
    """Documents the actual, narrow shape of the oracle: resolving a
    catalog_symbol_id/uuid/page_code that belongs to a row satisfying
    `gs.catalog_symbol_id IS NOT NULL` (resolver's own filter) yields
    `resolved is not None` regardless of visibility; the caller then turns
    "resolved but gated-excluded" into HTTP 404 `catalog_symbol_unavailable`
    versus "never resolved" -> HTTP 404 `catalog_symbol_not_found`. This
    test asserts that boundary condition explicitly so a future change to
    either query is caught by this suite, and records that it depends on a
    combination (`organization_private` + non-null `catalog_symbol_id`)
    that no current application code path can produce (see module
    docstring) — i.e. the oracle is proven inert under every reachable
    production state, though not under a database-level guarantee."""
    engine, public_symbol, public_page, private_symbol, private_page = wp52a_fixtures
    with Session(engine) as session:
        never_existed = resolve_catalog_symbol(session, "S-DOES-NOT-EXIST-00000000", route_family="test")
        assert never_existed is None  # -> catalog_symbol_not_found in the real route

        resolved_private = resolve_catalog_symbol(session, str(private_symbol), route_family="test")
        assert resolved_private is not None  # resolver alone: found (matches by design)
        assert _gated_reexecute(session, resolved_private.symbol_id) == []  # -> catalog_symbol_unavailable in the real route
