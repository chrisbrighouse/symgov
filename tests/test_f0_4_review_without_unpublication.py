from __future__ import annotations

import importlib.util
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from symgov_backend.app import create_app
from symgov_backend.agent_queue_reconciliation import (
    ACTIVE_STATUSES,
    WAITING_OPERATOR_STATUSES,
)
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import authenticate_catalog_api_key, hash_api_key
from symgov_backend.catalog_developer import catalog_openapi_document
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    CatalogApiKey,
    CatalogApiUsageEvent,
    ClarificationRecord,
    GovernedSymbol,
    PackEntry,
    PublicationPack,
    PublishedPage,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
    User,
)
from symgov_backend.models.base import Base
import symgov_backend.routes.published as published_routes
import symgov_backend.routes.catalog as catalog_routes
import test_catalog_feedback as catalog_fixture
from symgov_backend.routes.published import (
    group_distinct_published_symbol_targets,
    normalize_published_symbol_command_request,
)
from symgov_backend.services import published_feedback


REQUEST_ID = UUID("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb")
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SYMBOL_ID = UUID("22222222-2222-4222-8222-222222222222")
REAL_KEY_ID = UUID("99999999-9999-4999-8999-999999999999")
REAL_KEY_TOKEN = "f04-real-transaction-token"


@pytest.fixture
def real_postgres_session_factory():
    database_url = os.environ.get("SYMGOV_F04_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SYMGOV_F04_TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    engine = create_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    revision_id = UUID("33333333-3333-4333-8333-333333333333")
    page_id = UUID("44444444-4444-4444-8444-444444444444")
    pack_id = UUID("55555555-5555-4555-8555-555555555555")
    owner_id = UUID("66666666-6666-4666-8666-666666666666")
    ed_user_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    with Session.begin() as session:
        session.add_all([
            User(id=USER_ID, email="requester@example.test", display_name="Requester", pin_hash="test", pin_set_at=now,
                 must_change_pin=False, is_active=True, created_at=now, updated_at=now),
            User(id=owner_id, email="owner@example.test", display_name="Owner", pin_hash="test", pin_set_at=now,
                 must_change_pin=False, is_active=True, created_at=now, updated_at=now),
            User(id=ed_user_id, email="ed@symgov.local", display_name="Ed", pin_hash="test", pin_set_at=now,
                 must_change_pin=False, is_active=False, created_at=now, updated_at=now),
        ])
        session.flush()
        symbol = GovernedSymbol(
            id=SYMBOL_ID, slug="pump", canonical_name="Pump", category="equipment", discipline="mechanical",
            owner_id=owner_id, current_revision_id=None, created_at=now, updated_at=now,
        )
        session.add(symbol)
        session.flush()
        revision = SymbolRevision(
            id=revision_id, symbol_id=SYMBOL_ID, revision_label="Rev A", lifecycle_state="published",
            payload_json={"package_display_id": "0001", "package_symbol_sequence": 7}, rationale="test",
            author_id=owner_id, created_at=now,
        )
        session.add(revision)
        session.flush()
        symbol.current_revision_id = revision_id
        session.add_all([
            PublicationPack(id=pack_id, pack_code="0001", title="Test pack", audience="public",
                            effective_date=date(2026, 7, 28), status="published", created_at=now, updated_at=now),
            AgentDefinition(id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"), slug="ed", display_name="Ed",
                            role="feedback", model="test", status="active", queue_family="feedback",
                            created_at=now, updated_at=now),
            CatalogApiKey(id=REAL_KEY_ID, customer_name="Test customer", integration_name="Test integration",
                          key_prefix="symgov_test", key_hash=hash_api_key(REAL_KEY_TOKEN),
                          scopes_json=["catalog.feedback.write"], status="active", allowed_origins_json=[],
                          expires_at=now + timedelta(days=1), last_used_at=None, created_at=now, updated_at=now),
        ])
        session.flush()
        session.add(PublishedPage(id=page_id, page_code="0001-7", title="Pump", pack_id=pack_id,
                                  current_symbol_revision_id=revision_id, effective_date=date(2026, 7, 28),
                                  created_at=now, updated_at=now))
        session.flush()
        session.add(PackEntry(id=UUID("77777777-7777-4777-8777-777777777777"), pack_id=pack_id,
                              symbol_revision_id=revision_id, published_page_id=page_id, sort_order=7, created_at=now))
    try:
        yield Session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _real_client(Session):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(USER_ID), email="requester@example.test", display_name="Requester",
        roles=("reviewer",), must_change_pin=False,
    )

    def override_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app, raise_server_exceptions=False)


