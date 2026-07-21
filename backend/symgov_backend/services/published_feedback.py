from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

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


def create_ed_queue_item(
    session: Session,
    *,
    source_type: str,
    source_id: uuid.UUID,
    payload: dict,
    runtime_queue_dir: Path,
    priority: str = "medium",
    now: datetime | None = None,
) -> AgentQueueItem | None:
    ed_definition = session.query(AgentDefinition).filter_by(slug="ed").one_or_none()
    if ed_definition is None:
        return None
    resolved_now = now or _utc_now()
    queue_item_id = uuid.uuid4()
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
    runtime_queue_dir.mkdir(parents=True, exist_ok=True)
    (runtime_queue_dir / f"{queue_item_id}.json").write_text(
        json.dumps(runtime_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return queue_item


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
) -> PublishedFeedbackResult:
    """Create published feedback and its optional review workflow without committing."""
    submitters = (submitted_by, external_submitter_id, catalog_api_key_id)
    if sum(value is not None for value in submitters) != 1:
        raise ValueError("Exactly one submitter attribution is required.")

    resolved_now = now or _utc_now()
    symbol_id = uuid.UUID(str(row.symbol_id))
    page_id = uuid.UUID(str(row.page_id))
    revision_id = uuid.UUID(str(row.symbol_revision_id))
    display_id = published_symbol_display_id(row)
    should_request_review = kind == "review_request" if request_review is None else request_review

    record = ClarificationRecord(
        id=uuid.uuid4(),
        symbol_id=symbol_id,
        published_page_id=page_id,
        source=source,
        kind=kind,
        status="open",
        submitted_by=submitted_by,
        external_submitter_id=external_submitter_id,
        catalog_api_key_id=catalog_api_key_id,
        context_json=dict(context_json),
        detail=message,
        created_at=resolved_now,
        updated_at=resolved_now,
    )
    session.add(record)
    session.flush()

    review_case = None
    action = None
    queue_item = None
    resolved_audit_actor_id = audit_actor_id
    if should_request_review:
        if workflow_owner_id is None:
            ed_user = get_or_create_ed_user(session, now=resolved_now)
            workflow_owner_id = ed_user.id
        if resolved_audit_actor_id is None and catalog_api_key_id is None:
            resolved_audit_actor_id = workflow_owner_id

        current_revision = session.get(SymbolRevision, revision_id, with_for_update=True)
        if current_revision is not None:
            current_revision.lifecycle_state = "review"

        review_case = (
            session.query(ReviewCase)
            .filter_by(source_entity_type="published_symbol", source_entity_id=symbol_id)
            .filter(ReviewCase.closed_at.is_(None))
            .one_or_none()
        )
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
        else:
            review_case.current_stage = "ux_feedback_coordination"

        action = ReviewCaseAction(
            id=uuid.uuid4(),
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
                "managed_by": "ed",
            },
            created_by_type="system",
            created_by_id=workflow_owner_id,
            created_at=resolved_now,
            started_at=None,
            completed_at=None,
        )
        session.add(action)
        session.flush()

        queue_item = create_ed_queue_item(
            session,
            source_type="published_symbol_review_request",
            source_id=symbol_id,
            payload={
                "task_type": "published_symbol_review_request",
                "review_case_id": str(review_case.id),
                "symbol_id": str(symbol_id),
                "symbol_slug": row.slug,
                "symbol_name": row.canonical_name,
                "symbol_display_id": display_id,
                "display_name": display_id,
                "workspace_display_name": display_id,
                "published_display_id": display_id,
                "comment": message,
                "managed_by": "ed",
                "next_stage": "classification_review",
            },
            priority="medium",
            runtime_queue_dir=runtime_queue_dir,
            now=resolved_now,
        )

    audit_payload = {
        "comment_id": str(record.id),
        "comment": message,
        "review_case_id": str(review_case.id) if review_case is not None else None,
        "queue_item_id": str(queue_item.id) if queue_item is not None else None,
        "managed_by": "ed",
    }
    if catalog_audit_attribution is not None:
        audit_payload["catalog_api_key"] = catalog_audit_attribution.as_payload()

    audit_event = AuditEvent(
        id=uuid.uuid4(),
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
    )
