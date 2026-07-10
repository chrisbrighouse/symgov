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


class ExecuteRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class CapturingContextualSearchSession:
    def __init__(self, *, key_rows=None, symbol_rows=None, fail_usage_logging: bool = False):
        self.key_rows = list(key_rows or [])
        self.symbol_rows = list(symbol_rows or [])
        self.fail_usage_logging = fail_usage_logging
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.executed = []

    def query(self, model):
        assert model is CatalogApiKey
        return CatalogApiKeyQuery(self.key_rows)

    def execute(self, statement, params=None):
        self.executed.append((str(statement), dict(params or {})))
        return ExecuteRows(self.symbol_rows)

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


def symbol_row(**overrides):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    payload = {
        "package_display_id": "0003",
        "package_symbol_sequence": 12,
        "summary": "Approved fire alarm smoke detector symbol.",
        "keywords": ["smoke", "detector", "fire alarm"],
        "downloads": [
            {"format": "DXF", "filename": "smoke-detector.dxf"},
            {"format": "SVG", "filename": "smoke-detector.svg"},
            {"format": "PNG", "filename": "smoke-detector.png"},
        ],
        "preview_object_key": "previews/smoke-detector.svg",
        "preview_format": "SVG",
    }
    base = {
        "symbol_id": str(uuid.uuid4()),
        "slug": "smoke-detector",
        "canonical_name": "Smoke Detector",
        "category": "symbol",
        "discipline": "Electrical",
        "symbol_revision_id": str(uuid.uuid4()),
        "revision_label": "A",
        "revision_created_at": now,
        "payload_json": payload,
        "rationale": "Approved for catalog publication.",
        "page_id": str(uuid.uuid4()),
        "page_code": "FA-12",
        "page_title": "Smoke Detector",
        "effective_date": now,
        "page_updated_at": now,
        "pack_id": str(uuid.uuid4()),
        "pack_code": "0003",
        "pack_title": "Fire Alarm Symbols",
        "audience": "public",
        "pack_updated_at": now,
        "sort_order": 12,
        "last_updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def build_client(*, key_rows=None, symbol_rows=None, fail_usage_logging: bool = False):
    session = CapturingContextualSearchSession(
        key_rows=key_rows,
        symbol_rows=symbol_rows,
        fail_usage_logging=fail_usage_logging,
    )
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


def contextual_request() -> dict:
    return {
        "query": "smoke detector near stairwell",
        "context": {
            "application": "AutoCAD",
            "discipline": "fire_life_safety",
            "drawingType": "life_safety_plan",
            "selectedLayer": "FIRE_ALARM",
            "units": "mm",
            "preferredFormats": ["DXF", "DWG"],
        },
        "limit": 20,
    }


def test_catalog_contextual_search_requires_valid_catalog_read_key():
    client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[symbol_row()])

    missing = client.post("/api/v1/catalog/search", json=contextual_request())
    invalid = client.post("/api/v1/catalog/search", headers=auth_headers("wrong-token"), json=contextual_request())
    allowed = client.post("/api/v1/catalog/search", headers=auth_headers(), json=contextual_request())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert len(session.executed) == 1


def test_catalog_contextual_search_rejects_valid_key_without_catalog_read_scope():
    client, session = build_client(key_rows=[api_key_row("valid-token", scopes=("catalog.preview",))], symbol_rows=[symbol_row()])

    response = client.post("/api/v1/catalog/search", headers=auth_headers(), json=contextual_request())

    assert response.status_code == 403
    assert session.executed == []


def test_catalog_contextual_search_merges_context_into_ranked_results_and_public_contract():
    client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[symbol_row()])

    response = client.post("/api/v1/catalog/search", headers=auth_headers(), json=contextual_request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["downloadAvailable"] is False
    assert payload["noDownloadNotice"]
    assert payload["query"] == "smoke detector near stairwell"
    assert payload["interpretedFilters"] == {
        "application": "AutoCAD",
        "catalogDisciplines": ["Fire & Life Safety"],
        "drawingType": "life_safety_plan",
        "selectedLayer": "FIRE_ALARM",
        "units": "mm",
        "preferredFormats": ["DXF", "DWG"],
    }
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["displayId"] == "0003-12"
    assert item["availableFormats"] == ["DXF", "SVG", "PNG"]
    assert item["downloadAvailable"] is False
    assert item["links"] == {
        "api": "/api/v1/catalog/symbols/0003-12",
        "thumbnail": "/api/v1/catalog/symbols/0003-12/thumbnail",
        "preview": "/api/v1/catalog/symbols/0003-12/preview",
    }
    assert any("query" in explanation.lower() for explanation in payload["rankingExplanation"])
    assert any("discipline" in explanation.lower() for explanation in payload["rankingExplanation"])
    assert any("format" in explanation.lower() for explanation in payload["rankingExplanation"])
    assert any("DWG" in warning for warning in payload["warnings"])
    serialized = str(payload).lower()
    assert "/api/v1/published" not in serialized
    assert "/api/v1/workspace" not in serialized
    assert "/api/v1/admin" not in serialized
    assert "downloadassets" not in serialized
    assert "downloadurl" not in serialized
    executed_sql, params = session.executed[-1]
    assert "CAST(sr.payload_json AS TEXT) ILIKE :discipline" in executed_sql
    assert params["discipline"] == "%Fire & Life Safety%"
    assert any(event.route_name == "catalog_contextual_search" for event in session.added if isinstance(event, CatalogApiUsageEvent))
    assert session.added[-1].query_text == "smoke detector near stairwell"
    assert session.added[-1].result_count == 1


def test_catalog_contextual_search_usage_logging_failure_does_not_fail_response():
    client, session = build_client(
        key_rows=[api_key_row("valid-token")],
        symbol_rows=[symbol_row()],
        fail_usage_logging=True,
    )

    response = client.post("/api/v1/catalog/search", headers=auth_headers(), json=contextual_request())

    assert response.status_code == 200
    assert response.json()["items"][0]["downloadAvailable"] is False
    assert session.rollbacks == 1
