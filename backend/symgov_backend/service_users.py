from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .models import User
from .subscriptions import cancel_plus, ensure_subscription, remove_roles


DISABLED_SERVICE_PIN_HASH = "disabled-service-account"


def new_service_pin_hash() -> str:
    """Return a deliberately unverifiable marker for a non-interactive account."""
    return DISABLED_SERVICE_PIN_HASH


def enforce_noninteractive_service_account(session: Session, user: User, *, now: datetime) -> User:
    """Repair a system-owned user so it cannot log in or retain human privileges."""
    user.is_active = False
    user.must_change_pin = False
    user.pin_hash = DISABLED_SERVICE_PIN_HASH
    user.pin_set_at = now
    user.updated_at = now

    subscription = ensure_subscription(session, user, as_of=now.date())
    if subscription.tier == "plus":
        cancel_plus(session, user, as_of=now.date())
    else:
        remove_roles(session, user)
    session.flush()
    return user
