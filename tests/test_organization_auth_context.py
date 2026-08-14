from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.cookies import SimpleCookie
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import current_user_from_token, hash_session_token, upsert_user
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
                if not (isinstance(item, CheckConstraint) and ("~" in str(item.sqltext) or "interval" in str(item.sqltext)))
            }
            table.create(engine)
        finally:
            table.constraints = original


def _build_client(*, enabled=True, pilots=(), must_change_pin=False):
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
            must_change_pin=must_change_pin,
        )
        upgrade_to_plus(session, user, months=1)
        session.add(
            UserRole(
                user_id=user.id,
                role="submitter",
                created_at=datetime.now(timezone.utc).replace(microsecond=0),
            )
        )
        session.commit()
        user_id = user.id

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=enabled,
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
    membership_status="active",
    organization_active=True,
    entitlement="active",
    base_role="user",
    role_active=True,
    capabilities=(),
    platform_admin=False,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = Organization(
            id=uuid.uuid4(),
            code=code if code == "symgov" else code.upper(),
            normalized_code=code.lower(),
            display_name=f"{code.upper()} Organization",
            name_key=f"{code}-organization",
            entitlement_status=entitlement,
            is_active=organization_active,
            is_protected=code == "symgov",
            fallback_icon_svg="<svg/>",
            created_at=now,
            updated_at=now,
        )
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=user_id,
            status=membership_status,
            activated_at=now if membership_status == "active" else None,
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
                is_active=role_active,
                assigned_at=now,
                revoked_at=None if role_active else now,
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
        return organization.id


def _login(client, path="/api/v1/auth/login"):
    return client.post(path, json={"email": "member@example.test", "pin": "1234"})


def test_zero_eligible_organizations_issues_personal_session_when_feature_is_off():
    client, Session, user_id = _build_client(enabled=False, pilots=("acme",))
    _add_membership(Session, user_id, "acme", capabilities=("contributor",))

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["user"]["session"] == {
        "purpose": "application",
        "mode": "personal",
        "activeOrganizationId": None,
    }
    assert response.json()["user"]["organization"] is None
    assert response.json()["user"]["capabilities"] == {
        "organizationsEnabled": False,
        "organizationAdminEnabled": False,
        "symbolSetsEnabled": False,
        "organizationSymbolsEnabled": False,
        "organizationAgentsEnabled": False,
    }
    assert response.cookies.get("symgov_session")


def test_commercial_display_code_is_returned_while_pilot_uses_normalized_code():
    client, Session, user_id = _build_client(pilots=(" ACME-01 ",))
    organization_id = _add_membership(Session, user_id, "acme-01")

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["user"]["organization"]["id"] == str(organization_id)
    assert response.json()["user"]["organization"]["code"] == "ACME-01"
    token = response.cookies.get("symgov_session")
    with Session() as session:
        principal = current_user_from_token(session, token)
        assert principal is not None
        assert principal.organization_code == "ACME-01"


def test_one_eligible_organization_issues_bound_session_and_effective_context():
    client, Session, user_id = _build_client(pilots=(" symgov ", "ignored"))
    organization_id = _add_membership(
        Session,
        user_id,
        "symgov",
        base_role="admin",
        capabilities=("contributor", "symbol_reviewer"),
        platform_admin=True,
    )

    response = _login(client)

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["roles"] == ["submitter"]
    assert user["session"] == {
        "purpose": "application",
        "mode": "organization",
        "activeOrganizationId": str(organization_id),
    }
    assert user["organization"] == {
        "id": str(organization_id),
        "code": "symgov",
        "displayName": "SYMGOV Organization",
        "baseRole": "admin",
        "capabilities": ["contributor", "symbol_reviewer"],
    }
    assert user["isPlatformAdmin"] is True
    assert user["capabilities"] == {
        "organizationsEnabled": True,
        "organizationAdminEnabled": True,
        "symbolSetsEnabled": True,
        "organizationSymbolsEnabled": True,
        "organizationAgentsEnabled": True,
    }
    token = response.cookies.get("symgov_session")
    with Session() as session:
        principal = current_user_from_token(session, token)
        assert principal is not None
        assert principal.active_organization_id == str(organization_id)
        assert principal.session_mode == "organization"