def _real_persistence_snapshot(Session, runtime_dir: Path) -> dict:
    with Session() as session:
        key = session.get(CatalogApiKey, REAL_KEY_ID)
        revision = session.get(SymbolRevision, UUID("33333333-3333-4333-8333-333333333333"))
        page = session.get(PublishedPage, UUID("44444444-4444-4444-8444-444444444444"))
        pack = session.get(PublicationPack, UUID("55555555-5555-4555-8555-555555555555"))
        entry = session.get(PackEntry, UUID("77777777-7777-4777-8777-777777777777"))
        models = (CatalogApiUsageEvent, ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
        return {
            "last_used_at": key.last_used_at,
            "counts": {model.__name__: session.query(func.count(model.id)).scalar() for model in models},
            "usage": sorted(
                (
                    str(row.id), str(row.api_key_id), row.scope_used, row.method, row.path,
                    row.route_name, row.status_code, row.symbol_ref, row.result_count, row.ed_query_type,
                )
                for row in session.query(CatalogApiUsageEvent).all()
            ),
            "clarifications": sorted(
                (
                    str(row.id), str(row.symbol_id), str(row.published_page_id), row.source, row.kind,
                    row.status, str(row.catalog_api_key_id) if row.catalog_api_key_id else None,
                    row.context_json, row.detail,
                )
                for row in session.query(ClarificationRecord).all()
            ),
            "audits": sorted(
                (
                    str(row.id), row.entity_type, str(row.entity_id), row.action,
                    str(row.actor_id) if row.actor_id else None, row.payload_json,
                )
                for row in session.query(AuditEvent).all()
            ),
            "cases": sorted(
                (
                    str(row.id), row.source_entity_type, str(row.source_entity_id), row.current_stage,
                    str(row.owner_id) if row.owner_id else None, row.escalation_level, row.closed_at,
                )
                for row in session.query(ReviewCase).all()
            ),
            "actions": sorted(
                (
                    str(row.id), str(row.review_case_id), row.action_code, row.action_status,
                    str(row.assigned_to) if row.assigned_to else None, row.target_agent_slug,
                    row.target_stage, row.created_by_type,
                    str(row.created_by_id) if row.created_by_id else None, row.action_payload_json,
                )
                for row in session.query(ReviewCaseAction).all()
            ),
            "queue": sorted(
                (
                    str(row.id), str(row.agent_id), row.source_type, str(row.source_id), row.status,
                    row.priority, row.payload_json, row.started_at, row.completed_at,
                )
                for row in session.query(AgentQueueItem).all()
            ),
            "publication_counts": {
                model.__name__: session.query(func.count(model.id)).scalar()
                for model in (SymbolRevision, PublishedPage, PublicationPack, PackEntry)
            },
            "publication": (
                str(revision.id), revision.lifecycle_state, revision.payload_json,
                str(page.id), str(page.current_symbol_revision_id), page.page_code,
                str(pack.id), pack.status, pack.audience, pack.pack_code,
                str(entry.id), str(entry.symbol_revision_id), str(entry.published_page_id), entry.sort_order,
            ),
            "runtime": sorted((str(path.relative_to(runtime_dir)), path.read_bytes())
                              for path in runtime_dir.rglob("*") if path.is_file()) if runtime_dir.exists() else [],
        }


def _assert_catalog_feedback_matches_generated_openapi(response, *, expected_status: int) -> None:
    document = catalog_openapi_document()
    operation = document["paths"]["/api/v1/catalog/symbols/{symbol_ref}/feedback"]["post"]
    documented = operation["responses"][str(expected_status)]
    schema_ref = documented["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref == "#/components/schemas/FeedbackResponse"
    schema = document["components"]["schemas"][schema_ref.rsplit("/", 1)[1]]
    body = response.json()
    assert response.status_code == expected_status
    assert set(body) == set(schema["required"]) == set(schema["properties"])
    assert body["status"] in schema["properties"]["status"]["enum"]
    assert body["workflowDeliveryState"] in schema["properties"]["workflowDeliveryState"]["enum"]
    assert body["mutatesPublishedState"] is schema["properties"]["mutatesPublishedState"]["const"]
    assert body["remainsPublished"] is schema["properties"]["remainsPublished"]["const"]
    assert set(body["symbol"]) == set(schema["properties"]["symbol"]["required"])


def test_feedback_service_has_no_publication_lifecycle_assignment() -> None:
    source = Path(published_feedback.__file__).read_text(encoding="utf-8")

    assert ".lifecycle_state =" not in source


def test_browser_command_requires_uuid_request_id_and_rejects_unknown_identity_fields() -> None:
    with pytest.raises(ValueError, match="requestId"):
        normalize_published_symbol_command_request(
            {"command": "comment", "symbolIds": [str(SYMBOL_ID)], "comment": "Check this."}
        )

    with pytest.raises(ValueError, match="unknown"):
        normalize_published_symbol_command_request(
            {
                "command": "comment",
                "symbolIds": [str(SYMBOL_ID)],
                "comment": "Check this.",
                "requestId": str(REQUEST_ID),
                "requester": {"id": str(USER_ID)},
            }
        )


def test_browser_target_is_canonical_sorted_lowercase_uuid_input_set() -> None:
    upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    lower = "11111111-1111-4111-8111-111111111111"
    normalized = normalize_published_symbol_command_request(
        {
            "command": "comment",
            "symbolIds": [upper, lower],
            "comment": "Check this.",
            "requestId": str(REQUEST_ID),
        }
    )

    assert normalized["symbol_ids"] == [lower, upper.lower()]
    with pytest.raises(ValueError, match="UUID"):
        normalize_published_symbol_command_request(
            {
                "command": "comment",
                "symbolIds": ["browser-slug"],
                "comment": "Check this.",
                "requestId": str(REQUEST_ID),
            }
        )


def test_idempotency_namespace_and_per_symbol_ids_follow_exact_contract() -> None:
    expected_namespace = uuid5(NAMESPACE_URL, "symgov/published-feedback-idempotency/v1")

    assert published_feedback.PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE == expected_namespace
    anchor_id = published_feedback.published_feedback_request_anchor_id(
        principal_type="user",
        principal_id=USER_ID,
        request_key=REQUEST_ID,
    )
    assert anchor_id == uuid5(expected_namespace, f"user:{USER_ID}:{REQUEST_ID}:request")
    assert published_feedback.published_feedback_symbol_id(anchor_id, SYMBOL_ID, "clarification") == uuid5(
        expected_namespace, f"{anchor_id}:{SYMBOL_ID}:clarification"
    )


def test_canonical_publication_target_is_stable_and_rejects_ambiguous_revisions() -> None:
    rows = [
        SimpleNamespace(
            symbol_id=SYMBOL_ID,
            symbol_revision_id=UUID("33333333-3333-4333-8333-333333333333"),
            revision_label="Rev A",
            page_id=UUID("55555555-5555-4555-8555-555555555555"),
            pack_code="z-pack",
            sort_order=None,
        ),
        SimpleNamespace(
            symbol_id=SYMBOL_ID,
            symbol_revision_id=UUID("33333333-3333-4333-8333-333333333333"),
            revision_label="Rev A",
            page_id=UUID("44444444-4444-4444-8444-444444444444"),
            pack_code="A-pack",
            sort_order=7,
        ),
    ]

    target = published_feedback.normalize_publication_target(list(reversed(rows)))

    assert target.canonical_page_id == rows[1].page_id
    assert target.revision_id == rows[0].symbol_revision_id
    assert target.snapshot == published_feedback.normalize_publication_target(rows).snapshot
    assert json.loads(json.dumps(target.snapshot, sort_keys=True)) == target.snapshot

    ambiguous = [SimpleNamespace(**{**vars(rows[1]), "symbol_revision_id": UUID(int=99)})]
    with pytest.raises(published_feedback.PublishedFeedbackConflict, match="ambiguous_published_revision"):
        published_feedback.normalize_publication_target([rows[0], *ambiguous])


def test_grouped_target_normalization_rejects_aliases_for_the_same_published_symbol() -> None:
    row = SimpleNamespace(
        symbol_id=SYMBOL_ID,
        symbol_revision_id=UUID(int=101),
        page_id=UUID(int=102),
        slug="pump",
        canonical_name="Pump",
        pack_code="0001",
        sort_order=12,
        payload_json={"package_display_id": "0001", "package_symbol_sequence": "12"},
    )

    with pytest.raises(published_feedback.PublishedFeedbackConflict, match="duplicate_published_symbol_target"):
        group_distinct_published_symbol_targets(["0001-12", "pump"], [row])


def test_catalog_authentication_can_be_read_only_for_feedback_transaction() -> None:
    key = SimpleNamespace(
        id=UUID(int=7),
        customer_name="Customer",
        integration_name="Integration",
        key_prefix="safe-prefix",
        key_hash="unused",
        scopes_json=["catalog.feedback.write"],
        status="active",
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
    )

    class Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return key

    class Session:
        def query(self, _model):
            return Query()

    context = authenticate_catalog_api_key(Session(), "token", update_last_used=False)

    assert context is not None
    assert key.last_used_at is None


def test_pause_gate_module_is_repository_owned() -> None:
    assert importlib.util.find_spec("symgov_backend.published_feedback_gate") is not None


class BrowserBoundarySession:
    def __init__(self, *, race=False):
        self.row = SimpleNamespace(
            symbol_id=SYMBOL_ID,
            catalog_symbol_id="S-000001",
            symbol_revision_id=UUID("33333333-3333-4333-8333-333333333333"),
            revision_label="Rev A",
            page_id=UUID("44444444-4444-4444-8444-444444444444"),
            slug="pump",
            canonical_name="Pump",
            pack_code="0001",
            sort_order=7,
            payload_json={"package_display_id": "0001", "package_symbol_sequence": 7},
        )
        self.added = []
        self.anchors = {}
        self.events = []
        self.operations = []
        self.rollbacks = 0
        self.commits = 0
        self.committed = []
        self.race = race
        self.race_raised = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.events.append(sql)
        self.operations.append(("execute", sql, params or {}))
        if "FROM published_pages" in sql:
            requested = {UUID(value) for value in (params or {}).get("symbol_ids", [])}
            return SimpleNamespace(all=lambda: [self.row] if SYMBOL_ID in requested else [])
        return SimpleNamespace(all=lambda: [])

    def get(self, model, key, **_kwargs):
        if model is AuditEvent:
            return self.anchors.get(key) or next(
                (item for item in self.added if isinstance(item, AuditEvent) and item.id == key), None
            )
        if model is AgentQueueItem:
            return next((item for item in self.added if isinstance(item, AgentQueueItem) and item.id == key), None)
        return None

    def query(self, model):
        value = (
            SimpleNamespace(id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))
            if model is User
            else SimpleNamespace(id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"))
            if model is AgentDefinition
            else None
        )
        return SimpleNamespace(
            filter=lambda *_args, **_kwargs: SimpleNamespace(one_or_none=lambda: value),
            filter_by=lambda *_args, **_kwargs: SimpleNamespace(
                filter=lambda *_args, **_kwargs: SimpleNamespace(one_or_none=lambda: value),
                one_or_none=lambda: value,
            ),
        )

    def add(self, value):
        self.operations.append(("add", value.__class__.__name__, {}))
        if self.race and isinstance(value, AuditEvent) and value.entity_type == "published_feedback_request" and not self.race_raised:
            self.race_raised = True
            self.anchors[value.id] = value
            raise IntegrityError("request anchor primary key", {}, Exception("duplicate key"))
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1
        self.committed = list(self.added)
        for value in self.added:
            if isinstance(value, AuditEvent) and value.entity_type == "published_feedback_request":
                self.anchors[value.id] = value

    def rollback(self):
        self.rollbacks += 1
        self.added[:] = self.committed


def browser_client(session: BrowserBoundarySession, *, roles=("reviewer",)) -> TestClient:
    app = create_app()
    user = AuthenticatedUser(
        id=str(USER_ID),
        email="requester@example.test",
        display_name="Requester",
        roles=roles,
        must_change_pin=False,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/published/symbols/commands", "/api/published/symbols/commands"],
)
def test_real_browser_aliases_reject_same_key_disjoint_target_before_authority_lookup(
    path, tmp_path, monkeypatch
):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": "comment",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    accepted = client.post(path, json=body)
    assert accepted.status_code == 200
    session.events.clear()

    conflict = client.post(path, json={**body, "symbolIds": [str(UUID(int=999))]})

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_conflict"
    assert not any("FROM published_pages" in sql for sql in session.events)


def test_real_browser_boundary_recovers_request_anchor_primary_key_race(tmp_path, monkeypatch):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession(race=True)
    response = browser_client(session).post(
        "/api/v1/published/symbols/commands",
        json={
            "command": "comment",
            "symbolIds": [str(SYMBOL_ID)],
            "comment": "Check this.",
            "requestId": str(REQUEST_ID),
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["requestReplayed"] is True
    assert session.rollbacks == 1


def test_real_browser_boundary_locks_request_then_symbols_before_any_add(tmp_path, monkeypatch):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    response = browser_client(session).post(
        "/api/v1/published/symbols/commands",
        json={
            "command": "comment",
            "symbolIds": [str(SYMBOL_ID)],
            "comment": "Check this.",
            "requestId": str(REQUEST_ID),
        },
    )

    assert response.status_code == 200
    first_add = next(index for index, operation in enumerate(session.operations) if operation[0] == "add")
    before_add = session.operations[:first_add]
    assert [operation[0] for operation in before_add] == ["execute", "execute", "execute"]
    assert "pg_advisory_xact_lock" in before_add[0][1]
    assert "FROM published_pages" in before_add[1][1]
    assert "pg_advisory_xact_lock" in before_add[2][1]


def test_real_browser_boundary_creates_and_replays_review_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": "send_for_review",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }

    created = client.post("/api/v1/published/symbols/commands", json=body)
    replayed = client.post("/api/v1/published/symbols/commands", json=body)

    assert created.status_code == replayed.status_code == 200
    assert created.json()["items"][0]["reviewCaseId"]
    assert created.json()["items"][0]["edQueueItemId"]
    assert created.json()["items"][0]["remainsPublished"] is True
    assert replayed.json()["items"][0]["requestReplayed"] is True
    assert len([value for value in session.added if isinstance(value, ReviewCase)]) == 1


ACTOR_LIKE_KEYS = (
    "actorId", "submittedBy", "requester", "requestedBy", "createdBy",
    "deciderName", "deciderRole", "updatedBy", "managedBy",
)


@pytest.mark.parametrize("actor_key", ACTOR_LIKE_KEYS)
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_real_browser_boundary_rejects_complete_actor_spoof_matrix_before_lookup(
    actor_key, wrapped, nested
):
    session = BrowserBoundarySession()
    body = {
        "command": "comment",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
        actor_key: {actor_key: "spoof"} if nested else "spoof",
    }
    response = browser_client(session).post(
        "/api/v1/published/symbols/commands",
        json={"payload": body} if wrapped else body,
    )

    assert response.status_code == 422
    assert session.events == []
    assert session.added == []


@pytest.mark.parametrize("roles", [("user",), ("reviewer",), ("admin",)])
def test_real_browser_requester_matrix_attributes_session_user_not_ed(roles, tmp_path, monkeypatch):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    response = browser_client(session, roles=roles).post(
        "/api/v1/published/symbols/commands",
        json={
            "command": "comment",
            "symbolIds": [str(SYMBOL_ID)],
            "comment": "Check this.",
            "requestId": str(REQUEST_ID),
        },
    )

    assert response.status_code == 200
    clarification = next(value for value in session.added if isinstance(value, ClarificationRecord))
    request_anchor = next(
        value for value in session.added
        if isinstance(value, AuditEvent) and value.entity_type == "published_feedback_request"
    )
    assert clarification.submitted_by == USER_ID
    assert request_anchor.actor_id == USER_ID
    assert request_anchor.actor_id != UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")


@pytest.mark.parametrize("kind", ["comment", "send_for_review"])
def test_real_catalog_boundary_persists_key_attribution_and_safe_response(kind, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = catalog_fixture.FakeSession(
        key_row=catalog_fixture.api_key_row(), row=catalog_fixture.published_row()
    )
    response = catalog_fixture.build_client(session).post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind=kind),
        headers=catalog_fixture.auth_headers(),
    )

    assert response.status_code == 201
    assert response.json()["mutatesPublishedState"] is False
    clarification = catalog_fixture.records(session, ClarificationRecord)[0]
    anchor = next(
        value for value in session.added
        if isinstance(value, AuditEvent) and value.entity_type == "published_feedback_request"
    )
    assert clarification.catalog_api_key_id == catalog_fixture.KEY_ID
    assert clarification.submitted_by is None
    assert anchor.actor_id is None
    assert catalog_fixture.records(session, catalog_fixture.CatalogApiUsageEvent)


PAUSED_BODY = {
    "error": "published_feedback_paused",
    "detail": "Published feedback and review requests are temporarily unavailable.",
    "retryable": True,
}

PAUSE_SNAPSHOT_MODELS = (
    CatalogApiUsageEvent,
    ClarificationRecord,
    AuditEvent,
    ReviewCase,
    ReviewCaseAction,
    AgentQueueItem,
)


def _runtime_file_snapshot(runtime_dir: Path) -> list[Path]:
    if not runtime_dir.exists():
        return []
    return sorted(path.relative_to(runtime_dir) for path in runtime_dir.rglob("*") if path.is_file())


@pytest.mark.parametrize(
    ("queue_status", "expected_state"),
    [
        *[(status, "pending") for status in sorted(ACTIVE_STATUSES - {"queued"})],
        *[(status, "pending") for status in sorted(WAITING_OPERATOR_STATUSES)],
        ("completed", "historical"),
        ("failed", "historical"),
    ],
)
@pytest.mark.parametrize("runtime_mirror", ["present", "missing"])
def test_replay_delivery_groups_are_truthful_and_non_mutating(
    queue_status, expected_state, runtime_mirror, tmp_path
):
    queue_id = UUID("77777777-7777-4777-8777-777777777777")
    runtime_path = tmp_path / f"{queue_id}.json"
    if runtime_mirror == "present":
        runtime_path.write_text('{"status":"stale-active-mirror"}\n', encoding="utf-8")
    queue_row = SimpleNamespace(id=queue_id, status=queue_status)
    materialize_calls = []
    runtime_before = _runtime_file_snapshot(tmp_path)

    delivery_state = published_feedback.replay_workflow_delivery_state(
        queue_row,
        tmp_path,
        materialize=lambda envelope: materialize_calls.append(envelope),
    )

    assert delivery_state == expected_state
    assert materialize_calls == []
    assert _runtime_file_snapshot(tmp_path) == runtime_before
    if runtime_mirror == "present":
        assert runtime_path.read_text(encoding="utf-8") == '{"status":"stale-active-mirror"}\n'


@pytest.mark.parametrize("queue_status", ["blocked", "cancelled", "mystery_status", ""])
def test_replay_delivery_unknown_or_unsupported_status_fails_closed(queue_status, tmp_path):
    queue_row = SimpleNamespace(
        id=UUID("77777777-7777-4777-8777-777777777777"),
        status=queue_status,
        source_type="published_symbol_review_request",
        source_id=SYMBOL_ID,
        payload_json={"task_type": "published_symbol_review_request", "symbol_id": str(SYMBOL_ID)},
    )

    with pytest.raises(published_feedback.PublishedFeedbackConflict) as exc_info:
        published_feedback.replay_workflow_delivery_state(queue_row, tmp_path)

    assert str(exc_info.value) == "published_feedback_workflow_integrity"
    assert _runtime_file_snapshot(tmp_path) == []


@pytest.mark.parametrize(
    ("queue_id", "queue_row"),
    [
        ("not-a-uuid", None),
        ("77777777-7777-4777-8777-777777777777", None),
        (
            "77777777-7777-4777-8777-777777777777",
            SimpleNamespace(
                id=UUID("77777777-7777-4777-8777-777777777777"),
                source_type="other_task",
                source_id=SYMBOL_ID,
                payload_json={"task_type": "other_task", "symbol_id": str(SYMBOL_ID)},
            ),
        ),
    ],
)
def test_replay_queue_anchor_missing_invalid_or_corrupt_linkage_fails_closed(
    queue_id, queue_row
):
    session = SimpleNamespace(get=lambda _model, _key: queue_row)
    request_anchor_id = UUID("66666666-6666-4666-8666-666666666666")

    with pytest.raises(published_feedback.PublishedFeedbackConflict) as exc_info:
        published_feedback.load_replay_queue_item(
            session,
            request_anchor_id=request_anchor_id,
            queue_item_id=queue_id,
            symbol_id=str(SYMBOL_ID),
        )

    assert str(exc_info.value) == "published_feedback_workflow_integrity"


def test_replay_queue_anchor_cannot_redirect_to_another_valid_same_symbol_queue_row():
    request_anchor_id = UUID("66666666-6666-4666-8666-666666666666")
    redirected_queue_id = UUID("77777777-7777-4777-8777-777777777777")
    redirected_queue_row = SimpleNamespace(
        id=redirected_queue_id,
        source_type="published_symbol_review_request",
        source_id=SYMBOL_ID,
        payload_json={
            "task_type": "published_symbol_review_request",
            "symbol_id": str(SYMBOL_ID),
        },
    )
    session = SimpleNamespace(get=lambda _model, _key: redirected_queue_row)

    with pytest.raises(published_feedback.PublishedFeedbackConflict) as exc_info:
        published_feedback.load_replay_queue_item(
            session,
            request_anchor_id=request_anchor_id,
            queue_item_id=redirected_queue_id,
            symbol_id=str(SYMBOL_ID),
        )

    assert str(exc_info.value) == "published_feedback_workflow_integrity"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/published/symbols/commands", "/api/published/symbols/commands"],
)
@pytest.mark.parametrize(
    ("queue_status", "expected_status", "expected_delivery"),
    [
        ("running", 202, "pending"),
        ("needs_review", 202, "pending"),
        ("completed", 200, "historical"),
        ("failed", 200, "historical"),
        ("mystery_status", 409, None),
        (None, 409, None),
        ("invalid_anchor", 409, None),
        ("corrupt_linkage", 409, None),
    ],
)
def test_browser_alias_replay_groups_have_zero_governance_and_runtime_mutation(
    path, queue_status, expected_status, expected_delivery, tmp_path, monkeypatch
):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": "send_for_review",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    created = client.post(path, json=body)
    assert created.status_code == 200
    queue_id = UUID(created.json()["items"][0]["edQueueItemId"])
    queue_row = session.get(AgentQueueItem, queue_id)
    assert queue_row is not None
    if queue_status is None:
        session.added.remove(queue_row)
        session.committed.remove(queue_row)
    elif queue_status == "invalid_anchor":
        anchor = next(
            row for row in session.added
            if isinstance(row, AuditEvent) and row.entity_type == "published_feedback_request"
        )
        anchor.payload_json["items"][0]["edQueueItemId"] = "not-a-uuid"
    elif queue_status == "corrupt_linkage":
        queue_row.source_type = "other_task"
    else:
        queue_row.status = queue_status
    if queue_status in {"running", "needs_review"}:
        (tmp_path / f"{queue_id}.json").unlink()
    models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {model: len([row for row in session.added if isinstance(row, model)]) for model in models}
    runtime_before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    commits_before = session.commits

    replay = client.post(path, json=body)

    assert replay.status_code == expected_status
    if expected_delivery is None:
        assert replay.json()["detail"] == "published_feedback_workflow_integrity"
    else:
        assert replay.json()["items"][0]["workflowDeliveryState"] == expected_delivery
    assert session.commits == commits_before
    assert counts_before == {
        model: len([row for row in session.added if isinstance(row, model)]) for model in models
    }
    assert runtime_before == {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "path",
    ["/api/v1/published/symbols/commands", "/api/published/symbols/commands"],
)
def test_browser_alias_replay_rejects_valid_same_symbol_queue_from_wrong_anchor(
    path, tmp_path, monkeypatch
):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": "send_for_review",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    created = client.post(path, json=body)
    assert created.status_code == 200
    original_queue = session.get(
        AgentQueueItem, UUID(created.json()["items"][0]["edQueueItemId"])
    )
    assert original_queue is not None
    anchor = next(
        row for row in session.added
        if isinstance(row, AuditEvent) and row.entity_type == "published_feedback_request"
    )
    wrong_queue_id = published_feedback.published_feedback_symbol_id(
        UUID("bbbbbbbb-2222-4222-8333-cccccccccccc"), SYMBOL_ID, "agent-queue"
    )
    wrong_queue = AgentQueueItem(
        id=wrong_queue_id,
        agent_id=original_queue.agent_id,
        source_type=original_queue.source_type,
        source_id=original_queue.source_id,
        status="completed",
        priority=original_queue.priority,
        payload_json=dict(original_queue.payload_json),
        confidence=original_queue.confidence,
        escalation_reason=original_queue.escalation_reason,
        created_at=original_queue.created_at,
        started_at=original_queue.started_at,
        completed_at=original_queue.completed_at,
    )
    session.added.append(wrong_queue)
    session.committed.append(wrong_queue)
    anchor.payload_json["items"][0]["edQueueItemId"] = str(wrong_queue_id)
    models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {model: len([row for row in session.added if isinstance(row, model)]) for model in models}
    runtime_before = _runtime_file_snapshot(tmp_path)
    commits_before = session.commits

    replay = client.post(path, json=body)

    assert replay.status_code == 409
    assert replay.json()["detail"] == "published_feedback_workflow_integrity"
    assert session.commits == commits_before
    assert counts_before == {
        model: len([row for row in session.added if isinstance(row, model)]) for model in models
    }
    assert _runtime_file_snapshot(tmp_path) == runtime_before


@pytest.mark.parametrize(
    ("queue_status", "expected_status", "expected_delivery"),
    [
        ("processing", 202, "pending"),
        ("escalated", 202, "pending"),
        ("completed", 201, "historical"),
        ("failed", 201, "historical"),
        ("mystery_status", 409, None),
        (None, 409, None),
        ("invalid_anchor", 409, None),
        ("corrupt_linkage", 409, None),
    ],
)
def test_catalog_replay_groups_have_zero_governance_and_runtime_mutation(
    queue_status, expected_status, expected_delivery, tmp_path, monkeypatch
):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = catalog_fixture.FakeSession(
        key_row=catalog_fixture.api_key_row(), row=catalog_fixture.published_row()
    )
    client = catalog_fixture.build_client(session)
    created = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind="send_for_review"),
        headers=catalog_fixture.auth_headers(),
    )
    assert created.status_code == 201
    queue_row = catalog_fixture.records(session, AgentQueueItem)[0]
    if queue_status is None:
        session.added.remove(queue_row)
        session.committed.remove(queue_row)
    elif queue_status == "invalid_anchor":
        anchor = next(
            row for row in session.added
            if isinstance(row, AuditEvent) and row.entity_type == "published_feedback_request"
        )
        anchor.payload_json["ed_queue_item_id"] = "not-a-uuid"
    elif queue_status == "corrupt_linkage":
        queue_row.source_type = "other_task"
    else:
        queue_row.status = queue_status
    if queue_status in {"processing", "escalated"}:
        (tmp_path / f"{queue_row.id}.json").unlink()
    governance_models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {
        model: len(catalog_fixture.records(session, model)) for model in governance_models
    }
    runtime_before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    replay = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind="send_for_review"),
        headers=catalog_fixture.auth_headers(),
    )

    assert replay.status_code == expected_status
    if expected_delivery is None:
        assert replay.json()["detail"] == "published_feedback_workflow_integrity"
    else:
        assert replay.json()["workflowDeliveryState"] == expected_delivery
    assert counts_before == {
        model: len(catalog_fixture.records(session, model)) for model in governance_models
    }
    assert runtime_before == {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }


