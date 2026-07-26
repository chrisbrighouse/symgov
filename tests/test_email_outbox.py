import json
import logging
from datetime import datetime, timezone
from email.message import Message
from urllib.error import HTTPError

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.email_worker import (
    AgentMailEmailSender,
    configured_email_sender,
    deliver_pending_email_batch,
)
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


def test_http_delivery_failure_records_status_without_response_body():
    Session = build_session()
    with Session() as session:
        message, now = add_message(session)

        def fail(_):
            raise HTTPError(
                "https://api.agentmail.to/v0",
                429,
                "rate limited; api_key=secret-token",
                Message(),
                None,
            )

        assert deliver_pending_email_batch(session, fail, now=now) == 0
        session.commit()
        assert message.last_error == "HTTPError[429]: delivery failed"
        assert "secret-token" not in message.last_error


def test_transport_secrets_are_not_exposed_by_settings_repr():
    settings = SymgovAPISettings(
        smtp_password="secret-smtp-token",
        agentmail_api_key="secret-agentmail-token",
    )
    assert "secret-smtp-token" not in repr(settings)
    assert "secret-agentmail-token" not in repr(settings)


def test_agentmail_sender_posts_outbox_message_from_pat_inbox():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"message_id":"msg_123","thread_id":"thd_456"}'

    def open_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    settings = SymgovAPISettings(
        email_transport="agentmail",
        agentmail_api_key="agentmail-secret",
        agentmail_inbox="alfi-bot@agentmail.to",
    )
    Session = build_session()
    with Session() as session:
        message, _ = add_message(session)
        AgentMailEmailSender(settings, opener=open_request)(message)

    request = captured["request"]
    assert request.full_url == "https://api.agentmail.to/v0/inboxes/alfi-bot%40agentmail.to/messages/send"
    assert request.get_header("Authorization") == "Bearer agentmail-secret"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Idempotency-key") == f"symgov-outbox-{message.id}"
    assert json.loads(request.data) == {
        "to": ["customer@example.com"],
        "subject": "Subject",
        "text": "Body",
    }
    assert captured["timeout"] == 20


def test_agentmail_sender_requires_key_and_inbox():
    with pytest.raises(ValueError, match="API key and inbox"):
        AgentMailEmailSender(SymgovAPISettings(email_transport="agentmail", agentmail_api_key=""))


def test_agentmail_sender_rejects_non_agentmail_api_base_url():
    settings = SymgovAPISettings(
        email_transport="agentmail",
        agentmail_api_key="agentmail-secret",
        agentmail_inbox="alfi-bot@agentmail.to",
        agentmail_base_url="https://attacker.example/v0",
    )

    with pytest.raises(ValueError, match="official HTTPS API"):
        AgentMailEmailSender(settings)


def test_configured_email_sender_selects_agentmail_without_smtp():
    settings = SymgovAPISettings(
        email_transport="agentmail",
        agentmail_api_key="agentmail-secret",
        agentmail_inbox="alfi-bot@agentmail.to",
        smtp_host="",
        smtp_from_email="",
    )

    assert isinstance(configured_email_sender(settings), AgentMailEmailSender)


def test_configured_email_sender_warns_when_agentmail_is_incomplete(caplog):
    settings = SymgovAPISettings(
        email_transport="agentmail",
        agentmail_api_key="",
        agentmail_inbox="alfi-bot@agentmail.to",
    )

    with caplog.at_level(logging.WARNING):
        assert configured_email_sender(settings) is None

    assert "AgentMail transport is selected but not fully configured" in caplog.text
