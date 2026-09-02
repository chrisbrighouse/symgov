"""WP5.4 — organization review lifecycle (approve/reject/request-changes).

Per the Stage 5 implementation plan (`docs/plans/2026-09-01-symbol-set-management-stage5-implementation-plan.md`,
§4) and the programme plan §11:

- Only an active member with the explicit `symbol_reviewer` capability may
  decide a submission; Organization Admin status alone is insufficient
  (an admin may separately hold the capability). Actor derives from
  session.
- A decision is revision-specific and has no public-visibility side
  effect.
- Mutation after approval requires a fresh draft revision (a new review
  cycle), not an edit of the approved revision in place.
- Organization-wide scope can be enabled only for a current revision with
  an approved, closed decision — enforced by WP5.1's
  `trg_governed_symbols_organization_wide_eligibility` deferred trigger;
  this module only adds the actor-authorization layer around it.

WP5.1's schema already carries the hard concurrency guarantees this
package's plan note calls out: `OrganizationSymbolReviewDecision.submission_id`
is unique (a second decision on the same submission is a database-level
conflict, not something this module has to re-implement), and
`serialize_organization_symbol_review_binding` advisory-locks per governed
symbol before any submission/decision write. This module's job is to
authorize the actor and translate those DB-level failures into clear
domain errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .models import GovernedSymbol, OrganizationSymbolReviewDecision, OrganizationSymbolReviewSubmission, SymbolRevision

DECISIONS = ("approved", "rejected", "changes_requested")


class OrganizationSymbolReviewError(ValueError):
    """Domain validation failure — the route maps this to HTTP 400."""


class OrganizationSymbolReviewNotVisible(LookupError):
    """The submission/symbol does not exist, or the actor cannot see it.

    Mirrors `organization_symbol_drafts.OrganizationSymbolDraftNotVisible`:
    deliberately does not distinguish "not found" from "not authorized" so
    an unauthorized actor cannot enumerate or infer private review state.
    The route maps this to HTTP 404.
    """


class OrganizationSymbolReviewConflict(RuntimeError):
    """The submission was already decided (stale-decision conflict).

    The route maps this to HTTP 409.
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _active_organization_id(current_user: AuthenticatedUser) -> uuid.UUID:
    if current_user.session_mode != "organization" or current_user.active_organization_id is None:
        raise OrganizationSymbolReviewError("An organization-bound session is required.")
    return uuid.UUID(current_user.active_organization_id)


def _require_reviewer(current_user: AuthenticatedUser) -> None:
    if "symbol_reviewer" not in current_user.organization_capabilities:
        raise OrganizationSymbolReviewError(
            "The 'symbol_reviewer' capability is required to decide an organization review submission."
        )


def _require_organization_wide_toggle_authority(current_user: AuthenticatedUser) -> None:
    """Stage 6 WP6.3 (confirmed with Chris 2026-09-01): Organization Admin
    or an active `symbol_reviewer` may toggle `organization_wide` --
    broadened from the WP5.4/Stage 5 admin-only gate now that the toggle
    is reachable from a frontend surface, on the reasoning that a
    reviewer who is already trusted to approve a revision is equally
    trusted to decide whether it becomes organization-wide."""
    if current_user.organization_base_role != "admin" and "symbol_reviewer" not in current_user.organization_capabilities:
        raise OrganizationSymbolReviewError(
            "Organization Admin privileges or the 'symbol_reviewer' capability are required to change organization-wide scope."
        )


