from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.catalog_api_keys import CatalogApiKeyAlreadyActiveError, CatalogApiKeyCreateDTO, CatalogApiKeyDTO
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.routes import catalog_developer as developer_routes


USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000111")
KEY_ID = uuid.UUID("00000000-0000-0000-0000-000000000222")
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
RAW_KEY = "symgov_live_one-time-browser-secret"
PREFIX = "symgov_live_one-time"


class RouteSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def authenticated_user(roles=("integrator",)):
    return AuthenticatedUser(
        id=str(USER_ID),
        email="integrator@example.invalid",
        display_name="Integrator",
        roles=roles,
        must_change_pin=False,
    )


def key_dto(**overrides):
    values = {
        "id": KEY_ID,
        "customer_name": "Acme",
        "integration_name": "CAD portal",
        "key_prefix": PREFIX,
        "scopes": ("catalog.read", "catalog.preview"),
        "status": "active",
        "expires_at": None,
        "last_used_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "revoked_at": None,
    }
    values.update(overrides)
    return CatalogApiKeyDTO(**values)


def build_client(*, roles=("integrator",), logged_in=True):
    session = RouteSession()
    app = create_app()

    def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(roles) if logged_in else None
    return TestClient(app), session


def test_self_service_status_is_role_gated_and_secret_safe(monkeypatch):
    monkeypatch.setattr(developer_routes, "get_active_self_service_catalog_api_key", lambda *_args, **_kwargs: key_dto())

    client, _ = build_client()
    response = client.get("/api/v1/catalog/developer/api-key")
    payload = response.json()

    assert response.status_code == 200
    assert payload["activeKey"]["keyId"] == str(KEY_ID)
    assert payload["activeKey"]["keyPrefix"] == PREFIX
    assert payload["availableScopes"] == [
        "catalog.ed.query",
        "catalog.feedback.write",
        "catalog.preview",
        "catalog.read",
        "catalog.usage.read",
    ]
    assert payload["access"] == {"mode": "free", "subscriptionRequired": False}
    assert RAW_KEY not in repr(payload)
    assert "keyHash" not in repr(payload)

    submitter, _ = build_client(roles=("submitter",))
    anonymous, _ = build_client(logged_in=False)
    assert submitter.get("/api/v1/catalog/developer/api-key").status_code == 403
    assert anonymous.get("/api/v1/catalog/developer/api-key").status_code == 401


def test_self_service_create_commits_before_returning_one_time_secret(monkeypatch):
    calls = []
    created = CatalogApiKeyCreateDTO(key=key_dto(), raw_key=RAW_KEY)

    def create_service(session, **kwargs):
        assert session.commits == 0
        calls.append(kwargs)
        return created

    monkeypatch.setattr(developer_routes, "create_self_service_catalog_api_key", create_service)
    client, session = build_client()
    response = client.post(
        "/api/v1/catalog/developer/api-key",
        json={
            "customerName": "Acme",
            "integrationName": "CAD portal",
            "scopes": ["catalog.read", "catalog.preview"],
            "expiresAt": "2027-07-17T12:00:00Z",
        },
    )

    assert response.status_code == 201
    assert session.commits == 1
    assert session.rollbacks == 0
    payload = response.json()
    assert payload["rawKey"] == RAW_KEY
    assert "must be saved" in payload["warning"].lower()
    assert "won't be accessible again" in payload["warning"].lower()
    assert calls == [{
        "user_id": str(USER_ID),
        "customer_name": "Acme",
        "integration_name": "CAD portal",
        "scopes": ["catalog.read", "catalog.preview"],
        "expires_at": datetime(2027, 7, 17, 12, 0, tzinfo=timezone.utc),
    }]


def test_self_service_duplicate_rolls_back_and_never_returns_secret(monkeypatch):
    monkeypatch.setattr(
        developer_routes,
        "create_self_service_catalog_api_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CatalogApiKeyAlreadyActiveError("already active")),
    )
    client, session = build_client()

    response = client.post(
        "/api/v1/catalog/developer/api-key",
        json={"customerName": "Acme", "integrationName": "CAD", "scopes": ["catalog.read"]},
    )

    assert response.status_code == 409
    assert session.commits == 0
    assert session.rollbacks == 1
    assert RAW_KEY not in response.text


def test_self_service_commit_failure_rolls_back_without_disclosing_generated_secret(monkeypatch):
    monkeypatch.setattr(
        developer_routes,
        "create_self_service_catalog_api_key",
        lambda *_args, **_kwargs: CatalogApiKeyCreateDTO(key=key_dto(), raw_key=RAW_KEY),
    )
    client, session = build_client()

    def fail_commit():
        session.commits += 1
        raise RuntimeError(f"database failure containing {RAW_KEY}")

    session.commit = fail_commit
    response = client.post(
        "/api/v1/catalog/developer/api-key",
        json={"customerName": "Acme", "integrationName": "CAD", "scopes": ["catalog.read"]},
    )

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert RAW_KEY not in response.text