def test_catalog_replay_rejects_valid_same_symbol_queue_from_wrong_anchor(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = catalog_fixture.FakeSession(
        key_row=catalog_fixture.api_key_row(), row=catalog_fixture.published_row()
    )
    client = catalog_fixture.build_client(session)
    created = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind="send_for_review"),
        headers=catalog_fixture.auth_headers(),
    )
    assert created.status_code == 201
    original_queue = catalog_fixture.records(session, AgentQueueItem)[0]
    anchor = next(
        row for row in session.added
        if isinstance(row, AuditEvent) and row.entity_type == "published_feedback_request"
    )
    wrong_queue_id = published_feedback.published_feedback_symbol_id(
        UUID("bbbbbbbb-2222-4222-8333-cccccccccccc"), original_queue.source_id, "agent-queue"
    )
    wrong_queue = AgentQueueItem(
        id=wrong_queue_id,
        agent_id=original_queue.agent_id,
        source_type=original_queue.source_type,
        source_id=original_queue.source_id,
        status="completed",
        priority=original_queue.priority,
        payload_json=dict(original_queue.payload_json),
        confidence=original_queue.confidence,
        escalation_reason=original_queue.escalation_reason,
        created_at=original_queue.created_at,
        started_at=original_queue.started_at,
        completed_at=original_queue.completed_at,
    )
    session.added.append(wrong_queue)
    session.committed.append(wrong_queue)
    anchor.payload_json["ed_queue_item_id"] = str(wrong_queue_id)
    governance_models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {
        model: len(catalog_fixture.records(session, model)) for model in governance_models
    }
    runtime_before = _runtime_file_snapshot(tmp_path)
    commit_phases_before = list(session.commit_phases)

    replay = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind="send_for_review"),
        headers=catalog_fixture.auth_headers(),
    )

    assert replay.status_code == 409
    assert replay.json()["detail"] == "published_feedback_workflow_integrity"
    assert session.commit_phases == commit_phases_before
    assert counts_before == {
        model: len(catalog_fixture.records(session, model)) for model in governance_models
    }
    assert _runtime_file_snapshot(tmp_path) == runtime_before


