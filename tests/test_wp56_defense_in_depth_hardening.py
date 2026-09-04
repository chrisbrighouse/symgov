"""WP5.6 defense-in-depth hardening regressions.

The WP5.6 whole-stage audit found every current application code path
already respects the organization-private visibility floor, but flagged two
gaps that only hold "by construction" rather than being independently
enforced:

1. Nothing in the schema stops a `governed_symbols` row from carrying both
   `visibility='organization_private'` and a non-null `catalog_symbol_id`
   (catalog identifiers are meant to be public-only). This module proves the
   new `20260901_0034` migration's
   `ck_governed_symbols_catalog_symbol_visibility_barrier` CHECK constraint
   closes that gap at the database level, additively and without disturbing
   any row shape any existing code path produces.

2. `rupert_published_metadata` (`routes/workspace.py` and its `workspace.py`
   twin) and the Hannah/Whitney display-join queries
   (`list_hannah_photo_candidates`, `list_whitney_demand_signals`, in both
   files) joined `GovernedSymbol`/`PublishedPage`/`PublicationPack` with no
   independent `visibility` check. This module proves each of those six call
   sites now excludes an `organization_private` governed symbol constructed
   to otherwise satisfy every other condition the query checks, while a
   legitimate public row is returned unchanged.

Follows the disposable-PostgreSQL harness and fixture-construction patterns
established in `test_organization_symbol_postgresql.py` and
`test_wp52a_public_reader_visibility_floor.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _alembic,
    _approve,
    _database,
    _organization,
    _revision,
    _submission,
    _user,
)

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import symgov_backend.routes.workspace as routes_workspace  # noqa: E402

# `symgov_backend/workspace.py` (the top-level "twin" the WP5.6 audit also
# names) is pre-existing orphaned code: it is never imported by `app.py`
# (only `routes/workspace.py` is mounted) and its own `from ..dependencies
# import get_db_session` already raises `ImportError: attempted relative
# import beyond top-level package` on a clean checkout, independent of this
# change -- confirmed by reproducing the failure against the pre-fix
# revision. The hardening edit was still applied there (source-verified
# below) to match the audit's explicit remediation scope, but it cannot be
# exercised via direct import, so its regression coverage here is a static
# source check rather than a live call.

NEW_MIGRATION_HEAD = "20260904_0038"

psycopg = pytest.importorskip("psycopg")


def test_new_migration_is_the_sole_alembic_head():
    result = subprocess.run(
        ["alembic", "heads"],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    heads = [line.split()[0] for line in result.stdout.strip().splitlines() if line.strip()]
    assert heads == [NEW_MIGRATION_HEAD], result.stdout


@pytest.fixture(scope="module")
def wp56_database():
    with _database("symgov-wp56") as (engine, url, raw_url):
        _alembic(url, "upgrade", "20260829_0033")
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


# ---------------------------------------------------------------------------
# Finding 1: catalog_symbol_id / organization_private DB-level barrier
# ---------------------------------------------------------------------------


def _bare_symbol(connection, actor, *, visibility: str = "public") -> uuid.UUID:
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(
        text(
            "INSERT INTO governed_symbols "
            "(id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
            "VALUES (:id,:slug,:slug,'test','test',:owner,:visibility,:now,:now)"
        ),
        {
            "id": identifier,
            "slug": f"wp56-{identifier}",
            "owner": actor,
            "visibility": visibility,
            "now": now,
        },
    )
    return identifier


def _catalog_identifier(connection, governed_symbol_id: uuid.UUID, *, role: str = "canonical") -> str:
    """Insert a `catalog_symbol_identifiers` row. `role='canonical'` requires
    the caller to immediately point `governed_symbols.catalog_symbol_id` at
    the returned identifier in the same transaction, to satisfy the
    pre-existing bidirectional-consistency trigger from the
    `20260802_0026` migration. `role='historical_alias'` carries no such
    back-reference requirement, so it is used where the test only needs an
    FK-valid identifier value and does not want to also make its owning
    symbol a fully-linked catalog symbol.
    """
    identifier = f"WP56-{uuid.uuid4().hex[:16].upper()}"
    connection.execute(
        text(
            "INSERT INTO catalog_symbol_identifiers "
            "(identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:identifier,:role,:symbol,'global_sequence',now())"
        ),
        {"identifier": identifier, "role": role, "symbol": governed_symbol_id},
    )
    return identifier


def test_constraint_exists_in_information_schema(wp56_database):
    engine, _, _ = wp56_database
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = 'governed_symbols' "
                "AND constraint_name = 'ck_governed_symbols_catalog_symbol_visibility_barrier'"
            )
        ).one_or_none()
    assert row is not None


def test_public_row_with_catalog_symbol_id_inserts_successfully(wp56_database):
    engine, _, _ = wp56_database
    with engine.begin() as connection:
        actor = _user(connection, "wp56-public-catalog")
        symbol = _bare_symbol(connection, actor, visibility="public")
        catalog_id = _catalog_identifier(connection, symbol)
        connection.execute(
            text("UPDATE governed_symbols SET catalog_symbol_id=:c WHERE id=:s"),
            {"c": catalog_id, "s": symbol},
        )
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT visibility, catalog_symbol_id FROM governed_symbols WHERE id=:s"),
            {"s": symbol},
        ).one()
    assert stored.visibility == "public"
    assert stored.catalog_symbol_id == catalog_id


def test_organization_private_row_without_catalog_symbol_id_inserts_successfully(wp56_database):
    engine, _, _ = wp56_database
    with engine.begin() as connection:
        actor = _user(connection, "wp56-private-nocatalog")
        symbol = _bare_symbol(connection, actor, visibility="organization_private")
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT visibility, catalog_symbol_id FROM governed_symbols WHERE id=:s"),
            {"s": symbol},
        ).one()
    assert stored.visibility == "organization_private"
    assert stored.catalog_symbol_id is None


def test_insert_organization_private_with_catalog_symbol_id_is_rejected(wp56_database):
    engine, _, _ = wp56_database
    with engine.begin() as connection:
        actor = _user(connection, "wp56-reject-insert")
        placeholder = _bare_symbol(connection, actor, visibility="public")
        catalog_id = _catalog_identifier(connection, placeholder, role="historical_alias")

    symbol_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(DBAPIError) as excinfo:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO governed_symbols "
                    "(id,slug,canonical_name,category,discipline,owner_id,visibility,catalog_symbol_id,created_at,updated_at) "
                    "VALUES (:id,:slug,:slug,'test','test',:owner,'organization_private',:catalog,:now,:now)"
                ),
                {
                    "id": symbol_id,
                    "slug": f"wp56-{symbol_id}",
                    "owner": actor,
                    "catalog": catalog_id,
                    "now": now,
                },
            )
    assert "ck_governed_symbols_catalog_symbol_visibility_barrier" in str(excinfo.value)

    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).first()
    assert exists is None


def test_update_to_organization_private_with_existing_catalog_symbol_id_is_rejected(wp56_database):
    engine, _, _ = wp56_database
    with engine.begin() as connection:
        actor = _user(connection, "wp56-reject-update")
        symbol = _bare_symbol(connection, actor, visibility="public")
        catalog_id = _catalog_identifier(connection, symbol)
        connection.execute(
            text("UPDATE governed_symbols SET catalog_symbol_id=:c WHERE id=:s"),
            {"c": catalog_id, "s": symbol},
        )

    with pytest.raises(DBAPIError) as excinfo:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE governed_symbols SET visibility='organization_private' WHERE id=:s"),
                {"s": symbol},
            )
    assert "ck_governed_symbols_catalog_symbol_visibility_barrier" in str(excinfo.value)

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT visibility, catalog_symbol_id FROM governed_symbols WHERE id=:s"),
            {"s": symbol},
        ).one()
    assert stored.visibility == "public"
    assert stored.catalog_symbol_id == catalog_id


# ---------------------------------------------------------------------------
# Finding 2: workspace display-join visibility hardening
# ---------------------------------------------------------------------------


def _publish_symbol_for_display(
    connection, actor, organization, *, visibility: str, category: str = "wp56", discipline: str = "wp56"
):
    """Build a symbol satisfying every existing publication predicate these
    display joins already check (published pack/page/entry, published
    revision), for the given `visibility` -- deliberately including
    `organization_private`, to prove the display-join queries independently
    exclude it even though no current write path can produce this
    combination. Never assigns a `catalog_symbol_id`, so this fixture never
    interacts with the Finding 1 CHECK constraint.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbol = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO governed_symbols "
            "(id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
            "VALUES (:id,:slug,:slug,:category,:discipline,:owner,:now,:now,:organization,:visibility)"
        ),
        {
            "id": symbol,
            "slug": f"wp56-display-{symbol}",
            "category": category,
            "discipline": discipline,
            "owner": actor,
            "now": now,
            "organization": organization,
            "visibility": visibility,
        },
    )
    revision = _revision(connection, symbol, actor, lifecycle="published")
    if visibility == "public":
        submission = _submission(connection, organization, symbol, revision, actor)
        _approve(connection, submission, organization, symbol, revision, actor)

    pack, page, entry = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO publication_packs "
            "(id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'WP5.6','public',CURRENT_DATE,'published',now(),now())"
        ),
        {"id": pack, "code": f"WP56-{uuid.uuid4().hex}"},
    )
    connection.execute(
        text(
            "INSERT INTO published_pages "
            "(id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,'WP5.6',:pack,:revision,CURRENT_DATE,now(),now())"
        ),
        {"id": page, "code": f"WP56-PAGE-{uuid.uuid4().hex}", "pack": pack, "revision": revision},
    )
    connection.execute(
        text(
            "INSERT INTO pack_entries "
            "(id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:revision,:page,1,now())"
        ),
        {"id": entry, "pack": pack, "revision": revision, "page": page},
    )
    return symbol, revision, page


