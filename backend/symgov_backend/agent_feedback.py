from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .models import AgentFeedbackEvent

PROPERTY_FEEDBACK_AGENTS: dict[str, tuple[str, ...]] = {
    "name": ("libby", "vlad"),
    "description": ("libby",),
    "category": ("libby",),
    "discipline": ("libby",),
    "format": ("libby",),
}

DUPLICATE_FEEDBACK_AGENTS = ("rupert", "libby")


def _changed(previous: Any, updated: Any) -> bool:
    return (previous or None) != (updated or None)


def build_symbol_property_feedback_events(
    *,
    source_entity_type: str,
    source_entity_id: UUID,
    previous: Mapping[str, Any],
    updated: Mapping[str, Any],
    reviewer_name: str | None,
    reviewer_role: str | None,
    reason: str | None,
    evidence: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build observational feedback events from human metadata corrections.

    These events are deliberately observational: they capture correction evidence
    for future agent improvement but do not mutate prompts, rules, or agent policy.
    """

    events: list[dict[str, Any]] = []
    for field, agent_slugs in PROPERTY_FEEDBACK_AGENTS.items():
        if not _changed(previous.get(field), updated.get(field)):
            continue
        for agent_slug in agent_slugs:
            events.append(
                {
                    "agent_slug": agent_slug,
                    "feedback_type": f"metadata_{field}_corrected",
                    "source_entity_type": source_entity_type,
                    "source_entity_id": source_entity_id,
                    "original_value": {"field": field, "value": previous.get(field)},
                    "corrected_value": {"field": field, "value": updated.get(field)},
                    "reason": reason,
                    "reviewer_name": reviewer_name,
                    "reviewer_role": reviewer_role,
                    "evidence_json": dict(evidence or {}),
                    "applied_to_rules_at": None,
                    "applied_to_prompt_version": None,
                }
            )
    return events


def build_duplicate_decision_feedback_events(
    *,
    split_item: Any,
    action_code: str,
    reviewer_name: str | None,
    reviewer_role: str | None,
    reason: str | None,
    evidence: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build Rupert+Libby feedback for human duplicate decisions.

    A duplicate confirmation teaches Libby about triage and Rupert about gate
    precision. A false-positive override teaches both agents that the graphical
    duplicate gate/triage was too conservative for this symbol.
    """

    normalized = (action_code or "").strip().lower().replace("-", "_")
    if normalized == "duplicate":
        feedback_type = "duplicate_confirmed"
        corrected_outcome = "duplicate_confirmed"
    elif normalized == "approve":
        feedback_type = "duplicate_false_positive"
        corrected_outcome = "false_duplicate"
    else:
        return []

    source_id = split_item.id if isinstance(split_item.id, UUID) else UUID(str(split_item.id))
    evidence_payload = {
        "proposed_symbol_id": getattr(split_item, "proposed_symbol_id", None),
        **dict(evidence or {}),
    }
    return [
        {
            "agent_slug": agent_slug,
            "feedback_type": feedback_type,
            "source_entity_type": "review_split_item",
            "source_entity_id": source_id,
            "original_value": {"duplicate_status": "duplicate_exception"},
            "corrected_value": {"duplicate_outcome": corrected_outcome, "action_code": normalized},
            "reason": reason,
            "reviewer_name": reviewer_name,
            "reviewer_role": reviewer_role,
            "evidence_json": evidence_payload,
            "applied_to_rules_at": None,
            "applied_to_prompt_version": None,
        }
        for agent_slug in DUPLICATE_FEEDBACK_AGENTS
    ]


def add_agent_feedback_events(
    session: Session,
    events: list[dict[str, Any]],
    *,
    created_at: datetime | None = None,
) -> list[AgentFeedbackEvent]:
    """Persist observational feedback events without applying any rule changes."""

    now = created_at or datetime.now(timezone.utc).replace(microsecond=0)
    rows: list[AgentFeedbackEvent] = []
    for event in events:
        row = AgentFeedbackEvent(
            agent_slug=event["agent_slug"],
            feedback_type=event["feedback_type"],
            source_entity_type=event["source_entity_type"],
            source_entity_id=event["source_entity_id"],
            original_value_json=event.get("original_value") or {},
            corrected_value_json=event.get("corrected_value") or {},
            reason=event.get("reason"),
            reviewer_name=event.get("reviewer_name"),
            reviewer_role=event.get("reviewer_role"),
            evidence_json=event.get("evidence_json") or {},
            applied_to_rules_at=event.get("applied_to_rules_at"),
            applied_to_prompt_version=event.get("applied_to_prompt_version"),
            created_at=now,
        )
        session.add(row)
        rows.append(row)
    return rows
