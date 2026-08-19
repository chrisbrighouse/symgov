"""Tests for the Platform Admin API (Stage 3, Slice 3B)."""
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
    # Drop PostgreSQL-specific partial unique indexes that SQLite degrades to full unique indexes.
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


def _build_client(*, platform_admin_enabled=True, organizations_enabled=True):
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
        organization_pilot_codes=("symgov",),
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    return client, Session, platform_admin_id, candidate_id, plain_id


def _seed_symgov_org_with_platform_admin(Session, admin_id, *, candidate_admin_org_id=None):
    """Create the protected Symgov org, make admin_id an org admin with an active
    PlatformRoleAssignment. Optionally add candidate_admin_org_id as a Symgov org admin
    too (required before they can be granted platform admin)."""
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

        if candidate_admin_org_id is not None:
            candidate_membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=org.id,
                user_id=candidate_admin_org_id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(candidate_membership)
            session.flush()
            session.add(
                OrganizationRoleAssignment(
                    id=uuid.uuid4(),
                    membership_id=candidate_membership.id,
                    base_role="admin",
                    is_active=True,
                    assigned_at=now,
                )
            )

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

def test_feature_flag_off_returns_404():
    client, Session, admin_id, _, _ = _build_client(platform_admin_enabled=False)
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.get("/api/v1/platform/admins")

    assert response.status_code == 404


# --- GET /platform/admins ---

def test_list_requires_platform_admin_session():
    client, Session, admin_id, _, plain_id = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    client.post("/api/v1/auth/login", json={"email": "plain@example.test", "pin": "1234"})

    response = client.get("/api/v1/platform/admins")

    assert response.status_code == 403


def test_list_returns_paginated_admins():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.get("/api/v1/platform/admins")

    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["pageSize"] == 50
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["userId"] == str(admin_id)
    assert item["email"] == "platform-admin@example.test"
    assert item["displayName"] == "Platform Admin"
    assert item["userIsActive"] is True
    assert "grantedAt" in item


# --- POST /platform/admins ---

def test_grant_requires_step_up():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_select_org(client, "platform-admin@example.test", org_id)

    response = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    assert response.status_code == 403


def test_grant_platform_admin_adds_user_to_list():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    assert response.status_code == 201
    assert response.json()["userId"] == str(candidate_id)

    listing = client.get("/api/v1/platform/admins")
    user_ids = {item["userId"] for item in listing.json()["items"]}
    assert str(candidate_id) in user_ids
    assert listing.json()["total"] == 2


def test_grant_duplicate_is_idempotent():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    first = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})
    second = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["userId"] == str(candidate_id)

    listing = client.get("/api/v1/platform/admins")
    assert listing.json()["total"] == 2


def test_grant_rejects_non_symgov_admin_candidate():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    assert response.status_code == 400


# --- DELETE /platform/admins/{user_id} ---

def test_revoke_requires_step_up():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)
    client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    # Fresh login without step-up.
    _login_and_select_org(client, "platform-admin@example.test", org_id)
    response = client.delete(f"/api/v1/platform/admins/{candidate_id}")

    assert response.status_code == 403


def test_revoke_platform_admin_removes_from_list():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)
    client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    response = client.delete(f"/api/v1/platform/admins/{candidate_id}")

    assert response.status_code == 204
    listing = client.get("/api/v1/platform/admins")
    user_ids = {item["userId"] for item in listing.json()["items"]}
    assert str(candidate_id) not in user_ids
    assert listing.json()["total"] == 1


def test_revoke_last_admin_is_rejected():
    client, Session, admin_id, _, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    response = client.delete(f"/api/v1/platform/admins/{admin_id}")

    assert response.status_code == 400
    listing = client.get("/api/v1/platform/admins")
    assert listing.json()["total"] == 1


# --- Audit ---

def test_grant_emits_audit_event():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)

    with patch("symgov_backend.organization_service._emit_audit") as mock_emit:
        response = client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    assert response.status_code == 201
    actions = [call.kwargs.get("action") for call in mock_emit.call_args_list]
    assert "platform_admin.assigned" in actions


def test_revoke_emits_audit_event():
    client, Session, admin_id, candidate_id, _ = _build_client()
    org_id = _seed_symgov_org_with_platform_admin(
        Session, admin_id, candidate_admin_org_id=candidate_id
    )
    _login_and_step_up(client, "platform-admin@example.test", org_id)
    client.post("/api/v1/platform/admins", json={"userId": str(candidate_id)})

    with patch("symgov_backend.organization_service._emit_audit") as mock_emit:
        response = client.delete(f"/api/v1/platform/admins/{candidate_id}")

    assert response.status_code == 204
    actions = [call.kwargs.get("action") for call in mock_emit.call_args_list]
    assert "platform_admin.revoked" in actions
