from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import Attachment, CatalogApiKey, CatalogApiUsageEvent


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


class AttachmentQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *criteria):
        return self

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class ExecuteRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class CapturingCatalogDetailSession:
    def __init__(self, *, key_rows=None, symbol_rows=None, attachment_rows=None, fail_usage_logging: bool = False):
        self.key_rows = list(key_rows or [])
        self.symbol_rows = list(symbol_rows or [])
        self.attachment_rows = list(attachment_rows or [])
        self.fail_usage_logging = fail_usage_logging
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.executed = []

    def query(self, model):
        if model is CatalogApiKey:
            return CatalogApiKeyQuery(self.key_rows)
        if model is Attachment:
            return AttachmentQuery(self.attachment_rows)
        raise AssertionError(f"Unexpected query model: {model}")

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
        "description": "Smoke detector for approved fire alarm layouts.",
        "keywords": ["smoke", "detector", "fire alarm"],
        "downloads": [
            {"format": "DXF", "filename": "smoke-detector.dxf"},
            {"format": "SVG", "filename": "smoke-detector.svg"},
            {"format": "PNG", "filename": "smoke-detector.png"},
        ],
        "preview_object_key": "previews/smoke-detector.svg",
        "preview_format": "SVG",
        "source_ref": "libby:photo:123",
        "submitted_by": "Libby",
        "submission_kind": "curated_symbol",
        "curated": True,
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


def attachment_row(**overrides):
    base = {"content_type": "image/svg+xml"}
    base.update(overrides)
    return SimpleNamespace(**base)


def build_client(*, key_rows=None, symbol_rows=None, attachment_rows=None, fail_usage_logging: bool = False):
    session = CapturingCatalogDetailSession(
        key_rows=key_rows,
        symbol_rows=symbol_rows,
        attachment_rows=attachment_rows,
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


def test_catalog_symbol_detail_requires_valid_catalog_read_key():
    client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[symbol_row()])

    missing = client.get("/api/v1/catalog/symbols/0003-12")
    invalid = client.get("/api/v1/catalog/symbols/0003-12", headers=auth_headers("wrong-token"))
    allowed = client.get("/api/v1/catalog/symbols/0003-12", headers=auth_headers())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert len(session.executed) == 1


def test_catalog_symbol_detail_rejects_valid_key_without_catalog_read_scope():
    client, session = build_client(key_rows=[api_key_row("valid-token", scopes=("catalog.preview",))], symbol_rows=[symbol_row()])

    response = client.get("/api/v1/catalog/symbols/0003-12", headers=auth_headers())

    assert response.status_code == 403
    assert session.executed == []


