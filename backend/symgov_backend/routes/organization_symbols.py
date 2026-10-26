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
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import (
    get_db_session,
    require_organization_admin,
    require_organization_session,
    require_recent_step_up,
    require_user,
)
from ..models import GovernedSymbol, OrganizationSymbolReviewSubmission, PromotionRequest, SymbolRevision, User
from ..organization_review_policy import ReviewPolicy, get_review_policy, set_review_policy
from ..organization_symbol_drafts import (
    OrganizationSymbolDraftError,
    OrganizationSymbolDraftNotVisible,
    attach_asset,
    create_draft,
    get_draft,
    get_draft_asset,
    list_drafts,
    remove_asset,
    submission_history,
    submit_for_review,
    update_draft,
    withdraw_submission,
)
from ..organization_symbol_review import (
    OrganizationSymbolReviewConflict,
    OrganizationSymbolReviewError,
    OrganizationSymbolReviewNotVisible,
    create_new_draft_revision,
    decide_submission,
    discard_pending_revision,
    set_organization_wide,
)
from ..promotion_requests import (
    PromotionRequestConflict,
    PromotionRequestError,
    PromotionRequestNotVisible,
    get_promotion_request,
    list_promoted_symbols,
    list_promotion_requests,
    list_promotion_review_queue,
    open_promotion_review_case,
    submit_promotion_request,
    withdraw_promotion_request,
)
from ..image_content import UnsafeImageContentError, safe_image_response_headers, validate_stored_image
from ..runtime import download_object_bytes
from ..user_notifications import notify_decided, notify_submitted, notify_withdrawn
from ..schemas import (
    OrganizationSymbolAssetResponse,
    OrganizationSymbolAssetUploadRequest,
    OrganizationSymbolDraftCreateRequest,
    OrganizationSymbolDraftListResponse,
    OrganizationSymbolDraftResponse,
    OrganizationSymbolDraftUpdateRequest,
    OrganizationSymbolHistoryEntry,
    OrganizationSymbolHistoryResponse,
    OrganizationSymbolOrganizationWideRequest,
    OrganizationReviewPolicyResponse,
    OrganizationReviewPolicyUpdateRequest,
    OrganizationSymbolReviewDecisionRequest,
    OrganizationSymbolReviewDecisionResponse,
    OrganizationSymbolRevisionResponse,
    OrganizationSymbolSubmissionRequest,
    OrganizationSymbolSubmissionResponse,
    PromotionRequestListResponse,
    PromotionRequestResponse,
    PromotionRequestSubmitRequest,
    PromotionRequestWithdrawRequest,
    PromotedSymbolListResponse,
    PromotedSymbolResponse,
    PromotionReviewQueueItemResponse,
    PromotionReviewQueueResponse,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(prefix="/organization-symbols", tags=["organization-symbols"])


def organization_symbols_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_symbols_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


def require_workflow_v2(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    """The WP2 endpoints are absent, not forbidden, while the flag is off."""
    if not settings.organization_symbol_workflow_v2_enabled:
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
        pendingSubmissionSubmittedByUserId=(
            str(pending_submission.submitted_by_user_id) if pending_submission is not None else None
        ),
        category=payload.get("category"),
        discipline=payload.get("discipline"),
        authorId=str(revision.author_id) if revision.author_id else None,
    )


def full_draft_response(session: Session, symbol) -> OrganizationSymbolDraftResponse:
    """The symbol with its current revision and, when one is in progress, its pending revision."""
    current = session.get(SymbolRevision, symbol.current_revision_id) if symbol.current_revision_id else None
    pending = session.get(SymbolRevision, symbol.pending_revision_id) if symbol.pending_revision_id else None
    response = _draft_response(symbol, current, _pending_submission(session, current.id) if current is not None else None)
    owner = session.get(User, symbol.owner_id) if symbol.owner_id else None
    response.ownerName = owner.display_name if owner is not None else None
    if pending is not None:
        response.pendingRevisionId = str(pending.id)
        response.pendingRevision = _revision_response(pending, _pending_submission(session, pending.id))
    return response


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
    return OrganizationSymbolDraftListResponse(items=[full_draft_response(session, symbol) for symbol in symbols])


def review_policy_response(policy: ReviewPolicy) -> OrganizationReviewPolicyResponse:
    return OrganizationReviewPolicyResponse(
        organizationId=str(policy.organization_id),
        allowSelfApproval=policy.allow_self_approval,
        updatedAt=policy.updated_at,
        updatedByName=policy.updated_by_name,
    )


# WP7: promotion review usability. Both declared before `/{symbol_id}`.
@router.get(
    "/promotion-review-queue",
    response_model=PromotionReviewQueueResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def get_promotion_review_queue(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_user),
) -> PromotionReviewQueueResponse:
    """Open promotion requests from every organization, for Public Reviewers.
    Open-review and accept stay on their existing routes."""
    try:
        entries = list_promotion_review_queue(session, current_user)
    except PromotionRequestError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = []
    for entry in entries:
        base = _promotion_request_response(entry.request, session=session)
        items.append(
            PromotionReviewQueueItemResponse(
                **base.model_dump(),
                organizationName=entry.organization_name,
                symbolName=entry.symbol.canonical_name,
                slug=entry.symbol.slug,
                category=entry.symbol.category,
                discipline=entry.symbol.discipline,
                revision=_revision_response(entry.revision) if entry.revision is not None else None,
                submittedByName=entry.submitted_by_name,
                canDecide=entry.blocked_reason is None,
                blockedReason=entry.blocked_reason,
            )
        )
    return PromotionReviewQueueResponse(items=items)


@router.get(
    "/promoted",
    response_model=PromotedSymbolListResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def get_promoted_organization_symbols(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> PromotedSymbolListResponse:
    """The organization's symbols that promotion made public, with their S-numbers."""
    try:
        promoted = list_promoted_symbols(session, current_user)
    except PromotionRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PromotedSymbolListResponse(
        items=[
            PromotedSymbolResponse(
                id=str(entry.symbol.id),
                slug=entry.symbol.slug,
                canonicalName=entry.symbol.canonical_name,
                category=entry.symbol.category,
                discipline=entry.symbol.discipline,
                catalogSymbolId=entry.symbol.catalog_symbol_id,
                promotionRequestId=str(entry.request.id),
                promotedAt=entry.request.closed_at,
                promotedFromOrganizationName=entry.organization_name,
            )
            for entry in promoted
        ]
    )


# Declared before `/{symbol_id}` so the literal path is matched first.
@router.get(
    "/review-policy",
    response_model=OrganizationReviewPolicyResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def get_organization_review_policy(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationReviewPolicyResponse:
    """Every member may read it: submitters need to know whether they may decide their own."""
    return review_policy_response(get_review_policy(session, uuid.UUID(current_user.active_organization_id)))


@router.put(
    "/review-policy",
    response_model=OrganizationReviewPolicyResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def put_organization_review_policy(
    body: OrganizationReviewPolicyUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrganizationReviewPolicyResponse:
    policy = set_review_policy(
        session,
        current_user,
        uuid.UUID(current_user.active_organization_id),
        allow_self_approval=body.allowSelfApproval,
        effective_authority="organization_admin",
    )
    session.commit()
    return review_policy_response(policy)


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
    return full_draft_response(session, symbol)


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


@router.get("/{symbol_id}/revisions/{revision_id}/assets/{attachment_id}/preview")
def get_organization_symbol_draft_asset_preview(
    symbol_id: str,
    revision_id: str,
    attachment_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> Response:
    """The stored image for one asset of a private symbol, for the Private
    review queue and the submitter's own Submissions view."""
    not_found = HTTPException(status_code=404, detail="Organization symbol asset was not found.")
    try:
        attachment = get_draft_asset(
            session,
            current_user,
            symbol_id=_parse_uuid(symbol_id),
            revision_id=_parse_uuid(revision_id),
            attachment_id=_parse_uuid(attachment_id),
        )
    except OrganizationSymbolDraftNotVisible as exc:
        raise not_found from exc
    stored = download_object_bytes(object_key=attachment.object_key, env_file=str(settings.storage_env_file))
    try:
        media_type = validate_stored_image(stored["payload"], attachment.content_type, stored.get("content_type"))
    except UnsafeImageContentError as exc:
        raise not_found from exc
    return Response(
        content=stored["payload"],
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", **safe_image_response_headers()},
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
    settings: SymgovAPISettings = Depends(get_settings),
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
    if settings.organization_symbol_workflow_v2_enabled:
        notify_submitted(session, submission)
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
    settings: SymgovAPISettings = Depends(get_settings),
) -> OrganizationSymbolReviewDecisionResponse:
    parsed_submission_id = _parse_uuid(submission_id)
    try:
        decision = decide_submission(
            session,
            current_user,
            submission_id=parsed_submission_id,
            decision=body.decision,
            rationale=body.rationale,
            workflow_v2=settings.organization_symbol_workflow_v2_enabled,
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
    if settings.organization_symbol_workflow_v2_enabled:
        notify_decided(session, decision)
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
    settings: SymgovAPISettings = Depends(get_settings),
) -> OrganizationSymbolDraftResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    workflow_v2 = settings.organization_symbol_workflow_v2_enabled
    try:
        symbol, revision = create_new_draft_revision(
            session, current_user, symbol_id=parsed_symbol_id, workflow_v2=workflow_v2
        )
    except OrganizationSymbolReviewNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    if workflow_v2:
        return full_draft_response(session, symbol)
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


# --- Unified submission & review WP2: edit, withdraw, history, discard ---

@router.patch(
    "/{symbol_id}/revisions/{revision_id}",
    response_model=OrganizationSymbolDraftResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def update_organization_symbol_draft(
    symbol_id: str,
    revision_id: str,
    body: OrganizationSymbolDraftUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    parsed_revision_id = _parse_uuid(revision_id)
    try:
        symbol, _ = update_draft(
            session,
            current_user,
            symbol_id=parsed_symbol_id,
            revision_id=parsed_revision_id,
            **body.model_dump(exclude_unset=True),
        )
    except OrganizationSymbolDraftNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return full_draft_response(session, symbol)


@router.delete(
    "/{symbol_id}/revisions/{revision_id}/assets/{attachment_id}",
    response_model=OrganizationSymbolDraftResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def remove_organization_symbol_draft_asset(
    symbol_id: str,
    revision_id: str,
    attachment_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    parsed_symbol_id = _parse_uuid(symbol_id)
    try:
        remove_asset(
            session,
            current_user,
            symbol_id=parsed_symbol_id,
            revision_id=_parse_uuid(revision_id),
            attachment_id=_parse_uuid(attachment_id),
        )
    except OrganizationSymbolDraftNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return full_draft_response(session, get_draft(session, current_user, parsed_symbol_id))


@router.post(
    "/{symbol_id}/review-submissions/{submission_id}/withdraw",
    response_model=OrganizationSymbolSubmissionResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def withdraw_organization_symbol_review_submission(
    symbol_id: str,
    submission_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolSubmissionResponse:
    try:
        submission = withdraw_submission(
            session, current_user, symbol_id=_parse_uuid(symbol_id), submission_id=_parse_uuid(submission_id)
        )
    except OrganizationSymbolDraftNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization review submission was not found.") from exc
    except OrganizationSymbolDraftError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notify_withdrawn(session, submission)  # the route itself exists only under the v2 flag
    session.commit()
    return OrganizationSymbolSubmissionResponse(
        id=str(submission.id),
        organizationId=str(submission.organization_id),
        governedSymbolId=str(submission.governed_symbol_id),
        symbolRevisionId=str(submission.symbol_revision_id),
        submittedByUserId=str(submission.submitted_by_user_id),
        submittedAt=submission.submitted_at,
        status=submission.status,
        closureReason=submission.closure_reason,
    )


@router.get(
    "/{symbol_id}/history",
    response_model=OrganizationSymbolHistoryResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def organization_symbol_review_history(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolHistoryResponse:
    try:
        entries = submission_history(session, current_user, _parse_uuid(symbol_id))
    except OrganizationSymbolDraftNotVisible as exc:
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    return history_response(entries)


def history_response(entries) -> OrganizationSymbolHistoryResponse:
    return OrganizationSymbolHistoryResponse(
        items=[
            OrganizationSymbolHistoryEntry(
                submissionId=str(entry.submission.id),
                revisionId=str(entry.submission.symbol_revision_id),
                revisionLabel=entry.revision.revision_label if entry.revision is not None else "",
                submittedByUserId=str(entry.submission.submitted_by_user_id),
                submittedByName=entry.submitted_by_name,
                submittedAt=entry.submission.submitted_at,
                submissionRationale=entry.submission.rationale,
                outcome=entry.outcome,
                closedAt=entry.submission.closed_at,
                decidedByUserId=str(entry.decision.decided_by_user_id) if entry.decision is not None else None,
                decidedByName=entry.decided_by_name,
                decisionRationale=entry.decision.rationale if entry.decision is not None else None,
            )
            for entry in entries
        ]
    )


@router.delete(
    "/{symbol_id}/pending-revision",
    response_model=OrganizationSymbolDraftResponse,
    dependencies=[Depends(require_workflow_v2)],
)
def discard_organization_symbol_pending_revision(
    symbol_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrganizationSymbolDraftResponse:
    try:
        symbol = discard_pending_revision(session, current_user, symbol_id=_parse_uuid(symbol_id))
    except OrganizationSymbolReviewNotVisible as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Organization symbol draft was not found.") from exc
    except OrganizationSymbolReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return full_draft_response(session, symbol)


# --- Stage 7 WP7.2/WP7.3: promotion requests ---

def _promotion_request_response(request, session: Session | None = None) -> PromotionRequestResponse:
    # Stage 9 WP9.6: this is the one existing surface a reviewer actually
    # sees a promotion request's own detail through today (via
    # open_organization_symbol_promotion_review) -- there is no separate
    # workspace review-case renderer for organization_symbol_promotion
    # cases (see organization_promotion_handoff.py's own module docstring).
    possible_duplicate_slug = None
    if session is not None and request.possible_duplicate_governed_symbol_id is not None:
        duplicate_symbol = session.get(GovernedSymbol, request.possible_duplicate_governed_symbol_id)
        possible_duplicate_slug = duplicate_symbol.slug if duplicate_symbol is not None else None
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
        possibleDuplicateOfGovernedSymbolId=(
            str(request.possible_duplicate_governed_symbol_id) if request.possible_duplicate_governed_symbol_id else None
        ),
        possibleDuplicateOfSlug=possible_duplicate_slug,
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
    return _promotion_request_response(request, session=session)


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
    return PromotionRequestListResponse(items=[_promotion_request_response(r, session=session) for r in requests])


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
    return _promotion_request_response(request, session=session)


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
    return _promotion_request_response(request, session=session)


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
    return _promotion_request_response(request, session=session)
