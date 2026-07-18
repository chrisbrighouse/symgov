from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from .catalog_api_auth import PLANNED_CATALOG_API_SCOPES, IntegrationAuthContext
from .catalog_developer import redact_catalog_credential_label
from .models import CatalogApiUsageEvent

MAX_USAGE_TEXT_LENGTH = 500
MAX_HEADER_LENGTH = 200

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|pass(?:word|wd)?|token|secret|client[_ -]?secret)"
    r"(\s*[=:]\s*)([^\s&]+)"
)
_SECRET_BEARER = re.compile(r"(?i)\b(bearer\s+)([^\s&]+)")
_SECRET_CONNECTION_URI = re.compile(
    r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s]+"
)
_SECRET_PROVIDER_TOKEN = re.compile(
    r"(?ix)(?:"
    r"\bsymgov_(?:live|test)_[a-z0-9_-]{12,}\b|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{4,}\b|"
    r"\bsk-(?:proj-)?[a-z0-9_-]{16,}\b|"
    r"\b(?:xox[baprs]-|gh[pousr]_)[a-z0-9_-]{12,}\b|"
    r"\bAKIA[A-Z0-9]{16}\b"
    r")"
)
_SECRET_SHA256 = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_OBVIOUS_PLACEHOLDER = re.compile(
    r"(?ix)^(?:"
    r"\*{3,}|\.\.\.|…|"
    r"\[?redacted\]?|"
    r"<\s*(?:your[_ -]?)?(?:api[_ -]?key|token|secret|password)\s*>"
    r")$"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def sanitize_usage_text(value: object, *, max_length: int = MAX_USAGE_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: (
            match.group(0)
            if _OBVIOUS_PLACEHOLDER.fullmatch(match.group(3))
            else f"{match.group(1)}{match.group(2)}[REDACTED]"
        ),
        text,
    )
    text = _SECRET_BEARER.sub(
        lambda match: (
            match.group(0)
            if _OBVIOUS_PLACEHOLDER.fullmatch(match.group(2))
            else f"{match.group(1)}[REDACTED]"
        ),
        text,
    )
    text = _SECRET_CONNECTION_URI.sub(
        lambda match: (
            match.group(0)
            if re.search(
                r"(?i)://[^/\s:@]+:(?:\*{3,}|\[redacted\]|<[^>]+>)@",
                match.group(0),
            )
            else f"{match.group(1)}://[REDACTED]"
        ),
        text,
    )
    text = _SECRET_PROVIDER_TOKEN.sub("[REDACTED]", text)
    text = _SECRET_SHA256.sub("[REDACTED]", text)
    return text[:max_length]


def hash_client_ip(client_ip: str | None) -> str | None:
    normalized = str(client_ip or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _request_client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def build_catalog_usage_event(
    auth_context: IntegrationAuthContext,
    *,
    request: Request,
    scope_used: str | None,
    route_name: str | None,
    status_code: int,
    latency_ms: int | None = None,
    request_id: str | None = None,
    query_text: object = None,
    symbol_ref: str | None = None,
    result_count: int | None = None,
    ed_query_type: str | None = None,
    created_at: datetime | None = None,
) -> CatalogApiUsageEvent:
    return CatalogApiUsageEvent(
        api_key_id=uuid.UUID(str(auth_context.api_key_id)),
        customer_name_snapshot=redact_catalog_credential_label(
            sanitize_usage_text(auth_context.customer_name) or "[REDACTED]"
        ),
        integration_name_snapshot=redact_catalog_credential_label(
            sanitize_usage_text(auth_context.integration_name) or "[REDACTED]"
        ),
        scope_used=(
            normalized_scope
            if (normalized_scope := sanitize_usage_text(scope_used, max_length=100))
            in PLANNED_CATALOG_API_SCOPES
            else None
        ),
        method=sanitize_usage_text(request.method, max_length=100) or "[REDACTED]",
        path=sanitize_usage_text(request.url.path, max_length=500) or "[REDACTED]",
        route_name=sanitize_usage_text(route_name, max_length=200),
        status_code=int(status_code),
        latency_ms=latency_ms,
        request_id=sanitize_usage_text(
            request_id or request.headers.get("x-request-id"),
            max_length=200,
        ),
        query_text=sanitize_usage_text(query_text),
        symbol_ref=sanitize_usage_text(symbol_ref, max_length=100),
        result_count=result_count,
        ed_query_type=sanitize_usage_text(ed_query_type, max_length=100),
        user_agent=sanitize_usage_text(
            request.headers.get("user-agent"),
            max_length=500,
        ),
        client_ip_hash=hash_client_ip(_request_client_ip(request)),
        application_name=sanitize_usage_text(
            request.headers.get("x-symgov-application"),
            max_length=MAX_HEADER_LENGTH,
        ),
        application_version=sanitize_usage_text(
            request.headers.get("x-symgov-application-version"),
            max_length=MAX_HEADER_LENGTH,
        ),
        created_at=created_at or utc_now(),
    )


def log_catalog_usage_event_best_effort(
    session: Session,
    auth_context: IntegrationAuthContext,
    *,
    request: Request,
    scope_used: str | None,
    route_name: str | None,
    status_code: int,
    latency_ms: int | None = None,
    request_id: str | None = None,
    query_text: object = None,
    symbol_ref: str | None = None,
    result_count: int | None = None,
    ed_query_type: str | None = None,
) -> CatalogApiUsageEvent | None:
    try:
        event = build_catalog_usage_event(
            auth_context,
            request=request,
            scope_used=scope_used,
            route_name=route_name,
            status_code=status_code,
            latency_ms=latency_ms,
            request_id=request_id,
            query_text=query_text,
            symbol_ref=symbol_ref,
            result_count=result_count,
            ed_query_type=ed_query_type,
        )
        session.add(event)
        session.commit()
        return event
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return None
