from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import EmailOutbox, SubscriptionEvent, User, UserSubscription


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def queue_subscription_change_emails(
    session: Session,
    *,
    event: SubscriptionEvent,
    user: User,
    subscription: UserSubscription,
    admin_email: str,
    years: int | None = None,
    previous_expires_on: object | None = None,
) -> None:
    now = utc_now()
    if event.action == "upgraded":
        subject = "Your Symgov Plus subscription is active"
        detail = (
            f"Plus is now active for {years} year{'s' if years != 1 else ''}.\n"
            f"Start date: {subscription.started_on}\nExpiry date: {subscription.expires_on}\n"
            "No payment was taken for this initial release."
        )
        admin_subject = f"Symgov Plus activated: {user.email}"
    else:
        subject = "Your Symgov subscription changed to Free"
        detail = (
            "Your Plus subscription was downgraded to Free immediately.\n"
            f"Previous expiry date: {previous_expires_on or 'n/a'}\n"
            f"Effective date: {subscription.started_on}"
        )
        admin_subject = f"Symgov Plus downgraded: {user.email}"

    customer_body = f"Hello {user.display_name},\n\n{detail}\n"
    admin_body = f"Customer: {user.display_name}\nEmail: {user.email}\n\n{detail}\n"
    recipients = (
        ("customer", user.email.strip().lower(), subject, customer_body),
        ("admin", admin_email.strip().lower(), admin_subject, admin_body),
    )
    for recipient_kind, to_email, message_subject, body_text in recipients:
        session.add(
            EmailOutbox(
                id=uuid.uuid4(),
                subscription_event_id=event.id,
                recipient_kind=recipient_kind,
                to_email=to_email,
                subject=message_subject,
                body_text=body_text,
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
                last_error=None,
                created_at=now,
                sent_at=None,
            )
        )
    session.flush()