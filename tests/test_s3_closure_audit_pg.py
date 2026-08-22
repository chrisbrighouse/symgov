"""Physical transaction tests for Stage 3 mutations and AuditEvent integrity."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from symgov_backend.models import (
    AuditEvent,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    User,
)
from symgov_backend.organization_service import (
    add_protected_organization_member,
    add_organization_member,
    assign_platform_admin,
    create_organization_with_initial_admin,
    deactivate_membership,
    grant_member_capability,
    reactivate_organization,
    reconcile_symgov_organization_bootstrap,
    replace_membership_base_role,
    revoke_member_capability,
    revoke_platform_admin,
    suspend_organization,
    update_organization,
)
from symgov_backend.auth import upsert_user

# Reuse pg database fixture
from test_organization_postgresql_migration import organization_database

def _seed_minimal_context_pg(session, suffix: str):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    org_id = uuid.uuid4()
    code = f"AUDIT-{suffix}"
    org = Organization(
        id=org_id,
        code=code,
        normalized_code=code.lower(),
        display_name=f"Audit Org {suffix}",
        is_active=True,
        entitlement_status="active",
        created_at=now,
        updated_at=now,
        name_key=code.lower(),
        fallback_icon_svg="<svg></svg>",
        is_protected=False,
    )
    session.add(org)

    admin = upsert_user(
        session,
        email=f"admin-{suffix}@example.test",
        display_name=f"Admin {suffix}",
        roles=[],
        must_change_pin=False,
    )

    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org.id,
        user_id=admin.id,
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
    session.commit()
    return org, admin

def test_mutation_and_audit_commit_pg(organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        suffix = uuid.uuid4().hex[:8].upper()
        org, admin = _seed_minimal_context_pg(session, suffix)
        target_user = upsert_user(
            session,
            email=f"target-{suffix}@example.test",
            display_name=f"Target {suffix}",
            roles=[],
            must_change_pin=False,
        )
        session.commit()

        # Perform mutation
        add_organization_member(
            session,
            org.id,
            user_id=target_user.id,
            base_role="user",
            actor_user_id=admin.id,
        )
        session.commit()

        # Verify membership exists
        m = session.query(OrganizationMembership).filter_by(
            organization_id=org.id, user_id=target_user.id
        ).one()
        assert m.status == "active"

        # Verify audit event exists
        audit = session.query(AuditEvent).filter_by(
            entity_type="organization_membership",
            entity_id=m.id,
            action="membership.added"
        ).one()
        assert audit.actor_id == admin.id
        assert audit.payload_json["user_id"] == str(target_user.id)
        assert audit.payload_json["organization_id"] == str(org.id)
        assert audit.payload_json["effective_authority"] == "organization_admin"
        assert audit.payload_json["before"] == {"status": None}
        assert audit.payload_json["after"] == {"status": "active"}
        assert audit.payload_json["source"] == "organization_service"

        role = session.query(OrganizationRoleAssignment).filter_by(
            membership_id=m.id, is_active=True
        ).one()
        role_audit = session.query(AuditEvent).filter_by(
            entity_type="organization_role_assignment",
            entity_id=role.id,
            action="membership.base_role_assigned",
        ).one()
        assert role_audit.actor_id == admin.id
        assert role_audit.payload_json == {
            "organization_id": str(org.id),
            "membership_id": str(m.id),
            "effective_authority": "organization_admin",
            "before": {"base_role": None, "is_active": False},
            "after": {"base_role": "user", "is_active": True},
            "source": "organization_service",
        }


def test_membership_deactivation_audits_membership_and_role_in_one_transaction_pg(
    organization_database,
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        suffix = uuid.uuid4().hex[:8].upper()
        org, admin = _seed_minimal_context_pg(session, suffix)
        target_user = upsert_user(
            session,
            email=f"deactivate-{suffix}@example.test",
            display_name=f"Deactivate {suffix}",
            roles=[],
            must_change_pin=False,
        )
        session.commit()
        membership = add_organization_member(
            session,
            org.id,
            user_id=target_user.id,
            base_role="user",
            actor_user_id=admin.id,
        )
        session.commit()
        role = session.query(OrganizationRoleAssignment).filter_by(
            membership_id=membership.id, is_active=True
        ).one()

        deactivate_membership(
            session,
            membership_id=membership.id,
            actor_user_id=admin.id,
            reason="member_removed_by_organization_admin",
        )
        session.commit()

        membership_audit = session.query(AuditEvent).filter_by(
            entity_type="organization_membership",
            entity_id=membership.id,
            action="membership.deactivated",
        ).one()
        assert membership_audit.payload_json["organization_id"] == str(org.id)
        assert membership_audit.payload_json["effective_authority"] == "organization_admin"
        assert membership_audit.payload_json["before"] == {"status": "active"}
        assert membership_audit.payload_json["after"] == {"status": "inactive"}
        assert membership_audit.payload_json["reason"] == "member_removed_by_organization_admin"

        role_audit = session.query(AuditEvent).filter_by(
            entity_type="organization_role_assignment",
            entity_id=role.id,
            action="membership.base_role_revoked",
        ).one()
        assert role_audit.payload_json["organization_id"] == str(org.id)
        assert role_audit.payload_json["membership_id"] == str(membership.id)
        assert role_audit.payload_json["before"] == {
            "base_role": "user",
            "is_active": True,
        }
        assert role_audit.payload_json["after"] == {
            "base_role": "user",
            "is_active": False,
        }
        assert role_audit.payload_json["reason"] == "member_removed_by_organization_admin"

def test_mutation_and_audit_rollback_pg(organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        suffix = uuid.uuid4().hex[:8].upper()
        org, admin = _seed_minimal_context_pg(session, suffix)
        target_user = upsert_user(
            session,
            email=f"target-{suffix}@example.test",
            display_name=f"Target {suffix}",
            roles=[],
            must_change_pin=False,
        )
        session.commit()

        # Count audit events before
        before_count = session.query(func.count(AuditEvent.id)).scalar()

        # Perform mutation but rollback
        add_organization_member(
            session,
            org.id,
            user_id=target_user.id,
            base_role="user",
            actor_user_id=admin.id,
        )
        session.rollback()

        # Verify membership does NOT exist
        m = session.query(OrganizationMembership).filter_by(
            organization_id=org.id, user_id=target_user.id
        ).first()
        assert m is None

        # Verify audit event count is unchanged
        after_count = session.query(func.count(AuditEvent.id)).scalar()
        assert after_count == before_count


def test_stage3_mutation_families_persist_complete_bounded_audit_context_pg(
    organization_database,
):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL

        owner = upsert_user(
            session,
            email=PROTECTED_OWNER_EMAIL,
            display_name="Stage 3 audit owner",
            roles=["admin"],
            must_change_pin=False,
        )
        reconcile_symgov_organization_bootstrap(session, apply=True)
        target = upsert_user(
            session,
            email=f"audit-family-{uuid.uuid4().hex[:8]}@example.test",
            display_name="Stage 3 audit target",
            roles=[],
            must_change_pin=False,
        )
        session.commit()

        suffix = uuid.uuid4().hex[:8].upper()
        created = create_organization_with_initial_admin(
            session,
            code=f"AUD-{suffix}",
            display_name=f"Audit family {suffix}",
            initial_admin_user_id=owner.id,
            actor_user_id=owner.id,
        )
        membership = add_organization_member(
            session,
            created.organization.id,
            user_id=target.id,
            base_role="user",
            actor_user_id=owner.id,
        )
        replace_membership_base_role(
            session,
            membership_id=membership.id,
            new_base_role="admin",
            actor_user_id=owner.id,
        )
        grant_member_capability(
            session,
            membership.id,
            capability="contributor",
            actor_user_id=owner.id,
            organization_id=created.organization.id,
        )
        revoke_member_capability(
            session,
            membership.id,
            capability="contributor",
            actor_user_id=owner.id,
            organization_id=created.organization.id,
        )
        symgov = session.query(Organization).filter_by(normalized_code="symgov").one()
        add_protected_organization_member(
            session,
            symgov.id,
            user_id=target.id,
            base_role="admin",
            actor_user_id=owner.id,
            reason="Approved for platform audit coverage",
        )
        assign_platform_admin(session, user_id=target.id, actor_user_id=owner.id)
        revoke_platform_admin(session, user_id=target.id, actor_user_id=owner.id)
        update_organization(
            session,
            created.organization.id,
            actor_user_id=owner.id,
            display_name=f"Updated audit family {suffix}",
        )
        suspend_organization(session, created.organization.id, actor_user_id=owner.id)
        reactivate_organization(session, created.organization.id, actor_user_id=owner.id)
        session.commit()

        expected_actions = {
            "organization.created",
            "membership.added",
            "membership.base_role_assigned",
            "membership.base_role_replaced",
            "capability.granted",
            "capability.revoked",
            "platform_admin.assigned",
            "platform_admin.revoked",
            "organization.updated",
            "organization.suspended",
            "organization.reactivated",
        }
        events = session.query(AuditEvent).filter(AuditEvent.actor_id == owner.id).all()
        observed_actions = {event.action for event in events}
        assert expected_actions <= observed_actions
        for event in events:
            if event.action not in expected_actions:
                continue
            assert event.payload_json["effective_authority"] in {
                "organization_admin",
                "platform_admin",
            }
            assert event.payload_json["organization_id"]
            assert isinstance(event.payload_json["before"], dict)
            assert isinstance(event.payload_json["after"], dict)
            assert event.payload_json["source"] == "organization_service"

        assert session.query(OrganizationMemberCapability).filter_by(
            membership_id=membership.id,
            capability="contributor",
            is_active=False,
        ).one()
        assert session.query(PlatformRoleAssignment).filter_by(
            user_id=target.id,
            role="platform_admin",
            is_active=False,
        ).one()

def test_bootstrap_mutation_emits_audit_pg(organization_database):
    Session = sessionmaker(bind=organization_database)
    with Session() as session:
        # reconcile_symgov_organization_bootstrap requires a protected owner
        from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL
        upsert_user(session, email=PROTECTED_OWNER_EMAIL, display_name="Owner", roles=["admin"], must_change_pin=False)
        session.commit()

        # Run bootstrap with apply=True
        reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()

        # Verify audit event for organization creation
        org = session.query(Organization).filter_by(normalized_code="symgov").one()
        audit = session.query(AuditEvent).filter_by(
            entity_type="organization",
            entity_id=org.id,
            action="organization.created"
        ).one()
        assert audit.payload_json["code"] == "symgov"
        assert audit.actor_id is None
        assert audit.payload_json["organization_id"] == str(org.id)
        assert audit.payload_json["effective_authority"] == "system_bootstrap"
        assert audit.payload_json["before"] == {"exists": False}
        assert audit.payload_json["after"] == {
            "exists": True,
            "is_protected": True,
        }
        assert audit.payload_json["source"] == "management.bootstrap_symgov_organization"
        assert audit.payload_json["reason"] == "bootstrap_reconciliation"

        events = session.query(AuditEvent).filter(
            AuditEvent.actor_id.is_(None),
            AuditEvent.action.in_([
                "membership.added",
                "membership.base_role_assigned",
                "platform_admin.assigned",
            ])
        ).all()
        assert len(events) == 3
        assert all(event.payload_json["organization_id"] == str(org.id) for event in events)
        assert all(
            event.payload_json["effective_authority"] == "system_bootstrap"
            for event in events
        )
        assert all("before" in event.payload_json for event in events)
        assert all("after" in event.payload_json for event in events)
