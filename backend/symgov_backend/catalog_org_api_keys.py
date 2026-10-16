"""Organization Catalog API keys (WP-A).

An organization key is issued by an organization admin to one member of that
organization, the holder. Issuing makes the key and its holder row but
discloses nothing: the secret does not exist yet. The holder *claims* the key,
which generates the secret and returns it once; it cannot be read again.

A claimed key stays usable only while its holder is an active member of an
organization whose keys are allowed (`catalog_api_key_policy`). That is checked
on every request (`catalog_api_auth._holder_eligible`). On top of that, losing
the membership, or the organization being suspended, *revokes* the key
(`revoke_organization_keys_for`), so putting the member back later does not
bring an old key back to life: revocation is permanent, a new key is issued.
"""

from __future__ import annotations

from datetime import datetime
import secrets
import uuid

from sqlalchemy.orm import Session

from .catalog_api_auth import hash_api_key
from .catalog_api_key_policy import organization_api_keys_allowed
from .catalog_api_keys import (
    CatalogApiKeyError,
    CatalogApiKeyNotFoundError,
    _DISPLAY_SECRET_CHARS,
    _RAW_KEY_PREFIX,
    _aware_utc,
    _normalized_origins,
    _normalized_required_name,
    _normalized_scopes,
    _safe_label,
    _safe_stored_scopes,
    _serialize_datetime,
    _utc_now,
)
from .models import (
    AuditEvent,
    CatalogApiKey,
    CatalogApiKeyHolder,
    Organization,
    OrganizationMembership,
    User,
)

MAX_OPEN_KEYS_PER_HOLDER = 5
_PENDING_PREFIX = "pending"


class CatalogOrganizationKeyNotAllowedError(CatalogApiKeyError):
    """The organization or the holder may not have an organization key."""


class CatalogOrganizationKeyAlreadyClaimedError(CatalogApiKeyError):
    """The key has already been claimed; its secret cannot be shown again."""


def _audit(
    session: Session, row: CatalogApiKey, action: str, actor_id: uuid.UUID | None, now: datetime, *, actor_type: str, **extra: object
) -> None:
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="catalog_api_key",
            entity_id=row.id,
            action=action,
            actor_id=actor_id,
            payload_json={
                "actor_type": actor_type,
                "api_key_id": str(row.id),
                "key_prefix": _safe_label(row.key_prefix),
                "integration_name": _safe_label(row.integration_name),
                "scopes": list(_safe_stored_scopes(row.scopes_json)),
                **extra,
            },
            created_at=now,
        )
    )


def _key_view(row: CatalogApiKey, holder: CatalogApiKeyHolder, *, holder_user: User | None = None) -> dict[str, object]:
    if holder.revoke_reason is not None or row.status == "revoked":
        state = "revoked"
    elif holder.claimed_at is None:
        state = "pending_claim"
    elif row.expires_at is not None and row.expires_at <= _utc_now():  # type: ignore[operator]
        state = "expired"
    else:
        state = "active"
    view: dict[str, object] = {
        "keyId": str(row.id),
        "keyKind": holder.key_kind,
        "organizationId": str(holder.organization_id) if holder.organization_id else None,
        "integrationName": _safe_label(row.integration_name),
        "keyPrefix": None if holder.claimed_at is None else _safe_label(row.key_prefix),
        "scopes": list(_safe_stored_scopes(row.scopes_json)),
        "state": state,
        "rateLimitPerMinute": row.rate_limit_per_minute,
        "allowedOrigins": list(row.allowed_origins_json or []),
        "expiresAt": _serialize_datetime(row.expires_at),  # type: ignore[arg-type]
        "lastUsedAt": _serialize_datetime(row.last_used_at),  # type: ignore[arg-type]
        "createdAt": _serialize_datetime(row.created_at),  # type: ignore[arg-type]
        "claimedAt": _serialize_datetime(holder.claimed_at),  # type: ignore[arg-type]
        "revokedAt": _serialize_datetime(row.revoked_at),  # type: ignore[arg-type]
        "revokeReason": holder.revoke_reason,
        "holderUserId": str(holder.holder_user_id),
    }
    if holder_user is not None:
        view["holderDisplayName"] = holder_user.display_name
        view["holderEmail"] = holder_user.email
    return view


