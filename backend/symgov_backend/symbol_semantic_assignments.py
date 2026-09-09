from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SemanticConcept, SymbolRevision, SymbolSemanticAssignment

SEMANTIC_ASSIGNMENT_ROLES = frozenset({"primary", "qualifier", "component"})
SEMANTIC_ASSIGNMENT_STATUSES = frozenset({"proposed", "verified", "rejected", "retired"})
SEMANTIC_ASSIGNMENT_METHODS = frozenset({"manual", "source_mapping", "rule", "ai_assisted"})

# Governed review states for one assignment. Only the states the specification
# names are modelled; a rejected assignment is terminal, so a reviewer who
# changes their mind proposes afresh rather than reopening the old assertion.
SEMANTIC_ASSIGNMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"verified", "rejected", "retired"}),
    "verified": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# States that record a review decision, and therefore require reviewed_at.
_REVIEW_DECISION_STATES = frozenset({"verified", "rejected"})

# A concept in one of these states can no longer take new assignments.
_CLOSED_CONCEPT_STATUSES = frozenset({"withdrawn"})


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _require_optional_actor(value: object, label: str) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{label} must be a real UUID")
    return value


def normalize_semantic_assignment_confidence(value: object) -> Decimal | None:
    """Return a 0..1 confidence, or None.

    Confidence is a proposal signal only. Specification section 8.4 is explicit
    that it is not a governance state, so nothing in this module lets a
    confidence value decide whether an assignment is verified.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("semantic assignment confidence must be a number")
    confidence = Decimal(str(value))
    if confidence < 0 or confidence > 1:
        raise ValueError("semantic assignment confidence must be between 0 and 1")
    return confidence


def normalize_semantic_assignment_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("semantic assignment evidence must be an object")
    return value


def propose_symbol_semantic_assignment(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    semantic_concept_id: uuid.UUID,
    assignment_role: str,
    method: str,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    confidence: object = None,
    evidence: object = None,
) -> SymbolSemanticAssignment:
    """Record a proposed assignment of engineering meaning to a symbol revision.

    Every assignment starts as `proposed`, whatever its method or confidence:
    specification principle P-07 keeps machine proposals out of the public
    record until a review decision is taken.
    """
    if assignment_role not in SEMANTIC_ASSIGNMENT_ROLES:
        raise ValueError("invalid semantic assignment role")
    if method not in SEMANTIC_ASSIGNMENT_METHODS:
        raise ValueError("invalid semantic assignment method")
    _require_aware_timestamp(proposed_at, "semantic assignment proposal time")
    _require_optional_actor(proposed_by_user_id, "semantic assignment proposer")
    normalized_confidence = normalize_semantic_assignment_confidence(confidence)
    normalized_evidence = normalize_semantic_assignment_evidence(evidence)

    revision = session.get(SymbolRevision, symbol_revision_id)
    if revision is None:
        raise LookupError(f"symbol revision not found: {symbol_revision_id}")
    concept = session.get(SemanticConcept, semantic_concept_id)
    if concept is None:
        raise LookupError(f"semantic concept not found: {semantic_concept_id}")
    if concept.status in _CLOSED_CONCEPT_STATUSES:
        raise ValueError(f"semantic concept is {concept.status} and accepts no new assignments")

    assignment = SymbolSemanticAssignment(
        id=uuid.uuid4(),
        symbol_revision_id=symbol_revision_id,
        semantic_concept_id=semantic_concept_id,
        assignment_role=assignment_role,
        status="proposed",
        method=method,
        confidence=normalized_confidence,
        evidence_json=normalized_evidence,
        proposed_by_user_id=proposed_by_user_id,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    session.add(assignment)
    return assignment


def transition_symbol_semantic_assignment(
    session: Session,
    assignment_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None = None,
) -> SymbolSemanticAssignment:
    """Take a review decision on one assignment.

    Verifying a primary assignment retires whichever primary was verified
    before it, so the one-verified-primary-per-revision rule in specification
    section 7.9 is upheld by succession rather than by refusing the new
    decision. `reviewed_by_user_id` may be None only for a deterministic
    method auto-verified under explicit policy (section 8.4).
    """
    if target_status not in SEMANTIC_ASSIGNMENT_STATUSES:
        raise ValueError("invalid semantic assignment status")
    _require_aware_timestamp(occurred_at, "semantic assignment decision time")
    _require_optional_actor(reviewed_by_user_id, "semantic assignment reviewer")

    assignment = session.get(SymbolSemanticAssignment, assignment_id, with_for_update=True)
    if assignment is None:
        raise LookupError(f"semantic assignment not found: {assignment_id}")

    current_status = assignment.status
    if target_status not in SEMANTIC_ASSIGNMENT_TRANSITIONS[current_status]:
        raise ValueError(
            f"semantic assignment cannot move from {current_status} to {target_status}"
        )
    if reviewed_by_user_id is None and target_status == "verified" and assignment.method not in {
        "source_mapping",
        "rule",
    }:
        raise ValueError(
            "verifying a manual or ai_assisted semantic assignment requires a reviewer"
        )

    if target_status == "verified" and assignment.assignment_role == "primary":
        superseded = session.execute(
            select(SymbolSemanticAssignment)
            .where(
                SymbolSemanticAssignment.symbol_revision_id == assignment.symbol_revision_id,
                SymbolSemanticAssignment.assignment_role == "primary",
                SymbolSemanticAssignment.status == "verified",
                SymbolSemanticAssignment.id != assignment.id,
            )
            .with_for_update()
        ).scalars().all()
        for previous in superseded:
            previous.status = "retired"
            previous.updated_at = occurred_at
        # The partial unique index is evaluated per statement, so the retirement
        # must reach the database before the new row claims verified primary.
        session.flush()

    assignment.status = target_status
    assignment.updated_at = occurred_at
    if target_status in _REVIEW_DECISION_STATES:
        assignment.reviewed_by_user_id = reviewed_by_user_id
        assignment.reviewed_at = occurred_at
    return assignment


def verified_primary_assignment(
    session: Session, symbol_revision_id: uuid.UUID
) -> SymbolSemanticAssignment | None:
    """Return the revision's verified primary assignment, if it has one.

    This is the check the P0 publication gate in specification section 9.2
    depends on.
    """
    return session.execute(
        select(SymbolSemanticAssignment).where(
            SymbolSemanticAssignment.symbol_revision_id == symbol_revision_id,
            SymbolSemanticAssignment.assignment_role == "primary",
            SymbolSemanticAssignment.status == "verified",
        )
    ).scalar_one_or_none()


def list_symbol_semantic_assignments(
    session: Session,
    symbol_revision_id: uuid.UUID,
    *,
    status: str | None = None,
    assignment_role: str | None = None,
) -> list[SymbolSemanticAssignment]:
    """List a revision's assignments, primary first, then oldest first."""
    if status is not None and status not in SEMANTIC_ASSIGNMENT_STATUSES:
        raise ValueError("invalid semantic assignment status filter")
    if assignment_role is not None and assignment_role not in SEMANTIC_ASSIGNMENT_ROLES:
        raise ValueError("invalid semantic assignment role filter")

    query = select(SymbolSemanticAssignment).where(
        SymbolSemanticAssignment.symbol_revision_id == symbol_revision_id
    )
    if status is not None:
        query = query.where(SymbolSemanticAssignment.status == status)
    if assignment_role is not None:
        query = query.where(SymbolSemanticAssignment.assignment_role == assignment_role)
    query = query.order_by(
        # 'component' | 'primary' | 'qualifier' does not sort usefully by name.
        SymbolSemanticAssignment.assignment_role != "primary",
        SymbolSemanticAssignment.created_at,
    )
    return list(session.execute(query).scalars())
