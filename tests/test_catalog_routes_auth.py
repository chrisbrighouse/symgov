from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import CatalogApiKey, CatalogApiUsageEvent


class CatalogApiKeyQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def one_or_none(self):
        compiled = "\n".join(str(criterion.compile(compile_kwargs={"literal_binds": True})) for criterion in self.criteria)
        for row in self.rows:
            if row.key_hash in compiled:
                return row
        return None


class CapturingCatalogSession:
    def __init__(self, *, rows=None, fail_usage_logging: bool = False):
        self.rows = list(rows or [])
        self.fail_usage_logging = fail_usage_logging
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        assert model is CatalogApiKey
        return CatalogApiKeyQuery(self.rows)

    def add(self, row):
        if self.fail_usage_logging and isinstance(row, CatalogApiUsageEvent):
            raise RuntimeError("usage logging unavailable")
        self.added.append(row)

    def commit(self):
        if self.fail_usage_logging and self.added and isinstance(self.added[-1], CatalogApiUsageEvent):
            raise RuntimeError("usage logging commit unavailable")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def api_key_row(token: str, *, scopes=("catalog.read",), status="active"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_name="Acme Engineering",
        integration_name="AutoCAD pilot",
        key_prefix="symgov_live_acme",
        key_hash=hash_api_key(token),
        scopes_json=list(scopes),
        status=status,
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        last_used_at=None,
    )


def build_client(*, rows=None, fail_usage_logging: bool = False):
    session = CapturingCatalogSession(rows=rows, fail_usage_logging=fail_usage_logging)
    app = create_app()

    def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), session


def auth_headers(token: str = "valid-token") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Symgov-Application": "AutoCAD Plugin",
        "X-Symgov-Application-Version": "0.1.0",
    }


def test_catalog_capabilities_requires_valid_catalog_read_key():
    client, _ = build_client(rows=[api_key_row("valid-token")])

    missing = client.get("/api/v1/catalog/capabilities")
    invalid = client.get("/api/v1/catalog/capabilities", headers=auth_headers("wrong-token"))
    allowed = client.get("/api/v1/catalog/capabilities", headers=auth_headers())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200


def test_catalog_capabilities_rejects_valid_key_without_catalog_read_scope():
    client, _ = build_client(rows=[api_key_row("valid-token", scopes=("catalog.preview",))])

    response = client.get("/api/v1/catalog/capabilities", headers=auth_headers())

    assert response.status_code == 403


def test_catalog_capabilities_describes_only_public_catalog_integration_surface():
    client, session = build_client(rows=[api_key_row("valid-token")])

    response = client.get("/api/v1/catalog/capabilities", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["apiVersion"] == "v1"
    assert payload["catalogName"] == "Symgov Catalog"
    assert payload["downloadAvailable"] is True
    assert payload["auth"]["methods"] == ["api_key"]
    assert payload["auth"]["preferredHeader"] == "Authorization: Bearer ***"
    assert payload["supports"]["keywordSearch"] is True
    assert payload["supports"]["edQuestions"] is True
    assert payload["currentEndpoints"] == [
        {
            "method": "GET",
            "path": "/api/v1/catalog/capabilities",
            "scope": "catalog.read",
        },
        {
            "method": "GET",
            "path": "/api/v1/catalog/taxonomy",
            "scope": "catalog.read",
        },
        {
            "method": "GET",
            "path": "/api/v1/catalog/symbols",
            "scope": "catalog.read",
        },
        {
            "method": "POST",
            "path": "/api/v1/catalog/search",
            "scope": "catalog.read",
        },
        {
            "method": "GET",
            "path": "/api/v1/catalog/symbols/{symbol_ref}",
            "scope": "catalog.read",
        },
        {
            "method": "GET",
            "path": "/api/v1/catalog/symbols/{symbol_ref}/thumbnail",
            "scope": "catalog.read",
        },
        {
            "method": "GET",
            "path": "/api/v1/catalog/symbols/{symbol_ref}/preview",
            "scope": "catalog.read",
        },
        {
            "method": "POST",
            "path": "/api/v1/catalog/symbols/download",
            "scope": "catalog.read",
        },
        {
            "method": "POST",
            "path": "/api/v1/catalog/ed/query",
            "scope": "catalog.ed.query",
        },
        {
            "method": "POST",
            "path": "/api/v1/catalog/symbols/{symbol_ref}/feedback",
            "scope": "catalog.feedback.write",
        },
    ]
    assert payload["supports"]["feedback"] is True
    assert "integration feedback submission" not in payload["futureCapabilities"]
    assert "Ed question and symbol-finding support" not in payload["futureCapabilities"]
    assert "paginated symbol search" not in payload["futureCapabilities"]
    assert "symbol detail and preview aliases" not in payload["futureCapabilities"]
    assert "contextual Catalog search" not in payload["futureCapabilities"]
    assert payload["links"] == {
        "capabilities": "/api/v1/catalog/capabilities",
        "taxonomy": "/api/v1/catalog/taxonomy",
        "symbols": "/api/v1/catalog/symbols",
        "symbolSearch": "/api/v1/catalog/search",
        "symbolDownload": "/api/v1/catalog/symbols/download",
        "edQuery": "/api/v1/catalog/ed/query",
        "feedback": "/api/v1/catalog/symbols/{symbolRef}/feedback",
    }
    serialized = str(payload).lower()
    assert "/api/v1/admin" not in serialized
    assert "/api/v1/workspace" not in serialized
    assert "/api/v1/published" not in serialized
    assert "browser" not in serialized
    assert any(isinstance(row, CatalogApiUsageEvent) and row.route_name == "catalog_capabilities" for row in session.added)


def test_catalog_taxonomy_requires_catalog_read_and_returns_backend_owned_facets():
    client, session = build_client(rows=[api_key_row("valid-token")])

    response = client.get("/api/v1/catalog/taxonomy", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["apiVersion"] == "v1"
    assert payload["downloadAvailable"] is True
    assert "Fire & Life Safety" in payload["facets"]["disciplines"]
    assert "Fire Alarm Devices" in payload["facets"]["categories"]
    assert "DXF" in payload["facets"]["formats"]
    assert "Insert into CAD drawing" in payload["facets"]["useCases"]
    assert payload["links"] == {
        "capabilities": "/api/v1/catalog/capabilities",
        "symbols": "/api/v1/catalog/symbols",
        "symbolDownload": "/api/v1/catalog/symbols/download",
    }
    assert any(isinstance(row, CatalogApiUsageEvent) and row.route_name == "catalog_taxonomy" for row in session.added)


def test_catalog_taxonomy_rejects_key_without_catalog_read_scope():
    client, _ = build_client(rows=[api_key_row("valid-token", scopes=("catalog.preview",))])

    response = client.get("/api/v1/catalog/taxonomy", headers=auth_headers())

    assert response.status_code == 403


def test_catalog_usage_logging_failure_does_not_fail_successful_route_response():
    client, session = build_client(rows=[api_key_row("valid-token")], fail_usage_logging=True)

    response = client.get("/api/v1/catalog/capabilities", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["downloadAvailable"] is True
    assert session.rollbacks == 1
