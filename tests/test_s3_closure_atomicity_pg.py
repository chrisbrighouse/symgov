"""Physical rollback proofs for Stage 3 domain mutations and audit events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from symgov_backend.auth import upsert_user
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
    add_organization_member,
    add_protected_organization_member,
    assign_platform_admin,
    grant_member_capability,
    reactivate_membership,
    reconcile_symgov_organization_bootstrap,
    replace_membership_base_role,
)
from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL
from test_organization_postgresql_migration import organization_database


@pytest.fixture(autouse=True)
def _stub_record_governance_usage_event():
    """This module's fixture (`organization_database`, imported from
    `test_organization_postgresql_migration.py`) is deliberately pinned to
    an old migration snapshot shared across many tests there; the
    `product_usage_events` table Stage 9 WP9.2 added does not exist at that
    snapshot. This file only tests audit/domain rollback atomicity, not
    product-usage-event behavior, so the call is stubbed here -- mirroring
    `test_organization_admin_api.py`'s own `_stub_emit_audit` pattern for
    the same reason."""
    with patch("symgov_backend.organization_service.record_governance_usage_event"):
        yield


@pytest.fixture(scope="module", autouse=True)
def atomic_failure_triggers(organization_database):
    """Install transaction-local fault injection in the disposable PostgreSQL DB."""
    domain_tables = (
        "organizations",
        "organization_memberships",
        "organization_role_assignments",
        "organization_member_capabilities",
        "platform_role_assignments",
    )
    with organization_database.begin() as connection:
        connection.execute(
            text(
                """
                CREATE FUNCTION symgov_test_reject_audit_insert()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.action = current_setting(
                        'symgov.test_reject_audit_action', true
                    ) THEN
                        RAISE EXCEPTION 'synthetic audit insert failure for %', NEW.action;
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER symgov_test_reject_audit_insert
                BEFORE INSERT ON audit_events
                FOR EACH ROW EXECUTE FUNCTION symgov_test_reject_audit_insert()
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE FUNCTION symgov_test_reject_domain_commit()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_TABLE_NAME = current_setting(
                        'symgov.test_reject_domain_table', true
                    ) THEN
                        RAISE EXCEPTION 'synthetic deferred domain failure for %', TG_TABLE_NAME;
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        for table_name in domain_tables:
            connection.execute(
                text(
                    f"""
                    CREATE CONSTRAINT TRIGGER symgov_test_reject_{table_name}_commit
                    AFTER INSERT OR UPDATE ON {table_name}
                    DEFERRABLE INITIALLY DEFERRED
                    FOR EACH ROW EXECUTE FUNCTION symgov_test_reject_domain_commit()
                    """
                )
            )
    yield


def _arm_failure(
    session: Session,
    failure_side: str,
    *,
    audit_action: str,
    domain_table: str,
) -> None:
    setting, value = (
        ("symgov.test_reject_audit_action", audit_action)
        if failure_side == "audit"
        else ("symgov.test_reject_domain_table", domain_table)
    )
    session.execute(
        text("SELECT set_config(:setting, :value, true)"),
        {"setting": setting, "value": value},
    )


def _expect_atomic_failure(session: Session, operation: Callable[[], object]) -> None:
    with pytest.raises(DBAPIError, match="synthetic (audit insert|deferred domain) failure"):
        operation()
        session.commit()
    session.rollback()


def _seed_ordinary_organization(session: Session, suffix: str):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    organization = Organization(
        id=uuid.uuid4(),
        code=f"ATOMIC-{suffix.upper()}",
        normalized_code=f"atomic-{suffix}",
        display_name=f"Atomic {suffix}",
        name_key=f"atomic {suffix}",
        entitlement_status="active",
        is_active=True,
        is_protected=False,
        fallback_icon_svg="<svg></svg>",
        created_at=now,
        updated_at=now,
    )
    admin = upsert_user(
        session,
        email=f"atomic-admin-{suffix}@example.test",
        display_name=f"Atomic administrator {suffix}",
        roles=[],
        must_change_pin=False,
    )
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization.id,
        user_id=admin.id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            organization,
            membership,
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            ),
        ]
    )
    session.commit()
    return organization, admin


def _ensure_platform_context(session: Session):
    symgov = session.query(Organization).filter_by(normalized_code="symgov").one_or_none()
    if symgov is None:
        upsert_user(
            session,
            email=PROTECTED_OWNER_EMAIL,
            display_name="Atomic platform owner",
            roles=["admin"],
            must_change_pin=False,
        )
        session.commit()
        reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()
    symgov = session.query(Organization).filter_by(normalized_code="symgov").one()
    protected_owner = session.query(User).filter_by(email=PROTECTED_OWNER_EMAIL).one()
    return symgov, protected_owner


@pytest.mark.parametrize("failure_side", ["audit", "domain"])
def test_bootstrap_domain_and_audit_roll_back_together_pg(
    organization_database, failure_side
):
    SessionLocal = sessionmaker(bind=organization_database)
    with SessionLocal() as session:
        assert session.query(Organization).filter_by(normalized_code="symgov").count() == 0
        upsert_user(
            session,
            email=PROTECTED_OWNER_EMAIL,
            display_name="Atomic bootstrap owner",
            roles=["admin"],
            must_change_pin=False,
        )
        session.commit()
        _arm_failure(
            session,
            failure_side,
            audit_action="organization.created",
            domain_table="organizations",
        )
        _expect_atomic_failure(
            session, lambda: reconcile_symgov_organization_bootstrap(session, apply=True)
        )
        assert session.query(Organization).filter_by(normalized_code="symgov").count() == 0
        assert session.query(AuditEvent).filter_by(action="organization.created").count() == 0