def _active_member(session: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return session.query(OrganizationMembership.id).filter(
        OrganizationMembership.organization_id == organization_id,
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.status == "active",
    ).first() is not None


def issue_organization_catalog_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    issuer_user_id: uuid.UUID,
    holder_user_id: uuid.UUID,
    integration_name: str,
    scopes: object,
    expires_at: datetime | None = None,
    rate_limit_per_minute: int | None = None,
    allowed_origins: object = None,
    max_rate_limit_per_minute: int = 600,
    now: datetime | None = None,
) -> dict[str, object]:
    """Issue a key to a member. The caller has already established the issuer is an organization admin."""
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    organization = session.get(Organization, organization_id)
    if not organization_api_keys_allowed(organization):
        raise CatalogOrganizationKeyNotAllowedError("This organization cannot have API keys.")
    holder_user = session.query(User).filter(User.id == holder_user_id).with_for_update().one_or_none()
    if holder_user is None or not holder_user.is_active or not _active_member(session, organization_id, holder_user_id):
        raise CatalogOrganizationKeyNotAllowedError("The key holder must be an active member of this organization.")

    integration = _normalized_required_name(integration_name, field_name="integration_name")
    normalized_scopes = _normalized_scopes(scopes)
    resolved_expiry = None
    if expires_at is not None:
        resolved_expiry = _aware_utc(expires_at, field_name="expires_at")
        if resolved_expiry <= resolved_now:
            raise CatalogApiKeyError("expires_at must be in the future")
    if rate_limit_per_minute is not None and not (1 <= rate_limit_per_minute <= max_rate_limit_per_minute):
        raise CatalogApiKeyError(f"rate limit must be between 1 and {max_rate_limit_per_minute} requests per minute")
    origins = _normalized_origins(allowed_origins)

    open_keys = (
        session.query(CatalogApiKeyHolder)
        .join(CatalogApiKey, CatalogApiKey.id == CatalogApiKeyHolder.api_key_id)
        .filter(
            CatalogApiKeyHolder.holder_user_id == holder_user_id,
            CatalogApiKeyHolder.organization_id == organization_id,
            CatalogApiKeyHolder.revoke_reason.is_(None),
            CatalogApiKey.status == "active",
        )
        .count()
    )
    if open_keys >= MAX_OPEN_KEYS_PER_HOLDER:
        raise CatalogOrganizationKeyNotAllowedError("This member already holds the maximum number of API keys.")

    row = CatalogApiKey(
        id=uuid.uuid4(),
        customer_name=_safe_label(organization.display_name),
        integration_name=integration,
        key_prefix=_PENDING_PREFIX,
        # Hash of a secret that is generated and thrown away: nothing can match
        # it, so the key is unusable until the holder claims it.
        key_hash=hash_api_key(secrets.token_urlsafe(48)),
        scopes_json=list(normalized_scopes),
        status="active",
        contact_name=None,
        contact_email=None,
        allowed_origins_json=origins,
        rate_limit_per_minute=rate_limit_per_minute,
        expires_at=resolved_expiry,
        last_used_at=None,
        created_by=issuer_user_id,
        created_at=resolved_now,
        updated_at=resolved_now,
        revoked_at=None,
        notes=None,
    )
    session.add(row)
    session.flush()
    holder = CatalogApiKeyHolder(
        api_key_id=row.id,
        key_kind="organization",
        holder_user_id=holder_user_id,
        organization_id=organization_id,
        issued_by_user_id=issuer_user_id,
        claimed_at=None,
        revoke_reason=None,
    )
    session.add(holder)
    _audit(session, row, "catalog_api_key.issued", issuer_user_id, resolved_now, actor_type="organization_admin", organization_id=str(organization_id), holder_user_id=str(holder_user_id))
    session.flush()
    return _key_view(row, holder, holder_user=holder_user)


def claim_organization_catalog_api_key(
    session: Session, *, api_key_id: uuid.UUID, user_id: uuid.UUID, now: datetime | None = None
) -> tuple[dict[str, object], str]:
    """The holder takes delivery: the secret is made now and returned once."""
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    row = session.query(CatalogApiKey).filter(CatalogApiKey.id == api_key_id).with_for_update().one_or_none()
    holder = session.get(CatalogApiKeyHolder, api_key_id)
    if row is None or holder is None or holder.key_kind != "organization" or holder.holder_user_id != user_id:
        # Another member's key is indistinguishable from no key.
        raise CatalogApiKeyNotFoundError("Catalog API key not found")
    if holder.claimed_at is not None:
        raise CatalogOrganizationKeyAlreadyClaimedError("This key has already been claimed.")
    user = session.get(User, user_id)
    organization = session.get(Organization, holder.organization_id)
    if (
        holder.revoke_reason is not None
        or row.status != "active"
        or user is None
        or not user.is_active
        or not organization_api_keys_allowed(organization)
        or not _active_member(session, holder.organization_id, user_id)
    ):
        raise CatalogOrganizationKeyNotAllowedError("This key can no longer be claimed.")
    if row.expires_at is not None and _aware_utc(row.expires_at, field_name="expires_at") <= resolved_now:  # type: ignore[arg-type]
        raise CatalogOrganizationKeyNotAllowedError("This key has expired.")

    secret = secrets.token_urlsafe(32)
    raw_key = f"{_RAW_KEY_PREFIX}{secret}"
    row.key_hash = hash_api_key(raw_key)
    row.key_prefix = f"{_RAW_KEY_PREFIX}{secret[:_DISPLAY_SECRET_CHARS]}"
    row.updated_at = resolved_now
    holder.claimed_at = resolved_now
    _audit(session, row, "catalog_api_key.claimed", user_id, resolved_now, actor_type="key_holder", organization_id=str(holder.organization_id))
    session.flush()
    return _key_view(row, holder, holder_user=user), raw_key