def _browser_pause_snapshot(session: BrowserBoundarySession, runtime_dir: Path) -> dict:
    return {
        "commits": session.commits,
        "model_counts": {
            model.__name__: len([value for value in session.added if isinstance(value, model)])
            for model in PAUSE_SNAPSHOT_MODELS
        },
        "publication": {
            "symbol_id": session.row.symbol_id,
            "revision_id": session.row.symbol_revision_id,
            "page_id": session.row.page_id,
            "payload": dict(session.row.payload_json),
        },
        "runtime_files": _runtime_file_snapshot(runtime_dir),
    }


def _catalog_pause_snapshot(
    session: catalog_fixture.FakeSession,
    key_row,
    publication_row,
    runtime_dir: Path,
) -> dict:
    return {
        "last_used_at": key_row.last_used_at,
        "commits": list(session.commit_phases),
        "model_counts": {
            model.__name__: len(catalog_fixture.records(session, model))
            for model in PAUSE_SNAPSHOT_MODELS
        },
        "publication": {
            "lifecycle_state": session.revision.lifecycle_state,
            "symbol_id": publication_row.symbol_id,
            "revision_id": publication_row.symbol_revision_id,
            "page_id": publication_row.page_id,
            "payload": dict(publication_row.payload_json),
        },
        "runtime_files": _runtime_file_snapshot(runtime_dir),
    }


