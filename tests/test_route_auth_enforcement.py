from collections import Counter

import pytest
import fastapi.routing as fastapi_routing
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

iter_route_contexts = getattr(fastapi_routing, "iter_route_contexts", None)

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import (
    WORKSPACE_OPERATIONS,
    expand_workspace_operations,
    get_current_user,
    get_db_session,
    require_workspace_access,
)


PRODUCT_METHODS = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
SAMPLE_ID = "0c242672-3b15-43b7-b870-bbac5f92af06"
EXPECTED_V1_ONLY = {
    ("PATCH", "/review-cases/{review_case_id}/symbol-properties"),
    ("GET", "/review-symbol-property-options"),
    ("POST", "/rights-review-cases/{review_case_id}/decisions"),
    ("POST", "/review-cases/{review_case_id}/decisions"),
    ("POST", "/review-cases/{review_case_id}/split-items/process-decisions"),
    ("GET", "/review-cases/{review_case_id}/children/preview"),
    ("GET", "/review-cases/{review_case_id}/source/preview"),
}


def authenticated_user(*roles):
    return AuthenticatedUser(
        id="user-1",
        email="reviewer@example.test",
        display_name="Route policy test",
        roles=tuple(roles),
        must_change_pin=False,
    )


def workspace_routes(app):
    routes = iter_route_contexts(app.routes) if iter_route_contexts is not None else app.routes
    return {
        (method, route.path): route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and (route.path.startswith("/api/v1/workspace") or route.path.startswith("/api/workspace"))
        for method in route.methods & PRODUCT_METHODS
    }


def concrete_url(path):
    return path.replace("{queue_item_id}", SAMPLE_ID).replace("{source_site_id}", SAMPLE_ID).replace(
        "{review_case_id}", SAMPLE_ID
    )


def request_route(client, method, path):
    return client.request(method, concrete_url(path), json={} if method in {"POST", "PATCH", "PUT"} else None)


@pytest.fixture(scope="module")
def inert_workspace_app():
    app = create_app()
    routes = workspace_routes(app)
    originals = {}

    def inert_endpoint(**_kwargs):
        raise HTTPException(status_code=404, detail="Inert endpoint sentinel.")

    for route in routes.values():
        route_id = id(route)
        if route_id in originals:
            continue
        originals[route_id] = (
            route,
            route.dependant.call,
            route.dependant.path_params,
            route.dependant.query_params,
            route.dependant.body_params,
        )
        route.dependant.call = inert_endpoint
        route.dependant.path_params = []
        route.dependant.query_params = []
        route.dependant.body_params = []

    def inert_db():
        yield object()

    app.dependency_overrides[get_db_session] = inert_db
    yield app

    for route, call, path_params, query_params, body_params in originals.values():
        route.dependant.call = call
        route.dependant.path_params = path_params
        route.dependant.query_params = query_params
        route.dependant.body_params = body_params


