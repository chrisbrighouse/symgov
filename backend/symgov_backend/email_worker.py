from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable
from urllib import error, parse, request

from sqlalchemy.orm import Session

from .db import create_session_factory
from .models import EmailOutbox
from .settings import SymgovAPISettings

LOGGER = logging.getLogger(__name__)
AGENTMAIL_BASE_URL = "https://api.agentmail.to/v0"


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, "AgentMail redirects are refused.", headers, fp)


_AGENTMAIL_OPEN = request.build_opener(_NoRedirectHandler()).open


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
            status_code = getattr(exc, "code", None)
            error_category = (
                f"{type(exc).__name__}[{status_code}]"
                if isinstance(status_code, int)
                else type(exc).__name__
            )
            row.last_error = f"{error_category}: delivery failed"
        else:
            row.status = "sent"
            row.attempt_count += 1
            row.sent_at = resolved_now
            row.last_error = None
            delivered += 1
    session.flush()
    return delivered


class AgentMailEmailSender:
    def __init__(
        self,
        settings: SymgovAPISettings,
        *,
        opener: Callable[..., Any] = _AGENTMAIL_OPEN,
    ):
        if not settings.agentmail_api_key or not settings.agentmail_inbox:
            raise ValueError("AgentMail API key and inbox must be configured.")
        if settings.agentmail_base_url != AGENTMAIL_BASE_URL:
            raise ValueError("AgentMail delivery is restricted to the official HTTPS API.")
        self.settings = settings
        self.opener = opener

    def __call__(self, row: EmailOutbox) -> None:
        inbox = parse.quote(self.settings.agentmail_inbox, safe="")
        url = f"{self.settings.agentmail_base_url}/inboxes/{inbox}/messages/send"
        payload = json.dumps(
            {"to": [row.to_email], "subject": row.subject, "text": row.body_text}
        ).encode("utf-8")
        outbound = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.settings.agentmail_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"symgov-outbox-{row.id}",
                "User-Agent": "symgov-email-worker/1.0",
            },
        )
        with self.opener(outbound, timeout=self.settings.agentmail_timeout_seconds) as response:
            response.read()


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


def configured_email_sender(settings: SymgovAPISettings) -> Callable[[EmailOutbox], None] | None:
    if settings.email_transport == "agentmail":
        if settings.agentmail_api_key and settings.agentmail_inbox:
            return AgentMailEmailSender(settings)
        LOGGER.warning("AgentMail transport is selected but not fully configured.")
        return None
    if settings.email_transport == "smtp":
        if settings.smtp_host and settings.smtp_from_email:
            return SMTPEmailSender(settings)
        return None
    LOGGER.warning("Unsupported email transport configured: %s", settings.email_transport)
    return None


def deliver_configured_email_batch(settings: SymgovAPISettings) -> int:
    sender = configured_email_sender(settings)
    if sender is None:
        raise ValueError("Email transport is not fully configured.")
    Session = create_session_factory(env_file=settings.db_env_file, nopool=True)
    with Session() as session:
        delivered = deliver_pending_email_batch(session, sender)
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