"""Contract tests for the bounded, secret-safe Langfuse reporting query."""

from datetime import datetime, timezone
import base64
import importlib
import json
import socket
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler

import pytest

from symgov_backend.services import langfuse_reporting


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(
        langfuse_reporting,
        "_open_pinned_https",
        lambda request, *, timeout, addresses: langfuse_reporting.urlopen(request, timeout=timeout),
        raising=False,
    )


class Response:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size=-1):
        if size < 0:
            return self.body
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


def test_query_uses_bounded_observation_metrics_basic_auth_and_model_rows(monkeypatch):
    captured = {}

    def fake_open(request, *, timeout):
        captured.update(request=request, timeout=timeout)
        return Response({"data": [
            {"providedModelName": "model-a", "count_count": 2, "sum_inputTokens": 10,
             "sum_outputTokens": 5, "sum_totalTokens": 15, "sum_totalCost": 0.25}
        ]})

    monkeypatch.setattr(langfuse_reporting, "urlopen", fake_open)
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public-marker", secret_key="secret-marker", timeout_seconds=2,
    )
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 2, tzinfo=timezone.utc)

    result = langfuse_reporting.query_langfuse_usage(config, start, end)

    request = captured["request"]
    query = json.loads(parse_qs(urlparse(request.full_url).query)["query"][0])
    assert query["view"] == "observations"
    assert query["dimensions"] == [{"field": "providedModelName"}]
    assert [metric["measure"] for metric in query["metrics"]] == [
        "count", "inputTokens", "outputTokens", "totalTokens", "totalCost"
    ]
    assert query["fromTimestamp"] == "2026-08-01T00:00:00Z"
    assert query["toTimestamp"] == "2026-08-02T00:00:00Z"
    assert query["config"] == {"row_limit": 100}
    assert query["filters"] == [{
        "column": "type", "operator": "=", "value": "GENERATION", "type": "string"
    }]
    expected_auth = "Basic " + base64.b64encode(b"public-marker:secret-marker").decode("ascii")
    assert request.headers["Authorization"] == expected_auth
    assert captured["timeout"] == 2
    assert result == {
        "status": "available", "message": "Langfuse metrics are available.",
        "totals": {"observations": 2, "inputTokens": 10, "outputTokens": 5,
                   "totalTokens": 15, "totalCostUsd": 0.25},
        "byModel": [{"model": "model-a", "observations": 2, "inputTokens": 10,
                     "outputTokens": 5, "totalTokens": 15, "totalCostUsd": 0.25}],
    }
    assert "secret-marker" not in repr(config)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com",
        "http://langfuse-web:3000",
        "https://localhost",
        "https://127.0.0.1",
        "https://langfuse.example.invalid:444",
        "https://user:password@langfuse.example.invalid",
    ],
)
def test_query_config_rejects_unapproved_or_credential_bearing_origins(base_url):
    with pytest.raises(ValueError, match="safe configuration"):
        langfuse_reporting.LangfuseQueryConfig(
            enabled=True, base_url=base_url, public_key="public", secret_key="secret"
        )


def test_query_config_accepts_only_the_stable_internal_http_origin():
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True,
        base_url="http://symgov-langfuse:3000",
        public_key="public",
        secret_key="secret",
    )
    assert config.enabled is True

    with pytest.raises(ValueError, match="safe configuration"):
        langfuse_reporting.LangfuseQueryConfig(
            enabled=True,
            base_url="http://langfuse-poc-langfuse-web-1:3000",
            public_key="public",
            secret_key="secret",
        )


def test_query_rejects_malformed_or_oversized_response_without_disclosing_secrets(monkeypatch):
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public-marker", secret_key="secret-marker",
    )
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(langfuse_reporting, "urlopen", lambda *_args, **_kwargs: Response({"unexpected": []}))
    with pytest.raises(RuntimeError) as malformed:
        langfuse_reporting.query_langfuse_usage(config, start, end)
    assert "secret-marker" not in str(malformed.value)

    class OversizedResponse(Response):
        def read(self, size=-1):
            return b"x" * size

    monkeypatch.setattr(langfuse_reporting, "urlopen", lambda *_args, **_kwargs: OversizedResponse({}))
    with pytest.raises(RuntimeError, match="safe limit") as oversized:
        langfuse_reporting.query_langfuse_usage(config, start, end)
    assert "secret-marker" not in str(oversized.value)


