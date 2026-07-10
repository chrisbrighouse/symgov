from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from .catalog_api_auth import IntegrationAuthContext
from .models import CatalogApiUsageEvent

MAX_USAGE_TEXT_LENGTH = 500
MAX_HEADER_LENGTH = 200

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s&]+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s&]+)"),
    re.compile(r"(?i)(bearer\s+)(symgov_[^\s&]+)"),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def sanitize_usage_text(value: object, *, max_length: int = MAX_USAGE_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
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


def _request_header(request: Request, name: str, *, max_length: int = MAX_HEADER_LENGTH) -> str | None:
    return _truncate(request.headers.get(name), max_length)


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
        customer_name_snapshot=auth_context.customer_name,
        integration_name_snapshot=auth_context.integration_name,
        scope_used=_truncate(scope_used, 100),
        method=request.method,
        path=request.url.path,
        route_name=_truncate(route_name, 200),
        status_code=int(status_code),
        latency_ms=latency_ms,
        request_id=_truncate(request_id or request.headers.get("x-request-id"), 200),
        query_text=sanitize_usage_text(query_text),
        symbol_ref=_truncate(symbol_ref, 100),
        result_count=result_count,
        ed_query_type=_truncate(ed_query_type, 100),
        user_agent=_request_header(request, "user-agent", max_length=500),
        client_ip_hash=hash_client_ip(_request_client_ip(request)),
        application_name=_request_header(request, "x-symgov-application"),
        application_version=_request_header(request, "x-symgov-application-version"),
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
