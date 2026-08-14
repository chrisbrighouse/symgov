from collections import Counter

import fastapi.routing as fastapi_routing
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import (
    API_KEY_ONLY_MUTATION_OPERATIONS,
    UNAUTHENTICATED_LOGIN_OPERATIONS,
    get_current_user,
    get_db_session,
    require_cookie_mutation_security,
)
from symgov_backend.settings import SymgovAPISettings, get_settings

MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
COOKIE_MUTATION_PREFIXES = (
    "/api/v1/auth/",
    "/api/auth/",
    "/api/v1/admin/",
    "/api/admin/",
    "/api/v1/profile/",
    "/api/v1/public/",
    "/api/public/",
    "/api/v1/published/",
    "/api/published/",
    "/api/v1/workspace/",
    "/api/workspace/",
    "/api/v1/llm/",
    "/api/llm/",
    "/api/v1/catalog/developer/",
    "/api/v1/catalog/symbols/download",
)
EXCLUDED_OPERATIONS = UNAUTHENTICATED_LOGIN_OPERATIONS | API_KEY_ONLY_MUTATION_OPERATIONS


def authenticated_user():
    return AuthenticatedUser(
        id="0c242672-3b15-43b7-b870-bbac5f92af06",
        email="admin@example.test",
        display_name="Admin",
        roles=("admin", "reviewer", "submitter", "integrator"),
        must_change_pin=False,
    )


def app_routes(app):
    iter_route_contexts = getattr(fastapi_routing, "iter_route_contexts", None)
    routes = iter_route_contexts(app.routes) if iter_route_contexts is not None else app.routes
    return {
        (method, route.path): route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        for method in route.methods & MUTATION_METHODS
    }


def cookie_mutation_routes(app):
    return {
        key: route
        for key, route in app_routes(app).items()
        if key not in EXCLUDED_OPERATIONS
        and (key[1].startswith(COOKIE_MUTATION_PREFIXES) or key == ("POST", "/api/external-submissions"))
    }


def concrete_path(path):
    result = path
    for parameter in (
        "user_id",
        "queue_item_id",
        "source_site_id",
        "review_case_id",
        "symbol_ref",
    ):
        result = result.replace(f"{{{parameter}}}", "0c242672-3b15-43b7-b870-bbac5f92af06")
    return result


def test_every_cookie_authenticated_mutation_route_has_one_central_csrf_dependency():
    routes = cookie_mutation_routes(create_app())

    assert routes
    family_counts = Counter(path.split("/")[3] for _, path in routes if path.startswith("/api/v1/"))
    expected_minimums = {
        "auth": 2,
        "admin": 1,
        "profile": 2,
        "public": 1,
        "published": 1,
        "workspace": 1,
        "llm": 1,
        "catalog": 2,
    }
    assert all(family_counts[family] >= minimum for family, minimum in expected_minimums.items())
    assert ("POST", "/api/v1/catalog/symbols/download") in routes
    assert ("POST", "/api/v1/public/external-submissions") in routes
    assert ("POST", "/api/external-submissions") in routes
    for route in routes.values():
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert calls.count(require_cookie_mutation_security) == 1


def test_both_login_templates_are_explicitly_unauthenticated_cookie_csrf_exclusions():
    assert UNAUTHENTICATED_LOGIN_OPERATIONS == {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/auth/login"),
    }
    assert UNAUTHENTICATED_LOGIN_OPERATIONS.isdisjoint(API_KEY_ONLY_MUTATION_OPERATIONS)

    routes = app_routes(create_app())
    for operation in UNAUTHENTICATED_LOGIN_OPERATIONS:
        calls = [dependency.call for dependency in routes[operation].dependant.dependencies]
        assert calls.count(require_cookie_mutation_security) == 1


