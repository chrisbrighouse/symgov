from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import IntegrationAuthContext, get_catalog_api_key_context
from symgov_backend.dependencies import require_user


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        id="synthetic-user", email="dev@example.invalid", display_name="Dev", roles=("integrator",), must_change_pin=False
    )
    app.dependency_overrides[get_catalog_api_key_context] = lambda: IntegrationAuthContext(
        api_key_id="synthetic-key", customer_name="Synthetic", integration_name="Tests",
        scopes=("catalog.read",), key_prefix="not-returned"
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "operation,input_data",
    [
        ("capabilities", {}),
        ("taxonomy", {}),
        ("symbol_search", {"query": "smoke", "limit": 2}),
        ("symbol_detail", {"symbolRef": "SANDBOX-FA-001"}),
        ("contextual_search", {"query": "smoke", "context": {"application": "AutoCAD"}, "limit": 2}),
        ("ed_query", {"message": "Find smoke detector symbols"}),
    ],
)
def test_sandbox_allowlisted_operations_are_deterministic_synthetic_and_read_only(client, operation, input_data):
    first = client.post("/api/v1/catalog/developer/sandbox", json={"operation": operation, "input": input_data})
    second = client.post("/api/v1/catalog/developer/sandbox", json={"operation": operation, "input": input_data})

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["sandbox"] == {
        "simulated": True, "deterministic": True, "readOnly": True, "syntheticData": True
    }
    assert payload["operation"] == operation
    assert payload["mutatesRecords"] is False
    assert "SANDBOX-" in str(payload["result"])
    assert "downloadAvailable" in str(payload["result"])


@pytest.mark.parametrize(
    "body",
    [
        {"operation": "delete_symbol", "input": {}},
        {"operation": "POST /api/v1/admin/users", "input": {}},
        {"operation": "symbol_search", "path": "/api/v1/catalog/symbols", "input": {}},
        {"operation": "symbol_search", "method": "GET", "input": {}},
        {"operation": "symbol_search", "input": {"query": "x", "mutation": "delete"}},
        {"operation": "symbol_search", "input": {"query": "x", "authorization": "Bearer opaque-secret"}},
        {"operation": "symbol_search", "input": {"query": "api_key=secret-value"}},
        {"operation": "symbol_search", "input": {"query": "x", "limit": 101}},
        {"operation": "symbol_detail", "input": {"symbolRef": "../admin"}},
    ],
)
def test_sandbox_rejects_paths_methods_mutations_credentials_and_unbounded_input(client, body):
    response = client.post("/api/v1/catalog/developer/sandbox", json=body)

    assert response.status_code == 400
    assert "opaque-secret" not in str(response.json())
    assert "secret-value" not in str(response.json())


def test_sandbox_rejects_oversized_body(client):
    response = client.post(
        "/api/v1/catalog/developer/sandbox",
        headers={"content-type": "application/json"},
        content='{"operation":"symbol_search","input":{"query":"' + ("x" * 17000) + '"}}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body is too large."


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_sandbox_rejects_non_rfc_json_constants(client, constant):
    response = client.post(
        "/api/v1/catalog/developer/sandbox",
        headers={"content-type": "application/json"},
        content=(
            '{"operation":"contextual_search","input":{"query":"smoke","context":{"score":'
            + constant
            + "}}}"
        ),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body must be valid JSON."


@pytest.mark.parametrize(
    "input_data",
    [
        {"query": "x", "context": {"apiKey": "actual-secret-value"}},
        {"query": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"},
        {"query": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"},
        {"query": "AKIAZZZZZZZZZZZZZZZZ"},
        {"query": "postgresql://user:password@db/catalog"},
    ],
)
def test_sandbox_rejects_structured_and_provider_credentials(client, input_data):
    response = client.post(
        "/api/v1/catalog/developer/sandbox",
        json={"operation": "contextual_search", "input": input_data},
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "query",
    [
        "Use Authorization: Bearer <CATALOG_API_KEY>",
        "Set api_key=<YOUR_API_KEY> at runtime",
        "Example AWS key is AKIAIOSFODNN7EXAMPLE",
        "Connect with postgresql://user:<PASSWORD>@db/catalog",
    ],
)
def test_sandbox_allows_obvious_documentation_placeholders(client, query):
    response = client.post(
        "/api/v1/catalog/developer/sandbox",
        json={"operation": "symbol_search", "input": {"query": query}},
    )
    assert response.status_code == 200
