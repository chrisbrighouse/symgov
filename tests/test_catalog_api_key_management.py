from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import traceback
import uuid

import pytest
from sqlalchemy.exc import SQLAlchemyError

from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.catalog_api_keys import (
    CatalogApiKeyError,
    CatalogApiKeyNotFoundError,
    CatalogApiKeyPrefixMismatchError,
    create_catalog_api_key,
    list_catalog_api_keys,
    revoke_catalog_api_key,
)
from symgov_backend.models import AuditEvent, CatalogApiKey


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, rows, events):
        self.rows = rows
        self.events = events
        self.criteria = []
        self.ordering = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def order_by(self, *ordering):
        self.ordering.extend(ordering)
        return self

    def with_for_update(self):
        self.events.append("with_for_update")
        return self

    def one_or_none(self):
        self.events.append("one_or_none")
        key_id = next(
            (criterion.right.value for criterion in self.criteria if getattr(criterion.left, "name", None) == "id"),
            None,
        )
        return next((row for row in self.rows if row.id == key_id), None)

    def all(self):
        rows = list(self.rows)
        for criterion in self.criteria:
            field_name = criterion.left.name
            expected = criterion.right.value
            rows = [item for item in rows if getattr(item, field_name) == expected]
        return sorted(rows, key=lambda item: (item.created_at, item.id))


class FakeSession:
    def __init__(self, rows=(), *, flush_error=None):
        self.rows = list(rows)
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.flush_error = flush_error
        self.query_events = []
        self.queries = []

    def add(self, row):
        self.added.append(row)
        if isinstance(row, CatalogApiKey):
            self.rows.append(row)

    def flush(self):
        self.flushes += 1
        if self.flush_error is not None:
            raise self.flush_error

    def query(self, model):
        assert model is CatalogApiKey
        query = FakeQuery(self.rows, self.query_events)
        self.queries.append(query)
        return query

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


def test_create_result_repr_hides_raw_key_but_explicit_output_returns_it(monkeypatch):
    marker = "repr-secret-marker"
    monkeypatch.setattr("symgov_backend.catalog_api_keys.secrets.token_urlsafe", lambda _size: marker)

    created = create_catalog_api_key(
        FakeSession(),
        customer_name="Acme",
        integration_name="CAD",
        scopes=["catalog.read"],
        now=NOW,
    )

    assert marker not in repr(created)
    assert marker not in f"create result: {created!r}"
    assert created.raw_key == f"symgov_live_{marker}"
    assert created.to_dict()["raw_key"] == created.raw_key


def test_create_flush_failure_raises_only_secret_safe_domain_error(monkeypatch):
    secret_marker = "flush-secret-marker"
    raw_key = f"symgov_live_{secret_marker}"
    hash_marker = hash_api_key(raw_key)
    monkeypatch.setattr("symgov_backend.catalog_api_keys.secrets.token_urlsafe", lambda _size: secret_marker)
    session = FakeSession(flush_error=SQLAlchemyError(f"database rejected key_hash={hash_marker}"))

    with pytest.raises(CatalogApiKeyError) as caught:
        create_catalog_api_key(
            session,
            customer_name="Acme",
            integration_name="CAD",
            scopes=["catalog.read"],
            now=NOW,
        )

    rendered = "".join(traceback.format_exception(caught.value))
    assert str(caught.value) == "Unable to create Catalog API key"
    assert raw_key not in repr(caught.value)
    assert raw_key not in rendered
    assert hash_marker not in repr(caught.value)
    assert hash_marker not in rendered
    assert session.flushes == 1
    assert session.commits == session.rollbacks == 0


def test_create_validates_required_names_scopes_and_expiry_before_adding():
    invalid_cases = [
        {"customer_name": " ", "integration_name": "CAD", "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": " ", "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": []},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.download"]},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.read"], "expires_at": NOW.replace(tzinfo=None)},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": ["catalog.read"], "expires_at": NOW},
        {"customer_name": object(), "integration_name": "CAD", "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": object(), "scopes": ["catalog.read"]},
        {"customer_name": "Acme", "integration_name": "CAD", "scopes": [object()]},
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


def test_create_rejected_scope_error_does_not_render_the_rejected_value():
    raw_key = "symgov_live_scope-secret-marker"
    session = FakeSession()

    with pytest.raises(CatalogApiKeyError) as caught:
        create_catalog_api_key(
            session,
            customer_name="Acme",
            integration_name="CAD",
            scopes=[raw_key],
            now=NOW,
        )

    rendered = "".join(traceback.format_exception(caught.value))
    assert raw_key not in str(caught.value)
    assert raw_key not in repr(caught.value)
    assert raw_key not in rendered
    assert session.rows == session.added == session.queries == []
    assert session.flushes == session.commits == session.rollbacks == 0


def test_create_iterable_type_error_does_not_chain_attacker_value():
    raw_key = "symgov_live_iterable-error-secret"

    class LeakingIterable:
        def __iter__(self):
            raise TypeError(f"cannot iterate {raw_key}")

    session = FakeSession()
    with pytest.raises(CatalogApiKeyError) as caught:
        create_catalog_api_key(
            session,
            customer_name="Acme",
            integration_name="CAD",
            scopes=LeakingIterable(),
            now=NOW,
        )

    rendered = "".join(traceback.format_exception(caught.value))
    assert str(caught.value) == "scopes must be an iterable"
    assert raw_key not in rendered
    assert session.rows == session.added == session.queries == []