@pytest.mark.parametrize("path", ("/api/v1/auth/login", "/api/auth/login"))
def test_many_organizations_issue_hashed_bounded_challenge_without_application_cookie(path):
    client, Session, user_id = _build_client(pilots=tuple(f"org-{index}" for index in range(8)))
    for index in range(8):
        _add_membership(Session, user_id, f"org-{index}")

    response = _login(client, path)

    assert response.status_code == 200
    assert response.json()["user"] is None
    challenge = response.json()["selectionChallenge"]
    assert set(challenge) == {"token", "expiresAt", "choices", "page", "pageSize", "total", "hasMore"}
    assert len(challenge["choices"]) == 5
    assert challenge["page"] == 1
    assert challenge["pageSize"] == 5
    assert challenge["total"] == 8
    assert challenge["hasMore"] is True
    assert response.cookies.get("symgov_session") is None
    raw_token = challenge["token"]
    with Session() as session:
        stored = session.query(AuthOrganizationSelectionChallenge).one()
        assert stored.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert raw_token not in stored.eligible_organizations_json
        assert session.query(UserSession).count() == 0


def test_ineligible_memberships_are_omitted_without_private_directory_leakage():
    client, Session, user_id = _build_client(pilots=("good", "inactive", "suspended", "member-off", "role-off"))
    good_id = _add_membership(Session, user_id, "good")
    _add_membership(Session, user_id, "inactive", organization_active=False)
    _add_membership(Session, user_id, "suspended", entitlement="suspended")
    _add_membership(Session, user_id, "member-off", membership_status="inactive")
    _add_membership(Session, user_id, "role-off", role_active=False)
    _add_membership(Session, user_id, "private-non-pilot")

    response = _login(client)

    serialized = response.text
    assert response.json()["user"]["session"]["activeOrganizationId"] == str(good_id)
    for private_value in ("INACTIVE Organization", "SUSPENDED Organization", "PRIVATE-NON-PILOT Organization"):
        assert private_value not in serialized


def test_platform_admin_requires_every_independent_active_condition():
    client, Session, user_id = _build_client(pilots=("acme",))
    _add_membership(Session, user_id, "acme", base_role="admin", platform_admin=True)

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["user"]["isPlatformAdmin"] is False


def test_must_change_pin_does_not_query_organizations_or_issue_challenge(monkeypatch):
    client, Session, _ = _build_client(enabled=True, pilots=("symgov",), must_change_pin=True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("organization eligibility lookup reached")

    monkeypatch.setattr("symgov_backend.routes.auth.resolve_eligible_organization_memberships", forbidden)
    response = _login(client)

    assert response.status_code == 200
    assert response.json()["user"]["session"]["purpose"] == "credential_change"
    assert response.json()["user"]["session"]["mode"] == "personal"
    assert response.json()["selectionChallenge"] is None
    assert response.cookies.get("symgov_session")
    with Session() as session:
        assert session.query(AuthOrganizationSelectionChallenge).count() == 0
        assert session.query(UserSession).one().purpose == "credential_change"


@pytest.mark.parametrize("membership_count", (1, 2), ids=("one-organization", "several-organizations"))
def test_successful_mandatory_pin_change_reenters_context_selection(membership_count):
    codes = tuple(f"org-{index}" for index in range(membership_count))
    client, Session, user_id = _build_client(enabled=True, pilots=codes, must_change_pin=True)
    organization_ids = [_add_membership(Session, user_id, code) for code in codes]
    login_response = _login(client)
    limited_token = login_response.cookies.get("symgov_session")

    response = client.post(
        "/api/v1/auth/change-pin",
        json={"currentPin": "1234", "newPin": "5678"},
    )

    assert response.status_code == 200
    if membership_count == 1:
        assert response.json()["selectionChallenge"] is None
        assert response.json()["user"]["session"] == {
            "purpose": "application",
            "mode": "organization",
            "activeOrganizationId": str(organization_ids[0]),
        }
        full_token = response.cookies.get("symgov_session")
        assert full_token and full_token != limited_token
        with Session() as session:
            limited = session.query(UserSession).filter_by(token_hash=hash_session_token(limited_token)).one()
            full = session.query(UserSession).filter_by(token_hash=hash_session_token(full_token)).one()
            assert limited.revoked_at is not None
            assert full.session_mode == "organization"
            assert full.active_organization_id == organization_ids[0]
    else:
        assert response.json()["user"] is None
        assert response.json()["selectionChallenge"]["total"] == membership_count
        assert client.cookies.get("symgov_session") is None
        with Session() as session:
            assert session.query(UserSession).filter(UserSession.revoked_at.is_(None)).count() == 0
            assert session.query(AuthOrganizationSelectionChallenge).count() == 1


@pytest.mark.parametrize("session_mode", ("personal", "organization"))
def test_auth_me_aliases_return_only_bounded_current_context_without_private_membership_leakage(session_mode):
    pilot_codes = (
        "good",
        "sentinel-inactive-org",
        "sentinel-suspended-org",
        "sentinel-member-inactive",
        "sentinel-role-inactive",
    )
    client, Session, user_id = _build_client(pilots=pilot_codes)
    sentinel_organizations = {
        "sentinel-inactive-org": _add_membership(
            Session, user_id, "sentinel-inactive-org", organization_active=False
        ),
        "sentinel-suspended-org": _add_membership(
            Session, user_id, "sentinel-suspended-org", entitlement="suspended"
        ),
        "sentinel-non-pilot": _add_membership(Session, user_id, "sentinel-non-pilot"),
        "sentinel-member-inactive": _add_membership(
            Session, user_id, "sentinel-member-inactive", membership_status="inactive"
        ),
        "sentinel-role-inactive": _add_membership(
            Session, user_id, "sentinel-role-inactive", role_active=False
        ),
    }
    organization_id = None
    if session_mode == "organization":
        organization_id = _add_membership(
            Session, user_id, "good", capabilities=("contributor",)
        )

    login_response = _login(client)

    assert login_response.status_code == 200
    expected_user = login_response.json()["user"]
    expected_session = {
        "purpose": "application",
        "mode": session_mode,
        "activeOrganizationId": str(organization_id) if organization_id else None,
    }
    expected_organization = (
        {
            "id": str(organization_id),
            "code": "GOOD",
            "displayName": "GOOD Organization",
            "baseRole": "user",
            "capabilities": ["contributor"],
        }
        if organization_id
        else None
    )
    expected_capabilities = {
        "organizationsEnabled": True,
        "organizationAdminEnabled": True,
        "symbolSetsEnabled": True,
        "organizationSymbolsEnabled": True,
        "organizationAgentsEnabled": True,
    }

    for path in ("/api/v1/auth/me", "/api/auth/me"):
        response = client.get(path)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"user"}
        assert body == {"user": expected_user}
        assert body["user"]["session"] == expected_session
        assert body["user"]["organization"] == expected_organization
        assert body["user"]["capabilities"] == expected_capabilities
        assert "organizations" not in body
        assert "selectionChallenge" not in body
        serialized = response.text
        for code, sentinel_id in sentinel_organizations.items():
            assert str(sentinel_id) not in serialized
            assert code.upper() not in serialized
            assert f"{code.upper()} Organization" not in serialized


