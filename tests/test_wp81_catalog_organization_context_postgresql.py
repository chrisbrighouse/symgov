"""Stage 8 WP8.1 regression: organization-aware Catalog list against a real
disposable PostgreSQL container.

Real Postgres is required for the same reason every other `routes/published.py`
integration test in this repository is: `list_published_symbols` executes
`PUBLISHED_SYMBOLS_SQL` directly via `session.execute(text(...))`, which uses
Postgres-only syntax (`::text` casts) SQLite cannot run at all -- so exercising
the real endpoint, merged or not, structurally requires Postgres.

Proves, per the Stage 8 plan
(`docs/plans/2026-09-03-symbol-set-management-stage8-implementation-plan.md`,
WP8.1/SS4 Q2/Q3):
- An organization-bound session sees its own organization's
  `organization_wide=true` symbols merged into `GET /published/symbols`,
  tagged `source: "organization_private"`, with page/pack fields `null`.
- A *different* organization's session never sees the first organization's
  private symbols (the core cross-tenant acceptance bar) -- proved here at
  the unit level; the full reader-exclusion matrix is WP8.4's job.
- A personal-mode session (no organization membership) sees none of it.
- Both feature flags gate the whole branch off, even for an organization-
  bound session.
- The `q` search filter applies to organization-private results; the `pack`
  filter excludes them entirely (organization-private symbols have no pack).
- Favourites enrichment (`isFavourite`) works correctly for an
  organization-private entry favourited by its own organization's member.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import (  # noqa: E402
    _add_membership,
    _client,
    _create_user_with_global_roles,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.settings import SymgovAPISettings  # noqa: E402

NEW_MIGRATION_HEAD = "20260902_0037"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp81_database():
    with _database("symgov-wp81") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT, INSERT, DELETE ON catalog_favourites TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _make_organization_wide_symbol(admin_client, *, name):
    """Create, org-review-approve, and organization-wide-toggle a symbol --
    the WP8.1 counterpart of `_promote_symbol` (which continues on to a full
    public promotion this test never wants: WP8.1's scope is Catalog
    visibility for a symbol that stays organization-private)."""
    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]

    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    toggle = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/organization-wide", json={"enabled": True})
    assert toggle.status_code == 200, toggle.text
    assert toggle.json()["organizationWide"] is True
    return symbol_id


def _login(client, email):
    login = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert login.status_code == 200, login.text
    return login.json()


def test_organization_bound_session_sees_its_own_organization_wide_symbol(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme@example.test", display_name="AcmeAdmin", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    login = _login(acme_client, "wp81acme@example.test")
    assert login["user"]["session"]["mode"] == "organization"

    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Acme Hydrant")

    listing = acme_client.get("/api/v1/published/symbols", params={"q": "WP8.1 Acme Hydrant"})
    assert listing.status_code == 200, listing.text
    items = [item for item in listing.json()["items"] if item["symbolId"] == symbol_id]
    assert len(items) == 1, listing.json()["items"]
    item = items[0]
    assert item["source"] == "organization_private"
    assert item["catalogSymbolId"] is None
    assert item["pageId"] is None and item["pageCode"] is None and item["pageTitle"] is None
    assert item["packId"] is None and item["packCode"] is None and item["pack"] is None
    assert item["effectiveDate"] is None
    assert item["name"] == "WP8.1 Acme Hydrant"
    assert item["category"] == "fire"
    assert item["discipline"] == "civil"
    assert item["status"] == "Approved"


def test_a_different_organizations_session_never_sees_it(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme2@example.test", display_name="AcmeAdmin2", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp81acme2@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Cross-Tenant Hydrant")

    other_admin_id = _create_user_with_global_roles(Session, email="wp81other@example.test", display_name="OtherAdmin", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    login = _login(other_client, "wp81other@example.test")
    assert login["user"]["session"]["mode"] == "organization"

    listing = other_client.get("/api/v1/published/symbols", params={"q": "WP8.1 Cross-Tenant Hydrant"})
    assert listing.status_code == 200, listing.text
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"]), (
        "a different organization's session must never see another organization's "
        "organization-wide private symbol -- the core Stage 8 acceptance bar"
    )


def test_personal_mode_session_never_sees_organization_private_results(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    personal_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme3@example.test", display_name="AcmeAdmin3", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp81acme3@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Personal Mode Hydrant")

    _create_user_with_global_roles(Session, email="wp81personal@example.test", display_name="PersonalUser", roles=[])
    login = _login(personal_client, "wp81personal@example.test")
    assert login["user"]["session"]["mode"] == "personal"

    listing = personal_client.get("/api/v1/published/symbols", params={"q": "WP8.1 Personal Mode Hydrant"})
    assert listing.status_code == 200, listing.text
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"])


def test_feature_flags_gate_organization_private_results_off(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme4@example.test", display_name="AcmeAdmin4", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp81acme4@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Flag Gated Hydrant")

    from symgov_backend.app import create_app
    from symgov_backend.dependencies import get_db_session
    from symgov_backend.settings import get_settings
    from fastapi.testclient import TestClient

    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    flags_off_settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=False,
        platform_admin_enabled=True,
        organization_pilot_codes=("acme", "other", "symgov"),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: flags_off_settings
    flags_off_client = TestClient(app, headers={"origin": "http://testserver"})
    _login(flags_off_client, "wp81acme4@example.test")

    listing = flags_off_client.get("/api/v1/published/symbols", params={"q": "WP8.1 Flag Gated Hydrant"})
    assert listing.status_code == 200, listing.text
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"]), (
        "organization_symbols_enabled=False must suppress organization-private "
        "Catalog results even for an organization-bound session"
    )


def test_pack_filter_excludes_organization_private_results(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme5@example.test", display_name="AcmeAdmin5", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp81acme5@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Pack Filtered Hydrant")

    listing = acme_client.get("/api/v1/published/symbols", params={"pack": "any-pack-code"})
    assert listing.status_code == 200, listing.text
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"]), (
        "organization-private symbols have no pack, so a `pack` filter must "
        "exclude them entirely rather than always matching zero rows"
    )


def test_favouriting_an_organization_private_symbol_marks_it_in_the_list(wp81_database):
    engine, _, _ = wp81_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp81acme6@example.test", display_name="AcmeAdmin6", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp81acme6@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.1 Favourited Hydrant")

    with engine.begin() as connection:
        from sqlalchemy import text as sa_text
        connection.execute(
            sa_text(
                "INSERT INTO catalog_favourites (user_id, symbol_id, created_at) "
                "VALUES (:user_id, :symbol_id, now())"
            ),
            {"user_id": acme_admin_id, "symbol_id": symbol_id},
        )

    listing = acme_client.get("/api/v1/published/symbols", params={"q": "WP8.1 Favourited Hydrant"})
    assert listing.status_code == 200, listing.text
    items = [item for item in listing.json()["items"] if item["symbolId"] == symbol_id]
    assert len(items) == 1
    assert items[0]["isFavourite"] is True