@pytest.mark.parametrize("failure_side", ["audit", "domain"])
def test_membership_domain_and_audit_roll_back_together_pg(
    organization_database, failure_side
):
    SessionLocal = sessionmaker(bind=organization_database)
    with SessionLocal() as session:
        suffix = uuid.uuid4().hex[:8]
        organization, admin = _seed_ordinary_organization(session, suffix)
        target = upsert_user(
            session,
            email=f"atomic-member-{suffix}@example.test",
            display_name=f"Atomic member {suffix}",
            roles=[],
            must_change_pin=False,
        )
        session.commit()
        _arm_failure(
            session,
            failure_side,
            audit_action="membership.added",
            domain_table="organization_memberships",
        )
        _expect_atomic_failure(
            session,
            lambda: add_organization_member(
                session,
                organization.id,
                user_id=target.id,
                base_role="user",
                actor_user_id=admin.id,
            ),
        )
        assert session.query(OrganizationMembership).filter_by(
            organization_id=organization.id, user_id=target.id
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            action="membership.added", actor_id=admin.id
        ).count() == 0


@pytest.mark.parametrize(
    ("failure_side", "mutation_kind", "audit_action", "domain_table"),
    [
        (side, "role", "membership.base_role_replaced", "organization_role_assignments")
        for side in ("audit", "domain")
    ]
    + [
        (side, "capability", "capability.granted", "organization_member_capabilities")
        for side in ("audit", "domain")
    ],
)
def test_role_and_capability_domain_and_audit_roll_back_together_pg(
    organization_database,
    failure_side,
    mutation_kind,
    audit_action,
    domain_table,
):
    SessionLocal = sessionmaker(bind=organization_database)
    with SessionLocal() as session:
        suffix = uuid.uuid4().hex[:8]
        organization, admin = _seed_ordinary_organization(session, suffix)
        target = upsert_user(
            session,
            email=f"atomic-role-{suffix}@example.test",
            display_name=f"Atomic role target {suffix}",
            roles=[],
            must_change_pin=False,
        )
        membership = add_organization_member(
            session,
            organization.id,
            user_id=target.id,
            base_role="user",
            actor_user_id=admin.id,
        )
        session.commit()
        _arm_failure(
            session,
            failure_side,
            audit_action=audit_action,
            domain_table=domain_table,
        )
        if mutation_kind == "role":
            operation = lambda: replace_membership_base_role(
                session,
                membership_id=membership.id,
                new_base_role="admin",
                actor_user_id=admin.id,
            )
        else:
            operation = lambda: grant_member_capability(
                session,
                membership.id,
                capability="contributor",
                actor_user_id=admin.id,
                organization_id=organization.id,
            )
        _expect_atomic_failure(session, operation)
        active_role = session.query(OrganizationRoleAssignment).filter_by(
            membership_id=membership.id, is_active=True
        ).one()
        assert active_role.base_role == "user"
        assert session.query(OrganizationMemberCapability).filter_by(
            membership_id=membership.id, is_active=True
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            action=audit_action, actor_id=admin.id
        ).count() == 0


@pytest.mark.parametrize("failure_side", ["audit", "domain"])
def test_platform_assignment_domain_and_audit_roll_back_together_pg(
    organization_database, failure_side
):
    SessionLocal = sessionmaker(bind=organization_database)
    with SessionLocal() as session:
        symgov, owner = _ensure_platform_context(session)
        suffix = uuid.uuid4().hex[:8]
        candidate = upsert_user(
            session,
            email=f"atomic-platform-{suffix}@example.test",
            display_name=f"Atomic platform candidate {suffix}",
            roles=[],
            must_change_pin=False,
        )
        add_protected_organization_member(
            session,
            symgov.id,
            user_id=candidate.id,
            base_role="admin",
            actor_user_id=owner.id,
            reason="Approved atomic platform assignment setup",
        )
        session.commit()
        _arm_failure(
            session,
            failure_side,
            audit_action="platform_admin.assigned",
            domain_table="platform_role_assignments",
        )
        _expect_atomic_failure(
            session,
            lambda: assign_platform_admin(
                session, user_id=candidate.id, actor_user_id=owner.id
            ),
        )
        assert session.query(PlatformRoleAssignment).filter_by(
            user_id=candidate.id, role="platform_admin", is_active=True
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            action="platform_admin.assigned", actor_id=owner.id
        ).count() == 0


@pytest.mark.parametrize("failure_side", ["audit", "domain"])
def test_inactive_membership_reactivation_domain_and_audit_roll_back_together_pg(
    organization_database, failure_side
):
    SessionLocal = sessionmaker(bind=organization_database)
    with SessionLocal() as session:
        _symgov, owner = _ensure_platform_context(session)
        suffix = uuid.uuid4().hex[:8]
        organization, _admin = _seed_ordinary_organization(session, suffix)
        target = upsert_user(
            session,
            email=f"atomic-reactivation-{suffix}@example.test",
            display_name=f"Atomic reactivation target {suffix}",
            roles=[],
            must_change_pin=False,
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=target.id,
            status="inactive",
            activated_at=now,
            deactivated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        session.commit()
        _arm_failure(
            session,
            failure_side,
            audit_action="membership.reactivated",
            domain_table="organization_memberships",
        )
        _expect_atomic_failure(
            session,
            lambda: reactivate_membership(
                session,
                membership_id=membership.id,
                actor_user_id=owner.id,
                reason="Approved atomic reactivation proof",
            ),
        )
        session.refresh(membership)
        assert membership.status == "inactive"
        assert session.query(OrganizationRoleAssignment).filter_by(
            membership_id=membership.id, is_active=True
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            entity_type="organization_membership",
            entity_id=membership.id,
            action="membership.reactivated",
        ).count() == 0