@pytest.mark.parametrize("path", ("/api/v1/auth/login", "/api/auth/login"), ids=("v1", "legacy"))
@pytest.mark.parametrize("outcome", ("personal", "organization", "challenge"))
def test_login_aliases_have_exact_response_and_cookie_parity(path, outcome):
    pilots = ("good",) if outcome == "organization" else (("alpha", "beta") if outcome == "challenge" else ())
    client, Session, user_id = _build_client(pilots=pilots)
    organization_id = None
    challenge_organizations = {}
    if outcome == "organization":
        organization_id = _add_membership(
            Session, user_id, "good", capabilities=("contributor",)
        )
    elif outcome == "challenge":
        challenge_organizations = {
            code: _add_membership(Session, user_id, code) for code in pilots
        }
        client.cookies.set(
            "symgov_session",
            "stale-application-session",
            domain="testserver.local",
            path="/",
        )

    response = _login(client, path)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user", "selectionChallenge"}
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert len(set_cookie_headers) == 1
    set_cookie = set_cookie_headers[0]
    if outcome == "challenge":
        assert body["user"] is None
        challenge = body["selectionChallenge"]
        assert set(challenge) == {
            "token",
            "expiresAt",
            "choices",
            "page",
            "pageSize",
            "total",
            "hasMore",
        }
        assert challenge["token"]
        datetime.fromisoformat(challenge["expiresAt"])
        assert challenge["choices"] == [
            {
                "organizationId": str(challenge_organizations[code]),
                "code": code.upper(),
                "displayName": f"{code.upper()} Organization",
            }
            for code in pilots
        ]
        assert {
            key: challenge[key]
            for key in ("page", "pageSize", "total", "hasMore")
        } == {"page": 1, "pageSize": 5, "total": 2, "hasMore": False}
        deleted_cookie = SimpleCookie()
        deleted_cookie.load(set_cookie)
        morsel = deleted_cookie["symgov_session"]
        assert morsel.value == ""
        assert morsel["max-age"] == "0"
        assert morsel["path"] == "/"
        assert morsel["samesite"] == "lax"
        assert parsedate_to_datetime(morsel["expires"]) <= datetime.now(timezone.utc)
        assert client.cookies.get("symgov_session") is None
        with Session() as session:
            assert session.query(UserSession).count() == 0
            assert session.query(AuthOrganizationSelectionChallenge).count() == 1
        return

    assert body["selectionChallenge"] is None
    user = body["user"]
    assert set(user) == {
        "id",
        "email",
        "displayName",
        "roles",
        "mustChangePin",
        "subscription",
        "session",
        "organization",
        "isPlatformAdmin",
        "capabilities",
    }
    assert {
        key: user[key]
        for key in ("email", "displayName", "roles", "mustChangePin", "isPlatformAdmin")
    } == {
        "email": "member@example.test",
        "displayName": "Member",
        "roles": ["submitter"],
        "mustChangePin": False,
        "isPlatformAdmin": False,
    }
    assert set(user["subscription"]) == {
        "tier",
        "startedOn",
        "expiresOn",
        "isActive",
        "isProtected",
    }
    assert user["session"] == {
        "purpose": "application",
        "mode": outcome,
        "activeOrganizationId": str(organization_id) if organization_id else None,
    }
    assert user["organization"] == (
        {
            "id": str(organization_id),
            "code": "GOOD",
            "displayName": "GOOD Organization",
            "baseRole": "user",
            "capabilities": ["contributor"],
        }
        if organization_id
        else None
    )
    assert user["capabilities"] == {
        "organizationsEnabled": True,
        "organizationAdminEnabled": True,
        "symbolSetsEnabled": True,
        "organizationSymbolsEnabled": True,
        "organizationAgentsEnabled": True,
    }
    token = response.cookies.get("symgov_session")
    assert token and client.cookies.get("symgov_session") == token
    assert set_cookie == (
        f"symgov_session={token}; HttpOnly; Max-Age=1209600; "
        "Path=/; SameSite=lax"
    )
    with Session() as session:
        application_session = session.query(UserSession).one()
        assert application_session.purpose == "application"
        assert application_session.session_mode == outcome
        assert application_session.active_organization_id == organization_id


