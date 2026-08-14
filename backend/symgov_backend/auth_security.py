from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import math
import uuid

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import normalize_email, utc_now, verify_pin
from .models import AuthLoginAttemptEvent, AuthLoginThrottleBucket, AuthThrottleRecoveryEvent, User
from .settings import SymgovAPISettings


@dataclass(frozen=True)
class LoginThrottlePolicy:
    account_failure_limit: int
    ip_failure_limit: int
    window_seconds: int
    block_seconds: int
    hash_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.account_failure_limit <= 100:
            raise ValueError("Account login failure limit must be between 1 and 100.")
        if not 1 <= self.ip_failure_limit <= 1000:
            raise ValueError("IP login failure limit must be between 1 and 1000.")
        if not 1 <= self.window_seconds <= 86_400:
            raise ValueError("Login throttle window must be between 1 and 86400 seconds.")
        if not 1 <= self.block_seconds <= 86_400:
            raise ValueError("Login throttle block duration must be between 1 and 86400 seconds.")
        if self.hash_secret in {"", "symgov-local-auth-throttle-v1"}:
            raise ValueError("A deployment-provided login throttle hash secret is required.")
        if len(self.hash_secret) < 16:
            raise ValueError("Login throttle hash secret must be at least 16 characters.")


@dataclass(frozen=True)
class LoginAuthenticationResult:
    user: User | None
    throttled_scope: str | None = None
    retry_after_seconds: int | None = None