def _hannah_candidate(connection, symbol_id: uuid.UUID, published_page_id: uuid.UUID) -> uuid.UUID:
    candidate_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(
        text(
            "INSERT INTO hannah_photo_candidates "
            "(id,symbol_id,published_page_id,source_url,image_url,source_domain,rights_status,status,evidence_json,first_seen_at,last_seen_at) "
            "VALUES (:id,:symbol,:page,:source_url,:image_url,:domain,'public_domain','candidate','{}'::jsonb,:now,:now)"
        ),
        {
            "id": candidate_id,
            "symbol": symbol_id,
            "page": published_page_id,
            "source_url": f"https://example.test/source/{candidate_id}",
            "image_url": f"https://example.test/image/{candidate_id}.jpg",
            "domain": "example.test",
            "now": now,
        },
    )
    return candidate_id


def _whitney_signal(connection, symbol_id: uuid.UUID, published_page_id: uuid.UUID) -> uuid.UUID:
    signal_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(
        text(
            "INSERT INTO whitney_demand_signals "
            "(id,symbol_id,published_page_id,signal_type,source_type,source_ref,title,summary,evidence_json,status,first_seen_at,last_seen_at) "
            "VALUES (:id,:symbol,:page,'coverage_gap','manual',:ref,:title,'summary','{}'::jsonb,'open',:now,:now)"
        ),
        {
            "id": signal_id,
            "symbol": symbol_id,
            "page": published_page_id,
            "ref": f"wp56-{signal_id}",
            "title": f"WP5.6 demand signal {signal_id}",
            "now": now,
        },
    )
    return signal_id


