"""Stage 8 WP8.2 regression: organization-aware Catalog detail/preview/
supplemental-photo resolution against a real disposable PostgreSQL container.

Real Postgres is required for the same reason WP8.1's own test is: the
public resolution path this falls back from (`_load_published_symbol_row`)
executes `PUBLISHED_SYMBOLS_SQL` directly, which uses Postgres-only `::text`
casts SQLite cannot run at all.

Proves, per the Stage 8 plan
(`docs/plans/2026-09-03-symbol-set-management-stage8-implementation-plan.md`,
WP8.2/SS1.6):
- An organization-bound session can fetch its own organization-wide
  private symbol's detail by raw governed-symbol UUID, tagged
  `source: "organization_private"`, with page/pack fields `null`.
- A *different* organization's session gets 404 for that same UUID (the
  resolver is additive to, and never bypasses, per-organization scoping).
- A personal-mode session gets 404 for it too.
- Both feature flags gate the fallback off, even for an organization-bound
  session -- the public-only 404 is what the caller sees.
- The preview/supplemental-photo routes route through the same resolver
  and 404 correctly (without needing a real asset store) when there is no
  preview asset for the organization-private symbol's payload.
"""

from __future__ import annotations

import sys
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
from test_wp81_catalog_organization_context_postgresql import (  # noqa: E402
    _login,
    _make_organization_wide_symbol,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

NEW_MIGRATION_HEAD = "20260904_0039"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp82_database():
    with _database("symgov-wp82") as (engine, url, raw_url):
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


def test_owning_organization_can_fetch_the_detail_by_uuid(wp82_database):
    engine, _, _ = wp82_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp82acme1@example.test", display_name="AcmeAdmin1", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp82acme1@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.2 Detail Hydrant")

    detail = acme_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 200, detail.text
    item = detail.json()["item"]
    assert item["source"] == "organization_private"
    assert item["symbolId"] == symbol_id
    assert item["catalogSymbolId"] is None
    assert item["pageId"] is None and item["packId"] is None
    assert item["name"] == "WP8.2 Detail Hydrant"
    assert detail.json()["resolvedBy"] == "organization_private"


def test_a_different_organizations_session_gets_404_for_the_same_uuid(wp82_database):
    engine, _, _ = wp82_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp82acme2@example.test", display_name="AcmeAdmin2", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp82acme2@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.2 Cross-Tenant Detail Hydrant")

    other_admin_id = _create_user_with_global_roles(Session, email="wp82other@example.test", display_name="OtherAdmin", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(other_client, "wp82other@example.test")

    detail = other_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text

    preview = other_client.get(f"/api/v1/published/symbols/{symbol_id}/preview")
    assert preview.status_code == 404, preview.text


def test_personal_mode_session_gets_404_for_it(wp82_database):
    engine, _, _ = wp82_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    personal_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp82acme3@example.test", display_name="AcmeAdmin3", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp82acme3@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.2 Personal Mode Detail Hydrant")

    _create_user_with_global_roles(Session, email="wp82personal@example.test", display_name="PersonalUser", roles=[])
    login = _login(personal_client, "wp82personal@example.test")
    assert login["user"]["session"]["mode"] == "personal"

    detail = personal_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text


def test_feature_flags_gate_the_fallback_off(wp82_database):
    engine, _, _ = wp82_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp82acme4@example.test", display_name="AcmeAdmin4", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp82acme4@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.2 Flag Gated Detail Hydrant")

    from symgov_backend.app import create_app
    from symgov_backend.dependencies import get_db_session
    from symgov_backend.settings import SymgovAPISettings, get_settings
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
    _login(flags_off_client, "wp82acme4@example.test")

    detail = flags_off_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text


def test_preview_route_404s_cleanly_when_there_is_no_preview_asset(wp82_database):
    engine, _, _ = wp82_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp82acme5@example.test", display_name="AcmeAdmin5", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp82acme5@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.2 No Preview Hydrant")

    preview = acme_client.get(f"/api/v1/published/symbols/{symbol_id}/preview")
    assert preview.status_code == 404, preview.text

    supplemental = acme_client.get(f"/api/v1/published/symbols/{symbol_id}/supplemental-photos/{symbol_id}/preview")
    assert supplemental.status_code == 404, supplemental.text