def test_safe_query_degrades_to_secret_safe_unavailable(monkeypatch):
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public-marker", secret_key="secret-marker",
    )
    monkeypatch.setattr(
        langfuse_reporting,
        "query_langfuse_usage",
        lambda *_: (_ for _ in ()).throw(RuntimeError("secret-marker upstream body")),
    )
    result, warnings = langfuse_reporting.safe_langfuse_usage(
        config,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    assert result["status"] == "unavailable"
    assert "secret-marker" not in str(result) + str(warnings)


def test_query_parses_real_langfuse_v3_numeric_strings(monkeypatch):
    monkeypatch.setattr(
        langfuse_reporting,
        "urlopen",
        lambda *_args, **_kwargs: Response({"data": [{
            "providedModelName": "openai/gpt-4.1-mini",
            "count_count": "2",
            "sum_inputTokens": "10",
            "sum_outputTokens": "5",
            "sum_totalTokens": "15",
            "sum_totalCost": "0.2500",
        }]}),
    )
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True,
        base_url="https://langfuse.example.invalid",
        public_key="public",
        secret_key="secret",
    )

    result = langfuse_reporting.query_langfuse_usage(
        config,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert result["totals"] == {
        "observations": 2,
        "inputTokens": 10,
        "outputTokens": 5,
        "totalTokens": 15,
        "totalCostUsd": 0.25,
    }


@pytest.mark.parametrize(
    "model",
    [
        "<script>", "model name", "model\nname", "mødel", "user:pass@example.com",
        "Bearer:supersecrettoken", "«redacted:sk-…»", "api_key:verysecret",
        "A" + "KIA" + "A" * 16,
        "g" + "hp_demo_marker_123456789",
        "eyJ" + "demoheader.payload.signature",
        "https://model.invalid",
        "data:text/plain",
    ],
)
def test_query_rejects_unsafe_model_labels(monkeypatch, model):
    monkeypatch.setattr(
        langfuse_reporting,
        "urlopen",
        lambda *_args, **_kwargs: Response({"data": [{
            "providedModelName": model,
            "count_count": 1,
            "sum_inputTokens": 1,
            "sum_outputTokens": 1,
            "sum_totalTokens": 2,
            "sum_totalCost": 0.1,
        }]}),
    )
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True,
        base_url="https://langfuse.example.invalid",
        public_key="public",
        secret_key="secret",
    )

    with pytest.raises(RuntimeError, match="malformed"):
        langfuse_reporting.query_langfuse_usage(
            config,
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "value",
    [True, -1, float("inf"), float("nan"), "-1", "NaN", "1e999999", "1" * 40],
)
def test_query_rejects_unsafe_metric_numbers(monkeypatch, value):
    monkeypatch.setattr(
        langfuse_reporting,
        "urlopen",
        lambda *_args, **_kwargs: Response({"data": [{
            "providedModelName": "model-a",
            "count_count": 1,
            "sum_inputTokens": value,
            "sum_outputTokens": 1,
            "sum_totalTokens": 2,
            "sum_totalCost": 0.1,
        }]}),
    )
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public", secret_key="secret",
    )
    with pytest.raises(RuntimeError, match="malformed"):
        langfuse_reporting.query_langfuse_usage(
            config,
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
        )


def test_query_rejects_aggregate_totals_above_safe_bound(monkeypatch):
    row = {
        "count_count": 1,
        "sum_inputTokens": 1_000_000_000_000_000,
        "sum_outputTokens": 0,
        "sum_totalTokens": 1_000_000_000_000_000,
        "sum_totalCost": 0,
    }
    monkeypatch.setattr(
        langfuse_reporting,
        "urlopen",
        lambda *_args, **_kwargs: Response({"data": [
            {"providedModelName": "model-a", **row},
            {"providedModelName": "model-b", **row},
        ]}),
    )
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public", secret_key="secret",
    )
    with pytest.raises(RuntimeError, match="safe bounds"):
        langfuse_reporting.query_langfuse_usage(
            config,
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
        )


def test_enabled_but_misconfigured_query_is_unavailable_not_disabled(monkeypatch):
    monkeypatch.setenv("SYMGOV_LANGFUSE_QUERY_ENABLED", "true")
    monkeypatch.delenv("SYMGOV_LANGFUSE_QUERY_BASE_URL", raising=False)
    monkeypatch.delenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY", raising=False)

    result, warnings = langfuse_reporting.safe_langfuse_usage(
        langfuse_reporting.LangfuseQueryConfig.from_env(),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "unavailable"
    assert result["totals"] is None
    assert warnings == ["Langfuse reporting is misconfigured and unavailable."]


def test_external_https_query_rejects_non_global_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))],
    )
    opened = False

    def fake_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        return Response({"data": []})

    monkeypatch.setattr(langfuse_reporting, "urlopen", fake_open)
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public", secret_key="secret",
    )

    with pytest.raises(RuntimeError, match="destination"):
        langfuse_reporting.query_langfuse_usage(
            config,
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
    assert opened is False


def test_external_https_query_pins_the_validated_dns_answer(monkeypatch):
    calls = 0

    def resolve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        resolve,
    )
    captured = {}

    def fake_open(request, *, timeout, addresses):
        captured.update(request=request, timeout=timeout, addresses=addresses)
        return Response({"data": []})

    monkeypatch.setattr(langfuse_reporting, "_open_pinned_https", fake_open)
    config = langfuse_reporting.LangfuseQueryConfig(
        enabled=True, base_url="https://langfuse.example.invalid",
        public_key="public", secret_key="secret",
    )

    result = langfuse_reporting.query_langfuse_usage(
        config,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "available"
    assert calls == 1
    assert captured["addresses"] == frozenset({"93.184.216.34"})


def test_pinned_https_connection_preserves_hostname_for_tls_without_reresolving(monkeypatch):
    captured = {}
    raw_socket = object()
    wrapped_socket = object()

    class Context:
        verify_mode = langfuse_reporting.ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, sock, *, server_hostname):
            captured.update(sock=sock, server_hostname=server_hostname)
            return wrapped_socket

    monkeypatch.setattr(langfuse_reporting.ssl, "create_default_context", Context)
    monkeypatch.setattr(
        langfuse_reporting.socket,
        "create_connection",
        lambda address, timeout, source_address: captured.update(
            address=address, timeout=timeout, source_address=source_address
        ) or raw_socket,
    )
    connection = langfuse_reporting._PinnedHTTPSConnection(
        "langfuse.example.invalid", 443, "93.184.216.34", 2.5
    )

    connection.connect()

    assert captured["address"] == ("93.184.216.34", 443)
    assert captured["server_hostname"] == "langfuse.example.invalid"
    assert captured["sock"] is raw_socket
    assert connection.sock is wrapped_socket


def test_reporting_opener_ignores_ambient_proxy_variables(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")

    module = importlib.reload(langfuse_reporting)

    proxy_handlers = [handler for handler in module._OPENER.handlers if isinstance(handler, ProxyHandler)]
    assert all(handler.proxies == {} for handler in proxy_handlers)
