from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.auth_security import LoginThrottlePolicy, authenticate_login, login_throttle_policy, recover_throttle_bucket
from symgov_backend.dependencies import get_db_session, resolve_client_ip
from symgov_backend.models import (
    AuthLoginAttemptEvent,
    AuthLoginThrottleBucket,
    AuthThrottleRecoveryEvent,
    SubscriptionEvent,
    User,
    UserRole,
    UserSession,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings
from symgov_backend.subscriptions import upgrade_to_plus

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
POLICY = LoginThrottlePolicy(
    account_failure_limit=2,
    ip_failure_limit=3,
    window_seconds=60,
    block_seconds=120,
    hash_secret="test-only-throttle-secret",
)


def test_client_ip_resolution_uses_only_bounded_trusted_proxy_data():
    settings = SymgovAPISettings(trusted_proxy_cidrs=("10.0.0.0/8",), trusted_proxy_hops=2)

    assert resolve_client_ip("203.0.113.10", "198.51.100.8", settings) == "203.0.113.10"
    assert resolve_client_ip("10.0.0.5", "198.51.100.8, 10.0.0.4", settings) == "198.51.100.8"
    assert resolve_client_ip("10.0.0.5", "198.51.100.8, 10.0.0.4, 10.0.0.3", settings) == "10.0.0.5"
    assert resolve_client_ip("10.0.0.5", "not-an-ip", settings) == "10.0.0.5"

    with pytest.raises(ValueError, match="between 1 and 10"):
        resolve_client_ip(
            "10.0.0.5",
            "198.51.100.8",
            SymgovAPISettings(trusted_proxy_cidrs=("10.0.0.0/8",), trusted_proxy_hops=100),
        )


def test_login_hash_secret_is_required_outside_explicit_local_or_test_mode():
    for secret in (
        "",
        "symgov-local-auth-throttle-v1",
        "symgov-explicit-test-auth-throttle-secret",
        "symgov-explicit-local-auth-throttle-secret",
    ):
        settings = SymgovAPISettings(environment="production", auth_login_hash_secret=secret)
        with pytest.raises(ValueError, match="deployment-provided"):
            login_throttle_policy(settings)

    local = SymgovAPISettings(environment="test")
    assert login_throttle_policy(local).hash_secret
    assert "hash_secret" not in repr(login_throttle_policy(local))


def production_security_settings(**overrides):
    values = {
        "environment": "production",
        "auth_login_hash_secret": "deployment-specific-login-hash-secret",
        "trusted_proxy_cidrs": ("10.0.0.0/8",),
        "trusted_proxy_hops": 2,
        "csrf_trusted_origins": ("https://app.symgov.test",),
        "csrf_trusted_hosts": ("app.symgov.test",),
    }
    values.update(overrides)
    return SymgovAPISettings(**values)


@pytest.mark.parametrize(
    "secret",
    (
        "",
        "symgov-local-auth-throttle-v1",
        "symgov-explicit-test-auth-throttle-secret",
        "symgov-explicit-local-auth-throttle-secret",
    ),
    ids=("missing", "former-public-default", "test-placeholder", "local-placeholder"),
)
def test_production_create_app_rejects_every_non_deployment_hmac_secret_without_exposure(monkeypatch, secret):
    settings = production_security_settings(auth_login_hash_secret=secret)
    monkeypatch.setattr("symgov_backend.app.get_settings", lambda: settings)

    with pytest.raises(ValueError) as captured:
        create_app()

    assert "deployment-provided" in str(captured.value)
    assert not secret or secret not in str(captured.value)


@pytest.mark.parametrize(
    ("setting_name", "invalid_value"),
    (
        ("auth_login_account_failure_limit", 0),
        ("auth_login_ip_failure_limit", 0),
        ("auth_login_window_seconds", 0),
        ("auth_login_block_seconds", 0),
        ("mutation_max_body_bytes", 0),
        ("trusted_proxy_hops", 0),
        ("trusted_proxy_cidrs", ("not-a-network",)),
        ("csrf_trusted_origins", ()),
        ("csrf_trusted_origins", ("ftp://app.symgov.test",)),
        ("csrf_trusted_hosts", ()),
        ("csrf_trusted_hosts", ("https://app.symgov.test",)),
    ),
)
def test_create_app_validates_the_full_production_security_configuration(monkeypatch, setting_name, invalid_value):
    settings = production_security_settings(**{setting_name: invalid_value})
    monkeypatch.setattr("symgov_backend.app.get_settings", lambda: settings)

    with pytest.raises(ValueError):
        create_app()


def test_production_create_app_startup_and_health_accept_deployment_security_configuration(monkeypatch):
    settings = production_security_settings()
    monkeypatch.setattr("symgov_backend.app.get_settings", lambda: settings)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize("environment", ("local", "test"))
def test_create_app_retains_explicit_local_and_test_security_defaults(monkeypatch, environment):
    settings = SymgovAPISettings(environment=environment)
    monkeypatch.setattr("symgov_backend.app.get_settings", lambda: settings)

    app = create_app()

    assert app.title == "Symgov API"


def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (
        User.__table__,
        UserRole.__table__,
        UserSession.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def seed_user(Session, *, email="person@example.test", pin="1234", active=True, must_change_pin=False, roles=()):
    with Session() as session:
        user = upsert_user(
            session,
            email=email,
            display_name=email.split("@", 1)[0],
            roles=roles,
            pin=pin,
            must_change_pin=must_change_pin,
        )
        if roles:
            upgrade_to_plus(session, user, months=12)
        for role in roles:
            session.add(UserRole(user_id=user.id, role=role, created_at=user.created_at))
        user.is_active = active
        session.commit()
        return user.id


def test_account_throttle_boundary_expiry_and_append_only_audit():
    Session = session_factory()
    user_id = seed_user(Session)
    with Session() as session:
        first = authenticate_login(session, email="PERSON@example.test", pin="9999", client_ip="203.0.113.10", policy=POLICY, now=NOW)
        second = authenticate_login(session, email="person@example.test", pin="9999", client_ip="203.0.113.10", policy=POLICY, now=NOW + timedelta(seconds=1))
        blocked = authenticate_login(session, email="person@example.test", pin="1234", client_ip="203.0.113.10", policy=POLICY, now=NOW + timedelta(seconds=2))
        session.commit()

        assert first.user is None and first.throttled_scope is None
        assert second.user is None and second.throttled_scope is None
        assert blocked.user is None and blocked.throttled_scope == "account"
        assert blocked.retry_after_seconds == 119
        events = session.query(AuthLoginAttemptEvent).order_by(AuthLoginAttemptEvent.occurred_at).all()
        assert [event.outcome for event in events] == ["failure", "failure", "throttled"]
        assert [event.failure_reason for event in events] == ["invalid_credentials", "invalid_credentials", "throttled_account"]
        assert all(event.email_key_hash != "person@example.test" for event in events)
        assert all(event.client_ip_hash != "203.0.113.10" for event in events)
        assert events[-1].resolved_user_id == user_id

    with Session() as session:
        window_elapsed_but_block_active = authenticate_login(
            session,
            email="person@example.test",
            pin="1234",
            client_ip="203.0.113.10",
            policy=POLICY,
            now=NOW + timedelta(seconds=61),
        )
        session.commit()
        assert window_elapsed_but_block_active.user is None
        assert window_elapsed_but_block_active.throttled_scope == "account"
        assert window_elapsed_but_block_active.retry_after_seconds == 60

    with Session() as session:
        expired = authenticate_login(session, email="person@example.test", pin="1234", client_ip="203.0.113.10", policy=POLICY, now=NOW + timedelta(seconds=122))
        session.commit()
        assert expired.user is not None
        assert expired.throttled_scope is None
        assert session.query(AuthLoginThrottleBucket).filter_by(scope="account").count() == 0
        assert session.query(AuthLoginAttemptEvent).order_by(AuthLoginAttemptEvent.occurred_at.desc()).first().outcome == "success"


def test_ip_throttle_is_independent_and_not_cleared_by_success():
    Session = session_factory()
    seed_user(Session, email="one@example.test", pin="1234")
    seed_user(Session, email="two@example.test", pin="5678")
    with Session() as session:
        for index in range(3):
            result = authenticate_login(
                session,
                email=f"unknown-{index}@example.test",
                pin="0000",
                client_ip="198.51.100.7",
                policy=POLICY,
                now=NOW + timedelta(seconds=index),
            )
            assert result.throttled_scope is None
        blocked = authenticate_login(session, email="one@example.test", pin="1234", client_ip="198.51.100.7", policy=POLICY, now=NOW + timedelta(seconds=3))
        success_elsewhere = authenticate_login(session, email="two@example.test", pin="5678", client_ip="198.51.100.8", policy=POLICY, now=NOW + timedelta(seconds=4))
        still_blocked = authenticate_login(session, email="two@example.test", pin="5678", client_ip="198.51.100.7", policy=POLICY, now=NOW + timedelta(seconds=5))
        session.commit()

        assert blocked.throttled_scope == "ip"
        assert success_elsewhere.user is not None
        assert still_blocked.throttled_scope == "ip"


def test_inactive_failure_is_attributable_without_exposing_raw_identity():
    Session = session_factory()
    user_id = seed_user(Session, email="inactive@example.test", active=False)
    with Session() as session:
        result = authenticate_login(session, email="inactive@example.test", pin="1234", client_ip=None, policy=POLICY, now=NOW)
        session.commit()
        event = session.query(AuthLoginAttemptEvent).one()

        assert result.user is None
        assert event.resolved_user_id == user_id
        assert event.failure_reason == "inactive_or_deleted"
        assert event.client_ip_hash is None
        assert "inactive@example.test" not in event.request_metadata_json
        assert "1234" not in event.request_metadata_json


def test_bounded_recovery_deletes_one_bucket_and_records_actor_reason():
    Session = session_factory()
    actor_id = seed_user(Session, email="admin@example.test", roles=("admin",))
    with Session() as session:
        authenticate_login(session, email="target@example.test", pin="0000", client_ip="203.0.113.20", policy=POLICY, now=NOW)
        session.commit()
        cleared = recover_throttle_bucket(
            session,
            scope="account",
            key="target@example.test",
            actor_id=actor_id,
            reason="Verified account owner request",
            policy=POLICY,
            now=NOW + timedelta(seconds=1),
        )
        session.commit()

        assert cleared == 1
        assert session.query(AuthLoginThrottleBucket).filter_by(scope="account").count() == 0
        assert session.query(AuthLoginThrottleBucket).filter_by(scope="ip").count() == 1
        event = session.query(AuthThrottleRecoveryEvent).one()
        assert event.actor_id == actor_id
        assert event.reason == "Verified account owner request"
        assert event.target_key_hash != "target@example.test"


def build_route_client():
    Session = session_factory()
    seed_user(Session, email="admin@example.test", pin="1234", roles=("admin",))
    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        auth_login_account_failure_limit=2,
        auth_login_ip_failure_limit=3,
        auth_login_window_seconds=60,
        auth_login_block_seconds=120,
        auth_login_hash_secret="test-only-throttle-secret",
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), Session


def build_forwarding_route_client(*, peer_ip, trusted_proxy_cidrs, trusted_proxy_hops):
    Session = session_factory()
    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        auth_login_account_failure_limit=100,
        auth_login_ip_failure_limit=2,
        auth_login_window_seconds=60,
        auth_login_block_seconds=120,
        auth_login_hash_secret="test-only-throttle-secret",
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        trusted_proxy_hops=trusted_proxy_hops,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, client=(peer_ip, 50000)), Session


