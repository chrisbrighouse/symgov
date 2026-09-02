"""Stage 7 WP7.2 -- public promotion request submission and withdrawal.

Per the programme plan §13 tasks 1-2/6 and the Stage 7 plan's §4 decisions:

- Q1: promotion later drives the existing `ReviewCase`/
  `execute_publication_handoff` pipeline (WP7.3); this module only owns the
  dedicated submission/decision-log model and the submission/withdrawal
  actions, per I-10's "does not overload public `ReviewCase`" instruction.
- Q2: only an Organization Admin may submit a promotion request.
- Q3: one active (non-terminal) promotion request per governed symbol,
  enforced by the DB-level unique partial index
  `uq_promotion_requests_active_symbol` (mirroring
  `OrganizationSymbolReviewSubmission`'s own active-uniqueness pattern) --
  this module translates that DB-level conflict into a clear domain error,
  it does not re-implement the invariant in application logic.

Eligibility mirrors `organization_symbol_review.set_organization_wide`'s own
"current revision has an approved, closed organization review decision"
check (the same predicate the WP5.1 `trg_governed_symbols_organization_wide_eligibility`
trigger enforces for the organization-wide toggle) -- a symbol cannot be
submitted for public promotion unless its current revision already cleared
organization review, per FR-PUB-001.

Locks the governed-symbol row (`with_for_update=True`) before submission,
the same lock `symbol_set_service.py`'s set-item writers already take
before mutating `SymbolSetItem` rows -- this is the shared serialization
boundary the Stage 7 plan's §1.4 identifies as already existing and
required for WP7.4's demotion eligibility check to be race-safe against a
concurrent submission.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .models import (
    GovernedSymbol,
    OrganizationSymbolReviewDecision,
    OrganizationSymbolReviewSubmission,
    PromotionRequest,
    PromotionRequestDecision,
)

OPEN_STATUSES = ("submitted", "triage", "in_review", "changes_requested")
TERMINAL_STATUSES = ("accepted", "rejected", "withdrawn")


class PromotionRequestError(ValueError):
    """Domain validation failure -- the route maps this to HTTP 400."""


class PromotionRequestNotVisible(LookupError):
    """The symbol/request does not exist, or the actor cannot see it.

    Deliberately does not distinguish "not found" from "not authorized",
    mirroring `OrganizationSymbolReviewNotVisible`, so an unauthorized actor
    cannot enumerate or infer another organization's promotion state. The
    route maps this to HTTP 404.
    """


class PromotionRequestConflict(RuntimeError):
    """An active request already exists, or this request already closed.

    The route maps this to HTTP 409.
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _active_organization_id(current_user: AuthenticatedUser) -> uuid.UUID:
    if current_user.session_mode != "organization" or current_user.active_organization_id is None:
        raise PromotionRequestError("An organization-bound session is required.")
    return uuid.UUID(current_user.active_organization_id)


def _require_organization_admin(current_user: AuthenticatedUser) -> None:
    """Defense-in-depth: the route also gates both actions behind the
    `require_organization_admin` dependency (Q2 -- Organization Admin
    only), but this module is called directly by tests and, later,
    other services, so it must not rely solely on the route layer for
    authorization, mirroring `organization_symbol_review.py`'s own
    `_require_reviewer`/`_require_organization_wide_toggle_authority`
    convention."""
    if current_user.organization_base_role != "admin":
        raise PromotionRequestError("Organization Admin privileges are required.")


def _clean_text(value: str | None, *, field: str, required: bool = True) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise PromotionRequestError(f"{field} must not be blank.")
        return None
    if len(cleaned) > 2000:
        raise PromotionRequestError(f"{field} must be 2000 characters or fewer.")
    return cleaned


def _has_approved_closed_decision(session: Session, *, organization_id: uuid.UUID, symbol_id: uuid.UUID, revision_id: uuid.UUID) -> bool:
    """Mirrors `validate_governed_symbol_organization_wide_eligibility`'s
    predicate (20260829_0033_organization_symbol_visibility.py) -- the
    current revision must carry an approved, closed organization review
    decision."""
    return (
        session.execute(
            select(OrganizationSymbolReviewDecision.id)
            .join(
                OrganizationSymbolReviewSubmission,
                OrganizationSymbolReviewSubmission.id == OrganizationSymbolReviewDecision.submission_id,
            )
            .where(
                OrganizationSymbolReviewDecision.organization_id == organization_id,
                OrganizationSymbolReviewDecision.governed_symbol_id == symbol_id,
                OrganizationSymbolReviewDecision.symbol_revision_id == revision_id,
                OrganizationSymbolReviewDecision.decision == "approved",
                OrganizationSymbolReviewSubmission.status == "closed",
            )
        ).first()
        is not None
    )


