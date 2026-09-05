"""Stage 10 WP10.4 regression: the Organization Steward / Platform Governance
routes (`routes/agents.py`), driven through real HTTP against a real
disposable PostgreSQL container -- not direct ORM/service calls, since the
thing worth proving here is the route-level authorization split (self-scoped
Organization Admin vs broader Platform Admin), the on-demand run gating
(disabled/unconfigured capability returns 409, never runs silently), and the
step-up requirement on `AgentConfiguration` mutations (I-25).

Detection-logic correctness (which finding fires under which condition) is
already covered by `test_wp102_organization_steward_postgresql.py` and
`test_wp103_platform_governance_postgresql.py` -- this file drives the
simplest possible detectable condition (a `reviewer_coverage_gap`) through
the real HTTP surface instead, to keep its own fixtures small.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles, _make_platform_admin  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import GovernedSymbol, OrganizationSymbolReviewSubmission, SymbolRevision, User  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260905_0044"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture()
def wp104_database():
    # Function-scoped, not module-scoped: several of this file's own tests
    # call `_make_platform_admin`, each adding another permanently-active
    # Platform Administrator to the shared `symgov` organization -- sharing
    # one container across every test in this module would make
    # `test_platform_admin_runs_platform_governance`'s own
    # "exactly one active admin" precondition depend on how many earlier
    # tests happened to run first (mirroring WP10.3's own fixture-scoping
    # rationale for the identical reason).
    with _database("symgov-wp104") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute("GRANT SELECT, INSERT ON audit_events TO symgov_app")
        yield engine, url, raw_url


def _client(engine):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        platform_admin_enabled=True,
        organization_agents_enabled=True,
        organization_pilot_codes=("acme", "acmea", "acmeb", "acmec", "acmestep", "acmeflag", "symgov"),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"})
    return client, TestingSessionLocal


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response.json()


def _step_up(client):
    response = client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})
    assert response.status_code == 200, response.text


def _email(Session, user_id) -> str:
    with Session() as session:
        return session.get(User, user_id).email


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _create_review_submission(Session, *, organization_id, owner_id):
    now = _now()
    with Session() as session:
        symbol_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        symbol = GovernedSymbol(
            id=symbol_id, slug=f"wp104-symbol-{uuid.uuid4().hex[:8]}", canonical_name="WP10.4 Test Symbol",
            category="fire", discipline="fire-safety", owner_id=owner_id, owner_organization_id=organization_id,
            visibility="organization_private", organization_wide=False, current_revision_id=None,
            created_at=now, updated_at=now,
        )
        session.add(symbol)
        session.flush()
        session.add(SymbolRevision(id=revision_id, symbol_id=symbol_id, revision_label="1", lifecycle_state="approved", payload_json={}, author_id=owner_id, created_at=now))
        session.flush()
        symbol.current_revision_id = revision_id
        session.flush()
        session.add(OrganizationSymbolReviewSubmission(
            id=uuid.uuid4(), organization_id=organization_id, governed_symbol_id=symbol_id, symbol_revision_id=revision_id,
            submitted_by_user_id=owner_id, status="active", submitted_at=now,
        ))
        session.commit()


def test_platform_admin_configures_and_org_admin_runs_organization_steward(wp104_database):
    engine, _, _ = wp104_database
    client, Session = _client(engine)

    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp104plat-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Platform Admin", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    org_admin_id = _create_user_with_global_roles(Session, email=f"wp104org-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Org Admin", roles=[])
    organization_id = _add_membership(Session, org_admin_id, code="acme", base_role="admin")  # no symbol_reviewer capability
    _create_review_submission(Session, organization_id=organization_id, owner_id=org_admin_id)

    platform_client, _ = _client(engine)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)

    create_response = platform_client.post(
        "/api/v1/platform/agent-configurations",
        json={"logicalAgentName": "organization_steward", "scopeType": "organization", "scopeId": str(organization_id), "enabled": False},
    )
    assert create_response.status_code == 201, create_response.text
    config_id = create_response.json()["id"]
    assert create_response.json()["enabled"] is False

    org_client, _ = _client(engine)
    _login(org_client, _email(Session, org_admin_id))

    disabled_run = org_client.post("/api/v1/org/me/agents/organization-steward/run")
    assert disabled_run.status_code == 409, disabled_run.text

    patch_response = platform_client.patch(f"/api/v1/platform/agent-configurations/{config_id}", json={"enabled": True})
    assert patch_response.status_code == 200, patch_response.text
    assert patch_response.json()["enabled"] is True

    run_response = org_client.post("/api/v1/org/me/agents/organization-steward/run")
    assert run_response.status_code == 200, run_response.text
    touched_ids = run_response.json()["touchedFindingIds"]
    assert len(touched_ids) == 1

    list_response = org_client.get("/api/v1/org/me/agent-findings")
    assert list_response.status_code == 200, list_response.text
    findings = list_response.json()["items"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["findingType"] == "reviewer_coverage_gap"
    assert finding["status"] == "open"

    ack_response = org_client.post(f"/api/v1/org/me/agent-findings/{finding['id']}/acknowledge")
    assert ack_response.status_code == 200, ack_response.text
    assert ack_response.json()["status"] == "acknowledged"

    dismiss_response = org_client.post(f"/api/v1/org/me/agent-findings/{finding['id']}/dismiss", json={"reason": "Reviewer capability granted out of band."})
    assert dismiss_response.status_code == 200, dismiss_response.text
    assert dismiss_response.json()["status"] == "dismissed"
    assert dismiss_response.json()["dismissReason"] == "Reviewer capability granted out of band."

    second_dismiss = org_client.post(f"/api/v1/org/me/agent-findings/{finding['id']}/dismiss", json={"reason": "Already dismissed."})
    assert second_dismiss.status_code == 409, second_dismiss.text


def test_org_admin_config_mutation_without_step_up_is_rejected(wp104_database):
    engine, _, _ = wp104_database
    client, Session = _client(engine)

    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp104step-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Step-Up Admin", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    org_admin_id = _create_user_with_global_roles(Session, email=f"wp104step2-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Step-Up Org", roles=[])
    organization_id = _add_membership(Session, org_admin_id, code="acmestep", base_role="admin")

    platform_client, _ = _client(engine)
    _login(platform_client, _email(Session, platform_admin_id))
    # Deliberately no _step_up(platform_client) call here.

    response = platform_client.post(
        "/api/v1/platform/agent-configurations",
        json={"logicalAgentName": "organization_steward", "scopeType": "organization", "scopeId": str(organization_id), "enabled": True},
    )
    assert response.status_code == 403, response.text


def test_organization_findings_are_not_visible_to_a_different_organizations_admin(wp104_database):
    engine, _, _ = wp104_database
    client, Session = _client(engine)

    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp104tenant-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Tenant Admin", roles=[])
    _make_platform_admin(Session, platform_admin_id)

    owner_a_id = _create_user_with_global_roles(Session, email=f"wp104owna-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Org A Admin", roles=[])
    organization_a = _add_membership(Session, owner_a_id, code="acmea", base_role="admin")
    _create_review_submission(Session, organization_id=organization_a, owner_id=owner_a_id)

    owner_b_id = _create_user_with_global_roles(Session, email=f"wp104ownb-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Org B Admin", roles=[])
    _add_membership(Session, owner_b_id, code="acmeb", base_role="admin")

    platform_client, _ = _client(engine)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)
    create_response = platform_client.post(
        "/api/v1/platform/agent-configurations",
        json={"logicalAgentName": "organization_steward", "scopeType": "organization", "scopeId": str(organization_a), "enabled": True},
    )
    assert create_response.status_code == 201, create_response.text

    org_a_client, _ = _client(engine)
    _login(org_a_client, _email(Session, owner_a_id))
    run_response = org_a_client.post("/api/v1/org/me/agents/organization-steward/run")
    assert run_response.status_code == 200, run_response.text
    finding_id = run_response.json()["touchedFindingIds"][0]

    org_b_client, _ = _client(engine)
    _login(org_b_client, _email(Session, owner_b_id))

    list_response = org_b_client.get("/api/v1/org/me/agent-findings")
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["items"] == []

    forbidden_ack = org_b_client.post(f"/api/v1/org/me/agent-findings/{finding_id}/acknowledge")
    assert forbidden_ack.status_code == 404, forbidden_ack.text


def test_platform_admin_can_resolve_and_escalate_any_organizations_finding(wp104_database):
    engine, _, _ = wp104_database
    client, Session = _client(engine)

    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp104plat2-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Platform Admin 2", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    org_admin_id = _create_user_with_global_roles(Session, email=f"wp104org2-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Org Admin 2", roles=[])
    organization_id = _add_membership(Session, org_admin_id, code="acmec", base_role="admin")
    _create_review_submission(Session, organization_id=organization_id, owner_id=org_admin_id)

    platform_client, _ = _client(engine)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)
    platform_client.post(
        "/api/v1/platform/agent-configurations",
        json={"logicalAgentName": "organization_steward", "scopeType": "organization", "scopeId": str(organization_id), "enabled": True},
    )

    run_response = platform_client.post(f"/api/v1/platform/organizations/{organization_id}/agents/organization-steward/run")
    assert run_response.status_code == 200, run_response.text
    finding_id = run_response.json()["touchedFindingIds"][0]

    list_response = platform_client.get(f"/api/v1/platform/agent-findings?organizationId={organization_id}")
    assert list_response.status_code == 200, list_response.text
    assert len(list_response.json()["items"]) == 1

    escalate_response = platform_client.post(
        f"/api/v1/platform/agent-findings/{finding_id}/escalate",
        json={"issueReference": "ED-1234"},
    )
    assert escalate_response.status_code == 200, escalate_response.text
    assert escalate_response.json()["issueReference"] == "ED-1234"

    resolve_response = platform_client.post(f"/api/v1/platform/agent-findings/{finding_id}/resolve")
    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["status"] == "resolved"


def test_platform_admin_runs_platform_governance(wp104_database):
    engine, _, _ = wp104_database
    client, Session = _client(engine)

    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp104gov-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Governance Admin", roles=[])
    _make_platform_admin(Session, platform_admin_id)  # exactly one active admin -> continuity risk

    platform_client, _ = _client(engine)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)

    platform_client.post(
        "/api/v1/platform/agent-configurations",
        json={"logicalAgentName": "platform_governance", "scopeType": "platform", "scopeId": None, "enabled": True},
    )

    run_response = platform_client.post("/api/v1/platform/agents/platform-governance/run")
    assert run_response.status_code == 200, run_response.text
    assert len(run_response.json()["touchedFindingIds"]) == 1

    list_response = platform_client.get("/api/v1/platform/agent-findings?logicalAgentName=platform_governance")
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["items"][0]["findingType"] == "platform_admin_continuity_risk"


def test_agent_routes_return_404_when_feature_flag_disabled(wp104_database):
    engine, _, _ = wp104_database
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True, organization_admin_enabled=True, platform_admin_enabled=True,
        organization_agents_enabled=False, organization_pilot_codes=("acmeflag",),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"})

    user_id = _create_user_with_global_roles(TestingSessionLocal, email=f"wp104flag-{uuid.uuid4().hex[:8]}@example.test", display_name="WP10.4 Flag Admin", roles=[])
    _add_membership(TestingSessionLocal, user_id, code="acmeflag", base_role="admin")
    _login(client, _email(TestingSessionLocal, user_id))

    response = client.get("/api/v1/org/me/agent-findings")
    assert response.status_code == 404, response.text