def build_login_compatibility_client():
    Session = session_factory()
    seed_user(Session, email="login@example.test", pin="1234")
    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


@pytest.mark.parametrize("path", ("/api/v1/auth/login", "/api/auth/login"), ids=("v1", "legacy"))
@pytest.mark.parametrize("stale_cookie", (False, True), ids=("ordinary", "stale-cookie"))
def test_unauthenticated_login_is_unchanged_by_stale_cookie_without_origin_or_referer(path, stale_cookie):
    client = build_login_compatibility_client()
    if stale_cookie:
        client.cookies.set("symgov_session", "stale-or-invalid-session")

    response = client.post(path, json={"email": "login@example.test", "pin": "1234"})

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "login@example.test"


@pytest.mark.parametrize(
    ("peer_ip", "forwarded_for", "trusted_proxy_cidrs", "trusted_proxy_hops"),
    [
        ("203.0.113.10", None, ("10.0.0.0/8",), 2),
        ("203.0.113.10", "198.51.100.8", ("10.0.0.0/8",), 2),
        ("10.0.0.5", "198.51.100.8, 10.0.0.4", ("10.0.0.0/8",), 2),
        ("10.0.0.5", "not-an-ip", ("10.0.0.0/8",), 2),
        ("10.0.0.5", "198.51.100.8, 10.0.0.4, 10.0.0.3", ("10.0.0.0/8",), 2),
    ],
    ids=("direct", "spoofed-untrusted", "valid-trusted-proxy", "malformed-trusted-proxy", "overlong-trusted-proxy"),
)
def test_every_forwarding_class_retains_persisted_ip_throttle_boundary(
    peer_ip,
    forwarded_for,
    trusted_proxy_cidrs,
    trusted_proxy_hops,
):
    client, Session = build_forwarding_route_client(
        peer_ip=peer_ip,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        trusted_proxy_hops=trusted_proxy_hops,
    )
    headers = {"x-forwarded-for": forwarded_for} if forwarded_for is not None else {}

    for index in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": f"unknown-{index}@example.test", "pin": "9999"},
            headers=headers,
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login",
        json={"email": "unknown-blocked@example.test", "pin": "9999"},
        headers=headers,
    )

    assert blocked.status_code == 429
    with Session() as session:
        ip_buckets = session.query(AuthLoginThrottleBucket).filter_by(scope="ip").all()
        assert len(ip_buckets) == 1
        assert ip_buckets[0].failure_count == 2
        events = session.query(AuthLoginAttemptEvent).order_by(AuthLoginAttemptEvent.occurred_at).all()
        assert [event.outcome for event in events] == ["failure", "failure", "throttled"]
        assert len({event.client_ip_hash for event in events}) == 1
        assert events[0].client_ip_hash is not None


def test_login_route_returns_deterministic_429_and_persists_denials():
    client, Session = build_route_client()
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json={"email": "admin@example.test", "pin": "9999"}).status_code == 401

    response = client.post("/api/v1/auth/login", json={"email": "admin@example.test", "pin": "1234"})

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many login attempts. Try again later."
    assert response.headers["retry-after"].isdigit()
    with Session() as session:
        assert [row.outcome for row in session.query(AuthLoginAttemptEvent).order_by(AuthLoginAttemptEvent.occurred_at)] == [
            "failure",
            "failure",
            "throttled",
        ]


def test_recovery_route_is_admin_only_bounded_and_audited():
    client, Session = build_route_client()
    for _ in range(2):
        client.post("/api/v1/auth/login", json={"email": "target@example.test", "pin": "9999"})
    assert client.post("/api/v1/auth/login", json={"email": "admin@example.test", "pin": "1234"}).status_code == 200

    response = client.post(
        "/api/v1/admin/auth/throttles/recover",
        json={"scope": "account", "key": "target@example.test", "reason": "Verified account owner request"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "cleared": 1}
    with Session() as session:
        assert session.query(AuthThrottleRecoveryEvent).count() == 1
