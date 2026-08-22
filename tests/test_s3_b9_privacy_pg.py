"""Tests for member-directory privacy and inactive-member lifecycle (S3-B9/S3-C5) using PostgreSQL."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from symgov_backend.app import create_app
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import (
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    User,
    UserSession,
    UserSubscription,
)
from symgov_backend.auth import hash_session_token, upsert_user

# Import helpers from the existing PG test file
from test_organization_postgresql_migration import _insert_user, _insert_organization, organization_database

@pytest.fixture
def pg_client(organization_database):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=organization_database)

    def override_get_db_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from symgov_backend.settings import SymgovAPISettings, get_settings
    def override_get_settings():
        return SymgovAPISettings(
            organizations_enabled=True,
            organization_admin_enabled=True,
            platform_admin_enabled=True,
            organization_pilot_codes=("privacy-pg", "history-pg"),
        )

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_settings] = override_get_settings
    return TestClient(app)

def _seed_org_context_pg(session, org_code: str, admin_email: str, user_email: str):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # Create Organization
    org = Organization(
        id=uuid.uuid4(),
        code=org_code,
        normalized_code=org_code.lower(),
        display_name=f"Org {org_code}",
        is_active=True,
        entitlement_status="active",
        created_at=now,
        updated_at=now,
        name_key=org_code.lower(),
        fallback_icon_svg="<svg></svg>",
        is_protected=False,
        icon_seed_version="v1",
    )
    session.add(org)

    # Create Admin User
    admin = upsert_user(
        session,
        email=admin_email,
        display_name=f"Admin {org_code}",
        roles=[],
        must_change_pin=False,
    )

    # Create Admin Membership
    admin_membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org.id,
        user_id=admin.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(admin_membership)
    session.add(OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=admin_membership.id,
        base_role="admin",
        is_active=True,
        assigned_at=now,
    ))

    # Create Regular User
    regular = upsert_user(
        session,
        email=user_email,
        display_name=f"Regular {org_code}",
        roles=[],
        must_change_pin=False,
    )

    # Create Regular Membership
    regular_membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org.id,
        user_id=regular.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(regular_membership)
    session.add(OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=regular_membership.id,
        base_role="user",
        is_active=True,
        assigned_at=now,
    ))

    session.commit()
    return org, admin, regular, admin_membership, regular_membership

def _create_org_session_pg(session, user, org):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    session_id = uuid.uuid4()
    token_plain = "test-token-" + str(session_id)
    token_hash = hash_session_token(token_plain)

    import datetime as dt
    user_session = UserSession(
        id=session_id,
        auth_user_id=user.id,
        token_hash=token_hash,
        session_mode="organization",
        active_organization_id=org.id,
        purpose="application",
        created_at=now,
        expires_at=now + dt.timedelta(days=1),
    )

    session.add(user_session)
    session.commit()
    return token_plain

def test_member_directory_requires_admin_authority_pg(pg_client, organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        org, admin, regular, _admin_m, _regular_m = _seed_org_context_pg(
            session, "PRIVACY-PG", "admin-pg@example.test", "user-pg@example.test"
        )
        admin_token = _create_org_session_pg(session, admin, org)
        user_token = _create_org_session_pg(session, regular, org)

    # 1. Admin can list members
    resp = pg_client.get("/api/v1/org/me/members", cookies={"symgov_session": admin_token})
    assert resp.status_code == 200

    # 2. Regular user MUST fail closed
    resp = pg_client.get("/api/v1/org/me/members", cookies={"symgov_session": user_token})
    assert resp.status_code == 403

def test_member_directory_includes_inactive_members_pg(pg_client, organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        org, admin, regular, _admin_m, regular_m = _seed_org_context_pg(
            session, "HISTORY-PG", "admin-hist-pg@example.test", "user-hist-pg@example.test"
        )
        admin_token = _create_org_session_pg(session, admin, org)

        # Deactivate the regular user
        now = datetime.now(timezone.utc).replace(microsecond=0)
        regular_m.status = "inactive"
        regular_m.deactivated_at = now
        # Revoke role
        role = session.query(OrganizationRoleAssignment).filter_by(membership_id=regular_m.id, is_active=True).one()
        role.is_active = False
        role.revoked_at = now
        session.commit()

    # Admin should still see the inactive member
    resp = pg_client.get("/api/v1/org/me/members", cookies={"symgov_session": admin_token})
    assert resp.status_code == 200
    members = resp.json()["items"]
    # Should see both admin and the deactivated regular user
    emails = [m["email"] for m in members] # Note: response uses camelCase
    assert "admin-hist-pg@example.test" in emails
    assert "user-hist-pg@example.test" in emails

    # Check that the deactivated user has truthful role/history state
    regular_data = next(m for m in members if m["email"] == "user-hist-pg@example.test")
    assert regular_data["status"] == "inactive"
    assert regular_data["baseRole"] == "user"
