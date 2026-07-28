from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import func
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session

from ..agent_queue_reconciliation import ACTIVE_STATUSES, WAITING_OPERATOR_STATUSES
from ..models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    ClarificationRecord,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
    User,
)
from ..published_catalog import published_symbol_display_id
from ..service_users import enforce_noninteractive_service_account, new_service_pin_hash


SYSTEM_ED_EMAIL = "ed@symgov.local"
SYSTEM_ED_NAME = "Ed"
DEFAULT_ED_RUNTIME_QUEUE_DIR = Path("/data/.openclaw/workspaces/ed/runtime/agent_queue_items")
PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "symgov/published-feedback-idempotency/v1",
)
PUBLISHED_FEEDBACK_HISTORICAL_QUEUE_STATUSES = frozenset({"completed", "failed"})
PUBLISHED_FEEDBACK_PENDING_QUEUE_STATUSES = frozenset(
    (ACTIVE_STATUSES - {"queued"}) | WAITING_OPERATOR_STATUSES
)


class PublishedFeedbackConflict(ValueError):
    """A fail-closed published-feedback authority conflict."""


@dataclass(frozen=True)
class PublicationTarget:
    symbol_id: uuid.UUID
    revision_id: uuid.UUID
    canonical_page_id: uuid.UUID
    snapshot: dict


@dataclass(frozen=True)
class RuntimeEnvelope:
    queue_item_id: uuid.UUID
    path: Path
    payload: dict


@dataclass(frozen=True)
class CatalogAuditAttribution:
    api_key_id: uuid.UUID
    key_prefix: str
    customer_name: str
    integration_name: str

    def as_payload(self) -> dict[str, str]:
        return {
            "id": str(self.api_key_id),
            "prefix": self.key_prefix,
            "customer": self.customer_name,
            "integration": self.integration_name,
        }


@dataclass(frozen=True)
class PublishedFeedbackResult:
    record: ClarificationRecord
    review_case: ReviewCase | None
    action: ReviewCaseAction | None
    queue_item: AgentQueueItem | None
    audit_event: AuditEvent
    runtime_envelope: RuntimeEnvelope | None = None


def published_feedback_request_anchor_id(
    *, principal_type: str, principal_id: uuid.UUID, request_key: uuid.UUID
) -> uuid.UUID:
    return uuid.uuid5(
        PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE,
        f"{principal_type}:{str(principal_id).lower()}:{str(request_key).lower()}:request",
    )


def published_feedback_symbol_id(
    request_anchor_id: uuid.UUID, symbol_id: uuid.UUID, purpose: str
) -> uuid.UUID:
    return uuid.uuid5(
        PUBLISHED_FEEDBACK_IDEMPOTENCY_NAMESPACE,
        f"{str(request_anchor_id).lower()}:{str(symbol_id).lower()}:{purpose}",
    )


def published_feedback_advisory_lock_id(label: str, value: uuid.UUID) -> int:
    return int.from_bytes(
        hashlib.sha256(label.encode("ascii") + value.bytes).digest()[:8],
        "big",
        signed=True,
    )


def canonical_request_fingerprint(value: dict) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_publication_target(rows) -> PublicationTarget:
    normalized_rows = list(rows)
    if not normalized_rows:
        raise PublishedFeedbackConflict("published_symbol_not_found")
    symbol_ids = {uuid.UUID(str(row.symbol_id)) for row in normalized_rows}
    revision_ids = {uuid.UUID(str(row.symbol_revision_id)) for row in normalized_rows}
    if len(symbol_ids) != 1:
        raise PublishedFeedbackConflict("ambiguous_published_symbol")
    if len(revision_ids) != 1:
        raise PublishedFeedbackConflict("ambiguous_published_revision")

    def row_key(row):
        sort_order = getattr(row, "sort_order", None)
        return (
            str(getattr(row, "pack_code", "")).casefold(),
            int(sort_order) if sort_order is not None else 2147483647,
            str(row.page_id).lower(),
        )

    ordered = sorted(normalized_rows, key=row_key)
    symbol_id = next(iter(symbol_ids))
    revision_id = next(iter(revision_ids))
    snapshot = {
        "symbol_id": str(symbol_id).lower(),
        "revision_id": str(revision_id).lower(),
        "revision_label": str(getattr(ordered[0], "revision_label", "") or ""),
        "placements": [
            {
                "page_id": str(uuid.UUID(str(row.page_id))).lower(),
                "pack_code": str(getattr(row, "pack_code", "")),
                "sort_order": (
                    int(getattr(row, "sort_order"))
                    if getattr(row, "sort_order", None) is not None
                    else 2147483647
                ),
            }
            for row in ordered
        ],
    }
    return PublicationTarget(
        symbol_id=symbol_id,
        revision_id=revision_id,
        canonical_page_id=uuid.UUID(str(ordered[0].page_id)),
        snapshot=snapshot,
    )


