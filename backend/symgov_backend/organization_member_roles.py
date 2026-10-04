from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Organization,
    OrganizationMemberRole,
    OrganizationMembership,
    OrganizationSubscription,
    User,
)
from .organization_subscriptions import is_subscription_active

# `admin` is deliberately absent: it opens the platform's user and subscription
# administration, which an organization-scoped role must never reach.
ORG_SCOPED_ROLES = ("integrator", "reviewer", "submitter")
MIN_REASON_LENGTH = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _checked_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not MIN_REASON_LENGTH <= len(reason) <= 1000:
        raise ValueError(f"Reason must be between {MIN_REASON_LENGTH} and 1000 characters.")
    return reason


def _locked_membership(session: Session, organization_id: uuid.UUID, membership_id: uuid.UUID) -> OrganizationMembership:
    membership = session.execute(
        select(OrganizationMembership).where(OrganizationMembership.id == membership_id).with_for_update()
    ).scalar_one_or_none()
    if membership is None or membership.organization_id != organization_id:
        raise ValueError("Membership not found in this organization.")
    return membership


def grant_member_role(
    session: Session,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    role: str,
    actor_id: uuid.UUID,
    reason: str,
) -> OrganizationMemberRole:
    """Give a member a Plus-level role in this organization. Idempotent."""
    if role not in ORG_SCOPED_ROLES:
        raise ValueError(f"Unknown organization role '{role}'. Valid: {list(ORG_SCOPED_ROLES)}.")
    reason = _checked_reason(reason)
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected organization has no subscription, so it has no organization roles.")
    if session.get(OrganizationSubscription, organization_id) is None:
        raise ValueError("The organization has no subscription; create a plan before assigning roles.")
    membership = _locked_membership(session, organization_id, membership_id)
    if membership.status != "active":
        raise ValueError("Roles can only be assigned to an active membership.")
    existing = session.execute(
        select(OrganizationMemberRole).where(
            OrganizationMemberRole.membership_id == membership_id,
            OrganizationMemberRole.role == role,
            OrganizationMemberRole.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = OrganizationMemberRole(
        id=uuid.uuid4(), membership_id=membership_id, role=role, is_active=True, assigned_at=_now(),
        assigned_by_user_id=actor_id, assign_reason=reason,
    )
    session.add(row)
    session.flush()
    return row


def revoke_member_role(
    session: Session,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    role: str,
    actor_id: uuid.UUID,
    reason: str,
) -> OrganizationMemberRole:
    reason = _checked_reason(reason)
    _locked_membership(session, organization_id, membership_id)
    row = session.execute(
        select(OrganizationMemberRole)
        .where(
            OrganizationMemberRole.membership_id == membership_id,
            OrganizationMemberRole.role == role,
            OrganizationMemberRole.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ValueError("The member does not hold that role.")
    row.is_active = False
    row.revoked_at = _now()
    row.revoked_by_user_id = actor_id
    row.revoke_reason = reason
    session.flush()
    return row


def list_organization_member_roles(session: Session, organization_id: uuid.UUID) -> list[tuple]:
    """(membership_id, user email, display name, role, assigned_at) for every active role."""
    rows = session.execute(
        select(
            OrganizationMembership.id, User.email, User.display_name,
            OrganizationMemberRole.role, OrganizationMemberRole.assigned_at,
        )
        .join(OrganizationMembership, OrganizationMembership.id == OrganizationMemberRole.membership_id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMemberRole.is_active.is_(True),
        )
        .order_by(User.email, OrganizationMemberRole.role)
    ).all()
    return [tuple(row) for row in rows]


def organization_plan_roles(session: Session, organization_context) -> tuple[str, ...]:
    """The roles a session gets from its organization, or () when it gets none.

    None unless the session is bound to an organization whose subscription is
    active. An organization with no subscription (the protected one) grants
    nothing, and a lapsed one stops granting at once.
    """
    if organization_context is None:
        return ()
    subscription = session.execute(
        select(OrganizationSubscription)
        .where(OrganizationSubscription.organization_id == organization_context.organization_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if subscription is None or not is_subscription_active(subscription):
        return ()
    rows = session.execute(
        select(OrganizationMemberRole.role).where(
            OrganizationMemberRole.membership_id == organization_context.membership_id,
            OrganizationMemberRole.is_active.is_(True),
        )
    ).scalars()
    return tuple(sorted(set(rows)))
