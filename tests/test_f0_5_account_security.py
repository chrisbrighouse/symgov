import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from threading import Event
import uuid

import fastapi.routing as fastapi_routing
import pytest
import symgov_backend.app as app_module
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import (
    AuthenticatedUser,
    create_user_session,
    hash_session_token,
    revoke_all_user_sessions,
    upsert_user,
    utc_now,
)
from symgov_backend.dependencies import (
    BoundedMutationBodyMiddleware,
    get_current_user,
    get_db_session,
    get_runtime_bridge,
    session_access_decision,
)
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
from symgov_backend.subscriptions import upgrade_to_plus
from symgov_backend.settings import get_settings

OWNER_EMAIL = "chris.brighouse@hotmail.co.uk"
SAME_PIN_DETAIL = "New PIN must be different from the current PIN."
RESET_SAME_PIN_DETAIL = "Reset PIN must be different from the current PIN."
FORCED_PIN_DETAIL = "PIN change is required before accessing this operation."
OVERSIZED_BODY_DETAIL = "Request body is too large."


def build_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        User.__table__,
        UserRole.__table__,
        UserSession.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
    ):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        owner = upsert_user(
            session,
            email=OWNER_EMAIL,
            display_name="Chris",
            roles=["admin", "submitter", "reviewer"],
            pin="4590",
            must_change_pin=True,
        )
        ordinary = upsert_user(
            session,
            email="user@example.test",
            display_name="Ordinary",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        upgrade_to_plus(session, ordinary, months=12)
        session.commit()
        ordinary_id = ordinary.id
        owner_id = owner.id

    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app, headers={"origin": "http://testserver"}), Session, owner_id, ordinary_id


def login(client, email=OWNER_EMAIL, pin="4590", *, legacy=False):
    path = "/api/auth/login" if legacy else "/api/v1/auth/login"
    return client.post(path, json={"email": email, "pin": pin})


def login_admin_application_session(client):
    assert login(client).status_code == 200
    changed = client.post("/api/v1/auth/change-pin", json={"currentPin": "4590", "newPin": "6781"})
    assert changed.status_code == 200


def authentication_maintenance_snapshot(session, user_id):
    session_row = session.query(UserSession).filter(UserSession.auth_user_id == user_id).one()
    subscription = session.get(UserSubscription, user_id)
    return {
        "last_seen_at": session_row.last_seen_at,
        "subscription": None
        if subscription is None
        else (
            subscription.tier,
            subscription.started_on,
            subscription.expires_on,
            subscription.anchor_day,
            subscription.is_protected,
            subscription.version,
            subscription.created_at,
            subscription.updated_at,
        ),
        "roles": tuple(
            session.query(UserRole.role)
            .filter(UserRole.user_id == user_id)
            .order_by(UserRole.role)
            .all()
        ),
        "events": tuple(
            (event.id, event.action, event.created_at)
            for event in session.query(SubscriptionEvent)
            .filter(SubscriptionEvent.user_id == user_id)
            .order_by(SubscriptionEvent.created_at, SubscriptionEvent.id)
            .all()
        ),
    }


