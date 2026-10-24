"""Unified submission & review WP3 -- an organization's review policy for its
own private symbols (spec draft 2 FR8, decisions D3/D5, assumptions A1/A2).

- Self-approval is on by default: a submitter may decide their own private
  submission, even without the Org Reviewer capability (D3).
- With it off, the decider must be an Org Reviewer or Org Admin who is not the
  submitter (A2).
- An Org Admin may always decide, with or without the Org Reviewer capability
  (A1), subject to the self-approval rule.
- The policy never touches public submissions or promotion: those keep their
  own, independent review.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .models import OrganizationReviewPolicy, User
from .organization_service import _emit_audit, _mutation_audit_payload

DEFAULT_ALLOW_SELF_APPROVAL = True


@dataclass(frozen=True)
class ReviewPolicy:
    organization_id: uuid.UUID
    allow_self_approval: bool
    updated_at: datetime | None
    updated_by_user_id: uuid.UUID | None
    updated_by_name: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_review_policy(session: Session, organization_id: uuid.UUID, *, lock: bool = False) -> ReviewPolicy:
    """The organization's policy, or the defaults when it has never been set.

    `lock=True` takes a share lock on an existing row, so a decision and a
    concurrent policy change are ordered rather than interleaved.
    """
    query = select(OrganizationReviewPolicy).where(OrganizationReviewPolicy.organization_id == organization_id)
    if lock:
        query = query.with_for_update(read=True)
    row = session.execute(query).scalar_one_or_none()
    if row is None:
        return ReviewPolicy(organization_id, DEFAULT_ALLOW_SELF_APPROVAL, None, None, None)
    name = session.get(User, row.updated_by_user_id).display_name if row.updated_by_user_id else None
    return ReviewPolicy(organization_id, bool(row.allow_self_approval), row.updated_at, row.updated_by_user_id, name)


def set_review_policy(
    session: Session,
    actor: AuthenticatedUser,
    organization_id: uuid.UUID,
    *,
    allow_self_approval: bool,
    effective_authority: str,
) -> ReviewPolicy:
    """Change the policy and audit it. Authority is checked by the caller's route."""
    row = session.execute(
        select(OrganizationReviewPolicy)
        .where(OrganizationReviewPolicy.organization_id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    before = bool(row.allow_self_approval) if row is not None else DEFAULT_ALLOW_SELF_APPROVAL
    now = _utc_now()
    actor_id = uuid.UUID(actor.id)
    if row is None:
        row = OrganizationReviewPolicy(organization_id=organization_id, updated_at=now)
        session.add(row)
    row.allow_self_approval = allow_self_approval
    row.updated_by_user_id = actor_id
    row.updated_at = now
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="org_review_policy.updated",
        actor_id=actor_id,
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority=effective_authority,
            before={"allow_self_approval": before},
            after={"allow_self_approval": allow_self_approval},
            source="organization_review_policy",
            recent_step_up_at=actor.recent_step_up_at,
        ),
    )
    session.flush()
    return get_review_policy(session, organization_id)


def decision_authority(current_user: AuthenticatedUser, *, submitted_by_user_id: uuid.UUID, policy: ReviewPolicy) -> str | None:
    """Why this actor may decide this submission, or None if they may not.

    Returns "self_approval", "organization_admin" or "symbol_reviewer".
    """
    is_submitter = uuid.UUID(current_user.id) == submitted_by_user_id
    if is_submitter:
        return "self_approval" if policy.allow_self_approval else None
    if current_user.organization_base_role == "admin":
        return "organization_admin"
    if "symbol_reviewer" in current_user.organization_capabilities:
        return "symbol_reviewer"
    return None
