from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.catalog_usage import sanitize_usage_text
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import CatalogApiKey, CatalogApiUsageEvent
from symgov_backend.routes.catalog import _read_bounded_catalog_ed_body


class CatalogApiKeyQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def one_or_none(self):
        compiled = "\n".join(
            str(criterion.compile(compile_kwargs={"literal_binds": True}))
            for criterion in self.criteria
        )
        return next((row for row in self.rows if row.key_hash in compiled), None)


class ExecuteRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class CatalogEdSession:
    def __init__(self, *, key_rows=None, symbol_rows=None, fail_usage_logging=False):
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


def api_key_row(token="valid-token", *, scopes=("catalog.ed.query",)):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_name="Acme Engineering",
        integration_name="AutoCAD pilot",
        key_prefix="symgov_live_acme",
        key_hash=hash_api_key(token),
        scopes_json=list(scopes),
        status="active",
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        last_used_at=None,
    )


def symbol_row(**overrides):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    base = {
        "symbol_id": str(uuid.uuid4()),
        "slug": "smoke-detector",
        "canonical_name": "Smoke Detector",
        "category": "symbol",
        "discipline": "Electrical",
        "symbol_revision_id": str(uuid.uuid4()),
        "revision_label": "A",
        "revision_created_at": now,
        "payload_json": {
            "package_display_id": "0003",
            "package_symbol_sequence": 12,
            "summary": "Approved fire alarm smoke detector symbol.",
            "keywords": ["smoke", "detector", "fire alarm"],
            "downloads": [{"format": "DXF", "filename": "private.dxf"}],
            "preview_object_key": "previews/smoke-detector.svg",
            "preview_format": "SVG",
            "download_url": "/api/v1/published/private-download",
            "workspace_url": "/api/v1/workspace/private",
        },
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


def build_client(*, key_rows=None, symbol_rows=None, fail_usage_logging=False):
    session = CatalogEdSession(
        key_rows=key_rows,
        symbol_rows=symbol_rows,
        fail_usage_logging=fail_usage_logging,
    )
    app = create_app()

    def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), session


def auth_headers(token="valid-token"):
    return {
        "Authorization": f"Bearer {token}",
        "X-Symgov-Application": "AutoCAD Plugin",
        "X-Symgov-Application-Version": "0.1.0",
    }


def request_body(**overrides):
    body = {
        "message": "Find smoke detector symbols for a fire alarm CAD drawing",
        "mode": "auto",
        "context": {
            "application": "AutoCAD",
            "applicationVersion": "2026",
            "drawingType": "life_safety_plan",
            "selectedLayer": "FIRE_ALARM",
            "units": "mm",
            "preferredFormats": ["DXF"],
            "projectRef": "PRJ-42",
        },
        "conversationId": "customer-conversation-7",
        "limit": 10,
    }
    body.update(overrides)
    return body


def test_catalog_ed_query_requires_valid_scoped_key():
    scoped = api_key_row()
    client, _ = build_client(key_rows=[scoped], symbol_rows=[symbol_row()])

    missing = client.post("/api/v1/catalog/ed/query", json=request_body())
    invalid = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers("wrong-token"), json=request_body()
    )
    allowed = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body()
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200

    unscoped_client, unscoped_session = build_client(
        key_rows=[api_key_row(scopes=("catalog.read",))], symbol_rows=[symbol_row()]
    )
    forbidden = unscoped_client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body()
    )
    assert forbidden.status_code == 403
    assert unscoped_session.executed == []


@pytest.mark.parametrize(
    "content,content_type",
    [
        ("{broken", "application/json"),
        ("[]", "application/json"),
        ('"message"', "application/json"),
        ('{"message":"What does this catalog contain?","mode":"question","extra":NaN}', "application/json"),
    ],
)
def test_catalog_ed_query_rejects_malformed_or_non_object_json(content, content_type):
    client, _ = build_client(key_rows=[api_key_row()])

    response = client.post(
        "/api/v1/catalog/ed/query",
        headers={**auth_headers(), "content-type": content_type},
        content=content,
    )

    assert response.status_code == 400