@pytest.mark.parametrize("cookie_header", [b"symgov_session=forged", None], ids=["cookie-tagged", "no-cookie"])
def test_mutation_body_middleware_stops_receiving_after_limit_is_crossed(cookie_header):
    received = 0
    sent = []
    messages = [
        {"type": "http.request", "body": b"x" * 8, "more_body": True},
        {"type": "http.request", "body": b"x" * 8, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": True},
        {"type": "http.request", "body": b"must-not-be-read", "more_body": False},
    ]

    async def receive():
        nonlocal received
        received += 1
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def dangerous_app(_scope, _receive, _send):
        raise AssertionError("downstream application reached for oversized body")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/public/external-submissions",
        "headers": [] if cookie_header is None else [(b"cookie", cookie_header)],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    middleware = BoundedMutationBodyMiddleware(dangerous_app, max_body_bytes=16)

    asyncio.run(middleware(scope, receive, send))

    assert received == 3
    assert len(messages) == 1
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("path", ["/api/v1/public/external-submissions", "/api/external-submissions"])
@pytest.mark.parametrize("cookie_value", ["forged-nonempty-cookie", None], ids=["cookie-tagged", "no-cookie"])
def test_external_submission_rejects_chunked_oversized_unauthenticated_request_before_all_side_effects(
    path,
    cookie_value,
    monkeypatch,
):
    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: replace(get_settings(), mutation_max_body_bytes=16),
    )
    app = create_app()
    touched = Counter()
    chunks = (b"{" + (b"x" * 7), b"x" * 8, b"x", b'"late":"chunk"}')

    def chunked_body():
        for chunk in chunks:
            touched["body_chunks"] += 1
            yield chunk

    def dangerous_authentication():
        touched["authentication"] += 1
        raise AssertionError("authentication reached for oversized unauthenticated body")

    def dangerous_database():
        touched["database"] += 1
        raise AssertionError("database reached for oversized unauthenticated body")
        yield

    def dangerous_bridge():
        touched["bridge"] += 1
        raise AssertionError("runtime bridge reached for oversized unauthenticated body")

    routes = getattr(fastapi_routing, "iter_route_contexts", lambda routes: routes)(app.routes)
    target = next(
        route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path == path
        and "POST" in route.methods
    )

    def dangerous_handler(**_kwargs):
        touched["handler"] += 1
        raise AssertionError("handler/domain side effects reached for oversized unauthenticated body")

    target.dependant.call = dangerous_handler
    app.dependency_overrides[get_current_user] = dangerous_authentication
    app.dependency_overrides[get_db_session] = dangerous_database
    app.dependency_overrides[get_runtime_bridge] = dangerous_bridge
    client = TestClient(app, raise_server_exceptions=False, headers={"origin": "http://testserver"})
    if cookie_value is not None:
        client.cookies.set("symgov_session", cookie_value)

    response = client.post(path, headers={"content-type": "application/json"}, content=chunked_body())

    assert response.status_code == 413, (response.json(), touched)
    assert response.json()["detail"] == OVERSIZED_BODY_DETAIL
    assert touched == Counter({"body_chunks": 4})


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
def test_forced_pin_session_allows_only_essential_routes_and_denies_admin_before_handler(prefix):
    client, Session, owner_id, _ = build_client()
    response = login(client, legacy=prefix == "/api")
    assert response.status_code == 200

    with Session() as session:
        stored = session.query(UserSession).filter(UserSession.auth_user_id == owner_id).one()
        assert stored.purpose == "credential_change"

    assert client.get(f"{prefix}/auth/me").status_code == 200
    assert client.get("/api/v1/profile").status_code == 200
    denied = client.get(f"{prefix}/admin/users")
    assert denied.status_code == 403
    assert denied.json()["detail"] == FORCED_PIN_DETAIL


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
def test_live_forced_pin_state_denies_legacy_application_session_before_side_effects(prefix):
    app = create_app()
    iter_route_contexts = getattr(fastapi_routing, "iter_route_contexts", None)
    routes = iter_route_contexts(app.routes) if iter_route_contexts is not None else app.routes
    target = next(
        route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path == f"{prefix}/admin/users"
        and "GET" in route.methods
    )
    touched = Counter()

    def inert_endpoint(**_kwargs):
        touched["handler"] += 1
        raise HTTPException(status_code=418, detail="handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    target.dependant.call = inert_endpoint
    target.dependant.path_params = []
    target.dependant.query_params = []
    target.dependant.body_params = []
    migrated_user = AuthenticatedUser(
        id=str(uuid.uuid4()),
        email="migrated@example.test",
        display_name="Migrated forced PIN",
        roles=("admin",),
        must_change_pin=True,
        session_purpose="application",
    )
    app.dependency_overrides[get_current_user] = lambda: migrated_user
    app.dependency_overrides[get_db_session] = dangerous_db
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "pre-0027-application-session")

    decision = session_access_decision(migrated_user, "GET", f"{prefix}/admin/users")
    assert decision.allowed is False
    response = client.get(f"{prefix}/admin/users")
    assert response.status_code == 403
    assert response.json()["detail"] == FORCED_PIN_DETAIL
    assert touched == Counter()


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
def test_live_forced_pin_application_session_real_dependency_denies_before_authentication_maintenance(prefix):
    client, Session, _, ordinary_id = build_client()
    routes = getattr(fastapi_routing, "iter_route_contexts", lambda routes: routes)(getattr(client.app, "routes"))
    target = next(
        route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path == f"{prefix}/admin/users"
        and "GET" in route.methods
    )
    touched = Counter()

    def inert_endpoint(**_kwargs):
        touched["handler"] += 1
        raise HTTPException(status_code=418, detail="handler reached")

    target.dependant.call = inert_endpoint
    target.dependant.path_params = []
    target.dependant.query_params = []
    target.dependant.body_params = []
    with Session() as session:
        ordinary = session.get(User, ordinary_id)
        token = create_user_session(session, user=ordinary, purpose="application")
        session.commit()

        ordinary.must_change_pin = True
        stored_session = session.query(UserSession).filter(UserSession.auth_user_id == ordinary_id).one()
        stored_session.last_seen_at = datetime(2000, 1, 1)
        session.query(UserRole).filter(UserRole.user_id == ordinary_id).delete(synchronize_session=False)
        session.query(SubscriptionEvent).filter(SubscriptionEvent.user_id == ordinary_id).delete(
            synchronize_session=False
        )
        session.query(UserSubscription).filter(UserSubscription.user_id == ordinary_id).delete(
            synchronize_session=False
        )
        session.commit()
        before = authentication_maintenance_snapshot(session, ordinary_id)
        assert before["subscription"] is None
        assert before["roles"] == ()
        assert before["events"] == ()

    client.cookies.set("symgov_session", token)
    response = client.get(f"{prefix}/admin/users")

    assert response.status_code == 403
    assert response.json()["detail"] == FORCED_PIN_DETAIL
    assert touched == Counter()
    with Session() as session:
        assert authentication_maintenance_snapshot(session, ordinary_id) == before


