from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.catalog_api_keys import (
    CatalogApiKeyNotFoundError,
    CatalogApiKeyPrefixMismatchError,
    create_catalog_api_key,
    list_catalog_api_keys,
    revoke_catalog_api_key,
)
from symgov_backend.models import AuditEvent, CatalogApiKey


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def order_by(self, *ordering):
        return self

    def one_or_none(self):
        key_id = next(
            (criterion.right.value for criterion in self.criteria if getattr(criterion.left, "name", None) == "id"),
            None,
        )
        return next((row for row in self.rows if row.id == key_id), None)

    def all(self):
        return list(self.rows)


class FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def add(self, row):
        self.added.append(row)
        if isinstance(row, CatalogApiKey):
            self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def query(self, model):
        assert model is CatalogApiKey
        return FakeQuery(self.rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def row(**overrides):
    values = {
        "id": uuid.uuid4(),
        "customer_name": "Acme",
        "integration_name": "CAD",
        "key_prefix": "symgov_live_12345678",
        "key_hash": hash_api_key("not-returned"),
        "scopes_json": ["catalog.read"],
        "status": "active",
        "contact_name": None,
        "contact_email": None,
        "allowed_origins_json": [],
        "rate_limit_per_minute": None,
        "expires_at": None,
        "last_used_at": None,
        "created_by": None,
        "created_at": NOW,
        "updated_at": NOW,
        "revoked_at": None,
        "notes": None,
    }
    values.update(overrides)
    return CatalogApiKey(**values)


def test_create_generates_random_recognizable_key_and_persists_hash_only():
    session = FakeSession()

    first = create_catalog_api_key(
        session,
        customer_name="  Acme   Engineering  ",
        integration_name="  CAD   pilot ",
        scopes=[" catalog.read ", "catalog.preview", "catalog.read"],
        now=NOW,
    )
    second = create_catalog_api_key(session, customer_name="Acme", integration_name="CAD", scopes=["catalog.read"], now=NOW)

    assert first.raw_key.startswith("symgov_live_")
    assert len(first.raw_key.removeprefix("symgov_live_")) >= 43
    assert first.raw_key != second.raw_key
    stored = session.rows[0]
    assert stored.customer_name == "Acme Engineering"
    assert stored.integration_name == "CAD pilot"
    assert stored.scopes_json == ["catalog.read", "catalog.preview"]
    assert stored.key_hash == hash_api_key(first.raw_key)
    assert first.raw_key not in stored.key_prefix
    assert stored.key_prefix.startswith("symgov_live_")
    assert len(stored.key_prefix) < len(first.raw_key)
    assert not hasattr(stored, "raw_key")
    assert session.commits == session.rollbacks == 0


def test_create_validates_required_names_scopes_and_expiry_before_adding():
    invalid_cases = [
        {"customer_name": " ", "integration_name": "CAD", "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": " ", "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": []},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.download"]},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.read"], "expires_at": NOW.replace(tzinfo=None)},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.read"], "expires_at": NOW},
    ]

    for values in invalid_cases:
        session = FakeSession()
        with pytest.raises(ValueError):
            create_catalog_api_key(session, now=NOW, **values)
        assert session.added == []
        assert session.flushes == session.commits == session.rollbacks == 0

    eastern = timezone(timedelta(hours=-4))
    result = create_catalog_api_key(
        FakeSession(),
        customer_name="Acme",
        integration_name="CAD",
        scopes=["catalog.read"],
        expires_at=(NOW + timedelta(hours=2)).astimezone(eastern),
        now=NOW,
    )
    assert result.key.expires_at == NOW + timedelta(hours=2)


def test_list_filters_safely_and_has_stable_order_and_serialization():
    older_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    newer_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    rows = [
        row(id=newer_id, customer_name="Other", status="revoked", created_at=NOW + timedelta(hours=1), revoked_at=NOW),
        row(id=older_id, customer_name="Acme", integration_name="B", created_at=NOW),
        row(id=newer_id, customer_name="Acme", integration_name="A", created_at=NOW),
    ]

    listed = list_catalog_api_keys(FakeSession(rows), customer_name=" Acme ", status="active")

    assert [item.integration_name for item in listed] == ["A", "B"]
    assert all(item.customer_name == "Acme" and item.status == "active" for item in listed)
    for item in listed:
        serialized = asdict(item)
        serialized.update(item.to_dict())
        assert "key_hash" not in serialized
        assert "raw_key" not in serialized
        assert hash_api_key("not-returned") not in repr(serialized)


def test_create_and_revoke_audits_are_safe_and_service_never_commits():
    session = FakeSession()
    created = create_catalog_api_key(
        session,
        customer_name="Acme",
        integration_name="CAD",
        scopes=["catalog.read"],
        now=NOW,
    )
    create_audit = next(item for item in session.added if isinstance(item, AuditEvent))

    assert create_audit.actor_id is None
    assert create_audit.action == "catalog_api_key.created"
    assert create_audit.payload_json == {
        "actor_type": "operator_cli",
        "api_key_id": str(created.key.id),
        "key_prefix": created.key.key_prefix,
        "customer_name": "Acme",
        "integration_name": "CAD",
        "scopes": ["catalog.read"],
    }
    assert created.raw_key not in repr(create_audit.payload_json)
    assert hash_api_key(created.raw_key) not in repr(create_audit.payload_json)

    revoked = revoke_catalog_api_key(session, created.key.id, key_prefix=created.key.key_prefix, now=NOW + timedelta(minutes=1))
    revoke_audit = [item for item in session.added if isinstance(item, AuditEvent)][-1]
    assert revoked.status == "revoked"
    assert revoke_audit.actor_id is None
    assert revoke_audit.action == "catalog_api_key.revoked"
    assert revoke_audit.payload_json == create_audit.payload_json
    assert session.commits == session.rollbacks == 0


def test_revoke_unknown_or_wrong_prefix_fails_closed_without_mutation():
    key = row()
    session = FakeSession([key])

    with pytest.raises(CatalogApiKeyNotFoundError):
        revoke_catalog_api_key(session, uuid.uuid4(), key_prefix=key.key_prefix, now=NOW + timedelta(minutes=1))
    with pytest.raises(CatalogApiKeyPrefixMismatchError):
        revoke_catalog_api_key(session, key.id, key_prefix="symgov_live_wrong", now=NOW + timedelta(minutes=1))

    assert key.status == "active"
    assert key.revoked_at is None
    assert session.added == []
    assert session.flushes == session.commits == session.rollbacks == 0


def test_revoke_is_idempotent_and_preserves_original_timestamps_and_audit():
    key = row()
    session = FakeSession([key])
    first_time = NOW + timedelta(minutes=1)

    first = revoke_catalog_api_key(session, key.id, key_prefix=key.key_prefix, now=first_time)
    audit_count = len([item for item in session.added if isinstance(item, AuditEvent)])
    second = revoke_catalog_api_key(session, key.id, key_prefix=key.key_prefix, now=NOW + timedelta(days=1))

    assert first.revoked_at == second.revoked_at == first_time
    assert first.updated_at == second.updated_at == first_time
    assert len([item for item in session.added if isinstance(item, AuditEvent)]) == audit_count == 1
    assert session.commits == session.rollbacks == 0
