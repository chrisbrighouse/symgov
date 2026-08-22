
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, CheckConstraint
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import upsert_user, current_user_from_token, create_user_session
from symgov_backend.organization_authorization import resolve_bound_organization_context
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
from symgov_backend.settings import SymgovAPISettings

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

def test_s3_b6_logic_revalidates_live_settings():
    """Logic test for S3-B6: resolve_bound_organization_context must respect live settings."""
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
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=user.id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([org, membership])
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="user",
                is_active=True,
                assigned_at=now,
            )
        )
        session.commit()

        # Create a bound session
        token = create_user_session(
            session,
            user=user,
            purpose="application",
            session_mode="organization",
            active_organization_id=org.id,
        )
        session.commit()

        # 1. Test with organizations enabled and in pilot
        settings = SymgovAPISettings(
            organizations_enabled=True,
            organization_pilot_codes=("acme",),
        )
        context = resolve_bound_organization_context(session, user, org.id, settings)
        assert context is not None
        assert context.code == "ACME"

        # 2. Test with organizations disabled
        settings_disabled = SymgovAPISettings(
            organizations_enabled=False,
            organization_pilot_codes=("acme",),
        )
        context = resolve_bound_organization_context(session, user, org.id, settings_disabled)
        assert context is None, "Should be None when organizations are disabled"

        # 3. Test with organization removed from pilot
        settings_no_pilot = SymgovAPISettings(
            organizations_enabled=True,
            organization_pilot_codes=("other",),
        )
        context = resolve_bound_organization_context(session, user, org.id, settings_no_pilot)
        assert context is None, "Should be None when org is not in pilot"
