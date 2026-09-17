"""Governed relationships between semantic concepts (SM-P1-03 WP3.2).

Specification section 7.3. It is the section 7 entity that reached no section
15.1 work package, which is why `classification_mapping.py` could only record
`parentEquipmentClass` as a gap. Decision D6 (2026-09-17) ruled that a P0
omission rather than a deferral, so the table and this service exist; the
*vocabulary* the relationships will point into -- CFIHOS's equipment classes --
still waits on SM-P2-02, so nothing here proposes a relationship on its own.

Three rules shape this module.

**A row is one directed assertion.** Section 7.3 ships both directions of each
pair (`broader`/`narrower`, `component_of`/`has_component`,
`function_of`/`has_function`), so proposing `broader(A, B)` neither creates nor
implies `narrower(B, A)`, and the second is not refused as a duplicate of the
first. Asserting the inverse is a separate act with its own review. Inferring
one from the other would put an assertion nobody reviewed into the record,
which principle P-07 forbids.

**Cycles are a review question, not a constraint.** A `broader` chain can close
a loop across several rows, and neither a check constraint nor a single
`INSERT` can see the whole graph. The one loop a single row can express -- a
concept related to itself -- is refused here and in storage;
`broader_concept_ids` walks with a visited set so a cycle that does get stored
cannot hang a reader.

**Verification follows the sibling packages.** A rejected relationship is
terminal, so a reviewer who changes their mind proposes afresh, and a
deterministic method may be verified with no named reviewer under section 8.4's
"explicit policy". Unlike SM-P0-02, -03 and -04 there is no succession rule:
nothing in section 7.3 makes two verified relationships of the same type
mutually exclusive, so no row is retired to make way for another.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import SemanticConcept, SemanticConceptRelationship

# Section 7.3's controlled vocabulary, entire. Both directions of each pair are
# stored values: see the module docstring on why neither is derived.
CONCEPT_RELATIONSHIP_TYPES = frozenset(
    {
        "broader",
        "narrower",
        "related",
        "component_of",
        "has_component",
        "function_of",
        "has_function",
        "equivalent_internal",
    }
)

CONCEPT_RELATIONSHIP_STATUSES = frozenset({"proposed", "verified", "rejected", "retired"})

# Section 7.9's vocabulary minus `legacy_backfill`. Section 12.1 phase M2
# backfills *classifications*; there is no relationship backfill, so carrying
# the value would create a vocabulary with no writer. A fourth method
# vocabulary in this model, for the same reason the third one exists: unifying
# them would be a specification change, not an implementation tidy-up.
CONCEPT_RELATIONSHIP_METHODS = frozenset({"manual", "source_mapping", "rule", "ai_assisted"})

# Governed review states for one relationship. A rejected relationship is
# terminal -- the shape SM-P0-02, -03 and -04 already use.
CONCEPT_RELATIONSHIP_TRANSITIONS: dict[str, frozenset[str]] = {
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

# A concept in one of these states can no longer take new relationships, at
# either end: asserting a new fact about a withdrawn meaning is not a thing
# review can approve.
_CLOSED_CONCEPT_STATUSES = frozenset({"withdrawn"})

# Guards `broader_concept_ids` against a stored cycle and against a pathological
# chain. Ancestry deeper than this is a modelling error worth surfacing, not a
# read to serve.
MAX_BROADER_DEPTH = 32


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


def normalize_relationship_confidence(value: object) -> Decimal | None:
    """Return a 0..1 confidence, or None.

    Confidence is a proposal signal only. Specification section 8.4 is explicit
    that it is not a governance state, so nothing in this module lets a
    confidence value decide whether a relationship is verified.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("concept relationship confidence must be a number")
    confidence = Decimal(str(value))
    if confidence < 0 or confidence > 1:
        raise ValueError("concept relationship confidence must be between 0 and 1")
    return confidence


def normalize_relationship_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("concept relationship evidence must be an object")
    return value


def _open_concept(session: Session, concept_id: uuid.UUID, end: str) -> SemanticConcept:
    concept = session.get(SemanticConcept, concept_id)
    if concept is None:
        raise LookupError(f"{end} semantic concept not found: {concept_id}")
    if concept.status in _CLOSED_CONCEPT_STATUSES:
        raise ValueError(
            f"{end} semantic concept is {concept.status} and accepts no new relationships"
        )
    return concept


