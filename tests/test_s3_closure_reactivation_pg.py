"""Tests for Platform Admin member diagnostics and reactivation (S3-B9/Objective 3)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from symgov_backend.app import create_app
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import (
    AuditEvent,
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    User,
    UserSession,
)
from symgov_backend.auth import hash_session_token, upsert_user

# Import helpers
from test_organization_postgresql_migration import organization_database

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
            organization_pilot_codes=("symgov",),
        )

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_settings] = override_get_settings
    return TestClient(app, headers={"origin": "http://testserver"})

def _create_platform_admin_session_pg(session, email: str):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user = upsert_user(
        session,
        email=email,
        display_name=f"Platform Admin {email}",
        roles=[],
        must_change_pin=False,
    )
    symgov = session.query(Organization).filter(Organization.normalized_code == "symgov").one_or_none()
    if symgov is None:
        symgov = Organization(
            id=uuid.uuid4(),
            code="symgov",
            normalized_code="symgov",
            display_name="Symgov",
            name_key="symgov",
            entitlement_status="active",
            is_active=True,
            is_protected=True,
            fallback_icon_svg="<svg></svg>",
            created_at=now,
            updated_at=now,
        )
        session.add(symgov)
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=symgov.id,
        user_id=user.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(membership)
    session.add(OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=membership.id,
        base_role="admin",
        is_active=True,
        assigned_at=now,
    ))

    # Manually grant platform_admin role to avoid actor-locking issues in test setup
    session.add(PlatformRoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role="platform_admin",
        is_active=True,
        assigned_at=now,
    ))

    session_id = uuid.uuid4()
    token_plain = "test-platform-token-" + str(session_id)
    token_hash = hash_session_token(token_plain)

    import datetime as dt
    user_session = UserSession(
        id=session_id,
        auth_user_id=user.id,
        token_hash=token_hash,
        session_mode="organization",
        active_organization_id=symgov.id,
        purpose="application",
        created_at=now,
        expires_at=now + dt.timedelta(days=1),
        recent_step_up_at=now,
    )
    session.add(user_session)
    session.commit()
    return token_plain, user

def test_platform_admin_diagnostics_and_reactivation_pg(pg_client, organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        # 1. Setup: Org with one inactive member
        now = datetime.now(timezone.utc).replace(microsecond=0)
        org = Organization(
            id=uuid.uuid4(),
            code="DIAG-ORG",
            normalized_code="diag-org",
            display_name="Diag Org",
            is_active=True,
            entitlement_status="active",
            created_at=now,
            updated_at=now,
            name_key="diag-org",
            fallback_icon_svg="<svg></svg>",
            is_protected=False,
        )
        session.add(org)

        # Add an admin member to satisfy the DB constraint
        admin_user_setup = upsert_user(session, email="org-admin-setup@example.test", display_name="Admin", roles=[], must_change_pin=False)
        admin_membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=admin_user_setup.id,
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

        target_user = upsert_user(session, email="inactive@example.test", display_name="Inactive User", roles=[], must_change_pin=False)
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=target_user.id,
            status="inactive",
            activated_at=now,
            deactivated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        session.commit()

        token, admin_user = _create_platform_admin_session_pg(session, "platform-admin@example.test")
        org_id = org.id
        target_user_id = target_user.id
        membership_id = membership.id
        admin_user_id = admin_user.id

    # 2. Platform Admin should see the inactive member in diagnostics
    resp = pg_client.get(f"/api/v1/platform/organizations/{org_id}/members", cookies={"symgov_session": token})
    assert resp.status_code == 200
    members = resp.json()["items"]
    assert any(m["userId"] == str(target_user_id) for m in members)
    target_data = next(m for m in members if m["userId"] == str(target_user_id))
    assert target_data["status"] == "inactive"

    # 3. Reactivate the member
    resp = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": "Reactivating for testing purposes"},
        cookies={"symgov_session": token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["baseRole"] == "user"

    # 4. Verify in DB
    with Session() as session:
        m = session.query(OrganizationMembership).get(membership_id)
        assert m.status == "active"
        assert m.deactivated_at == now
        # Check audit event
        from symgov_backend.models import AuditEvent
        audit = session.query(AuditEvent).filter_by(
            entity_type="organization_membership",
            entity_id=membership_id,
            action="membership.reactivated"
        ).one()
        assert audit.actor_id == admin_user_id
        assert audit.payload_json["reason"] == "Reactivating for testing purposes"
        assert audit.payload_json["organization_id"] == str(m.organization_id)
        assert audit.payload_json["effective_authority"] == "platform_admin"
        assert audit.payload_json["before"] == {"status": "inactive"}
        assert audit.payload_json["after"] == {"status": "active"}
        assert audit.payload_json["source"] == "api.reactivate_organization_membership"
        assert "recent_step_up_at" in audit.payload_json

        role = session.query(OrganizationRoleAssignment).filter_by(
            membership_id=membership_id,
            is_active=True,
        ).one()
        role_audit = session.query(AuditEvent).filter_by(
            entity_type="organization_role_assignment",
            entity_id=role.id,
            action="membership.base_role_assigned",
        ).one()
        assert role_audit.payload_json["organization_id"] == str(m.organization_id)
        assert role_audit.payload_json["effective_authority"] == "platform_admin"
        assert role_audit.payload_json["before"] == {
            "base_role": None,
            "is_active": False,
        }
        assert role_audit.payload_json["after"] == {
            "base_role": "user",
            "is_active": True,
        }


def test_platform_admin_reactivation_returns_target_beyond_first_100_memberships_pg(
    pg_client, organization_database
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        org = Organization(
            id=uuid.uuid4(),
            code="PAGED-ORG",
            normalized_code="paged-org",
            display_name="Paged Org",
            is_active=True,
            entitlement_status="active",
            created_at=now,
            updated_at=now,
            name_key="paged-org",
            fallback_icon_svg="<svg></svg>",
            is_protected=False,
        )
        session.add(org)
        for index in range(100):
            user = upsert_user(
                session,
                email=f"paged-{index:03d}@example.test",
                display_name=f"Paged member {index:03d}",
                roles=[],
                must_change_pin=False,
            )
            membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=org.id,
                user_id=user.id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(membership)
            session.add(OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin" if index == 0 else "user",
                is_active=True,
                assigned_at=now,
            ))

        target_user = upsert_user(
            session,
            email="paged-target@example.test",
            display_name="Paged target",
            roles=[],
            must_change_pin=False,
        )
        target_created_at = now + timedelta(seconds=1)
        target = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=target_user.id,
            status="inactive",
            activated_at=now,
            deactivated_at=now,
            created_at=target_created_at,
            updated_at=target_created_at,
        )
        session.add(target)
        session.commit()
        token, _admin_user = _create_platform_admin_session_pg(
            session, "paged-platform-admin@example.test"
        )
        target_id = target.id

    response = pg_client.post(
        f"/api/v1/platform/memberships/{target_id}/reactivate",
        json={"reason": "Pagination boundary regression coverage"},
        cookies={"symgov_session": token},
    )

    assert response.status_code == 200
    assert response.json()["membershipId"] == str(target_id)
    assert response.json()["status"] == "active"


def _seed_reactivation_case(
    session,
    *,
    organization_is_active: bool = True,
    entitlement_status: str = "active",
    target_is_active: bool = True,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    case_suffix = uuid.uuid4().hex[:8]
    org = Organization(
        id=uuid.uuid4(),
        code=f"REACT-{case_suffix.upper()}",
        normalized_code=f"react-{case_suffix}",
        display_name=f"Reactivation Case {case_suffix}",
        is_active=organization_is_active,
        entitlement_status=entitlement_status,
        created_at=now,
        updated_at=now,
        name_key=f"reactivation case {case_suffix}",
        fallback_icon_svg="<svg></svg>",
        is_protected=False,
    )
    session.add(org)

    org_admin = upsert_user(
        session,
        email="reactivation-org-admin@example.test",
        display_name="Reactivation Org Admin",
        roles=[],
        must_change_pin=False,
    )
    admin_membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org.id,
        user_id=org_admin.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(admin_membership)
    session.add(
        OrganizationRoleAssignment(
            id=uuid.uuid4(),
            membership_id=admin_membership.id,
            base_role="admin",
            is_active=True,
            assigned_at=now,
        )
    )

    target_user = upsert_user(
        session,
        email="reactivation-target@example.test",
        display_name="Reactivation Target",
        roles=[],
        must_change_pin=False,
    )
    target_user.is_active = target_is_active
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org.id,
        user_id=target_user.id,
        status="inactive",
        activated_at=now,
        deactivated_at=now,
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    session.add(membership)
    session.commit()
    return org, org_admin, target_user, membership


def _create_non_platform_admin_session_pg(session, user):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symgov = session.query(Organization).filter_by(normalized_code="symgov").one()
    symgov_membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=symgov.id,
        user_id=user.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(symgov_membership)
    session.add(
        OrganizationRoleAssignment(
            id=uuid.uuid4(),
            membership_id=symgov_membership.id,
            base_role="admin",
            is_active=True,
            assigned_at=now,
        )
    )
    session_id = uuid.uuid4()
    token_plain = "test-non-platform-token-" + str(session_id)
    session.add(
        UserSession(
            id=session_id,
            auth_user_id=user.id,
            token_hash=hash_session_token(token_plain),
            session_mode="organization",
            active_organization_id=symgov.id,
            purpose="application",
            created_at=now,
            expires_at=now + timedelta(days=1),
            recent_step_up_at=now,
        )
    )
    session.commit()
    return token_plain


@pytest.mark.parametrize(
    ("organization_is_active", "entitlement_status"),
    [(False, "active"), (True, "suspended")],
    ids=["inactive-organization", "suspended-entitlement"],
)
def test_reactivation_rejects_ineligible_organization_pg(
    pg_client,
    organization_database,
    organization_is_active,
    entitlement_status,
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        _org, _org_admin, _target_user, membership = _seed_reactivation_case(
            session,
            organization_is_active=organization_is_active,
            entitlement_status=entitlement_status,
        )
        token, _platform_admin = _create_platform_admin_session_pg(
            session,
            f"organization-eligibility-{organization_is_active}-{entitlement_status}"
            "@example.test",
        )
        membership_id = membership.id

    response = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": "Approved organization eligibility check"},
        cookies={"symgov_session": token},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Membership organization must be active and entitled."
    with Session() as session:
        assert session.get(OrganizationMembership, membership_id).status == "inactive"


@pytest.mark.parametrize("recent_step_up_at", [None, "stale"], ids=["missing", "stale"])
def test_reactivation_requires_recent_step_up_pg(
    pg_client, organization_database, recent_step_up_at
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        _org, _org_admin, _target_user, membership = _seed_reactivation_case(session)
        token, platform_admin = _create_platform_admin_session_pg(
            session, f"{recent_step_up_at or 'missing'}-step-up-platform-admin@example.test"
        )
        user_session = session.query(UserSession).filter_by(auth_user_id=platform_admin.id).one()
        user_session.recent_step_up_at = (
            None
            if recent_step_up_at is None
            else datetime.now(timezone.utc) - timedelta(seconds=601)
        )
        session.commit()
        membership_id = membership.id

    response = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": "Approved recent step-up enforcement"},
        cookies={"symgov_session": token},
    )

    assert response.status_code == 403
    with Session() as session:
        assert session.get(OrganizationMembership, membership_id).status == "inactive"


def test_diagnostics_do_not_expose_cross_tenant_members_to_organization_admin_pg(
    pg_client, organization_database
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        org, org_admin, target_user, membership = _seed_reactivation_case(session)
        _create_platform_admin_session_pg(
            session, "cross-tenant-bootstrap-platform-admin@example.test"
        )
        token = _create_non_platform_admin_session_pg(session, org_admin)
        org_id = org.id
        membership_id = membership.id
        target_email = target_user.email

    diagnostics_response = pg_client.get(
        f"/api/v1/platform/organizations/{org_id}/members",
        cookies={"symgov_session": token},
    )
    reactivation_response = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": "Unauthorized cross-tenant reactivation attempt"},
        cookies={"symgov_session": token},
    )

    assert diagnostics_response.status_code == 403
    assert reactivation_response.status_code == 403
    assert target_email not in diagnostics_response.text
    assert target_email not in reactivation_response.text
    with Session() as session:
        assert session.get(OrganizationMembership, membership_id).status == "inactive"


@pytest.mark.parametrize("reason", ["short", "x" * 1001], ids=["too-short", "too-long"])
def test_reactivation_requires_bounded_nonempty_reason_pg(
    pg_client, organization_database, reason
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        _org, _org_admin, _target_user, membership = _seed_reactivation_case(session)
        token, _platform_admin = _create_platform_admin_session_pg(
            session, f"reason-{len(reason)}-platform-admin@example.test"
        )
        membership_id = membership.id

    response = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": reason},
        cookies={"symgov_session": token},
    )

    assert response.status_code == 422
    with Session() as session:
        assert session.get(OrganizationMembership, membership_id).status == "inactive"


def test_reactivation_rejects_inactive_target_user_pg(pg_client, organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        _org, _org_admin, _target_user, membership = _seed_reactivation_case(
            session, target_is_active=False
        )
        token, _platform_admin = _create_platform_admin_session_pg(
            session, "inactive-target-platform-admin@example.test"
        )
        membership_id = membership.id

    response = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate",
        json={"reason": "Approved inactive target validation"},
        cookies={"symgov_session": token},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Target user must be an active user."
    with Session() as session:
        assert session.get(OrganizationMembership, membership_id).status == "inactive"


def test_reactivation_is_idempotent_without_duplicate_role_or_audit_pg(
    pg_client, organization_database
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        _org, _org_admin, _target_user, membership = _seed_reactivation_case(session)
        token, _platform_admin = _create_platform_admin_session_pg(
            session, "idempotent-platform-admin@example.test"
        )
        membership_id = membership.id

    request = {
        "json": {"reason": "Approved idempotent reactivation request"},
        "cookies": {"symgov_session": token},
    }
    first = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate", **request
    )
    second = pg_client.post(
        f"/api/v1/platform/memberships/{membership_id}/reactivate", **request
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["membershipId"] == str(membership_id)
    with Session() as session:
        assert session.query(OrganizationRoleAssignment).filter_by(
            membership_id=membership_id, is_active=True
        ).count() == 1
        assert session.query(AuditEvent).filter_by(
            entity_type="organization_membership",
            entity_id=membership_id,
            action="membership.reactivated",
        ).count() == 1


def test_diagnostics_pagination_is_bounded_and_stable_pg(pg_client, organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        org, _org_admin, _target_user, first_membership = _seed_reactivation_case(session)
        second_user = upsert_user(
            session,
            email="second-diagnostic-target@example.test",
            display_name="Second Diagnostic Target",
            roles=[],
            must_change_pin=False,
        )
        second_membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=second_user.id,
            status="inactive",
            activated_at=first_membership.activated_at,
            deactivated_at=first_membership.deactivated_at,
            created_at=first_membership.created_at + timedelta(seconds=1),
            updated_at=first_membership.updated_at + timedelta(seconds=1),
        )
        session.add(second_membership)
        session.commit()
        token, _platform_admin = _create_platform_admin_session_pg(
            session, "pagination-platform-admin@example.test"
        )
        org_id = org.id

    first_page = pg_client.get(
        f"/api/v1/platform/organizations/{org_id}/members?page=1&pageSize=1",
        cookies={"symgov_session": token},
    )
    second_page = pg_client.get(
        f"/api/v1/platform/organizations/{org_id}/members?page=2&pageSize=1",
        cookies={"symgov_session": token},
    )
    over_limit = pg_client.get(
        f"/api/v1/platform/organizations/{org_id}/members?pageSize=201",
        cookies={"symgov_session": token},
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert second_page.json()["total"] == 3
    assert first_page.json()["items"][0]["membershipId"] != second_page.json()["items"][0]["membershipId"]
    assert over_limit.status_code == 422