def test_published_catalog_requires_authenticated_user():
    response = TestClient(create_app()).get("/api/v1/published/packs")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_external_submission_requires_submitter_or_admin_user():
    response = TestClient(create_app()).post("/api/v1/public/external-submissions", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_workspace_policy_inventory_exactly_matches_real_app():
    app = create_app()
    actual = set(workspace_routes(app))
    expanded = expand_workspace_operations()
    expected = {(entry.method, entry.path) for entry in expanded}

    assert actual == expected
    assert len(WORKSPACE_OPERATIONS) == 28
    assert len(expanded) == 49
    assert Counter(entry.surface for entry in expanded) == {"v1": 28, "legacy": 21}
    assert Counter(entry.policy for entry in WORKSPACE_OPERATIONS) == {"admin": 18, "reviewer_admin": 10}
    assert Counter(entry.policy for entry in expanded) == {"admin": 36, "reviewer_admin": 13}
    assert len({(entry.surface, entry.method, entry.template) for entry in expanded}) == 49


def test_workspace_policy_inventory_has_exact_surface_equivalence():
    expanded = expand_workspace_operations()
    by_surface = {
        surface: {(entry.method, entry.template): entry.policy for entry in expanded if entry.surface == surface}
        for surface in ("v1", "legacy")
    }

    assert len(by_surface["v1"].keys() & by_surface["legacy"].keys()) == 21
    assert {key for key in by_surface["v1"] if key not in by_surface["legacy"]} == EXPECTED_V1_ONLY
    assert not (by_surface["legacy"].keys() - by_surface["v1"].keys())
    assert all(by_surface["v1"][key] == by_surface["legacy"][key] for key in by_surface["legacy"])


def test_every_workspace_route_has_the_same_central_authorization_dependency():
    routes = workspace_routes(create_app())
    actor_attributed_mutations = {
        ("PATCH", "/api/v1/workspace/review-cases/{review_case_id}/symbol-properties"),
        ("POST", "/api/v1/workspace/rights-review-cases/{review_case_id}/decisions"),
        ("POST", "/api/v1/workspace/review-cases/{review_case_id}/decisions"),
        ("POST", "/api/v1/workspace/review-cases/{review_case_id}/split-items/process-decisions"),
    }

    assert len(routes) == 49
    for key, route in routes.items():
        calls = [dependency.call for dependency in route.dependant.dependencies]
        expected_count = 2 if key in actor_attributed_mutations else 1
        assert calls.count(require_workspace_access) == expected_count


@pytest.mark.parametrize("entry", expand_workspace_operations(), ids=lambda entry: f"anonymous-{entry.surface}-{entry.method}-{entry.template}")
def test_every_workspace_route_rejects_unauthenticated_callers(inert_workspace_app, entry):
    inert_workspace_app.dependency_overrides[get_current_user] = lambda: None
    response = request_route(TestClient(inert_workspace_app, raise_server_exceptions=False), entry.method, entry.path)

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


@pytest.mark.parametrize("entry", expand_workspace_operations(), ids=lambda entry: f"reviewer-{entry.surface}-{entry.method}-{entry.template}")
def test_every_workspace_route_enforces_reviewer_policy(inert_workspace_app, entry):
    inert_workspace_app.dependency_overrides[get_current_user] = lambda: authenticated_user("reviewer")
    response = request_route(TestClient(inert_workspace_app, raise_server_exceptions=False), entry.method, entry.path)

    expected = 404 if entry.policy == "reviewer_admin" else 403
    assert response.status_code == expected
    assert response.json()["detail"] == (
        "Inert endpoint sentinel." if expected == 404 else "Insufficient role for this operation."
    )


@pytest.mark.parametrize("entry", expand_workspace_operations(), ids=lambda entry: f"admin-{entry.surface}-{entry.method}-{entry.template}")
def test_every_workspace_route_accepts_admin(inert_workspace_app, entry):
    inert_workspace_app.dependency_overrides[get_current_user] = lambda: authenticated_user("admin")
    response = request_route(TestClient(inert_workspace_app, raise_server_exceptions=False), entry.method, entry.path)

    assert response.status_code == 404
    assert response.json()["detail"] == "Inert endpoint sentinel."


@pytest.mark.parametrize("roles,expected", [(None, 401), (("reviewer",), None), (("admin",), 404)])
def test_all_shared_workspace_operations_have_legacy_v1_authorization_parity(inert_workspace_app, roles, expected):
    shared = [entry for entry in WORKSPACE_OPERATIONS if set(entry.surfaces) == {"v1", "legacy"}]
    assert len(shared) == 21
    inert_workspace_app.dependency_overrides[get_current_user] = (
        (lambda: None) if roles is None else (lambda: authenticated_user(*roles))
    )
    client = TestClient(inert_workspace_app, raise_server_exceptions=False)

    for operation in shared:
        statuses = []
        for surface in ("v1", "legacy"):
            entry = next(
                entry
                for entry in expand_workspace_operations()
                if entry.surface == surface and entry.method == operation.method and entry.template == operation.template
            )
            statuses.append(request_route(client, entry.method, entry.path).status_code)
        policy_expected = 403 if roles == ("reviewer",) and operation.policy == "admin" else expected or 404
        assert statuses == [policy_expected, policy_expected]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/workspace/hannah/cleanup-actions"),
        ("POST", "/api/v1/workspace/scott/source-searches"),
        ("POST", f"/api/v1/workspace/scott/source-searches/{SAMPLE_ID}/stop"),
    ],
)
def test_reviewer_denial_precedes_dangerous_handler_and_database_dependencies(method, path):
    app = create_app()
    template_path = path.replace(SAMPLE_ID, "{queue_item_id}")
    route = workspace_routes(app)[(method, template_path)]
    touched = Counter()

    def dangerous_endpoint(**_kwargs):
        touched["handler"] += 1
        raise AssertionError("dangerous handler reached")

    def dangerous_db():
        touched["database"] += 1
        raise AssertionError("database dependency reached")
        yield

    route.dependant.call = dangerous_endpoint
    route.dependant.path_params = []
    route.dependant.query_params = []
    route.dependant.body_params = []
    app.dependency_overrides[get_db_session] = dangerous_db
    app.dependency_overrides[get_current_user] = lambda: authenticated_user("reviewer")

    response = request_route(TestClient(app, raise_server_exceptions=False), method, path)

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient role for this operation."
    assert touched == Counter()
