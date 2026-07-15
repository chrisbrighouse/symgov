from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import pytest

import manage_symgov
from symgov_backend.catalog_api_keys import CatalogApiKeyCreateDTO, CatalogApiKeyDTO


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
KEY_ID = uuid.UUID("00000000-0000-0000-0000-000000000008")
RAW_KEY = "symgov_live_once-only-secret"
PREFIX = "symgov_live_once-onl"


class FakeSession:
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
        close_error: Exception | None = None,
        before_commit=None,
    ):
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.before_commit = before_commit

    def commit(self):
        if self.before_commit is not None:
            self.before_commit()
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def safe_dto(**overrides):
    values = {
        "id": KEY_ID,
        "customer_name": "Acme",
        "integration_name": "CAD",
        "key_prefix": PREFIX,
        "scopes": ("catalog.read",),
        "status": "active",
        "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc),
        "last_used_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "revoked_at": None,
    }
    values.update(overrides)
    return CatalogApiKeyDTO(**values)


def install_factory(monkeypatch, session):
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return lambda: session

    monkeypatch.setattr(manage_symgov, "create_session_factory", factory)
    return calls


def forbidden_keys(value):
    forbidden = {"rawKey", "raw_key", "apiKey", "keyHash", "key_hash", "hash"}
    if isinstance(value, dict):
        return forbidden.intersection(value) | set().union(*(forbidden_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(forbidden_keys(item) for item in value)) if value else set()
    return set()


def test_parser_has_catalog_commands_and_preserves_existing_commands():
    parser = manage_symgov.build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")

    assert {
        "create-catalog-api-key",
        "list-catalog-api-keys",
        "revoke-catalog-api-key",
        "seed-agent-definitions",
        "check-db",
        "serve-api",
        "process-agent-queue",
    } <= set(command_action.choices)


@pytest.mark.parametrize(
    "argv",
    [
        ["create-catalog-api-key"],
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD"],
        ["revoke-catalog-api-key"],
        ["revoke-catalog-api-key", "--key-id", str(KEY_ID)],
        ["revoke-catalog-api-key", "--confirm-prefix", PREFIX],
    ],
)
def test_required_arguments_exit_nonzero(argv, capsys):
    with pytest.raises(SystemExit) as caught:
        manage_symgov.main(argv)

    assert caught.value.code != 0
    assert RAW_KEY not in capsys.readouterr().err


def test_create_outputs_raw_key_once_only_after_commit_and_forwards_values(monkeypatch, capsys):
    def before_commit():
        assert capsys.readouterr().out == ""

    session = FakeSession(before_commit=before_commit)
    factory_calls = install_factory(monkeypatch, session)
    service_calls = []

    def create_service(_session, **kwargs):
        service_calls.append((_session, kwargs))
        return CatalogApiKeyCreateDTO(key=safe_dto(), raw_key=RAW_KEY)

    monkeypatch.setattr(manage_symgov, "create_catalog_api_key", create_service)

    result = manage_symgov.main(
        [
            "create-catalog-api-key",
            "--customer",
            "Acme",
            "--integration",
            "CAD",
            "--scope",
            "catalog.read",
            "--expires-at",
            "2027-01-01T00:00:00Z",
            "--db-env-file",
            "/tmp/catalog.env",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert captured.out.count(RAW_KEY) == 1
    assert payload == {
        "keyId": str(KEY_ID),
        "keyPrefix": PREFIX,
        "customer": "Acme",
        "integration": "CAD",
        "scopes": ["catalog.read"],
        "status": "active",
        "expiresAt": "2027-01-01T00:00:00+00:00",
        "createdAt": "2026-07-15T12:00:00+00:00",
        "rawKey": RAW_KEY,
    }
    assert captured.err == ""
    assert service_calls[0][0] is session
    assert service_calls[0][1] == {
        "customer_name": "Acme",
        "integration_name": "CAD",
        "scopes": ["catalog.read"],
        "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc),
    }
    assert factory_calls == [{"env_file": "/tmp/catalog.env", "nopool": True}]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed == 1


def test_create_close_failure_after_commit_emits_one_time_key_and_reports_cleanup(monkeypatch, capsys):
    session = FakeSession(close_error=RuntimeError(f"close leaked {RAW_KEY}"))
    install_factory(monkeypatch, session)
    monkeypatch.setattr(
        manage_symgov,
        "create_catalog_api_key",
        lambda *_args, **_kwargs: CatalogApiKeyCreateDTO(key=safe_dto(), raw_key=RAW_KEY),
    )

    result = manage_symgov.main(
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert json.loads(captured.out)["rawKey"] == RAW_KEY
    assert captured.out.count(RAW_KEY) == 1
    assert captured.err == "Catalog API key session cleanup failed.\n"
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed == 1


def test_list_outputs_safe_metadata_and_forwards_filters(monkeypatch, capsys):
    session = FakeSession()
    factory_calls = install_factory(monkeypatch, session)
    service_calls = []

    def list_service(_session, **kwargs):
        service_calls.append((_session, kwargs))
        return [safe_dto()]

    monkeypatch.setattr(manage_symgov, "list_catalog_api_keys", list_service)

    assert manage_symgov.main(
        ["list-catalog-api-keys", "--customer", "Acme", "--status", "active", "--db-env-file", "catalog.env"]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"keys": [{
        "keyId": str(KEY_ID),
        "keyPrefix": PREFIX,
        "customer": "Acme",
        "integration": "CAD",
        "scopes": ["catalog.read"],
        "status": "active",
        "expiresAt": "2027-01-01T00:00:00+00:00",
        "lastUsedAt": None,
        "createdAt": "2026-07-15T12:00:00+00:00",
        "updatedAt": "2026-07-15T12:00:00+00:00",
        "revokedAt": None,
    }]}
    assert not forbidden_keys(payload)
    assert RAW_KEY not in repr(payload)
    assert service_calls == [(session, {"customer_name": "Acme", "status": "active"})]
    assert factory_calls == [{"env_file": "catalog.env", "nopool": True}]
    assert session.commits == 1


def test_revoke_outputs_safe_result_and_forwards_uuid_and_exact_prefix(monkeypatch, capsys):
    session = FakeSession()
    factory_calls = install_factory(monkeypatch, session)
    calls = []

    def revoke_service(_session, key_id, *, key_prefix):
        calls.append((_session, key_id, key_prefix))
        return safe_dto(status="revoked", revoked_at=NOW)

    monkeypatch.setattr(manage_symgov, "revoke_catalog_api_key", revoke_service)

    assert manage_symgov.main(
        ["revoke-catalog-api-key", "--key-id", str(KEY_ID), "--confirm-prefix", PREFIX, "--db-env-file", "catalog.env"]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["keyId"] == str(KEY_ID)
    assert payload["keyPrefix"] == PREFIX
    assert payload["status"] == "revoked"
    assert not forbidden_keys(payload)
    assert calls == [(session, str(KEY_ID), PREFIX)]
    assert factory_calls == [{"env_file": "catalog.env", "nopool": True}]
    assert session.commits == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "unknown.scope"],
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", RAW_KEY],
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read", "--expires-at", "not-time"],
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read", "--expires-at", RAW_KEY],
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read", "--expires-at", "2027-01-01T00:00:00"],
        ["list-catalog-api-keys", "--status", "unknown"],
    ],
)
def test_parser_validation_fails_without_secret_leakage(argv, capsys):
    with pytest.raises(SystemExit) as caught:
        manage_symgov.main(argv)

    captured = capsys.readouterr()
    assert caught.value.code != 0
    assert captured.out == ""
    assert RAW_KEY not in captured.err


def test_wrong_prefix_service_error_rolls_back_and_is_secret_safe(monkeypatch, capsys):
    session = FakeSession()
    install_factory(monkeypatch, session)

    def fail(*_args, **_kwargs):
        raise ValueError(f"wrong prefix {RAW_KEY}")

    monkeypatch.setattr(manage_symgov, "revoke_catalog_api_key", fail)

    assert manage_symgov.main(
        ["revoke-catalog-api-key", "--key-id", str(KEY_ID), "--confirm-prefix", "wrong"]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert RAW_KEY not in captured.err
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.parametrize("failure_point", ["service", "commit"])
def test_create_failures_rollback_and_never_print_generated_secret(monkeypatch, capsys, failure_point):
    session = FakeSession(commit_error=RuntimeError(f"DB parameters include {RAW_KEY}")) if failure_point == "commit" else FakeSession()
    install_factory(monkeypatch, session)

    def create_service(*_args, **_kwargs):
        if failure_point == "service":
            raise RuntimeError(f"DB parameters include {RAW_KEY}")
        return CatalogApiKeyCreateDTO(key=safe_dto(), raw_key=RAW_KEY)

    monkeypatch.setattr(manage_symgov, "create_catalog_api_key", create_service)

    assert manage_symgov.main(
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read"]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert RAW_KEY not in captured.err
    assert session.rollbacks == 1


def test_failed_create_with_rollback_and_close_failures_never_emits_payload(monkeypatch, capsys):
    session = FakeSession(
        rollback_error=RuntimeError(f"rollback leaked {RAW_KEY}"),
        close_error=RuntimeError(f"close leaked {RAW_KEY}"),
    )
    install_factory(monkeypatch, session)

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"operation leaked {RAW_KEY}")

    monkeypatch.setattr(manage_symgov, "create_catalog_api_key", fail)

    result = manage_symgov.main(
        ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == (
        "Catalog API key operation failed.\n"
        "Catalog API key session cleanup failed.\n"
    )
    assert RAW_KEY not in captured.err
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed == 1


@pytest.mark.parametrize("command", ["create", "list", "revoke"])
def test_all_catalog_service_exceptions_rollback(monkeypatch, capsys, command):
    session = FakeSession()
    install_factory(monkeypatch, session)

    def fail(*_args, **_kwargs):
        raise RuntimeError("unsafe DB detail")

    if command == "create":
        monkeypatch.setattr(manage_symgov, "create_catalog_api_key", fail)
        argv = ["create-catalog-api-key", "--customer", "Acme", "--integration", "CAD", "--scope", "catalog.read"]
    elif command == "list":
        monkeypatch.setattr(manage_symgov, "list_catalog_api_keys", fail)
        argv = ["list-catalog-api-keys"]
    else:
        monkeypatch.setattr(manage_symgov, "revoke_catalog_api_key", fail)
        argv = ["revoke-catalog-api-key", "--key-id", str(KEY_ID), "--confirm-prefix", PREFIX]

    assert manage_symgov.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe DB detail" not in captured.err
    assert session.rollbacks == 1
    assert session.closed == 1


def test_help_warns_that_create_stdout_contains_one_time_secret(capsys):
    with pytest.raises(SystemExit) as caught:
        manage_symgov.main(["create-catalog-api-key", "--help"])

    assert caught.value.code == 0
    help_text = capsys.readouterr().out.lower()
    assert "one-time" in help_text
    assert "secret" in help_text
    assert "stdout" in help_text
