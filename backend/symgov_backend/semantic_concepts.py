from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import SemanticConcept, SemanticConceptRevision

SEMANTIC_CONCEPT_CODE_PATTERN = re.compile(r"^SGC-[0-9]{8}$")
SEMANTIC_CONCEPT_CODE_MAX_SEQUENCE_VALUE = 99_999_999
SEMANTIC_CONCEPT_CODE_ALLOCATION_ATTEMPTS = 3
SEMANTIC_CONCEPT_CODE_UNIQUE_CONSTRAINT = "uq_semantic_concepts_concept_code"

SEMANTIC_CONCEPT_KINDS = frozenset(
    {
        "physical_equipment",
        "function",
        "property",
        "state",
        "action",
        "annotation",
        "connection",
        "safety_function",
        "other",
    }
)
SEMANTIC_CONCEPT_STATUSES = frozenset({"draft", "active", "deprecated", "withdrawn"})
SEMANTIC_CONCEPT_REVISION_STATES = frozenset(
    {"draft", "review", "approved", "published", "deprecated", "withdrawn"}
)

# Governed lifecycle for a concept revision. Only the states named in the
# specification are modelled; nothing here invents an additional workflow step.
SEMANTIC_CONCEPT_REVISION_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"review", "withdrawn"}),
    "review": frozenset({"approved", "draft", "withdrawn"}),
    "approved": frozenset({"published", "draft", "withdrawn"}),
    "published": frozenset({"deprecated", "withdrawn"}),
    "deprecated": frozenset({"withdrawn"}),
    "withdrawn": frozenset(),
}

# States that record a human verification decision on the revision itself.
_REVIEW_DECISION_STATES = frozenset({"approved", "published"})

REVISION_LABEL_MAX_LENGTH = 64
PREFERRED_NAME_MAX_LENGTH = 256
DEFINITION_MAX_LENGTH = 8000
NOTES_MAX_LENGTH = 4000
RATIONALE_MAX_LENGTH = 2000
ALIAS_MAX_LENGTH = 256
ALIASES_MAX_COUNT = 64


def _is_concept_code_unique_violation(error: IntegrityError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None) or getattr(
        original, "constraint_name", None
    )
    return (
        sqlstate == "23505"
        and constraint_name == SEMANTIC_CONCEPT_CODE_UNIQUE_CONSTRAINT
    )


def normalize_semantic_concept_code(value: object) -> str:
    """Return a concept code in canonical form."""
    if not isinstance(value, str):
        raise ValueError("semantic concept code must be a string")
    if value != value.strip():
        raise ValueError("semantic concept code must not contain surrounding whitespace")
    if not value.isascii():
        raise ValueError("semantic concept code must contain ASCII characters only")
    normalized_value = value.upper()
    if SEMANTIC_CONCEPT_CODE_PATTERN.fullmatch(normalized_value) is None:
        raise ValueError("semantic concept code has invalid grammar")
    return normalized_value


