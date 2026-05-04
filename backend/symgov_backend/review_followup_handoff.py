from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .models import AgentDefinition, AgentQueueItem, AuditEvent, HumanReviewDecision, ReviewCase, ReviewCaseAction
from .publication_handoff import load_review_context, source_file_from_intake, text_value
from .runtime import coerce_uuid


LIBBY_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/libby/runtime")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def list_value(value: Any) -> list:
    return value if isinstance(value, list) else []


def classify_follow_up(decision_code: str, child_decisions: list[dict[str, Any]]) -> str:
    action_codes = {str(item.get("action") or "").strip() for item in child_decisions}
    details_text = " ".join(
        str(item.get(key) or "")
        for item in child_decisions
        for key in ("note", "details")
    ).lower()
    if decision_code in {"deleted", "reject"} or "deleted" in action_codes or "reject" in action_codes:
        return "deletion_or_rejection"
    if decision_code == "duplicate" or "duplicate" in action_codes:
        return "duplicate_resolution"
    if decision_code in {"rename_classify"} or "rename_classify" in action_codes:
        return "metadata_or_classification_update"
    if decision_code == "more_evidence":
        return "evidence_request"
    if decision_code == "defer":
        return "deferral"
    graphic_terms = (
        "graphic",
        "drawing",
        "image",
        "symbol shape",
        "crop",
        "line",
        "rotate",
        "resize",
        "redraw",
        "edit symbol",
        "text",
        "label",
        "lettering",
        "annotation",
        "remove text",
        "erase text",
    )
    if decision_code == "request_changes" and any(term in details_text for term in graphic_terms):
        return "graphic_change_triage"
    if any(term in details_text for term in graphic_terms):
        return "graphic_change_triage"
    return "review_follow_up"


def build_libby_followup_payload(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
) -> dict[str, Any]:
    context = load_review_context(session, review_case)
    intake = context["intake_record"]
    classification = context["classification_record"]
    decision_payload = decision.decision_payload_json or {}
    child_decisions = list_value(decision_payload.get("child_decisions"))
    follow_up_type = classify_follow_up(decision.decision_code, child_decisions)
    normalized_submission = intake.normalized_submission_json if intake is not None else {}

    return {
        "task_type": "review_decision_follow_up",
        "review_case_id": str(review_case.id),
        "review_decision_id": str(decision.id),
        "decision_code": decision.decision_code,
        "decision_summary": decision.decision_summary,
        "decision_note": decision.decision_note,
        "case_comment": decision_payload.get("case_comment") or "",
        "decider_name": decision.decider_name,
        "decider_role": decision.decider_role,
        "review_recorded_at": isoformat_utc(decision.created_at),
        "from_stage": decision.from_stage,
        "to_stage": decision.to_stage,
        "current_stage": review_case.current_stage,
        "source_entity_type": review_case.source_entity_type,
        "source_entity_id": str(review_case.source_entity_id),
        "intake_record_id": str(intake.id) if intake else None,
        "origin_object_key": text_value(
            classification.origin_object_key if classification else None,
            intake.raw_object_key if intake else None,
        ),
        "origin_file_name": source_file_from_intake(intake),
        "origin_batch_id": normalized_submission.get("submission_batch_id") if normalized_submission else None,
        "candidate_symbol_id": text_value(
            classification.symbol_key if classification else None,
            normalized_submission.get("candidate_symbol_id") if normalized_submission else None,
        ),
        "candidate_symbol_name": text_value(
            normalized_submission.get("candidate_title") if normalized_submission else None,
            classification.symbol_family if classification else None,
        ),
        "current_classification_id": str(classification.id) if classification else None,
        "classification": {
            "status": classification.classification_status if classification else None,
            "confidence": float(classification.confidence) if classification else None,
            "libby_approved": classification.libby_approved if classification else None,
            "discipline": classification.discipline if classification else None,
            "category": classification.category if classification else None,
            "format": classification.format if classification else None,
            "industry": classification.industry if classification else None,
            "symbol_family": classification.symbol_family if classification else None,
            "process_category": classification.process_category if classification else None,
            "parent_equipment_class": classification.parent_equipment_class if classification else None,
            "standards_source": classification.standards_source if classification else None,
            "library_provenance_class": classification.library_provenance_class if classification else None,
            "source_classification": classification.source_classification if classification else None,
            "aliases": list_value(classification.aliases_json if classification else None),
            "keywords": list_value(classification.search_terms_json if classification else None),
            "source_refs": list_value(classification.source_refs_json if classification else None),
            "summary": classification.review_summary if classification else None,
        },
        "child_decisions": child_decisions,
        "libby_follow_up_type": follow_up_type,
        "loop": {
            "origin": "daisy_review",
            "next_review_agent": "daisy",
            "graphic_change_agent": "vlad",
            "return_to_agent": "libby",
        },
    }