def revoke_organization_catalog_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    api_key_id: uuid.UUID,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> dict[str, object]:
    """An organization admin revokes one of the organization's keys."""
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    holder = session.get(CatalogApiKeyHolder, api_key_id)
    if holder is None or holder.key_kind != "organization" or holder.organization_id != organization_id:
        raise CatalogApiKeyNotFoundError("Catalog API key not found")
    row = session.query(CatalogApiKey).filter(CatalogApiKey.id == api_key_id).with_for_update().one()
    if holder.revoke_reason is None and row.status != "revoked":
        _revoke(session, row, holder, "revoked_by_admin", actor_id, resolved_now, actor_type="organization_admin")
    return _key_view(row, holder, holder_user=session.get(User, holder.holder_user_id))


def _revoke(
    session: Session, row: CatalogApiKey, holder: CatalogApiKeyHolder, reason: str, actor_id: uuid.UUID | None, now: datetime,
    *, actor_type: str, **extra: object,
) -> None:
    row.status = "revoked"
    row.revoked_at = now
    row.updated_at = now
    holder.revoke_reason = reason
    _audit(session, row, "catalog_api_key.revoked", actor_id, now, actor_type=actor_type, reason=reason, **extra)


def revoke_organization_keys_for(
    session: Session,
    *,
    reason: str,
    user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Permanently revoke every live organization key of a user (in one organization, if given) or of an organization.

    Called from the places that end a membership, suspend an organization or
    deactivate a user. The per-request eligibility check already stops such a
    key working; this makes it stay stopped.
    """
    if user_id is None and organization_id is None:
        raise ValueError("A user or an organization is required.")
    resolved_now = _aware_utc(now, field_name="now") if now is not None else _utc_now()
    query = (
        session.query(CatalogApiKey, CatalogApiKeyHolder)
        .join(CatalogApiKeyHolder, CatalogApiKeyHolder.api_key_id == CatalogApiKey.id)
        .filter(CatalogApiKeyHolder.key_kind == "organization", CatalogApiKeyHolder.revoke_reason.is_(None))
    )
    if user_id is not None:
        query = query.filter(CatalogApiKeyHolder.holder_user_id == user_id)
    if organization_id is not None:
        query = query.filter(CatalogApiKeyHolder.organization_id == organization_id)
    count = 0
    for row, holder in query.all():
        _revoke(session, row, holder, reason, actor_id, resolved_now, actor_type="system", organization_id=str(holder.organization_id))
        count += 1
    if count:
        session.flush()
    return count


def list_organization_catalog_api_keys(session: Session, organization_id: uuid.UUID) -> list[dict[str, object]]:
    rows = (
        session.query(CatalogApiKey, CatalogApiKeyHolder, User)
        .join(CatalogApiKeyHolder, CatalogApiKeyHolder.api_key_id == CatalogApiKey.id)
        .join(User, User.id == CatalogApiKeyHolder.holder_user_id)
        .filter(CatalogApiKeyHolder.organization_id == organization_id, CatalogApiKeyHolder.key_kind == "organization")
        .order_by(CatalogApiKey.created_at.desc(), CatalogApiKey.id)
        .all()
    )
    return [_key_view(row, holder, holder_user=user) for row, holder, user in rows]


def list_holder_catalog_api_keys(session: Session, user_id: uuid.UUID) -> list[dict[str, object]]:
    """The keys a user holds on an organization's behalf, claimed or waiting to be."""
    rows = (
        session.query(CatalogApiKey, CatalogApiKeyHolder)
        .join(CatalogApiKeyHolder, CatalogApiKeyHolder.api_key_id == CatalogApiKey.id)
        .filter(CatalogApiKeyHolder.holder_user_id == user_id, CatalogApiKeyHolder.key_kind == "organization")
        .order_by(CatalogApiKey.created_at.desc(), CatalogApiKey.id)
        .all()
    )
    views = []
    for row, holder in rows:
        view = _key_view(row, holder)
        organization = session.get(Organization, holder.organization_id)
        view["organizationName"] = organization.display_name if organization else None
        views.append(view)
    return views