# The pre-existing (`20260802_0026`) publication invariant requires *every*
# symbol with a published revision -- regardless of visibility -- to carry a
# canonical `catalog_symbol_id`. Combined with this change's own Finding 1
# barrier (which forbids `catalog_symbol_id` on an `organization_private`
# row), an organization-private symbol can no longer legitimately reach
# `lifecycle_state='published'`/`published_pages`/`pack_entries` through any
# supported write path at all -- a stronger, incidental consequence of
# Finding 1. To still prove the Finding 2 display joins independently
# exclude such a row (matching the audit's own "no current path produces
# this, but the guard must hold anyway" framing), this fixture briefly
# disables the unrelated WP26 invariant triggers to force the otherwise-
# impossible row combination, then restores them immediately afterward.
_CATALOG_PUBLICATION_INVARIANT_TRIGGERS = (
    ("symbol_revisions", "trg_symbol_revisions_validate_catalog_publication"),
    ("published_pages", "trg_published_pages_validate_catalog_publication"),
    ("pack_entries", "trg_pack_entries_validate_catalog_publication"),
    ("governed_symbols", "trg_governed_symbols_validate_catalog_publication"),
    ("catalog_symbol_identifiers", "trg_catalog_symbol_identifiers_validate_publication"),
)


def _set_catalog_publication_invariant_triggers(connection, *, enabled: bool) -> None:
    verb = "ENABLE" if enabled else "DISABLE"
    for table, trigger in _CATALOG_PUBLICATION_INVARIANT_TRIGGERS:
        connection.execute(text(f"ALTER TABLE {table} {verb} TRIGGER {trigger}"))


