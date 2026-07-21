from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from .models import SubscriptionEvent, User, UserRole, UserSubscription

PROTECTED_OWNER_EMAIL = "chris.brighouse@hotmail.co.uk"


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def add_calendar_months(value: date, months: int, *, anchor_day: int | None = None) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(anchor_day or value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _timestamp() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _lock_user_and_subscription(session: Session, user: User) -> UserSubscription | None:
    session.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    return (
        session.query(UserSubscription)
        .filter(UserSubscription.user_id == user.id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )


def remove_roles(session: Session, user: User) -> None:
    session.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
    session.flush()


def _ensure_admin_role(session: Session, user: User, now: datetime) -> None:
    role = session.query(UserRole).filter(UserRole.user_id == user.id, UserRole.role == "admin").one_or_none()
    if role is None:
        session.add(UserRole(user_id=user.id, role="admin", created_at=now))
        session.flush()


def _record_event(
    session: Session,
    *,
    user: User,
    action: str,
    previous_tier: str | None,
    new_tier: str,
    previous_expires_on: date | None,
    new_expires_on: date | None,
    actor_id: uuid.UUID | None = None,
) -> None:
    session.add(
        SubscriptionEvent(
            id=uuid.uuid4(), user_id=user.id, actor_id=actor_id, action=action,
            previous_tier=previous_tier, new_tier=new_tier,
            previous_expires_on=previous_expires_on, new_expires_on=new_expires_on,
            created_at=_timestamp(),
        )
    )
    session.flush()


def ensure_subscription(session: Session, user: User, *, as_of: date | None = None) -> UserSubscription:
    resolved_date = as_of or today_utc()
    now = _timestamp()
    protected = user.email.strip().lower() == PROTECTED_OWNER_EMAIL
    subscription = session.get(UserSubscription, user.id)
    if subscription is None:
        subscription = _lock_user_and_subscription(session, user)
    if subscription is None:
        subscription = UserSubscription(
            user_id=user.id, tier="plus" if protected else "free", started_on=resolved_date,
            expires_on=None, anchor_day=resolved_date.day, is_protected=protected, version=1, created_at=now, updated_at=now,
        )
        session.add(subscription)
        session.flush()
        _record_event(
            session, user=user, action="created", previous_tier=None, new_tier=subscription.tier,
            previous_expires_on=None, new_expires_on=None,
        )
    if protected:
        repaired = subscription.tier != "plus" or subscription.expires_on is not None or not subscription.is_protected
        if repaired:
            subscription = _lock_user_and_subscription(session, user) or subscription
            repaired = subscription.tier != "plus" or subscription.expires_on is not None or not subscription.is_protected
        subscription.tier = "plus"
        subscription.expires_on = None
        subscription.is_protected = True
        user.is_active = True
        user.deleted_at = None
        _ensure_admin_role(session, user, now)
        if repaired:
            subscription.version += 1
            subscription.updated_at = now
            _record_event(
                session, user=user, action="owner_repaired", previous_tier=None, new_tier="plus",
                previous_expires_on=None, new_expires_on=None,
            )
    return reconcile_subscription(session, user, subscription=subscription, as_of=resolved_date)


def reconcile_subscription(
    session: Session, user: User, *, subscription: UserSubscription | None = None, as_of: date | None = None,
) -> UserSubscription:
    resolved_date = as_of or today_utc()
    current = subscription or session.get(UserSubscription, user.id)
    if current is None:
        return ensure_subscription(session, user, as_of=resolved_date)
    if current.tier == "plus" and not current.is_protected and current.expires_on is not None and current.expires_on <= resolved_date:
        current = _lock_user_and_subscription(session, user) or current
    if current.tier == "plus" and not current.is_protected and current.expires_on is not None and current.expires_on <= resolved_date:
        previous_expiry = current.expires_on
        current.tier = "free"
        current.started_on = resolved_date
        current.anchor_day = resolved_date.day
        current.expires_on = None
        current.version += 1
        current.updated_at = _timestamp()
        remove_roles(session, user)
        _record_event(
            session, user=user, action="expired", previous_tier="plus", new_tier="free",
            previous_expires_on=previous_expiry, new_expires_on=None,
        )
    return current


def upgrade_to_plus(
    session: Session, user: User, *, months: int, as_of: date | None = None, actor_id: uuid.UUID | None = None,
) -> UserSubscription:
    if months < 1:
        raise ValueError("Plus duration must be at least one month.")
    resolved_date = as_of or today_utc()
    _lock_user_and_subscription(session, user)
    current = ensure_subscription(session, user, as_of=resolved_date)
    if current.is_protected:
        return current
    if current.tier == "plus":
        raise ValueError("The user already has an active Plus subscription; adjust its duration instead.")
    previous_tier, previous_expiry = current.tier, current.expires_on
    current.tier = "plus"
    current.started_on = resolved_date
    current.anchor_day = resolved_date.day
    current.expires_on = add_calendar_months(resolved_date, months, anchor_day=current.anchor_day)
    current.version += 1
    current.updated_at = _timestamp()
    _record_event(
        session, user=user, action="upgraded", previous_tier=previous_tier, new_tier="plus",
        previous_expires_on=previous_expiry, new_expires_on=current.expires_on, actor_id=actor_id,
    )
    return current


def adjust_plus_months(
    session: Session, user: User, *, months: int, as_of: date | None = None, actor_id: uuid.UUID | None = None,
) -> UserSubscription:
    if months == 0:
        raise ValueError("Subscription adjustment must not be zero months.")
    resolved_date = as_of or today_utc()
    _lock_user_and_subscription(session, user)
    current = ensure_subscription(session, user, as_of=resolved_date)
    if current.is_protected:
        raise ValueError("The protected owner subscription cannot be adjusted.")
    if current.tier != "plus" or current.expires_on is None:
        raise ValueError("Only an active Plus subscription can be adjusted.")
    candidate = add_calendar_months(current.expires_on, months, anchor_day=current.anchor_day)
    if (candidate.year, candidate.month) < (resolved_date.year, resolved_date.month):
        raise ValueError("Subscription expiry cannot be earlier than the current month.")
    previous_expiry = current.expires_on
    current.expires_on = candidate
    current.version += 1
    current.updated_at = _timestamp()
    _record_event(
        session, user=user, action="adjusted", previous_tier="plus", new_tier="plus",
        previous_expires_on=previous_expiry, new_expires_on=candidate, actor_id=actor_id,
    )
    return reconcile_subscription(session, user, subscription=current, as_of=resolved_date)


def cancel_plus(
    session: Session, user: User, *, as_of: date | None = None, actor_id: uuid.UUID | None = None,
    action: str = "cancelled",
) -> UserSubscription:
    resolved_date = as_of or today_utc()
    _lock_user_and_subscription(session, user)
    current = ensure_subscription(session, user, as_of=resolved_date)
    if current.is_protected:
        raise ValueError("The protected owner subscription cannot be cancelled.")
    previous_tier, previous_expiry = current.tier, current.expires_on
    current.tier = "free"
    current.started_on = resolved_date
    current.expires_on = None
    current.anchor_day = resolved_date.day
    current.version += 1
    current.updated_at = _timestamp()
    remove_roles(session, user)
    _record_event(
        session, user=user, action=action, previous_tier=previous_tier, new_tier="free",
        previous_expires_on=previous_expiry, new_expires_on=None, actor_id=actor_id,
    )
    return current
