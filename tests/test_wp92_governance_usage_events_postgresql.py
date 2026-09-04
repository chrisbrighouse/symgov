"""Stage 9 WP9.2 regression: governance-mutation `ProductUsageEvent` emission
against a real disposable PostgreSQL container, driven through the real HTTP
routes (not direct ORM inserts) -- unlike WP9.1, WP9.2's own job is wiring
`record_governance_usage_event(...)` calls into already-existing governance
mutation endpoints, so the thing worth proving is that the real request path
actually produces the row, with the right dimensions, in the same transaction
as the mutation itself.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.2) and `backend/symgov_backend/product_usage_events.py`'s own module
docstring, this does not exhaustively cover all 15 wired call sites -- it
proves a representative sample spanning every module WP9.2 touched:

- organization review submit + decide (`organization_symbol_drafts.py`,
  `organization_symbol_review.py`)
- organization-wide scope toggle (`organization_symbol_review.py`)
- public promotion submit + accept (`promotion_requests.py`,
  `organization_promotion_handoff.py`, via the real reviewer decision
  endpoint)
- demotion (`symbol_demotion.py`)
- project create + archive, symbol-set create + archive (`project_service.py`,
  `symbol_set_service.py`)
- organization icon upload, a role/capability change, and platform-admin
  assignment (`organization_service.py`)

Two tests specifically assert the deliberate modeling choice documented in
`product_usage_events.py`: every row this module writes has
`session_mode='organization'` and a real `organization_id`, regardless of the
acting user's own literal session mode/organization at request time --
demotion (acting platform admin's own session is bound to the protected
`symgov` organization, not the demoted symbol's owning organization) and
promotion acceptance (the deciding reviewer's own session is `personal` --
reviewer authority is a global role, not organization membership) both prove
this concretely, since in both cases the actor's own session state differs
from the event's own organization/session-mode fields.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_admin_api import _FakeStorageBridge, _icon_upload_json, _make_png_bytes  # noqa: E402
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles, _make_platform_admin  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import OrganizationMembership, ProductUsageEvent, User  # noqa: E402
from symgov_backend.routes.organizations import get_icon_storage_bridge  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0041"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp92_database():
    with _database("symgov-wp92") as (engine, url, raw_url):
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
    """A superset-of-flags client: WP9.2's own call sites span organization
    review/promotion/demotion (WP7-8's own flag set) plus project/symbol-set
    lifecycle and organization-admin/platform-admin member and icon
    management, none of which wp74's own narrower `_client` enables."""
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        platform_admin_enabled=True,
        symbol_sets_enabled=True,
        organization_admin_enabled=True,
        organization_custom_icons_enabled=True,
        organization_icon_upload_enabled=True,
        organization_pilot_codes=("acme", "symgov"),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_bridge = _FakeStorageBridge()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_icon_storage_bridge] = lambda: fake_bridge
    client = TestClient(app, headers={"origin": "http://testserver"})
    client.fake_bridge = fake_bridge
    return client, TestingSessionLocal


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response.json()


def _step_up(client):
    response = client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})
    assert response.status_code == 200, response.text


def _membership_id(Session, *, organization_id, user_id) -> uuid.UUID:
    with Session() as session:
        return session.execute(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        ).scalar_one()


def _email(Session, user_id) -> str:
    with Session() as session:
        return session.get(User, user_id).email


def _event(Session, *, event_type, **filters) -> ProductUsageEvent:
    with Session() as session:
        query = session.query(ProductUsageEvent).filter(ProductUsageEvent.event_type == event_type)
        for column, value in filters.items():
            query = query.filter(getattr(ProductUsageEvent, column) == value)
        return query.one()


def _create_org_symbol(admin_client, *, name):
    response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A WP9.2 test symbol."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["id"], body["currentRevisionId"]


