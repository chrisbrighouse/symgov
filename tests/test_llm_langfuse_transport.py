"""Offline tests for the disabled-by-default Langfuse ingestion transport."""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock
from urllib.request import ProxyHandler, Request

import pytest
import symgov_backend.services.llm_telemetry as telemetry
from symgov_backend.services.llm_telemetry import LLMTelemetry, LangfuseTransport, TelemetryConfig
from test_llm_telemetry import BASE, TRACE_SEED, event


class Response:
    status = 207

    def __init__(self):
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        self.read_sizes.append(size)
        return b'{}'


def test_ingestion_opener_ignores_ambient_proxy_configuration():
    proxy_handlers = [
        handler for handler in telemetry._NO_REDIRECT_OPENER.handlers
        if isinstance(handler, ProxyHandler)
    ]
    assert all(getattr(handler, "proxies", None) == {} for handler in proxy_handlers)


def configured_env(monkeypatch):
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENDPOINT", "https://langfuse.invalid/api/public/ingestion")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY", "synthetic-secret")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS", "2.5")


def test_disabled_environment_never_calls_urlopen(monkeypatch):
    monkeypatch.delenv("SYMGOV_LLM_TELEMETRY_ENABLED", raising=False)
    network = Mock(side_effect=AssertionError("network contacted while disabled"))
    monkeypatch.setattr(telemetry, "urlopen", network)

    adapter = LLMTelemetry.from_env()

    assert not adapter.enabled
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=0.01)
    network.assert_not_called()


def test_incomplete_environment_never_calls_urlopen(monkeypatch):
    configured_env(monkeypatch)
    monkeypatch.delenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY")
    network = Mock(side_effect=AssertionError("network contacted with incomplete config"))
    monkeypatch.setattr(telemetry, "urlopen", network)

    adapter = LLMTelemetry.from_env()

    assert not adapter.enabled
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    network.assert_not_called()


def test_enabled_transport_posts_verify_poc_batch_shape_with_basic_auth(monkeypatch):
    configured_env(monkeypatch)
    response = Response()
    network = Mock(return_value=response)
    monkeypatch.setattr(telemetry, "urlopen", network)
    adapter = LLMTelemetry.from_env()

    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=1.0)

    request = network.call_args.args[0]
    assert request.full_url == "https://langfuse.invalid/api/public/ingestion"
    assert network.call_args.kwargs == {"timeout": 2.5}
    assert response.read_sizes == [4096]
    expected_token = base64.b64encode(b"synthetic-public:synthetic-secret").decode("ascii")
    assert request.get_header("Authorization") == f"Basic {expected_token}"
    payload = json.loads(request.data)
    assert set(payload) == {"batch"}
    assert [item["type"] for item in payload["batch"]] == ["trace-create", "generation-create"]
    trace, generation = payload["batch"]
    assert trace["body"] == {
        "id": event()["trace_id"],
        "timestamp": BASE["occurred_at_utc"],
        "name": "symbol_property_vision",
        "metadata": event()["metadata"],
    }
    assert generation["body"]["usageDetails"] == {
        "input": 120,
        "output": 40,
        "cachedInput": 10,
        "cacheWrite": 2,
        "reasoning": 3,
        "inputImage": 1,
        "audio_seconds": 1.25,
    }
    assert generation["body"]["costDetails"] == {"total": 0.00125}
    serialized = request.data.decode("utf-8")
    assert "synthetic-secret" not in serialized
    assert "synthetic-public" not in serialized


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://langfuse.invalid/api/public/ingestion",
        "http://localhost:3000/api/public/ingestion",
        "http://127.0.0.2:3000/api/public/ingestion",
        "http://[::2]:3000/api/public/ingestion",
    ],
)
def test_basic_auth_transport_rejects_non_https_and_hostname_loopback_aliases(endpoint):
    with pytest.raises(ValueError, match="endpoint|HTTPS|loopback"):
        _direct_config(endpoint=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:3000/api/public/ingestion",
        "http://[::1]:3000/api/public/ingestion",
    ],
)
def test_isolated_poc_allows_only_exact_loopback_ip_http(endpoint):
    assert _direct_config(endpoint=endpoint).endpoint == endpoint


def test_production_transport_allows_only_the_exact_internal_langfuse_endpoint():
    endpoint = "http://symgov-langfuse:3000/api/public/ingestion"
    assert _direct_config(endpoint=endpoint).endpoint == endpoint
    with pytest.raises(ValueError, match="endpoint|configuration"):
        _direct_config(endpoint="http://langfuse-poc-langfuse-web-1:3000/api/public/ingestion")