@pytest.mark.parametrize(
    "path",
    ["/api/v1/published/symbols/commands", "/api/published/symbols/commands"],
)
@pytest.mark.parametrize("command", ["comment", "send_for_review"])
def test_live_pause_gate_precedes_browser_body_processing_and_all_mutation(
    path, command, tmp_path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    pause_file = tmp_path / "published-feedback.pause"
    monkeypatch.setenv("SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE", str(pause_file))
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", runtime_dir)
    original_request_json = published_routes.Request.json
    body_processing_calls = []

    async def recording_request_json(request):
        body_processing_calls.append(request)
        return await original_request_json(request)

    monkeypatch.setattr(published_routes.Request, "json", recording_request_json)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": command,
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    pause_file.write_text("paused\n", encoding="utf-8")
    paused_snapshot = _browser_pause_snapshot(session, runtime_dir)

    paused = client.post(path, json=body)

    assert paused.status_code == 503
    assert paused.json() == PAUSED_BODY
    assert paused.headers["Retry-After"] == "60"
    assert body_processing_calls == []
    assert _browser_pause_snapshot(session, runtime_dir) == paused_snapshot
    assert session.rollbacks == 0

    pause_file.unlink()
    resumed = client.post(path, json=body)

    assert resumed.status_code == 200
    assert len(body_processing_calls) == 1
    assert session.commits == 1
    assert len([value for value in session.added if isinstance(value, ClarificationRecord)]) == 1
    assert len([value for value in session.added if isinstance(value, AuditEvent)]) >= 2
    expected_work = 1 if command == "send_for_review" else 0
    assert len([value for value in session.added if isinstance(value, ReviewCase)]) == expected_work
    assert len([value for value in session.added if isinstance(value, ReviewCaseAction)]) == expected_work
    assert len([value for value in session.added if isinstance(value, AgentQueueItem)]) == expected_work
    assert len(list(runtime_dir.glob("*.json"))) == expected_work


@pytest.mark.parametrize("kind", ["comment", "send_for_review"])
def test_live_pause_gate_precedes_catalog_body_processing_key_usage_and_all_mutation(
    kind, tmp_path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    pause_file = tmp_path / "published-feedback.pause"
    monkeypatch.setenv("SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE", str(pause_file))
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", runtime_dir)
    original_read_body = catalog_routes._read_bounded_catalog_body
    body_processing_calls = []

    async def recording_read_body(request):
        body_processing_calls.append(request)
        return await original_read_body(request)

    monkeypatch.setattr(catalog_routes, "_read_bounded_catalog_body", recording_read_body)
    key_row = catalog_fixture.api_key_row()
    publication_row = catalog_fixture.published_row()
    session = catalog_fixture.FakeSession(key_row=key_row, row=publication_row)
    client = catalog_fixture.build_client(session)
    pause_file.write_text("paused\n", encoding="utf-8")
    paused_snapshot = _catalog_pause_snapshot(session, key_row, publication_row, runtime_dir)

    paused = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind=kind),
        headers=catalog_fixture.auth_headers(),
    )

    assert paused.status_code == 503
    assert paused.json() == PAUSED_BODY
    assert paused.headers["Retry-After"] == "60"
    assert body_processing_calls == []
    assert _catalog_pause_snapshot(session, key_row, publication_row, runtime_dir) == paused_snapshot
    assert session.rollbacks == 0

    pause_file.unlink()
    resumed = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=catalog_fixture.valid_body(kind=kind),
        headers=catalog_fixture.auth_headers(),
    )

    assert resumed.status_code == 201
    assert len(body_processing_calls) == 1
    assert key_row.last_used_at is not None
    assert session.commit_phases == ["authoritative", "usage"]
    assert len(catalog_fixture.records(session, ClarificationRecord)) == 1
    assert len(catalog_fixture.records(session, CatalogApiUsageEvent)) == 1
    expected_work = 1 if kind == "send_for_review" else 0
    assert len(catalog_fixture.records(session, ReviewCaseAction)) == expected_work
    assert len(catalog_fixture.records(session, AgentQueueItem)) == expected_work
    assert len(list(runtime_dir.glob("*.json"))) == expected_work


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
@pytest.mark.parametrize("runtime_mirror", ["missing", "archived"])
def test_browser_terminal_replay_is_historical_and_never_rematerialized(
    terminal_status, runtime_mirror, tmp_path, monkeypatch
):
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", tmp_path)
    session = BrowserBoundarySession()
    client = browser_client(session)
    body = {
        "command": "send_for_review",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    created = client.post("/api/v1/published/symbols/commands", json=body)
    queue_id = created.json()["items"][0]["edQueueItemId"]
    queue_row = session.get(AgentQueueItem, UUID(queue_id))
    queue_row.status = terminal_status
    runtime_path = tmp_path / f"{queue_id}.json"
    archived_path = tmp_path / "archived_agent_queue_items" / runtime_path.name
    if runtime_mirror == "archived":
        archived_path.parent.mkdir()
        runtime_path.replace(archived_path)
    else:
        runtime_path.unlink()
    runtime_snapshot = _runtime_file_snapshot(tmp_path)
    materialize_calls = []

    def reject_terminal_materialization(envelope):
        materialize_calls.append(envelope)
        raise AssertionError("terminal queue rows must never be materialized")

    monkeypatch.setattr(published_routes, "materialize_runtime_envelope", reject_terminal_materialization)
    models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {model: len([value for value in session.added if isinstance(value, model)]) for model in models}

    replay = client.post("/api/v1/published/symbols/commands", json=body)

    assert replay.status_code == 200
    assert replay.json()["status"] == "completed"
    assert replay.json()["items"][0]["workflowDeliveryState"] == "historical"
    assert replay.json()["items"][0]["requestReplayed"] is True
    assert queue_row.status == terminal_status
    assert not runtime_path.exists()
    assert _runtime_file_snapshot(tmp_path) == runtime_snapshot
    assert archived_path.exists() is (runtime_mirror == "archived")
    assert materialize_calls == []
    assert counts_before == {
        model: len([value for value in session.added if isinstance(value, model)]) for model in models
    }


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
@pytest.mark.parametrize("runtime_mirror", ["missing", "archived"])
def test_catalog_terminal_replay_is_historical_and_never_rematerialized(
    terminal_status, runtime_mirror, tmp_path, monkeypatch
):
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", tmp_path)
    session = catalog_fixture.FakeSession(
        key_row=catalog_fixture.api_key_row(), row=catalog_fixture.published_row()
    )
    client = catalog_fixture.build_client(session)
    body = catalog_fixture.valid_body(kind="send_for_review")
    created = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=body,
        headers=catalog_fixture.auth_headers(),
    )
    queue_row = catalog_fixture.records(session, AgentQueueItem)[0]
    queue_row.status = terminal_status
    runtime_path = tmp_path / f"{queue_row.id}.json"
    archived_path = tmp_path / "archived_agent_queue_items" / runtime_path.name
    if runtime_mirror == "archived":
        archived_path.parent.mkdir()
        runtime_path.replace(archived_path)
    else:
        runtime_path.unlink()
    runtime_snapshot = _runtime_file_snapshot(tmp_path)
    materialize_calls = []

    def reject_terminal_materialization(envelope):
        materialize_calls.append(envelope)
        raise AssertionError("terminal queue rows must never be materialized")

    monkeypatch.setattr(catalog_routes, "materialize_runtime_envelope", reject_terminal_materialization)
    models = (ClarificationRecord, AuditEvent, ReviewCase, ReviewCaseAction, AgentQueueItem)
    counts_before = {model: len(catalog_fixture.records(session, model)) for model in models}

    replay = client.post(
        "/api/v1/catalog/symbols/0002-32/feedback",
        json=body,
        headers=catalog_fixture.auth_headers(),
    )

    assert replay.status_code == 201
    assert replay.json()["status"] == "recorded"
    assert replay.json()["workflowDeliveryState"] == "historical"
    assert replay.json()["requestReplayed"] is True
    assert queue_row.status == terminal_status
    assert not runtime_path.exists()
    assert _runtime_file_snapshot(tmp_path) == runtime_snapshot
    assert archived_path.exists() is (runtime_mirror == "archived")
    assert materialize_calls == []
    assert counts_before == {model: len(catalog_fixture.records(session, model)) for model in models}