def test_self_service_create_rollback_failure_preserves_fixed_secret_safe_500(monkeypatch):
    key_hash = hash_api_key(RAW_KEY)
    commit_error = RuntimeError(f"commit failure containing raw_key={RAW_KEY} key_hash={key_hash}")
    rollback_error = RuntimeError(f"rollback failure containing raw_key={RAW_KEY} key_hash={key_hash}")
    monkeypatch.setattr(
        developer_routes,
        "create_self_service_catalog_api_key",
        lambda *_args, **_kwargs: CatalogApiKeyCreateDTO(key=key_dto(), raw_key=RAW_KEY),
    )
    client, session = build_client()

    def fail_commit():
        session.commits += 1
        raise commit_error

    def fail_rollback():
        session.rollbacks += 1
        raise rollback_error

    session.commit = fail_commit
    session.rollback = fail_rollback

    assert RAW_KEY in repr(commit_error)
    assert key_hash in repr(commit_error)
    assert RAW_KEY in repr(rollback_error)
    assert key_hash in repr(rollback_error)
    response = client.post(
        "/api/v1/catalog/developer/api-key",
        json={"customerName": "Acme", "integrationName": "CAD", "scopes": ["catalog.read"]},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": "request_error",
        "detail": "Catalog API key could not be created.",
    }
    assert session.commits == 1
    assert session.rollbacks == 1
    assert RAW_KEY not in response.text
    assert key_hash not in response.text


def test_self_service_revoke_is_explicit_and_committed(monkeypatch):
    calls = []

    def revoke_service(session, **kwargs):
        calls.append(kwargs)
        return key_dto(status="revoked", revoked_at=NOW)

    monkeypatch.setattr(developer_routes, "revoke_self_service_catalog_api_key", revoke_service)
    client, session = build_client()
    response = client.request(
        "DELETE",
        "/api/v1/catalog/developer/api-key",
        json={"keyId": str(KEY_ID), "keyPrefix": PREFIX},
    )

    assert response.status_code == 200
    assert response.json()["activeKey"] is None
    assert response.json()["revokedKey"]["status"] == "revoked"
    assert session.commits == 1
    assert calls == [{"user_id": str(USER_ID), "api_key_id": str(KEY_ID), "key_prefix": PREFIX}]


def test_self_service_revoke_rollback_failure_preserves_fixed_secret_safe_500(monkeypatch):
    key_hash = hash_api_key(RAW_KEY)
    commit_error = RuntimeError(f"commit failure containing raw_key={RAW_KEY} key_hash={key_hash}")
    rollback_error = RuntimeError(f"rollback failure containing raw_key={RAW_KEY} key_hash={key_hash}")
    monkeypatch.setattr(
        developer_routes,
        "revoke_self_service_catalog_api_key",
        lambda *_args, **_kwargs: key_dto(status="revoked", revoked_at=NOW),
    )
    client, session = build_client()

    def fail_commit():
        session.commits += 1
        raise commit_error

    def fail_rollback():
        session.rollbacks += 1
        raise rollback_error

    session.commit = fail_commit
    session.rollback = fail_rollback

    assert RAW_KEY in repr(commit_error)
    assert key_hash in repr(commit_error)
    assert RAW_KEY in repr(rollback_error)
    assert key_hash in repr(rollback_error)
    response = client.request(
        "DELETE",
        "/api/v1/catalog/developer/api-key",
        json={"keyId": str(KEY_ID), "keyPrefix": PREFIX},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": "request_error",
        "detail": "Catalog API key could not be revoked.",
    }
    assert session.commits == 1
    assert session.rollbacks == 1
    assert RAW_KEY not in response.text
    assert key_hash not in response.text


def test_wrapped_create_and_revoke_reject_unknown_outer_fields_without_service_execution(monkeypatch):
    create_calls = []
    revoke_calls = []
    monkeypatch.setattr(
        developer_routes,
        "create_self_service_catalog_api_key",
        lambda *_args, **kwargs: create_calls.append(kwargs),
    )
    monkeypatch.setattr(
        developer_routes,
        "revoke_self_service_catalog_api_key",
        lambda *_args, **kwargs: revoke_calls.append(kwargs),
    )
    client, session = build_client()

    create_response = client.post(
        "/api/v1/catalog/developer/api-key",
        json={
            "request": {
                "customerName": "Acme",
                "integrationName": "CAD",
                "scopes": ["catalog.read"],
            },
            "unexpected": RAW_KEY,
        },
    )
    revoke_response = client.request(
        "DELETE",
        "/api/v1/catalog/developer/api-key",
        json={
            "request": {"keyId": str(KEY_ID), "keyPrefix": PREFIX},
            "unexpected": RAW_KEY,
        },
    )

    assert create_response.status_code == 422
    assert revoke_response.status_code == 422
    assert RAW_KEY not in create_response.text
    assert RAW_KEY not in revoke_response.text
    assert create_calls == revoke_calls == []
    assert session.commits == session.rollbacks == 0