def submit_promotion_request(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    reason: str,
    sharing_acknowledgment: bool,
    proposed_metadata: dict | None = None,
    trace_id: str | None = None,
) -> PromotionRequest:
    organization_id = _active_organization_id(current_user)
    _require_organization_admin(current_user)

    if not sharing_acknowledgment:
        raise PromotionRequestError(
            "Explicit acknowledgment that the contribution is shared freely with the Symgov community is required."
        )
    clean_reason = _clean_text(reason, field="reason")

    symbol = session.get(GovernedSymbol, symbol_id, with_for_update=True)
    if symbol is None or symbol.owner_organization_id != organization_id or symbol.visibility != "organization_private":
        raise PromotionRequestNotVisible()
    if symbol.current_revision_id is None:
        raise PromotionRequestError("The symbol has no current revision to submit for public promotion.")

    revision_id = symbol.current_revision_id
    if not _has_approved_closed_decision(session, organization_id=organization_id, symbol_id=symbol.id, revision_id=revision_id):
        raise PromotionRequestError(
            "The current revision must have an approved, closed organization review decision before it can be submitted for public promotion."
        )

    now = _utc_now()
    request = PromotionRequest(
        id=uuid.uuid4(),
        governed_symbol_id=symbol.id,
        organization_id=organization_id,
        symbol_revision_id=revision_id,
        status="submitted",
        proposed_metadata_json=dict(proposed_metadata or {}),
        reason=clean_reason,
        sharing_acknowledgment=True,
        submitted_by_user_id=uuid.UUID(current_user.id),
        submitted_at=now,
        trace_id=trace_id,
        created_at=now,
        updated_at=now,
    )
    session.add(request)
    try:
        session.flush()
    except IntegrityError as exc:
        raise PromotionRequestConflict(
            "This symbol already has an active public promotion request."
        ) from exc
    return request


def get_promotion_request(session: Session, current_user: AuthenticatedUser, request_id: uuid.UUID) -> PromotionRequest:
    organization_id = _active_organization_id(current_user)
    request = session.get(PromotionRequest, request_id)
    if request is None or request.organization_id != organization_id:
        raise PromotionRequestNotVisible()
    return request


def list_promotion_requests(session: Session, current_user: AuthenticatedUser, *, symbol_id: uuid.UUID | None = None) -> list[PromotionRequest]:
    organization_id = _active_organization_id(current_user)
    stmt = select(PromotionRequest).where(PromotionRequest.organization_id == organization_id)
    if symbol_id is not None:
        stmt = stmt.where(PromotionRequest.governed_symbol_id == symbol_id)
    stmt = stmt.order_by(PromotionRequest.submitted_at.desc(), PromotionRequest.id)
    return list(session.execute(stmt).scalars().all())


def withdraw_promotion_request(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    request_id: uuid.UUID,
    note: str | None = None,
) -> PromotionRequest:
    organization_id = _active_organization_id(current_user)
    _require_organization_admin(current_user)

    request = session.get(PromotionRequest, request_id, with_for_update=True)
    if request is None or request.organization_id != organization_id:
        raise PromotionRequestNotVisible()
    if request.status not in OPEN_STATUSES:
        raise PromotionRequestConflict("This promotion request has already reached a terminal state.")

    clean_note = _clean_text(note, field="note", required=False)
    now = _utc_now()
    from_status = request.status

    decision = PromotionRequestDecision(
        id=uuid.uuid4(),
        promotion_request_id=request.id,
        decision_code="withdrawn",
        from_status=from_status,
        to_status="withdrawn",
        decided_by_user_id=uuid.UUID(current_user.id),
        decider_name=current_user.display_name,
        decider_role="organization_admin",
        note=clean_note,
        created_at=now,
    )
    session.add(decision)

    request.status = "withdrawn"
    request.closed_at = now
    request.updated_at = now
    session.flush()
    return request