def test_change_pin_rejects_current_pin_reuse_without_changing_hash():
    client, Session, owner_id, _ = build_client()
    assert login(client).status_code == 200
    with Session() as session:
        before = session.get(User, owner_id).pin_hash

    response = client.post("/api/v1/auth/change-pin", json={"currentPin": "4590", "newPin": "4590"})

    assert response.status_code == 400
    assert response.json()["detail"] == SAME_PIN_DETAIL
    with Session() as session:
        assert session.get(User, owner_id).pin_hash == before
        assert session.get(User, owner_id).must_change_pin is True


def test_successful_forced_pin_change_replaces_limited_session_with_application_session():
    client, Session, owner_id, _ = build_client()
    login_response = login(client)
    old_token = login_response.cookies.get("symgov_session")

    response = client.post("/api/v1/auth/change-pin", json={"currentPin": "4590", "newPin": "6781"})

    assert response.status_code == 200
    new_token = response.cookies.get("symgov_session")
    assert new_token and new_token != old_token
    with Session() as session:
        old = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(old_token)).one()
        new = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(new_token)).one()
        assert old.revoked_at is not None
        assert new.purpose == "application"
    assert client.get("/api/v1/admin/users").status_code == 200


def test_forced_pin_session_cannot_use_hybrid_catalog_download_surface():
    client, _, _, _ = build_client()
    assert login(client).status_code == 200

    response = client.post("/api/v1/catalog/symbols/download", json={})

    assert response.status_code == 403
    assert response.json()["detail"] == FORCED_PIN_DETAIL