def format_allocated_semantic_concept_code(sequence_value: int) -> str:
    """Format an allocated PostgreSQL sequence value as a semantic concept code."""
    if isinstance(sequence_value, bool) or not isinstance(sequence_value, int):
        raise ValueError("semantic concept code sequence value must be an integer")
    if sequence_value <= 0:
        raise ValueError("semantic concept code sequence value must be positive")
    if sequence_value > SEMANTIC_CONCEPT_CODE_MAX_SEQUENCE_VALUE:
        raise ValueError(
            "semantic concept code sequence value exceeds the SGC-######## grammar"
        )
    return f"SGC-{sequence_value:08d}"


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _require_actor(value: object, label: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{label} must be a real UUID")
    return value


def _normalize_required_text(value: object, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{label} must not be empty")
    if len(normalized_value) > max_length:
        raise ValueError(f"{label} must not exceed {max_length} characters")
    return normalized_value


def _normalize_optional_text(value: object, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _normalize_required_text(value, label, max_length)


def normalize_semantic_concept_aliases(value: object) -> list[str]:
    """Return a de-duplicated alias list, preserving first-seen order."""
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("semantic concept aliases must be a list of strings")
    normalized_aliases: list[str] = []
    seen_aliases: set[str] = set()
    for alias in value:
        normalized_alias = _normalize_required_text(alias, "semantic concept alias", ALIAS_MAX_LENGTH)
        fold_key = normalized_alias.casefold()
        if fold_key in seen_aliases:
            continue
        seen_aliases.add(fold_key)
        normalized_aliases.append(normalized_alias)
    if len(normalized_aliases) > ALIASES_MAX_COUNT:
        raise ValueError(f"semantic concept aliases must not exceed {ALIASES_MAX_COUNT} entries")
    return normalized_aliases


def allocate_semantic_concept_code(session: Session, *, allocated_at: datetime) -> str:
    """Draw the next concept code from the global sequence.

    Uniqueness is enforced by uq_semantic_concepts_concept_code at insert time
    rather than by a pre-check here, so a concurrent writer or a backfilled code
    cannot slip through a check-then-act gap.
    """
    _require_aware_timestamp(allocated_at, "semantic concept code allocation time")
    sequence_value = session.execute(
        text("SELECT nextval('semantic_concept_code_seq')")
    ).scalar_one()
    return format_allocated_semantic_concept_code(sequence_value)


def create_semantic_concept(
    session: Session,
    *,
    concept_kind: str,
    preferred_name: str,
    definition: str,
    created_by_user_id: uuid.UUID,
    created_at: datetime,
    revision_label: str = "r1",
    aliases: object = None,
    notes: object = None,
    rationale: object = None,
) -> tuple[SemanticConcept, SemanticConceptRevision]:
    """Create a draft concept identity plus its first draft revision."""
    if concept_kind not in SEMANTIC_CONCEPT_KINDS:
        raise ValueError("invalid semantic concept kind")
    _require_aware_timestamp(created_at, "semantic concept creation time")
    _require_actor(created_by_user_id, "semantic concept author")

    normalized_label = _normalize_required_text(
        revision_label, "semantic concept revision label", REVISION_LABEL_MAX_LENGTH
    )
    normalized_name = _normalize_required_text(
        preferred_name, "semantic concept preferred name", PREFERRED_NAME_MAX_LENGTH
    )
    normalized_definition = _normalize_required_text(
        definition, "semantic concept definition", DEFINITION_MAX_LENGTH
    )
    normalized_notes = _normalize_optional_text(
        notes, "semantic concept notes", NOTES_MAX_LENGTH
    )
    normalized_rationale = _normalize_optional_text(
        rationale, "semantic concept rationale", RATIONALE_MAX_LENGTH
    )
    normalized_aliases = normalize_semantic_concept_aliases(aliases)

    # Session.begin_nested() performs a mandatory pre-savepoint flush. Establish
    # a clean boundary first so failures from unrelated pending work are never
    # mistaken for a candidate-code collision.
    session.flush()

    for attempt in range(SEMANTIC_CONCEPT_CODE_ALLOCATION_ATTEMPTS):
        concept_code = allocate_semantic_concept_code(session, allocated_at=created_at)
        concept = SemanticConcept(
            id=uuid.uuid4(),
            concept_code=concept_code,
            concept_kind=concept_kind,
            status="draft",
            current_revision_id=None,
            created_by_user_id=created_by_user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        revision = SemanticConceptRevision(
            id=uuid.uuid4(),
            concept_id=concept.id,
            revision_label=normalized_label,
            lifecycle_state="draft",
            preferred_name=normalized_name,
            definition=normalized_definition,
            aliases_json=normalized_aliases,
            notes=normalized_notes,
            rationale=normalized_rationale,
            author_id=created_by_user_id,
            created_at=created_at,
        )
        savepoint = session.begin_nested()
        try:
            with savepoint:
                session.add(concept)
                session.add(revision)
                session.flush()
        except IntegrityError as error:
            if (
                not _is_concept_code_unique_violation(error)
                or attempt + 1 == SEMANTIC_CONCEPT_CODE_ALLOCATION_ATTEMPTS
            ):
                raise
            continue
        return concept, revision

    raise RuntimeError("semantic concept code allocation attempts exhausted")


def add_semantic_concept_revision(
    session: Session,
    concept_id: uuid.UUID,
    *,
    revision_label: str,
    preferred_name: str,
    definition: str,
    author_id: uuid.UUID,
    created_at: datetime,
    aliases: object = None,
    notes: object = None,
    rationale: object = None,
) -> SemanticConceptRevision:
    """Add a new draft revision to an existing concept."""
    _require_aware_timestamp(created_at, "semantic concept revision time")
    _require_actor(author_id, "semantic concept revision author")

    normalized_label = _normalize_required_text(
        revision_label, "semantic concept revision label", REVISION_LABEL_MAX_LENGTH
    )
    normalized_name = _normalize_required_text(
        preferred_name, "semantic concept preferred name", PREFERRED_NAME_MAX_LENGTH
    )
    normalized_definition = _normalize_required_text(
        definition, "semantic concept definition", DEFINITION_MAX_LENGTH
    )
    normalized_notes = _normalize_optional_text(
        notes, "semantic concept notes", NOTES_MAX_LENGTH
    )
    normalized_rationale = _normalize_optional_text(
        rationale, "semantic concept rationale", RATIONALE_MAX_LENGTH
    )
    normalized_aliases = normalize_semantic_concept_aliases(aliases)

    concept = session.get(SemanticConcept, concept_id, with_for_update=True)
    if concept is None:
        raise LookupError(f"semantic concept not found: {concept_id}")
    if concept.status in {"deprecated", "withdrawn"}:
        raise ValueError(f"semantic concept is {concept.status} and accepts no new revisions")

    revision = SemanticConceptRevision(
        id=uuid.uuid4(),
        concept_id=concept.id,
        revision_label=normalized_label,
        lifecycle_state="draft",
        preferred_name=normalized_name,
        definition=normalized_definition,
        aliases_json=normalized_aliases,
        notes=normalized_notes,
        rationale=normalized_rationale,
        author_id=author_id,
        created_at=created_at,
    )
    session.add(revision)
    concept.updated_at = created_at
    return revision


def transition_semantic_concept_revision(
    session: Session,
    revision_id: uuid.UUID,
    *,
    target_state: str,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> SemanticConceptRevision:
    """Move a concept revision through its governed lifecycle.

    Publishing a revision repoints the concept's current_revision_id at it,
    marks the concept active, and deprecates the revision it supersedes, so a
    concept exposes exactly one published revision at a time.
    """
    if target_state not in SEMANTIC_CONCEPT_REVISION_STATES:
        raise ValueError("invalid semantic concept revision lifecycle state")
    _require_aware_timestamp(occurred_at, "semantic concept revision transition time")
    _require_actor(actor_id, "semantic concept revision reviewer")

    revision = session.get(SemanticConceptRevision, revision_id, with_for_update=True)
    if revision is None:
        raise LookupError(f"semantic concept revision not found: {revision_id}")

    current_state = revision.lifecycle_state
    if current_state not in SEMANTIC_CONCEPT_REVISION_TRANSITIONS:
        raise ValueError(f"unknown semantic concept revision state: {current_state}")
    if target_state not in SEMANTIC_CONCEPT_REVISION_TRANSITIONS[current_state]:
        raise ValueError(
            f"semantic concept revision cannot move from {current_state} to {target_state}"
        )

    concept = session.get(SemanticConcept, revision.concept_id, with_for_update=True)
    if concept is None:
        raise LookupError(f"semantic concept not found: {revision.concept_id}")

    revision.lifecycle_state = target_state
    if target_state in _REVIEW_DECISION_STATES:
        revision.reviewed_by_user_id = actor_id
        revision.reviewed_at = occurred_at

    if target_state == "published":
        superseded_id = concept.current_revision_id
        if superseded_id is not None and superseded_id != revision.id:
            superseded = session.get(
                SemanticConceptRevision, superseded_id, with_for_update=True
            )
            if superseded is not None and superseded.lifecycle_state == "published":
                superseded.lifecycle_state = "deprecated"
        concept.current_revision_id = revision.id
        concept.status = "active"
    elif concept.current_revision_id == revision.id and target_state in {
        "deprecated",
        "withdrawn",
    }:
        concept.current_revision_id = None
        concept.status = "deprecated" if target_state == "deprecated" else "withdrawn"

    concept.updated_at = occurred_at
    return revision


def get_semantic_concept_by_code(
    session: Session, concept_code: str
) -> SemanticConcept | None:
    """Read a concept identity by its stable SGC-######## code."""
    normalized_code = normalize_semantic_concept_code(concept_code)
    return session.execute(
        select(SemanticConcept).where(SemanticConcept.concept_code == normalized_code)
    ).scalar_one_or_none()


def list_semantic_concepts(
    session: Session,
    *,
    status: str | None = None,
    concept_kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SemanticConcept]:
    """List concept identities in stable concept-code order."""
    if status is not None and status not in SEMANTIC_CONCEPT_STATUSES:
        raise ValueError("invalid semantic concept status filter")
    if concept_kind is not None and concept_kind not in SEMANTIC_CONCEPT_KINDS:
        raise ValueError("invalid semantic concept kind filter")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 500:
        raise ValueError("semantic concept list limit must be between 1 and 500")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("semantic concept list offset must not be negative")

    query = select(SemanticConcept)
    if status is not None:
        query = query.where(SemanticConcept.status == status)
    if concept_kind is not None:
        query = query.where(SemanticConcept.concept_kind == concept_kind)
    query = query.order_by(SemanticConcept.concept_code).limit(limit).offset(offset)
    return list(session.execute(query).scalars())


def list_semantic_concept_revisions(
    session: Session, concept_id: uuid.UUID
) -> list[SemanticConceptRevision]:
    """List a concept's revisions oldest-first."""
    query = (
        select(SemanticConceptRevision)
        .where(SemanticConceptRevision.concept_id == concept_id)
        .order_by(SemanticConceptRevision.created_at, SemanticConceptRevision.revision_label)
    )
    return list(session.execute(query).scalars())
