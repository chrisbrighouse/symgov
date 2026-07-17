from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from starlette.requests import Request

from symgov_backend.catalog_api_auth import IntegrationAuthContext
from symgov_backend.catalog_usage import (
    MAX_USAGE_TEXT_LENGTH,
    build_catalog_usage_event,
    hash_client_ip,
    log_catalog_usage_event_best_effort,
    sanitize_usage_text,
)
from symgov_backend.models import CatalogApiUsageEvent


EXPECTED_USAGE_COLUMNS = {
    "id",
    "api_key_id",
    "customer_name_snapshot",
    "integration_name_snapshot",
    "scope_used",
    "method",
    "path",
    "route_name",
    "status_code",
    "latency_ms",
    "request_id",
    "query_text",
    "symbol_ref",
    "result_count",
    "ed_query_type",
    "user_agent",
    "client_ip_hash",
    "application_name",
    "application_version",
    "created_at",
}


def request_with_headers(headers: dict[str, str], *, method: str = "POST", path: str = "/api/v1/catalog/ed/query") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": ("203.0.113.10", 49152),
        }
    )


def auth_context(**overrides) -> IntegrationAuthContext:
    values = {
        "api_key_id": str(uuid.uuid4()),
        "customer_name": "Acme Engineering",
        "integration_name": "AutoCAD pilot",
        "scopes": ("catalog.read", "catalog.ed.query"),
        "key_prefix": "symgov_live_abc123",
    }
    values.update(overrides)
    return IntegrationAuthContext(**values)


class CapturingSession:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.added = []
        self.committed = False
        self.rolled_back = False

    def add(self, row):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.added.append(row)

    def commit(self):
        if self.fail:
            raise RuntimeError("commit unavailable")
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_catalog_api_usage_event_model_captures_reporting_metadata_without_raw_secrets():
    columns = CatalogApiUsageEvent.__table__.columns

    assert CatalogApiUsageEvent.__tablename__ == "catalog_api_usage_events"
    assert EXPECTED_USAGE_COLUMNS.issubset(set(columns.keys()))
    assert "api_key" not in columns
    assert "raw_api_key" not in columns
    assert "client_ip" not in columns
    assert columns["api_key_id"].nullable is False
    assert columns["customer_name_snapshot"].nullable is False
    assert columns["integration_name_snapshot"].nullable is False
    assert columns["method"].nullable is False
    assert columns["path"].nullable is False
    assert columns["status_code"].nullable is False
    assert columns["created_at"].nullable is False

    index_names = {index.name for index in CatalogApiUsageEvent.__table__.indexes}
    assert "ix_catalog_api_usage_events_api_key_created" in index_names
    assert "ix_catalog_api_usage_events_customer_created" in index_names
    assert "ix_catalog_api_usage_events_route_created" in index_names
    assert "ix_catalog_api_usage_events_symbol_created" in index_names


def test_catalog_api_usage_event_migration_creates_safe_usage_event_storage():
    migration_dir = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    migration_texts = [path.read_text() for path in migration_dir.glob("*_catalog_api_usage_events.py")]

    assert migration_texts, "Expected a catalog API usage events Alembic migration"
    migration_text = "\n".join(migration_texts)

    for required in EXPECTED_USAGE_COLUMNS:
        assert required in migration_text
    assert "catalog_api_usage_events" in migration_text
    assert "catalog_api_keys" in migration_text
    assert "key_hash" not in migration_text
    assert "raw_api_key" not in migration_text
    assert "client_ip," not in migration_text


def test_sanitize_usage_text_redacts_sensitive_tokens_and_truncates():
    raw = "find smoke detector api_key=super-secret Authorization: Bearer live-token\n" + "x" * (MAX_USAGE_TEXT_LENGTH + 50)

    sanitized = sanitize_usage_text(raw)

    assert len(sanitized) <= MAX_USAGE_TEXT_LENGTH
    assert "super-secret" not in sanitized
    assert "live-token" not in sanitized
    assert "api_key=[REDACTED]" in sanitized
    assert "Authorization: Bearer [REDACTED]" in sanitized
    assert "\n" not in sanitized


