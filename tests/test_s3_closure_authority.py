
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
    get_settings,
    require_organization_session,
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

def test_s3_b6_bound_session_revalidates_live_settings():
    """S3-B6: Existing organization sessions must respect SYMGOV_ORGANIZATIONS_ENABLED and pilot allowlist."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    current_settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_pilot_codes=("acme",),
    )

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
        user_id = user.id

    app = create_app()

    @app.get("/test-session")
    def _probe_session(user=Depends(require_organization_session)):
        return {"ok": True}

    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_settings] = lambda: current_settings

    client = TestClient(app, headers={"origin": "http://testserver"})

    # 1. Login and get organization session
    login = client.post("/api/v1/auth/login", json={"email": "member@example.test", "pin": "1234"})
    assert login.status_code == 200
    assert login.json()["user"]["session"]["mode"] == "organization"

    # Verify session works
    response = client.get("/test-session")
    assert response.status_code == 200

    # 2. Disable organizations globally
    current_settings = SymgovAPISettings(
        organizations_enabled=False,
        organization_pilot_codes=("acme",),
    )

    response = client.get("/test-session")
    # This SHOULD fail now (S3-B6)
    assert response.status_code == 401, "Session should be rejected when organizations are disabled"

    # 3. Re-enable but remove from pilot
    current_settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_pilot_codes=("other",),
    )

    response = client.get("/test-session")
    # This SHOULD fail now (S3-B6)
    assert response.status_code == 401, "Session should be rejected when org is not in pilot"
