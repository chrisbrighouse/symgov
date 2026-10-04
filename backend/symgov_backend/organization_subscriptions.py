from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Organization,
    OrganizationMembership,
    OrganizationSubscription,
    OrganizationSubscriptionEvent,
)
from .subscriptions import add_calendar_months, today_utc

# A membership holds a seat while it is active or invited; deactivating,
# suspending or never accepting frees it.
SEAT_HOLDING_STATUSES = ("active", "invited")
MAX_SEAT_LIMIT = 100_000
# Plan every organization starts on until a platform admin changes it. The same
# values seed existing organizations in migration 20261004_0068.
DEFAULT_SEAT_LIMIT = 25
DEFAULT_TERM_MONTHS = 12


def _timestamp() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def seats_in_use(
    session: Session, organization_id: uuid.UUID, *, excluding_membership_id: uuid.UUID | None = None
) -> int:
    query = select(func.count()).select_from(OrganizationMembership).where(
        OrganizationMembership.organization_id == organization_id,
        OrganizationMembership.status.in_(SEAT_HOLDING_STATUSES),
    )
    if excluding_membership_id is not None:
        query = query.where(OrganizationMembership.id != excluding_membership_id)
    return int(session.execute(query).scalar_one())


def get_organization_subscription(
    session: Session, organization_id: uuid.UUID, *, lock: bool = False
) -> OrganizationSubscription | None:
    query = select(OrganizationSubscription).where(OrganizationSubscription.organization_id == organization_id)
    if lock:
        query = query.with_for_update()
    return session.execute(query).scalar_one_or_none()


def is_subscription_active(subscription: OrganizationSubscription, *, as_of: date | None = None) -> bool:
    return subscription.expires_on > (as_of or today_utc())


def assert_seat_available(
    session: Session,
    organization_id: uuid.UUID,
    *,
    excluding_membership_id: uuid.UUID | None = None,
    as_of: date | None = None,
) -> None:
    """Raise ValueError unless one more membership may take a seat.

    NOT wired into any membership path yet; seat enforcement is deferred until
    the subscription, renewal and expiry mechanisms are in place.

    Callers hold the organization row lock (FOR UPDATE), which serialises the
    count against concurrent adds. An organization without a subscription row
    is unmetered. A lapsed subscription admits no new seats; existing members
    are left alone.
    """
    subscription = get_organization_subscription(session, organization_id)
    if subscription is None:
        return
    if not is_subscription_active(subscription, as_of=as_of):
        raise ValueError("The organization's subscription has expired; renew it to add members.")
    used = seats_in_use(session, organization_id, excluding_membership_id=excluding_membership_id)
    if used >= subscription.seat_limit:
        raise ValueError(
            f"No seats available: {used} of {subscription.seat_limit} are in use. "
            "Deactivate a member or increase the seat limit."
        )


def set_organization_subscription(
    session: Session,
    organization_id: uuid.UUID,
    *,
    seat_limit: int,
    months: int | None = None,
    expires_on: date | None = None,
    actor_id: uuid.UUID | None = None,
    reason: str | None = None,
    as_of: date | None = None,
) -> OrganizationSubscription:
    """Create or replace an organization's seat limit and expiry.

    Give exactly one of `months` (from today, or from `as_of`) or `expires_on`.
    The seat limit is recorded but not enforced: membership changes do not
    consult it yet, and it may be set below the seats currently held.
    """
    if isinstance(seat_limit, bool) or not isinstance(seat_limit, int) or not 1 <= seat_limit <= MAX_SEAT_LIMIT:
        raise ValueError(f"Seat limit must be a whole number between 1 and {MAX_SEAT_LIMIT}.")
    if (months is None) == (expires_on is None):
        raise ValueError("Give exactly one of months or expires_on.")
    today = as_of or today_utc()
    if months is not None:
        if isinstance(months, bool) or not isinstance(months, int) or months < 1:
            raise ValueError("Subscription duration must be at least one month.")
        expires_on = add_calendar_months(today, months)
    if expires_on <= today:
        raise ValueError("Subscription expiry must be in the future.")

    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected organization is not metered.")

    now = _timestamp()
    current = get_organization_subscription(session, organization_id, lock=True)
    previous_limit = previous_expiry = None
    if current is None:
        current = OrganizationSubscription(
            organization_id=organization_id, seat_limit=seat_limit, started_on=today,
            expires_on=expires_on, version=1, created_at=now, updated_at=now,
        )
        session.add(current)
        action = "created"
    else:
        previous_limit, previous_expiry = current.seat_limit, current.expires_on
        if not is_subscription_active(current, as_of=today):
            current.started_on = today
        current.seat_limit = seat_limit
        current.expires_on = expires_on
        current.version += 1
        current.updated_at = now
        action = "updated"
    session.add(
        OrganizationSubscriptionEvent(
            id=uuid.uuid4(), organization_id=organization_id, actor_id=actor_id, action=action,
            previous_seat_limit=previous_limit, new_seat_limit=seat_limit,
            previous_expires_on=previous_expiry, new_expires_on=expires_on,
            reason=(reason or None), created_at=now,
        )
    )
    session.flush()
    return current