def materialize_runtime_envelope(envelope: RuntimeEnvelope) -> Path:
    envelope.path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = envelope.path.with_name(
        f".{envelope.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps(envelope.payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, envelope.path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return envelope.path


def runtime_envelope_for_queue_item(
    queue_item: AgentQueueItem, runtime_queue_dir: Path
) -> RuntimeEnvelope:
    created_at = queue_item.created_at or _utc_now()
    created_value = (
        created_at.isoformat().replace("+00:00", "Z")
        if hasattr(created_at, "isoformat")
        else str(created_at)
    )
    payload = {
        "id": str(queue_item.id),
        "agent_id": "ed",
        "source_type": queue_item.source_type,
        "source_id": str(queue_item.source_id),
        "status": queue_item.status,
        "priority": queue_item.priority,
        "payload_json": queue_item.payload_json,
        "confidence": queue_item.confidence,
        "escalation_reason": queue_item.escalation_reason,
        "created_at": created_value,
        "started_at": queue_item.started_at.isoformat() if queue_item.started_at else None,
        "completed_at": queue_item.completed_at.isoformat() if queue_item.completed_at else None,
    }
    return RuntimeEnvelope(
        queue_item_id=uuid.UUID(str(queue_item.id)),
        path=runtime_queue_dir / f"{queue_item.id}.json",
        payload=payload,
    )


def replay_workflow_delivery_state(
    queue_item: AgentQueueItem | None,
    runtime_queue_dir: Path,
    *,
    materialize=materialize_runtime_envelope,
) -> str:
    """Report truthful replay delivery without mutating non-queued work."""

    if queue_item is None:
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    if queue_item.status in PUBLISHED_FEEDBACK_HISTORICAL_QUEUE_STATUSES:
        return "historical"
    if queue_item.status in PUBLISHED_FEEDBACK_PENDING_QUEUE_STATUSES:
        return "pending"
    if queue_item.status != "queued":
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    runtime_path = runtime_queue_dir / f"{queue_item.id}.json"
    if not runtime_path.exists():
        try:
            materialize(runtime_envelope_for_queue_item(queue_item, runtime_queue_dir))
        except OSError:
            pass
    if runtime_path.is_file():
        return "materialized"
    return "pending"


def load_replay_queue_item(
    session: Session,
    *,
    request_anchor_id: object,
    queue_item_id: object,
    symbol_id: object,
) -> AgentQueueItem:
    """Load an anchor-linked Ed queue row, rejecting missing or corrupt linkage."""

    try:
        expected_anchor_id = uuid.UUID(str(request_anchor_id))
        expected_queue_id = uuid.UUID(str(queue_item_id))
        expected_symbol_id = uuid.UUID(str(symbol_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity") from exc
    if expected_queue_id != published_feedback_symbol_id(
        expected_anchor_id,
        expected_symbol_id,
        "agent-queue",
    ):
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    queue_item = session.get(AgentQueueItem, expected_queue_id)
    if queue_item is None or not isinstance(queue_item.payload_json, dict):
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    payload = queue_item.payload_json
    try:
        payload_symbol_id = uuid.UUID(str(payload.get("symbol_id")))
        queue_row_id = uuid.UUID(str(queue_item.id))
        queue_source_id = uuid.UUID(str(queue_item.source_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity") from exc
    if (
        queue_row_id != expected_queue_id
        or queue_item.source_type != "published_symbol_review_request"
        or queue_source_id != expected_symbol_id
        or payload.get("task_type") != "published_symbol_review_request"
        or payload_symbol_id != expected_symbol_id
    ):
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    return queue_item


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_or_create_ed_user(session: Session, *, now: datetime | None = None) -> User:
    user = session.query(User).filter(func.lower(User.email) == SYSTEM_ED_EMAIL).one_or_none()
    resolved_now = now or _utc_now()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=SYSTEM_ED_EMAIL,
            display_name=SYSTEM_ED_NAME,
            pin_hash=new_service_pin_hash(),
            pin_set_at=resolved_now,
            must_change_pin=False,
            is_active=False,
            created_at=resolved_now,
            updated_at=resolved_now,
        )
        session.add(user)
        session.flush()
    return enforce_noninteractive_service_account(session, user, now=resolved_now)


def load_ed_user_for_published_feedback(session: Session) -> User:
    """Load the configured Ed executor without creating or repairing authority data."""
    user = session.query(User).filter(func.lower(User.email) == SYSTEM_ED_EMAIL).one_or_none()
    if user is None:
        raise PublishedFeedbackConflict("ed_user_not_found")
    return user


def validate_published_feedback_review_case(
    session: Session,
    *,
    symbol_id: uuid.UUID,
    workflow_owner_id: uuid.UUID,
) -> ReviewCase | None:
    """Validate the open-case authority boundary before any feedback write."""
    try:
        review_case = (
            session.query(ReviewCase)
            .filter_by(source_entity_type="published_symbol", source_entity_id=symbol_id)
            .filter(ReviewCase.closed_at.is_(None))
            .one_or_none()
        )
    except MultipleResultsFound as exc:
        raise PublishedFeedbackConflict("duplicate_open_review_cases") from exc
    if review_case is not None and review_case.owner_id != workflow_owner_id:
        raise PublishedFeedbackConflict("review_case_owner_conflict")
    return review_case


def create_ed_queue_item(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    payload: dict,
    runtime_queue_dir: Path,
    priority: str = "medium",
    now: datetime | None = None,
    queue_item_id: uuid.UUID | None = None,
) -> tuple[AgentQueueItem | None, RuntimeEnvelope | None]:
    ed_definition = session.query(AgentDefinition).filter_by(slug="ed").one_or_none()
    if ed_definition is None:
        return None, None
    resolved_now = now or _utc_now()
    queue_item_id = queue_item_id or uuid.uuid4()
    queue_item = AgentQueueItem(
        id=queue_item_id,
        agent_id=ed_definition.id,
        source_type=source_type,
        source_id=source_id,
        status="queued",
        priority=priority,
        payload_json=payload,
        confidence=None,
        escalation_reason=None,
        created_at=resolved_now,
        started_at=None,
        completed_at=None,
    )
    session.add(queue_item)
    session.flush()

    runtime_payload = {
        "id": str(queue_item_id),
        "agent_id": "ed",
        "source_type": source_type,
        "source_id": str(source_id),
        "status": "queued",
        "priority": priority,
        "payload_json": payload,
        "confidence": None,
        "escalation_reason": None,
        "created_at": resolved_now.isoformat().replace("+00:00", "Z"),
        "started_at": None,
        "completed_at": None,
    }
    return queue_item, RuntimeEnvelope(
        queue_item_id=queue_item_id,
        path=runtime_queue_dir / f"{queue_item_id}.json",
        payload=runtime_payload,
    )


def submit_published_feedback(
    session: Session,
    *,
    row,
    source: str,
    kind: str,
    message: str,
    context_json: dict,
    audit_action: str,
    submitted_by: uuid.UUID | None = None,
    external_submitter_id: uuid.UUID | None = None,
    catalog_api_key_id: uuid.UUID | None = None,
    audit_actor_id: uuid.UUID | None = None,
    catalog_audit_attribution: CatalogAuditAttribution | None = None,
    request_review: bool | None = None,
    workflow_owner_id: uuid.UUID | None = None,
    runtime_queue_dir: Path = DEFAULT_ED_RUNTIME_QUEUE_DIR,
    now: datetime | None = None,
    request_anchor_id: uuid.UUID | None = None,
    publication_target: PublicationTarget | None = None,
    requester_payload: dict | None = None,
    validated_review_case: ReviewCase | None = None,
    review_case_validated: bool = False,
) -> PublishedFeedbackResult:
    """Create published feedback and its optional review workflow without committing."""
    submitters = (submitted_by, external_submitter_id, catalog_api_key_id)
    if sum(value is not None for value in submitters) != 1:
        raise ValueError("Exactly one submitter attribution is required.")

    resolved_now = now or _utc_now()
    symbol_id = uuid.UUID(str(row.symbol_id))
    target = publication_target or normalize_publication_target([row])
    page_id = target.canonical_page_id
    revision_id = target.revision_id
    display_id = published_symbol_display_id(row)
    should_request_review = kind == "review_request" if request_review is None else request_review

    review_case = None
    if should_request_review:
        if workflow_owner_id is None:
            workflow_owner_id = load_ed_user_for_published_feedback(session).id
        review_case = (
            validated_review_case
            if review_case_validated
            else validate_published_feedback_review_case(
                session,
                symbol_id=symbol_id,
                workflow_owner_id=workflow_owner_id,
            )
        )

    record = ClarificationRecord(
        id=(
            published_feedback_symbol_id(request_anchor_id, symbol_id, "clarification")
            if request_anchor_id is not None
            else uuid.uuid4()
        ),
        symbol_id=symbol_id,
        published_page_id=page_id,
        source=source,
        kind=kind,
        status="open",
        submitted_by=submitted_by,
        external_submitter_id=external_submitter_id,
        catalog_api_key_id=catalog_api_key_id,
        context_json={
            **dict(context_json),
            "publication_snapshot": target.snapshot,
            **({"request_anchor_id": str(request_anchor_id)} if request_anchor_id else {}),
        },
        detail=message,
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(record)
    session.flush()

    action = None
    queue_item = None
    runtime_envelope = None
    resolved_audit_actor_id = audit_actor_id
    if should_request_review:
        if review_case is None:
            review_case = ReviewCase(
                id=uuid.uuid4(),
                source_entity_type="published_symbol",
                source_entity_id=symbol_id,
                current_stage="ux_feedback_coordination",
                owner_id=workflow_owner_id,
                escalation_level="medium",
                opened_at=resolved_now,
                closed_at=None,
            )
            session.add(review_case)
            session.flush()
        action = ReviewCaseAction(
            id=(
                published_feedback_symbol_id(request_anchor_id, symbol_id, "review-action")
                if request_anchor_id is not None
                else uuid.uuid4()
            ),
            review_case_id=review_case.id,
            decision_id=None,
            action_code="published_symbol_returned_for_review",
            action_status="queued",
            assigned_to=workflow_owner_id,
            target_agent_slug="ed",
            target_stage="ux_feedback_coordination",
            action_payload_json={
                "comment": message,
                "symbol_slug": row.slug,
                "symbol_display_id": display_id,
                "display_name": display_id,
                "workspace_display_name": display_id,
                "published_display_id": display_id,
                "symbol_name": row.canonical_name,
                "published_page_id": str(page_id),
                "published_revision_id": str(revision_id),
                "publication_snapshot": target.snapshot,
                "managed_by": "ed",
                "requester": requester_payload,
                "workflow_executor": {"type": "agent", "slug": "ed", "user_id": str(workflow_owner_id)},
                "withdrawal_actor": None,
            },
            created_by_type="catalog_api_key" if catalog_api_key_id else "human",
            created_by_id=catalog_api_key_id or submitted_by,
            created_at=resolved_now,
            started_at=None,
            completed_at=None,
        )
        session.add(action)
        session.flush()

        queue_item_id = (
            published_feedback_symbol_id(request_anchor_id, symbol_id, "agent-queue")
            if request_anchor_id is not None
            else uuid.uuid4()
        )
        action.action_payload_json["ed_queue_item_id"] = str(queue_item_id)
        queue_item, runtime_envelope = create_ed_queue_item(
            session,
            source_type="published_symbol_review_request",
            source_id=symbol_id,
            payload={
                "task_type": "published_symbol_review_request",
                "review_case_id": str(review_case.id),
                "review_action_id": str(action.id),
                "symbol_id": str(symbol_id),
                "symbol_slug": row.slug,
                "symbol_name": row.canonical_name,
                "symbol_display_id": display_id,
                "display_name": display_id,
                "workspace_display_name": display_id,
                "published_display_id": display_id,
                "comment": message,
                "managed_by": "ed",
                "requester": requester_payload,
                "workflow_executor": {"type": "agent", "slug": "ed", "user_id": str(workflow_owner_id)},
                "withdrawal_actor": None,
                "published_revision_id": str(revision_id),
                "published_page_id": str(page_id),
                "publication_snapshot": target.snapshot,
                "next_stage": "classification_review",
            },
            priority="medium",
            runtime_queue_dir=runtime_queue_dir,
            now=resolved_now,
            queue_item_id=queue_item_id,
        )

    audit_payload = {
        "comment_id": str(record.id),
        "comment": message,
        "review_case_id": str(review_case.id) if review_case is not None else None,
        "queue_item_id": str(queue_item.id) if queue_item is not None else None,
        "managed_by": "ed",
        "request_anchor_id": str(request_anchor_id) if request_anchor_id else None,
        "publication_snapshot": target.snapshot,
        "publication_transition": None,
        "published_availability_changed": False,
        "requester": requester_payload,
        "workflow_executor": (
            {"type": "agent", "slug": "ed", "user_id": str(workflow_owner_id)}
            if should_request_review
            else None
        ),
        "withdrawal_actor": None,
    }
    if catalog_audit_attribution is not None:
        audit_payload["catalog_api_key"] = catalog_audit_attribution.as_payload()

    audit_event = AuditEvent(
        id=(
            published_feedback_symbol_id(request_anchor_id, symbol_id, "symbol-audit")
            if request_anchor_id is not None
            else uuid.uuid4()
        ),
        entity_type="published_symbol",
        entity_id=symbol_id,
        action=audit_action,
        actor_id=resolved_audit_actor_id,
        payload_json=audit_payload,
        created_at=resolved_now,
    )
    session.add(audit_event)
    session.flush()

    return PublishedFeedbackResult(
        record=record,
        review_case=review_case,
        action=action,
        queue_item=queue_item,
        audit_event=audit_event,
        runtime_envelope=runtime_envelope,
    )