def test_catalog_ed_query_rejects_oversized_request_body_before_parsing_fields():
    client, _ = build_client(key_rows=[api_key_row()])

    response = client.post(
        "/api/v1/catalog/ed/query",
        headers={**auth_headers(), "content-type": "application/json"},
        content='{"message":"' + ("x" * 17000) + '"}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body is too large."


def test_catalog_ed_body_reader_stops_after_limit_without_consuming_remaining_chunks():
    class ChunkedRequest:
        headers = {}

        def __init__(self):
            self.consumed = 0

        async def stream(self):
            for chunk in (b"x" * 8192, b"x" * 8192, b"x", b"must-not-be-read"):
                self.consumed += 1
                yield chunk

    request = ChunkedRequest()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_bounded_catalog_ed_body(request))

    assert getattr(exc_info.value, "status_code", None) == 400
    assert request.consumed == 3


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
def test_catalog_ed_query_rejects_empty_or_oversized_trimmed_message(message):
    client, _ = build_client(key_rows=[api_key_row()])

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body(message=message)
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "override",
    [
        {"mode": "approve"},
        {"mode": 7},
        {"context": []},
        {"context": None},
        {"limit": True},
        {"limit": "10"},
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_catalog_ed_query_rejects_invalid_mode_context_or_limit(override):
    client, _ = build_client(key_rows=[api_key_row()])

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body(**override)
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "context",
    [
        {"discipline": "fire_life_safety"},
        {"application": {"nested": "value"}},
        {"application": "x" * 257},
        {"preferredFormats": ["DXF"] * 21},
        {"preferredFormats": [["DXF"]]},
        {"projectRef": "password=hunter2"},
        {"application": "Authorization: " + "Bearer " + "opaque-credential-value"},
        {
            "projectRef": "postgresql://"
            + "user:"
            + "URI_CREDENTIAL_MARKER"
            + "@db.example/catalog"
        },
        {"selectedLayer": "api_key=super-secret"},
        {"projectRef": "symgov_" + "live_" + "abcdefghijklmnopqrstuvwxyz"},
        {
            "projectRef": "eyJ"
            + "abcdefghij"
            + "."
            + "abcdefghijk"
            + "."
            + "signature"
        },
        {"projectRef": "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz"},
    ],
)
def test_catalog_ed_query_rejects_unsafe_context(context):
    client, _ = build_client(key_rows=[api_key_row()])

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body(context=context)
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Symgov-Application", "symgov_" + "live_" + "abcdefghijklmnopqrstuvwxyz"),
        ("X-Symgov-Application-Version", "token=synthetic-token-value"),
        ("X-Request-ID", "Bearer opaque-credential-value"),
        (
            "User-Agent",
            "postgresql://"
            + "catalog_user:"
            + "URI_"
            + "CREDENTIAL_MARKER"
            + "@example.invalid/catalog",
        ),
    ],
)
def test_catalog_ed_query_rejects_credentials_in_persisted_usage_headers(header, value):
    client, session = build_client(key_rows=[api_key_row()], symbol_rows=[symbol_row()])
    headers = {**auth_headers(), header: value}

    response = client.post("/api/v1/catalog/ed/query", headers=headers, json=request_body())

    assert response.status_code == 400
    assert session.executed == []
    assert not any(isinstance(row, CatalogApiUsageEvent) for row in session.added)


def test_catalog_ed_query_rejects_context_over_8kib():
    client, _ = build_client(key_rows=[api_key_row()])
    context = {
        "application": "😀" * 256,
        "applicationVersion": "😀" * 256,
        "drawingType": "😀" * 256,
        "selectedLayer": "😀" * 256,
        "units": "😀" * 256,
        "preferredFormats": ["😀" * 64] * 20,
        "projectRef": "😀" * 256,
    }

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body(context=context)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "context is too large."


def test_catalog_ed_query_preserves_requested_and_auto_selected_modes():
    client, _ = build_client(key_rows=[api_key_row()], symbol_rows=[symbol_row()])

    requested = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(mode="question", message="Where are smoke detectors?"),
    )
    auto_question = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(mode="auto", message="What does this catalog contain?"),
    )
    auto_find = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(mode="auto", message="find smoke detector symbols"),
    )

    assert requested.json()["mode"] == "question"
    assert auto_question.json()["mode"] == "question"
    assert auto_find.json()["mode"] == "find_symbols"


