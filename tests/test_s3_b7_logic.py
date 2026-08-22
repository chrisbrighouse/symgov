
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine, CheckConstraint
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import upsert_user
from symgov_backend.organization_service import (
    add_organization_member,
    reconcile_symgov_organization_bootstrap
)
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


@pytest.fixture(autouse=True)
def _stub_postgresql_audit_sink_for_sqlite():
    with patch("symgov_backend.organization_service._emit_audit"):
        yield

def test_s3_b7_ordinary_admin_cannot_mutate_symgov():
    """S3-B7: Ordinary Org Admin paths must fail closed for protected Symgov organization."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with Session() as session:
        # Bootstrap Symgov
        from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL
        upsert_user(session, email=PROTECTED_OWNER_EMAIL, display_name="Owner", roles=["admin"])
        reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()

        # Find Symgov org
        symgov_org = session.query(Organization).filter(Organization.normalized_code == "symgov").one()

        # Create a non-platform-admin Org Admin for Symgov (should be impossible in prod but let's test the service)
        # Wait, if we use the service to add them, it should already fail!

        # Let's create an actor manually who is an admin of Symgov but NOT a platform admin
        actor = upsert_user(session, email="actor@example.test", display_name="Actor", roles=[])
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=symgov_org.id,
            user_id=actor.id,
            status="active",
            activated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(membership)
        session.flush()
        session.add(OrganizationRoleAssignment(
            id=uuid.uuid4(),
            membership_id=membership.id,
            base_role="admin",
            is_active=True,
            assigned_at=datetime.now(timezone.utc),
        ))
        session.commit()

        actor_id = actor.id
        org_id = symgov_org.id

        # Target user
        target = upsert_user(
            session,
            email="target@example.test",
            display_name="Target",
            roles=[],
            must_change_pin=False,
        )
        target_id = target.id
        session.commit()

    # Try to add member via ordinary service
    with Session() as session:
        import pytest
        with pytest.raises(ValueError, match="protected Symgov organization cannot be managed through ordinary services"):
            add_organization_member(
                session,
                organization_id=org_id,
                user_id=target_id,
                base_role="user",
                actor_user_id=actor_id,
            )