@pytest.mark.parametrize("catalog_terminal_status", ["completed", "failed"])
def test_real_postgres_routes_preserve_transactions_across_pause_resume_error_and_terminal_replay(
    catalog_terminal_status, real_postgres_session_factory, tmp_path, monkeypatch
):
    Session = real_postgres_session_factory
    runtime_dir = tmp_path / "runtime"
    pause_file = tmp_path / "published-feedback.pause"
    monkeypatch.setenv("SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE", str(pause_file))
    monkeypatch.setattr(published_routes, "ED_RUNTIME_QUEUE_DIR", runtime_dir)
    monkeypatch.setattr(catalog_routes, "CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR", runtime_dir)
    client = _real_client(Session)
    browser_body = {
        "command": "send_for_review",
        "symbolIds": [str(SYMBOL_ID)],
        "comment": "Check this.",
        "requestId": str(REQUEST_ID),
    }
    catalog_headers = {
        "Authorization": f"Bearer {REAL_KEY_TOKEN}",
        "Idempotency-Key": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "X-Symgov-Application": "F0.4 test",
    }
    catalog_body = {"kind": "send_for_review", "message": "Check this.", "context": {}}
    pause_file.write_text("paused\n", encoding="utf-8")
    paused_snapshot = _real_persistence_snapshot(Session, runtime_dir)

    for path in ("/api/v1/published/symbols/commands", "/api/published/symbols/commands"):
        response = client.post(path, json=browser_body)
        assert response.status_code == 503
        assert response.json() == PAUSED_BODY
        assert response.headers["Retry-After"] == "60"
        assert _real_persistence_snapshot(Session, runtime_dir) == paused_snapshot
    catalog_paused = client.post(
        "/api/v1/catalog/symbols/pump/feedback", json=catalog_body, headers=catalog_headers
    )
    assert catalog_paused.status_code == 503
    assert catalog_paused.json() == PAUSED_BODY
    pause_operation = catalog_openapi_document()["paths"]["/api/v1/catalog/symbols/{symbol_ref}/feedback"]["post"]
    pause_contract = pause_operation["responses"]["503"]
    assert pause_contract["headers"]["Retry-After"]["schema"]["const"] == catalog_paused.headers["Retry-After"]
    pause_schema_ref = pause_contract["content"]["application/json"]["schema"]["$ref"]
    pause_schema = catalog_openapi_document()["components"]["schemas"][pause_schema_ref.rsplit("/", 1)[1]]
    assert set(catalog_paused.json()) == set(pause_schema["required"]) == set(pause_schema["properties"])
    assert all(
        catalog_paused.json()[name] is definition["const"]
        if isinstance(definition["const"], bool)
        else catalog_paused.json()[name] == definition["const"]
        for name, definition in pause_schema["properties"].items()
    )
    assert _real_persistence_snapshot(Session, runtime_dir) == paused_snapshot

    pause_file.unlink()
    browser_created = client.post("/api/v1/published/symbols/commands", json=browser_body)
    assert browser_created.status_code == 200
    browser_queue_id = UUID(browser_created.json()["items"][0]["edQueueItemId"])
    browser_runtime = runtime_dir / f"{browser_queue_id}.json"
    with Session.begin() as session:
        session.get(AgentQueueItem, browser_queue_id, with_for_update=True).status = "completed"
    browser_runtime.unlink()
    browser_terminal_snapshot = _real_persistence_snapshot(Session, runtime_dir)

    browser_replay = client.post("/api/published/symbols/commands", json=browser_body)
    assert browser_replay.status_code == 200
    assert browser_replay.json()["items"][0]["workflowDeliveryState"] == "historical"
    assert _real_persistence_snapshot(Session, runtime_dir) == browser_terminal_snapshot

    catalog_created = client.post(
        "/api/v1/catalog/symbols/pump/feedback", json=catalog_body, headers=catalog_headers
    )
    _assert_catalog_feedback_matches_generated_openapi(catalog_created, expected_status=201)
    assert catalog_created.json()["requestReplayed"] is False
    assert catalog_created.json()["workflowDeliveryState"] == "materialized"
    with Session() as session:
        catalog_anchor = session.get(
            AuditEvent,
            published_feedback.published_feedback_request_anchor_id(
                principal_type="catalog_api_key",
                principal_id=REAL_KEY_ID,
                request_key=UUID(catalog_headers["Idempotency-Key"]),
            ),
        )
        catalog_queue_id = UUID(catalog_anchor.payload_json["ed_queue_item_id"])
    catalog_runtime = runtime_dir / f"{catalog_queue_id}.json"
    with Session.begin() as session:
        session.get(AgentQueueItem, catalog_queue_id, with_for_update=True).status = catalog_terminal_status
    catalog_runtime.unlink()
    catalog_terminal_snapshot = _real_persistence_snapshot(Session, runtime_dir)

    conflict = client.post(
        "/api/v1/catalog/symbols/pump/feedback",
        json={**catalog_body, "message": "Different request."},
        headers=catalog_headers,
    )
    assert conflict.status_code == 409
    assert _real_persistence_snapshot(Session, runtime_dir) == catalog_terminal_snapshot

    catalog_replay = client.post(
        "/api/v1/catalog/symbols/pump/feedback", json=catalog_body, headers=catalog_headers
    )
    _assert_catalog_feedback_matches_generated_openapi(catalog_replay, expected_status=201)
    assert catalog_replay.json()["requestReplayed"] is True
    assert catalog_replay.json()["workflowDeliveryState"] == "historical"
    replay_snapshot = _real_persistence_snapshot(Session, runtime_dir)
    assert replay_snapshot["publication"] == catalog_terminal_snapshot["publication"]
    assert replay_snapshot["queue"] == catalog_terminal_snapshot["queue"]
    assert replay_snapshot["runtime"] == catalog_terminal_snapshot["runtime"]
    for model_name in ("ClarificationRecord", "AuditEvent", "ReviewCase", "ReviewCaseAction", "AgentQueueItem"):
        assert replay_snapshot["counts"][model_name] == catalog_terminal_snapshot["counts"][model_name]
    assert replay_snapshot["counts"]["CatalogApiUsageEvent"] == (
        catalog_terminal_snapshot["counts"]["CatalogApiUsageEvent"] + 1
    )
    assert replay_snapshot["last_used_at"] >= catalog_terminal_snapshot["last_used_at"]
