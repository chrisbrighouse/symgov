"""WP5.3/WP5.4 — organization-private symbol draft/revision/intake/asset API,
plus the organization review lifecycle (approve/reject/request-changes,
new-draft-revision-after-mutation, and organization-wide toggling).

Mounted behind the `organizations_enabled` and `organization_symbols_enabled`
feature flags (both default off — see `settings.py`), per the Stage 5 plan's
"roll out schema-first with organization-symbol flags off" instruction.
"""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_organization_admin, require_organization_session, require_user
from ..models import OrganizationSymbolReviewSubmission, PromotionRequest, SymbolRevision
from ..organization_symbol_drafts import (
    OrganizationSymbolDraftError,
    OrganizationSymbolDraftNotVisible,
    attach_asset,
    create_draft,
    get_draft,
    get_draft_revision,
    list_drafts,
    submit_for_review,
)
from ..organization_symbol_review import (
    OrganizationSymbolReviewConflict,
    OrganizationSymbolReviewError,
    OrganizationSymbolReviewNotVisible,
    create_new_draft_revision,
    decide_submission,
    set_organization_wide,
)
from ..promotion_requests import (
    PromotionRequestConflict,
    PromotionRequestError,
    PromotionRequestNotVisible,
    get_promotion_request,
    list_promotion_requests,
    open_promotion_review_case,
    submit_promotion_request,
    withdraw_promotion_request,
)
from ..schemas import (
    OrganizationSymbolAssetResponse,
    OrganizationSymbolAssetUploadRequest,
    OrganizationSymbolDraftCreateRequest,
    OrganizationSymbolDraftListResponse,
    OrganizationSymbolDraftResponse,
    OrganizationSymbolOrganizationWideRequest,
    OrganizationSymbolReviewDecisionRequest,
    OrganizationSymbolReviewDecisionResponse,
    OrganizationSymbolRevisionResponse,
    OrganizationSymbolSubmissionRequest,
    OrganizationSymbolSubmissionResponse,
    PromotionRequestListResponse,
    PromotionRequestResponse,
    PromotionRequestSubmitRequest,
    PromotionRequestWithdrawRequest,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(prefix="/organization-symbols", tags=["organization-symbols"])


def organization_symbols_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_symbols_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


def _parse_uuid(value: str, *, detail: str = "Not found.") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


def _pending_submission(session: Session, revision_id: uuid.UUID) -> OrganizationSymbolReviewSubmission | None:
    return session.execute(
        select(OrganizationSymbolReviewSubmission).where(
            OrganizationSymbolReviewSubmission.symbol_revision_id == revision_id,
            OrganizationSymbolReviewSubmission.status == "active",
        )
    ).scalars().first()


def _revision_response(
    revision, pending_submission: OrganizationSymbolReviewSubmission | None = None
) -> OrganizationSymbolRevisionResponse | None:
    if revision is None:
        return None
    payload = revision.payload_json or {}
    return OrganizationSymbolRevisionResponse(
        id=str(revision.id),
        revisionLabel=revision.revision_label,
        lifecycleState=revision.lifecycle_state,
        name=str(payload.get("name") or ""),
        summary=str(payload.get("summary") or ""),
        description=payload.get("description"),
        aliases=list(payload.get("aliases") or []),
        keywords=list(payload.get("keywords") or []),
        assets=[
            OrganizationSymbolAssetResponse(
                id=str(asset.get("attachment_id")),
                objectKey=str(asset.get("object_key")),
                filename=str(asset.get("filename")),
                contentType=str(asset.get("content_type")),
                role=str(asset.get("role") or "source"),
                sha256=str(asset.get("sha256") or ""),
                sizeBytes=int(asset.get("size_bytes") or 0),
            )
            for asset in (payload.get("assets") or [])
        ],
        createdAt=revision.created_at,
        pendingSubmissionId=str(pending_submission.id) if pending_submission is not None else None,
        pendingSubmissionRationale=pending_submission.rationale if pending_submission is not None else None,
        pendingSubmissionSubmittedAt=pending_submission.submitted_at if pending_submission is not None else None,
    )


def _draft_response(
    symbol, current_revision=None, pending_submission: OrganizationSymbolReviewSubmission | None = None
) -> OrganizationSymbolDraftResponse:
    return OrganizationSymbolDraftResponse(
        id=str(symbol.id),
        slug=symbol.slug,
        canonicalName=symbol.canonical_name,
        category=symbol.category,
        discipline=symbol.discipline,
        visibility=symbol.visibility,
        organizationWide=bool(symbol.organization_wide),
        organizationId=str(symbol.owner_organization_id),
        ownerId=str(symbol.owner_id),
        currentRevisionId=str(symbol.current_revision_id) if symbol.current_revision_id else None,
        currentRevision=_revision_response(current_revision, pending_submission),
        createdAt=symbol.created_at,
        updatedAt=symbol.updated_at,
    )


@router.post("", response_model=OrganizationSymbolDraftResponse)
def create_organization_symbol_draft(
    body: OrganizationSymbolDraftCreateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    try:
        symbol, revision = create_draft(
            session,
            current_user,
            name=body.name,
            category=body.category,
            discipline=body.discipline,
            summary=body.summary,
            description=body.description,
            aliases=body.aliases,
            keywords=body.keywords,
        )
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _draft_response(symbol, revision)


@router.get("", response_model=OrganizationSymbolDraftListResponse)
def list_organization_symbol_drafts(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftListResponse:
    symbols = list_drafts(session, current_user)
    items = []
    for symbol in symbols:
        revision = session.get(SymbolRevision, symbol.current_revision_id) if symbol.current_revision_id else None
        pending_submission = _pending_submission(session, revision.id) if revision is not None else None
        items.append(_draft_response(symbol, revision, pending_submission))
    return OrganizationSymbolDraftListResponse(items=items)


@router.get("/{symbol_id}", response_model=OrganizationSymbolDraftResponse)
def get_organization_symbol_draft(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    parsed_id = _parse_uuid(symbol_id)
    try:
        symbol = get_draft(session, current_user, parsed_id)
    except OrganizationSymbolDraftNotVisible as exc:
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    current_revision = None
    pending_submission = None
    if symbol.current_revision_id is not None:
        _, current_revision = get_draft_revision(session, current_user, symbol.id, symbol.current_revision_id)
        pending_submission = _pending_submission(session, current_revision.id)
    return _draft_response(symbol, current_revision, pending_submission)


@router.post(
    "/{symbol_id}/revisions/{revision_id}/assets",
    response_model=OrganizationSymbolAssetResponse,
)
def attach_organization_symbol_draft_asset(
    symbol_id: str,
    revision_id: str,
    body: OrganizationSymbolAssetUploadRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> OrganizationSymbolAssetResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    parsed_revision_id = _parse_uuid(revision_id)
    try:
        payload = base64.b64decode(body.contentBase64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="File content is not valid base64.") from exc

    try:
        upload = attach_asset(
            session,
            current_user,
            symbol_id=parsed_symbol_id,
            revision_id=parsed_revision_id,
            filename=body.filename,
            declared_content_type=body.contentType,
            payload=payload,
            storage_env_file=str(settings.storage_env_file),
            role=body.role,
        )
    except OrganizationSymbolDraftNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return OrganizationSymbolAssetResponse(
        id=str(upload.id),
        objectKey=upload.object_key,
        filename=upload.filename,
        contentType=upload.content_type,
        role=body.role,
        sha256=upload.sha256,
        sizeBytes=upload.size_bytes,
    )


@router.post(
    "/{symbol_id}/revisions/{revision_id}/submit",
    response_model=OrganizationSymbolSubmissionResponse,
)
def submit_organization_symbol_draft_for_review(
    symbol_id: str,
    revision_id: str,
    body: OrganizationSymbolSubmissionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolSubmissionResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    parsed_revision_id = _parse_uuid(revision_id)
    try:
        submission = submit_for_review(
            session,
            current_user,
            symbol_id=parsed_symbol_id,
            revision_id=parsed_revision_id,
            rationale=body.rationale,
        )
    except OrganizationSymbolDraftNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return OrganizationSymbolSubmissionResponse(
        id=str(submission.id),
        organizationId=str(submission.organization_id),
        governedSymbolId=str(submission.governed_symbol_id),
        symbolRevisionId=str(submission.symbol_revision_id),
        submittedByUserId=str(submission.submitted_by_user_id),
        submittedAt=submission.submitted_at,
        status=submission.status,
    )


@router.post(
    "/{symbol_id}/review-submissions/{submission_id}/decision",
    response_model=OrganizationSymbolReviewDecisionResponse,
)
def decide_organization_symbol_review_submission(
    symbol_id: str,
    submission_id: str,
    body: OrganizationSymbolReviewDecisionRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolReviewDecisionResponse:
    parsed_submission_id = _parse_uuid(submission_id)
    try:
        decision = decide_submission(
            session,
            current_user,
            submission_id=parsed_submission_id,
            decision=body.decision,
            rationale=body.rationale,
        )
    except OrganizationSymbolReviewNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization review submission was not found.") from exc
    except OrganizationSymbolReviewConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OrganizationSymbolReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(decision.governed_symbol_id) != symbol_id:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization review submission was not found.")
    session.commit()
    return OrganizationSymbolReviewDecisionResponse(
        id=str(decision.id),
        submissionId=str(decision.submission_id),
        organizationId=str(decision.organization_id),
        governedSymbolId=str(decision.governed_symbol_id),
        symbolRevisionId=str(decision.symbol_revision_id),
        decidedByUserId=str(decision.decided_by_user_id),
        decision=decision.decision,
        rationale=decision.rationale,
        decidedAt=decision.decided_at,
    )


@router.post(
    "/{symbol_id}/revisions",
    response_model=OrganizationSymbolDraftResponse,
)
def create_organization_symbol_draft_revision(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        symbol, revision = create_new_draft_revision(session, current_user, symbol_id=parsed_symbol_id)
    except OrganizationSymbolReviewNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _draft_response(symbol, revision)


@router.post(
    "/{symbol_id}/organization-wide",
    response_model=OrganizationSymbolDraftResponse,
)
def set_organization_symbol_organization_wide(
    symbol_id: str,
    body: OrganizationSymbolOrganizationWideRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        symbol = set_organization_wide(session, current_user, symbol_id=parsed_symbol_id, enabled=body.enabled)
    except OrganizationSymbolReviewNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _draft_response(symbol)


# --- Stage 7 WP7.2/WP7.3: promotion requests ---

def _promotion_request_response(request) -> PromotionRequestResponse:
    return PromotionRequestResponse(
        id=str(request.id),
        governedSymbolId=str(request.governed_symbol_id),
        organizationId=str(request.organization_id),
        symbolRevisionId=str(request.symbol_revision_id),
        status=request.status,
        proposedMetadata=dict(request.proposed_metadata_json or {}),
        reason=request.reason,
        sharingAcknowledgment=bool(request.sharing_acknowledgment),
        submittedByUserId=str(request.submitted_by_user_id),
        submittedAt=request.submitted_at,
        closedAt=request.closed_at,
        traceId=request.trace_id,
        reviewCaseId=str(request.review_case_id) if request.review_case_id else None,
    )


@router.post(
    "/{symbol_id}/promotion-requests",
    response_model=PromotionRequestResponse,
)
def submit_organization_symbol_promotion_request(
    symbol_id: str,
    body: PromotionRequestSubmitRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> PromotionRequestResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        request = submit_promotion_request(
            session,
            current_user,
            symbol_id=parsed_symbol_id,
            reason=body.reason,
            sharing_acknowledgment=body.sharingAcknowledgment,
            proposed_metadata=body.proposedMetadata,
            trace_id=body.traceId,
        )
    except PromotionRequestNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol was not found.") from exc
    except PromotionRequestConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PromotionRequestError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _promotion_request_response(request)


@router.get(
    "/{symbol_id}/promotion-requests",
    response_model=PromotionRequestListResponse,
)
def list_organization_symbol_promotion_requests(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> PromotionRequestListResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        requests = list_promotion_requests(session, current_user, symbol_id=parsed_symbol_id)
    except PromotionRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PromotionRequestListResponse(items=[_promotion_request_response(r) for r in requests])


@router.get(
    "/{symbol_id}/promotion-requests/{request_id}",
    response_model=PromotionRequestResponse,
)
def get_organization_symbol_promotion_request(
    symbol_id: str,
    request_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> PromotionRequestResponse:
    parsed_request_id = _parse_uuid(request_id)
    try:
        request = get_promotion_request(session, current_user, parsed_request_id)
    except PromotionRequestNotVisible as exc:
        raise HTTPException(status_code=404, detail="Promotion request was not found.") from exc
    except PromotionRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(request.governed_symbol_id) != symbol_id:
        raise HTTPException(status_code=404, detail="Promotion request was not found.")
    return _promotion_request_response(request)


@router.post(
    "/{symbol_id}/promotion-requests/{request_id}/withdraw",
    response_model=PromotionRequestResponse,
)
def withdraw_organization_symbol_promotion_request(
    symbol_id: str,
    request_id: str,
    body: PromotionRequestWithdrawRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> PromotionRequestResponse:
    parsed_request_id = _parse_uuid(request_id)
    try:
        request = withdraw_promotion_request(session, current_user, request_id=parsed_request_id, note=body.note)
    except PromotionRequestNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Promotion request was not found.") from exc
    except PromotionRequestConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PromotionRequestError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(request.governed_symbol_id) != symbol_id:
        session.rollback()
        raise HTTPException(status_code=404, detail="Promotion request was not found.")
    session.commit()
    return _promotion_request_response(request)


@router.post(
    "/{symbol_id}/promotion-requests/{request_id}/open-review",
    response_model=PromotionRequestResponse,
)
def open_organization_symbol_promotion_review(
    symbol_id: str,
    request_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_user),
) -> PromotionRequestResponse:
    """Reviewer-facing (global `admin`/`reviewer` role, not organization
    membership -- see `promotion_requests._require_reviewer_authority`):
    opens the `ReviewCase` a reviewer then decides via the existing
    `POST /workspace/review-cases/{id}/decisions` endpoint."""
    parsed_request_id = _parse_uuid(request_id)
    try:
        review_case = open_promotion_review_case(session, current_user, request_id=parsed_request_id)
    except PromotionRequestNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Promotion request was not found.") from exc
    except PromotionRequestConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PromotionRequestError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    request = session.get(PromotionRequest, review_case.source_entity_id)
    if request is None or str(request.governed_symbol_id) != symbol_id:
        session.rollback()
        raise HTTPException(status_code=404, detail="Promotion request was not found.")
    session.commit()
    return _promotion_request_response(request)
