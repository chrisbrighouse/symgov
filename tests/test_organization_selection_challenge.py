from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import hash_session_token, upsert_user
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
from symgov_backend.subscriptions import upgrade_to_plus


def _create_tables(engine) -> None:
    for table in (
        User.__table__,
        UserRole.__table__,
        Organization.__table__,
        OrganizationMembership.__table__,
        OrganizationMemberCapability.__table__,
        OrganizationRoleAssignment.__table__,
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
                constraint
                for constraint in original
                if not (
                    isinstance(constraint, CheckConstraint)
                    and ("~" in str(constraint.sqltext) or "interval" in str(constraint.sqltext))
                )
            }
            table.create(engine)
        finally:
            table.constraints = original


def _build_client(organization_count: int = 8):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    codes = tuple(f"org-{index}" for index in range(organization_count))
    organization_ids = []
    with Session() as session:
        user = upsert_user(
            session,
            email="selection-member@example.test",
            display_name="Selection Member",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        upgrade_to_plus(session, user, months=1)
        for code in codes:
            organization = Organization(
                id=uuid.uuid4(),
                code=code.upper(),
                normalized_code=code,
                display_name=f"{code.upper()} Organization",
                name_key=f"{code}-organization",
                entitlement_status="active",
                is_active=True,
                is_protected=False,
                fallback_icon_svg="<svg/>",
                created_at=now,
                updated_at=now,
            )
            membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=organization.id,
                user_id=user.id,
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
                    base_role="user",
                    is_active=True,
                    assigned_at=now,
                )
            )
            organization_ids.append(organization.id)
        session.commit()
        user_id = user.id

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_pilot_codes=codes,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"})
    return client, Session, user_id, tuple(organization_ids)


def _login(client: TestClient):
    return client.post(
        "/api/v1/auth/login",
        json={"email": "selection-member@example.test", "pin": "1234"},
    )


def test_later_page_retrieval_is_bounded_and_does_not_consume_or_create_session():
    client, Session, _, organization_ids = _build_client()
    challenge = _login(client).json()["selectionChallenge"]

    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "page": 2, "pageSize": 5},
    )

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "selectionChallenge": {
            "token": challenge["token"],
            "expiresAt": challenge["expiresAt"],
            "choices": [
                {
                    "organizationId": str(organization_ids[index]),
                    "code": f"ORG-{index}",
                    "displayName": f"ORG-{index} Organization",
                }
                for index in range(5, 8)
            ],
            "page": 2,
            "pageSize": 5,
            "total": 8,
            "hasMore": False,
        },
    }
    assert response.cookies.get("symgov_session") is None
    with Session() as session:
        stored = session.query(AuthOrganizationSelectionChallenge).one()
        assert stored.attempt_count == 0
        assert stored.consumed_at is None
        assert stored.revoked_at is None
        assert session.query(UserSession).count() == 0


def test_same_token_selection_creates_one_bound_application_session_and_consumes_challenge():
    client, Session, user_id, organization_ids = _build_client()
    login = _login(client)
    challenge = login.json()["selectionChallenge"]

    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "organizationId": str(organization_ids[6])},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["selectionChallenge"] is None
    assert body["user"]["id"] == str(user_id)
    assert body["user"]["session"] == {
        "purpose": "application",
        "mode": "organization",
        "activeOrganizationId": str(organization_ids[6]),
    }
    assert body["user"]["organization"]["id"] == str(organization_ids[6])
    token = response.cookies.get("symgov_session")
    assert token
    with Session() as session:
        challenge_row = session.query(AuthOrganizationSelectionChallenge).one()
        application_session = session.query(UserSession).one()
        assert challenge_row.consumed_at is not None
        assert challenge_row.revoked_at is None
        assert application_session.auth_user_id == user_id
        assert application_session.token_hash == hash_session_token(token)
        assert application_session.session_mode == "organization"
        assert application_session.active_organization_id == organization_ids[6]


def test_select_organization_has_no_legacy_alias():
    client, _, _, _ = _build_client(organization_count=0)

    response = client.post(
        "/api/auth/select-organization",
        json={"token": "not-a-token", "organizationId": str(uuid.uuid4())},
    )

    assert response.status_code == 404


def test_challenge_expires_at_exactly_600_seconds_and_is_usable_at_599(monkeypatch):
    from datetime import timedelta
    from symgov_backend import routes

    client, Session, _, organization_ids = _build_client()
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(routes.auth, "utc_now", lambda: fixed_now)
    challenge = _login(client).json()["selectionChallenge"]

    def _aware(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    with Session() as session:
        stored = session.query(AuthOrganizationSelectionChallenge).one()
        assert _aware(stored.expires_at) - fixed_now == timedelta(minutes=10)

    monkeypatch.setattr(routes.auth, "utc_now", lambda: fixed_now + timedelta(seconds=599))
    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "page": 1, "pageSize": 5},
    )
    assert response.status_code == 200
    assert response.json()["user"] is None

    monkeypatch.setattr(routes.auth, "utc_now", lambda: fixed_now + timedelta(seconds=600))
    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "page": 1, "pageSize": 5},
    )
    assert response.status_code == 401
    body = response.json()
    assert "invalid" in body["detail"].lower() or "unavailable" in body["detail"].lower()