def test_admin_reset_rejects_hash_equivalent_pin_and_preserves_sessions():
    client, Session, _, ordinary_id = build_client()
    login_admin_application_session(client)
    with Session() as session:
        ordinary = session.get(User, ordinary_id)
        from symgov_backend.auth import create_user_session

        create_user_session(session, user=ordinary)
        session.commit()
        before_hash = ordinary.pin_hash

    response = client.post(f"/api/v1/admin/users/{ordinary_id}/reset-pin", json={"pin": "1234"})

    assert response.status_code == 400
    assert response.json()["detail"] == RESET_SAME_PIN_DETAIL
    with Session() as session:
        assert session.get(User, ordinary_id).pin_hash == before_hash
        assert session.query(UserSession).filter(UserSession.auth_user_id == ordinary_id, UserSession.revoked_at.is_(None)).count() == 1


def test_admin_reset_and_deactivation_revoke_sessions_while_reactivation_does_not_revive_them():
    client, Session, _, ordinary_id = build_client()
    login_admin_application_session(client)
    with Session() as session:
        ordinary = session.get(User, ordinary_id)
        from symgov_backend.auth import create_user_session

        create_user_session(session, user=ordinary)
        create_user_session(session, user=ordinary)
        session.commit()

    reset = client.post(f"/api/v1/admin/users/{ordinary_id}/reset-pin", json={"pin": "5678"})
    assert reset.status_code == 200
    with Session() as session:
        assert session.query(UserSession).filter(UserSession.auth_user_id == ordinary_id, UserSession.revoked_at.is_(None)).count() == 0
        ordinary = session.get(User, ordinary_id)
        from symgov_backend.auth import create_user_session

        create_user_session(session, user=ordinary)
        session.commit()

    deactivated = client.patch(f"/api/v1/admin/users/{ordinary_id}", json={"isActive": False})
    assert deactivated.status_code == 200
    assert client.patch(f"/api/v1/admin/users/{ordinary_id}", json={"isActive": True}).status_code == 200
    with Session() as session:
        assert session.query(UserSession).filter(UserSession.auth_user_id == ordinary_id, UserSession.revoked_at.is_(None)).count() == 0


def test_forced_pin_guard_denies_every_protected_route_family_before_handler_or_database():
    app = create_app()
    iter_route_contexts = getattr(fastapi_routing, "iter_route_contexts", None)
    routes = iter_route_contexts(app.routes) if iter_route_contexts is not None else app.routes
    selected_operations = {
        ("GET", "/api/v1/admin/users"),
        ("GET", "/api/admin/users"),
        ("POST", "/api/v1/profile/subscription/upgrade"),
        ("PUT", "/api/v1/published/favourites/{symbol_ref}"),
        ("POST", "/api/published/symbols/commands"),
        ("POST", "/api/v1/workspace/hannah/cleanup-actions"),
        ("POST", "/api/workspace/hannah/cleanup-actions"),
        ("POST", "/api/v1/llm/chat"),
        ("POST", "/api/llm/chat"),
        ("POST", "/api/v1/public/external-submissions"),
        ("POST", "/api/external-submissions"),
        ("POST", "/api/v1/catalog/developer/api-key"),
    }
    selected = {
        (method, route.path): route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        for method in route.methods
        if (method, route.path) in selected_operations
    }
    assert set(selected) == selected_operations
    touched = Counter()

    def inert_endpoint(**_kwargs):
        touched["handler"] += 1
        raise HTTPException(status_code=418, detail="handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    for route in selected.values():
        route.dependant.call = inert_endpoint
        route.dependant.path_params = []
        route.dependant.query_params = []
        route.dependant.body_params = []
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()),
        email="forced@example.test",
        display_name="Forced PIN",
        roles=("admin", "integrator", "reviewer", "submitter"),
        must_change_pin=True,
        session_purpose="credential_change",
    )
    app.dependency_overrides[get_db_session] = dangerous_db
    client = TestClient(app, raise_server_exceptions=False, headers={"origin": "http://testserver"})
    client.cookies.set("symgov_session", "opaque-limited-session")

    for method, path in selected_operations:
        response = client.request(
            method,
            path.replace("{symbol_ref}", "SYM-001"),
            json={} if method in {"POST", "PUT", "PATCH"} else None,
        )
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == FORCED_PIN_DETAIL
    assert touched == Counter()


