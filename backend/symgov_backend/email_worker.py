from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable

from sqlalchemy.orm import Session

from .db import create_session_factory
from .models import EmailOutbox
from .settings import SymgovAPISettings

LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def deliver_pending_email_batch(
    session: Session,
    sender: Callable[[EmailOutbox], None],
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> int:
    resolved_now = now or utc_now()
    rows = (
        session.query(EmailOutbox)
        .filter(EmailOutbox.status == "pending", EmailOutbox.next_attempt_at <= resolved_now)
        .order_by(EmailOutbox.created_at, EmailOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    delivered = 0
    for row in rows:
        try:
            sender(row)
        except Exception as exc:  # transport failures are persisted for retry
            row.attempt_count += 1
            delay_seconds = min(3600, 30 * (2 ** min(row.attempt_count - 1, 7)))
            row.next_attempt_at = resolved_now + timedelta(seconds=delay_seconds)
            row.last_error = f"{type(exc).__name__}: delivery failed"
        else:
            row.status = "sent"
            row.attempt_count += 1
            row.sent_at = resolved_now
            row.last_error = None
            delivered += 1
    session.flush()
    return delivered


class SMTPEmailSender:
    def __init__(self, settings: SymgovAPISettings):
        if not settings.smtp_host or not settings.smtp_from_email:
            raise ValueError("SMTP host and from address must be configured.")
        self.settings = settings

    def __call__(self, row: EmailOutbox) -> None:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_email
        message["To"] = row.to_email
        message["Subject"] = row.subject
        message.set_content(row.body_text)
        if self.settings.smtp_ssl:
            client = smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=20,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20)
        with client:
            if self.settings.smtp_starttls and not self.settings.smtp_ssl:
                client.starttls(context=ssl.create_default_context())
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(message)


def deliver_configured_email_batch(settings: SymgovAPISettings) -> int:
    Session = create_session_factory(env_file=settings.db_env_file, nopool=True)
    with Session() as session:
        delivered = deliver_pending_email_batch(session, SMTPEmailSender(settings))
        session.commit()
        return delivered


async def run_email_outbox_worker(settings: SymgovAPISettings, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(deliver_configured_email_batch, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep a transient database or transport fault from killing the worker
            LOGGER.warning("Email outbox worker cycle failed (%s).", type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.email_worker_interval_seconds)
        except asyncio.TimeoutError:
            pass