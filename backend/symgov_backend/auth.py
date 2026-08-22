from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Iterable

from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .models import AuthOrganizationSelectionChallenge, User, UserRole, UserSession, UserSubscription
from .subscriptions import PROTECTED_OWNER_EMAIL, ensure_subscription
from .settings import SymgovAPISettings, get_settings

PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 260_000
DEFAULT_INITIAL_PIN = "4590"
VALID_ROLES = {"admin", "integrator", "submitter", "reviewer"}
SESSION_TOKEN_BYTES = 32


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str
    display_name: str
    roles: tuple[str, ...]
    must_change_pin: bool
    subscription_tier: str = "plus"
    subscription_started_on: date = date(1970, 1, 1)
    subscription_expires_on: date | None = None
    subscription_is_protected: bool = False
    session_purpose: str = "application"
    session_mode: str = "personal"
    active_organization_id: str | None = None
    organization_code: str | None = None
    organization_display_name: str | None = None
    organization_base_role: str | None = None
    organization_capabilities: tuple[str, ...] = ()
    is_platform_admin: bool = False
    recent_step_up_at: datetime | None = None


@dataclass(frozen=True)
class ReviewOperationActor:
    id: uuid.UUID
    display_name: str
    effective_role: str
    roles: tuple[str, ...]