@pytest.mark.parametrize(
    "path",
    ("/api/v1/auth/change-pin", "/api/auth/change-pin"),
    ids=("v1", "legacy"),
)
def test_successful_mandatory_pin_change_with_no_eligible_organization_issues_personal_session(path):
    client, Session, user_id = _build_client(
        enabled=True,
        pilots=("pilot-without-membership",),
        must_change_pin=True,
    )
    login_response = _login(client)
    limited_token = login_response.cookies.get("symgov_session")
    limited_user = login_response.json()["user"]

    response = client.post(
        path,
        json={"currentPin": "1234", "newPin": "5678"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"user", "selectionChallenge"}
    assert body["selectionChallenge"] is None
    assert body["user"] == {
        "id": str(user_id),
        "email": "member@example.test",
        "displayName": "Member",
        "roles": ["submitter"],
        "mustChangePin": False,
        "subscription": limited_user["subscription"],
        "session": {
            "purpose": "application",
            "mode": "personal",
            "activeOrganizationId": None,
        },
        "organization": None,
        "isPlatformAdmin": False,
        "capabilities": {
            "organizationsEnabled": True,
            "organizationAdminEnabled": True,
            "symbolSetsEnabled": True,
            "organizationSymbolsEnabled": True,
            "organizationAgentsEnabled": True,
        },
    }
    application_token = response.cookies.get("symgov_session")
    assert application_token and application_token != limited_token
    assert client.cookies.get("symgov_session") == application_token
    assert response.headers.get_list("set-cookie") == [
        f"symgov_session={application_token}; HttpOnly; Max-Age=1209600; Path=/; SameSite=lax"
    ]
    with Session() as session:
        limited_session = (
            session.query(UserSession)
            .filter(UserSession.token_hash == hash_session_token(limited_token))
            .one()
        )
        application_session = (
            session.query(UserSession)
            .filter(UserSession.token_hash == hash_session_token(application_token))
            .one()
        )
        assert limited_session.purpose == "credential_change"
        assert limited_session.revoked_at is not None
        assert application_session.purpose == "application"
        assert application_session.session_mode == "personal"
        assert application_session.active_organization_id is None
        assert application_session.revoked_at is None
        assert session.query(AuthOrganizationSelectionChallenge).count() == 0
