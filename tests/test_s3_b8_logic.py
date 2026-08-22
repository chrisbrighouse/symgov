
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, CheckConstraint
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import upsert_user
from symgov_backend.organization_service import add_organization_member
from symgov_backend.models import (
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    User,
    UserRole,
    UserSession,
    UserSubscription,
    PlatformRoleAssignment,
    OrganizationMemberCapability,
    AuthOrganizationSelectionChallenge,
    AuthLoginThrottleBucket,
    AuthLoginAttemptEvent,
    AuthThrottleRecoveryEvent,
    SubscriptionEvent,
)

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

def test_s3_b8_revalidate_admin_authority_under_lock():
    """S3-B8: Organization Admin authority must be rechecked under the mutation lock."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with Session() as session:
        # Create actor who WILL be demoted
        actor = upsert_user(
            session,
            email="actor@example.test",
            display_name="Actor",
            roles=[],
        )
        # Create target user
        target = upsert_user(
            session,
            email="target@example.test",
            display_name="Target",
            roles=[],
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        org = Organization(
            id=uuid.uuid4(),
            code="ACME",
            normalized_code="acme",
            display_name="ACME Org",
            name_key="acme-org",
            entitlement_status="active",
            is_active=True,
            fallback_icon_svg="<svg/>",
            created_at=now,
            updated_at=now,
        )
        # Actor is admin
        actor_membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=actor.id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([org, actor_membership])
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=actor_membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            )
        )
        session.commit()

        org_id = org.id
        actor_id = actor.id
        target_id = target.id

    # 1. Simulate a concurrent demotion
    with Session() as session:
        # Resolve actor membership
        membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == actor_id,
            OrganizationMembership.organization_id == org_id
        ).one()
        # Find active role
        role = session.query(OrganizationRoleAssignment).filter(
            OrganizationRoleAssignment.membership_id == membership.id,
            OrganizationRoleAssignment.is_active.is_(True)
        ).one()
        # Demote!
        role.is_active = False
        role.revoked_at = datetime.now(timezone.utc)
        session.commit()

    # 2. Now call the service as the demoted actor
    with Session() as session:
        import pytest
        with pytest.raises(ValueError, match="Actor must be an active administrator"):
            add_organization_member(
                session,
                organization_id=org_id,
                user_id=target_id,
                base_role="user",
                actor_user_id=actor_id,
            )