def propose_concept_relationship(
    session: Session,
    *,
    source_concept_id: uuid.UUID,
    target_concept_id: uuid.UUID,
    relationship_type: str,
    method: str,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    confidence: object = None,
    evidence: object = None,
) -> SemanticConceptRelationship:
    """Record a proposed relationship from one concept to another.

    Every relationship starts as `proposed`, whatever its method or confidence:
    specification principle P-07 keeps machine proposals out of the public
    record until a review decision is taken. The assertion is directed, so the
    caller names which concept is the subject; nothing here asserts the inverse.
    """
    if relationship_type not in CONCEPT_RELATIONSHIP_TYPES:
        raise ValueError("invalid concept relationship type")
    if method not in CONCEPT_RELATIONSHIP_METHODS:
        raise ValueError("invalid concept relationship method")
    _require_aware_timestamp(proposed_at, "concept relationship proposal time")
    _require_optional_actor(proposed_by_user_id, "concept relationship proposer")
    normalized_confidence = normalize_relationship_confidence(confidence)
    normalized_evidence = normalize_relationship_evidence(evidence)

    if source_concept_id == target_concept_id:
        raise ValueError("a concept cannot hold a relationship to itself")

    _open_concept(session, source_concept_id, "source")
    _open_concept(session, target_concept_id, "target")

    relationship = SemanticConceptRelationship(
        id=uuid.uuid4(),
        source_concept_id=source_concept_id,
        target_concept_id=target_concept_id,
        relationship_type=relationship_type,
        status="proposed",
        method=method,
        confidence=normalized_confidence,
        evidence_json=normalized_evidence,
        proposed_by_user_id=proposed_by_user_id,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    session.add(relationship)
    return relationship


def transition_concept_relationship(
    session: Session,
    relationship_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None = None,
) -> SemanticConceptRelationship:
    """Take a review decision on one concept relationship.

    No row is retired to make way for this one. Section 7.3 makes no two
    relationships mutually exclusive -- a concept can legitimately be `broader`
    than several others -- so the succession rule SM-P0-02, -03 and -04 each
    carry has no counterpart here.
    """
    if target_status not in CONCEPT_RELATIONSHIP_STATUSES:
        raise ValueError("invalid concept relationship status")
    _require_aware_timestamp(occurred_at, "concept relationship decision time")
    _require_optional_actor(reviewed_by_user_id, "concept relationship reviewer")

    relationship = session.get(
        SemanticConceptRelationship, relationship_id, with_for_update=True
    )
    if relationship is None:
        raise LookupError(f"concept relationship not found: {relationship_id}")

    current_status = relationship.status
    if target_status not in CONCEPT_RELATIONSHIP_TRANSITIONS[current_status]:
        raise ValueError(
            f"concept relationship cannot move from {current_status} to {target_status}"
        )

    if target_status == "verified" and reviewed_by_user_id is None:
        if relationship.method not in _AUTO_VERIFIABLE_METHODS:
            raise ValueError(
                f"verifying a {relationship.method} concept relationship requires a reviewer"
            )

    relationship.status = target_status
    relationship.updated_at = occurred_at
    if target_status in _REVIEW_DECISION_STATES:
        relationship.reviewed_by_user_id = reviewed_by_user_id
        relationship.reviewed_at = occurred_at
    return relationship


def list_concept_relationships(
    session: Session,
    semantic_concept_id: uuid.UUID,
    *,
    direction: str = "outgoing",
    status: str | None = None,
    relationship_type: str | None = None,
) -> list[SemanticConceptRelationship]:
    """List the relationships touching one concept, oldest first.

    `direction` chooses which end the concept sits at: `outgoing` reads the
    assertions it is the subject of, `incoming` the assertions made about it,
    and `both` reads either end. Rows are returned exactly as stored -- an
    `incoming` row still names the other concept as its source.
    """
    if direction not in {"outgoing", "incoming", "both"}:
        raise ValueError("invalid concept relationship direction")
    if status is not None and status not in CONCEPT_RELATIONSHIP_STATUSES:
        raise ValueError("invalid concept relationship status filter")
    if relationship_type is not None and relationship_type not in CONCEPT_RELATIONSHIP_TYPES:
        raise ValueError("invalid concept relationship type filter")

    if direction == "outgoing":
        end = SemanticConceptRelationship.source_concept_id == semantic_concept_id
    elif direction == "incoming":
        end = SemanticConceptRelationship.target_concept_id == semantic_concept_id
    else:
        end = or_(
            SemanticConceptRelationship.source_concept_id == semantic_concept_id,
            SemanticConceptRelationship.target_concept_id == semantic_concept_id,
        )

    query = select(SemanticConceptRelationship).where(end)
    if status is not None:
        query = query.where(SemanticConceptRelationship.status == status)
    if relationship_type is not None:
        query = query.where(SemanticConceptRelationship.relationship_type == relationship_type)
    query = query.order_by(
        SemanticConceptRelationship.created_at, SemanticConceptRelationship.id
    )
    return list(session.execute(query).scalars())


def verified_broader_concept_ids(
    session: Session, semantic_concept_id: uuid.UUID
) -> list[uuid.UUID]:
    """Return the concepts this one is verified to be narrower than.

    One hop only, and `verified` only: this is the read a `parentEquipmentClass`
    display would use, and an unreviewed proposal must not reach it (principle
    P-07). More than one parent is legitimate -- nothing in section 7.3 makes
    `broader` single-valued.
    """
    return [
        relationship.target_concept_id
        for relationship in list_concept_relationships(
            session,
            semantic_concept_id,
            direction="outgoing",
            status="verified",
            relationship_type="broader",
        )
    ]


def broader_concept_ids(
    session: Session, semantic_concept_id: uuid.UUID, *, max_depth: int = MAX_BROADER_DEPTH
) -> list[uuid.UUID]:
    """Return the verified `broader` ancestry of a concept, nearest first.

    Breadth-first over verified `broader` edges, with a visited set and a depth
    ceiling: storage cannot refuse a cycle spread across several rows, so a
    reader that walks the graph has to survive one. The starting concept is
    never included, even if a cycle leads back to it.
    """
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")

    seen = {semantic_concept_id}
    ancestry: list[uuid.UUID] = []
    frontier = [semantic_concept_id]
    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: list[uuid.UUID] = []
        for concept_id in frontier:
            for parent_id in verified_broader_concept_ids(session, concept_id):
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                ancestry.append(parent_id)
                next_frontier.append(parent_id)
        frontier = next_frontier
    return ancestry


def find_concept_relationships_to(
    session: Session,
    target_concept_id: uuid.UUID,
    *,
    relationship_type: str | None = None,
    status: str | None = None,
) -> list[SemanticConceptRelationship]:
    """Find every assertion pointing at one concept.

    The reverse lookup `ix_semantic_concept_relationships_target_status` exists
    for: "what is a component of this?", "what is narrower than this?".
    """
    return list_concept_relationships(
        session,
        target_concept_id,
        direction="incoming",
        status=status,
        relationship_type=relationship_type,
    )
