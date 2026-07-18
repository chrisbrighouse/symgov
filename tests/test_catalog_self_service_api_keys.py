from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.catalog_api_keys import (
    CatalogApiKeyAlreadyActiveError,
    CatalogApiKeyNotFoundError,
    create_self_service_catalog_api_key,
    get_active_self_service_catalog_api_key,
    revoke_self_service_catalog_api_key,
)
from symgov_backend.models import AuditEvent, CatalogApiKey, User


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000202")


class FakeQuery:
    def __init__(self, rows, events, label):
        self.rows = list(rows)
        self.events = events
        self.label = label
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def with_for_update(self):
        self.events.append(f"lock:{self.label}")
        return self

    def _matching(self):
        rows = list(self.rows)
        for criterion in self.criteria:
            field = getattr(criterion.left, "name", None)
            expected = getattr(criterion.right, "value", None)
            rows = [row for row in rows if getattr(row, field) == expected]
        return rows

    def one_or_none(self):
        rows = self._matching()
        if len(rows) > 1:
            raise AssertionError("query unexpectedly returned multiple rows")
        return rows[0] if rows else None

    def all(self):
        return self._matching()


class FakeSession:
    def __init__(self, keys=(), users=(USER_ID, OTHER_USER_ID)):
        self.keys = list(keys)
        self.users = [SimpleNamespace(id=user_id) for user_id in users]
        self.added = []
        self.events = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is User:
            self.events.append("query:user")
            return FakeQuery(self.users, self.events, "user")
        if model is CatalogApiKey:
            self.events.append("query:key")
            return FakeQuery(self.keys, self.events, "key")
        raise AssertionError(f"unexpected query model {model}")

    def add(self, value):
        self.added.append(value)
        if isinstance(value, CatalogApiKey) and value not in self.keys:
            self.keys.append(value)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def key_row(*, owner=USER_ID, expires_at=None, status="active"):
    return CatalogApiKey(
        id=uuid.uuid4(),
        customer_name="Acme",
        integration_name="CAD portal",
        key_prefix="symgov_live_12345678",
        key_hash="hashed-only",
        scopes_json=["catalog.read"],
        status=status,
        contact_name=None,
        contact_email=None,
        allowed_origins_json=[],
        rate_limit_per_minute=None,
        expires_at=expires_at,
        last_used_at=None,
        created_by=owner,
        created_at=NOW,
        updated_at=NOW,
        revoked_at=None,
        notes=None,
    )


def test_self_service_create_locks_user_and_attributes_key_and_audit():
    session = FakeSession()

    created = create_self_service_catalog_api_key(
        session,
        user_id=USER_ID,
        customer_name="Acme",
        integration_name="CAD portal",
        scopes=["catalog.read", "catalog.preview"],
        expires_at=NOW + timedelta(days=30),
        now=NOW,
    )

    assert session.events[:3] == ["query:user", "lock:user", "query:key"]
    stored = next(value for value in session.added if isinstance(value, CatalogApiKey))
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert stored.created_by == USER_ID
    assert created.key.id == stored.id
    assert audit.actor_id == USER_ID
    assert audit.payload_json["actor_type"] == "integrator_self_service"
    assert created.raw_key not in repr(audit.payload_json)
    assert session.commits == session.rollbacks == 0


def test_self_service_create_rejects_second_current_key_without_generating_one():
    existing = key_row(expires_at=NOW + timedelta(days=1))
    session = FakeSession([existing])

    with pytest.raises(CatalogApiKeyAlreadyActiveError):
        create_self_service_catalog_api_key(
            session,
            user_id=USER_ID,
            customer_name="Acme",
            integration_name="Second integration",
            scopes=["catalog.read"],
            now=NOW,
        )

    assert session.keys == [existing]
    assert session.added == []
    assert session.flushes == 0


def test_expired_self_service_key_is_retired_before_replacement():
    expired = key_row(expires_at=NOW - timedelta(seconds=1))
    session = FakeSession([expired])

    created = create_self_service_catalog_api_key(
        session,
        user_id=USER_ID,
        customer_name="Acme",
        integration_name="Replacement",
        scopes=["catalog.read"],
        now=NOW,
    )

    assert expired.status == "revoked"
    assert expired.revoked_at == NOW
    assert created.key.id != expired.id
    assert [event.action for event in session.added if isinstance(event, AuditEvent)] == [
        "catalog_api_key.expired",
        "catalog_api_key.created",
    ]


def test_status_returns_only_current_nonexpired_key_for_requesting_owner():
    own = key_row(expires_at=NOW + timedelta(days=1))
    other = key_row(owner=OTHER_USER_ID, expires_at=NOW + timedelta(days=1))
    expired = key_row(expires_at=NOW - timedelta(seconds=1))

    assert get_active_self_service_catalog_api_key(FakeSession([other, expired]), USER_ID, now=NOW) is None
    result = get_active_self_service_catalog_api_key(FakeSession([other, own]), USER_ID, now=NOW)
    assert result is not None
    assert result.id == own.id
    assert "key_hash" not in result.to_dict()


def test_status_and_revoke_redact_credential_bearing_legacy_labels():
    raw_key = "symgov_live_legacy-label-secret"
    key_hash = hash_api_key(raw_key)
    own = key_row(expires_at=NOW + timedelta(days=1))
    own.customer_name = raw_key
    own.integration_name = key_hash
    own.key_prefix = raw_key
    own.scopes_json = [raw_key, key_hash, "catalog.read"]
    session = FakeSession([own])

    status = get_active_self_service_catalog_api_key(session, USER_ID, now=NOW)
    assert status is not None
    assert raw_key not in repr(status)
    assert key_hash not in repr(status)
    assert raw_key not in repr(status.to_dict())
    assert key_hash not in repr(status.to_dict())
    assert status.key_prefix == "[REDACTED]"
    assert status.scopes == ("catalog.read",)

    revoked = revoke_self_service_catalog_api_key(
        session,
        user_id=USER_ID,
        api_key_id=own.id,
        key_prefix=own.key_prefix,
        now=NOW + timedelta(minutes=1),
    )
    audit = [value for value in session.added if isinstance(value, AuditEvent)][-1]
    for surface in (repr(revoked), repr(revoked.to_dict()), repr(audit.payload_json)):
        assert raw_key not in surface
        assert key_hash not in surface


def test_self_service_revoke_is_owner_scoped_and_requires_exact_prefix():
    own = key_row(expires_at=NOW + timedelta(days=1))
    session = FakeSession([own])

    with pytest.raises(CatalogApiKeyNotFoundError):
        revoke_self_service_catalog_api_key(
            session,
            user_id=OTHER_USER_ID,
            api_key_id=own.id,
            key_prefix=own.key_prefix,
            now=NOW + timedelta(minutes=1),
        )

    assert own.status == "active"
    revoked = revoke_self_service_catalog_api_key(
        session,
        user_id=USER_ID,
        api_key_id=own.id,
        key_prefix=own.key_prefix,
        now=NOW + timedelta(minutes=1),
    )
    assert revoked.status == "revoked"
    audit = [value for value in session.added if isinstance(value, AuditEvent)][-1]
    assert audit.actor_id == USER_ID
    assert audit.payload_json["actor_type"] == "integrator_self_service"
    assert session.commits == session.rollbacks == 0
