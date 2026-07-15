from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from symgov_backend.models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    ClarificationRecord,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
    User,
)
from symgov_backend.services.published_feedback import (
    CatalogAuditAttribution,
    submit_published_feedback,
)


SYMBOL_ID = UUID("11111111-1111-1111-1111-111111111111")
REVISION_ID = UUID("22222222-2222-2222-2222-222222222222")
PAGE_ID = UUID("33333333-3333-3333-3333-333333333333")
ED_USER_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
ED_AGENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
CATALOG_KEY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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


class Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, *args, **kwargs):
        return self

    def one_or_none(self):
        return self.result


class FakeSession:
    def __init__(self, *, existing_case=None, with_agent=True):
        self.revision = SimpleNamespace(id=REVISION_ID, lifecycle_state="published")
        self.ed_user = SimpleNamespace(id=ED_USER_ID)
        self.existing_case = existing_case
        self.with_agent = with_agent
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.get_calls = []
        self.workflow_events = []

    def query(self, model):
        if model is User:
            return Query(self.ed_user)
        if model is AgentDefinition:
            return Query(SimpleNamespace(id=ED_AGENT_ID) if self.with_agent else None)
        if model is ReviewCase:
            self.workflow_events.append("case_lookup")
            return Query(self.existing_case)
        raise AssertionError(f"Unexpected query model: {model}")

    def get(self, model, key, *, with_for_update=False):
        self.get_calls.append((model, key, with_for_update))
        if model is SymbolRevision and key == REVISION_ID:
            self.workflow_events.append("revision_lock" if with_for_update else "revision_get")
            return self.revision
        return None

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1


def added(session, model):
    return [value for value in session.added if isinstance(value, model)]


def test_comment_creates_open_record_with_one_browser_submitter_without_committing(tmp_path: Path):
    session = FakeSession()

    result = submit_published_feedback(
        session,
        row=published_row(),
        source="published_symbol_command_menu",
        kind="comment",
        message="Please correct the designation.",
        context_json={},
        submitted_by=ED_USER_ID,
        audit_action="published_symbol_comment",
        audit_actor_id=ED_USER_ID,
        runtime_queue_dir=tmp_path,
    )

    assert result.record in added(session, ClarificationRecord)
    assert result.record.status == "open"
    assert result.record.source == "published_symbol_command_menu"
    assert result.record.kind == "comment"
    assert result.record.detail == "Please correct the designation."
    assert result.record.context_json == {}
    assert result.record.submitted_by == ED_USER_ID
    assert result.record.external_submitter_id is None
    assert result.record.catalog_api_key_id is None
    assert result.review_case is None
    assert result.action is None
    assert result.queue_item is None
    assert session.flushes >= 1
    assert session.commits == 0
    assert session.get_calls == []

    audit = added(session, AuditEvent)[0]
    assert audit.action == "published_symbol_comment"
    assert audit.actor_id == ED_USER_ID
    assert audit.payload_json == {
        "comment_id": str(result.record.id),
        "comment": "Please correct the designation.",
        "review_case_id": None,
        "queue_item_id": None,
        "managed_by": "ed",
    }


def test_catalog_comment_preserves_context_and_safe_api_key_attribution(tmp_path: Path):
    session = FakeSession()

    result = submit_published_feedback(
        session,
        row=published_row(),
        source="catalog_integration_api",
        kind="comment",
        message="The CAD block has the wrong insertion point.",
        context_json={"application": "AutoCAD", "projectRef": "bounded-ref"},
        catalog_api_key_id=CATALOG_KEY_ID,
        audit_action="published_symbol_comment",
        catalog_audit_attribution=CatalogAuditAttribution(
            api_key_id=CATALOG_KEY_ID,
            key_prefix="symgov_live_acme",
            customer_name="Acme Engineering",
            integration_name="AutoCAD pilot",
        ),
        runtime_queue_dir=tmp_path,
    )

    assert result.record.source == "catalog_integration_api"
    assert result.record.catalog_api_key_id == CATALOG_KEY_ID
    assert result.record.submitted_by is None
    assert result.record.external_submitter_id is None
    assert result.record.context_json == {"application": "AutoCAD", "projectRef": "bounded-ref"}

    audit = added(session, AuditEvent)[0]
    serialized = json.dumps(audit.payload_json)
    assert audit.actor_id is None
    assert audit.payload_json["catalog_api_key"] == {
        "id": str(CATALOG_KEY_ID),
        "prefix": "symgov_live_acme",
        "customer": "Acme Engineering",
        "integration": "AutoCAD pilot",
    }
    assert "raw" not in serialized.lower()
    assert "hash" not in serialized.lower()