@pytest.mark.parametrize(
    "endpoint",
    ["https://:443/api", *(f"https://langfuse.invalid/api{chr(code)}path" for code in (*range(0x21), 0x7F))],
)
def test_direct_configuration_rejects_missing_hostname_and_ascii_control_or_whitespace(endpoint):
    with pytest.raises(ValueError, match="endpoint|configuration"):
        _direct_config(endpoint=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    ["https://:443/api", *(f"https://langfuse.invalid/api{chr(code)}path" for code in (*range(0x21), 0x7F))],
)
def test_environment_endpoint_failures_disable_without_network(monkeypatch, endpoint):
    environment = {
        "SYMGOV_LLM_TELEMETRY_ENABLED": "true",
        "SYMGOV_LLM_TELEMETRY_ENDPOINT": endpoint,
        "SYMGOV_LLM_TELEMETRY_PUBLIC_KEY": "synthetic-public",
        "SYMGOV_LLM_TELEMETRY_SECRET_KEY": "synthetic-secret",
        "SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS": "2.5",
    }
    monkeypatch.setattr(telemetry.os, "getenv", lambda name, default=None: environment.get(name, default))
    network = Mock(side_effect=AssertionError("network contacted with malformed endpoint"))
    monkeypatch.setattr(telemetry, "urlopen", network)

    adapter = LLMTelemetry.from_env()

    assert not adapter.enabled
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=0.01)
    network.assert_not_called()


INVALID_CANONICAL_ENDPOINTS = [
    "https://@langfuse.invalid/api/public/ingestion",
    "https://:@langfuse.invalid/api/public/ingestion",
    "https://langfuse.invalid/api\x85path",
    "https://langfuse.invalid/api\u00a0path",
    "https://langfuse.invalid/api\u200bpath",
    "https://lang_fuse.invalid/api/public/ingestion",
    "https://%6cangfuse.invalid/api/public/ingestion",
    "https://langfuse.invalid\\evil/api/public/ingestion",
    "https://-langfuse.invalid/api/public/ingestion",
    "https://langfuse-.invalid/api/public/ingestion",
    "https://langfuse..invalid/api/public/ingestion",
    f"https://{'a' * 64}.invalid/api/public/ingestion",
    f"https://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 62}/api/public/ingestion",
    "https://[fe80::1%25eth0]/api/public/ingestion",
    "https://[2001:db8::1/api/public/ingestion",
    "https://langfuse.invalid:65536/api/public/ingestion",
]


@pytest.mark.parametrize("endpoint", INVALID_CANONICAL_ENDPOINTS)
def test_direct_configuration_rejects_userinfo_non_ascii_and_noncanonical_hosts(endpoint):
    with pytest.raises(ValueError, match="endpoint|configuration"):
        _direct_config(endpoint=endpoint)


@pytest.mark.parametrize("endpoint", INVALID_CANONICAL_ENDPOINTS)
def test_noncanonical_environment_endpoints_disable_without_network(monkeypatch, endpoint):
    configured_env(monkeypatch)
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENDPOINT", endpoint)
    network = Mock(side_effect=AssertionError("network contacted with noncanonical endpoint"))
    monkeypatch.setattr(telemetry, "urlopen", network)

    adapter = LLMTelemetry.from_env()

    assert not adapter.enabled
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=0.01)
    network.assert_not_called()


NONCANONICAL_NUMERIC_IP_ENDPOINTS = [
    "https://127.0.0.01/api/public/ingestion",
    "https://127.1/api/public/ingestion",
    "https://2130706433/api/public/ingestion",
    "https://0x7f000001/api/public/ingestion",
    "https://017700000001/api/public/ingestion",
    "https://0177.0.0.1/api/public/ingestion",
    "https://0x7f.0x0.0x0.0x1/api/public/ingestion",
    "https://127.0x0.0.1/api/public/ingestion",
]


@pytest.mark.parametrize("endpoint", NONCANONICAL_NUMERIC_IP_ENDPOINTS)
def test_direct_configuration_rejects_noncanonical_numeric_ip_hosts(endpoint):
    with pytest.raises(ValueError, match="endpoint|configuration"):
        _direct_config(endpoint=endpoint)


@pytest.mark.parametrize("endpoint", NONCANONICAL_NUMERIC_IP_ENDPOINTS)
def test_noncanonical_numeric_ip_environment_endpoints_disable_without_network(monkeypatch, endpoint):
    configured_env(monkeypatch)
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENDPOINT", endpoint)
    network = Mock(side_effect=AssertionError("network contacted with ambiguous numeric IP endpoint"))
    monkeypatch.setattr(telemetry, "urlopen", network)

    adapter = LLMTelemetry.from_env()

    assert not adapter.enabled
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=0.01)
    network.assert_not_called()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://192.0.2.1/api/public/ingestion",
        "https://[2001:db8::1]:443/api/public/ingestion",
        "https://127.0.0.1.langfuse.invalid/api/public/ingestion",
        "https://0x7f.langfuse.invalid/api/public/ingestion",
        f"https://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 61}/api/public/ingestion",
    ],
)
def test_direct_configuration_accepts_parseable_ips_and_dns_boundary(endpoint):
    assert _direct_config(endpoint=endpoint).endpoint == endpoint


def test_redirect_handler_refuses_redirects_without_reusing_authorization():
    original = Request(
        "https://langfuse.invalid/api/public/ingestion",
        headers={"Authorization": "Basic synthetic-token"},
    )

    redirected = telemetry._NoRedirectHandler().redirect_request(
        original,
        None,
        307,
        "Temporary Redirect",
        {},
        "https://attacker.invalid/collect",
    )

    assert redirected is None