def execute_review_followup_handoff(
    session: Session,
    *,
    review_case_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> dict[str, Any]:
    action = (
        session.query(ReviewCaseAction)
        .filter(
            ReviewCaseAction.review_case_id == review_case_id,
            ReviewCaseAction.decision_id == decision_id,
            ReviewCaseAction.action_code == "route_review_follow_up_to_libby",
            ReviewCaseAction.target_agent_slug == "libby",
        )
        .order_by(ReviewCaseAction.created_at.desc())
        .first()
    )
    if action is None:
        return {"status": "skipped", "detail": "No Libby review follow-up action was found."}

    review_case = session.get(ReviewCase, review_case_id)
    decision = session.get(HumanReviewDecision, decision_id)
    libby_definition = session.query(AgentDefinition).filter_by(slug="libby").one_or_none()
    if review_case is None or decision is None or libby_definition is None:
        action.action_status = "failed"
        action.action_payload_json = {
            **(action.action_payload_json or {}),
            "error": "Missing review case, decision, or Libby agent definition.",
        }
        action.completed_at = utc_now()
        session.commit()
        return {"status": "failed", "detail": action.action_payload_json["error"]}

    now = utc_now()
    payload = build_libby_followup_payload(session, review_case=review_case, decision=decision)
    queue_id = f"aqi-libby-review-{str(decision.id)[:8]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    queue_item = {
        "id": queue_id,
        "agent_id": "libby",
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "status": "queued",
        "priority": "medium",
        "payload_json": payload,
        "confidence": None,
        "escalation_reason": None,
        "created_at": isoformat_utc(now),
        "started_at": None,
        "completed_at": None,
    }

    action.action_status = "completed"
    action.started_at = now
    action.completed_at = now
    action.action_payload_json = {
        **(action.action_payload_json or {}),
        "libby_queue_item_id": queue_id,
        "libby_follow_up_type": payload["libby_follow_up_type"],
    }

    db_queue_item = session.get(AgentQueueItem, coerce_uuid(queue_id))
    if db_queue_item is None:
        db_queue_item = AgentQueueItem(id=coerce_uuid(queue_id))
        session.add(db_queue_item)
    db_queue_item.agent_id = libby_definition.id
    db_queue_item.source_type = queue_item["source_type"]
    db_queue_item.source_id = coerce_uuid(queue_item["source_id"])
    db_queue_item.status = queue_item["status"]
    db_queue_item.priority = queue_item["priority"]
    db_queue_item.payload_json = payload
    db_queue_item.confidence = None
    db_queue_item.escalation_reason = None
    db_queue_item.created_at = now
    db_queue_item.started_at = None
    db_queue_item.completed_at = None

    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="review_case",
            entity_id=review_case.id,
            action="libby_review_followup_queued",
            actor_id=None,
            payload_json={
                "decision_id": str(decision.id),
                "review_case_action_id": str(action.id),
                "libby_queue_item_id": queue_id,
                "libby_follow_up_type": payload["libby_follow_up_type"],
            },
            created_at=now,
        )
    )
    session.commit()

    write_json(LIBBY_RUNTIME_ROOT / "agent_queue_items" / f"{queue_id}.json", queue_item)
    return {"status": "completed", "libby_queue_item_id": queue_id, "libby_follow_up_type": payload["libby_follow_up_type"]}
