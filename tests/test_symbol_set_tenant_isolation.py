"""Stage 6 WP6.1 — effective palette tenant isolation, against a real
disposable PostgreSQL container.

Per the Stage 6 plan (WP6.1 acceptance): "Treat any effective-palette
query as a new reader that must itself prove it can't leak a
cross-organization private symbol — the same rigor Stage 5's WP5.2/5.6
applied to public readers should apply here to organization-scoped
readers." The DB-level guarantees this proves against are Postgres-only
(the `organization_wide_scope`/`catalog_symbol_visibility_barrier` CHECK
constraints and the `trg_governed_symbols_organization_wide_eligibility`
deferred constraint trigger from WP5.1/5.4) — a SQLite unit test cannot
exercise them, which is why `tests/test_effective_palette.py` mocks
eligibility instead and this file is Postgres-only.

An organization-wide symbol is built through the real WP5.3/5.4 service
pipeline (create_draft -> submit_for_review -> decide_submission ->
set_organization_wide), the same path Stage 5's own tests use, so the
fixture itself is only reachable the way production would reach it.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _alembic,
    _organization,
    _user,
    stage5_database,
)
from test_project_symbol_set_postgresql import _availability, _project, _symbol_set  # noqa: E402
from test_wp53_organization_symbol_drafts import _actor, _membership  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.auth import hash_session_token  # noqa: E402
from symgov_backend.effective_palette import effective_palette  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import OrganizationSymbolReviewError, decide_submission, set_organization_wide  # noqa: E402
from symgov_backend.symbol_set_builder import search_symbol_set_builder  # noqa: E402
from symgov_backend.symbol_set_service import replace_items  # noqa: E402


def _published_public_symbol(engine, canonical_name: str) -> uuid.UUID:
    """Builds a genuinely published, public-eligible governed symbol via
    raw SQL (mirrors `test_project_symbol_set_postgresql.py`'s pattern),
    so `symbol_set_builder._search_public_symbols`'s real raw SQL against
    `PUBLISHED_SYMBOLS_SQL` is exercised at least once against real
    Postgres tables and the `active_public_symbol_projections` view --
    every other Builder search test mocks this half out."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbol_id, revision_id, pack_id, page_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        owner = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ), {"id": owner, "email": f"builder-{owner}@example.test", "now": now})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
            "VALUES (:id,:slug,:name,'fire','fire-safety',:owner,:now,:now)"
        ), {"id": symbol_id, "slug": canonical_name.lower().replace(" ", "-"), "name": canonical_name, "owner": owner, "now": now})
        connection.execute(text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'1','published','{}'::jsonb,:owner,:now)"
        ), {"id": revision_id, "symbol": symbol_id, "owner": owner, "now": now})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision_id, "symbol": symbol_id})
        catalog_id = f"BUILDER-{uuid.uuid4().hex[:16].upper()}"
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
        ), {"catalog": catalog_id, "symbol": symbol_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"), {"catalog": catalog_id, "symbol": symbol_id})
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'Builder Search','public',CURRENT_DATE,'published',:now,:now)"
        ), {"id": pack_id, "code": f"BUILDER-{uuid.uuid4().hex}", "now": now})
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,'Builder Search',:pack,:revision,CURRENT_DATE,:now,:now)"
        ), {"id": page_id, "code": f"BUILDER-PAGE-{uuid.uuid4().hex}", "pack": pack_id, "revision": revision_id, "now": now})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:revision,:page,1,:now)"
        ), {"id": uuid.uuid4(), "pack": pack_id, "revision": revision_id, "page": page_id, "now": now})
    return symbol_id


def _activate(connection, organization_id):
    connection.execute(text("UPDATE organizations SET is_active=true WHERE id=:id"), {"id": organization_id})


def _normalized_code(connection, organization_id) -> str:
    return connection.execute(
        text("SELECT normalized_code FROM organizations WHERE id=:id"), {"id": organization_id}
    ).scalar_one()


def _organization_wide_symbol(engine, actor) -> uuid.UUID:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        symbol, revision = create_draft(
            session, actor,
            name="Fire alarm call point", category="fire", discipline="fire-safety",
            summary="Test organization-wide symbol.",
        )
        session.commit()
        submission = submit_for_review(session, actor, symbol_id=symbol.id, revision_id=revision.id)
        session.commit()
        decide_submission(session, actor, submission_id=submission.id, decision="approved")
        session.commit()
        set_organization_wide(session, actor, symbol_id=symbol.id, enabled=True)
        session.commit()
        return symbol.id


def _bound_session_request(engine, user_id, organization_id) -> Request:
    raw_token = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": uuid.uuid4(), "user": user_id, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": organization_id,
        })
    return Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})


