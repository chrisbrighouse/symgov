"""Governed mappings from SymGov concepts into external scheme releases (SM-P0-03).

Two specification rules shape this module.

Section 7.5 / principle P-04: a DEXPI, CFIHOS or PCA identifier is a *mapping*,
never the concept's primary key. The identifier lives on the mapping row, so
SymGov identity survives an external scheme splitting, merging, renaming or
deprecating a class.

Section 16.2: no verified `exact` mapping may be created from string
similarity alone. `scheme_version_id NOT NULL` and the verified-exact check
constraints carry part of that in storage; the rest is here, because "what the
verifier actually relied on" is an argument to a decision, not a column value a
constraint can inspect.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .external_semantic_schemes import CLOSED_SCHEME_VERSION_STATUSES
from .models import (
    ConceptExternalReference,
    ExternalSemanticSchemeVersion,
    SemanticConcept,
)

EXTERNAL_MAPPING_TYPES = frozenset({"exact", "close", "broader", "narrower", "related"})
EXTERNAL_MAPPING_STATUSES = frozenset({"proposed", "verified", "rejected", "retired"})

# Deliberately *not* the same vocabulary as SymbolSemanticAssignment.method:
# specification section 7.5 names `imported` where section 7.9 names
# `source_mapping`. The two are left distinct until the specification owner
# says they mean the same thing.
EXTERNAL_MAPPING_METHODS = frozenset({"manual", "imported", "rule", "ai_assisted"})

# Governed review states for one mapping. A rejected mapping is terminal, so a
# reviewer who changes their mind proposes afresh rather than reopening the old
# assertion -- the same shape SM-P0-02 uses for semantic assignments.
EXTERNAL_MAPPING_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"verified", "rejected", "retired"}),
    "verified": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# What a verifier relied on. Section 8.4 permits "human or deterministic
# authoritative-source verification"; the third value exists so a
# similarity-driven decision can be recorded honestly and then refused for
# `exact`.
EXTERNAL_MAPPING_VERIFICATION_BASES = frozenset(
    {"human_review", "authoritative_source", "string_similarity"}
)

# Section 16.2: string similarity alone is never enough for `exact`.
BASES_INSUFFICIENT_FOR_EXACT = frozenset({"string_similarity"})

# Deterministic methods that may be auto-verified with no named reviewer, and
# then only on an authoritative-source basis (section 8.4's "explicit policy").
_AUTO_VERIFIABLE_METHODS = frozenset({"imported"})

# States that record a review decision, and therefore require reviewed_at.
_REVIEW_DECISION_STATES = frozenset({"verified", "rejected"})

# A concept in one of these states can no longer take new mappings.
_CLOSED_CONCEPT_STATUSES = frozenset({"withdrawn"})

EXTERNAL_IDENTIFIER_MAX_LENGTH = 512
EXTERNAL_LABEL_MAX_LENGTH = 512


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


def normalize_external_identifier(value: object) -> str:
    """Return an external identifier in stored form.

    Case is preserved: external schemes are the authority on their own
    identifiers, and folding case here would silently rewrite them.
    """
    if not isinstance(value, str):
        raise ValueError("external identifier must be a string")
    identifier = value.strip()
    if not identifier:
        raise ValueError("external identifier must not be empty")
    if len(identifier) > EXTERNAL_IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"external identifier must be at most {EXTERNAL_IDENTIFIER_MAX_LENGTH} characters"
        )
    return identifier


def normalize_external_label(value: object) -> str | None:
    """Return the label as observed in that release, or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("external label must be a string")
    label = value.strip()
    if not label:
        raise ValueError("external label must not be empty when given")
    if len(label) > EXTERNAL_LABEL_MAX_LENGTH:
        raise ValueError(f"external label must be at most {EXTERNAL_LABEL_MAX_LENGTH} characters")
    return label


