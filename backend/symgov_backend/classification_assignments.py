"""Governed classification assignments (SM-P0-04).

Two tables, deliberately not collapsed into one.

Specification section 7.7 is *meaning-oriented*: an equipment or function
family attaches to the semantic concept, so every symbol revision carrying
that concept inherits it. Section 7.8 is *representation-oriented*: an
engineering discipline, drawing application or graphical context attaches to
the exact graphic, because it is a property of the drawing convention rather
than of the meaning.

Three deviations from the specification's literal text are recorded here
rather than left implicit.

* Section 7.7 lists `primary | secondary | inherited | proposed` as
  `assignment_role`. `proposed` is a governance *status* everywhere else in
  this model (section 8.4, SM-P0-01, -02 and -03), so it is carried by
  `status` and the role vocabulary stops at `inherited`.
* Section 12.1 phase M2 adds `legacy_backfill` to section 7.9's method
  vocabulary. That makes three method vocabularies across the semantic model;
  they are not unified, because unifying them would be a specification change.
* `classification_scheme_id` is stored alongside `classification_node_id`.
  It is redundant with the node, and a composite foreign key keeps the two in
  agreement -- but it is what lets "at most one verified primary per scheme"
  be a partial unique index rather than a service-layer convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .classification_schemes import CLOSED_NODE_STATUSES
from .models import (
    ClassificationNode,
    ConceptClassificationAssignment,
    SemanticConcept,
    SymbolRevisionClassificationAssignment,
    SymbolRevision,
)

# Section 7.7, minus the `proposed` slip. `inherited` records a classification
# a concept takes from a broader concept rather than one asserted directly.
CONCEPT_CLASSIFICATION_ROLES = frozenset({"primary", "secondary", "inherited"})

# Section 7.8. A revision inherits nothing -- its concept does -- so there is
# no `inherited` role here.
SYMBOL_CLASSIFICATION_ROLES = frozenset({"primary", "secondary"})

CLASSIFICATION_ASSIGNMENT_STATUSES = frozenset({"proposed", "verified", "rejected", "retired"})

# Section 7.9's vocabulary plus section 12.1 phase M2's `legacy_backfill`.
# Deliberately distinct from `concept_external_references.mapping_method`,
# which names `imported` where this names `source_mapping`.
CLASSIFICATION_ASSIGNMENT_METHODS = frozenset(
    {"manual", "source_mapping", "rule", "ai_assisted", "legacy_backfill"}
)

# Section 12.3: existing published symbols must not be silently reclassified
# as verified. A backfilled row is never eligible for verification, and the
# `backfill_not_verified` check constraint says the same thing in storage.
BACKFILL_METHODS = frozenset({"legacy_backfill"})

# Governed review states for one assignment. A rejected assignment is
# terminal, so a reviewer who changes their mind proposes afresh -- the shape
# SM-P0-02 and SM-P0-03 already use.
CLASSIFICATION_ASSIGNMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"verified", "rejected", "retired"}),
    "verified": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# Deterministic methods that may be verified with no named reviewer, under
# section 8.4's "explicit policy". `ai_assisted` and `manual` always need one.
_AUTO_VERIFIABLE_METHODS = frozenset({"source_mapping", "rule"})

# States that record a review decision, and therefore require reviewed_at.
_REVIEW_DECISION_STATES = frozenset({"verified", "rejected"})

# A concept in one of these states can no longer take new classifications.
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


def normalize_classification_confidence(value: object) -> Decimal | None:
    """Return a 0..1 confidence, or None.

    Confidence is a proposal signal only. Specification section 8.4 is
    explicit that it is not a governance state, so nothing in this module lets
    a confidence value decide whether an assignment is verified.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("classification confidence must be a number")
    confidence = Decimal(str(value))
    if confidence < 0 or confidence > 1:
        raise ValueError("classification confidence must be between 0 and 1")
    return confidence


def normalize_classification_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("classification evidence must be an object")
    return value


