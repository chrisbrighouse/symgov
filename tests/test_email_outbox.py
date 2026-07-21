from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.email_worker import deliver_pending_email_batch
from symgov_backend.models import EmailOutbox, SubscriptionEvent, User
from symgov_backend.settings import SymgovAPISettings


def build_session():
    engine = create_engine("sqlite:///:memory:")
    User.__table__.create(engine)
    SubscriptionEvent.__table__.create(engine)
    EmailOutbox.__table__.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def add_message(session):
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    user = User(
        email="customer@example.com", display_name="Customer", pin_hash="x", pin_set_at=now,
        must_change_pin=False, is_active=True, created_at=now, updated_at=now, deleted_at=None,
    )
    session.add(user)
    session.flush()
    event = SubscriptionEvent(
        user_id=user.id, actor_id=user.id, action="upgraded", origin="self_service",
        previous_tier="free", new_tier="plus", previous_expires_on=None,
        new_expires_on=None, created_at=now,
    )
    session.add(event)
    session.flush()
    message = EmailOutbox(
        subscription_event_id=event.id, recipient_kind="customer", to_email=user.email, subject="Subject", body_text="Body",
        status="pending", attempt_count=0, next_attempt_at=now, last_error=None,
        created_at=now, sent_at=None,
    )
    session.add(message)
    session.commit()
    return message, now


def test_delivery_marks_pending_message_sent_and_is_idempotent():
    Session = build_session()
    sent = []
    with Session() as session:
        message, now = add_message(session)
        assert deliver_pending_email_batch(session, lambda item: sent.append(item.to_email), now=now) == 1
        session.commit()
        assert message.status == "sent"
        assert message.sent_at == now
        assert deliver_pending_email_batch(session, lambda item: sent.append(item.to_email), now=now) == 0
        assert sent == ["customer@example.com"]


def test_delivery_failure_is_sanitized_and_scheduled_for_retry():
    Session = build_session()
    with Session() as session:
        message, now = add_message(session)

        def fail(_):
            raise RuntimeError("smtp password=hunter2\nnetwork unavailable")

        assert deliver_pending_email_batch(session, fail, now=now) == 0
        session.commit()
        assert message.status == "pending"
        assert message.attempt_count == 1
        assert message.next_attempt_at > now
        assert "hunter2" not in (message.last_error or "")
        assert "RuntimeError" in (message.last_error or "")


def test_smtp_password_is_not_exposed_by_settings_repr():
    settings = SymgovAPISettings(smtp_password="secret-token")
    assert "secret-token" not in repr(settings)