@pytest.mark.parametrize("field_name", ["customer_name", "integration_name"])
@pytest.mark.parametrize(
    "credential",
    [
        "symgov_live_label-secret-marker",
        hash_api_key("symgov_live_label-secret-marker"),
    ],
)
def test_create_rejects_credential_bearing_labels_before_persistence(field_name, credential):
    session = FakeSession()
    values = {
        "customer_name": "Acme",
        "integration_name": "CAD",
        "scopes": ["catalog.read"],
    }
    values[field_name] = credential

    with pytest.raises(CatalogApiKeyError) as caught:
        create_catalog_api_key(session, now=NOW, **values)

    rendered = "".join(traceback.format_exception(caught.value))
    assert credential not in str(caught.value)
    assert credential not in repr(caught.value)
    assert credential not in rendered
    assert session.rows == session.added == session.queries == []
    assert session.flushes == session.commits == session.rollbacks == 0


def test_list_filters_safely_and_has_stable_order_and_serialization():
    older_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    newer_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    unrelated_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    rows = [
        row(id=unrelated_id, customer_name="Other", status="revoked", created_at=NOW + timedelta(hours=1), revoked_at=NOW),
        row(id=older_id, customer_name="Acme", integration_name="B", created_at=NOW),
        row(id=newer_id, customer_name="Acme", integration_name="A", created_at=NOW),
    ]

    session = FakeSession(rows)
    listed = list_catalog_api_keys(session, customer_name=" Acme ", status="active")

    assert [item.integration_name for item in listed] == ["A", "B"]
    assert all(item.customer_name == "Acme" and item.status == "active" for item in listed)
    query = session.queries[0]
    compiled_criteria = [
        str(criterion.compile(compile_kwargs={"literal_binds": True}))
        for criterion in query.criteria
    ]
    assert compiled_criteria == [
        "catalog_api_keys.customer_name = 'Acme'",
        "catalog_api_keys.status = 'active'",
    ]
    assert [str(ordering) for ordering in query.ordering] == [
        "catalog_api_keys.created_at ASC",
        "catalog_api_keys.id ASC",
    ]
    for item in listed:
        serialized = asdict(item)
        serialized.update(item.to_dict())
        assert "key_hash" not in serialized
        assert "raw_key" not in serialized
        assert hash_api_key("not-returned") not in repr(serialized)


@pytest.mark.parametrize(
    "contaminated_prefix",
    [
        "symgov_live_legacy-prefix-secret",
        hash_api_key("symgov_live_legacy-prefix-secret"),
    ],
)
def test_list_and_revoke_redact_credential_contaminated_legacy_prefix(contaminated_prefix):
    key = row(key_prefix=contaminated_prefix)
    session = FakeSession([key])

    listed = list_catalog_api_keys(session)
    revoked = revoke_catalog_api_key(
        session,
        key.id,
        key_prefix=contaminated_prefix,
        now=NOW + timedelta(minutes=1),
    )
    audit = next(item for item in session.added if isinstance(item, AuditEvent))

    assert listed[0].key_prefix == "[REDACTED]"
    assert revoked.key_prefix == "[REDACTED]"
    assert audit.payload_json["key_prefix"] == "[REDACTED]"
    for surface in (repr(listed), repr(revoked), repr(revoked.to_dict()), repr(audit.payload_json)):
        assert contaminated_prefix not in surface


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        ({"customer_name": object()}, "customer_name must be a string"),
        ({"status": object()}, "status must be a string"),
    ],
)
def test_list_rejects_non_string_filters_before_querying(filters, message):
    session = FakeSession()

    with pytest.raises(CatalogApiKeyError, match=message):
        list_catalog_api_keys(session, **filters)

    assert session.queries == []


def test_list_rejected_status_error_does_not_render_the_rejected_value():
    raw_key = "symgov_live_status-secret-marker"
    session = FakeSession()

    with pytest.raises(CatalogApiKeyError) as caught:
        list_catalog_api_keys(session, status=raw_key)

    rendered = "".join(traceback.format_exception(caught.value))
    assert raw_key not in str(caught.value)
    assert raw_key not in repr(caught.value)
    assert raw_key not in rendered
    assert session.rows == session.added == session.queries == []
    assert session.flushes == session.commits == session.rollbacks == 0


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
    with pytest.raises(CatalogApiKeyNotFoundError):
        revoke_catalog_api_key(session, "not-a-uuid", key_prefix=key.key_prefix, now=NOW + timedelta(minutes=1))
    with pytest.raises(CatalogApiKeyNotFoundError):
        revoke_catalog_api_key(session, object(), key_prefix=key.key_prefix, now=NOW + timedelta(minutes=1))
    with pytest.raises(CatalogApiKeyPrefixMismatchError):
        revoke_catalog_api_key(session, key.id, key_prefix=object(), now=NOW + timedelta(minutes=1))

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
    assert session.query_events == [
        "with_for_update",
        "one_or_none",
        "with_for_update",
        "one_or_none",
    ]
    assert session.commits == session.rollbacks == 0
