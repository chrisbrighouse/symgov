from __future__ import annotations

import pytest

from symgov_backend.app import create_app
from fastapi.testclient import TestClient
from symgov_backend.settings import SymgovAPISettings, get_settings


def test_stage4_routes_are_registered_in_openapi():
    paths = create_app().openapi()["paths"]
    assert "/api/v1/org/me/projects" in paths
    assert "/api/v1/org/me/projects/{project_id}" in paths
    assert "/api/v1/org/me/symbol-sets" in paths
    assert "/api/v1/org/me/symbol-sets/{set_id}" in paths
    assert "orgId" not in str(paths["/api/v1/org/me/projects"])


def test_stage4_routes_are_hidden_when_capability_is_disabled():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        environment="test", organizations_enabled=True, symbol_sets_enabled=False
    )
    response = TestClient(app).get("/api/v1/org/me/projects")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "Not found."}


@pytest.mark.parametrize(
    "path, methods, response_schema",
    [
        ("/api/v1/org/me/projects", ("post",), "ProjectResponse"),
        ("/api/v1/org/me/projects/{project_id}", ("get", "patch"), "ProjectResponse"),
        ("/api/v1/org/me/symbol-sets", ("post",), "SymbolSetResponse"),
        ("/api/v1/org/me/symbol-sets/{set_id}", ("get", "patch"), "SymbolSetResponse"),
    ],
)
def test_stage4_lifecycle_operations_publish_frozen_openapi_contract(path, methods, response_schema):
    operation_map = create_app().openapi()["paths"][path]
    for method in methods:
        operation = operation_map[method]
        success_status = "201" if method == "post" else "200"
        assert operation["responses"][success_status]["content"]["application/json"]["schema"]["$ref"].endswith(response_schema)
        for status in ("401", "403", "404", "409"):
            assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("APIErrorResponse")
        assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("APIValidationErrorResponse")
