from __future__ import annotations

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import IntegrationAuthContext, get_catalog_api_key_context
from symgov_backend.dependencies import require_user


def build_client():
    app = create_app()
    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        id="synthetic-user", email="dev@example.invalid", display_name="Dev", roles=("integrator",), must_change_pin=False
    )
    app.dependency_overrides[get_catalog_api_key_context] = lambda: IntegrationAuthContext(
        api_key_id="synthetic-key", customer_name="Synthetic", integration_name="Tests",
        scopes=("catalog.read", "catalog.ed.query", "catalog.feedback.write"), key_prefix="not-returned"
    )
    return app, TestClient(app)


def test_developer_openapi_requires_integrator_login_but_not_catalog_key():
    app = create_app()
    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        id="synthetic-user", email="dev@example.invalid", display_name="Dev", roles=("integrator",), must_change_pin=False
    )
    client = TestClient(app)

    response = client.get("/api/v1/catalog/developer/openapi.json")

    assert response.status_code == 200


def test_generic_fastapi_documentation_is_not_publicly_exposed():
    client = TestClient(create_app())

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_developer_openapi_is_catalog_only_and_describes_security_errors_and_no_download():
    _, client = build_client()

    response = client.get("/api/v1/catalog/developer/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == "Symgov Catalog Integration API"
    assert document["x-download-available"] is True
    assert document["components"]["securitySchemes"]["CatalogApiKey"] == {
        "type": "http", "scheme": "bearer", "bearerFormat": "Catalog API key"
    }
    assert {"CatalogApiKey": ["catalog.read"]} in document["security"]
    assert document["components"]["schemas"]
    assert document["components"]["examples"]
    assert document["components"]["responses"]["AuthenticationError"]
    assert document["components"]["responses"]["ScopeError"]
    assert document["components"]["responses"]["ValidationError"]

    for path, operations in document["paths"].items():
        assert path.startswith("/api/v1/catalog/")
        assert not path.startswith("/api/v1/catalog/developer")
        for operation in operations.values():
            assert operation["security"]
            assert operation["responses"]
            assert operation["description"]

    assert "/api/v1/catalog/symbols/download" in document["paths"]
    documented_paths = " ".join(document["paths"]).lower()
    for forbidden in ("/admin", "/auth", "/workspace", "/published", "/llm"):
        assert forbidden not in documented_paths


def test_developer_openapi_paths_match_registered_catalog_integration_routes():
    app, client = build_client()
    effective_routes = []
    for route in app.routes:
        if hasattr(route, "path"):
            effective_routes.append(route)
        else:
            effective_routes.extend(route.effective_route_contexts())
    registered = {
        (method, route.path)
        for route in effective_routes
        if route.path.startswith("/api/v1/catalog/")
        and not route.path.startswith("/api/v1/catalog/developer")
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"}
    }
    document = client.get("/api/v1/catalog/developer/openapi.json").json()
    documented = {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
    }

    assert documented == registered


def test_developer_openapi_documents_bounds_examples_schemas_and_exact_scopes():
    _, client = build_client()
    document = client.get("/api/v1/catalog/developer/openapi.json").json()

    symbols = document["paths"]["/api/v1/catalog/symbols"]["get"]
    limit = next(item for item in symbols["parameters"] if item["name"] == "limit")
    assert "maximum" not in limit["schema"]
    assert "capped" in limit["description"]
    assert symbols["x-required-scope"] == "catalog.read"
    assert document["paths"]["/api/v1/catalog/ed/query"]["post"]["x-required-scope"] == "catalog.ed.query"
    feedback = document["paths"]["/api/v1/catalog/symbols/{symbol_ref}/feedback"]["post"]
    assert feedback["x-required-scope"] == "catalog.feedback.write"
    assert feedback["requestBody"]["content"]["application/json"]["schema"]
    assert feedback["requestBody"]["content"]["application/json"]["example"]
    download = document["paths"]["/api/v1/catalog/symbols/download"]["post"]
    assert download["x-required-scope"] == "catalog.read"
    assert download["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DownloadRequest"
    }
    assert set(download["responses"]["200"]["content"]) == {
        "application/octet-stream",
        "application/zip",
    }


def test_developer_openapi_matches_filter_preview_and_feedback_wire_contracts():
    _, client = build_client()
    document = client.get("/api/v1/catalog/developer/openapi.json").json()

    symbols = document["paths"]["/api/v1/catalog/symbols"]["get"]
    assert {item["name"] for item in symbols["parameters"]} == {
        "q", "discipline", "category", "useCase", "format", "pack",
        "symbolFamily", "hasPreview", "updatedSince", "limit", "cursor", "include",
    }

    for suffix in ("thumbnail", "preview"):
        response = document["paths"][f"/api/v1/catalog/symbols/{{symbol_ref}}/{suffix}"]["get"]["responses"]["200"]
        assert "application/json" not in response.get("content", {})
        assert any(media_type.startswith("image/") for media_type in response["content"])
        assert all(content["schema"] == {"type": "string", "format": "binary"} for content in response["content"].values())

    feedback = document["paths"]["/api/v1/catalog/symbols/{symbol_ref}/feedback"]["post"]
    assert "201" in feedback["responses"]
    assert "200" not in feedback["responses"]

    ed_schema = document["components"]["schemas"]["EdQueryRequest"]
    assert "conversationId" in ed_schema["properties"]
    assert set(ed_schema["properties"]["context"]["properties"]) == {
        "application", "applicationVersion", "drawingType", "selectedLayer",
        "units", "preferredFormats", "projectRef",
    }


def test_developer_openapi_has_endpoint_specific_success_and_truthful_error_schemas():
    _, client = build_client()
    document = client.get("/api/v1/catalog/developer/openapi.json").json()
    refs = {}
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            success_code = "201" if path.endswith("/feedback") else "200"
            content = operation["responses"][success_code].get("content", {})
            if "application/json" in content:
                refs[(method, path)] = content["application/json"]["schema"]["$ref"]
    assert len(set(refs.values())) == len(refs)
    assert all(not ref.endswith("/CatalogResponse") for ref in refs.values())

    error = document["components"]["schemas"]["Error"]
    assert error["required"] == ["error", "detail"]
    assert set(error["properties"]) == {"error", "detail"}
    assert error["properties"]["error"]["type"] == "string"
    assert error["properties"]["detail"]["type"] == "string"

    validation_error = document["components"]["schemas"]["HTTPValidationError"]
    assert validation_error["required"] == ["error", "detail", "issues"]
    assert set(validation_error["properties"]) == {"error", "detail", "issues"}
    assert validation_error["properties"]["error"]["type"] == "string"
    assert validation_error["properties"]["detail"]["type"] == "string"
    assert validation_error["properties"]["issues"]["type"] == "array"
    symbols = document["paths"]["/api/v1/catalog/symbols"]["get"]
    assert "422" in symbols["responses"]


def test_developer_openapi_contextual_search_constraints_match_live_clamping_contract():
    _, client = build_client()
    document = client.get("/api/v1/catalog/developer/openapi.json").json()
    schema = document["components"]["schemas"]["ContextualSearchRequest"]
    assert schema["required"] == ["query"]
    assert schema["properties"]["query"]["minLength"] == 1
    assert schema["properties"]["query"]["maxLength"] == 2000
    limit = schema["properties"]["limit"]
    assert "minimum" not in limit and "maximum" not in limit
    assert "clamp" in limit["description"].lower()