def _clean_rationale(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > 2000:
        raise OrganizationSymbolReviewError("rationale must be 2000 characters or fewer.")
    return text


def decide_submission(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    submission_id: uuid.UUID,
    decision: str,
    rationale: str | None = None,
) -> OrganizationSymbolReviewDecision:
    organization_id = _active_organization_id(current_user)
    _require_reviewer(current_user)

    if decision not in DECISIONS:
        raise OrganizationSymbolReviewError(f"decision must be one of {DECISIONS}.")

    submission = session.get(OrganizationSymbolReviewSubmission, submission_id)
    if submission is None or submission.organization_id != organization_id:
        raise OrganizationSymbolReviewNotVisible()
    if submission.status != "active":
        raise OrganizationSymbolReviewConflict(
            "This submission has already been decided by another reviewer."
        )

    clean_rationale = _clean_rationale(rationale)
    now = _utc_now()

    decision_row = OrganizationSymbolReviewDecision(
        id=uuid.uuid4(),
        submission_id=submission.id,
        organization_id=submission.organization_id,
        governed_symbol_id=submission.governed_symbol_id,
        symbol_revision_id=submission.symbol_revision_id,
        decided_by_user_id=uuid.UUID(current_user.id),
        decision=decision,
        rationale=clean_rationale,
        decided_at=now,
    )
    session.add(decision_row)
    submission.status = "closed"
    submission.closed_at = now

    try:
        session.flush()
    except IntegrityError as exc:
        # The database-level unique(submission_id) constraint is the
        # authoritative stale-decision guard for a race between two
        # reviewers; this is the expected shape of that race losing.
        raise OrganizationSymbolReviewConflict(
            "This submission has already been decided by another reviewer."
        ) from exc

    revision = session.get(SymbolRevision, submission.symbol_revision_id)
    if revision is not None:
        revision.lifecycle_state = "approved" if decision == "approved" else "draft"
    session.flush()
    return decision_row


def create_new_draft_revision(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
) -> tuple[GovernedSymbol, SymbolRevision]:
    """Start a fresh review cycle after approval (or after changes/rejection).

    Per the plan, mutation after approval must not edit the approved
    revision in place — it must create a new draft revision that requires
    a new review. Clears `organization_wide` when set, since eligibility
    requires the *current* revision to carry an approved decision, and the
    new draft revision does not yet have one.
    """
    organization_id = _active_organization_id(current_user)
    symbol = session.get(GovernedSymbol, symbol_id)
    if (
        symbol is None
        or symbol.owner_organization_id != organization_id
        or symbol.visibility != "organization_private"
    ):
        raise OrganizationSymbolReviewNotVisible()
    if symbol.owner_id != uuid.UUID(current_user.id) and current_user.organization_base_role != "admin":
        raise OrganizationSymbolReviewNotVisible()

    base_revision = session.get(SymbolRevision, symbol.current_revision_id) if symbol.current_revision_id else None
    if base_revision is not None and base_revision.lifecycle_state not in ("approved", "draft", "withdrawn"):
        # 'review' means an active submission still exists for the current
        # revision — creating a parallel draft would orphan it. 'published'
        # is not an organization-private lifecycle state (a currently
        # public revision cannot be the symbol's own base for a new
        # organization-private draft). 'withdrawn' *is* allowed: Stage 7
        # demotion (symbol_demotion.py) sets the formerly published
        # revision's lifecycle_state to 'withdrawn' while flipping the
        # symbol back to organization_private in the same transaction --
        # re-promotion (programme plan §13) requires starting a fresh draft
        # revision from that point, not editing the withdrawn one in place.
        raise OrganizationSymbolReviewError(
            "Cannot start a new draft revision while the current revision is still under review."
        )

    now = _utc_now()
    payload_json = dict((base_revision.payload_json if base_revision is not None else {}) or {})
    new_revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label=f"draft-{now.date().isoformat()}-{uuid.uuid4().hex[:8]}",
        lifecycle_state="draft",
        payload_json=payload_json,
        rationale=None,
        author_id=uuid.UUID(current_user.id),
        created_at=now,
    )
    session.add(new_revision)
    session.flush()

    symbol.current_revision_id = new_revision.id
    symbol.organization_wide = False
    symbol.updated_at = now
    session.flush()
    return symbol, new_revision


def set_organization_wide(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    enabled: bool,
) -> GovernedSymbol:
    organization_id = _active_organization_id(current_user)
    _require_organization_wide_toggle_authority(current_user)

    symbol = session.get(GovernedSymbol, symbol_id)
    if (
        symbol is None
        or symbol.owner_organization_id != organization_id
        or symbol.visibility != "organization_private"
    ):
        raise OrganizationSymbolReviewNotVisible()

    symbol.organization_wide = enabled
    symbol.updated_at = _utc_now()
    try:
        session.flush()
        # The organization-wide eligibility check
        # (trg_governed_symbols_organization_wide_eligibility) is a
        # DEFERRABLE INITIALLY DEFERRED constraint trigger — it does not
        # fire on flush(). Force it to fire now so a real violation
        # surfaces as a catchable IntegrityError here instead of an
        # unhandled failure at the route's later session.commit().
        session.execute(text("SET CONSTRAINTS trg_governed_symbols_organization_wide_eligibility IMMEDIATE"))
    except IntegrityError as exc:
        raise OrganizationSymbolReviewError(
            "Organization-wide scope requires the current revision to have an approved, closed organization review decision."
        ) from exc
    return symbol
