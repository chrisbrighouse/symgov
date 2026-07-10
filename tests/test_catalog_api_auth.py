from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from symgov_backend.catalog_api_auth import (
    PLANNED_CATALOG_API_SCOPES,
    IntegrationAuthContext,
    authenticate_catalog_api_key,
    get_catalog_api_key_context,
    hash_api_key,
    require_catalog_scope,
)


class CapturingQuery:
    def __init__(self, row):
        self.row = row
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def one_or_none(self):
        return self.row


class CapturingSession:
    def __init__(self, row=None):
        self.query_obj = CapturingQuery(row)
        self.committed = False

    def query(self, model):
        self.model = model
        return self.query_obj

    def commit(self):
        self.committed = True


def request_with_headers(headers: dict[str, str]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def api_key_row(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "customer_name": "Acme Engineering",
        "integration_name": "AutoCAD pilot",
        "key_prefix": "symgov_live_abc123",
        "key_hash": hash_api_key("secret-token"),
        "scopes_json": ["catalog.read", "catalog.preview"],
        "status": "active",
        "expires_at": now + timedelta(days=1),
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_hash_api_key_is_sha256_hex_and_never_returns_raw_key():
    digest = hash_api_key("secret-token")

    assert digest != "secret-token"
    assert len(digest) == 64
    assert digest == hash_api_key("secret-token")


def test_authenticate_catalog_api_key_hashes_presented_token_before_lookup():
    session = CapturingSession(api_key_row())

    context = authenticate_catalog_api_key(session, "secret-token", now=datetime.now(timezone.utc))

    assert context == IntegrationAuthContext(
        api_key_id=str(session.query_obj.row.id),
        customer_name="Acme Engineering",
        integration_name="AutoCAD pilot",
        scopes=("catalog.read", "catalog.preview"),
        key_prefix="symgov_live_abc123",
    )
    compiled_criteria = "\n".join(str(criterion.compile(compile_kwargs={"literal_binds": True})) for criterion in session.query_obj.criteria)
    assert hash_api_key("secret-token") in compiled_criteria
    assert "secret-token" not in compiled_criteria


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "disabled"},
        {"status": "revoked"},
        {"revoked_at": datetime.now(timezone.utc)},
        {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
    ],
)
def test_authenticate_catalog_api_key_rejects_disabled_revoked_or_expired_keys(overrides):
    session = CapturingSession(api_key_row(**overrides))

    assert authenticate_catalog_api_key(session, "secret-token", now=datetime.now(timezone.utc)) is None


def test_authenticate_catalog_api_key_rejects_unknown_key():
    session = CapturingSession(row=None)

    assert authenticate_catalog_api_key(session, "secret-token", now=datetime.now(timezone.utc)) is None


def test_get_catalog_api_key_context_accepts_bearer_token_and_commits_last_used_update():
    session = CapturingSession(api_key_row())
    request = request_with_headers({"Authorization": "Bearer secret-token"})

    context = get_catalog_api_key_context(request, session)

    assert context.customer_name == "Acme Engineering"
    assert session.committed is True
    assert session.query_obj.row.last_used_at is not None


def test_get_catalog_api_key_context_accepts_legacy_header_when_bearer_missing():
    session = CapturingSession(api_key_row())
    request = request_with_headers({"X-Symgov-Api-Key": "secret-token"})

    assert get_catalog_api_key_context(request, session).integration_name == "AutoCAD pilot"


def test_get_catalog_api_key_context_rejects_missing_or_unknown_key_with_401():
    with pytest.raises(HTTPException) as missing:
        get_catalog_api_key_context(request_with_headers({}), CapturingSession(api_key_row()))
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as unknown:
        get_catalog_api_key_context(request_with_headers({"Authorization": "Bearer wrong"}), CapturingSession(row=None))
    assert unknown.value.status_code == 401


def test_require_catalog_scope_accepts_matching_scope_and_rejects_insufficient_scope_with_403():
    context = IntegrationAuthContext(
        api_key_id="key-1",
        customer_name="Acme Engineering",
        integration_name="AutoCAD pilot",
        scopes=("catalog.read",),
        key_prefix="symgov_live_abc123",
    )

    assert require_catalog_scope("catalog.read")(context) is context
    with pytest.raises(HTTPException) as exc:
        require_catalog_scope("catalog.ed.query")(context)
    assert exc.value.status_code == 403


def test_planned_catalog_api_scopes_are_declared_without_download_scope():
    assert PLANNED_CATALOG_API_SCOPES == {
        "catalog.read",
        "catalog.preview",
        "catalog.ed.query",
        "catalog.feedback.write",
        "catalog.usage.read",
    }
    assert "catalog.download" not in PLANNED_CATALOG_API_SCOPES