def test_organization_review_submit_and_decide_emit_usage_events(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92admin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    symbol_id, revision_id = _create_org_symbol(admin_client, name="WP9.2 Review Symbol")

    submit = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit.status_code == 200, submit.text
    submission_id = submit.json()["id"]

    submitted_event = _event(
        Session,
        event_type="organization_review_submitted",
        governed_symbol_id=uuid.UUID(symbol_id),
        symbol_revision_id=uuid.UUID(revision_id),
    )
    assert submitted_event.organization_id == organization_id
    assert submitted_event.user_id == admin_id
    assert submitted_event.session_mode == "organization"
    assert submitted_event.symbol_source == "organization_private"

    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submission_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    decided_event = _event(
        Session,
        event_type="organization_review_decided",
        governed_symbol_id=uuid.UUID(symbol_id),
        symbol_revision_id=uuid.UUID(revision_id),
    )
    assert decided_event.organization_id == organization_id
    assert decided_event.user_id == admin_id
    assert decided_event.session_mode == "organization"


def test_organization_wide_toggle_emits_usage_event(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92wide-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Wide Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    # This symbol is deliberately never promoted -- `set_organization_wide`
    # requires `visibility == 'organization_private'`, so a promoted (public)
    # symbol would fail this toggle with a 400/409, not the demotion path.
    symbol_id, revision_id = _create_org_symbol(admin_client, name="WP9.2 Org-Wide Symbol")
    submit = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit.status_code == 200, submit.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    toggle = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/organization-wide", json={"enabled": True})
    assert toggle.status_code == 200, toggle.text
    assert toggle.json()["organizationWide"] is True

    event = _event(Session, event_type="organization_wide_changed", governed_symbol_id=uuid.UUID(symbol_id))
    assert event.organization_id == organization_id
    assert event.symbol_revision_id == uuid.UUID(revision_id)
    assert event.symbol_source == "organization_private"
    assert event.session_mode == "organization"


def test_promotion_submit_and_accept_emit_usage_events(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92promoadmin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Promo Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp92promoreviewer-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.2 Promo Reviewer", roles=["reviewer"])
    reviewer_login = _login(reviewer_client, reviewer_email)
    # The deciding reviewer holds a *global* role, not organization membership --
    # their own session stays 'personal', unlike the event's own 'organization'
    # scope below. This is the load-bearing contrast this test asserts on.
    assert reviewer_login["user"]["session"]["mode"] == "personal"

    symbol_id, revision_id = _create_org_symbol(admin_client, name="WP9.2 Promotion Symbol")
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful across organizations.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    submitted_event = _event(Session, event_type="publication_submitted", governed_symbol_id=uuid.UUID(symbol_id))
    assert submitted_event.organization_id == organization_id
    assert submitted_event.user_id == admin_id
    assert submitted_event.session_mode == "organization"

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["currentStage"] == "published"

    decided_event = _event(Session, event_type="publication_decided", governed_symbol_id=uuid.UUID(symbol_id))
    # The deciding reviewer's own session never bound to any organization, yet
    # the emitted event still carries the *promoted symbol's owning*
    # organization and the hardcoded 'organization' session_mode -- proving
    # `record_governance_usage_event`'s modeling choice end-to-end.
    assert decided_event.organization_id == organization_id
    assert decided_event.session_mode == "organization"
    assert decided_event.symbol_source == "public"


def test_demotion_emits_usage_event_scoped_to_symbol_owner_not_actor_session(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)
    platform_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92demoteadmin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Demote Admin", roles=[]
    )
    acme_org_id = _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp92demotereviewer-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.2 Demote Reviewer", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    symbol_id, revision_id = _create_org_symbol(admin_client, name="WP9.2 Demotion Symbol")
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful.", "sharingAcknowledgment": True},
    )
    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{submit_promotion.json()['id']}/open-review")
    review_case_id = open_review.json()["reviewCaseId"]
    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text

    platform_admin_id = _create_user_with_global_roles(
        Session, email=f"wp92platform-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Platform Admin", roles=[]
    )
    _make_platform_admin(Session, platform_admin_id)
    platform_login = _login(platform_client, _email(Session, platform_admin_id))
    assert platform_login["user"]["session"]["mode"] == "organization"
    symgov_org_id = uuid.UUID(platform_login["user"]["organization"]["id"])
    assert symgov_org_id != acme_org_id
    _step_up(platform_client)

    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "WP9.2 regression test demotion."})
    assert demote.status_code == 200, demote.text

    event = _event(Session, event_type="public_symbol_demoted", governed_symbol_id=uuid.UUID(symbol_id))
    # The acting platform admin's own live session is bound to the protected
    # 'symgov' organization (required by `require_platform_admin`), but the
    # emitted event's `organization_id` is the *demoted symbol's own owning*
    # organization ('acme') -- the two deliberately differ, per
    # `record_governance_usage_event`'s own module docstring.
    assert event.organization_id == acme_org_id
    assert event.organization_id != symgov_org_id
    assert event.user_id == platform_admin_id
    assert event.session_mode == "organization"
    assert event.symbol_source == "organization_private"


