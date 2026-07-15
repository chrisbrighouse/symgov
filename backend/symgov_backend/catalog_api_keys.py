from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import uuid

from sqlalchemy.orm import Session

from .catalog_api_auth import PLANNED_CATALOG_API_SCOPES, hash_api_key
from .models import AuditEvent, CatalogApiKey


_RAW_KEY_PREFIX = "symgov_live_"
_DISPLAY_SECRET_CHARS = 8
_VALID_STATUSES = frozenset({"active", "disabled", "revoked"})


class CatalogApiKeyError(ValueError):
    """Base error for safe Catalog API-key lifecycle operations."""


class CatalogApiKeyNotFoundError(CatalogApiKeyError):
    """Raised when a requested immutable key ID does not exist."""


class CatalogApiKeyPrefixMismatchError(CatalogApiKeyError):
    """Raised when revoke confirmation does not exactly match the stored prefix."""


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
    raw_key: str

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
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise CatalogApiKeyError(f"{field_name} is required")
    return normalized


def _normalized_scopes(scopes: object) -> tuple[str, ...]:
    if scopes is None or isinstance(scopes, (str, bytes)):
        candidates = [] if scopes is None else [scopes]
    else:
        try:
            candidates = list(scopes)  # type: ignore[arg-type]
        except TypeError as exc:
            raise CatalogApiKeyError("scopes must be an iterable") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        scope = str(candidate or "").strip()
        if not scope or scope in seen:
            continue
        if scope not in PLANNED_CATALOG_API_SCOPES:
            raise CatalogApiKeyError(f"unsupported Catalog API scope: {scope}")
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


def _audit_payload(row: CatalogApiKey) -> dict[str, object]:
    return {
        "actor_type": "operator_cli",
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
        created_by=None,
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
            actor_id=None,
            payload_json=_audit_payload(key_row),
            created_at=resolved_now,
        )
    )
    session.flush()
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
        status_filter = str(status).strip().lower()
        if status_filter not in _VALID_STATUSES:
            raise CatalogApiKeyError(f"unsupported Catalog API key status: {status}")

    query = session.query(CatalogApiKey)
    if customer_filter is not None:
        query = query.filter(CatalogApiKey.customer_name == customer_filter)
    if status_filter is not None:
        query = query.filter(CatalogApiKey.status == status_filter)
    rows = query.order_by(CatalogApiKey.created_at.asc(), CatalogApiKey.id.asc()).all()
    filtered = [
        row
        for row in rows
        if (customer_filter is None or row.customer_name.casefold() == customer_filter.casefold())
        and (status_filter is None or row.status == status_filter)
    ]
    filtered.sort(key=lambda row: (row.created_at, str(row.id)))
    return [_safe_dto(row) for row in filtered]


def revoke_catalog_api_key(
    session: Session,
    api_key_id: uuid.UUID | str,
    *,
    key_prefix: str,
    now: datetime | None = None,
) -> CatalogApiKeyDTO:
    """Revoke by immutable UUID and exact display-prefix confirmation."""
    try:
        resolved_id = api_key_id if isinstance(api_key_id, uuid.UUID) else uuid.UUID(str(api_key_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CatalogApiKeyNotFoundError("Catalog API key not found") from exc

    row = session.query(CatalogApiKey).filter(CatalogApiKey.id == resolved_id).one_or_none()
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
            actor_id=None,
            payload_json=_audit_payload(row),
            created_at=resolved_now,
        )
    )
    session.flush()
    return _safe_dto(row)