@pytest.mark.parametrize(
    ("marker", "submitted_value"),
    [
        ("usage_assignment_probe_7f3a", "token=usage_assignment_probe_7f3a"),
        ("usage_bearer_probe_8c4b", "Bearer usage_bearer_probe_8c4b"),
        (
            "usage_uri_probe_9d5c",
            "redis://catalog:usage_uri_probe_9d5c@cache.example/0",
        ),
        (
            "usage_provider_probe_9a1c",
            "ghp_usage_provider_probe_9a1c",
        ),
    ],
    ids=("assignment", "bearer", "connection_uri", "provider_token"),
)
def test_build_catalog_usage_event_sanitizes_credentials_from_every_persisted_text_field(
    marker,
    submitted_value,
):
    submitted = {
        "customer_name_snapshot": submitted_value,
        "integration_name_snapshot": submitted_value,
        "scope_used": submitted_value,
        "method": submitted_value,
        "path": f"/api/v1/catalog/{submitted_value}",
        "route_name": submitted_value,
        "request_id": submitted_value,
        "query_text": submitted_value,
        "symbol_ref": submitted_value,
        "ed_query_type": submitted_value,
        "user_agent": submitted_value,
        "application_name": submitted_value,
        "application_version": submitted_value,
    }
    assert all(marker in value for value in submitted.values())
    request = request_with_headers(
        {
            "X-Request-ID": submitted["request_id"],
            "User-Agent": submitted["user_agent"],
            "X-Symgov-Application": submitted["application_name"],
            "X-Symgov-Application-Version": submitted["application_version"],
        },
        method=submitted["method"],
        path=submitted["path"],
    )

    event = build_catalog_usage_event(
        auth_context(
            customer_name=submitted["customer_name_snapshot"],
            integration_name=submitted["integration_name_snapshot"],
        ),
        request=request,
        scope_used=submitted["scope_used"],
        route_name=submitted["route_name"],
        status_code=200,
        query_text=submitted["query_text"],
        symbol_ref=submitted["symbol_ref"],
        ed_query_type=submitted["ed_query_type"],
    )

    persisted_values = {
        field_name: getattr(event, field_name)
        for field_name in submitted
    }
    assert all(marker not in str(value) for value in persisted_values.values()), persisted_values


def test_build_catalog_usage_event_snapshots_auth_context_and_request_metadata_without_raw_ip():
    api_key_id = str(uuid.uuid4())
    request = request_with_headers(
        {
            "User-Agent": "Symgov CAD Plugin/0.1",
            "X-Request-ID": "req-123",
            "X-Symgov-Application": "AutoCAD Plugin",
            "X-Symgov-Application-Version": "0.1.0",
        },
        method="GET",
        path="/api/v1/catalog/symbols",
    )

    event = build_catalog_usage_event(
        auth_context(api_key_id=api_key_id),
        request=request,
        scope_used="catalog.read",
        route_name="catalog_symbols",
        status_code=200,
        latency_ms=37,
        query_text="smoke detector",
        symbol_ref="0003-12",
        result_count=4,
        ed_query_type="find_symbols",
    )

    assert event.api_key_id == uuid.UUID(api_key_id)
    assert event.customer_name_snapshot == "Acme Engineering"
    assert event.integration_name_snapshot == "AutoCAD pilot"
    assert event.scope_used == "catalog.read"
    assert event.method == "GET"
    assert event.path == "/api/v1/catalog/symbols"
    assert event.route_name == "catalog_symbols"
    assert event.status_code == 200
    assert event.latency_ms == 37
    assert event.request_id == "req-123"
    assert event.query_text == "smoke detector"
    assert event.symbol_ref == "0003-12"
    assert event.result_count == 4
    assert event.ed_query_type == "find_symbols"
    assert event.user_agent == "Symgov CAD Plugin/0.1"
    assert event.client_ip_hash == hash_client_ip("203.0.113.10")
    assert event.client_ip_hash != "203.0.113.10"
    assert event.application_name == "AutoCAD Plugin"
    assert event.application_version == "0.1.0"
    assert isinstance(event.created_at, datetime)
    assert event.created_at.tzinfo is not None


def test_log_catalog_usage_event_best_effort_commits_successful_event():
    session = CapturingSession()
    request = request_with_headers({"User-Agent": "Symgov CAD Plugin/0.1"})

    event = log_catalog_usage_event_best_effort(
        session,
        auth_context(),
        request=request,
        scope_used="catalog.ed.query",
        route_name="catalog_ed_query",
        status_code=200,
        latency_ms=12,
        query_text="Which smoke detector should I use?",
        result_count=2,
    )

    assert event is session.added[0]
    assert session.committed is True
    assert event.customer_name_snapshot == "Acme Engineering"
    assert event.integration_name_snapshot == "AutoCAD pilot"


def test_log_catalog_usage_event_best_effort_swallows_logging_failures():
    session = CapturingSession(fail=True)
    request = request_with_headers({})

    result = log_catalog_usage_event_best_effort(
        session,
        auth_context(),
        request=request,
        scope_used="catalog.read",
        route_name="catalog_symbols",
        status_code=500,
    )

    assert result is None
    assert session.rolled_back is True