def login_throttle_policy(settings: SymgovAPISettings) -> LoginThrottlePolicy:
    local_only_secrets = {
        "symgov-explicit-test-auth-throttle-secret",
        "symgov-explicit-local-auth-throttle-secret",
    }
    if settings.environment not in {"local", "test"} and settings.auth_login_hash_secret in local_only_secrets:
        raise ValueError("A deployment-provided login throttle hash secret is required.")
    return LoginThrottlePolicy(
        account_failure_limit=settings.auth_login_account_failure_limit,
        ip_failure_limit=settings.auth_login_ip_failure_limit,
        window_seconds=settings.auth_login_window_seconds,
        block_seconds=settings.auth_login_block_seconds,
        hash_secret=settings.auth_login_hash_secret,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash_key(policy: LoginThrottlePolicy, scope: str, value: str) -> str:
    return hmac.new(
        policy.hash_secret.encode("utf-8"),
        f"{scope}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalize_ip(client_ip: str | None) -> str | None:
    if not client_ip:
        return None
    try:
        return ipaddress.ip_address(client_ip.strip()).compressed
    except ValueError:
        return None


def _bucket(
    session: Session,
    *,
    scope: str,
    key_hash: str,
    now: datetime,
    policy: LoginThrottlePolicy,
) -> AuthLoginThrottleBucket:
    row = session.query(AuthLoginThrottleBucket).filter(
        AuthLoginThrottleBucket.scope == scope,
        AuthLoginThrottleBucket.bucket_key_hash == key_hash,
    ).with_for_update().one_or_none()
    if row is None:
        candidate = AuthLoginThrottleBucket(
            id=uuid.uuid4(),
            scope=scope,
            bucket_key_hash=key_hash,
            window_started_at=now,
            failure_count=0,
            blocked_until=None,
            updated_at=now,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            row = candidate
        except IntegrityError:
            row = session.query(AuthLoginThrottleBucket).filter(
                AuthLoginThrottleBucket.scope == scope,
                AuthLoginThrottleBucket.bucket_key_hash == key_hash,
            ).with_for_update().one()
    block_is_active = row.blocked_until is not None and _aware(row.blocked_until) > _aware(now)
    if not block_is_active and (_aware(now) - _aware(row.window_started_at)).total_seconds() >= policy.window_seconds:
        row.window_started_at = now
        row.failure_count = 0
        row.blocked_until = None
        row.updated_at = now
    return row


def _active_block(row: AuthLoginThrottleBucket, now: datetime) -> int | None:
    if row.blocked_until is None or _aware(row.blocked_until) <= _aware(now):
        return None
    return max(1, math.ceil((_aware(row.blocked_until) - _aware(now)).total_seconds()))


def _record_attempt(
    session: Session,
    *,
    now: datetime,
    email_hash: str,
    user: User | None,
    ip_hash: str | None,
    outcome: str,
    failure_reason: str | None,
) -> None:
    session.add(
        AuthLoginAttemptEvent(
            id=uuid.uuid4(),
            occurred_at=now,
            email_key_hash=email_hash,
            resolved_user_id=user.id if user is not None else None,
            client_ip_hash=ip_hash,
            outcome=outcome,
            failure_reason=failure_reason,
            request_metadata_json=json.dumps({"transport": "http"}, separators=(",", ":"), sort_keys=True),
        )
    )


def authenticate_login(
    session: Session,
    *,
    email: str,
    pin: str,
    client_ip: str | None,
    policy: LoginThrottlePolicy,
    now: datetime | None = None,
) -> LoginAuthenticationResult:
    resolved_now = now or utc_now()
    try:
        normalized_email = normalize_email(email)
    except ValueError:
        normalized_email = str(email or "").strip().lower()[:320]
    normalized_ip = _normalize_ip(client_ip)
    email_hash = _hash_key(policy, "account", normalized_email)
    ip_hash = _hash_key(policy, "ip", normalized_ip) if normalized_ip else None
    user = (
        session.query(User)
        .filter(func.lower(User.email) == normalized_email)
        .with_for_update()
        .one_or_none()
    )

    account_bucket = _bucket(
        session,
        scope="account",
        key_hash=email_hash,
        now=resolved_now,
        policy=policy,
    )
    account_retry = _active_block(account_bucket, resolved_now)
    if account_retry is not None:
        _record_attempt(
            session,
            now=resolved_now,
            email_hash=email_hash,
            user=user,
            ip_hash=ip_hash,
            outcome="throttled",
            failure_reason="throttled_account",
        )
        return LoginAuthenticationResult(None, "account", account_retry)

    ip_bucket = None
    if ip_hash is not None:
        ip_bucket = _bucket(session, scope="ip", key_hash=ip_hash, now=resolved_now, policy=policy)
        ip_retry = _active_block(ip_bucket, resolved_now)
        if ip_retry is not None:
            _record_attempt(
                session,
                now=resolved_now,
                email_hash=email_hash,
                user=user,
                ip_hash=ip_hash,
                outcome="throttled",
                failure_reason="throttled_ip",
            )
            return LoginAuthenticationResult(None, "ip", ip_retry)

    valid_user = user is not None and user.is_active and user.deleted_at is None
    if valid_user and verify_pin(pin, user.pin_hash):
        session.delete(account_bucket)
        _record_attempt(
            session,
            now=resolved_now,
            email_hash=email_hash,
            user=user,
            ip_hash=ip_hash,
            outcome="success",
            failure_reason=None,
        )
        return LoginAuthenticationResult(user)

    account_bucket.failure_count += 1
    account_bucket.updated_at = resolved_now
    if account_bucket.failure_count >= policy.account_failure_limit:
        account_bucket.blocked_until = resolved_now + timedelta(seconds=policy.block_seconds)
    if ip_bucket is not None:
        ip_bucket.failure_count += 1
        ip_bucket.updated_at = resolved_now
        if ip_bucket.failure_count >= policy.ip_failure_limit:
            ip_bucket.blocked_until = resolved_now + timedelta(seconds=policy.block_seconds)
    _record_attempt(
        session,
        now=resolved_now,
        email_hash=email_hash,
        user=user,
        ip_hash=ip_hash,
        outcome="failure",
        failure_reason="inactive_or_deleted" if user is not None and not valid_user else "invalid_credentials",
    )
    return LoginAuthenticationResult(None)


def recover_throttle_bucket(
    session: Session,
    *,
    scope: str,
    key: str,
    actor_id: uuid.UUID,
    reason: str,
    policy: LoginThrottlePolicy,
    now: datetime | None = None,
) -> int:
    if scope not in {"account", "ip"}:
        raise ValueError("Throttle recovery scope must be account or ip.")
    normalized_key = normalize_email(key) if scope == "account" else _normalize_ip(key)
    if not normalized_key:
        raise ValueError("Throttle recovery target is invalid.")
    normalized_reason = str(reason or "").strip()
    if not 10 <= len(normalized_reason) <= 500:
        raise ValueError("Throttle recovery reason must be between 10 and 500 characters.")
    target_hash = _hash_key(policy, scope, normalized_key)
    cleared = session.query(AuthLoginThrottleBucket).filter(
        AuthLoginThrottleBucket.scope == scope,
        AuthLoginThrottleBucket.bucket_key_hash == target_hash,
    ).delete(synchronize_session=False)
    session.add(
        AuthThrottleRecoveryEvent(
            id=uuid.uuid4(),
            actor_id=actor_id,
            scope=scope,
            target_key_hash=target_hash,
            reason=normalized_reason,
            cleared_count=cleared,
            created_at=now or utc_now(),
        )
    )
    return cleared
