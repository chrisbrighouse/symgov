"""Cross-target read queries for the semantic review queue (SM-P1-01 WP1.1).

Specification section 15.2 row `SM-P1-01`, and the two section 16.1 criteria
it closes: "the review workflow can see proposed semantic/classification/
source assertions with evidence and status", and "no organisation-private
symbol existence is revealed by public semantic endpoints".

**Why this module exists at all.** Every governance act the review UI needs
already exists as a tested service function with a frozen vocabulary --
`propose_symbol_semantic_assignment`, `transition_symbol_revision_classification`,
`propose_concept_external_reference`, `transition_rights_record` and the rest.
What did not exist anywhere was a query that crosses targets.
`list_symbol_semantic_assignments` and `list_symbol_revision_classifications`
are per-revision, `list_concept_external_references` is per-concept,
`list_rights_records` is per-subject and `find_classified_targets` is
per-node. A review *queue* asks a question none of them answer: every open
proposal, newest first, across every symbol. This module is only that
question. It writes nothing and it decides nothing.

**Capabilities are computed here, not in the frontend.** The rule that
matters most is a database CHECK constraint, not a UI preference:
`ck_symbol_revision_classifications_backfill_not_verified` (section 12.3)
makes a `legacy_backfill` assignment permanently ineligible for
verification. It can only be rejected, and re-proposed with a real method.
Every one of the 132 rows SM-P0-10's backfill applied in production is that
case, so a queue that rendered a generic approve button would render one the
database refuses on the entire population it was built for. Deriving the
answer from `CLASSIFICATION_ASSIGNMENT_TRANSITIONS` and `BACKFILL_METHODS`
rather than restating it is what keeps this module from drifting away from
`classification_assignments._transition`, which is the code that actually
enforces it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .classification_assignments import (
    BACKFILL_METHODS,
    CLASSIFICATION_ASSIGNMENT_METHODS,
    CLASSIFICATION_ASSIGNMENT_STATUSES,
    CLASSIFICATION_ASSIGNMENT_TRANSITIONS,
)
from .concept_external_references import (
    EXTERNAL_MAPPING_METHODS,
    EXTERNAL_MAPPING_STATUSES,
    EXTERNAL_MAPPING_TRANSITIONS,
)
from .rights_provenance import (
    NON_APPROVING_DETERMINATION_METHODS,
    RIGHTS_DECISION_STATUSES,
    RIGHTS_DECISION_TRANSITIONS,
    RIGHTS_DETERMINATION_METHODS,
)
from .symbol_semantic_assignments import (
    SEMANTIC_ASSIGNMENT_METHODS,
    SEMANTIC_ASSIGNMENT_STATUSES,
    SEMANTIC_ASSIGNMENT_TRANSITIONS,
)
from .models import (
    ClassificationNode,
    ClassificationScheme,
    ConceptClassificationAssignment,
    ConceptExternalReference,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
    GovernedSymbol,
    RightsRecord,
    SemanticConcept,
    SemanticConceptRevision,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
)

# A queue page. Bounded rather than clamped: a caller asking for 5000 rows has
# a defect or a misunderstanding, and silently returning 200 hides both.
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 200

# The state a review queue is *for*. Every queue function defaults to it, and
# every one of the 132 rows SM-P0-10 backfilled in production is in it.
OPEN_STATUS = "proposed"


@dataclass(frozen=True)
class DecisionCapabilities:
    """Which governance decisions one queue row can actually take.

    `must_repropose` is not the negation of `can_verify`: a terminal row
    cannot be verified either, but there is nothing for a reviewer to do
    about that. It is true only where verification is barred *and* the row
    is otherwise live -- the backfill case, where the available action is to
    reject and propose afresh with a real method.
    """

    can_verify: bool
    can_reject: bool
    can_retire: bool
    must_repropose: bool
    blocked_reason: str | None = None


def classification_decision_capabilities(*, status: str, method: str) -> DecisionCapabilities:
    """Derive one classification assignment's available decisions.

    Reads the same two vocabularies `transition_symbol_revision_classification`
    enforces, so a UI built on this cannot offer a decision the service will
    refuse.
    """
    if status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid classification status: {status!r}")
    if method not in CLASSIFICATION_ASSIGNMENT_METHODS:
        raise ValueError(f"invalid classification method: {method!r}")

    allowed = CLASSIFICATION_ASSIGNMENT_TRANSITIONS[status]
    verification_offered = "verified" in allowed
    backfilled = method in BACKFILL_METHODS

    return DecisionCapabilities(
        can_verify=verification_offered and not backfilled,
        can_reject="rejected" in allowed,
        can_retire="retired" in allowed,
        must_repropose=backfilled and verification_offered,
        blocked_reason=(
            f"a {method} classification cannot be verified (section 12.3); "
            "reject it and propose afresh with a real method"
            if backfilled and verification_offered
            else None
        ),
    )


@dataclass(frozen=True)
class ReviewSymbolIdentity:
    """How a queue row names its symbol to an operator.

    `CLAUDE.md`: human-readable symbol IDs and operator-readable timestamps
    stay prominent, and a UUID never replaces them in compact UI. The
    identity is joined into the queue so no surface has to resolve one.
    `governed_symbol_id` is present for links and nothing else.
    """

    governed_symbol_id: uuid.UUID
    catalog_symbol_id: str | None
    canonical_name: str
    slug: str
    visibility: str
    owner_organization_id: uuid.UUID | None


@dataclass(frozen=True)
class ClassificationReviewRow:
    """One open classification proposal, with everything a reviewer needs.

    `evidence` is section 16.1's "with evidence and status" -- returned whole
    rather than summarised, because what counts as the salient part of a
    match differs by method and the queue does not get to decide.
    """

    assignment_id: uuid.UUID
    symbol_revision_id: uuid.UUID
    symbol: ReviewSymbolIdentity
    classification_scheme_id: uuid.UUID
    scheme_code: str
    node_code: str
    node_label: str
    assignment_role: str
    status: str
    method: str
    confidence: Decimal | None
    evidence: dict
    proposed_at: datetime
    capabilities: DecisionCapabilities


def _validated_page(limit: int, offset: int) -> tuple[int, int]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_QUEUE_LIMIT:
        raise ValueError(f"queue limit must be between 1 and {MAX_QUEUE_LIMIT}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("queue offset must not be negative")
    return limit, offset


def _visible_symbol_predicate(organization_id: uuid.UUID | None):
    """Section 14.2's private/public boundary, as one predicate.

    "External mappings and classification assertions attached to
    organisation-private symbols must not leak private symbol existence."
    A platform-scoped queue (`organization_id=None`) therefore sees public
    symbols only -- not "all symbols", and not "public plus whatever the
    caller happens to own". An organisation-bound session additionally sees
    that organisation's own private symbols and no other organisation's.

    This is the first read path over these tables: `publication_gate` records
    its own module header that section 14.2 was untouched there precisely
    because it exposed no read route.
    """
    if organization_id is None:
        return GovernedSymbol.visibility == "public"
    return or_(
        GovernedSymbol.visibility == "public",
        GovernedSymbol.owner_organization_id == organization_id,
    )


def list_open_symbol_revision_classifications(
    session: Session,
    *,
    organization_id: uuid.UUID | None = None,
    status: str = OPEN_STATUS,
    method: str | None = None,
    classification_scheme_code: str | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
    offset: int = 0,
) -> list[ClassificationReviewRow]:
    """Every open classification proposal across symbols, newest first.

    The query none of the five existing list functions answers: they are
    per-revision, per-concept or per-node, and a queue is per-*population*.

    Newest first because a review queue is worked from the newest arrival;
    `assignment_id` breaks ties so paging is stable when a backfill applies
    many rows at one timestamp -- which is exactly how the 132 production
    rows arrived.
    """
    if status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid classification status filter: {status!r}")
    if method is not None and method not in CLASSIFICATION_ASSIGNMENT_METHODS:
        raise ValueError(f"invalid classification method filter: {method!r}")
    limit, offset = _validated_page(limit, offset)

    query = (
        select(
            SymbolRevisionClassificationAssignment,
            GovernedSymbol,
            ClassificationScheme.scheme_code,
            ClassificationNode.node_code,
            ClassificationNode.preferred_label,
        )
        .join(
            SymbolRevision,
            SymbolRevision.id == SymbolRevisionClassificationAssignment.symbol_revision_id,
        )
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .join(
            ClassificationScheme,
            ClassificationScheme.id == SymbolRevisionClassificationAssignment.classification_scheme_id,
        )
        .join(
            ClassificationNode,
            ClassificationNode.id == SymbolRevisionClassificationAssignment.classification_node_id,
        )
        .where(
            SymbolRevisionClassificationAssignment.status == status,
            _visible_symbol_predicate(organization_id),
        )
    )
    if method is not None:
        query = query.where(SymbolRevisionClassificationAssignment.method == method)
    if classification_scheme_code is not None:
        query = query.where(ClassificationScheme.scheme_code == classification_scheme_code)

    query = query.order_by(
        SymbolRevisionClassificationAssignment.created_at.desc(),
        SymbolRevisionClassificationAssignment.id.desc(),
    ).limit(limit).offset(offset)

    return [
        ClassificationReviewRow(
            assignment_id=assignment.id,
            symbol_revision_id=assignment.symbol_revision_id,
            symbol=ReviewSymbolIdentity(
                governed_symbol_id=symbol.id,
                catalog_symbol_id=symbol.catalog_symbol_id,
                canonical_name=symbol.canonical_name,
                slug=symbol.slug,
                visibility=symbol.visibility,
                owner_organization_id=symbol.owner_organization_id,
            ),
            classification_scheme_id=assignment.classification_scheme_id,
            scheme_code=scheme_code,
            node_code=node_code,
            node_label=node_label,
            assignment_role=assignment.assignment_role,
            status=assignment.status,
            method=assignment.method,
            confidence=assignment.confidence,
            evidence=assignment.evidence_json or {},
            proposed_at=assignment.created_at,
            capabilities=classification_decision_capabilities(
                status=assignment.status, method=assignment.method
            ),
        )
        for assignment, symbol, scheme_code, node_code, node_label in session.execute(query).all()
    ]


def semantic_assignment_decision_capabilities(*, status: str, method: str) -> DecisionCapabilities:
    """Derive one symbol->concept assignment's available decisions.

    Section 7.9 carries no permanent bar: `manual` and `ai_assisted` need a
    named reviewer, which a review route always supplies, so there is nothing
    here a reviewer cannot lift. That asymmetry with classifications and
    rights is real and is why this is a separate function rather than a
    parameter.
    """
    if status not in SEMANTIC_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid semantic assignment status: {status!r}")
    if method not in SEMANTIC_ASSIGNMENT_METHODS:
        raise ValueError(f"invalid semantic assignment method: {method!r}")

    allowed = SEMANTIC_ASSIGNMENT_TRANSITIONS[status]
    return DecisionCapabilities(
        can_verify="verified" in allowed,
        can_reject="rejected" in allowed,
        can_retire="retired" in allowed,
        must_repropose=False,
    )


def external_mapping_decision_capabilities(*, status: str, method: str) -> DecisionCapabilities:
    """Derive one concept->external-scheme mapping's available decisions.

    Its own vocabulary, not the assignment one: section 7.5 names `imported`
    where section 7.9 names `source_mapping`, and the two stay distinct until
    the specification owner says they mean the same thing.

    Section 16.2's "no verified `exact` mapping from string similarity alone"
    is not represented here on purpose. It is a check on the verification
    *basis* the decider supplies, not on anything stored on the row, so it
    belongs at the decision call and not in a row capability that would claim
    a mapping is unverifiable when it is merely unverifiable on one basis.
    """
    if status not in EXTERNAL_MAPPING_STATUSES:
        raise ValueError(f"invalid external mapping status: {status!r}")
    if method not in EXTERNAL_MAPPING_METHODS:
        raise ValueError(f"invalid external mapping method: {method!r}")

    allowed = EXTERNAL_MAPPING_TRANSITIONS[status]
    return DecisionCapabilities(
        can_verify="verified" in allowed,
        can_reject="rejected" in allowed,
        can_retire="retired" in allowed,
        must_repropose=False,
    )


def rights_decision_capabilities(*, status: str, determination_method: str) -> DecisionCapabilities:
    """Derive one rights record's available decisions.

    This table names its affirmative decision `approved` where the other three
    name theirs `verified`. `can_verify` keeps the queue's single name so one
    control can render across every row kind; `blocked_reason` says which word
    the service uses when the decision is barred.

    The bar here is the exact analogue of the classification backfill rule and
    matters just as much: section 8.4 forbids an `ai_assisted` determination
    from approving itself, and `publication_gate.propose_intake_rights_record`
    is the only thing in production that creates a rights record at all --
    writing `ai_assisted` every time. So today the entire population is
    unapprovable, and the reviewer's route forward is their own proposal.
    """
    if status not in RIGHTS_DECISION_STATUSES:
        raise ValueError(f"invalid rights decision status: {status!r}")
    if determination_method not in RIGHTS_DETERMINATION_METHODS:
        raise ValueError(f"invalid rights determination method: {determination_method!r}")

    allowed = RIGHTS_DECISION_TRANSITIONS[status]
    approval_offered = "approved" in allowed
    non_approving = determination_method in NON_APPROVING_DETERMINATION_METHODS

    return DecisionCapabilities(
        can_verify=approval_offered and not non_approving,
        can_reject="rejected" in allowed,
        can_retire="retired" in allowed,
        must_repropose=non_approving and approval_offered,
        blocked_reason=(
            f"a {determination_method} rights determination cannot be approved "
            "(section 8.4); propose your own record with a reviewed method"
            if non_approving and approval_offered
            else None
        ),
    )


@dataclass(frozen=True)
class ReviewConceptIdentity:
    """How a queue row names its concept to an operator.

    `concept_code` is the stable `SGC-*` identifier section 16.1 requires to
    stay independent of the preferred name, so it is what a reviewer should
    see and cite. `preferred_name` comes from the concept's current revision
    and is a label, not an identity -- section 16.2: changing it changes
    neither the code nor any assignment.
    """

    semantic_concept_id: uuid.UUID
    concept_code: str
    preferred_name: str | None
    concept_kind: str
    status: str


@dataclass(frozen=True)
class SemanticAssignmentReviewRow:
    """One open symbol->concept assignment (section 7.7/7.9)."""

    assignment_id: uuid.UUID
    symbol_revision_id: uuid.UUID
    symbol: ReviewSymbolIdentity
    concept: ReviewConceptIdentity
    assignment_role: str
    status: str
    method: str
    confidence: Decimal | None
    evidence: dict
    proposed_at: datetime
    capabilities: DecisionCapabilities


@dataclass(frozen=True)
class ConceptClassificationReviewRow:
    """One open concept->classification-node assignment (section 7.8)."""

    assignment_id: uuid.UUID
    concept: ReviewConceptIdentity
    classification_scheme_id: uuid.UUID
    scheme_code: str
    node_code: str
    node_label: str
    assignment_role: str
    status: str
    method: str
    confidence: Decimal | None
    evidence: dict
    proposed_at: datetime
    capabilities: DecisionCapabilities


@dataclass(frozen=True)
class ExternalMappingReviewRow:
    """One open concept->external-scheme mapping (section 7.5).

    Carries the scheme code and version label, not just `scheme_version_id`:
    section 16.2 requires that no mapping exist without a release, and a
    reviewer cannot judge one without seeing which release it was made
    against.
    """

    reference_id: uuid.UUID
    concept: ReviewConceptIdentity
    scheme_version_id: uuid.UUID
    scheme_code: str
    scheme_version_label: str
    external_identifier: str
    external_label: str | None
    mapping_type: str
    status: str
    method: str
    confidence: Decimal | None
    evidence: dict
    proposed_at: datetime
    capabilities: DecisionCapabilities


@dataclass(frozen=True)
class RightsReviewRow:
    """One open rights record (section 7.12).

    `symbol` is None for a record whose subject is a source package or a
    standard edition rather than a symbol revision -- those are platform-level
    subjects with no tenant of their own. `subject_kind` says which it is so a
    surface never has to infer it from which field is null.
    """

    record_id: uuid.UUID
    subject_kind: str
    symbol_revision_id: uuid.UUID | None
    symbol: ReviewSymbolIdentity | None
    source_package_id: uuid.UUID | None
    standard_version_id: uuid.UUID | None
    rights_status: str
    disposition: str
    determination_method: str
    licence_reference: str | None
    status: str
    evidence: dict
    proposed_at: datetime
    capabilities: DecisionCapabilities


def _symbol_identity(symbol: GovernedSymbol) -> ReviewSymbolIdentity:
    return ReviewSymbolIdentity(
        governed_symbol_id=symbol.id,
        catalog_symbol_id=symbol.catalog_symbol_id,
        canonical_name=symbol.canonical_name,
        slug=symbol.slug,
        visibility=symbol.visibility,
        owner_organization_id=symbol.owner_organization_id,
    )


def _concept_identity(concept: SemanticConcept, preferred_name: str | None) -> ReviewConceptIdentity:
    return ReviewConceptIdentity(
        semantic_concept_id=concept.id,
        concept_code=concept.concept_code,
        preferred_name=preferred_name,
        concept_kind=concept.concept_kind,
        status=concept.status,
    )


# The name to show for a concept, resolved in one correlated subquery.
#
# `SemanticConcept.current_revision_id` is set only when a revision is
# *published* (`semantic_concepts.transition_semantic_concept_revision`), and a
# concept created through this package's own UI starts as `draft` with no
# current revision at all. Keying the display name on the current revision
# would therefore render a nameless row for precisely the concepts a reviewer
# has been queued to act on. So: the current revision's name when there is
# one, else the newest revision's.
#
# A name, never an identity -- section 16.2 keeps `concept_code` stable
# through any rename, and that is what a reviewer cites.
_CONCEPT_DISPLAY_NAME = (
    select(SemanticConceptRevision.preferred_name)
    .where(SemanticConceptRevision.concept_id == SemanticConcept.id)
    .order_by(
        (SemanticConceptRevision.id == SemanticConcept.current_revision_id).desc(),
        SemanticConceptRevision.created_at.desc(),
        SemanticConceptRevision.id.desc(),
    )
    .limit(1)
    .correlate(SemanticConcept)
    .scalar_subquery()
)


def list_open_symbol_semantic_assignments(
    session: Session,
    *,
    organization_id: uuid.UUID | None = None,
    status: str = OPEN_STATUS,
    method: str | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
    offset: int = 0,
) -> list[SemanticAssignmentReviewRow]:
    """Every open symbol->concept assignment across symbols, newest first.

    Tenant-scoped: this is the case section 14.2 names in so many words --
    "the assignment from a private symbol revision to a concept remains
    tenant-scoped information unless explicitly promoted".
    """
    if status not in SEMANTIC_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid semantic assignment status filter: {status!r}")
    if method is not None and method not in SEMANTIC_ASSIGNMENT_METHODS:
        raise ValueError(f"invalid semantic assignment method filter: {method!r}")
    limit, offset = _validated_page(limit, offset)

    query = (
        select(SymbolSemanticAssignment, GovernedSymbol, SemanticConcept, _CONCEPT_DISPLAY_NAME)
        .join(SymbolRevision, SymbolRevision.id == SymbolSemanticAssignment.symbol_revision_id)
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .join(SemanticConcept, SemanticConcept.id == SymbolSemanticAssignment.semantic_concept_id)
        .where(
            SymbolSemanticAssignment.status == status,
            _visible_symbol_predicate(organization_id),
        )
    )
    if method is not None:
        query = query.where(SymbolSemanticAssignment.method == method)
    query = query.order_by(
        SymbolSemanticAssignment.created_at.desc(), SymbolSemanticAssignment.id.desc()
    ).limit(limit).offset(offset)

    return [
        SemanticAssignmentReviewRow(
            assignment_id=assignment.id,
            symbol_revision_id=assignment.symbol_revision_id,
            symbol=_symbol_identity(symbol),
            concept=_concept_identity(concept, preferred_name),
            assignment_role=assignment.assignment_role,
            status=assignment.status,
            method=assignment.method,
            confidence=assignment.confidence,
            evidence=assignment.evidence_json or {},
            proposed_at=assignment.created_at,
            capabilities=semantic_assignment_decision_capabilities(
                status=assignment.status, method=assignment.method
            ),
        )
        for assignment, symbol, concept, preferred_name in session.execute(query).all()
    ]


def list_open_concept_classifications(
    session: Session,
    *,
    status: str = OPEN_STATUS,
    method: str | None = None,
    classification_scheme_code: str | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
    offset: int = 0,
) -> list[ConceptClassificationReviewRow]:
    """Every open concept classification, newest first.

    No tenant predicate, and that is not an omission: section 17 made concept
    governance platform-level, and this assertion names a concept and a
    classification node with no symbol anywhere in it. There is no private
    symbol existence for it to reveal. The symbol-scoped queues above are
    where section 14.2 bites.
    """
    if status not in CLASSIFICATION_ASSIGNMENT_STATUSES:
        raise ValueError(f"invalid classification status filter: {status!r}")
    if method is not None and method not in CLASSIFICATION_ASSIGNMENT_METHODS:
        raise ValueError(f"invalid classification method filter: {method!r}")
    limit, offset = _validated_page(limit, offset)

    query = (
        select(
            ConceptClassificationAssignment,
            SemanticConcept,
            _CONCEPT_DISPLAY_NAME,
            ClassificationScheme.scheme_code,
            ClassificationNode.node_code,
            ClassificationNode.preferred_label,
        )
        .join(SemanticConcept, SemanticConcept.id == ConceptClassificationAssignment.semantic_concept_id)
        .join(
            ClassificationScheme,
            ClassificationScheme.id == ConceptClassificationAssignment.classification_scheme_id,
        )
        .join(
            ClassificationNode,
            ClassificationNode.id == ConceptClassificationAssignment.classification_node_id,
        )
        .where(ConceptClassificationAssignment.status == status)
    )
    if method is not None:
        query = query.where(ConceptClassificationAssignment.method == method)
    if classification_scheme_code is not None:
        query = query.where(ClassificationScheme.scheme_code == classification_scheme_code)
    query = query.order_by(
        ConceptClassificationAssignment.created_at.desc(), ConceptClassificationAssignment.id.desc()
    ).limit(limit).offset(offset)

    return [
        ConceptClassificationReviewRow(
            assignment_id=assignment.id,
            concept=_concept_identity(concept, preferred_name),
            classification_scheme_id=assignment.classification_scheme_id,
            scheme_code=scheme_code,
            node_code=node_code,
            node_label=node_label,
            assignment_role=assignment.assignment_role,
            status=assignment.status,
            method=assignment.method,
            confidence=assignment.confidence,
            evidence=assignment.evidence_json or {},
            proposed_at=assignment.created_at,
            capabilities=classification_decision_capabilities(
                status=assignment.status, method=assignment.method
            ),
        )
        for assignment, concept, preferred_name, scheme_code, node_code, node_label in session.execute(
            query
        ).all()
    ]


def list_open_concept_external_references(
    session: Session,
    *,
    status: str = OPEN_STATUS,
    method: str | None = None,
    scheme_code: str | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
    offset: int = 0,
) -> list[ExternalMappingReviewRow]:
    """Every open concept->external-scheme mapping, newest first.

    Platform-scoped for `list_open_concept_classifications`' reason: a mapping
    names a concept and an external release, never a symbol.
    """
    if status not in EXTERNAL_MAPPING_STATUSES:
        raise ValueError(f"invalid external mapping status filter: {status!r}")
    if method is not None and method not in EXTERNAL_MAPPING_METHODS:
        raise ValueError(f"invalid external mapping method filter: {method!r}")
    limit, offset = _validated_page(limit, offset)

    query = (
        select(
            ConceptExternalReference,
            SemanticConcept,
            _CONCEPT_DISPLAY_NAME,
            ExternalSemanticScheme.scheme_code,
            ExternalSemanticSchemeVersion.version_label,
        )
        .join(SemanticConcept, SemanticConcept.id == ConceptExternalReference.semantic_concept_id)
        .join(
            ExternalSemanticSchemeVersion,
            ExternalSemanticSchemeVersion.id == ConceptExternalReference.scheme_version_id,
        )
        .join(
            ExternalSemanticScheme,
            ExternalSemanticScheme.id == ExternalSemanticSchemeVersion.scheme_id,
        )
        .where(ConceptExternalReference.mapping_status == status)
    )
    if method is not None:
        query = query.where(ConceptExternalReference.mapping_method == method)
    if scheme_code is not None:
        query = query.where(ExternalSemanticScheme.scheme_code == scheme_code)
    query = query.order_by(
        ConceptExternalReference.created_at.desc(), ConceptExternalReference.id.desc()
    ).limit(limit).offset(offset)

    return [
        ExternalMappingReviewRow(
            reference_id=reference.id,
            concept=_concept_identity(concept, preferred_name),
            scheme_version_id=reference.scheme_version_id,
            scheme_code=code,
            scheme_version_label=version_label,
            external_identifier=reference.external_identifier,
            external_label=reference.external_label,
            mapping_type=reference.mapping_type,
            status=reference.mapping_status,
            method=reference.mapping_method,
            confidence=reference.confidence,
            evidence=reference.evidence_json or {},
            proposed_at=reference.created_at,
            capabilities=external_mapping_decision_capabilities(
                status=reference.mapping_status, method=reference.mapping_method
            ),
        )
        for reference, concept, preferred_name, code, version_label in session.execute(query).all()
    ]


def list_open_rights_records(
    session: Session,
    *,
    organization_id: uuid.UUID | None = None,
    status: str = OPEN_STATUS,
    determination_method: str | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
    offset: int = 0,
) -> list[RightsReviewRow]:
    """Every open rights record, newest first.

    Tenant-scoped where the subject is a symbol revision. A record whose
    subject is a source package or a standard edition has no symbol and no
    tenant, and is returned at every scope: those are platform-level
    assertions about a licence, and withholding them would hide the very
    records section 9.2's rights dimension needs approved.
    """
    if status not in RIGHTS_DECISION_STATUSES:
        raise ValueError(f"invalid rights decision status filter: {status!r}")
    if determination_method is not None and determination_method not in RIGHTS_DETERMINATION_METHODS:
        raise ValueError(f"invalid rights determination method filter: {determination_method!r}")
    limit, offset = _validated_page(limit, offset)

    # Outer-joined so a package- or standard-subject record survives the join,
    # then filtered so a *symbol*-subject record outside the scope does not.
    query = (
        select(RightsRecord, GovernedSymbol)
        .outerjoin(SymbolRevision, SymbolRevision.id == RightsRecord.symbol_revision_id)
        .outerjoin(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .where(
            RightsRecord.decision_status == status,
            or_(
                RightsRecord.symbol_revision_id.is_(None),
                _visible_symbol_predicate(organization_id),
            ),
        )
    )
    if determination_method is not None:
        query = query.where(RightsRecord.determination_method == determination_method)
    query = query.order_by(
        RightsRecord.created_at.desc(), RightsRecord.id.desc()
    ).limit(limit).offset(offset)

    return [
        RightsReviewRow(
            record_id=record.id,
            subject_kind=(
                "symbol_revision"
                if record.symbol_revision_id is not None
                else "source_package"
                if record.source_package_id is not None
                else "standard_version"
            ),
            symbol_revision_id=record.symbol_revision_id,
            symbol=_symbol_identity(symbol) if symbol is not None else None,
            source_package_id=record.source_package_id,
            standard_version_id=record.standard_version_id,
            rights_status=record.rights_status,
            disposition=record.disposition,
            determination_method=record.determination_method,
            licence_reference=record.licence_reference,
            status=record.decision_status,
            evidence=record.evidence_json or {},
            proposed_at=record.created_at,
            capabilities=rights_decision_capabilities(
                status=record.decision_status, determination_method=record.determination_method
            ),
        )
        for record, symbol in session.execute(query).all()
    ]