@pytest.fixture()
def two_organizations(stage5_database):
    engine, url, _ = stage5_database
    # Stage 9 WP9.2 added ProductUsageEvent emission inside submit_for_review /
    # decide_submission / set_organization_wide (shared, widely-tested
    # functions this file exercises directly). Applied locally, not by
    # bumping the shared `stage5_database` fixture itself.
    _alembic(url, "upgrade", "20260904_0039")
    with engine.begin() as connection:
        user_a = _user(connection, "tenant-a")
        user_b = _user(connection, "tenant-b")
        org_a = _organization(connection, "tenanta")
        org_b = _organization(connection, "tenantb")
        _activate(connection, org_a)
        _activate(connection, org_b)
        _membership(connection, org_a, user_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
        _membership(connection, org_b, user_b, base_role="admin")
        code_a = _normalized_code(connection, org_a)
        code_b = _normalized_code(connection, org_b)
        project_a = _project(connection, org_a, user_a, "PRJ-A")
        project_b = _project(connection, org_b, user_b, "PRJ-B")
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_symbols_enabled=True,
        organization_pilot_codes=(code_a, code_b),
    )
    return SimpleNamespace(
        engine=engine, user_a=user_a, user_b=user_b, org_a=org_a, org_b=org_b,
        project_a=project_a, project_b=project_b, settings=settings,
    )


def test_effective_palette_never_leaks_a_cross_organization_organization_wide_symbol(two_organizations):
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _organization_wide_symbol(fixtures.engine, actor_a)

    request_b = _bound_session_request(fixtures.engine, fixtures.user_b, fixtures.org_b)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with SessionLocal.begin() as session:
        _, result = effective_palette(session, request_b, fixtures.settings, fixtures.project_b, page=1, page_size=50)

    assert result["items"] == []
    assert result["total"] == 0


def test_effective_palette_includes_the_owning_organizations_own_organization_wide_symbol(two_organizations):
    """Positive control: proves the isolation test above is not vacuous
    (e.g. from a fixture bug that stops the symbol becoming organization-wide
    at all) by proving the *owning* organization does see it."""
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _organization_wide_symbol(fixtures.engine, actor_a)

    request_a = _bound_session_request(fixtures.engine, fixtures.user_a, fixtures.org_a)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with SessionLocal.begin() as session:
        _, result = effective_palette(session, request_a, fixtures.settings, fixtures.project_a, page=1, page_size=50)

    assert result["total"] == 1
    assert result["items"][0]["governedSymbolId"] == symbol_id
    assert result["items"][0]["source"] == "organization_wide"


def test_a_cross_organization_private_symbol_cannot_become_a_symbol_set_item(two_organizations):
    """Structural claim in `effective_palette.py`'s module docstring,
    proven directly: a `SymbolSetItem` can only ever reference a
    `visibility='public'` governed symbol, so the set-sourced half of the
    palette union can never carry an organization-private symbol from
    any organization, including its own."""
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _organization_wide_symbol(fixtures.engine, actor_a)

    with fixtures.engine.begin() as connection:
        set_id = _symbol_set(connection, fixtures.org_a, fixtures.user_a, "SET-A")
        _availability(connection, fixtures.project_a, set_id, fixtures.user_a, default=True)

    request_a = _bound_session_request(fixtures.engine, fixtures.user_a, fixtures.org_a)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with pytest.raises(Exception) as caught:
        with SessionLocal.begin() as session:
            replace_items(
                session, request_a, fixtures.settings, set_id,
                SimpleNamespace(items=[SimpleNamespace(
                    governedSymbolId=symbol_id, sortOrder=0, groupName=None, displayLabel=None,
                    notes=None, preferredFormat=None, provenance={},
                )]),
            )
    assert "eligible" in str(caught.value).lower() or getattr(caught.value, "status_code", None) == 409


def test_builder_search_real_public_sql_excludes_a_cross_organization_private_symbol(two_organizations):
    """Exercises the real (unmocked) `PUBLISHED_SYMBOLS_SQL`-based public
    half of the Builder search against Postgres, and proves org A's
    private symbol never appears in org B's search results even though
    org B's search also matches on the same query text."""
    fixtures = two_organizations
    public_symbol_id = _published_public_symbol(fixtures.engine, "Shared Fire Alarm Beacon")
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    private_symbol_id = _organization_wide_symbol(fixtures.engine, actor_a)

    request_b = _bound_session_request(fixtures.engine, fixtures.user_b, fixtures.org_b)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with SessionLocal.begin() as session:
        _, result = search_symbol_set_builder(session, request_b, fixtures.settings, query_text=None, page=1, page_size=50)

    ids = {item["governedSymbolId"] for item in result["items"]}
    assert public_symbol_id in ids
    assert private_symbol_id not in ids
