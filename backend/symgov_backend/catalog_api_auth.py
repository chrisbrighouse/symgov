from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .catalog_developer import redact_catalog_credential_label
from .dependencies import get_db_session
from .models import CatalogApiKey

PLANNED_CATALOG_API_SCOPES = {
    "catalog.read",
    "catalog.preview",
    "catalog.ed.query",
    "catalog.feedback.write",
    "catalog.usage.read",
}


@dataclass(frozen=True)
class IntegrationAuthContext:
    api_key_id: str
    customer_name: str
    integration_name: str
    scopes: tuple[str, ...]
    key_prefix: str


class CatalogApiAuthenticationError(RuntimeError):
    """Fixed, secret-safe authentication infrastructure failure."""


def _rollback_without_diagnostics(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def hash_api_key(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_scopes(scopes: Iterable[object] | None) -> tuple[str, ...]:
    return tuple(
        normalized
        for scope in scopes or []
        if (normalized := str(scope).strip()) in PLANNED_CATALOG_API_SCOPES
    )


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.headers.get("x-symgov-api-key", "").strip()


def authenticate_catalog_api_key(
    session: Session,
    token: str,
    *,
    now: datetime | None = None,
) -> IntegrationAuthContext | None:
    if not token:
        return None
    resolved_now = now or utc_now()
    try:
        key_row = session.query(CatalogApiKey).filter(CatalogApiKey.key_hash == hash_api_key(token)).one_or_none()
    except SQLAlchemyError:
        raise CatalogApiAuthenticationError("Catalog API key authentication failed") from None
    if key_row is None:
        return None
    if key_row.status != "active" or key_row.revoked_at is not None:
        return None
    expires_at = key_row.expires_at
    if isinstance(expires_at, datetime) and _as_aware_utc(expires_at) <= _as_aware_utc(resolved_now):
        return None

    scopes = _normalize_scopes(key_row.scopes_json)
    key_row.last_used_at = _as_aware_utc(resolved_now)
    return IntegrationAuthContext(
        api_key_id=str(key_row.id),
        customer_name=redact_catalog_credential_label(key_row.customer_name),
        integration_name=redact_catalog_credential_label(key_row.integration_name),
        scopes=scopes,
        key_prefix=redact_catalog_credential_label(key_row.key_prefix),
    )


def get_catalog_api_key_context(
    request: Request,
    session: Session = Depends(get_db_session),
) -> IntegrationAuthContext:
    try:
        context = authenticate_catalog_api_key(session, _bearer_token(request))
    except CatalogApiAuthenticationError:
        _rollback_without_diagnostics(session)
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None
    if context is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        session.commit()
    except SQLAlchemyError:
        _rollback_without_diagnostics(session)
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None
    return context


def require_catalog_scope(required_scope: str) -> Callable[[IntegrationAuthContext], IntegrationAuthContext]:
    normalized_scope = str(required_scope or "").strip()

    def dependency(
        context: IntegrationAuthContext = Depends(get_catalog_api_key_context),
    ) -> IntegrationAuthContext:
        if normalized_scope not in context.scopes:
            raise HTTPException(status_code=403, detail="Insufficient scope for this operation.")
        return context

    return dependency