def test_exactly_one_submitter_is_required(tmp_path: Path):
    session = FakeSession()

    with pytest.raises(ValueError, match="Exactly one submitter"):
        submit_published_feedback(
            session,
            row=published_row(),
            source="catalog_integration_api",
            kind="comment",
            message="Incorrect.",
            context_json={},
            submitted_by=ED_USER_ID,
            catalog_api_key_id=CATALOG_KEY_ID,
            audit_action="published_symbol_comment",
            runtime_queue_dir=tmp_path,
        )

    assert session.added == []
    assert session.commits == 0


def test_send_for_review_changes_revision_and_creates_human_readable_workflow(tmp_path: Path):
    session = FakeSession()

    result = submit_published_feedback(
        session,
        row=published_row(),
        source="catalog_integration_api",
        kind="review_request",
        message="Return this symbol for correction.",
        context_json={},
        catalog_api_key_id=CATALOG_KEY_ID,
        audit_action="published_symbol_send_for_review",
        catalog_audit_attribution=CatalogAuditAttribution(
            api_key_id=CATALOG_KEY_ID,
            key_prefix="symgov_live_acme",
            customer_name="Acme Engineering",
            integration_name="AutoCAD pilot",
        ),
        runtime_queue_dir=tmp_path,
    )

    assert session.revision.lifecycle_state == "review"
    assert session.get_calls == [(SymbolRevision, REVISION_ID, True)]
    assert session.workflow_events.index("revision_lock") < session.workflow_events.index("case_lookup")
    assert result.record.catalog_api_key_id == CATALOG_KEY_ID
    assert result.record.submitted_by is None
    assert result.record.external_submitter_id is None
    assert result.review_case in added(session, ReviewCase)
    assert result.review_case.current_stage == "ux_feedback_coordination"
    assert result.review_case.owner_id == ED_USER_ID

    assert result.action in added(session, ReviewCaseAction)
    assert result.action.action_payload_json["symbol_display_id"] == "0002-32"
    assert result.action.action_payload_json["display_name"] == "0002-32"
    assert result.action.action_payload_json["workspace_display_name"] == "0002-32"
    assert result.action.action_payload_json["published_display_id"] == "0002-32"
    assert result.action.action_payload_json["symbol_slug"] == "check-valve"
    assert result.action.assigned_to == ED_USER_ID
    assert result.action.created_by_id == ED_USER_ID

    assert result.audit_event.actor_id is None
    assert result.audit_event.payload_json["catalog_api_key"] == {
        "id": str(CATALOG_KEY_ID),
        "prefix": "symgov_live_acme",
        "customer": "Acme Engineering",
        "integration": "AutoCAD pilot",
    }

    assert result.queue_item in added(session, AgentQueueItem)
    assert result.queue_item.payload_json["symbol_display_id"] == "0002-32"
    assert result.queue_item.payload_json["display_name"] == "0002-32"
    assert result.queue_item.payload_json["workspace_display_name"] == "0002-32"
    assert result.queue_item.payload_json["published_display_id"] == "0002-32"
    assert result.queue_item.payload_json["next_stage"] == "classification_review"

    runtime_payload = json.loads((tmp_path / f"{result.queue_item.id}.json").read_text(encoding="utf-8"))
    assert runtime_payload["payload_json"]["symbol_display_id"] == "0002-32"
    assert session.commits == 0


def test_send_for_review_reuses_existing_open_case(tmp_path: Path):
    existing_case = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        current_stage="classification_review",
        owner_id=ED_USER_ID,
    )
    session = FakeSession(existing_case=existing_case)

    result = submit_published_feedback(
        session,
        row=published_row(),
        source="published_symbol_command_menu",
        kind="review_request",
        message="Review again.",
        context_json={},
        submitted_by=ED_USER_ID,
        audit_action="published_symbol_send_for_review",
        audit_actor_id=ED_USER_ID,
        runtime_queue_dir=tmp_path,
    )

    assert result.review_case is existing_case
    assert existing_case.current_stage == "ux_feedback_coordination"
    assert added(session, ReviewCase) == []
    assert len(added(session, ReviewCaseAction)) == 1
    assert len(added(session, AgentQueueItem)) == 1
