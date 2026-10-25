"""In-app notifications for the private symbol workflow (unified submission &
review WP6, spec draft 2 FR17, decision D9: in-app first, email later).

Events and recipients:
- submitted -> the organization's Org Admins and Org Reviewers, except the submitter;
- approved / rejected / changes requested -> the submitter, unless they decided it
  themselves (self-approval);
- withdrawn -> the organization's Org Admins and Org Reviewers, except the submitter.

There is no "review assigned" notice: the organization shares one queue.

Isolation: a row is written only for an active member of the owning
organization, and every read re-checks that the reader is still one, so a
member who leaves stops seeing that organization's notices.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .models import (
    GovernedSymbol,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    OrganizationSymbolReviewDecision,
    OrganizationSymbolReviewSubmission,
    User,
    UserNotification,
)

DECISION_KINDS = {
    "approved": "org_symbol.approved",
    "rejected": "org_symbol.rejected",
    "changes_requested": "org_symbol.changes_requested",
}
LIST_LIMIT = 50


class UserNotificationNotFound(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _active_members(session: Session, organization_id: uuid.UUID):
    return (
        select(OrganizationMembership.user_id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
            User.is_active.is_(True),
        )
    )


def is_active_member(session: Session, user_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
    query = _active_members(session, organization_id).where(OrganizationMembership.user_id == user_id)
    return session.execute(query.limit(1)).first() is not None


def organization_reviewer_ids(session: Session, organization_id: uuid.UUID) -> set[uuid.UUID]:
    """Active members who may review: Org Admins and `symbol_reviewer` holders."""
    admin = (
        select(OrganizationRoleAssignment.id)
        .where(
            OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
            OrganizationRoleAssignment.base_role == "admin",
            OrganizationRoleAssignment.is_active.is_(True),
        )
        .exists()
    )
    reviewer = (
        select(OrganizationMemberCapability.id)
        .where(
            OrganizationMemberCapability.membership_id == OrganizationMembership.id,
            OrganizationMemberCapability.capability == "symbol_reviewer",
            OrganizationMemberCapability.is_active.is_(True),
        )
        .exists()
    )
    query = _active_members(session, organization_id).where(or_(admin, reviewer))
    return set(session.execute(query).scalars())


def _write(
    session: Session,
    recipients: set[uuid.UUID],
    *,
    kind: str,
    submission: OrganizationSymbolReviewSubmission,
    payload: dict,
) -> list[UserNotification]:
    now = _utc_now()
    rows = [
        UserNotification(
            id=uuid.uuid4(),
            user_id=recipient,
            organization_id=submission.organization_id,
            kind=kind,
            governed_symbol_id=submission.governed_symbol_id,
            submission_id=submission.id,
            payload_json=payload,
            created_at=now,
        )
        for recipient in sorted(recipients)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def _payload(session: Session, submission: OrganizationSymbolReviewSubmission, actor_id: uuid.UUID, **extra) -> dict:
    symbol = session.get(GovernedSymbol, submission.governed_symbol_id)
    actor = session.get(User, actor_id)
    return {
        "symbolName": symbol.canonical_name if symbol is not None else None,
        "actorName": actor.display_name if actor is not None else None,
        **extra,
    }


def notify_submitted(session: Session, submission: OrganizationSymbolReviewSubmission) -> list[UserNotification]:
    recipients = organization_reviewer_ids(session, submission.organization_id) - {submission.submitted_by_user_id}
    return _write(
        session, recipients, kind="org_symbol.submitted", submission=submission,
        payload=_payload(session, submission, submission.submitted_by_user_id),
    )


def notify_withdrawn(session: Session, submission: OrganizationSymbolReviewSubmission) -> list[UserNotification]:
    recipients = organization_reviewer_ids(session, submission.organization_id) - {submission.submitted_by_user_id}
    return _write(
        session, recipients, kind="org_symbol.withdrawn", submission=submission,
        payload=_payload(session, submission, submission.submitted_by_user_id),
    )


def notify_decided(session: Session, decision: OrganizationSymbolReviewDecision) -> list[UserNotification]:
    submission = session.get(OrganizationSymbolReviewSubmission, decision.submission_id)
    if submission is None or submission.submitted_by_user_id == decision.decided_by_user_id:
        return []
    if not is_active_member(session, submission.submitted_by_user_id, submission.organization_id):
        return []
    return _write(
        session, {submission.submitted_by_user_id}, kind=DECISION_KINDS[decision.decision], submission=submission,
        payload=_payload(session, submission, decision.decided_by_user_id, rationale=decision.rationale),
    )


@dataclass(frozen=True)
class NotificationPage:
    items: list[UserNotification]
    unread_count: int


def list_notifications(session: Session, user_id: uuid.UUID, organization_id: uuid.UUID) -> NotificationPage:
    """The reader's newest notices in this organization, if they are still a member."""
    if not is_active_member(session, user_id, organization_id):
        return NotificationPage(items=[], unread_count=0)
    mine = (UserNotification.user_id == user_id, UserNotification.organization_id == organization_id)
    items = session.execute(
        select(UserNotification).where(*mine).order_by(UserNotification.created_at.desc(), UserNotification.id).limit(LIST_LIMIT)
    ).scalars().all()
    unread = session.execute(
        select(func.count()).select_from(UserNotification).where(*mine, UserNotification.read_at.is_(None))
    ).scalar_one()
    return NotificationPage(items=list(items), unread_count=unread)


def mark_read(session: Session, user_id: uuid.UUID, organization_id: uuid.UUID, notification_id: uuid.UUID) -> None:
    if not is_active_member(session, user_id, organization_id):
        raise UserNotificationNotFound()
    notification = session.get(UserNotification, notification_id)
    if notification is None or notification.user_id != user_id or notification.organization_id != organization_id:
        raise UserNotificationNotFound()
    if notification.read_at is None:
        notification.read_at = _utc_now()
        session.flush()


def mark_all_read(session: Session, user_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    if not is_active_member(session, user_id, organization_id):
        return
    session.execute(
        update(UserNotification)
        .where(
            UserNotification.user_id == user_id,
            UserNotification.organization_id == organization_id,
            UserNotification.read_at.is_(None),
        )
        .values(read_at=_utc_now())
    )
    session.flush()
