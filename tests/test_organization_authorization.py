"""Tests for composable FastAPI dependency functions that enforce organization authorization."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import (
    get_db_session,
    require_capability,
    require_organization_admin,
    require_organization_session,
    require_platform_admin,
)
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


def _build_client(*, pilots=()):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        user = upsert_user(
            session,
            email="member@example.test",
            display_name="Member",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        session.commit()
        user_id = user.id

    app = create_app()

    @app.get("/test-require-org-session")
    def _probe_org_session(user=Depends(require_organization_session)):
        return {"ok": True, "orgId": str(user.active_organization_id)}

    @app.get("/test-require-org-admin")
    def _probe_org_admin(user=Depends(require_organization_admin)):
        return {"ok": True, "role": user.organization_base_role}

    @app.get("/test-require-platform-admin")
    def _probe_platform_admin(user=Depends(require_platform_admin)):
        return {"ok": True}

    @app.get("/test-require-capability")
    def _probe_capability(user=Depends(require_capability("contributor"))):
        return {"ok": True}

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        symbol_sets_enabled=True,
        organization_symbols_enabled=True,
        organization_agents_enabled=True,
        organization_pilot_codes=pilots,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), Session, user_id


def _add_membership(
    Session,
    user_id,
    code,
    *,
    base_role="user",
    capabilities=(),
    platform_admin=False,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = Organization(
            id=uuid.uuid4(),
            code=code if code == "symgov" else code.upper(),
            normalized_code=code.lower(),
            display_name=f"{code.upper()} Org",
            name_key=f"{code.lower()}-org",
            entitlement_status="active",
            is_active=True,
            is_protected=code == "symgov",
            fallback_icon_svg="<svg/>",
            created_at=now,
            updated_at=now,
        )
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=user_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([organization, membership])
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role=base_role,
                is_active=True,
                assigned_at=now,
            )
        )
        for capability in capabilities:
            session.add(
                OrganizationMemberCapability(
                    id=uuid.uuid4(),
                    membership_id=membership.id,
                    capability=capability,
                    is_active=True,
                    granted_at=now,
                )
            )
        if platform_admin:
            session.add(
                PlatformRoleAssignment(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    role="platform_admin",
                    is_active=True,
                    assigned_at=now,
                )
            )
        session.commit()
        return organization.id, membership.id


def _login(client):
    return client.post("/api/v1/auth/login", json={"email": "member@example.test", "pin": "1234"})


# --- require_organization_session ---


def test_require_organization_session_rejects_unauthenticated():
    client, _, _ = _build_client(pilots=("acme",))

    response = client.get("/test-require-org-session")

    assert response.status_code == 401


def test_require_organization_session_rejects_personal_session():
    client, _, _ = _build_client()
    login = _login(client)
    assert login.json()["user"]["session"]["mode"] == "personal"

    response = client.get("/test-require-org-session")

    assert response.status_code == 403
    assert response.json()["detail"] == "An organization-bound session is required."


def test_require_organization_session_permits_org_session():
    client, Session, user_id = _build_client(pilots=("acme",))
    org_id, _ = _add_membership(Session, user_id, "acme")
    login = _login(client)
    assert login.json()["user"]["session"]["mode"] == "organization"

    response = client.get("/test-require-org-session")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["orgId"] == str(org_id)


# --- require_organization_admin ---


def test_require_organization_admin_rejects_personal_session():
    client, _, _ = _build_client()
    _login(client)

    response = client.get("/test-require-org-admin")

    assert response.status_code == 403
    assert response.json()["detail"] == "An organization-bound session is required."


def test_require_organization_admin_rejects_non_admin_org_session():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="user")
    _login(client)

    response = client.get("/test-require-org-admin")

    assert response.status_code == 403
    assert response.json()["detail"] == "Organization Admin privileges are required."


def test_require_organization_admin_permits_org_admin_session():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="admin")
    _login(client)

    response = client.get("/test-require-org-admin")

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


# --- require_platform_admin ---


def test_require_platform_admin_rejects_non_platform_admin():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="admin")
    _login(client)

    response = client.get("/test-require-platform-admin")

    assert response.status_code == 403
    assert response.json()["detail"] == "Platform Admin privileges are required."


def test_require_platform_admin_requires_symgov_org_membership_not_just_role_row():
    """A PlatformRoleAssignment without Symgov org admin membership does not confer platform admin."""
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="admin", platform_admin=True)
    login = _login(client)
    assert login.json()["user"]["isPlatformAdmin"] is False

    response = client.get("/test-require-platform-admin")

    assert response.status_code == 403


def test_require_platform_admin_permits_symgov_org_admin_with_platform_role():
    client, Session, user_id = _build_client(pilots=("symgov",))
    _add_membership(Session, user_id, "symgov", base_role="admin", platform_admin=True)
    login = _login(client)
    assert login.json()["user"]["isPlatformAdmin"] is True

    response = client.get("/test-require-platform-admin")

    assert response.status_code == 200
    assert response.json()["ok"] is True


# --- require_capability ---


def test_require_capability_rejects_personal_session():
    client, _, _ = _build_client()
    _login(client)

    response = client.get("/test-require-capability")

    assert response.status_code == 403
    assert response.json()["detail"] == "An organization-bound session is required."


def test_require_capability_rejects_org_session_without_required_capability():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme")
    _login(client)

    response = client.get("/test-require-capability")

    assert response.status_code == 403
    assert "contributor" in response.json()["detail"]


def test_require_capability_permits_org_session_with_required_capability():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", capabilities=("contributor",))
    _login(client)

    response = client.get("/test-require-capability")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_require_capability_rejects_org_session_with_different_capability():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", capabilities=("symbol_reviewer",))
    _login(client)

    response = client.get("/test-require-capability")

    assert response.status_code == 403
    assert "contributor" in response.json()["detail"]
