from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import hash_api_key
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.models import CatalogApiKey


class KeyQuery:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def one_or_none(self):
        compiled = "\n".join(
            str(item.compile(compile_kwargs={"literal_binds": True})) for item in self.criteria
        )
        return next((row for row in self.rows if row.key_hash in compiled), None)


class DeveloperSession:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    def query(self, model):
        assert model is CatalogApiKey
        return KeyQuery(self.rows)

    def commit(self):
        self.commits += 1


def key_row(token="valid-token", *, status="active", expires_delta=timedelta(days=1), revoked=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_name="Acme Engineering",
        integration_name="CAD integration",
        key_prefix="symgov_live_acme",
        key_hash=hash_api_key(token),
        scopes_json=["catalog.read"],
        status=status,
        expires_at=now + expires_delta,
        revoked_at=now if revoked else None,
        last_used_at=None,
    )


def user(roles=("integrator",)):
    return AuthenticatedUser(
        id=str(uuid.uuid4()),
        email="developer@example.invalid",
        display_name="Developer",
        roles=roles,
        must_change_pin=False,
    )


def build_client(*, rows=None, logged_in=True, roles=("integrator",)):
    session = DeveloperSession(list(rows or []))
    app = create_app()

    def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_user] = lambda: user(roles) if logged_in else None
    return TestClient(app), session


def headers(token="valid-token"):
    return {"Authorization": f"Bearer {token}"}


def test_integrator_manifest_requires_login_and_role_but_not_catalog_key():
    active = key_row()

    no_session, _ = build_client(rows=[active], logged_in=False)
    missing_session = no_session.get("/api/v1/catalog/developer", headers=headers())

    client, _ = build_client(rows=[active])
    without_key = client.get("/api/v1/catalog/developer")

    assert missing_session.status_code == 401
    assert without_key.status_code == 200


def test_integrator_tools_still_require_active_catalog_key():
    active = key_row()
    client, _ = build_client(rows=[active])

    missing_key = client.post("/api/v1/catalog/developer/sandbox", json={"operation": "capabilities", "input": {}})
    invalid_key = client.post(
        "/api/v1/catalog/developer/sandbox",
        headers=headers("wrong-token"),
        json={"operation": "capabilities", "input": {}},
    )
    allowed = client.post(
        "/api/v1/catalog/developer/sandbox",
        headers=headers(),
        json={"operation": "capabilities", "input": {}},
    )

    assert missing_key.status_code == 401
    assert invalid_key.status_code == 401
    assert allowed.status_code == 200


def test_integrator_screen_requires_integrator_or_admin_role():
    active = key_row()

    submitter, _ = build_client(rows=[active], roles=("submitter",))
    integrator, _ = build_client(rows=[active], roles=("integrator",))
    admin, _ = build_client(rows=[active], roles=("admin",))

    assert submitter.get("/api/v1/catalog/developer", headers=headers()).status_code == 403
    assert integrator.get("/api/v1/catalog/developer", headers=headers()).status_code == 200
    assert admin.get("/api/v1/catalog/developer", headers=headers()).status_code == 200


def test_integrator_tools_reject_expired_revoked_and_inactive_keys():
    rows = [
        key_row("expired", expires_delta=timedelta(seconds=-1)),
        key_row("revoked", revoked=True),
        key_row("inactive", status="disabled"),
    ]
    client, _ = build_client(rows=rows)

    body = {"operation": "capabilities", "input": {}}
    assert client.post("/api/v1/catalog/developer/sandbox", headers=headers("expired"), json=body).status_code == 401
    assert client.post("/api/v1/catalog/developer/sandbox", headers=headers("revoked"), json=body).status_code == 401
    assert client.post("/api/v1/catalog/developer/sandbox", headers=headers("inactive"), json=body).status_code == 401


def test_developer_manifest_is_current_and_credentials_are_not_disclosed():
    client, _ = build_client(rows=[key_row()])

    response = client.get("/api/v1/catalog/developer", headers=headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Catalog Integrator Hub"
    assert payload["sandbox"]["available"] is True
    assert payload["sandbox"]["deterministic"] is True
    assert payload["support"]["route"] == "/support"
    assert payload["security"]["requiresLoginSession"] is True
    assert payload["security"]["requiresCatalogApiKey"] is True
    assert "no user/customer association" in payload["security"]["authorizationBoundary"].lower()
    assert "memory only" in payload["security"]["keyHandling"].lower()
    assert {item["id"] for item in payload["guides"]} >= {
        "quickstart", "authentication", "search", "pagination", "previews", "errors", "feedback"
    }
    assert {item["name"] for item in payload["scopes"]} == {
        "catalog.read", "catalog.ed.query", "catalog.feedback.write"
    }
    assert all(item["path"].startswith("/api/v1/catalog/") for item in payload["endpoints"])
    serialized = str(payload)
    assert "valid-token" not in serialized
    assert "symgov_live_acme" not in serialized
    for forbidden in ("/api/v1/admin", "/api/v1/auth", "/api/v1/workspace", "/api/v1/published"):
        assert forbidden not in serialized
