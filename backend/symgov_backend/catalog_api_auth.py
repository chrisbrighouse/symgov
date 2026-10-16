from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .catalog_api_key_policy import organization_api_keys_allowed
from .catalog_developer import redact_catalog_credential_label
from .dependencies import _canonical_source_origin, get_db_session
from .models import (
    CatalogApiKey,
    CatalogApiKeyHolder,
    CatalogApiRateWindow,
    Organization,
    OrganizationMembership,
    User,
)
from .settings import SymgovAPISettings, get_settings

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
    key_kind: str = "personal"
    organization_id: str | None = None
    holder_user_id: str | None = None
    allowed_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int | None = None


class CatalogApiKeyPolicyError(Exception):
    """A key that authenticated but may not make this request."""

    def __init__(self, status_code: int, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


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
    update_last_used: bool = True,
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
    try:
        holder = session.get(CatalogApiKeyHolder, key_row.id)
        if holder is None or not _holder_eligible(session, holder):
            return None
    except SQLAlchemyError:
        raise CatalogApiAuthenticationError("Catalog API key authentication failed") from None

    scopes = _normalize_scopes(key_row.scopes_json)
    if update_last_used:
        key_row.last_used_at = _as_aware_utc(resolved_now)
    return IntegrationAuthContext(
        api_key_id=str(key_row.id),
        customer_name=redact_catalog_credential_label(key_row.customer_name),
        integration_name=redact_catalog_credential_label(key_row.integration_name),
        scopes=scopes,
        key_prefix=redact_catalog_credential_label(key_row.key_prefix),
        key_kind=holder.key_kind,
        organization_id=str(holder.organization_id) if holder.organization_id else None,
        holder_user_id=str(holder.holder_user_id),
        allowed_origins=tuple(str(origin) for origin in (getattr(key_row, "allowed_origins_json", None) or []) if isinstance(origin, str)),
        rate_limit_per_minute=getattr(key_row, "rate_limit_per_minute", None),
    )


def _holder_eligible(session: Session, holder: CatalogApiKeyHolder) -> bool:
    """Whether the key's holder (and, for an organization key, the organization) may still use it.

    Checked on every request, not only when the membership changes, so a
    deactivated user's key stops working at once whatever path deactivated them.
    """
    user = session.get(User, holder.holder_user_id)
    if user is None or not user.is_active:
        return False
    if holder.key_kind != "organization":
        return True
    if holder.claimed_at is None or holder.revoke_reason is not None:
        return False
    if not organization_api_keys_allowed(session.get(Organization, holder.organization_id)):
        return False
    return session.query(OrganizationMembership.id).filter(
        OrganizationMembership.organization_id == holder.organization_id,
        OrganizationMembership.user_id == holder.holder_user_id,
        OrganizationMembership.status == "active",
    ).first() is not None


def _origin_key(value: str) -> tuple[str, str, int] | None:
    return _canonical_source_origin(value)


def effective_rate_limit(context: IntegrationAuthContext, settings: SymgovAPISettings) -> int:
    default = (
        settings.catalog_api_organization_rate_limit_per_minute
        if context.key_kind == "organization"
        else settings.catalog_api_personal_rate_limit_per_minute
    )
    limit = context.rate_limit_per_minute if context.rate_limit_per_minute and context.rate_limit_per_minute > 0 else default
    return max(1, min(limit, settings.catalog_api_max_rate_limit_per_minute))


def _count_request(session: Session, api_key_id: str, window_start: datetime) -> int:
    insert = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
    table = CatalogApiRateWindow.__table__
    key_id = uuid.UUID(api_key_id)
    statement = insert(table).values(api_key_id=key_id, window_start=window_start, request_count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.api_key_id, table.c.window_start],
        set_={"request_count": table.c.request_count + 1},
    ).returning(table.c.request_count)
    count = session.execute(statement).scalar_one()
    # Only the current minute is ever read. When a key opens a new minute,
    # drop its windows older than an hour.
    if count == 1:
        session.query(CatalogApiRateWindow).filter(
            CatalogApiRateWindow.api_key_id == key_id,
            CatalogApiRateWindow.window_start < window_start - timedelta(hours=1),
        ).delete(synchronize_session=False)
    return int(count)