def test_project_create_and_archive_emit_usage_events(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92project-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Project Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    code = f"P-{uuid.uuid4().hex[:8].upper()}"
    create = admin_client.post("/api/v1/org/me/projects", json={"code": code, "name": "WP9.2 Project"})
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    created_event = _event(Session, event_type="project_created", project_id=uuid.UUID(project_id))
    assert created_event.organization_id == organization_id
    assert created_event.user_id == admin_id
    assert created_event.session_mode == "organization"

    archive = admin_client.patch(f"/api/v1/org/me/projects/{project_id}", json={"status": "closed"})
    assert archive.status_code == 200, archive.text
    assert archive.json()["status"] == "closed"

    archived_event = _event(Session, event_type="project_archived", project_id=uuid.UUID(project_id))
    assert archived_event.organization_id == organization_id


def test_symbol_set_create_and_archive_emit_usage_events(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92set-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Set Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    code = f"SET-{uuid.uuid4().hex[:8].upper()}"
    create = admin_client.post("/api/v1/org/me/symbol-sets", json={"code": code, "name": "WP9.2 Symbol Set"})
    assert create.status_code == 201, create.text
    set_id = create.json()["id"]

    created_event = _event(Session, event_type="set_created", symbol_set_id=uuid.UUID(set_id))
    assert created_event.organization_id == organization_id
    assert created_event.user_id == admin_id
    assert created_event.session_mode == "organization"

    archive = admin_client.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": "archived"})
    assert archive.status_code == 200, archive.text
    assert archive.json()["status"] == "archived"

    archived_event = _event(Session, event_type="set_archived", symbol_set_id=uuid.UUID(set_id))
    assert archived_event.organization_id == organization_id


def test_organization_icon_upload_emits_usage_event(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92icon-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Icon Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin")
    _login(admin_client, _email(Session, admin_id))
    _step_up(admin_client)

    upload = admin_client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))
    assert upload.status_code == 200, upload.text
    assert upload.json()["hasCustomIcon"] is True

    event = _event(Session, event_type="organization_icon_uploaded", organization_id=organization_id)
    assert event.user_id == admin_id
    assert event.session_mode == "organization"


def test_role_capability_change_emits_usage_event(wp92_database):
    engine, _, _ = wp92_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp92roleadmin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Role Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin")
    _login(admin_client, _email(Session, admin_id))
    _step_up(admin_client)

    member_id = _create_user_with_global_roles(
        Session, email=f"wp92member-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Member", roles=[]
    )
    _add_membership(Session, member_id, code="acme", base_role="user")
    membership_id = _membership_id(Session, organization_id=organization_id, user_id=member_id)

    patch = admin_client.patch(f"/api/v1/org/me/members/{membership_id}", json={"grantCapability": "contributor"})
    assert patch.status_code == 200, patch.text
    assert "contributor" in [c["capability"] for c in patch.json()["capabilities"]]

    event = _event(Session, event_type="organization_role_changed", organization_id=organization_id, user_id=admin_id)
    assert event.session_mode == "organization"


def test_platform_admin_assign_emits_usage_event(wp92_database):
    engine, _, _ = wp92_database
    platform_client, Session = _client(engine)

    actor_id = _create_user_with_global_roles(
        Session, email=f"wp92grantor-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Grantor", roles=[]
    )
    _make_platform_admin(Session, actor_id)
    _login(platform_client, _email(Session, actor_id))
    _step_up(platform_client)

    candidate_id = _create_user_with_global_roles(
        Session, email=f"wp92candidate-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.2 Candidate", roles=[]
    )
    symgov_org_id = _add_membership(Session, candidate_id, code="symgov", base_role="admin")

    grant = platform_client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})
    assert grant.status_code == 201, grant.text

    event = _event(Session, event_type="platform_admin_assigned", user_id=actor_id)
    assert event.organization_id == symgov_org_id
    assert event.session_mode == "organization"