@pytest.mark.parametrize("path", ["/api/v1/public/external-submissions", "/api/external-submissions"])
@pytest.mark.parametrize("operation", ["reset", "deactivate"])
def test_external_submission_revalidates_session_after_concurrent_admin_revocation_before_any_side_effect(
    path,
    operation,
):
    client, Session, _, ordinary_id = build_client()
    with Session() as session:
        ordinary = session.get(User, ordinary_id)
        session.add(UserRole(user_id=ordinary_id, role="submitter", created_at=utc_now()))
        token = create_user_session(session, user=ordinary, purpose="application")
        session.commit()

    stale_user = AuthenticatedUser(
        id=str(ordinary_id),
        email="user@example.test",
        display_name="Ordinary",
        roles=("submitter",),
        must_change_pin=False,
        session_purpose="application",
    )
    authentication_completed = Event()
    resume_request = Event()
    touched = Counter()

    def paused_cached_authentication():
        authentication_completed.set()
        assert resume_request.wait(timeout=10)
        return stale_user

    def dangerous_bridge():
        touched["bridge"] += 1
        raise AssertionError("runtime bridge reached after revocation")

    def dangerous_handler(**_kwargs):
        touched["handler"] += 1
        raise AssertionError("external-submission handler reached after revocation")

    client.app.dependency_overrides[get_current_user] = paused_cached_authentication
    client.app.dependency_overrides[get_runtime_bridge] = dangerous_bridge
    routes = getattr(fastapi_routing, "iter_route_contexts", lambda routes: routes)(getattr(client.app, "routes"))
    target = next(
        route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path == path
        and "POST" in route.methods
    )
    target.dependant.call = dangerous_handler
    client.cookies.set("symgov_session", token)

    with ThreadPoolExecutor(max_workers=1) as executor:
        request_future = executor.submit(client.post, path, json={})
        assert authentication_completed.wait(timeout=10)
        with Session.begin() as session:
            user = session.query(User).filter(User.id == ordinary_id).with_for_update().one()
            now = utc_now()
            if operation == "reset":
                user.must_change_pin = True
            else:
                user.is_active = False
            user.updated_at = now
            revoke_all_user_sessions(session, ordinary_id, now=now)
        with Session() as session:
            after_revocation = authentication_maintenance_snapshot(session, ordinary_id)
        resume_request.set()
        response = request_future.result(timeout=10)

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."
    assert touched == Counter()
    with Session() as session:
        assert authentication_maintenance_snapshot(session, ordinary_id) == after_revocation


@pytest.mark.parametrize("path", ["/api/v1/public/external-submissions", "/api/external-submissions"])
def test_external_submission_authoritative_guard_preserves_normal_application_session_usability(path):
    client, Session, _, ordinary_id = build_client()
    with Session() as session:
        ordinary = session.get(User, ordinary_id)
        session.add(UserRole(user_id=ordinary_id, role="submitter", created_at=utc_now()))
        token = create_user_session(session, user=ordinary, purpose="application")
        session.commit()

    def reached_bridge_after_guard():
        raise HTTPException(status_code=418, detail="authoritative guard passed")

    client.app.dependency_overrides[get_runtime_bridge] = reached_bridge_after_guard
    client.cookies.set("symgov_session", token)

    response = client.post(path, json={})

    assert response.status_code == 418
    assert response.json()["detail"] == "authoritative guard passed"
