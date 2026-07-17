from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import User, UserRole, UserSession

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
    if not normalized:
        raise ValueError("At least one role is required.")
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

    session.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
    for role in normalized_roles:
        session.add(UserRole(user_id=user.id, role=role, created_at=now))
    session.flush()
    return user


def authenticate_user(session: Session, *, email: str, pin: str) -> User | None:
    try:
        normalized_email = normalize_email(email)
    except ValueError:
        return None
    user = session.query(User).filter(func.lower(User.email) == normalized_email).one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_pin(pin, user.pin_hash):
        return None
    return user


def create_user_session(session: Session, *, user: User, ttl_hours: int = 24 * 14) -> str:
    raw_token, token_hash = create_session_token()
    now = utc_now()
    session.add(
        UserSession(
            id=uuid.uuid4(),
            auth_user_id=user.id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            revoked_at=None,
            last_seen_at=now,
        )
    )
    session.flush()
    return raw_token


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def current_user_from_token(session: Session, token: str, *, now: datetime | None = None) -> AuthenticatedUser | None:
    if not token:
        return None
    resolved_now = now or utc_now()
    session_row = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one_or_none()
    if session_row is None or session_row.revoked_at is not None:
        return None
    if _as_aware_utc(session_row.expires_at) <= _as_aware_utc(resolved_now):
        return None
    user = session.get(User, session_row.auth_user_id)
    if user is None or not user.is_active:
        return None
    session_row.last_seen_at = _as_aware_utc(resolved_now)
    return AuthenticatedUser(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=user_roles(session, user.id),
        must_change_pin=bool(user.must_change_pin),
    )


def revoke_session(session: Session, token: str, *, now: datetime | None = None) -> bool:
    if not token:
        return False
    session_row = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one_or_none()
    if session_row is None or session_row.revoked_at is not None:
        return False
    session_row.revoked_at = now or utc_now()
    session.flush()
    return True