def normalize_external_mapping_confidence(value: object) -> Decimal | None:
    """Return a 0..1 confidence, or None.

    Confidence is a proposal signal only. Specification section 8.4 is explicit
    that it is not a governance state, so nothing in this module lets a
    confidence value decide whether a mapping is verified.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("external mapping confidence must be a number")
    confidence = Decimal(str(value))
    if confidence < 0 or confidence > 1:
        raise ValueError("external mapping confidence must be between 0 and 1")
    return confidence


def normalize_external_mapping_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("external mapping evidence must be an object")
    return value


def propose_concept_external_reference(
    session: Session,
    *,
    semantic_concept_id: uuid.UUID,
    scheme_version_id: uuid.UUID,
    external_identifier: str,
    mapping_type: str,
    mapping_method: str,
    proposed_at: datetime,
    external_label: object = None,
    proposed_by_user_id: uuid.UUID | None = None,
    confidence: object = None,
    evidence: object = None,
) -> ConceptExternalReference:
    """Record a proposed mapping from a concept into one external release.

    Every mapping starts as `proposed`, whatever its method or confidence:
    specification principle P-07 keeps machine proposals out of the public
    record until a review decision is taken. `scheme_version_id` is required,
    which is section 16.2's "no external mapping without a scheme version" --
    the argument is positional in the sense that there is no code path that
    stores a mapping without one.
    """
    if mapping_type not in EXTERNAL_MAPPING_TYPES:
        raise ValueError("invalid external mapping type")
    if mapping_method not in EXTERNAL_MAPPING_METHODS:
        raise ValueError("invalid external mapping method")
    normalized_identifier = normalize_external_identifier(external_identifier)
    normalized_label = normalize_external_label(external_label)
    _require_aware_timestamp(proposed_at, "external mapping proposal time")
    _require_optional_actor(proposed_by_user_id, "external mapping proposer")
    normalized_confidence = normalize_external_mapping_confidence(confidence)
    normalized_evidence = normalize_external_mapping_evidence(evidence)

    concept = session.get(SemanticConcept, semantic_concept_id)
    if concept is None:
        raise LookupError(f"semantic concept not found: {semantic_concept_id}")
    if concept.status in _CLOSED_CONCEPT_STATUSES:
        raise ValueError(f"semantic concept is {concept.status} and accepts no new mappings")

    version = session.get(ExternalSemanticSchemeVersion, scheme_version_id)
    if version is None:
        raise LookupError(f"external scheme version not found: {scheme_version_id}")
    if version.status in CLOSED_SCHEME_VERSION_STATUSES:
        raise ValueError(f"external scheme version is {version.status} and accepts no new mappings")

    reference = ConceptExternalReference(
        id=uuid.uuid4(),
        semantic_concept_id=semantic_concept_id,
        scheme_version_id=scheme_version_id,
        external_identifier=normalized_identifier,
        external_label=normalized_label,
        mapping_type=mapping_type,
        mapping_status="proposed",
        mapping_method=mapping_method,
        confidence=normalized_confidence,
        evidence_json=normalized_evidence,
        proposed_by_user_id=proposed_by_user_id,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    session.add(reference)
    return reference


def transition_concept_external_reference(
    session: Session,
    reference_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID | None = None,
    verification_basis: str | None = None,
) -> ConceptExternalReference:
    """Take a review decision on one mapping.

    Verifying requires a `verification_basis`, and it is recorded in
    `evidence_json` so the reason a mapping is trusted outlives the reviewer.
    Section 16.2 is enforced here: an `exact` mapping cannot be verified on a
    `string_similarity` basis, however high its confidence.

    Verifying a new `exact` mapping retires whichever `exact` mapping was
    verified before it for the same concept and release, so the one-verified-
    exact rule is upheld by succession rather than by refusing the new decision
    -- the pattern SM-P0-02 already uses for primary semantic assignments.
    """
    if target_status not in EXTERNAL_MAPPING_STATUSES:
        raise ValueError("invalid external mapping status")
    _require_aware_timestamp(occurred_at, "external mapping decision time")
    _require_optional_actor(reviewed_by_user_id, "external mapping reviewer")

    reference = session.get(ConceptExternalReference, reference_id, with_for_update=True)
    if reference is None:
        raise LookupError(f"external mapping not found: {reference_id}")

    current_status = reference.mapping_status
    if target_status not in EXTERNAL_MAPPING_TRANSITIONS[current_status]:
        raise ValueError(
            f"external mapping cannot move from {current_status} to {target_status}"
        )

    if target_status == "verified":
        _check_verification(session, reference, reviewed_by_user_id, verification_basis)
        _retire_superseded_exact_mapping(session, reference, occurred_at)
        reference.evidence_json = {**reference.evidence_json, "verification_basis": verification_basis}
    elif verification_basis is not None:
        raise ValueError("a verification basis only applies to verifying a mapping")

    reference.mapping_status = target_status
    reference.updated_at = occurred_at
    if target_status in _REVIEW_DECISION_STATES:
        reference.reviewed_by_user_id = reviewed_by_user_id
        reference.reviewed_at = occurred_at
    return reference


def _check_verification(
    session: Session,
    reference: ConceptExternalReference,
    reviewed_by_user_id: uuid.UUID | None,
    verification_basis: str | None,
) -> None:
    if verification_basis not in EXTERNAL_MAPPING_VERIFICATION_BASES:
        raise ValueError("verifying an external mapping requires a valid verification basis")

    if reference.mapping_type == "exact" and verification_basis in BASES_INSUFFICIENT_FOR_EXACT:
        raise ValueError(
            "an exact external mapping cannot be verified from string similarity alone"
        )

    if reviewed_by_user_id is None:
        if reference.mapping_method not in _AUTO_VERIFIABLE_METHODS:
            raise ValueError(
                f"verifying a {reference.mapping_method} external mapping requires a reviewer"
            )
        if verification_basis != "authoritative_source":
            raise ValueError(
                "an unreviewed import may only be verified on an authoritative-source basis"
            )

    if reference.mapping_type == "exact" and not reference.evidence_json:
        raise ValueError("an exact external mapping cannot be verified without evidence")

    version = session.get(ExternalSemanticSchemeVersion, reference.scheme_version_id)
    if version is not None and version.status in CLOSED_SCHEME_VERSION_STATUSES:
        raise ValueError(
            f"external scheme version is {version.status} and its mappings cannot be verified"
        )


def _retire_superseded_exact_mapping(
    session: Session, reference: ConceptExternalReference, occurred_at: datetime
) -> None:
    if reference.mapping_type != "exact":
        return
    superseded = session.execute(
        select(ConceptExternalReference)
        .where(
            ConceptExternalReference.semantic_concept_id == reference.semantic_concept_id,
            ConceptExternalReference.scheme_version_id == reference.scheme_version_id,
            ConceptExternalReference.mapping_type == "exact",
            ConceptExternalReference.mapping_status == "verified",
            ConceptExternalReference.id != reference.id,
        )
        .with_for_update()
    ).scalars().all()
    for previous in superseded:
        previous.mapping_status = "retired"
        previous.updated_at = occurred_at
    if superseded:
        # The partial unique index is evaluated per statement, so the
        # retirement must reach the database before the successor claims it.
        session.flush()


def verified_exact_reference(
    session: Session, semantic_concept_id: uuid.UUID, scheme_version_id: uuid.UUID
) -> ConceptExternalReference | None:
    """Return the concept's verified `exact` mapping in one release, if any."""
    return session.execute(
        select(ConceptExternalReference).where(
            ConceptExternalReference.semantic_concept_id == semantic_concept_id,
            ConceptExternalReference.scheme_version_id == scheme_version_id,
            ConceptExternalReference.mapping_type == "exact",
            ConceptExternalReference.mapping_status == "verified",
        )
    ).scalar_one_or_none()