def _validated_node(session: Session, classification_node_id: uuid.UUID) -> ClassificationNode:
    node = session.get(ClassificationNode, classification_node_id)
    if node is None:
        raise LookupError(f"classification node not found: {classification_node_id}")
    if node.status in CLOSED_NODE_STATUSES:
        raise ValueError(f"classification node is {node.status} and accepts no new assignments")
    return node


def _validated_proposal(
    *, assignment_role: str, roles: frozenset[str], method: str, proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None, confidence: object, evidence: object,
) -> tuple[Decimal | None, dict]:
    if assignment_role not in roles:
        raise ValueError("invalid classification assignment role")
    if method not in CLASSIFICATION_ASSIGNMENT_METHODS:
        raise ValueError("invalid classification assignment method")
    _require_aware_timestamp(proposed_at, "classification proposal time")
    _require_optional_actor(proposed_by_user_id, "classification proposer")
    return (
        normalize_classification_confidence(confidence),
        normalize_classification_evidence(evidence),
    )


def propose_concept_classification(
    session: Session,
    *,
    semantic_concept_id: uuid.UUID,
    classification_node_id: uuid.UUID,
    assignment_role: str,
    method: str,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    confidence: object = None,
    evidence: object = None,
) -> ConceptClassificationAssignment:
    """Record a proposed meaning-oriented classification of a concept.

    Every assignment starts as `proposed`, whatever its method or confidence:
    specification principle P-07 keeps machine proposals out of the public
    record until a review decision is taken. The scheme is read from the node
    rather than accepted from the caller, so the two can never disagree.
    """
    normalized_confidence, normalized_evidence = _validated_proposal(
        assignment_role=assignment_role,
        roles=CONCEPT_CLASSIFICATION_ROLES,
        method=method,
        proposed_at=proposed_at,
        proposed_by_user_id=proposed_by_user_id,
        confidence=confidence,
        evidence=evidence,
    )

    concept = session.get(SemanticConcept, semantic_concept_id)
    if concept is None:
        raise LookupError(f"semantic concept not found: {semantic_concept_id}")
    if concept.status in _CLOSED_CONCEPT_STATUSES:
        raise ValueError(f"semantic concept is {concept.status} and accepts no new classifications")
    node = _validated_node(session, classification_node_id)

    assignment = ConceptClassificationAssignment(
        id=uuid.uuid4(),
        semantic_concept_id=semantic_concept_id,
        classification_node_id=node.id,
        classification_scheme_id=node.scheme_id,
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


def propose_symbol_revision_classification(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    classification_node_id: uuid.UUID,
    assignment_role: str,
    method: str,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    confidence: object = None,
    evidence: object = None,
) -> SymbolRevisionClassificationAssignment:
    """Record a proposed representation-oriented classification of a revision."""
    normalized_confidence, normalized_evidence = _validated_proposal(
        assignment_role=assignment_role,
        roles=SYMBOL_CLASSIFICATION_ROLES,
        method=method,
        proposed_at=proposed_at,
        proposed_by_user_id=proposed_by_user_id,
        confidence=confidence,
        evidence=evidence,
    )

    revision = session.get(SymbolRevision, symbol_revision_id)
    if revision is None:
        raise LookupError(f"symbol revision not found: {symbol_revision_id}")
    node = _validated_node(session, classification_node_id)

    assignment = SymbolRevisionClassificationAssignment(
        id=uuid.uuid4(),
        symbol_revision_id=symbol_revision_id,
        classification_node_id=node.id,
        classification_scheme_id=node.scheme_id,
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


def transition_concept_classification(
    session: Session,
    assignment_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None = None,
) -> ConceptClassificationAssignment:
    """Take a review decision on one concept classification.

    Verifying a primary assignment retires whichever primary was verified
    before it *in the same scheme*, so the one-verified-primary rule is upheld
    by succession rather than by refusing the new decision -- the pattern
    SM-P0-02 and SM-P0-03 already use.
    """
    return _transition(
        session,
        model=ConceptClassificationAssignment,
        target_column=ConceptClassificationAssignment.semantic_concept_id,
        assignment_id=assignment_id,
        target_status=target_status,
        occurred_at=occurred_at,
        reviewed_by_user_id=reviewed_by_user_id,
        label="concept classification",
    )


def transition_symbol_revision_classification(
    session: Session,
    assignment_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None = None,
) -> SymbolRevisionClassificationAssignment:
    """Take a review decision on one symbol revision classification."""
    return _transition(
        session,
        model=SymbolRevisionClassificationAssignment,
        target_column=SymbolRevisionClassificationAssignment.symbol_revision_id,
        assignment_id=assignment_id,
        target_status=target_status,
        occurred_at=occurred_at,
        reviewed_by_user_id=reviewed_by_user_id,
        label="symbol revision classification",
    )


def _transition(
    session: Session,
    *,
    model,
    target_column,
    assignment_id: uuid.UUID,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None,
    label: str,
):
    if target_status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid {label} status")
    _require_aware_timestamp(occurred_at, f"{label} decision time")
    _require_optional_actor(reviewed_by_user_id, f"{label} reviewer")

    assignment = session.get(model, assignment_id, with_for_update=True)
    if assignment is None:
        raise LookupError(f"{label} not found: {assignment_id}")

    current_status = assignment.status
    if target_status not in CLASSIFICATION_ASSIGNMENT_TRANSITIONS[current_status]:
        raise ValueError(f"{label} cannot move from {current_status} to {target_status}")

    if target_status == "verified":
        # Section 12.3: a backfilled classification is a proposal about legacy
        # data, and reviewing it means proposing afresh with a real method.
        if assignment.method in BACKFILL_METHODS:
            raise ValueError(
                f"a {assignment.method} {label} cannot be verified; propose it afresh"
            )
        if reviewed_by_user_id is None and assignment.method not in _AUTO_VERIFIABLE_METHODS:
            raise ValueError(f"verifying a {assignment.method} {label} requires a reviewer")
        if assignment.assignment_role == "primary":
            _retire_superseded_primary(
                session,
                model=model,
                target_column=target_column,
                assignment=assignment,
                occurred_at=occurred_at,
            )

    assignment.status = target_status
    assignment.updated_at = occurred_at
    if target_status in _REVIEW_DECISION_STATES:
        assignment.reviewed_by_user_id = reviewed_by_user_id
        assignment.reviewed_at = occurred_at
    return assignment


def _retire_superseded_primary(
    session: Session, *, model, target_column, assignment, occurred_at: datetime
) -> None:
    superseded = session.execute(
        select(model)
        .where(
            target_column == getattr(assignment, target_column.key),
            model.classification_scheme_id == assignment.classification_scheme_id,
            model.assignment_role == "primary",
            model.status == "verified",
            model.id != assignment.id,
        )
        .with_for_update()
    ).scalars().all()
    for previous in superseded:
        previous.status = "retired"
        previous.updated_at = occurred_at
    if superseded:
        # The partial unique index is evaluated per statement, so the
        # retirement must reach the database before the successor claims it.
        session.flush()


def verified_primary_concept_classification(
    session: Session, semantic_concept_id: uuid.UUID, classification_scheme_id: uuid.UUID
) -> ConceptClassificationAssignment | None:
    """Return a concept's verified primary node in one scheme, if it has one."""
    return session.execute(
        select(ConceptClassificationAssignment).where(
            ConceptClassificationAssignment.semantic_concept_id == semantic_concept_id,
            ConceptClassificationAssignment.classification_scheme_id == classification_scheme_id,
            ConceptClassificationAssignment.assignment_role == "primary",
            ConceptClassificationAssignment.status == "verified",
        )
    ).scalar_one_or_none()


def verified_primary_symbol_classification(
    session: Session, symbol_revision_id: uuid.UUID, classification_scheme_id: uuid.UUID
) -> SymbolRevisionClassificationAssignment | None:
    """Return a revision's verified primary node in one scheme, if it has one.

    This is the read the legacy `GovernedSymbol.category` / `.discipline`
    display values are derived from once SM-P0-09 dual-writes them
    (specification section 12.2).
    """
    return session.execute(
        select(SymbolRevisionClassificationAssignment).where(
            SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id,
            SymbolRevisionClassificationAssignment.classification_scheme_id == classification_scheme_id,
            SymbolRevisionClassificationAssignment.assignment_role == "primary",
            SymbolRevisionClassificationAssignment.status == "verified",
        )
    ).scalar_one_or_none()


def list_concept_classifications(
    session: Session,
    semantic_concept_id: uuid.UUID,
    *,
    status: str | None = None,
    assignment_role: str | None = None,
    classification_scheme_id: uuid.UUID | None = None,
) -> list[ConceptClassificationAssignment]:
    """List a concept's classifications, primary first, then oldest first."""
    return _list(
        session,
        model=ConceptClassificationAssignment,
        target_column=ConceptClassificationAssignment.semantic_concept_id,
        target_id=semantic_concept_id,
        roles=CONCEPT_CLASSIFICATION_ROLES,
        status=status,
        assignment_role=assignment_role,
        classification_scheme_id=classification_scheme_id,
    )


def list_symbol_revision_classifications(
    session: Session,
    symbol_revision_id: uuid.UUID,
    *,
    status: str | None = None,
    assignment_role: str | None = None,
    classification_scheme_id: uuid.UUID | None = None,
) -> list[SymbolRevisionClassificationAssignment]:
    """List a revision's classifications, primary first, then oldest first."""
    return _list(
        session,
        model=SymbolRevisionClassificationAssignment,
        target_column=SymbolRevisionClassificationAssignment.symbol_revision_id,
        target_id=symbol_revision_id,
        roles=SYMBOL_CLASSIFICATION_ROLES,
        status=status,
        assignment_role=assignment_role,
        classification_scheme_id=classification_scheme_id,
    )


def _list(
    session: Session,
    *,
    model,
    target_column,
    target_id: uuid.UUID,
    roles: frozenset[str],
    status: str | None,
    assignment_role: str | None,
    classification_scheme_id: uuid.UUID | None,
):
    if status is not None and status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError("invalid classification status filter")
    if assignment_role is not None and assignment_role not in roles:
        raise ValueError("invalid classification role filter")

    query = select(model).where(target_column == target_id)
    if status is not None:
        query = query.where(model.status == status)
    if assignment_role is not None:
        query = query.where(model.assignment_role == assignment_role)
    if classification_scheme_id is not None:
        query = query.where(model.classification_scheme_id == classification_scheme_id)
    query = query.order_by(
        # 'inherited' | 'primary' | 'secondary' does not sort usefully by name.
        model.assignment_role != "primary",
        model.created_at,
    )
    return list(session.execute(query).scalars())


def find_classified_targets(
    session: Session,
    classification_node_id: uuid.UUID,
    *,
    status: str | None = None,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Return the concepts and symbol revisions classified under one node.

    This is the reverse lookup specification section 14.3 indexes
    `classification_node_id` for, and the read a catalogue facet browse uses.
    """
    if status is not None and status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError("invalid classification status filter")

    concept_query = select(ConceptClassificationAssignment.semantic_concept_id).where(
        ConceptClassificationAssignment.classification_node_id == classification_node_id
    )
    revision_query = select(SymbolRevisionClassificationAssignment.symbol_revision_id).where(
        SymbolRevisionClassificationAssignment.classification_node_id == classification_node_id
    )
    if status is not None:
        concept_query = concept_query.where(ConceptClassificationAssignment.status == status)
        revision_query = revision_query.where(
            SymbolRevisionClassificationAssignment.status == status
        )
    return (
        list(session.execute(concept_query).scalars()),
        list(session.execute(revision_query).scalars()),
    )