def test_invalid_selections_one_through_four_increment_and_keep_usable_then_fifth_exhausts():
    client, Session, _, organization_ids = _build_client()
    challenge = _login(client).json()["selectionChallenge"]
    forged_id = str(uuid.uuid4())

    for attempt in range(1, 5):
        response = client.post(
            "/api/v1/auth/select-organization",
            json={"token": challenge["token"], "organizationId": forged_id},
        )
        assert response.status_code == 401
        with Session() as session:
            stored = session.query(AuthOrganizationSelectionChallenge).one()
            assert stored.attempt_count == attempt
            assert stored.consumed_at is None
            assert stored.revoked_at is None
            assert session.query(UserSession).count() == 0

    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "organizationId": forged_id},
    )
    assert response.status_code == 401
    with Session() as session:
        stored = session.query(AuthOrganizationSelectionChallenge).one()
        assert stored.attempt_count == 5
        assert stored.revoked_at is not None
        assert stored.consumed_at is None
        assert session.query(UserSession).count() == 0

    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "organizationId": forged_id},
    )
    assert response.status_code == 401
    with Session() as session:
        stored = session.query(AuthOrganizationSelectionChallenge).one()
        assert stored.attempt_count == 5
        assert stored.revoked_at is not None
        assert session.query(UserSession).count() == 0


def test_supersession_replay_logout_revocation_and_eligibility_loss():
    from symgov_backend.auth import create_user_session, hash_session_token

    client, Session, user_id, organization_ids = _build_client()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # 1) Supersession: a second login revokes the older challenge
    login_1 = _login(client)
    challenge_a = login_1.json()["selectionChallenge"]
    assert challenge_a is not None
    login_2 = _login(client)
    _ = login_2.json()["selectionChallenge"]
    with Session() as session:
        old = session.query(AuthOrganizationSelectionChallenge).filter(
            AuthOrganizationSelectionChallenge.token_hash == hashlib.sha256(challenge_a["token"].encode()).hexdigest()
        ).one_or_none()
        assert old is not None
        assert old.revoked_at is not None
    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge_a["token"], "organizationId": str(organization_ids[0])},
    )
    assert response.status_code == 401

    # 2) Replay: a consumed challenge token cannot be reused
    login_3 = _login(client)
    challenge_b = login_3.json()["selectionChallenge"]
    consume = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge_b["token"], "organizationId": str(organization_ids[0])},
    )
    assert consume.status_code == 200
    first_session_token = consume.cookies.get("symgov_session")
    assert first_session_token
    replay = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge_b["token"], "organizationId": str(organization_ids[0])},
    )
    assert replay.status_code == 401
    with Session() as session:
        sessions = session.query(UserSession).all()
        assert len(sessions) == 1
        assert sessions[0].token_hash == hash_session_token(first_session_token)

    # 3) Logout revocation: revoke a session with an outstanding challenge for the same user
    login_4 = _login(client)
    challenge_c = login_4.json()["selectionChallenge"]
    with Session() as session:
        user = session.query(User).filter(User.id == user_id).one()
        session_token = create_user_session(session, user=user)
        session.commit()
    client.cookies.set("symgov_session", session_token)
    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    with Session() as session:
        challenge_c_row = session.query(AuthOrganizationSelectionChallenge).filter(
            AuthOrganizationSelectionChallenge.token_hash == hashlib.sha256(challenge_c["token"].encode()).hexdigest()
        ).one_or_none()
        assert challenge_c_row is not None
        assert challenge_c_row.revoked_at is not None
    client.cookies.clear()
    client.cookies.set("symgov_session", session_token)
    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge_c["token"], "organizationId": str(organization_ids[0])},
    )
    assert response.status_code == 401

    # 4) Eligibility loss: deactivate membership after challenge issuance
    login_5 = _login(client)
    challenge_d = login_5.json()["selectionChallenge"]
    with Session() as session:
        membership = session.query(OrganizationMembership).filter(
            OrganizationMembership.organization_id == organization_ids[0],
            OrganizationMembership.user_id == user_id,
        ).one()
        membership.status = "suspended"
        membership.deactivated_at = now
        membership.updated_at = now
        session.commit()
    response = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge_d["token"], "organizationId": str(organization_ids[0])},
    )
    assert response.status_code == 401
    with Session() as session:
        challenge_d_row = session.query(AuthOrganizationSelectionChallenge).filter(
            AuthOrganizationSelectionChallenge.token_hash == hashlib.sha256(challenge_d["token"].encode()).hexdigest()
        ).one_or_none()
        assert challenge_d_row is not None
        assert challenge_d_row.revoked_at is not None
        assert challenge_d_row.consumed_at is None
        assert challenge_d_row.attempt_count == 0