def test_catalog_ed_query_returns_public_found_symbol_contract_without_mutation():
    key = api_key_row()
    client, session = build_client(key_rows=[key], symbol_rows=[symbol_row()])

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body()
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "conversationId",
        "mode",
        "answer",
        "searchQuery",
        "interpretedFilters",
        "symbols",
        "citations",
        "suggestedFollowups",
        "warnings",
        "downloadAvailable",
        "mutatesRecords",
    }
    assert payload["conversationId"] == "customer-conversation-7"
    assert payload["mode"] == "find_symbols"
    assert payload["downloadAvailable"] is False
    assert "downloads are not available" in payload["answer"].lower()
    assert payload["mutatesRecords"] is False
    assert payload["symbols"][0]["displayId"] == "0003-12"
    assert payload["symbols"][0]["downloadAvailable"] is False
    assert payload["citations"] == [
        {"displayId": "0003-12", "href": "/api/v1/catalog/symbols/0003-12"}
    ]
    serialized = str(payload).lower()
    for forbidden in (
        "/api/v1/admin",
        "/api/v1/workspace",
        "/api/v1/published",
        "downloadurl",
        "downloadassets",
    ):
        assert forbidden not in serialized
    assert key.last_used_at is not None
    assert len(session.executed) == 1
    assert all(isinstance(row, CatalogApiUsageEvent) for row in session.added)


def test_catalog_ed_query_returns_no_results_without_inventing_citations():
    client, session = build_client(key_rows=[api_key_row()], symbol_rows=[])

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body()
    )

    assert response.status_code == 200
    assert response.json()["symbols"] == []
    assert response.json()["citations"] == []
    assert session.executed


def test_catalog_ed_question_does_not_search_symbols():
    client, session = build_client(key_rows=[api_key_row()], symbol_rows=[symbol_row()])

    response = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(mode="question", message="What does this catalog contain?"),
    )

    assert response.status_code == 200
    assert response.json()["symbols"] == []
    assert response.json()["citations"] == []
    assert session.executed == []


def test_catalog_ed_query_logs_sanitized_success_metadata():
    message = "find smoke detector api_key=super-secret"
    client, session = build_client(key_rows=[api_key_row()], symbol_rows=[symbol_row()])

    response = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(message=message),
    )

    assert response.status_code == 200
    events = [row for row in session.added if isinstance(row, CatalogApiUsageEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.scope_used == "catalog.ed.query"
    assert event.route_name == "catalog_ed_query"
    assert event.query_text == sanitize_usage_text(message)
    assert "super-secret" not in event.query_text
    assert event.ed_query_type == "find_symbols"
    assert event.result_count == 1
    assert isinstance(event.latency_ms, int) and event.latency_ms >= 0


@pytest.mark.parametrize(
    "message,secret",
    [
        ("find a detector password=hunter2", "hunter2"),
        ("find a detector token=abcd1234", "abcd1234"),
        ("find a detector secret=secret-value", "secret-value"),
        (
            "find a detector "
            + "eyJ"
            + "abcdefghij"
            + "."
            + "abcdefghijk"
            + "."
            + "signature",
            "eyJ" + "abcdefghij" + "." + "abcdefghijk" + "." + "signature",
        ),
        (
            "find a detector " + "symgov_" + "live_" + "abcdefghijklmnopqrstuvwxyz",
            "symgov_" + "live_" + "abcdefghijklmnopqrstuvwxyz",
        ),
        (
            "find a detector " + "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz",
            "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz",
        ),
        ("find a detector Bearer opaque-credential-value", "opaque-credential-value"),
        (
            "find a detector postgresql://"
            + "catalog_user:"
            + "URI_"
            + "CREDENTIAL_MARKER"
            + "@example.invalid/catalog",
            "URI_" + "CREDENTIAL_MARKER",
        ),
    ],
)
def test_catalog_ed_query_redacts_credential_material_from_usage_logging(message, secret):
    client, session = build_client(key_rows=[api_key_row()], symbol_rows=[symbol_row()])
    assert secret in message

    response = client.post(
        "/api/v1/catalog/ed/query",
        headers=auth_headers(),
        json=request_body(message=message),
    )

    assert response.status_code == 200
    events = [row for row in session.added if isinstance(row, CatalogApiUsageEvent)]
    assert len(events) == 1
    assert secret not in events[0].query_text
    assert "[REDACTED]" in events[0].query_text


def test_catalog_ed_query_usage_logging_failure_does_not_fail_answer():
    client, session = build_client(
        key_rows=[api_key_row()], symbol_rows=[symbol_row()], fail_usage_logging=True
    )

    response = client.post(
        "/api/v1/catalog/ed/query", headers=auth_headers(), json=request_body()
    )

    assert response.status_code == 200
    assert response.json()["answer"]
    assert response.json()["mutatesRecords"] is False
    assert session.rollbacks == 1