def enforce_key_policies(
    session: Session,
    request: Request,
    context: IntegrationAuthContext,
    settings: SymgovAPISettings,
    *,
    now: datetime | None = None,
) -> None:
    """The key's own limits: its origin allow-list, then its per-minute budget.

    A request with no Origin header (a server calling the API) is not
    origin-checked. A browser request must come from Symgov's own origin or one
    the key lists; an empty list therefore permits no foreign origin. Raises
    `CatalogApiKeyPolicyError`; the 429 carries `Retry-After` and
    `X-RateLimit-*`. The caller must commit the session afterwards, so the
    count of a refused request is kept.
    """
    origin = request.headers.get("origin")
    if origin:
        presented = _origin_key(origin)
        permitted = {
            canonical
            for value in (*settings.csrf_trusted_origins, *context.allowed_origins)
            if (canonical := _origin_key(value)) is not None
        }
        if presented is None or presented not in permitted:
            raise CatalogApiKeyPolicyError(403, "This origin is not permitted for this API key.")

    resolved_now = _as_aware_utc(now or utc_now())
    window_start = resolved_now.replace(second=0, microsecond=0)
    limit = effective_rate_limit(context, settings)
    count = _count_request(session, context.api_key_id, window_start)
    if count > limit:
        reset = int((window_start.timestamp() + 60) - resolved_now.timestamp())
        raise CatalogApiKeyPolicyError(
            429,
            "Rate limit exceeded for this API key.",
            {
                "Retry-After": str(max(1, reset)),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(window_start.timestamp() + 60)),
            },
        )


def apply_key_policies(session: Session, request: Request, context: IntegrationAuthContext, settings: SymgovAPISettings) -> None:
    """`enforce_key_policies` as an HTTP outcome, committing the window count either way."""
    try:
        enforce_key_policies(session, request, context, settings)
    except CatalogApiKeyPolicyError as error:
        try:
            session.commit()
        except SQLAlchemyError:
            _rollback_without_diagnostics(session)
        raise HTTPException(status_code=error.status_code, detail=error.detail, headers=error.headers or None) from None
    except SQLAlchemyError:
        _rollback_without_diagnostics(session)
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None


def get_catalog_api_key_context(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
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
    apply_key_policies(session, request, context, settings)
    try:
        session.commit()
    except SQLAlchemyError:
        _rollback_without_diagnostics(session)
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None
    return context


def get_catalog_feedback_api_key_context(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> IntegrationAuthContext:
    """Authenticate feedback without committing usage before request acceptance."""
    try:
        context = authenticate_catalog_api_key(
            session,
            _bearer_token(request),
            update_last_used=False,
        )
    except CatalogApiAuthenticationError:
        _rollback_without_diagnostics(session)
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None
    if context is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    apply_key_policies(session, request, context, settings)
    return context


def require_catalog_feedback_scope(
    required_scope: str,
) -> Callable[[IntegrationAuthContext], IntegrationAuthContext]:
    normalized_scope = str(required_scope or "").strip()

    def dependency(
        context: IntegrationAuthContext = Depends(get_catalog_feedback_api_key_context),
    ) -> IntegrationAuthContext:
        if normalized_scope not in context.scopes:
            raise HTTPException(status_code=403, detail="Insufficient scope for this operation.")
        return context

    return dependency


def require_catalog_scope(required_scope: str) -> Callable[[IntegrationAuthContext], IntegrationAuthContext]:
    normalized_scope = str(required_scope or "").strip()

    def dependency(
        context: IntegrationAuthContext = Depends(get_catalog_api_key_context),
    ) -> IntegrationAuthContext:
        if normalized_scope not in context.scopes:
            raise HTTPException(status_code=403, detail="Insufficient scope for this operation.")
        return context

    return dependency
