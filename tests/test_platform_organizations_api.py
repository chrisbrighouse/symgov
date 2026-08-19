"""Tests for the Platform Admin organization directory API (Stage 3, Slice 3C)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import (
    AuthLoginAttemptEvent,
    AuthLoginThrottleBucket,
    AuthOrganizationSelectionChallenge,
    AuthThrottleRecoveryEvent,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    SubscriptionEvent,
    User,
    UserRole,
    UserSession,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    """AuditEvent uses JSONB (PostgreSQL-only). Stub _emit_audit in all tests by default.
    Audit-specific tests override this by patching explicitly and capturing calls."""
    with patch("symgov_backend.organization_service._emit_audit"):
        yield


def _create_tables(engine) -> None:
    for table in (
        User.__table__,
        UserRole.__table__,
        Organization.__table__,
        OrganizationMembership.__table__,
        OrganizationRoleAssignment.__table__,
        OrganizationMemberCapability.__table__,
        PlatformRoleAssignment.__table__,
        UserSession.__table__,
        AuthOrganizationSelectionChallenge.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
    ):
        original = table.constraints
        try:
            table.constraints = {
                item
                for item in original
                if not (
                    isinstance(item, CheckConstraint)
                    and ("~" in str(item.sqltext) or "interval" in str(item.sqltext))
                )
            }
            table.create(engine)
        finally:
            table.constraints = original
    with engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_org_role_active_membership"))
        conn.execute(text("DROP INDEX IF EXISTS uq_org_capability_active_membership"))
        conn.execute(text("DROP INDEX IF EXISTS uq_platform_role_active_user_role"))
        conn.commit()


def _build_engine_and_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _build_client(*, platform_admin_enabled=True, organizations_enabled=True, pilot_codes=("symgov",)):
    Session = _build_engine_and_session()
    with Session() as session:
        platform_admin_user = upsert_user(
            session,
            email="platform-admin@example.test",
            display_name="Platform Admin",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        candidate_user = upsert_user(
            session,
            email="candidate@example.test",
            display_name="Candidate Admin",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        plain_user = upsert_user(
            session,
            email="plain@example.test",
            display_name="Plain User",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        session.commit()
        platform_admin_id = platform_admin_user.id
        candidate_id = candidate_user.id
        plain_id = plain_user.id

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=organizations_enabled,
        platform_admin_enabled=platform_admin_enabled,
        organization_admin_enabled=False,
        symbol_sets_enabled=False,
        organization_symbols_enabled=False,
        organization_agents_enabled=False,
        organization_pilot_codes=pilot_codes,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    return client, Session, platform_admin_id, candidate_id, plain_id


def _seed_symgov_org_with_platform_admin(Session, admin_id):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        org = Organization(
            id=uuid.uuid4(),
            code="symgov",
            normalized_code="symgov",
            display_name="Symgov",
            name_key="symgov",
            entitlement_status="active",
            is_active=True,
            is_protected=True,
            fallback_icon_svg="<svg><text>S</text></svg>",
            created_at=now,
            updated_at=now,
        )
        session.add(org)
        session.flush()

        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=admin_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            )
        )
        session.add(
            PlatformRoleAssignment(
                id=uuid.uuid4(),
                user_id=admin_id,
                role="platform_admin",
                is_active=True,
                assigned_at=now,
            )
        )
        session.commit()
        return org.id


def _seed_commercial_org(Session, *, code="ACME", entitlement_status="active"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        org = Organization(
            id=uuid.uuid4(),
            code=code,
            normalized_code=code.lower(),
            display_name=f"{code} Inc",
            name_key=f"{code.lower()} inc",
            entitlement_status=entitlement_status,
            is_active=True,
            is_protected=False,
            fallback_icon_svg="<svg><text>A</text></svg>",
            created_at=now,
            updated_at=now,
        )
        session.add(org)
        session.commit()
        return org.id


def _login_and_select_org(client, email, org_id):
    client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    client.post("/api/v1/auth/organizations/select", json={"organizationId": str(org_id)})


def _step_up(client):
    client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})


def _login_and_step_up(client, email, org_id):
    _login_and_select_org(client, email, org_id)
    _step_up(client)


# --- Feature flag ---

def test_directory_feature_flag_off_returns_404():
    client, Session, admin_id, _, _ = _build_client(platform_admin_enabled=False)
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.get("/api/v1/platform/organizations")

    assert response.status_code == 404


# --- GET /platform/organizations ---

def test_list_requires_platform_admin_session():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    client.post("/api/v1/auth/login", json={"email": "plain@example.test", "pin": "1234"})

    response = client.get("/api/v1/platform/organizations")

    assert response.status_code == 403


def test_list_returns_paginated_organizations():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _seed_commercial_org(Session, code="ACME")
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.get("/api/v1/platform/organizations")

    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["pageSize"] == 50
    assert data["total"] == 2
    codes = {item["code"] for item in data["items"]}
    assert codes == {"symgov", "ACME"}


# --- POST /platform/organizations ---

def test_create_requires_step_up():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.post(
        "/api/v1/platform/organizations",
        json={"code": "ACME", "displayName": "Acme Inc", "initialAdminUserId": str(candidate_id)},
    )

    assert response.status_code == 403


def test_create_organization_adds_to_directory():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(
        "/api/v1/platform/organizations",
        json={"code": "ACME", "displayName": "Acme Inc", "initialAdminUserId": str(candidate_id)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "ACME"
    assert body["entitlementStatus"] == "active"

    listing = client.get("/api/v1/platform/organizations")
    assert listing.json()["total"] == 2


def test_create_rejects_duplicate_code():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _seed_commercial_org(Session, code="ACME")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(
        "/api/v1/platform/organizations",
        json={"code": "ACME", "displayName": "Acme Duplicate", "initialAdminUserId": str(candidate_id)},
    )

    assert response.status_code == 400


def test_create_rejects_non_platform_admin_caller():
    client, Session, admin_id, candidate_id, plain_id = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    client.post("/api/v1/auth/login", json={"email": "plain@example.test", "pin": "1234"})
    _step_up(client)

    response = client.post(
        "/api/v1/platform/organizations",
        json={"code": "ACME", "displayName": "Acme Inc", "initialAdminUserId": str(candidate_id)},
    )

    assert response.status_code == 403


# --- POST /platform/organizations/{id}/suspend ---

def test_suspend_requires_step_up():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")

    assert response.status_code == 403


def test_suspend_organization_sets_suspended():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")

    assert response.status_code == 200
    assert response.json()["entitlementStatus"] == "suspended"


def test_suspend_revokes_bound_sessions_for_that_organization():
    client, Session, admin_id, _, _ = _build_client(pilot_codes=("symgov", "acme"))
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        member = upsert_user(
            session,
            email="member@example.test",
            display_name="Acme Member",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        session.commit()
        member_membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=acme_id,
            user_id=member.id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(member_membership)
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=member_membership.id,
                base_role="user",
                is_active=True,
                assigned_at=now,
            )
        )
        session.commit()
        member_id = member.id

    # A second client sharing the same app/session factory acts as the Acme member.
    member_client = TestClient(client.app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    _login_and_select_org(member_client, "member@example.test", acme_id)

    with Session() as session:
        active_before = (
            session.query(UserSession)
            .filter(UserSession.auth_user_id == member_id, UserSession.revoked_at.is_(None))
            .one()
        )
        assert active_before.active_organization_id == acme_id

    _login_and_step_up(client, "platform-admin@example.test", org_id)
    response = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")
    assert response.status_code == 200

    with Session() as session:
        remaining = (
            session.query(UserSession)
            .filter(
                UserSession.auth_user_id == member_id,
                UserSession.active_organization_id == acme_id,
                UserSession.revoked_at.is_(None),
            )
            .count()
        )
        assert remaining == 0


def test_suspend_protected_org_is_rejected():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{org_id}/suspend")

    assert response.status_code == 400


def test_suspend_unknown_organization_returns_404():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{uuid.uuid4()}/suspend")

    assert response.status_code == 404


def test_suspend_is_idempotent():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    first = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")
    second = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["entitlementStatus"] == "suspended"


# --- POST /platform/organizations/{id}/reactivate ---

def test_reactivate_requires_step_up():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME", entitlement_status="suspended")
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{acme_id}/reactivate")

    assert response.status_code == 403


def test_reactivate_organization_sets_active():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME", entitlement_status="suspended")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{acme_id}/reactivate")

    assert response.status_code == 200
    assert response.json()["entitlementStatus"] == "active"


def test_reactivate_is_idempotent():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    first = client.post(f"/api/v1/platform/organizations/{acme_id}/reactivate")
    second = client.post(f"/api/v1/platform/organizations/{acme_id}/reactivate")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["entitlementStatus"] == "active"


def test_reactivate_unknown_organization_returns_404():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post(f"/api/v1/platform/organizations/{uuid.uuid4()}/reactivate")

    assert response.status_code == 404


# --- Audit ---

def test_create_emits_audit_event():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    with patch("symgov_backend.organization_service._emit_audit") as mock_emit:
        response = client.post(
            "/api/v1/platform/organizations",
            json={"code": "ACME", "displayName": "Acme Inc", "initialAdminUserId": str(candidate_id)},
        )

    assert response.status_code == 201
    actions = [call.kwargs.get("action") for call in mock_emit.call_args_list]
    assert "organization.created" in actions


def test_suspend_emits_audit_event():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    with patch("symgov_backend.organization_service._emit_audit") as mock_emit:
        response = client.post(f"/api/v1/platform/organizations/{acme_id}/suspend")

    assert response.status_code == 200
    actions = [call.kwargs.get("action") for call in mock_emit.call_args_list]
    assert "organization.suspended" in actions


def test_reactivate_emits_audit_event():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME", entitlement_status="suspended")
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    with patch("symgov_backend.organization_service._emit_audit") as mock_emit:
        response = client.post(f"/api/v1/platform/organizations/{acme_id}/reactivate")

    assert response.status_code == 200
    actions = [call.kwargs.get("action") for call in mock_emit.call_args_list]
    assert "organization.reactivated" in actions
