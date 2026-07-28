from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, MultipleResultsFound

from symgov_backend.app import create_app
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    CatalogApiKey,
    CatalogApiUsageEvent,
    ClarificationRecord,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
    User,
)
import symgov_backend.routes.catalog as catalog_routes


SYMBOL_ID = UUID("11111111-1111-1111-1111-111111111111")
REVISION_ID = UUID("22222222-2222-2222-2222-222222222222")
PAGE_ID = UUID("33333333-3333-3333-3333-333333333333")
KEY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ED_USER_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
ED_AGENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
TOKEN = "valid-token"
IDEMPOTENCY_KEY = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def published_row():
    return SimpleNamespace(
        symbol_id=SYMBOL_ID,
        symbol_revision_id=REVISION_ID,
        page_id=PAGE_ID,
        slug="check-valve",
        canonical_name="Check valve",
        pack_code="0002",
        sort_order=32,
        payload_json={"package_display_id": "0002", "package_symbol_sequence": 32},
    )


def api_key_row(*, scopes=("catalog.feedback.write",)):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return SimpleNamespace(
        id=KEY_ID,
        customer_name="Acme Engineering",
        integration_name="AutoCAD pilot",
        key_prefix="symgov_live_acme",
        key_hash=hash_api_key(TOKEN),
        scopes_json=list(scopes),
        status="active",
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        last_used_at=None,
    )


class Query:
    def __init__(self, session, model):
        self.session = session
        self.model = model
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def filter_by(self, **criteria):
        self.criteria.append(criteria)
        return self

    def one_or_none(self):
        if self.model is CatalogApiKey:
            compiled = "\n".join(
                str(criterion.compile(compile_kwargs={"literal_binds": True}))
                for criterion in self.criteria
                if not isinstance(criterion, dict)
            )
            return self.session.key_row if self.session.key_row and self.session.key_row.key_hash in compiled else None
        if self.model is User:
            return SimpleNamespace(id=ED_USER_ID)
        if self.model is AgentDefinition:
            return SimpleNamespace(id=ED_AGENT_ID)
        if self.model is ReviewCase:
            return self.session.existing_case or next(
                (item for item in self.session.added if isinstance(item, ReviewCase)), None
            )
        raise AssertionError(f"Unexpected query model: {self.model}")


class FakeSession:
    def __init__(
        self,
        *,
        key_row=None,
        row=None,
        existing_case=None,
        fail_authoritative_commit=False,
        fail_service=False,
        fail_usage_add=False,
        fail_usage_commit=False,
    ):
        self.key_row = key_row
        self.row = row
        self.existing_case = existing_case
        self.fail_authoritative_commit = fail_authoritative_commit
        self.fail_service = fail_service
        self.fail_usage_add = fail_usage_add
        self.fail_usage_commit = fail_usage_commit
        self.revision = SimpleNamespace(id=REVISION_ID, lifecycle_state="published")
        self.added = []
        self.committed = []
        self.commit_phases = []
        self.rollbacks = 0
        self.sql = []

    def query(self, model):
        return Query(self, model)

    def execute(self, statement, params=None):
        self.sql.append((str(statement), params or {}))
        return SimpleNamespace(all=lambda: [] if self.row is None else [self.row])

    def get(self, model, key, *, with_for_update=False):
        if model is AuditEvent:
            return next((item for item in self.added if isinstance(item, AuditEvent) and item.id == key), None)
        if model is CatalogApiKey and self.key_row is not None and key == self.key_row.id:
            return self.key_row
        if model is AgentQueueItem:
            return next((item for item in self.added if isinstance(item, AgentQueueItem) and item.id == key), None)
        if model is SymbolRevision and key == REVISION_ID:
            return self.revision
        return None

    def add(self, value):
        if self.fail_service and isinstance(value, ClarificationRecord):
            raise RuntimeError("feedback service unavailable")
        if self.fail_usage_add and isinstance(value, CatalogApiUsageEvent):
            raise RuntimeError("usage add unavailable")
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        uncommitted = [item for item in self.added if item not in self.committed]
        if any(isinstance(item, CatalogApiUsageEvent) for item in uncommitted):
            phase = "usage"
        elif any(isinstance(item, ClarificationRecord) for item in uncommitted):
            phase = "authoritative"
        else:
            phase = "auth"
        self.commit_phases.append(phase)
        if phase == "authoritative" and self.fail_authoritative_commit:
            raise RuntimeError("authoritative commit unavailable")
        if phase == "usage" and self.fail_usage_commit:
            raise RuntimeError("usage commit unavailable")
        self.committed.extend(uncommitted)

    def rollback(self):
        self.rollbacks += 1
        self.added[:] = [item for item in self.added if item in self.committed]