def derive_review_operation_actor(user: AuthenticatedUser) -> ReviewOperationActor:
    actor_id = uuid.UUID(str(user.id))
    display_name = normalize_display_name(user.display_name)
    roles = tuple(sorted({str(role).strip().lower() for role in user.roles if str(role).strip()}))
    if "reviewer" in roles:
        effective_role = "reviewer"
    elif "admin" in roles:
        effective_role = "admin"
    else:
        raise ValueError("Authenticated user is not authorized for a review operation.")
    return ReviewOperationActor(
        id=actor_id,
        display_name=display_name,
        effective_role=effective_role,
        roles=roles,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def validate_pin(pin: str) -> str:
    if not isinstance(pin, str) or len(pin) != 4 or not pin.isdigit():
        raise ValueError("PIN must be exactly four digits.")
    return pin


def hash_pin(pin: str) -> str:
    normalized = validate_pin(pin)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, PIN_HASH_ITERATIONS)
    return "$".join(
        [
            PIN_HASH_ALGORITHM,
            str(PIN_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        normalized = validate_pin(pin)
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != PIN_HASH_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def normalize_email(email: str) -> str:
    normalized = str(email or "").strip().lower()
    if "@" not in normalized or len(normalized) < 3:
        raise ValueError("Email address is required.")
    return normalized


def normalize_display_name(display_name: str) -> str:
    normalized = str(display_name or "").strip()
    if not normalized:
        raise ValueError("Display name is required.")
    return normalized


def normalize_roles(roles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(role or "").strip().lower() for role in roles if str(role or "").strip()}))
    invalid = [role for role in normalized if role not in VALID_ROLES]
    if invalid:
        raise ValueError(f"Unsupported user role(s): {', '.join(invalid)}")
    return normalized


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    return raw_token, hash_session_token(raw_token)


def user_roles(session: Session, user_id: uuid.UUID) -> tuple[str, ...]:
    rows = session.query(UserRole.role).filter(UserRole.user_id == user_id).order_by(UserRole.role).all()
    return tuple(row[0] for row in rows)


def upsert_user(
    session: Session,
    *,
    email: str,
    display_name: str,
    roles: Iterable[str],
    pin: str = DEFAULT_INITIAL_PIN,
    must_change_pin: bool = True,
) -> User:
    normalized_email = normalize_email(email)
    normalized_display_name = normalize_display_name(display_name)
    normalized_roles = normalize_roles(roles)
    now = utc_now()

    conflicting_name = (
        session.query(User)
        .filter(func.lower(User.display_name) == normalized_display_name.lower(), func.lower(User.email) != normalized_email)
        .one_or_none()
    )
    if conflicting_name is not None:
        raise ValueError("Display name is already in use.")

    user = session.query(User).filter(func.lower(User.email) == normalized_email).one_or_none()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=normalized_email,
            display_name=normalized_display_name,
            pin_hash=hash_pin(pin),
            pin_set_at=now,
            must_change_pin=must_change_pin,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
    else:
        user.email = normalized_email
        user.display_name = normalized_display_name
        user.updated_at = now

    subscription = ensure_subscription(session, user)
    allowed_roles = normalized_roles if subscription.tier == "plus" else ()
    if normalized_email == PROTECTED_OWNER_EMAIL:
        allowed_roles = tuple(sorted(set(allowed_roles) | {"admin"}))
    session.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
    for role in allowed_roles:
        session.add(UserRole(user_id=user.id, role=role, created_at=now))
    session.flush()
    return user


def authenticate_user(session: Session, *, email: str, pin: str) -> User | None:
    try:
        normalized_email = normalize_email(email)
    except ValueError:
        return None
    user = session.query(User).filter(func.lower(User.email) == normalized_email).one_or_none()
    if user is None or not user.is_active or user.deleted_at is not None:
        return None
    if not verify_pin(pin, user.pin_hash):
        return None
    return user


def create_user_session(
    session: Session,
    *,
    user: User,
    ttl_hours: float = 24 * 14,
    purpose: str | None = None,
    session_mode: str = "personal",
    active_organization_id: uuid.UUID | None = None,
    recent_step_up_at: datetime | None = None,
) -> str:
    session.flush()
    locked_user = (
        session.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if locked_user is None or not locked_user.is_active or locked_user.deleted_at is not None:
        raise ValueError("An active user is required to issue a session.")
    raw_token, token_hash = create_session_token()
    now = utc_now()
    resolved_purpose = purpose or ("credential_change" if locked_user.must_change_pin else "application")
    if resolved_purpose == "application" and locked_user.must_change_pin:
        raise ValueError("PIN change is required before issuing an application session.")
    if resolved_purpose == "credential_change" and not locked_user.must_change_pin:
        raise ValueError("A credential-change session requires a pending PIN change.")
    session.add(
        UserSession(
            id=uuid.uuid4(),
            auth_user_id=locked_user.id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            revoked_at=None,
            last_seen_at=now,
            purpose=resolved_purpose,
            session_mode=session_mode,
            active_organization_id=active_organization_id,
            recent_step_up_at=recent_step_up_at,
        )
    )
    session.flush()
    return raw_token


def complete_credential_change(
    session: Session,
    *,
    token: str,
    current_pin: str,
    new_pin: str,
) -> tuple[User, str | None]:
    if not token:
        raise ValueError("Authentication required.")
    token_hash = hash_session_token(token)
    initial_session = session.query(UserSession).filter(UserSession.token_hash == token_hash).one_or_none()
    if initial_session is None:
        raise ValueError("Authentication required.")
    user = (
        session.query(User)
        .filter(User.id == initial_session.auth_user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if user is None or not user.is_active or user.deleted_at is not None:
        raise ValueError("Authentication required.")
    session_row = (
        session.query(UserSession)
        .filter(UserSession.id == initial_session.id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    now = utc_now()
    if (
        session_row is None
        or session_row.revoked_at is not None
        or _as_aware_utc(session_row.expires_at) <= _as_aware_utc(now)
    ):
        raise ValueError("Authentication required.")
    if session_row.purpose == "credential_change" and not user.must_change_pin:
        raise ValueError("Authentication required.")
    if session_row.purpose == "application" and user.must_change_pin:
        raise ValueError("Authentication required.")
    if not verify_pin(current_pin, user.pin_hash):
        raise ValueError("Current PIN is incorrect.")
    if verify_pin(new_pin, user.pin_hash):
        raise ValueError("New PIN must be different from the current PIN.")
    user.pin_hash = hash_pin(new_pin)
    user.pin_set_at = now
    user.must_change_pin = False
    user.updated_at = now
    session_row.recent_step_up_at = None
    revoke_outstanding_selection_challenges(session, user.id, now=now)
    session.flush()
    if session_row.purpose == "credential_change":
        session_row.revoked_at = now
        session.flush()
        return user, None
    return user, token


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def current_user_from_token(
    session: Session,
    token: str | None,
    *,
    now: datetime | None = None,
    settings: SymgovAPISettings | None = None,
    before_maintenance: Callable[[bool, str], None] | None = None,
) -> AuthenticatedUser | None:
    if not token:
        return None
    resolved_now = now or utc_now()
    session_row = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one_or_none()
    if session_row is None or session_row.revoked_at is not None:
        return None
    if _as_aware_utc(session_row.expires_at) <= _as_aware_utc(resolved_now):
        return None
    user = session.get(User, session_row.auth_user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return None
    if before_maintenance is not None:
        before_maintenance(bool(user.must_change_pin), session_row.purpose)
    resolved_settings = settings or get_settings()
    subscription = ensure_subscription(session, user, as_of=_as_aware_utc(resolved_now).date())
    session_row.last_seen_at = _as_aware_utc(resolved_now)
    organization_context = None
    if session_row.session_mode == "organization" and session_row.active_organization_id is not None:
        from .organization_authorization import resolve_bound_organization_context

        organization_context = resolve_bound_organization_context(session, user, session_row.active_organization_id, resolved_settings)
        if organization_context is None:
            return None
    return AuthenticatedUser(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=user_roles(session, user.id) if subscription.tier == "plus" else (),
        must_change_pin=bool(user.must_change_pin),
        subscription_tier=subscription.tier,
        subscription_started_on=subscription.started_on,
        subscription_expires_on=subscription.expires_on,
        subscription_is_protected=bool(subscription.is_protected),
        session_purpose=session_row.purpose,
        session_mode=session_row.session_mode,
        active_organization_id=str(session_row.active_organization_id) if session_row.active_organization_id else None,
        organization_code=organization_context.code if organization_context else None,
        organization_display_name=organization_context.display_name if organization_context else None,
        organization_base_role=organization_context.base_role if organization_context else None,
        organization_capabilities=organization_context.capabilities if organization_context else (),
        is_platform_admin=organization_context.is_platform_admin if organization_context else False,
        recent_step_up_at=_as_aware_utc(session_row.recent_step_up_at) if session_row.recent_step_up_at else None,
    )


def authoritative_user_from_token(
    session: Session,
    token: str | None,
    *,
    now: datetime | None = None,
    settings: SymgovAPISettings | None = None,
) -> AuthenticatedUser | None:
    """Revalidate a session while retaining the user lock through caller side effects."""
    if not token:
        return None
    token_hash = hash_session_token(token)
    initial_session = (
        session.query(UserSession)
        .filter(UserSession.token_hash == token_hash)
        .populate_existing()
        .one_or_none()
    )
    if initial_session is None:
        return None

    user = (
        session.query(User)
        .filter(User.id == initial_session.auth_user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    session_row = (
        session.query(UserSession)
        .filter(UserSession.id == initial_session.id, UserSession.token_hash == token_hash)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    resolved_now = now or utc_now()
    if (
        user is None
        or not user.is_active
        or user.deleted_at is not None
        or session_row is None
        or session_row.auth_user_id != user.id
        or session_row.revoked_at is not None
        or _as_aware_utc(session_row.expires_at) <= _as_aware_utc(resolved_now)
    ):
        return None

    cached_subscription = session.get(UserSubscription, user.id)
    if cached_subscription is not None:
        session.refresh(cached_subscription)
    resolved_settings = settings or get_settings()
    subscription = ensure_subscription(session, user, as_of=_as_aware_utc(resolved_now).date())
    organization_context = None
    if session_row.session_mode == "organization" and session_row.active_organization_id is not None:
        from .organization_authorization import resolve_bound_organization_context

        organization_context = resolve_bound_organization_context(session, user, session_row.active_organization_id, resolved_settings)
        if organization_context is None:
            return None
    return AuthenticatedUser(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=user_roles(session, user.id) if subscription.tier == "plus" else (),
        must_change_pin=bool(user.must_change_pin),
        subscription_tier=subscription.tier,
        subscription_started_on=subscription.started_on,
        subscription_expires_on=subscription.expires_on,
        subscription_is_protected=bool(subscription.is_protected),
        session_purpose=session_row.purpose,
        session_mode=session_row.session_mode,
        active_organization_id=str(session_row.active_organization_id) if session_row.active_organization_id else None,
        organization_code=organization_context.code if organization_context else None,
        organization_display_name=organization_context.display_name if organization_context else None,
        organization_base_role=organization_context.base_role if organization_context else None,
        organization_capabilities=organization_context.capabilities if organization_context else (),
        is_platform_admin=organization_context.is_platform_admin if organization_context else False,
        recent_step_up_at=_as_aware_utc(session_row.recent_step_up_at) if session_row.recent_step_up_at else None,
    )


def revoke_session(session: Session, token: str, *, now: datetime | None = None) -> bool:
    if not token:
        return False
    session_row = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one_or_none()
    if session_row is None or session_row.revoked_at is not None:
        return False
    revoked_at = now or utc_now()
    session_row.revoked_at = revoked_at
    revoke_outstanding_selection_challenges(session, session_row.auth_user_id, now=revoked_at)
    session.flush()
    return True


def revoke_all_user_sessions(session: Session, user_id: uuid.UUID, *, now: datetime | None = None) -> int:
    revoked_at = now or utc_now()
    revoked = session.query(UserSession).filter(
        UserSession.auth_user_id == user_id,
        UserSession.revoked_at.is_(None),
    ).update({UserSession.revoked_at: revoked_at}, synchronize_session=False)
    revoke_outstanding_selection_challenges(session, user_id, now=revoked_at)
    return revoked


def revoke_outstanding_selection_challenges(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    bind = session.get_bind()
    if bind is None:
        return 0
    table_name = AuthOrganizationSelectionChallenge.__tablename__
    if bind.dialect.name == "sqlite":
        exists = session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        ).first() is not None
        if not exists:
            return 0
    elif not inspect(bind).has_table(table_name):
        return 0
    revoked_at = now or utc_now()
    return session.query(AuthOrganizationSelectionChallenge).filter(
        AuthOrganizationSelectionChallenge.user_id == user_id,
        AuthOrganizationSelectionChallenge.consumed_at.is_(None),
        AuthOrganizationSelectionChallenge.revoked_at.is_(None),
    ).update(
        {
            AuthOrganizationSelectionChallenge.revoked_at: revoked_at,
            AuthOrganizationSelectionChallenge.updated_at: revoked_at,
        },
        synchronize_session=False,
    )
