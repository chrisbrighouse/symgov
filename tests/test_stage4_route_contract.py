from __future__ import annotations

import pytest

from symgov_backend.app import create_app
from fastapi.testclient import TestClient
from symgov_backend.settings import SymgovAPISettings, get_settings


def test_stage4_routes_are_registered_in_openapi():
    paths = create_app().openapi()["paths"]
    assert "/api/v1/org/me/projects" in paths
    assert "/api/v1/org/me/projects/{projectId}" in paths
    assert "/api/v1/org/me/symbol-sets" in paths
    assert "/api/v1/org/me/symbol-sets/{setId}" in paths
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
        ("/api/v1/org/me/projects/{projectId}", ("get", "patch"), "ProjectResponse"),
        ("/api/v1/org/me/symbol-sets", ("post",), "SymbolSetResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}", ("get", "patch"), "SymbolSetResponse"),
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


def test_wp3_routes_publish_exact_success_schemas_and_statuses():
    paths = create_app().openapi()["paths"]
    default_put = paths["/api/v1/org/me/default-symbol-set"]["put"]
    assert default_put["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "OrganizationDefaultSymbolSetResponse"
    )
    assert paths["/api/v1/org/me/default-symbol-set"]["delete"]["responses"]["204"] == {
        "description": "Successful Response"
    }
    assert default_put["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "APIValidationErrorResponse"
    )
    delete = paths["/api/v1/org/me/default-symbol-set"]["delete"]
    for status in ("401", "403", "404", "409"):
        assert delete["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("APIErrorResponse")
    assert delete["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("APIValidationErrorResponse")
    for path, method, success, schema in (
        ("/api/v1/org/me/symbol-sets", "get", "200", "PagedSymbolSetResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}/copy", "post", "201", "SymbolSetResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}/items", "get", "200", "SymbolSetItemsResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}/items", "put", "200", "SymbolSetItemsResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}/projects", "get", "200", "SymbolSetProjectsResponse"),
        ("/api/v1/org/me/symbol-sets/{setId}/projects", "put", "200", "SymbolSetProjectsResponse"),
    ):
        operation = paths[path][method]
        assert operation["responses"][success]["content"]["application/json"]["schema"]["$ref"].endswith(schema)
        if method == "get" or path.endswith("/copy") or method == "put":
            assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
                "APIValidationErrorResponse"
            )


def test_stage4_routes_publish_frozen_camel_case_parameters():
    paths = create_app().openapi()["paths"]
    project_list = paths["/api/v1/org/me/projects"]["get"]
    assert {parameter["name"] for parameter in project_list["parameters"]} == {"page", "pageSize", "includeClosed"}
    assert {parameter["name"] for parameter in paths["/api/v1/org/me/projects/{projectId}"]["get"]["parameters"]} == {"projectId"}
    assert {parameter["name"] for parameter in paths["/api/v1/org/me/symbol-sets"]["get"]["parameters"]} == {"page", "pageSize", "status", "projectId"}
    for path, methods in {
        "/api/v1/org/me/symbol-sets/{setId}": ("get", "patch"),
        "/api/v1/org/me/symbol-sets/{setId}/copy": ("post",),
        "/api/v1/org/me/symbol-sets/{setId}/items": ("get", "put"),
        "/api/v1/org/me/symbol-sets/{setId}/projects": ("get", "put"),
    }.items():
        for method in methods:
            parameters = paths[path][method].get("parameters", [])
            assert "setId" in {parameter["name"] for parameter in parameters}


def test_project_list_publishes_custom_validation_and_auth_error_schemas():
    operation = create_app().openapi()["paths"]["/api/v1/org/me/projects"]["get"]
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "APIValidationErrorResponse"
    )
    for status in ("401", "403"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "APIErrorResponse"
        )
