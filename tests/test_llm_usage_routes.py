from fastapi.testclient import TestClient
from unittest.mock import MagicMock
import uuid
import pytest
from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.routes.llm import _reconcile_usage
from symgov_backend.services import langfuse_reporting, llm_usage_ledger


def test_admin_llm_usage_unauthenticated():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/admin/llm/usage")
    assert response.status_code == 401


def test_legacy_admin_llm_usage_unauthenticated():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/admin/llm/usage")
    assert response.status_code == 401


def test_admin_usage_contract_and_legacy_alias_parity(monkeypatch):
    monkeypatch.setenv("SYMGOV_ENV", "test")
    ledger = {
        "totals": {"attempts": 2, "successful": 1, "failed": 1, "latencyMs": 20,
                   "inputTokens": 10, "outputTokens": 4, "cachedInputTokens": 0,
                   "cacheWriteInputTokens": 0, "reasoningTokens": 0,
                   "effectiveCostUsd": 0.5, "providerReportedCostUsd": None,
                   "calculatedCostUsd": 0.5, "unknownCostAttempts": 0, "retryAttempts": 1},
        "breakdowns": {"byProviderModel": [], "byUseCase": [], "byAgent": [], "byStatus": []},
        "warnings": [],
    }
    langfuse = {
        "status": "available", "message": "Langfuse metrics are available.",
        "totals": {"observations": 2, "inputTokens": 10, "outputTokens": 4,
                   "totalTokens": 14, "totalCostUsd": 0.5}, "byModel": [],
    }
    monkeypatch.setattr(llm_usage_ledger, "aggregate_llm_usage", lambda *_args, **_kwargs: ledger)
    monkeypatch.setattr(langfuse_reporting.LangfuseQueryConfig, "from_env", classmethod(lambda cls: cls()))
    monkeypatch.setattr(langfuse_reporting, "safe_langfuse_usage", lambda *_: (langfuse, []))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()), email="admin@example.invalid", display_name="Admin",
        roles=("admin",), must_change_pin=False,
    )
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    client = TestClient(app)

    v1 = client.get("/api/v1/admin/llm/usage?period=day&anchor=2026-08-01T12:00:00Z")
    legacy = client.get("/api/admin/llm/usage?period=day&anchor=2026-08-01T12:00:00Z")

    assert v1.status_code == 200
    assert legacy.status_code == 200
    assert v1.headers["cache-control"] == "no-store, private"
    assert legacy.headers["cache-control"] == "no-store, private"
    assert v1.json() == legacy.json()
    payload = v1.json()
    assert payload["ledger"] == {"status": "available", **ledger}
    assert payload["langfuse"] == langfuse
    assert payload["reconciliation"] == {"status": "matched", "tokenDifference": 0, "costDifferenceUsd": 0.0}
    assert payload["startUtc"] == "2026-08-01T00:00:00Z"
    assert payload["endUtcExclusive"] == "2026-08-02T00:00:00Z"


def test_admin_usage_returns_bounded_200_when_authoritative_ledger_is_unavailable(monkeypatch):
    monkeypatch.setenv("SYMGOV_ENV", "test")
    monkeypatch.setattr(
        llm_usage_ledger,
        "aggregate_llm_usage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database details")),
    )
    monkeypatch.setattr(langfuse_reporting.LangfuseQueryConfig, "from_env", classmethod(lambda cls: cls()))
    monkeypatch.setattr(
        langfuse_reporting,
        "safe_langfuse_usage",
        lambda *_: ({"status": "disabled", "message": "Langfuse reporting is disabled.", "totals": None, "byModel": None}, []),
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()), email="admin@example.invalid", display_name="Admin",
        roles=("admin",), must_change_pin=False,
    )
    app.dependency_overrides[get_db_session] = lambda: MagicMock()

    response = TestClient(app).get("/api/v1/admin/llm/usage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ledger"] == {
        "status": "unavailable",
        "message": "The authoritative Symgov ledger is temporarily unavailable.",
        "totals": None,
        "breakdowns": None,
    }
    assert payload["reconciliation"] == {"status": "unavailable"}
    assert payload["warnings"] == ["The authoritative Symgov ledger is temporarily unavailable."]
    assert "database details" not in response.text


@pytest.mark.parametrize(
    "query",
    ["?period=year", "?anchor=not-a-date", "?anchor=" + "x" * 65, "?anchor=9999-12-31"],
)
def test_admin_usage_rejects_invalid_bounded_period_inputs(query):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()), email="admin@example.invalid", display_name="Admin",
        roles=("admin",), must_change_pin=False,
    )
    response = TestClient(app).get("/api/v1/admin/llm/usage" + query)
    assert response.status_code == 422


def test_admin_usage_rejects_authenticated_non_admin():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()), email="reviewer@example.invalid", display_name="Reviewer",
        roles=("reviewer",), must_change_pin=False,
    )
    response = TestClient(app).get("/api/v1/admin/llm/usage")
    assert response.status_code == 403


@pytest.mark.parametrize("environment", [None, "", "prod", "PRODUCTION", "private prose"])
def test_admin_usage_fails_closed_for_missing_or_invalid_runtime_environment(monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("SYMGOV_ENV", raising=False)
    else:
        monkeypatch.setenv("SYMGOV_ENV", environment)
    monkeypatch.setattr(
        llm_usage_ledger,
        "aggregate_llm_usage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ledger queried with invalid environment")),
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(uuid.uuid4()), email="admin@example.invalid", display_name="Admin",
        roles=("admin",), must_change_pin=False,
    )
    app.dependency_overrides[get_db_session] = lambda: MagicMock()

    response = TestClient(app).get("/api/v1/admin/llm/usage")

    assert response.status_code == 503
    assert response.json()["detail"] == "The server runtime environment is not safely configured."


def test_reconciliation_suppresses_differences_with_unknown_ledger_values():
    ledger = {
        "status": "available",
        "totals": {
            "inputTokens": 10,
            "outputTokens": 4,
            "effectiveCostUsd": 0.5,
            "unknownInputTokenAttempts": 1,
            "unknownOutputTokenAttempts": 0,
            "unknownCostAttempts": 1,
        },
    }
    langfuse = {
        "status": "available",
        "totals": {"totalTokens": 14, "totalCostUsd": 0.5},
    }

    assert _reconcile_usage(ledger, langfuse) == {"status": "notComparable"}