def test_transport_repr_and_network_errors_hide_credentials(monkeypatch):
    configured_env(monkeypatch)
    config = TelemetryConfig.from_env()
    transport = LangfuseTransport.from_config(config)
    assert transport is not None
    rendered = repr(config) + repr(transport)
    assert "synthetic-public" not in rendered
    assert "synthetic-secret" not in rendered

    monkeypatch.setattr(telemetry, "urlopen", Mock(side_effect=RuntimeError("synthetic upstream failure")))
    adapter = LLMTelemetry(config=config, transport=transport)
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=1.0)


def test_transport_only_receives_defensive_sanitized_snapshot(monkeypatch):
    configured_env(monkeypatch)
    network = Mock(return_value=Response())
    monkeypatch.setattr(telemetry, "urlopen", network)
    adapter = LLMTelemetry.from_env()
    unsafe = event()
    unsafe["prompt"] = "must not leave process"

    assert not adapter.record(unsafe, trace_seed=TRACE_SEED)
    assert adapter.close(timeout=0.1)
    network.assert_not_called()


def test_timeout_configuration_is_bounded_and_invalid_values_disable(monkeypatch):
    configured_env(monkeypatch)
    for value in ("nan", "0", "31", "private prose"):
        monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS", value)
        assert not TelemetryConfig.from_env().enabled


def _direct_config(**overrides):
    values = {
        "enabled": True,
        "endpoint": "https://langfuse.invalid/api/public/ingestion",
        "public_key": "synthetic-public",
        "secret_key": "synthetic-secret",
        "timeout_seconds": 2.5,
    }
    values.update(overrides)
    return TelemetryConfig(**values)


def _generation_body(monkeypatch, item):
    response = Response()
    network = Mock(return_value=response)
    monkeypatch.setattr(telemetry, "urlopen", network)
    transport = LangfuseTransport.from_config(_direct_config())
    assert transport is not None
    transport(item)
    assert response.read_sizes == [4096]
    return json.loads(network.call_args.args[0].data)["batch"][1]["body"]


def _batch(monkeypatch, item):
    network = Mock(return_value=Response())
    monkeypatch.setattr(telemetry, "urlopen", network)
    transport = LangfuseTransport.from_config(_direct_config())
    assert transport is not None
    transport(item)
    return json.loads(network.call_args.args[0].data)["batch"]


def test_generation_observation_id_is_global_deterministic_and_retransmit_idempotent(monkeypatch):
    first = event()
    same_again = _generation_body(monkeypatch, first)["id"]
    assert _generation_body(monkeypatch, first)["id"] == same_again
    second_attempt = event(event_id="22222222-2222-4222-8222-222222222222", observation_id="attempt-2", attempt_number=2)
    other_trace = event(trace_seed="queue:00000000-0000-4000-8000-000000000009", queue_item_id="00000000-0000-4000-8000-000000000009")
    assert _generation_body(monkeypatch, second_attempt)["id"] != same_again
    assert _generation_body(monkeypatch, other_trace)["id"] != same_again


def test_envelope_ids_use_lineage_not_reusable_caller_event_id(monkeypatch):
    first = event()
    second_attempt = event(
        event_id=first["event_id"],
        observation_id="attempt-2",
        attempt_number=2,
    )

    first_ids = [item["id"] for item in _batch(monkeypatch, first)]
    assert [item["id"] for item in _batch(monkeypatch, first)] == first_ids
    assert [item["id"] for item in _batch(monkeypatch, second_attempt)] != first_ids


def test_unknown_cost_omits_cost_details_and_local_zero_is_preserved(monkeypatch):
    unknown = event(cost_basis="unknown", provider_reported_cost_usd=None, calculated_cost_usd=None, pricing_version=None)
    assert "costDetails" not in _generation_body(monkeypatch, unknown)
    zero = event(cost_basis="local_policy", provider_reported_cost_usd=None, calculated_cost_usd="0", pricing_version="local-v1")
    assert _generation_body(monkeypatch, zero)["costDetails"] == {"total": 0.0}


def test_direct_configuration_rejects_incomplete_unsafe_or_unbounded_values():
    import pytest
    for overrides in (
        {"timeout_seconds": float("nan")}, {"timeout_seconds": 0.049},
        {"timeout_seconds": 30.01}, {"endpoint": None}, {"public_key": None},
        {"secret_key": "bad\nheader"},
    ):
        with pytest.raises((TypeError, ValueError), match="timeout|configuration|endpoint|key|credential"):
            _direct_config(**overrides)


def test_direct_transport_rejects_bad_timeout_and_credentials():
    import pytest
    for args in (
        ("https://langfuse.invalid/api/public/ingestion", "pk", "sk", float("inf")),
        ("https://langfuse.invalid/api/public/ingestion", "pk", "sk", 31),
        ("https://langfuse.invalid/api/public/ingestion", "", "sk", 1),
        ("https://langfuse.invalid/api/public/ingestion", "pk", "bad\rkey", 1),
    ):
        with pytest.raises((TypeError, ValueError), match="timeout|key|credential|configuration"):
            LangfuseTransport(*args)
