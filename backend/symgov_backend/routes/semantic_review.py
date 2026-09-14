"""The semantic review API (SM-P1-01 WP1.2).

Specification section 15.2 row `SM-P1-01`, closing the two section 16.1
criteria the package claims: "the review workflow can see proposed
semantic/classification/source assertions with evidence and status", and "no
organisation-private symbol existence is revealed by public semantic
endpoints".

**This is the first read route over the semantic tables, so section 14.2's
private/public boundary becomes live surface here for the first time.**
`publication_gate` records in its own module header that the boundary was
untouched there precisely because it exposed no read route; that sentence
stops being true at this file. Every query therefore runs through
`semantic_review._visible_symbol_predicate` by way of `organization_id`, and
every route that names a single existing row re-resolves that row through the
same predicate before doing anything with it. A row outside the caller's
scope is reported as absent, never as forbidden: `404` and `403` are
distinguishable, and the difference is exactly the private-symbol existence
section 14.2 forbids revealing.

**Authorization is decision Q2, resolved 2026-09-12.** Concept lifecycle --
create, add revision, transition -- is `require_platform_admin`, matching
section 17's "concept governance is platform-level initially". Symbol
semantic assignment, classification assignment and external-mapping decisions
are open to `admin` or `reviewer`, matching the existing Reviews surface.
`require_workspace_access` is deliberately not used: it is route-template
driven off `WORKSPACE_OPERATIONS` and would drag this router into the
workspace policy inventory it does not belong to.

**No route here takes `require_recent_step_up`** (decision Q8, 2026-09-13).
Every act is a reversible governed transition with a succession model and a
full audit trail -- a wrong `verified` is retired by the next decision rather
than destroying anything. `symbol_demotion` is the one surface that uses
step-up, and it is genuinely destructive; these are not.

**Why propose routes exist alongside decide routes.** WP1.2's line in the
plan names "assignment, classification and external-mapping decisions", and a
decision route on its own would be unreachable for two of the three.
`semantic_concepts` and `concept_external_references` have no production
importer at all (plan section 1.2), so no assignment and no mapping exists to
decide on until a human can make one. For classifications the reverse problem
applies: 132 rows exist, and every one is `legacy_backfill`, which section
12.3 makes permanently unverifiable. WP1.1 surfaces that as `must_repropose`
with the instruction to "reject it and propose afresh with a real method" --
an instruction no route could carry out. Proposing is what makes the queue's
own advice actionable; it writes nothing the services did not already own.

**Section 8.4 holds on every proposal.** A route cannot create a `verified`
row. Every propose endpoint goes through the P0 service, which starts every
assertion as `proposed` whatever its method or confidence, and every decision
is attributed to the authenticated reviewer -- never to a service user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..classification_assignments import (
    list_symbol_revision_classifications,
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from ..classification_schemes import list_classification_nodes
from ..concept_external_references import (
    list_concept_external_references,
    propose_concept_external_reference,
    transition_concept_external_reference,
)
from ..dependencies import get_db_session, require_any_role, require_platform_admin
from ..models import (
    ClassificationNode,
    ClassificationScheme,
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
from ..rights_provenance import (
    list_rights_records,
    propose_rights_record,
    transition_rights_record,
)
from ..schemas import (
    APIErrorResponse,
    APIValidationErrorResponse,
    ClassificationSchemeOptionsResponse,
    ConceptClassificationQueueResponse,
    ConceptExternalMappingListResponse,
    ConceptExternalMappingProposeRequest,
    ConceptExternalMappingQueueResponse,
    ExternalMappingDecisionRequest,
    RightsRecordDecisionRequest,
    RightsRecordProposeRequest,
    RightsRecordQueueResponse,
    RightsRecordReviewRowResponse,
    SemanticConceptCreateRequest,
    SemanticConceptRevisionCreateRequest,
    SemanticConceptRevisionResponse,
    SemanticConceptRevisionTransitionRequest,
    SemanticReviewDecisionRequest,
    SymbolClassificationProposeRequest,
    SymbolClassificationQueueResponse,
    SymbolRevisionSemanticStateResponse,
    SymbolSemanticAssignmentProposeRequest,
    SymbolSemanticAssignmentQueueResponse,
)
from ..semantic_concepts import (
    add_semantic_concept_revision,
    create_semantic_concept,
    transition_semantic_concept_revision,
)
from ..semantic_review import (
    DEFAULT_QUEUE_LIMIT,
    MAX_QUEUE_LIMIT,
    classification_decision_capabilities,
    external_mapping_decision_capabilities,
    list_open_concept_classifications,
    list_open_concept_external_references,
    list_open_rights_records,
    list_open_symbol_revision_classifications,
    list_open_symbol_semantic_assignments,
    rights_decision_capabilities,
    semantic_assignment_decision_capabilities,
)
from ..settings import SymgovAPISettings, get_settings
from ..symbol_semantic_assignments import (
    list_symbol_semantic_assignments,
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
)

router = APIRouter(prefix="/semantic-review", tags=["semantic-review"])

# Decision Q6: `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` are
# read-only in v1. Existing assignments in them are displayed; nothing
# creates one. The decision is written as a statement about the UI, but a
# control the API still honours is not read-only -- so the refusal lives
# here, at the layer that can actually enforce it. Only the two schemes
# `classification_mapping.plan_classification_mapping` already maps into can
# receive a reviewer's proposal.
REVIEWER_ASSIGNABLE_SCHEME_CODES = frozenset({"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"})

_ERROR_RESPONSES = {
    401: {"model": APIErrorResponse},
    403: {"model": APIErrorResponse},
    404: {"model": APIErrorResponse},
    422: {"model": APIValidationErrorResponse},
}

reviewer = require_any_role({"admin", "reviewer"})


def semantic_review_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    """A disabled feature is absent, not forbidden.

    404 rather than 403, matching `symbol_demotion`: a 403 would confirm that
    the surface exists, which advertises an unreleased feature to anyone who
    probes for it.
    """
    if not settings.semantic_review_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


def _scope(current_user: AuthenticatedUser) -> uuid.UUID | None:
    """The caller's tenant scope for section 14.2's predicate.

    A personal-mode session scopes to `None`, which `semantic_review`
    resolves to public symbols only -- not "all symbols". Platform Admin is
    deliberately not a bypass: WP1.1 decided this predicate, and widening it
    for a role would change the boundary rather than apply it.
    """
    if current_user.session_mode != "organization" or not current_user.active_organization_id:
        return None
    return uuid.UUID(str(current_user.active_organization_id))


def _actor(current_user: AuthenticatedUser) -> uuid.UUID:
    return uuid.UUID(str(current_user.id))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _invalid(message: str, field: tuple[str, ...] = ("body",)) -> RequestValidationError:
    """Turn a service vocabulary refusal into the project's 422 envelope.

    The frozen vocabularies live in the P0 services and are enforced by
    CHECK constraints; restating them in the schemas would create a second
    copy to drift. So the schema accepts a bounded string, the service
    decides, and its `ValueError` becomes the same validation envelope a
    Pydantic failure produces (`app.py`'s handler).
    """
    return RequestValidationError([{"loc": field, "msg": message, "type": "value_error"}])


def _is_active_node_conflict(exc: IntegrityError) -> bool:
    """Is this the partial unique index on one revision's live nodes?

    `uq_symbol_revision_classifications_active_node` is unique on
    (symbol_revision_id, classification_node_id) while the status is
    `proposed` or `verified`. Matched by name rather than by catching every
    IntegrityError, so a genuine storage fault still surfaces as one.
    """
    message = str(exc.orig or exc).lower()
    return (
        "duplicate key value" in message
        and "uq_symbol_revision_classifications_active_node" in message
    )


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _page(
    limit: int = Query(DEFAULT_QUEUE_LIMIT, ge=1, le=MAX_QUEUE_LIMIT),
    offset: int = Query(0, ge=0),
) -> tuple[int, int]:
    """Bounded rather than clamped, matching `semantic_review`'s own rule.

    `le=MAX_QUEUE_LIMIT` rejects an over-large page at the contract boundary
    with the project's validation envelope, so a caller asking for 5000 rows
    is told rather than silently handed 200.
    """
    return limit, offset


# ---------------------------------------------------------------------------
# Row rendering
# ---------------------------------------------------------------------------


def _symbol_payload(symbol) -> dict:
    return {
        "governedSymbolId": str(symbol.governed_symbol_id),
        "catalogSymbolId": symbol.catalog_symbol_id,
        "canonicalName": symbol.canonical_name,
        "slug": symbol.slug,
        "visibility": symbol.visibility,
    }


def _concept_payload(concept) -> dict:
    return {
        "semanticConceptId": str(concept.semantic_concept_id),
        "conceptCode": concept.concept_code,
        "preferredName": concept.preferred_name,
        "conceptKind": concept.concept_kind,
        "status": concept.status,
    }


def _capabilities_payload(capabilities) -> dict:
    return {
        "canVerify": capabilities.can_verify,
        "canReject": capabilities.can_reject,
        "canRetire": capabilities.can_retire,
        "mustRepropose": capabilities.must_repropose,
        "blockedReason": capabilities.blocked_reason,
    }


def _classification_row(row) -> dict:
    return {
        "assignmentId": str(row.assignment_id),
        "symbolRevisionId": str(row.symbol_revision_id),
        "symbol": _symbol_payload(row.symbol),
        "classificationSchemeId": str(row.classification_scheme_id),
        "classificationNodeId": str(row.classification_node_id),
        "schemeCode": row.scheme_code,
        "nodeCode": row.node_code,
        "nodeLabel": row.node_label,
        "assignmentRole": row.assignment_role,
        "status": row.status,
        "method": row.method,
        "confidence": _float(row.confidence),
        "evidence": row.evidence,
        "proposedAt": row.proposed_at.isoformat(),
        "capabilities": _capabilities_payload(row.capabilities),
    }


def _semantic_assignment_row(row) -> dict:
    return {
        "assignmentId": str(row.assignment_id),
        "symbolRevisionId": str(row.symbol_revision_id),
        "symbol": _symbol_payload(row.symbol),
        "concept": _concept_payload(row.concept),
        "assignmentRole": row.assignment_role,
        "status": row.status,
        "method": row.method,
        "confidence": _float(row.confidence),
        "evidence": row.evidence,
        "proposedAt": row.proposed_at.isoformat(),
        "capabilities": _capabilities_payload(row.capabilities),
    }


def _concept_classification_row(row) -> dict:
    return {
        "assignmentId": str(row.assignment_id),
        "concept": _concept_payload(row.concept),
        "classificationSchemeId": str(row.classification_scheme_id),
        "schemeCode": row.scheme_code,
        "nodeCode": row.node_code,
        "nodeLabel": row.node_label,
        "assignmentRole": row.assignment_role,
        "status": row.status,
        "method": row.method,
        "confidence": _float(row.confidence),
        "evidence": row.evidence,
        "proposedAt": row.proposed_at.isoformat(),
        "capabilities": _capabilities_payload(row.capabilities),
    }


def _external_mapping_row(row) -> dict:
    return {
        "referenceId": str(row.reference_id),
        "concept": _concept_payload(row.concept),
        "schemeVersionId": str(row.scheme_version_id),
        "schemeCode": row.scheme_code,
        "schemeVersionLabel": row.scheme_version_label,
        "externalIdentifier": row.external_identifier,
        "externalLabel": row.external_label,
        "mappingType": row.mapping_type,
        "status": row.status,
        "method": row.method,
        "confidence": _float(row.confidence),
        "evidence": row.evidence,
        "proposedAt": row.proposed_at.isoformat(),
        "capabilities": _capabilities_payload(row.capabilities),
    }


def _rights_row(row) -> dict:
    return {
        "recordId": str(row.record_id),
        "subjectKind": row.subject_kind,
        "symbolRevisionId": str(row.symbol_revision_id) if row.symbol_revision_id else None,
        "symbol": _symbol_payload(row.symbol) if row.symbol is not None else None,
        "sourcePackageId": str(row.source_package_id) if row.source_package_id else None,
        "standardVersionId": str(row.standard_version_id) if row.standard_version_id else None,
        "rightsStatus": row.rights_status,
        "disposition": row.disposition,
        "determinationMethod": row.determination_method,
        "licenceReference": row.licence_reference,
        "status": row.status,
        "evidence": row.evidence,
        "proposedAt": row.proposed_at.isoformat(),
        "capabilities": _capabilities_payload(row.capabilities),
    }


# ---------------------------------------------------------------------------
# Scope-aware resolution
#
# Every one of these reports an out-of-scope row as absent. The alternative --
# 403 for "exists but not yours" -- is precisely the private-symbol existence
# disclosure section 14.2 forbids, because the caller can tell the two apart.
# ---------------------------------------------------------------------------


def _visible_revision(session: Session, revision_id: uuid.UUID, scope: uuid.UUID | None) -> tuple[SymbolRevision, GovernedSymbol]:
    row = session.execute(
        select(SymbolRevision, GovernedSymbol)
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .where(SymbolRevision.id == revision_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Symbol revision was not found.")
    revision, symbol = row
    if symbol.visibility != "public" and (scope is None or symbol.owner_organization_id != scope):
        raise HTTPException(status_code=404, detail="Symbol revision was not found.")
    return revision, symbol


def _visible_symbol_row(session: Session, model, row_id: uuid.UUID, scope: uuid.UUID | None, label: str):
    """Resolve a symbol-targeted governed row inside the caller's scope."""
    row = session.execute(
        select(model, GovernedSymbol)
        .join(SymbolRevision, SymbolRevision.id == model.symbol_revision_id)
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .where(model.id == row_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{label} was not found.")
    governed_row, symbol = row
    if symbol.visibility != "public" and (scope is None or symbol.owner_organization_id != scope):
        raise HTTPException(status_code=404, detail=f"{label} was not found.")
    return governed_row


def _concept(session: Session, concept_id: uuid.UUID) -> SemanticConcept:
    """Concepts carry no tenant.

    Section 17 made concept governance platform-level and section 14.2's
    sentence is specifically about "the assignment from a private symbol
    revision to a concept" -- the concept itself has no private existence to
    leak. WP1.1 made the same call for the two concept-targeted queues.
    """
    concept = session.get(SemanticConcept, concept_id)
    if concept is None:
        raise HTTPException(status_code=404, detail="Semantic concept was not found.")
    return concept


# ---------------------------------------------------------------------------
# Queue reads
# ---------------------------------------------------------------------------


@router.get(
    "/queues/symbol-classifications",
    response_model=SymbolClassificationQueueResponse,
    responses=_ERROR_RESPONSES,
)
def symbol_classification_queue(
    page: tuple[int, int] = Depends(_page),
    status: str = Query("proposed", min_length=1, max_length=32),
    method: str | None = Query(None, min_length=1, max_length=32),
    schemeCode: str | None = Query(None, min_length=1, max_length=64),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolClassificationQueueResponse:
    limit, offset = page
    try:
        rows = list_open_symbol_revision_classifications(
            session,
            organization_id=_scope(current_user),
            status=status,
            method=method,
            classification_scheme_code=schemeCode,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _invalid(str(exc), ("query",)) from exc
    return SymbolClassificationQueueResponse(
        items=[_classification_row(row) for row in rows], limit=limit, offset=offset
    )


@router.get(
    "/queues/symbol-semantic-assignments",
    response_model=SymbolSemanticAssignmentQueueResponse,
    responses=_ERROR_RESPONSES,
)
def symbol_semantic_assignment_queue(
    page: tuple[int, int] = Depends(_page),
    status: str = Query("proposed", min_length=1, max_length=32),
    method: str | None = Query(None, min_length=1, max_length=32),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolSemanticAssignmentQueueResponse:
    limit, offset = page
    try:
        rows = list_open_symbol_semantic_assignments(
            session,
            organization_id=_scope(current_user),
            status=status,
            method=method,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _invalid(str(exc), ("query",)) from exc
    return SymbolSemanticAssignmentQueueResponse(
        items=[_semantic_assignment_row(row) for row in rows], limit=limit, offset=offset
    )


@router.get(
    "/queues/concept-classifications",
    response_model=ConceptClassificationQueueResponse,
    responses=_ERROR_RESPONSES,
)
def concept_classification_queue(
    page: tuple[int, int] = Depends(_page),
    status: str = Query("proposed", min_length=1, max_length=32),
    method: str | None = Query(None, min_length=1, max_length=32),
    schemeCode: str | None = Query(None, min_length=1, max_length=64),
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(reviewer),
) -> ConceptClassificationQueueResponse:
    limit, offset = page
    try:
        rows = list_open_concept_classifications(
            session,
            status=status,
            method=method,
            classification_scheme_code=schemeCode,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _invalid(str(exc), ("query",)) from exc
    return ConceptClassificationQueueResponse(
        items=[_concept_classification_row(row) for row in rows], limit=limit, offset=offset
    )


@router.get(
    "/queues/concept-external-mappings",
    response_model=ConceptExternalMappingQueueResponse,
    responses=_ERROR_RESPONSES,
)
def concept_external_mapping_queue(
    page: tuple[int, int] = Depends(_page),
    status: str = Query("proposed", min_length=1, max_length=32),
    method: str | None = Query(None, min_length=1, max_length=32),
    schemeCode: str | None = Query(None, min_length=1, max_length=64),
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(reviewer),
) -> ConceptExternalMappingQueueResponse:
    limit, offset = page
    try:
        rows = list_open_concept_external_references(
            session,
            status=status,
            method=method,
            scheme_code=schemeCode,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _invalid(str(exc), ("query",)) from exc
    return ConceptExternalMappingQueueResponse(
        items=[_external_mapping_row(row) for row in rows], limit=limit, offset=offset
    )


@router.get(
    "/queues/rights-records",
    response_model=RightsRecordQueueResponse,
    responses=_ERROR_RESPONSES,
)
def rights_record_queue(
    page: tuple[int, int] = Depends(_page),
    status: str = Query("proposed", min_length=1, max_length=32),
    determinationMethod: str | None = Query(None, min_length=1, max_length=32),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> RightsRecordQueueResponse:
    limit, offset = page
    try:
        rows = list_open_rights_records(
            session,
            organization_id=_scope(current_user),
            status=status,
            determination_method=determinationMethod,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _invalid(str(exc), ("query",)) from exc
    return RightsRecordQueueResponse(
        items=[_rights_row(row) for row in rows], limit=limit, offset=offset
    )


@router.get(
    "/classification-schemes",
    response_model=ClassificationSchemeOptionsResponse,
    responses=_ERROR_RESPONSES,
)
def classification_scheme_options(
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(reviewer),
) -> ClassificationSchemeOptionsResponse:
    """The nodes a reviewer may propose against.

    Added by the 2026-09-14 amendment. WP1.2 shipped a propose route taking a
    `classificationNodeId` and no read that returned one, so section 12.3's
    "reject it and propose afresh with a real method" was readable advice a
    client could not act on. `test_a_reviewer_rejects_a_backfilled_row_then_
    proposes_afresh` passed only because the fixture held the identifier.

    Scoped to `REVIEWER_ASSIGNABLE_SCHEME_CODES` for decision Q6: the other
    three schemes are display-only in v1 and the propose route refuses them,
    so offering them as choices would invite a refusal.

    No tenant predicate: schemes and nodes are seeded platform reference data
    naming no symbol, so section 14.2 -- which is about private symbol
    existence -- has nothing to say here. Only active nodes are offered; a
    retired node is not a choice.
    """
    schemes = session.execute(
        select(ClassificationScheme)
        .where(ClassificationScheme.scheme_code.in_(sorted(REVIEWER_ASSIGNABLE_SCHEME_CODES)))
        .order_by(ClassificationScheme.scheme_code)
    ).scalars().all()

    return ClassificationSchemeOptionsResponse(
        items=[
            {
                "schemeId": str(scheme.id),
                "schemeCode": scheme.scheme_code,
                "name": scheme.name,
                "nodes": [
                    {
                        "nodeId": str(node.id),
                        "nodeCode": node.node_code,
                        "nodeLabel": node.preferred_label,
                        "parentNodeId": str(node.parent_node_id) if node.parent_node_id else None,
                    }
                    for node in list_classification_nodes(session, scheme.id, status="active")
                ],
            }
            for scheme in schemes
        ]
    )


# ---------------------------------------------------------------------------
# One revision's semantic state
# ---------------------------------------------------------------------------


@router.get(
    "/symbol-revisions/{symbol_revision_id}",
    response_model=SymbolRevisionSemanticStateResponse,
    responses=_ERROR_RESPONSES,
)
def symbol_revision_semantic_state(
    symbol_revision_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolRevisionSemanticStateResponse:
    """Every governed assertion about one revision, whatever its status.

    Not filtered to `proposed`: section 14.4 keeps the decision history with
    the governed data, and a reviewer deciding on a proposal needs to see the
    rejected and retired ones that came before it.
    """
    scope = _scope(current_user)
    revision, symbol = _visible_revision(session, symbol_revision_id, scope)
    identity = _identity_from_model(symbol)

    assignments = []
    for assignment in list_symbol_semantic_assignments(session, revision.id):
        concept = session.get(SemanticConcept, assignment.semantic_concept_id)
        assignments.append(
            {
                "assignmentId": str(assignment.id),
                "symbolRevisionId": str(revision.id),
                "symbol": identity,
                "concept": _concept_payload_from_model(session, concept),
                "assignmentRole": assignment.assignment_role,
                "status": assignment.status,
                "method": assignment.method,
                "confidence": _float(assignment.confidence),
                "evidence": assignment.evidence_json or {},
                "proposedAt": assignment.created_at.isoformat(),
                "capabilities": _capabilities_payload(
                    semantic_assignment_decision_capabilities(
                        status=assignment.status, method=assignment.method
                    )
                ),
            }
        )

    classifications = []
    for assignment in list_symbol_revision_classifications(session, revision.id):
        node, scheme = session.execute(
            select(ClassificationNode, ClassificationScheme)
            .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
            .where(ClassificationNode.id == assignment.classification_node_id)
        ).one()
        classifications.append(
            {
                "assignmentId": str(assignment.id),
                "symbolRevisionId": str(revision.id),
                "symbol": identity,
                "classificationSchemeId": str(assignment.classification_scheme_id),
                "classificationNodeId": str(assignment.classification_node_id),
                "schemeCode": scheme.scheme_code,
                "nodeCode": node.node_code,
                "nodeLabel": node.preferred_label,
                "assignmentRole": assignment.assignment_role,
                "status": assignment.status,
                "method": assignment.method,
                "confidence": _float(assignment.confidence),
                "evidence": assignment.evidence_json or {},
                "proposedAt": assignment.created_at.isoformat(),
                "capabilities": _capabilities_payload(
                    classification_decision_capabilities(
                        status=assignment.status, method=assignment.method
                    )
                ),
            }
        )

    rights = [
        {
            "recordId": str(record.id),
            "subjectKind": "symbol_revision",
            "symbolRevisionId": str(revision.id),
            "symbol": identity,
            "sourcePackageId": None,
            "standardVersionId": None,
            "rightsStatus": record.rights_status,
            "disposition": record.disposition,
            "determinationMethod": record.determination_method,
            "licenceReference": record.licence_reference,
            "status": record.decision_status,
            "evidence": record.evidence_json or {},
            "proposedAt": record.created_at.isoformat(),
            "capabilities": _capabilities_payload(
                rights_decision_capabilities(
                    status=record.decision_status,
                    determination_method=record.determination_method,
                )
            ),
        }
        for record in list_rights_records(session, symbol_revision_id=revision.id)
    ]

    return SymbolRevisionSemanticStateResponse(
        symbolRevisionId=str(revision.id),
        revisionLabel=revision.revision_label,
        lifecycleState=revision.lifecycle_state,
        symbol=identity,
        semanticAssignments=assignments,
        classificationAssignments=classifications,
        rightsRecords=rights,
    )


def _identity_from_model(symbol: GovernedSymbol) -> dict:
    """The same identity WP1.1's queues render, from an ORM row.

    `catalog_symbol_id` is a column on `governed_symbols`, so a symbol that
    has one shows `S-000001` and never its UUID (`CLAUDE.md`).
    """
    return {
        "governedSymbolId": str(symbol.id),
        "catalogSymbolId": symbol.catalog_symbol_id,
        "canonicalName": symbol.canonical_name,
        "slug": symbol.slug,
        "visibility": symbol.visibility,
    }


def _concept_payload_from_model(session: Session, concept: SemanticConcept | None) -> dict:
    if concept is None:
        raise HTTPException(status_code=404, detail="Semantic concept was not found.")
    revision = None
    if concept.current_revision_id is not None:
        revision = session.get(SemanticConceptRevision, concept.current_revision_id)
    if revision is None:
        # A concept's `current_revision_id` is set only on publication, and
        # manual creation starts every concept as `draft`. Falling back to the
        # newest revision is WP1.1's rule, and the reason its queue does not
        # render a nameless row for exactly the concepts a reviewer is queued
        # to act on.
        revision = session.execute(
            select(SemanticConceptRevision)
            .where(SemanticConceptRevision.concept_id == concept.id)
            .order_by(SemanticConceptRevision.created_at.desc(), SemanticConceptRevision.id.desc())
        ).scalars().first()
    return {
        "semanticConceptId": str(concept.id),
        "conceptCode": concept.concept_code,
        "preferredName": revision.preferred_name if revision is not None else None,
        "conceptKind": concept.concept_kind,
        "status": concept.status,
    }


# ---------------------------------------------------------------------------
# Concept lifecycle -- platform-level (decision Q2, section 17)
# ---------------------------------------------------------------------------


def _concept_revision_response(concept: SemanticConcept, revision: SemanticConceptRevision) -> SemanticConceptRevisionResponse:
    return SemanticConceptRevisionResponse(
        semanticConceptId=str(concept.id),
        conceptCode=concept.concept_code,
        revisionId=str(revision.id),
        revisionLabel=revision.revision_label,
        preferredName=revision.preferred_name,
        definition=revision.definition,
        lifecycleState=revision.lifecycle_state,
        conceptStatus=concept.status,
        createdAt=revision.created_at.isoformat(),
    )


@router.post(
    "/concepts",
    status_code=201,
    response_model=SemanticConceptRevisionResponse,
    responses=_ERROR_RESPONSES,
)
def create_concept(
    body: SemanticConceptCreateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> SemanticConceptRevisionResponse:
    """Manual concept creation -- the first writer `semantic_concepts` has had.

    M3's provisional concept candidates are deliberately not in this package:
    manual creation lands first, so there is something to review before a
    generator fills the queue.
    """
    try:
        concept, revision = create_semantic_concept(
            session,
            concept_kind=body.conceptKind,
            preferred_name=body.preferredName,
            definition=body.definition,
            created_by_user_id=_actor(current_user),
            created_at=_now(),
            revision_label=body.revisionLabel,
            aliases=body.aliases,
            notes=body.notes,
            rationale=body.rationale,
        )
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    session.commit()
    return _concept_revision_response(concept, revision)


@router.post(
    "/concepts/{concept_id}/revisions",
    status_code=201,
    response_model=SemanticConceptRevisionResponse,
    responses=_ERROR_RESPONSES,
)
def add_concept_revision(
    concept_id: uuid.UUID,
    body: SemanticConceptRevisionCreateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> SemanticConceptRevisionResponse:
    concept = _concept(session, concept_id)
    try:
        revision = add_semantic_concept_revision(
            session,
            concept.id,
            revision_label=body.revisionLabel,
            preferred_name=body.preferredName,
            definition=body.definition,
            author_id=_actor(current_user),
            created_at=_now(),
            aliases=body.aliases,
            notes=body.notes,
            rationale=body.rationale,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Semantic concept was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    session.commit()
    return _concept_revision_response(concept, revision)


@router.post(
    "/concept-revisions/{revision_id}/transition",
    response_model=SemanticConceptRevisionResponse,
    responses=_ERROR_RESPONSES,
)
def transition_concept_revision(
    revision_id: uuid.UUID,
    body: SemanticConceptRevisionTransitionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> SemanticConceptRevisionResponse:
    try:
        revision = transition_semantic_concept_revision(
            session,
            revision_id,
            target_state=body.targetState,
            actor_id=_actor(current_user),
            occurred_at=_now(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Semantic concept revision was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    concept = session.get(SemanticConcept, revision.concept_id)
    session.commit()
    return _concept_revision_response(concept, revision)


# ---------------------------------------------------------------------------
# Symbol semantic assignments -- admin or reviewer (decision Q2)
#
# Every write below returns the affected revision's whole governed state
# rather than a page of rows. The queue response shape carries
# `limit`/`offset`, and a write applied neither -- reporting `limit=len(items)`
# would describe a pagination bound that was never taken. The state is also
# what the caller wants next: it can re-render the panel from the response.
# ---------------------------------------------------------------------------


@router.post(
    "/symbol-revisions/{symbol_revision_id}/semantic-assignments",
    status_code=201,
    response_model=SymbolRevisionSemanticStateResponse,
    responses=_ERROR_RESPONSES,
)
def propose_semantic_assignment(
    symbol_revision_id: uuid.UUID,
    body: SymbolSemanticAssignmentProposeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolRevisionSemanticStateResponse:
    scope = _scope(current_user)
    revision, _symbol = _visible_revision(session, symbol_revision_id, scope)
    _concept(session, body.semanticConceptId)
    try:
        propose_symbol_semantic_assignment(
            session,
            symbol_revision_id=revision.id,
            semantic_concept_id=body.semanticConceptId,
            assignment_role=body.assignmentRole,
            method=body.method,
            proposed_at=_now(),
            proposed_by_user_id=_actor(current_user),
            confidence=_decimal(body.confidence),
            evidence=body.evidence,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Symbol revision was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    session.commit()
    return symbol_revision_semantic_state(revision.id, session=session, current_user=current_user)



@router.post(
    "/semantic-assignments/{assignment_id}/decision",
    response_model=SymbolRevisionSemanticStateResponse,
    responses=_ERROR_RESPONSES,
)
def decide_semantic_assignment(
    assignment_id: uuid.UUID,
    body: SemanticReviewDecisionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolRevisionSemanticStateResponse:
    scope = _scope(current_user)
    assignment = _visible_symbol_row(
        session, SymbolSemanticAssignment, assignment_id, scope, "Semantic assignment"
    )
    try:
        updated = transition_symbol_semantic_assignment(
            session,
            assignment.id,
            target_status=body.targetStatus,
            occurred_at=_now(),
            reviewed_by_user_id=_actor(current_user),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Semantic assignment was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    revision_id = updated.symbol_revision_id
    session.commit()
    return symbol_revision_semantic_state(revision_id, session=session, current_user=current_user)


# ---------------------------------------------------------------------------
# Symbol classification assignments -- admin or reviewer (decision Q2)
# ---------------------------------------------------------------------------


@router.post(
    "/symbol-revisions/{symbol_revision_id}/classifications",
    status_code=201,
    response_model=SymbolRevisionSemanticStateResponse,
    responses=_ERROR_RESPONSES,
)
def propose_symbol_classification(
    symbol_revision_id: uuid.UUID,
    body: SymbolClassificationProposeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolRevisionSemanticStateResponse:
    """A reviewer's own classification -- what `must_repropose` asks for.

    Decision Q6 keeps `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE`
    read-only in v1, so a proposal into one of them is refused here rather
    than only being absent from the UI.
    """
    scope = _scope(current_user)
    revision, _symbol = _visible_revision(session, symbol_revision_id, scope)

    node_row = session.execute(
        select(ClassificationNode, ClassificationScheme)
        .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
        .where(ClassificationNode.id == body.classificationNodeId)
    ).first()
    if node_row is None:
        raise HTTPException(status_code=404, detail="Classification node was not found.")
    _node, scheme = node_row
    if scheme.scheme_code not in REVIEWER_ASSIGNABLE_SCHEME_CODES:
        raise _invalid(
            f"the {scheme.scheme_code} scheme is read-only in this release; "
            "its existing assignments are displayed but none may be created",
            ("body", "classificationNodeId"),
        )

    try:
        propose_symbol_revision_classification(
            session,
            symbol_revision_id=revision.id,
            classification_node_id=body.classificationNodeId,
            assignment_role=body.assignmentRole,
            method=body.method,
            proposed_at=_now(),
            proposed_by_user_id=_actor(current_user),
            confidence=_decimal(body.confidence),
            evidence=body.evidence,
        )
        session.flush()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Symbol revision was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    except IntegrityError as exc:
        # Reachable since the 2026-09-14 amendment gave reviewers a node
        # picker: before it, no client could name a node at all, so the
        # collision could only be produced from inside the process. It is a
        # refusal like any other on this router, not a fault, so it wears the
        # same 422 envelope rather than escaping as a 500.
        session.rollback()
        if not _is_active_node_conflict(exc):
            raise
        raise _invalid(
            "this revision already has a live assignment to that node; "
            "decide the existing one before proposing the node again",
            ("body", "classificationNodeId"),
        ) from exc
    session.commit()
    return symbol_revision_semantic_state(revision.id, session=session, current_user=current_user)


@router.post(
    "/symbol-classifications/{assignment_id}/decision",
    response_model=SymbolRevisionSemanticStateResponse,
    responses=_ERROR_RESPONSES,
)
def decide_symbol_classification(
    assignment_id: uuid.UUID,
    body: SemanticReviewDecisionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> SymbolRevisionSemanticStateResponse:
    """Verifying a `legacy_backfill` row is refused by the database itself.

    `ck_symbol_revision_classifications_backfill_not_verified` (section 12.3)
    is the rule, `transition_symbol_revision_classification` enforces it, and
    the queue's `mustRepropose` flag warns of it before a reviewer tries.
    """
    scope = _scope(current_user)
    assignment = _visible_symbol_row(
        session,
        SymbolRevisionClassificationAssignment,
        assignment_id,
        scope,
        "Classification assignment",
    )
    try:
        updated = transition_symbol_revision_classification(
            session,
            assignment.id,
            target_status=body.targetStatus,
            occurred_at=_now(),
            reviewed_by_user_id=_actor(current_user),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Classification assignment was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    revision_id = updated.symbol_revision_id
    session.commit()
    return symbol_revision_semantic_state(revision_id, session=session, current_user=current_user)


# ---------------------------------------------------------------------------
# Concept external mappings -- admin or reviewer (decision Q2)
# ---------------------------------------------------------------------------


@router.post(
    "/concepts/{concept_id}/external-mappings",
    status_code=201,
    response_model=ConceptExternalMappingListResponse,
    responses=_ERROR_RESPONSES,
)
def propose_external_mapping(
    concept_id: uuid.UUID,
    body: ConceptExternalMappingProposeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> ConceptExternalMappingListResponse:
    concept = _concept(session, concept_id)
    try:
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept.id,
            scheme_version_id=body.schemeVersionId,
            external_identifier=body.externalIdentifier,
            mapping_type=body.mappingType,
            mapping_method=body.mappingMethod,
            proposed_at=_now(),
            external_label=body.externalLabel,
            proposed_by_user_id=_actor(current_user),
            confidence=_decimal(body.confidence),
            evidence=body.evidence,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="External scheme version was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    session.commit()
    return _concept_mapping_state(session, concept)


def _concept_mapping_state(session, concept: SemanticConcept) -> ConceptExternalMappingListResponse:
    """Every mapping the concept holds, whatever its status.

    Not the open queue filtered in Python: that would list only `proposed`
    rows, so a caller who had just rejected a mapping would get a response
    that did not contain it. Section 14.4 keeps the decision history with the
    governed data, and the row just decided is the first thing the caller
    needs back.
    """
    identity = _concept_payload_from_model(session, concept)
    rows = []
    for reference in list_concept_external_references(session, concept.id):
        version, scheme = session.execute(
            select(ExternalSemanticSchemeVersion, ExternalSemanticScheme)
            .join(ExternalSemanticScheme, ExternalSemanticScheme.id == ExternalSemanticSchemeVersion.scheme_id)
            .where(ExternalSemanticSchemeVersion.id == reference.scheme_version_id)
        ).one()
        rows.append(
            {
                "referenceId": str(reference.id),
                "concept": identity,
                "schemeVersionId": str(reference.scheme_version_id),
                "schemeCode": scheme.scheme_code,
                "schemeVersionLabel": version.version_label,
                "externalIdentifier": reference.external_identifier,
                "externalLabel": reference.external_label,
                "mappingType": reference.mapping_type,
                "status": reference.mapping_status,
                "method": reference.mapping_method,
                "confidence": _float(reference.confidence),
                "evidence": reference.evidence_json or {},
                "proposedAt": reference.created_at.isoformat(),
                "capabilities": _capabilities_payload(
                    external_mapping_decision_capabilities(
                        status=reference.mapping_status, method=reference.mapping_method
                    )
                ),
            }
        )
    return ConceptExternalMappingListResponse(items=rows)


@router.post(
    "/external-mappings/{reference_id}/decision",
    response_model=ConceptExternalMappingListResponse,
    responses=_ERROR_RESPONSES,
)
def decide_external_mapping(
    reference_id: uuid.UUID,
    body: ExternalMappingDecisionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> ConceptExternalMappingListResponse:
    reference = session.get(ConceptExternalReference, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="External mapping was not found.")
    concept_id = reference.semantic_concept_id
    try:
        transition_concept_external_reference(
            session,
            reference.id,
            target_status=body.targetStatus,
            occurred_at=_now(),
            reviewed_by_user_id=_actor(current_user),
            verification_basis=body.verificationBasis,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="External mapping was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    session.commit()
    return _concept_mapping_state(session, _concept(session, concept_id))


# ---------------------------------------------------------------------------
# Rights records -- admin or reviewer (decision Q2, WP1.3)
#
# This is the package that makes section 9.2's rights dimension satisfiable at
# all. `publication_gate.propose_intake_rights_record` is the only other
# creator of a `RightsRecord`, and it always writes `ai_assisted`, which
# section 8.4 and the `approved_not_ai_determined` constraint make permanently
# unapprovable. Without a route by which a reviewer proposes their own record,
# every rights record in production is a proposal that can never become an
# approval, and the gate's rights dimension can only ever be waived.
#
# Tenant scope follows WP1.1's split rather than inventing a second rule: a
# symbol-subject record is scoped to the caller, and a package- or
# standard-subject record is a platform-level assertion about a licence, with
# no symbol and no tenant. Withholding the latter would hide exactly the
# records section 9.2 needs approved.
# ---------------------------------------------------------------------------


def _visible_rights_record(session: Session, record_id: uuid.UUID, scope: uuid.UUID | None) -> RightsRecord:
    record = session.get(RightsRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Rights record was not found.")
    if record.symbol_revision_id is not None:
        # Resolves through the same predicate as the queue, and reports an
        # out-of-scope row as absent rather than forbidden.
        _visible_revision(session, record.symbol_revision_id, scope)
    return record


def _rights_record_payload(session: Session, record: RightsRecord) -> dict:
    symbol = None
    if record.symbol_revision_id is not None:
        symbol = session.execute(
            select(GovernedSymbol)
            .join(SymbolRevision, SymbolRevision.symbol_id == GovernedSymbol.id)
            .where(SymbolRevision.id == record.symbol_revision_id)
        ).scalar_one_or_none()
    return {
        "recordId": str(record.id),
        "subjectKind": (
            "symbol_revision"
            if record.symbol_revision_id is not None
            else "source_package"
            if record.source_package_id is not None
            else "standard_version"
        ),
        "symbolRevisionId": str(record.symbol_revision_id) if record.symbol_revision_id else None,
        "symbol": _identity_from_model(symbol) if symbol is not None else None,
        "sourcePackageId": str(record.source_package_id) if record.source_package_id else None,
        "standardVersionId": str(record.standard_version_id) if record.standard_version_id else None,
        "rightsStatus": record.rights_status,
        "disposition": record.disposition,
        "determinationMethod": record.determination_method,
        "licenceReference": record.licence_reference,
        "status": record.decision_status,
        "evidence": record.evidence_json or {},
        "proposedAt": record.created_at.isoformat(),
        "capabilities": _capabilities_payload(
            rights_decision_capabilities(
                status=record.decision_status, determination_method=record.determination_method
            )
        ),
    }


@router.post(
    "/rights-records",
    status_code=201,
    response_model=RightsRecordReviewRowResponse,
    responses=_ERROR_RESPONSES,
)
def propose_rights_record_route(
    body: RightsRecordProposeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> RightsRecordReviewRowResponse:
    """A reviewer's own rights determination -- the half production lacks.

    The record starts `proposed` whatever its method: principle P-07 and
    section 8.4 keep a determination out of the governed record until a
    decision is taken, and that holds for a human's proposal too.
    """
    scope = _scope(current_user)
    if body.symbolRevisionId is not None:
        _visible_revision(session, body.symbolRevisionId, scope)
    try:
        record = propose_rights_record(
            session,
            disposition=body.disposition,
            determination_method=body.determinationMethod,
            proposed_at=_now(),
            rights_status=body.rightsStatus,
            source_package_id=body.sourcePackageId,
            standard_version_id=body.standardVersionId,
            symbol_revision_id=body.symbolRevisionId,
            licence_reference=body.licenceReference,
            decision_reason=body.decisionReason,
            evidence=body.evidence,
            proposed_by_user_id=_actor(current_user),
        )
        session.flush()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Rights record subject was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    payload = _rights_record_payload(session, record)
    session.commit()
    return RightsRecordReviewRowResponse(**payload)


@router.post(
    "/rights-records/{record_id}/decision",
    response_model=RightsRecordReviewRowResponse,
    responses=_ERROR_RESPONSES,
)
def decide_rights_record(
    record_id: uuid.UUID,
    body: RightsRecordDecisionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(reviewer),
) -> RightsRecordReviewRowResponse:
    """Section 7.12's who, when and why.

    A named decider is required unconditionally on an approval, unlike
    section 7.10's verification: there is no controlled-system rights
    decision. `retired` is the exception in the other direction -- the service
    refuses a decider on it, because retirement is a succession rather than a
    judgement about the work, so the actor is passed only where it is a
    decision.
    """
    scope = _scope(current_user)
    record = _visible_rights_record(session, record_id, scope)
    decides = body.targetStatus in {"approved", "rejected"}
    try:
        updated = transition_rights_record(
            session,
            record.id,
            target_status=body.targetStatus,
            occurred_at=_now(),
            decided_by_user_id=_actor(current_user) if decides else None,
            decision_reason=body.decisionReason,
            rights_status=body.rightsStatus,
            licence_reference=body.licenceReference,
        )
        session.flush()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Rights record was not found.") from exc
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    payload = _rights_record_payload(session, updated)
    session.commit()
    return RightsRecordReviewRowResponse(**payload)
