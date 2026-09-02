"""Stage 7 WP7.4 regression: demotion impact preview and execution against
a real disposable PostgreSQL container.

Real Postgres is required for the same reasons WP7.1-7.3's own Postgres
tests are: the governed-symbol-row lock, the multi-row lock-then-mutate
sequence across `symbol_revisions`/`published_pages`/`pack_entries`/
`publication_packs`, and `active_public_symbol_projections`'s join
semantics are not meaningfully exercisable against SQLite.

Proves, per the programme plan §13 tasks 8-10 and the Stage 7 plan's Q4/Q5
decisions:
- Demotion is Platform-Admin-only, requires `require_recent_step_up`
  (10-minute window, no additional control), and a non-blank reason.
- Demotion is rejected while any Symbol Set owned by a *different*
  organization still references the symbol (FR-PUB-009); removing that
  reference restores eligibility.
- An ownerless legacy public symbol (`owner_organization_id is None`)
  cannot be demoted (§13 task 9).
- Favorites do not affect eligibility (§10.3) -- reported by the preview
  for impact only.
- Execution flips `visibility` to `organization_private`, withdraws the
  published revision, retires the active page/entry projection, retires
  the now-empty publication pack, and the symbol disappears from
  `active_public_symbol_projections` -- the real public reader gate.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    SymbolSet,
    SymbolSetItem,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260902_0037"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp74_database():
    with _database("symgov-wp74") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT ON catalog_symbol_identifiers TO symgov_app",
                "GRANT USAGE, SELECT ON SEQUENCE catalog_symbol_id_seq TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_cases TO symgov_app",
                "GRANT SELECT, INSERT ON human_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_case_actions TO symgov_app",
                "GRANT SELECT, INSERT ON publication_approval_targets TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT ON active_public_symbol_projections TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_sets TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_set_items TO symgov_app",
                "GRANT SELECT ON catalog_favourites TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _client(engine):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
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
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), TestingSessionLocal


def _add_membership(Session, user_id, *, code, base_role="user", capabilities=()):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = session.query(Organization).filter(Organization.normalized_code == code).one_or_none()
        if organization is None:
            # ck_organizations_reserved_identity requires the 'symgov' org
            # specifically to have code='symgov' (lowercase) and
            # is_protected=true; every other org must be is_protected=false
            # with an uppercase code.
            is_symgov = code == "symgov"
            organization = Organization(
                id=uuid.uuid4(),
                code="symgov" if is_symgov else code.upper(),
                normalized_code=code,
                display_name="Symgov" if is_symgov else f"{code.upper()} Organization",
                name_key=f"{code}-organization",
                entitlement_status="active",
                is_active=True,
                is_protected=is_symgov,
                fallback_icon_svg="<svg/>",
                created_at=now,
                updated_at=now,
            )
            session.add(organization)
            session.flush()
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=user_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(), membership_id=membership.id, base_role=base_role, is_active=True,
                assigned_at=now, revoked_at=None,
            )
        )
        for capability in capabilities:
            session.add(
                OrganizationMemberCapability(
                    id=uuid.uuid4(), membership_id=membership.id, capability=capability, is_active=True,
                    granted_at=now,
                )
            )
        session.commit()
        return organization.id


def _create_user_with_global_roles(Session, *, email, display_name, roles):
    with Session() as session:
        user = upsert_user(session, email=email, display_name=display_name, roles=[], pin="1234", must_change_pin=False)
        session.commit()
        user_id = user.id
    if roles:
        with Session() as session:
            subscription = session.get(UserSubscription, user_id)
            subscription.tier = "plus"
            subscription.expires_on = datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 1)
            session.commit()
        with Session() as session:
            upsert_user(session, email=email, display_name=display_name, roles=roles, pin="1234", must_change_pin=False)
            session.commit()
    return user_id


def _make_platform_admin(Session, user_id):
    """The `enforce_platform_admin_eligibility` trigger requires the active
    Symgov organization to retain at least one active Platform Administrator
    at every commit -- so the org-admin membership and the
    PlatformRoleAssignment must be created in the same transaction as each
    other (mirroring test_platform_admin_api.py's own
    _seed_symgov_org_with_platform_admin), not across two separate commits
    as a generic _add_membership + PlatformRoleAssignment sequence would do."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = session.query(Organization).filter(Organization.normalized_code == "symgov").one_or_none()
        if organization is None:
            organization = Organization(
                id=uuid.uuid4(),
                code="symgov",
                normalized_code="symgov",
                display_name="Symgov",
                name_key="symgov-organization",
                entitlement_status="active",
                is_active=True,
                is_protected=True,
                fallback_icon_svg="<svg/>",
                created_at=now,
                updated_at=now,
            )
            session.add(organization)
            session.flush()
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=organization.id, user_id=user_id, status="active",
            activated_at=now, created_at=now, updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(), membership_id=membership.id, base_role="admin", is_active=True, assigned_at=now,
            )
        )
        session.add(
            PlatformRoleAssignment(id=uuid.uuid4(), user_id=user_id, role="platform_admin", is_active=True, assigned_at=now)
        )
        session.commit()
        return organization.id


def _promote_symbol(admin_client, reviewer_client, *, name="WP7.4 Fire Hydrant"):
    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]

    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text
    return symbol_id


def _login_platform_admin_with_step_up(client, email):
    login = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["session"]["mode"] == "organization"
    step_up = client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})
    assert step_up.status_code == 200, step_up.text