def build_client(session: FakeSession, *, raise_server_exceptions=True):
    app = create_app()

    def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def auth_headers(token=TOKEN, **extra):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Symgov-Application": "AutoCAD Plugin",
        "X-Symgov-Application-Version": "0.1.0",
        "X-Request-ID": "request-123",
        "Idempotency-Key": IDEMPOTENCY_KEY,
    }
    headers.update(extra)
    return headers


def valid_body(**overrides):
    body = {"kind": "comment", "message": "Please correct the insertion point.", "context": {}}
    body.update(overrides)
    return body


def records(session, model):
    return [item for item in session.added if isinstance(item, model)]


def test_feedback_requires_valid_key_and_dedicated_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    allowed_session = FakeSession(key_row=api_key_row(), row=published_row())
    missing = build_client(allowed_session).post("/api/v1/catalog/symbols/0002-32/feedback", json=valid_body())

    invalid_session = FakeSession(key_row=api_key_row(), row=published_row())
    invalid = build_client(invalid_session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers("wrong")
    )

    for scopes in (("catalog.read",), ("catalog.preview",)):
        forbidden_session = FakeSession(key_row=api_key_row(scopes=scopes), row=published_row())
        forbidden = build_client(forbidden_session).post(
            "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
        )
        assert forbidden.status_code == 403

    scoped = build_client(allowed_session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert scoped.status_code == 201


@pytest.mark.parametrize("raw", [b"{", b"[]", b"NaN", b'{"kind":"comment","message":Infinity}'])
def test_feedback_rejects_malformed_non_object_and_nonfinite_json(raw: bytes):
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        content=raw,
        headers={**auth_headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert records(session, ClarificationRecord) == []


def test_feedback_body_is_capped_before_endpoint_buffering():
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        content=b"{" + b" " * 17000,
        headers={**auth_headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Request body is too large."


@pytest.mark.parametrize(
    "body",
    [
        {"message": "hello"},
        {"kind": "unknown", "message": "hello"},
        {"kind": 7, "message": "hello"},
        {"kind": "comment"},
        {"kind": "comment", "message": "   "},
        {"kind": "comment", "message": 7},
        {"kind": "comment", "message": "x" * 2001},
    ],
)
def test_feedback_rejects_invalid_kind_and_message(body):
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=body, headers=auth_headers()
    )
    assert response.status_code == 400
    assert records(session, ClarificationRecord) == []


@pytest.mark.parametrize(
    "message",
    [
        "Authorization: Bearer actual-secret-value",
        "token=actual-secret-value",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIAZZZZZZZZZZZZZZZZ",
        "postgresql://user:password@db/catalog",
    ],
)
def test_feedback_rejects_credentials_in_principal_message_before_persistence(message):
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(message=message),
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert records(session, ClarificationRecord) == []
    assert records(session, CatalogApiUsageEvent) == []


@pytest.mark.parametrize(
    "context",
    [
        None,
        [],
        {"unknown": "value"},
        {"application": {"nested": "no"}},
        {"application": "x" * 257},
        {"preferredFormats": "DWG"},
        {"preferredFormats": ["DWG"] * 21},
        {"preferredFormats": ["x" * 65]},
        {"preferredFormats": [None]},
        {"application": "api_key=super-secret-value"},
        {"projectRef": "Bearer abc.def.ghi"},
        {"units": "postgresql://user:password@db/catalog"},
        {"application": "sk-proj-abcdefghijklmnopqrstuvwxyz"},
    ],
)
def test_feedback_rejects_invalid_or_credential_like_context(context):
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(context=context),
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert records(session, ClarificationRecord) == []


def test_feedback_rejects_context_over_compact_utf8_limit():
    session = FakeSession(key_row=api_key_row(), row=published_row())
    context = {name: "😀" * 256 for name in (
        "application", "applicationVersion", "drawingType", "selectedLayer", "units", "projectRef"
    )}
    context["preferredFormats"] = ["界" * 64 for _ in range(20)]
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()) > 8192
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(context=context), headers=auth_headers()
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Symgov-Application", "api_key=super-secret"),
        ("X-Symgov-Application-Version", "Bearer abc.def.ghi"),
        ("X-Request-ID", "redis://user:password@host/0"),
        ("User-Agent", "sk-proj-abcdefghijklmnopqrstuvwxyz"),
    ],
)
def test_feedback_rejects_credential_like_usage_headers(header, value):
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(),
        headers=auth_headers(**{header: value}),
    )
    assert response.status_code == 400
    assert records(session, ClarificationRecord) == []