def list_concept_external_references(
    session: Session,
    semantic_concept_id: uuid.UUID,
    *,
    mapping_status: str | None = None,
    mapping_type: str | None = None,
) -> list[ConceptExternalReference]:
    """List a concept's external mappings, strongest type first, then oldest."""
    if mapping_status is not None and mapping_status not in EXTERNAL_MAPPING_STATUSES:
        raise ValueError("invalid external mapping status filter")
    if mapping_type is not None and mapping_type not in EXTERNAL_MAPPING_TYPES:
        raise ValueError("invalid external mapping type filter")

    query = select(ConceptExternalReference).where(
        ConceptExternalReference.semantic_concept_id == semantic_concept_id
    )
    if mapping_status is not None:
        query = query.where(ConceptExternalReference.mapping_status == mapping_status)
    if mapping_type is not None:
        query = query.where(ConceptExternalReference.mapping_type == mapping_type)
    query = query.order_by(
        # 'broader' | 'close' | 'exact' does not sort usefully by name.
        ConceptExternalReference.mapping_type != "exact",
        ConceptExternalReference.created_at,
    )
    return list(session.execute(query).scalars())


def find_references_by_external_identifier(
    session: Session,
    scheme_version_id: uuid.UUID,
    external_identifier: str,
    *,
    mapping_status: str | None = None,
) -> list[ConceptExternalReference]:
    """Find every concept mapped to one identifier in one release.

    This is the reverse lookup specification section 14.3 indexes
    `(scheme_version_id, external_identifier)` for. More than one concept may
    legitimately map to the same external class -- only the forward direction
    (one concept, one release, one verified `exact`) is constrained.
    """
    if mapping_status is not None and mapping_status not in EXTERNAL_MAPPING_STATUSES:
        raise ValueError("invalid external mapping status filter")
    query = select(ConceptExternalReference).where(
        ConceptExternalReference.scheme_version_id == scheme_version_id,
        ConceptExternalReference.external_identifier == normalize_external_identifier(external_identifier),
    )
    if mapping_status is not None:
        query = query.where(ConceptExternalReference.mapping_status == mapping_status)
    return list(session.execute(query.order_by(ConceptExternalReference.created_at)).scalars())