@pytest.mark.parametrize("surface", ["v1", "legacy"])
def test_cross_origin_is_rejected_before_every_cookie_mutation_handler(surface):
    app = create_app()
    routes = cookie_mutation_routes(app)
    selected = {
        key: route
        for key, route in routes.items()
        if (surface == "v1" and key[1].startswith("/api/v1/"))
        or (surface == "legacy" and key[1].startswith("/api/") and not key[1].startswith("/api/v1/"))
    }
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
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db_session] = dangerous_db
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("https://app.symgov.test",),
        csrf_trusted_hosts=("app.symgov.test",),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "opaque-session-token")

    for (method, path), _route in selected.items():
        response = client.request(
            method,
            concrete_path(path),
            json={},
            headers={
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            },
        )
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == "Cross-origin request is not permitted."
    assert touched == Counter()


def test_browser_cookie_mutation_requires_origin_and_json_object_before_handler():
    app = create_app()
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("http://testserver",),
        csrf_trusted_hosts=("testserver",),
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "opaque-session-token")

    missing_origin = client.post(
        "/api/v1/auth/logout",
        headers={"sec-fetch-site": "same-origin"},
    )
    malformed = client.post(
        "/api/v1/auth/change-pin",
        content="[]",
        headers={
            "origin": "http://testserver",
            "sec-fetch-site": "same-origin",
            "content-type": "application/json",
        },
    )
    wrong_type = client.post(
        "/api/v1/auth/change-pin",
        content="currentPin=1234&newPin=5678",
        headers={
            "origin": "http://testserver",
            "sec-fetch-site": "same-origin",
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert missing_origin.status_code == 403
    assert missing_origin.json()["detail"] == "Origin or Referer is required for browser mutations."
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "Request body must be a JSON object."
    assert wrong_type.status_code == 415
    assert wrong_type.json()["detail"] == "Content-Type must be application/json."


@pytest.mark.parametrize("path", ["/api/v1/auth/change-pin", "/api/auth/change-pin"])
@pytest.mark.parametrize("non_finite_constant", ["NaN", "Infinity", "-Infinity"])
def test_cookie_mutations_reject_non_finite_json_before_handler_or_database(
    path,
    non_finite_constant,
):
    app = create_app()
    route = app_routes(app)[("POST", path)]
    touched = Counter()

    def dangerous_endpoint(**_kwargs):
        touched["handler"] += 1
        raise AssertionError("handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    route.dependant.call = dangerous_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_db_session] = dangerous_db
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("http://testserver",),
        csrf_trusted_hosts=("testserver",),
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "opaque-session-token")

    response = client.post(
        path,
        content=f'{{"value":{non_finite_constant}}}',
        headers={"origin": "http://testserver", "content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body must be valid JSON."
    assert touched == Counter()


@pytest.mark.parametrize("path", ["/api/v1/admin/users", "/api/admin/users"])
@pytest.mark.parametrize("non_finite_constant", ["NaN", "Infinity", "-Infinity"])
def test_cookie_mutations_reject_non_finite_json_before_side_effects_without_session_cookie(
    path,
    non_finite_constant,
):
    app = create_app()
    route = app_routes(app)[("POST", path)]
    touched = Counter()

    def dangerous_endpoint(**_kwargs):
        touched["handler"] += 1
        raise AssertionError("handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    route.dependant.call = dangerous_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_db_session] = dangerous_db
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("http://testserver",),
        csrf_trusted_hosts=("testserver",),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        path,
        content=(
            "{"
            '"email":"fresh@example.com",'
            '"displayName":"Fresh",'
            '"roles":[],'
            f'"pin":{non_finite_constant}'
            "}"
        ),
        headers={"origin": "http://testserver", "content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body must be valid JSON."
    assert touched == Counter()


def test_api_key_only_surfaces_are_explicitly_excluded_from_cookie_csrf_policy():
    app = create_app()
    routes = app_routes(app)
    api_key_only = API_KEY_ONLY_MUTATION_OPERATIONS

    def inert_endpoint(**_kwargs):
        raise HTTPException(status_code=418, detail="handler reached")

    for operation in api_key_only:
        route = routes[operation]
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert require_cookie_mutation_security in calls
        route.dependant.call = inert_endpoint
        route.dependant.path_params = []
        route.dependant.query_params = []
        route.dependant.body_params = []
        route.dependant.dependencies = [
            dependency
            for dependency in route.dependant.dependencies
            if dependency.call is require_cookie_mutation_security
        ]
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "opaque-session-token")
    for method, path in api_key_only:
        response = client.request(method, concrete_path(path), json={})
        assert response.status_code == 418, (method, path, response.text)


@pytest.mark.parametrize("surface", ["v1", "legacy"])
def test_every_cookie_mutation_enforces_complete_origin_host_and_proxy_contract(surface):
    app = create_app()
    routes = cookie_mutation_routes(app)
    selected = {
        key: route
        for key, route in routes.items()
        if (surface == "v1" and key[1].startswith("/api/v1/"))
        or (surface == "legacy" and key[1].startswith("/api/") and not key[1].startswith("/api/v1/"))
    }
    touched = Counter()

    def inert_endpoint(**_kwargs):
        touched["handler"] += 1
        raise HTTPException(status_code=418, detail="handler reached")

    for route in selected.values():
        route.dependant.call = inert_endpoint
        route.dependant.path_params = []
        route.dependant.query_params = []
        route.dependant.body_params = []
        route.dependant.dependencies = [
            dependency
            for dependency in route.dependant.dependencies
            if dependency.call is require_cookie_mutation_security
        ]
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("https://app.symgov.test",),
        csrf_trusted_hosts=("app.symgov.test",),
    )
    client = TestClient(app, raise_server_exceptions=False, base_url="https://app.symgov.test")
    client.cookies.set("symgov_session", "opaque-session-token")

    for method, path in selected:
        target = concrete_path(path)
        missing = client.request(method, target, json={})
        allowed = client.request(
            method,
            target,
            json={},
            headers={"origin": "https://app.symgov.test", "host": "app.symgov.test"},
        )
        hostile_host = client.request(
            method,
            target,
            json={},
            headers={"origin": "https://app.symgov.test", "host": "attacker.example"},
        )
        spoofed_forwarding = client.request(
            method,
            target,
            json={},
            headers={
                "origin": "https://app.symgov.test",
                "host": "app.symgov.test",
                "x-forwarded-host": "attacker.example",
                "x-forwarded-proto": "http",
            },
        )
        assert missing.status_code == 403, (method, path, missing.text)
        assert missing.json()["detail"] == "Origin or Referer is required for browser mutations."
        assert allowed.status_code == 418, (method, path, allowed.text)
        assert hostile_host.status_code == 403, (method, path, hostile_host.text)
        assert spoofed_forwarding.status_code == 418, (method, path, spoofed_forwarding.text)
    assert touched == Counter({"handler": len(selected) * 2})


@pytest.mark.parametrize("path", ["/api/v1/auth/logout", "/api/auth/logout"])
def test_cookie_mutation_requires_exact_effective_request_origin(path):
    app = create_app()

    def inert_endpoint(**_kwargs):
        raise HTTPException(status_code=418, detail="handler reached")

    route = app_routes(app)[("POST", path)]
    route.dependant.call = inert_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    route.dependant.dependencies = [
        dependency
        for dependency in route.dependant.dependencies
        if dependency.call is require_cookie_mutation_security
    ]
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=(
            "https://one.symgov.test",
            "https://two.symgov.test",
            "https://app.symgov.test",
            "https://app.symgov.test:8443",
        ),
        csrf_trusted_hosts=("one.symgov.test", "two.symgov.test", "app.symgov.test"),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    client = TestClient(app, raise_server_exceptions=False, base_url="https://app.symgov.test")
    client.cookies.set("symgov_session", "opaque-session-token")

    mismatched_trusted_hosts = client.post(
        path,
        headers={"origin": "https://one.symgov.test", "host": "two.symgov.test"},
    )
    mismatched_port = client.post(
        path,
        headers={"origin": "https://app.symgov.test:8443", "host": "app.symgov.test:9443"},
    )
    default_port = client.post(
        path,
        headers={"origin": "https://app.symgov.test:443", "host": "app.symgov.test"},
    )
    same_origin = client.post(
        path,
        headers={"origin": "https://app.symgov.test", "host": "app.symgov.test"},
    )
    hostile_host = client.post(
        path,
        headers={"origin": "https://app.symgov.test", "host": "attacker.example"},
    )
    spoofed_forwarding = client.post(
        path,
        headers={
            "origin": "https://app.symgov.test",
            "host": "app.symgov.test",
            "x-forwarded-host": "attacker.example",
            "x-forwarded-proto": "http",
        },
    )

    assert mismatched_trusted_hosts.status_code == 403
    assert mismatched_port.status_code == 403
    assert default_port.status_code == 418
    assert same_origin.status_code == 418
    assert hostile_host.status_code == 403
    assert spoofed_forwarding.status_code == 418


@pytest.mark.parametrize(
    "path",
    ["/api/v1/public/external-submissions", "/api/external-submissions"],
)
def test_external_submission_csrf_and_json_fail_before_handler_and_dependencies(path):
    app = create_app()
    route = app_routes(app)[("POST", path)]
    touched = Counter()

    def inert_endpoint(**_kwargs):
        touched["handler"] += 1
        raise HTTPException(status_code=418, detail="handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    route.dependant.call = inert_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db_session] = dangerous_db
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("symgov_session", "opaque-session-token")

    missing_origin = client.post(path, json={})
    cross_origin = client.post(path, json={}, headers={"origin": "https://attacker.example"})
    malformed_json = client.post(
        path,
        content="{",
        headers={"origin": "http://testserver", "content-type": "application/json"},
    )

    assert missing_origin.status_code == 403
    assert cross_origin.status_code == 403
    assert malformed_json.status_code == 422
    assert malformed_json.json()["detail"] == "Request validation failed."
    assert touched == Counter()


def test_trusted_origin_and_host_contract_ignores_untrusted_forwarding():
    app = create_app()

    def inert_endpoint(**_kwargs):
        raise HTTPException(status_code=418, detail="handler reached")

    route = app_routes(app)[("POST", "/api/v1/auth/logout")]
    route.dependant.call = inert_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("https://app.symgov.test",),
        csrf_trusted_hosts=("app.symgov.test",),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    client = TestClient(app, raise_server_exceptions=False, base_url="https://app.symgov.test")
    client.cookies.set("symgov_session", "opaque-session-token")

    allowed = client.post(
        "/api/v1/auth/logout",
        headers={"origin": "https://app.symgov.test", "host": "app.symgov.test"},
    )
    hostile_host = client.post(
        "/api/v1/auth/logout",
        headers={"origin": "https://app.symgov.test", "host": "attacker.example"},
    )
    hostile_forwarded = client.post(
        "/api/v1/auth/logout",
        headers={
            "origin": "https://app.symgov.test",
            "host": "app.symgov.test",
            "x-forwarded-host": "attacker.example",
            "x-forwarded-proto": "http",
        },
    )

    assert allowed.status_code == 418
    assert hostile_host.status_code == 403
    assert hostile_forwarded.status_code == 418


def test_trusted_proxy_may_supply_one_configured_forwarded_host_and_scheme():
    app = create_app()

    def inert_endpoint(**_kwargs):
        raise HTTPException(status_code=418, detail="handler reached")

    route = app_routes(app)[("POST", "/api/v1/auth/logout")]
    route.dependant.call = inert_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        csrf_trusted_origins=("https://app.symgov.test",),
        csrf_trusted_hosts=("app.symgov.test",),
        trusted_proxy_cidrs=("10.0.0.0/8",),
        trusted_proxy_hops=1,
    )
    client = TestClient(app, raise_server_exceptions=False, client=("10.0.0.5", 50000))
    client.cookies.set("symgov_session", "opaque-session-token")

    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "origin": "https://app.symgov.test",
            "host": "internal-proxy:8010",
            "x-forwarded-host": "app.symgov.test",
            "x-forwarded-proto": "https",
        },
    )

    assert response.status_code == 418


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/catalog/ed/query", {"question": "What is available?"}),
        ("/api/v1/catalog/search", {"query": "pump"}),
        (
            "/api/v1/catalog/symbols/SYM-001/feedback",
            {"feedbackType": "metadata", "message": "Needs a clearer description."},
        ),
    ],
)
def test_api_key_only_mutations_ignore_an_unrelated_browser_cookie(path, payload):
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.cookies.set("symgov_session", "stale-browser-cookie")

    response = client.post(
        path,
        json=payload,
        headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] != "Cross-origin request is not permitted."