@pytest.mark.parametrize(
    "kind",
    ["comment", "usage_question", "issue", "request_alternative", "not_found", "standards_question"],
)
def test_each_non_review_kind_persists_attributed_open_feedback_and_returns_safe_receipt(kind, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(key_row=api_key_row(), row=published_row())
    context = {"application": "AutoCAD", "projectRef": "project-17", "preferredFormats": ["DWG"]}
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind=kind, message="  A bounded feedback message.  ", context=context),
        headers=auth_headers(),
    )

    assert response.status_code == 201
    record = records(session, ClarificationRecord)[0]
    assert record.source == "catalog_integration_api"
    assert record.kind == kind
    assert record.status == "open"
    assert record.detail == "A bounded feedback message."
    assert {key: record.context_json[key] for key in context} == context
    assert record.context_json["publication_snapshot"]["revision_id"] == str(REVISION_ID)
    assert record.catalog_api_key_id == KEY_ID
    assert record.submitted_by is None
    assert record.external_submitter_id is None
    audit = records(session, AuditEvent)[0]
    assert audit.actor_id is None
    assert audit.payload_json["catalog_api_key"] == {
        "id": str(KEY_ID),
        "prefix": "symgov_live_acme",
        "customer": "Acme Engineering",
        "integration": "AutoCAD pilot",
    }
    assert response.json() == {
        "status": "recorded",
        "feedbackId": str(record.id),
        "kind": kind,
        "symbol": {"displayId": "0002-32", "symbolId": str(SYMBOL_ID)},
        "reviewRequested": False,
        "mutatesPublishedState": False,
        "remainsPublished": True,
        "requestReplayed": False,
        "workflowDeliveryState": "not_applicable",
    }
    serialized = response.text.lower()
    for forbidden in ("reviewcase", "queue", "action", "hash", "symgov_live", "acme", "autocad", "slug", "name", "/api/v1/published"):
        assert forbidden not in serialized
    assert session.commit_phases == ["authoritative", "usage"]


def test_symbol_lookup_prioritizes_display_id_then_slug_then_uuid_while_preserving_publication_order(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )
    assert response.status_code == 201
    sql, params = next((sql, params) for sql, params in session.sql if "symbol_ref" in params)
    normalized = " ".join(sql.split()).lower()
    assert params == {"symbol_ref": "0002-32"}
    assert "case" in normalized
    order_position = normalized.index("order by case")
    ranking_sql = normalized[order_position:]
    display_position = ranking_sql.index("package_display_id")
    slug_position = ranking_sql.index("gs.slug = :symbol_ref")
    uuid_position = ranking_sql.index("gs.id::text = :symbol_ref")
    assert display_position < slug_position < uuid_position
    assert "pk.pack_code asc" in normalized
    assert "pe.sort_order asc" in normalized
    assert "pp.id asc" in normalized