def test_catalog_symbol_detail_resolves_display_id_slug_and_uuid_with_public_links_only():
    row = symbol_row(symbol_id=str(uuid.uuid4()))
    for symbol_ref in ["0003-12", "smoke-detector", row.symbol_id]:
        client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[row])

        response = client.get(f"/api/v1/catalog/symbols/{symbol_ref}", headers=auth_headers())

        assert response.status_code == 200
        payload = response.json()
        assert payload["displayId"] == "0003-12"
        assert payload["symbolId"] == row.symbol_id
        assert payload["slug"] == "smoke-detector"
        assert payload["name"] == "Smoke Detector"
        assert payload["summary"] == "Approved fire alarm smoke detector symbol."
        assert payload["taxonomy"] == {
            "disciplines": ["Electrical", "Fire & Life Safety"],
            "categories": ["Fire Alarm Devices", "Sensors / Detectors", "Drawing Symbols"],
            "useCases": ["Insert into CAD drawing", "Mark up / annotate drawing", "Use in PDF/report"],
        }
        assert payload["rawAudit"] == {
            "category": "symbol",
            "discipline": "Electrical",
            "rawCategories": ["symbol"],
            "rawDisciplines": ["Electrical"],
        }
        assert payload["governance"] == {
            "status": "published",
            "revisionId": row.symbol_revision_id,
            "revision": "A",
            "revisionCreatedAt": "2026-07-10T12:00:00+00:00",
            "rationale": "Approved for catalog publication.",
            "effectiveDate": "2026-07-10T12:00:00+00:00",
            "lastUpdatedAt": "2026-07-10T12:00:00+00:00",
            "packCode": "0003",
            "packTitle": "Fire Alarm Symbols",
            "pageCode": "FA-12",
            "pageTitle": "Smoke Detector",
        }
        assert payload["availableFormats"] == ["DXF", "SVG", "PNG"]
        assert payload["downloadAvailable"] is False
        assert payload["preview"] == {
            "thumbnailUrl": "/api/v1/catalog/symbols/0003-12/thumbnail",
            "previewUrl": "/api/v1/catalog/symbols/0003-12/preview",
            "format": "SVG",
        }
        assert payload["curated"] is True
        assert payload["provenance"] == {
            "sourceRef": "libby:photo:123",
            "submittedBy": "Libby",
            "submissionKind": "curated_symbol",
        }
        assert payload["links"] == {
            "api": "/api/v1/catalog/symbols/0003-12",
            "thumbnail": "/api/v1/catalog/symbols/0003-12/thumbnail",
            "preview": "/api/v1/catalog/symbols/0003-12/preview",
        }
        serialized = str(payload).lower()
        assert "/api/v1/published" not in serialized
        assert "/api/v1/workspace" not in serialized
        assert "/api/v1/admin" not in serialized
        assert "downloadassets" not in serialized
        assert "downloadurl" not in serialized
        executed_sql, params = session.executed[-1]
        assert "gs.slug = :symbol_ref" in executed_sql
        assert "gs.id::text = :symbol_ref" in executed_sql
        assert "package_display_id" in executed_sql
        assert params["symbol_ref"] == symbol_ref
        assert any(isinstance(event, CatalogApiUsageEvent) and event.route_name == "catalog_symbol_detail" for event in session.added)
        assert session.added[-1].symbol_ref == symbol_ref


def test_catalog_symbol_detail_returns_404_for_unknown_symbol_ref():
    client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[])

    response = client.get("/api/v1/catalog/symbols/unknown-symbol", headers=auth_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Catalog symbol was not found."
    assert len(session.executed) == 1


def test_catalog_symbol_preview_alias_requires_catalog_read_and_streams_preview_asset(monkeypatch):
    client, session = build_client(
        key_rows=[api_key_row("valid-token")],
        symbol_rows=[symbol_row()],
        attachment_rows=[attachment_row()],
    )

    import symgov_backend.routes.catalog as catalog_route

    monkeypatch.setattr(
        catalog_route,
        "download_object_bytes",
        lambda *, object_key, env_file: {"payload": b"<svg>preview</svg>", "content_type": "image/svg+xml"},
        raising=False,
    )

    missing = client.get("/api/v1/catalog/symbols/0003-12/preview")
    no_scope_client, no_scope_session = build_client(key_rows=[api_key_row("valid-token", scopes=("catalog.preview",))], symbol_rows=[symbol_row()])
    no_scope = no_scope_client.get("/api/v1/catalog/symbols/0003-12/preview", headers=auth_headers())
    allowed = client.get("/api/v1/catalog/symbols/0003-12/preview", headers=auth_headers())
    thumbnail = client.get("/api/v1/catalog/symbols/0003-12/thumbnail", headers=auth_headers())

    assert missing.status_code == 401
    assert no_scope.status_code == 403
    assert no_scope_session.executed == []
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith("image/svg+xml")
    assert allowed.content == b"<svg>preview</svg>"
    assert thumbnail.status_code == 200
    assert thumbnail.content == b"<svg>preview</svg>"
    assert [event.route_name for event in session.added if isinstance(event, CatalogApiUsageEvent)] == [
        "catalog_symbol_preview",
        "catalog_symbol_thumbnail",
    ]


def test_catalog_symbol_detail_usage_logging_failure_does_not_fail_response():
    client, session = build_client(key_rows=[api_key_row("valid-token")], symbol_rows=[symbol_row()], fail_usage_logging=True)

    response = client.get("/api/v1/catalog/symbols/0003-12", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["downloadAvailable"] is False
    assert session.rollbacks == 1