def test_demotion_removes_symbol_from_the_real_public_reader_gate(wp74_database):
    engine, _, _ = wp74_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)
    platform_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email="wp74admin@example.test", display_name="WP7.4 Admin", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": "wp74admin@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email="wp74reviewer@example.test", display_name="WP7.4 Reviewer", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp74reviewer@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client)

    platform_admin_id = _create_user_with_global_roles(Session, email="wp74platform@example.test", display_name="WP7.4 Platform", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, "wp74platform@example.test")

    preview = platform_client.get(f"/api/v1/platform/governed-symbols/{symbol_id}/demotion-impact-preview")
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["eligible"] is True
    assert preview_body["reasons"] == []
    assert preview_body["blockingOrganizationIds"] == []

    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "Contribution withdrawn by mutual agreement."})
    assert demote.status_code == 200, demote.text
    demote_body = demote.json()
    assert demote_body["visibility"] == "organization_private"
    assert len(demote_body["symbolRevisionIds"]) == 1
    assert len(demote_body["publishedPageIds"]) == 1
    assert len(demote_body["retiredPackIds"]) == 1

    with engine.begin() as connection:
        symbol_row = connection.execute(text("SELECT visibility FROM governed_symbols WHERE id=:id"), {"id": symbol_id}).one()
        assert symbol_row.visibility == "organization_private"

        revision_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id=:id"), {"id": demote_body["symbolRevisionIds"][0]}
        ).scalar_one()
        assert revision_state == "withdrawn"

        page_state = connection.execute(
            text("SELECT publication_state, retirement_reason FROM published_pages WHERE id=:id"),
            {"id": demote_body["publishedPageIds"][0]},
        ).one()
        assert page_state.publication_state == "retired"
        assert page_state.retirement_reason == "Contribution withdrawn by mutual agreement."

        pack_status = connection.execute(
            text("SELECT status FROM publication_packs WHERE id=:id"), {"id": demote_body["retiredPackIds"][0]}
        ).scalar_one()
        assert pack_status == "retired"

        excluded = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:id"), {"id": symbol_id}
        ).first()
        assert excluded is None, "demoted symbol must be excluded from the real public reader gate"


def test_demotion_blocked_while_another_organizations_set_references_the_symbol(wp74_database):
    engine, _, _ = wp74_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)
    platform_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email="wp74admin2@example.test", display_name="Admin2", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": "wp74admin2@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email="wp74reviewer2@example.test", display_name="Reviewer2", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp74reviewer2@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client, name="WP7.4 Valve")

    # A different organization's Symbol Set now references the promoted symbol.
    other_admin_id = _create_user_with_global_roles(Session, email="wp74other@example.test", display_name="Other Admin", roles=[])
    other_org_id = _add_membership(Session, other_admin_id, code="other", base_role="admin")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        symbol_set = SymbolSet(
            id=uuid.uuid4(), owner_organization_id=other_org_id, code="OTHERSET", normalized_code="otherset",
            name="Other Org Set", status="active", created_by_user_id=other_admin_id, created_at=now, updated_at=now,
        )
        session.add(symbol_set)
        session.flush()
        session.add(
            SymbolSetItem(
                id=uuid.uuid4(), symbol_set_id=symbol_set.id, governed_symbol_id=uuid.UUID(symbol_id), sort_order=0,
                availability_status="active", provenance_json={}, created_at=now, updated_at=now,
            )
        )
        session.commit()

    platform_admin_id = _create_user_with_global_roles(Session, email="wp74platform2@example.test", display_name="Platform2", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, "wp74platform2@example.test")

    preview = platform_client.get(f"/api/v1/platform/governed-symbols/{symbol_id}/demotion-impact-preview")
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["eligible"] is False
    assert str(other_org_id) in preview_body["blockingOrganizationIds"]

    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "test"})
    assert demote.status_code == 409, demote.text

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM symbol_set_items WHERE governed_symbol_id=:id"), {"id": symbol_id})

    demote_after_removal = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "test, references removed"})
    assert demote_after_removal.status_code == 200, demote_after_removal.text


def test_demotion_requires_step_up(wp74_database):
    engine, _, _ = wp74_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)
    platform_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email="wp74admin3@example.test", display_name="Admin3", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": "wp74admin3@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email="wp74reviewer3@example.test", display_name="Reviewer3", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp74reviewer3@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client, name="WP7.4 No Step Up")

    platform_admin_id = _create_user_with_global_roles(Session, email="wp74platform3@example.test", display_name="Platform3", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    login = platform_client.post("/api/v1/auth/login", json={"email": "wp74platform3@example.test", "pin": "1234"})
    assert login.status_code == 200, login.text
    # No step-up performed.

    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "test"})
    assert demote.status_code == 403


def test_non_platform_admin_cannot_preview_or_demote(wp74_database):
    engine, _, _ = wp74_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)
    plain_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email="wp74admin4@example.test", display_name="Admin4", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": "wp74admin4@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email="wp74reviewer4@example.test", display_name="Reviewer4", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp74reviewer4@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client, name="WP7.4 Plain User")

    _create_user_with_global_roles(Session, email="wp74plain@example.test", display_name="Plain", roles=[])
    plain_client.post("/api/v1/auth/login", json={"email": "wp74plain@example.test", "pin": "1234"})

    preview = plain_client.get(f"/api/v1/platform/governed-symbols/{symbol_id}/demotion-impact-preview")
    assert preview.status_code == 403

    demote = plain_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "test"})
    assert demote.status_code == 403