@pytest.mark.parametrize("symbol_ref", ["check-valve", str(SYMBOL_ID)])
def test_feedback_symbol_lookup_retains_slug_and_uuid_compatibility(symbol_ref, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        f"/api/v1/catalog/symbols/{symbol_ref}/feedback", json=valid_body(), headers=auth_headers()
    )
    assert response.status_code == 201
    assert next(params["symbol_ref"] for _sql, params in session.sql if "symbol_ref" in params) == symbol_ref


def test_unknown_symbol_returns_404_without_feedback_side_effect():
    session = FakeSession(key_row=api_key_row(), row=None)
    response = build_client(session).post(
        "/api/v1/catalog/symbols/missing/feedback", json=valid_body(), headers=auth_headers()
    )
    assert response.status_code == 404
    assert records(session, ClarificationRecord) == []
    assert records(session, CatalogApiUsageEvent) == []
    assert session.commit_phases == []


def test_same_catalog_key_rejects_disjoint_route_target_before_symbol_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(key_row=api_key_row(), row=published_row())
    client = build_client(session)
    first = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )
    assert first.status_code == 201
    session.row = None
    session.sql.clear()

    conflict = client.post(
        "/api/v1/catalog/symbols/completely-disjoint/feedback",
        json=valid_body(),
        headers=auth_headers(),
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_conflict"
    assert not any("FROM published_pages" in sql for sql, _params in session.sql)


def test_review_case_owner_conflict_is_exact_409_without_intake_write(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(
        key_row=api_key_row(),
        row=published_row(),
        existing_case=SimpleNamespace(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            current_stage="classification_review",
            owner_id=UUID("99999999-9999-4999-8999-999999999999"),
        ),
    )

    response = build_client(session, raise_server_exceptions=False).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review"),
        headers=auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "review_case_owner_conflict"
    assert records(session, ClarificationRecord) == []


def test_catalog_boundary_recovers_request_anchor_primary_key_race(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)

    class RaceSession(FakeSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.raced = False

        def add(self, value):
            if isinstance(value, AuditEvent) and value.entity_type == "published_feedback_request" and not self.raced:
                self.raced = True
                self.added.append(value)
                self.committed.append(value)
                raise IntegrityError("request anchor primary key", {}, Exception("duplicate key"))
            super().add(value)

    session = RaceSession(key_row=api_key_row(), row=published_row())
    response = build_client(session, raise_server_exceptions=False).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )

    assert response.status_code == 201
    assert response.json()["requestReplayed"] is True
    assert session.rollbacks == 1


def test_duplicate_open_review_cases_is_exact_catalog_409(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)

    class DuplicateCaseSession(FakeSession):
        def query(self, model):
            query = super().query(model)
            if model is ReviewCase:
                query.one_or_none = lambda: (_ for _ in ()).throw(MultipleResultsFound())
            return query

    session = DuplicateCaseSession(key_row=api_key_row(), row=published_row())
    response = build_client(session, raise_server_exceptions=False).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review"),
        headers=auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "duplicate_open_review_cases"
    assert records(session, ClarificationRecord) == []


def test_send_for_review_uses_shared_workflow_reuses_case_and_queues_before_success(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    monkeypatch.setattr(
        "symgov_backend.services.published_feedback.get_or_create_ed_user",
        lambda session, now=None: SimpleNamespace(id=ED_USER_ID),
    )
    existing_case = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        current_stage="classification_review",
        owner_id=ED_USER_ID,
    )
    session = FakeSession(key_row=api_key_row(), row=published_row(), existing_case=existing_case)
    client = build_client(session)

    first = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review", message="Review this published symbol."),
        headers=auth_headers(),
    )
    second = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review", message="Review it again."),
        headers=auth_headers(**{"Idempotency-Key": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}),
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["kind"] == "send_for_review"
    assert first.json()["reviewRequested"] is True
    assert first.json()["mutatesPublishedState"] is False
    assert first.json()["remainsPublished"] is True
    assert session.revision.lifecycle_state == "published"
    assert records(session, ReviewCase) == []
    assert len(records(session, ReviewCaseAction)) == 2
    assert len(records(session, AgentQueueItem)) == 2
    assert len(records(session, ClarificationRecord)) == 2
    assert all(item.kind == "send_for_review" for item in records(session, ClarificationRecord))
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert existing_case.current_stage == "classification_review"


def test_feedback_usage_event_is_separate_safe_commit_with_kind_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(key_row=api_key_row(), row=published_row())
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="issue", message="Sensitive business feedback is not usage metadata."),
        headers=auth_headers(),
    )
    assert response.status_code == 201
    event = records(session, CatalogApiUsageEvent)[0]
    assert event.scope_used == "catalog.feedback.write"
    assert event.route_name == "catalog_symbol_feedback"
    assert event.symbol_ref == "0002-32"
    assert event.ed_query_type == "issue"
    assert event.query_text is None
    assert event.status_code == 201
    assert session.commit_phases[-2:] == ["authoritative", "usage"]