@pytest.fixture()
def wp56_display_fixtures(wp56_database):
    engine, _, _ = wp56_database
    with engine.begin() as connection:
        _set_catalog_publication_invariant_triggers(connection, enabled=False)
    try:
        with engine.begin() as connection:
            actor = _user(connection, "wp56-display")
            organization = _organization(connection, "wp56-display")
            public_symbol, public_revision, public_page = _publish_symbol_for_display(
                connection, actor, organization, visibility="public"
            )
            private_symbol, private_revision, private_page = _publish_symbol_for_display(
                connection, actor, organization, visibility="organization_private"
            )
            _hannah_candidate(connection, public_symbol, public_page)
            _hannah_candidate(connection, private_symbol, private_page)
            _whitney_signal(connection, public_symbol, public_page)
            _whitney_signal(connection, private_symbol, private_page)
    finally:
        with engine.begin() as connection:
            _set_catalog_publication_invariant_triggers(connection, enabled=True)
    return SimpleNamespace(
        engine=engine,
        public_symbol=public_symbol,
        public_revision=public_revision,
        private_symbol=private_symbol,
        private_revision=private_revision,
    )


def _rupert_queue_item(revision_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(payload_json={"symbol_revision_ids": [str(revision_id)]})


@pytest.mark.parametrize("module", [routes_workspace])
def test_rupert_published_metadata_excludes_organization_private_symbol(wp56_display_fixtures, module):
    with Session(wp56_display_fixtures.engine) as session:
        private_result = module.rupert_published_metadata(
            session, _rupert_queue_item(wp56_display_fixtures.private_revision)
        )
        public_result = module.rupert_published_metadata(
            session, _rupert_queue_item(wp56_display_fixtures.public_revision)
        )
    assert private_result == {}
    assert public_result.get("published_symbol_id") is not None


_HANNAH_KWARGS = dict(
    offset=0,
    limit=50,
    sort="lastSeenAt",
    direction="desc",
    symbolName=None,
    sourceUrl=None,
    sourceDomain=None,
    title=None,
    rightsStatus=None,
    licenseLabel=None,
    status=None,
    relevanceScore=None,
    lastSeenAt=None,
    lastSessionQueueItemId=None,
)


@pytest.mark.parametrize(
    "module,extra_kwargs",
    [
        (routes_workspace, {"description": None}),
    ],
)
def test_list_hannah_photo_candidates_excludes_organization_private_symbol(
    wp56_display_fixtures, module, extra_kwargs
):
    with Session(wp56_display_fixtures.engine) as session:
        response = module.list_hannah_photo_candidates(
            session=session, **_HANNAH_KWARGS, **extra_kwargs
        )
    returned_symbol_ids = {item.symbolId for item in response.items}
    assert str(wp56_display_fixtures.public_symbol) in returned_symbol_ids
    assert str(wp56_display_fixtures.private_symbol) not in returned_symbol_ids


_WHITNEY_KWARGS = dict(
    offset=0,
    limit=50,
    sort="lastSeenAt",
    direction="desc",
    signalType=None,
    marketSegment=None,
    discipline=None,
    category=None,
    sourceType=None,
    title=None,
    demandScore=None,
    confidence=None,
    recommendedAction=None,
    status=None,
    lastSeenAt=None,
    lastSessionQueueItemId=None,
)


@pytest.mark.parametrize("module", [routes_workspace])
def test_list_whitney_demand_signals_excludes_organization_private_symbol(wp56_display_fixtures, module):
    with Session(wp56_display_fixtures.engine) as session:
        response = module.list_whitney_demand_signals(session=session, **_WHITNEY_KWARGS)
    returned_symbol_ids = {item.symbolId for item in response.items if item.symbolId is not None}
    assert str(wp56_display_fixtures.public_symbol) in returned_symbol_ids
    assert str(wp56_display_fixtures.private_symbol) not in returned_symbol_ids


def test_workspace_twin_file_has_matching_hardening_edits_applied():
    """`symgov_backend/workspace.py` cannot be imported (see module banner
    above), so verify its three hardening edits by source inspection instead
    of a live call."""
    source = (BACKEND / "symgov_backend" / "workspace.py").read_text(encoding="utf-8")
    # rupert_published_metadata + the Hannah inner-join site each add a bare
    # filter; the Whitney outer-join site's filter also contains the same
    # substring inside its or_(...) clause, for 3 occurrences total.
    assert source.count('GovernedSymbol.visibility == "public"') == 3
    assert 'or_(GovernedSymbol.id.is_(None), GovernedSymbol.visibility == "public")' in source
