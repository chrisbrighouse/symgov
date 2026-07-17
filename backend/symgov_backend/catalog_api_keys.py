from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
from typing import cast
import uuid

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .catalog_api_auth import PLANNED_CATALOG_API_SCOPES, hash_api_key
from .models import AuditEvent, CatalogApiKey, User


_RAW_KEY_PREFIX = "symgov_live_"
_DISPLAY_SECRET_CHARS = 8
_VALID_STATUSES = frozenset({"active", "disabled", "revoked"})


class CatalogApiKeyError(ValueError):
    """Base error for safe Catalog API-key lifecycle operations."""


class CatalogApiKeyNotFoundError(CatalogApiKeyError):
    """Raised when a requested immutable key ID does not exist."""


class CatalogApiKeyPrefixMismatchError(CatalogApiKeyError):
    """Raised when revoke confirmation does not exactly match the stored prefix."""


class CatalogApiKeyAlreadyActiveError(CatalogApiKeyError):
    """Raised when a user already owns a current self-service key."""


@dataclass(frozen=True)
class CatalogApiKeyDTO:
    id: uuid.UUID
    customer_name: str
    integration_name: str
    key_prefix: str
    scopes: tuple[str, ...]
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe metadata that cannot contain a key secret or hash."""
        return {
            "id": str(self.id),
            "customer_name": self.customer_name,
            "integration_name": self.integration_name,
            "key_prefix": self.key_prefix,
            "scopes": list(self.scopes),
            "status": self.status,
            "expires_at": _serialize_datetime(self.expires_at),
            "last_used_at": _serialize_datetime(self.last_used_at),
            "created_at": _serialize_datetime(self.created_at),
            "updated_at": _serialize_datetime(self.updated_at),
            "revoked_at": _serialize_datetime(self.revoked_at),
        }


@dataclass(frozen=True)
class CatalogApiKeyCreateDTO:
    """Creation result; ``raw_key`` is returned once and is never persisted."""

    key: CatalogApiKeyDTO
    raw_key: str = field(repr=False)

    @property
    def api_key(self) -> str:
        """Compatibility-friendly name for the one-time credential."""
        return self.raw_key

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key.to_dict(), "raw_key": self.raw_key}

    def __getattr__(self, name: str) -> object:
        # Keep common DTO metadata convenient without duplicating fields in the
        # creation serialization surface.
        return getattr(self.key, name)


# Descriptive aliases for adapters that prefer result/summary terminology.
CatalogApiKeySummary = CatalogApiKeyDTO
CreatedCatalogApiKey = CatalogApiKeyCreateDTO


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CatalogApiKeyError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _normalized_required_name(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise CatalogApiKeyError(f"{field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise CatalogApiKeyError(f"{field_name} is required")
    return normalized


def _normalized_scopes(scopes: object) -> tuple[str, ...]:
    if scopes is None or isinstance(scopes, str):
        candidates = [] if scopes is None else [scopes]
    else:
        try:
            candidates = list(scopes)  # type: ignore[arg-type]
        except TypeError as exc:
            raise CatalogApiKeyError("scopes must be an iterable") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise CatalogApiKeyError("Catalog API scopes must be strings")
        scope = candidate.strip()
        if not scope or scope in seen:
            continue
        if scope not in PLANNED_CATALOG_API_SCOPES:
            raise CatalogApiKeyError("unsupported Catalog API scope")
        seen.add(scope)
        normalized.append(scope)
    if not normalized:
        raise CatalogApiKeyError("at least one Catalog API scope is required")
    return tuple(normalized)


def _safe_dto(row: CatalogApiKey) -> CatalogApiKeyDTO:
    return CatalogApiKeyDTO(
        id=row.id,
        customer_name=row.customer_name,
        integration_name=row.integration_name,
        key_prefix=row.key_prefix,
        scopes=tuple(str(scope) for scope in (row.scopes_json or [])),
        status=row.status,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
    )


def _audit_payload(row: CatalogApiKey, *, actor_type: str = "operator_cli") -> dict[str, object]:
    return {
        "actor_type": actor_type,
        "api_key_id": str(row.id),
        "key_prefix": row.key_prefix,
        "customer_name": row.customer_name,
        "integration_name": row.integration_name,
        "scopes": list(row.scopes_json or []),
    }


def create_catalog_api_key(
    session: Session,
    *,
    customer_name: str,
    integration_name: str,
    scopes: object,
    expires_at: datetime | None = None,
    now: datetime | None = None,
    created_by: uuid.UUID | None = None,
    actor_type: str = "operator_cli",
) -> CatalogApiKeyCreateDTO:
    """Create a key and audit row without taking ownership of the transaction."""
    customer = _normalized_required_name(customer_name, field_name="customer_name")
    integration = _normalized_required_name(integration_name, field_name="integration_name")
    normalized_scopes = _normalized_scopes(scopes)
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    resolved_expiry = None
    if expires_at is not None:
        resolved_expiry = _aware_utc(expires_at, field_name="expires_at")
        if resolved_expiry <= resolved_now:
            raise CatalogApiKeyError("expires_at must be in the future")

    secret = secrets.token_urlsafe(32)
    raw_key = f"{_RAW_KEY_PREFIX}{secret}"
    key_row = CatalogApiKey(
        id=uuid.uuid4(),
        customer_name=customer,
        integration_name=integration,
        key_prefix=f"{_RAW_KEY_PREFIX}{secret[:_DISPLAY_SECRET_CHARS]}",
        key_hash=hash_api_key(raw_key),
        scopes_json=list(normalized_scopes),
        status="active",
        contact_name=None,
        contact_email=None,
        allowed_origins_json=[],
        rate_limit_per_minute=None,
        expires_at=resolved_expiry,
        last_used_at=None,
        created_by=created_by,
        created_at=resolved_now,
        updated_at=resolved_now,
        revoked_at=None,
        notes=None,
    )
    session.add(key_row)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="catalog_api_key",
            entity_id=key_row.id,
            action="catalog_api_key.created",
            actor_id=created_by,
            payload_json=_audit_payload(key_row, actor_type=actor_type),
            created_at=resolved_now,
        )
    )
    try:
        session.flush()
    except SQLAlchemyError:
        raise CatalogApiKeyError("Unable to create Catalog API key") from None
    return CatalogApiKeyCreateDTO(key=_safe_dto(key_row), raw_key=raw_key)


def list_catalog_api_keys(
    session: Session,
    *,
    customer_name: str | None = None,
    status: str | None = None,
) -> list[CatalogApiKeyDTO]:
    """List stable, secret-safe key metadata with optional exact filters."""
    customer_filter = None
    if customer_name is not None:
        customer_filter = _normalized_required_name(customer_name, field_name="customer_name")
    status_filter = None
    if status is not None:
        if not isinstance(status, str):
            raise CatalogApiKeyError("status must be a string")
        status_filter = status.strip().lower()
        if status_filter not in _VALID_STATUSES:
            raise CatalogApiKeyError("unsupported Catalog API key status")

    query = session.query(CatalogApiKey)
    if customer_filter is not None:
        query = query.filter(CatalogApiKey.customer_name == customer_filter)
    if status_filter is not None:
        query = query.filter(CatalogApiKey.status == status_filter)
    rows = query.order_by(CatalogApiKey.created_at.asc(), CatalogApiKey.id.asc()).all()
    return [_safe_dto(row) for row in rows]


def revoke_catalog_api_key(
    session: Session,
    api_key_id: uuid.UUID | str,
    *,
    key_prefix: str,
    now: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    actor_type: str = "operator_cli",
) -> CatalogApiKeyDTO:
    """Revoke by immutable UUID and exact display-prefix confirmation."""
    try:
        if isinstance(api_key_id, uuid.UUID):
            resolved_id = api_key_id
        elif isinstance(api_key_id, str):
            resolved_id = uuid.UUID(api_key_id)
        else:
            raise TypeError
    except (TypeError, ValueError, AttributeError):
        raise CatalogApiKeyNotFoundError("Catalog API key not found") from None

    row = (
        session.query(CatalogApiKey)
        .filter(CatalogApiKey.id == resolved_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise CatalogApiKeyNotFoundError("Catalog API key not found")
    if not isinstance(key_prefix, str) or not secrets.compare_digest(row.key_prefix, key_prefix):
        raise CatalogApiKeyPrefixMismatchError("Catalog API key prefix confirmation did not match")
    if row.status == "revoked":
        return _safe_dto(row)

    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    row.status = "revoked"
    row.revoked_at = resolved_now
    row.updated_at = resolved_now
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="catalog_api_key",
            entity_id=row.id,
            action="catalog_api_key.revoked",
            actor_id=actor_id,
            payload_json=_audit_payload(row, actor_type=actor_type),
            created_at=resolved_now,
        )
    )
    session.flush()
    return _safe_dto(row)


def _user_uuid(user_id: uuid.UUID | str) -> uuid.UUID:
    try:
        return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError, AttributeError):
        raise CatalogApiKeyNotFoundError("Catalog API key owner was not found") from None


def _self_service_key_rows(session: Session, user_id: uuid.UUID) -> list[CatalogApiKey]:
    return (
        session.query(CatalogApiKey)
        .filter(CatalogApiKey.created_by == user_id, CatalogApiKey.status == "active")
        .all()
    )


def get_active_self_service_catalog_api_key(
    session: Session,
    user_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> CatalogApiKeyDTO | None:
    """Return only a current, non-secret key owned by the requesting user."""
    owner_id = _user_uuid(user_id)
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    for row in _self_service_key_rows(session, owner_id):
        if row.revoked_at is not None:
            continue
        if row.expires_at is not None and _aware_utc(cast(datetime, row.expires_at), field_name="expires_at") <= resolved_now:
            continue
        return _safe_dto(row)
    return None


def create_self_service_catalog_api_key(
    session: Session,
    *,
    user_id: uuid.UUID | str,
    customer_name: str,
    integration_name: str,
    scopes: object,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> CatalogApiKeyCreateDTO:
    """Create at most one current key for a user under a stable user-row lock."""
    owner_id = _user_uuid(user_id)
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    owner = session.query(User).filter(User.id == owner_id).with_for_update().one_or_none()
    if owner is None:
        raise CatalogApiKeyNotFoundError("Catalog API key owner was not found")

    for row in _self_service_key_rows(session, owner_id):
        is_expired = row.expires_at is not None and _aware_utc(cast(datetime, row.expires_at), field_name="expires_at") <= resolved_now
        if row.revoked_at is None and not is_expired:
            raise CatalogApiKeyAlreadyActiveError("An active Catalog API key already exists for this account")
        if is_expired and row.revoked_at is None:
            row.status = "revoked"
            row.revoked_at = resolved_now
            row.updated_at = resolved_now
            session.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    entity_type="catalog_api_key",
                    entity_id=row.id,
                    action="catalog_api_key.expired",
                    actor_id=owner_id,
                    payload_json=_audit_payload(row, actor_type="integrator_self_service"),
                    created_at=resolved_now,
                )
            )

    return create_catalog_api_key(
        session,
        customer_name=customer_name,
        integration_name=integration_name,
        scopes=scopes,
        expires_at=expires_at,
        now=resolved_now,
        created_by=owner_id,
        actor_type="integrator_self_service",
    )


def revoke_self_service_catalog_api_key(
    session: Session,
    *,
    user_id: uuid.UUID | str,
    api_key_id: uuid.UUID | str,
    key_prefix: str,
    now: datetime | None = None,
) -> CatalogApiKeyDTO:
    """Revoke only the requesting user's own key, with prefix confirmation."""
    owner_id = _user_uuid(user_id)
    try:
        resolved_key_id = api_key_id if isinstance(api_key_id, uuid.UUID) else uuid.UUID(str(api_key_id))
    except (TypeError, ValueError, AttributeError):
        raise CatalogApiKeyNotFoundError("Catalog API key not found") from None
    owned = (
        session.query(CatalogApiKey)
        .filter(CatalogApiKey.id == resolved_key_id, CatalogApiKey.created_by == owner_id)
        .with_for_update()
        .one_or_none()
    )
    if owned is None:
        raise CatalogApiKeyNotFoundError("Catalog API key not found")
    return revoke_catalog_api_key(
        session,
        resolved_key_id,
        key_prefix=key_prefix,
        now=now,
        actor_id=owner_id,
        actor_type="integrator_self_service",
    )