def test_post_commit_runtime_failure_returns_exact_pending_body_and_same_key_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    monkeypatch.setattr(
        "symgov_backend.services.published_feedback.get_or_create_ed_user",
        lambda session, now=None: SimpleNamespace(id=ED_USER_ID),
    )
    monkeypatch.setattr(
        catalog_routes,
        "materialize_runtime_envelope",
        lambda _envelope: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    existing_case = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        current_stage="classification_review",
        owner_id=ED_USER_ID,
    )
    session = FakeSession(key_row=api_key_row(), row=published_row(), existing_case=existing_case)
    client = build_client(session)

    first = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review", message="Review this published symbol."),
        headers=auth_headers(),
    )
    replay = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=valid_body(kind="send_for_review", message="Review this published symbol."),
        headers=auth_headers(),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["status"] == replay.json()["status"] == "accepted_pending_delivery"
    assert first.json()["workflowDeliveryState"] == replay.json()["workflowDeliveryState"] == "pending"
    assert first.json()["requestReplayed"] is False
    assert replay.json()["requestReplayed"] is True
    assert first.json()["feedbackId"] == replay.json()["feedbackId"]
    assert len(records(session, ClarificationRecord)) == 1
    assert len(records(session, ReviewCaseAction)) == 1
    assert len(records(session, AgentQueueItem)) == 1


@pytest.mark.parametrize("failure", ["add", "commit"])
def test_usage_failure_after_authoritative_commit_still_returns_201_and_preserves_feedback(failure, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(
        key_row=api_key_row(),
        row=published_row(),
        fail_usage_add=failure == "add",
        fail_usage_commit=failure == "commit",
    )
    response = build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )
    assert response.status_code == 201
    committed_feedback = [item for item in session.committed if isinstance(item, ClarificationRecord)]
    assert len(committed_feedback) == 1
    assert committed_feedback[0].id == UUID(response.json()["feedbackId"])
    assert session.rollbacks == 1


@pytest.mark.parametrize("failure", ["service", "commit"])
def test_authoritative_service_or_commit_failure_rolls_back_and_never_returns_success(failure, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = FakeSession(
        key_row=api_key_row(),
        row=published_row(),
        fail_service=failure == "service",
        fail_authoritative_commit=failure == "commit",
    )
    response = build_client(session, raise_server_exceptions=False).post(
        "/api/v1/catalog/symbols/0002-32/feedback", json=valid_body(), headers=auth_headers()
    )
    assert response.status_code == 500
    assert session.rollbacks == 1
    assert [item for item in session.committed if isinstance(item, ClarificationRecord)] == []
    assert records(session, CatalogApiUsageEvent) == []


def test_catalog_route_delegates_feedback_workflow_to_shared_service():
    source = Path(catalog_routes.__file__).read_text(encoding="utf-8")
    route_source = source[source.index("async def catalog_symbol_feedback"):]
    assert "submit_published_feedback(" in route_source
    assert "ReviewCase(" not in route_source
    assert "ReviewCaseAction(" not in route_source
    assert "AgentQueueItem(" not in route_source
