"""Phase 2 contract tests for privacy-safe, provider-neutral LLM telemetry.

These tests are intentionally offline. They define the shared adapter boundary before
any OpenRouter, Gemini, or Langfuse instrumentation is added.
"""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import Mock

import pytest
import symgov_backend.services.llm_telemetry as llm_telemetry
from symgov_backend.services.llm_telemetry import (
    ALLOWED_METADATA_KEYS,
    LLMTelemetry,
    TelemetryConfig,
    build_llm_event,
    trace_id_from_seed,
    validate_event,
)


BASE_EVENT = {
    "environment": "development",
    "trace_seed": "queue:00000000-0000-4000-8000-000000000001",
    "observation_id": "attempt-1",
    "use_case": "libby_symbol_vision",
    "service_name": "libby",
    "agent_slug": "libby",
    "provider": "google",
    "requested_model": "gemini-2.5-flash",
    "resolved_model": "gemini-2.5-flash",
    "request_kind": "vision",
    "attempt_number": 1,
    "status": "succeeded",
    "latency_ms": 125,
    "cost_basis": "provider_reported",
    "provider_reported_cost_usd": "0.001250",
    "calculated_cost_usd": None,
    "pricing_version": None,
    "metadata": {
        "environment": "development",
        "service": "libby",
        "agent": "libby",
        "usecase": "libby_symbol_vision",
        "provider": "google",
        "model": "gemini-2.5-flash",
        "requestkind": "vision",
        "queueitemid": "00000000-0000-4000-8000-000000000001",
        "initiatorkind": "scheduled_worker",
        "costbasis": "provider_reported",
    },
}


def test_default_configuration_is_disabled_and_a_true_noop(monkeypatch):
    for name in (
        "SYMGOV_LLM_TELEMETRY_ENABLED",
        "SYMGOV_LLM_TELEMETRY_ENDPOINT",
        "SYMGOV_LLM_TELEMETRY_PUBLIC_KEY",
        "SYMGOV_LLM_TELEMETRY_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    transport = Mock(side_effect=AssertionError("disabled telemetry contacted a transport"))
    adapter = LLMTelemetry(config=TelemetryConfig.from_env(), transport=transport)

    assert adapter.enabled is False
    assert adapter.record(dict(BASE_EVENT)) is False
    transport.assert_not_called()


@pytest.mark.parametrize("ambiguous_value", ["1", "yes", "on", "TRUE ", "false"])
def test_environment_activation_requires_exact_true(monkeypatch, ambiguous_value):
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENABLED", ambiguous_value)

    assert TelemetryConfig.from_env().enabled is False


def test_environment_activation_accepts_exact_true(monkeypatch):
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENABLED", "true")

    assert TelemetryConfig.from_env().enabled is True


def test_event_builder_is_provider_neutral_and_preserves_cost_provenance():
    provider_reported = build_llm_event(**BASE_EVENT)
    locally_calculated = build_llm_event(
        **{
            **BASE_EVENT,
            "provider": "openrouter",
            "requested_model": "openai/gpt-4o-mini",
            "resolved_model": "openai/gpt-4o-mini",
            "cost_basis": "price_snapshot",
            "provider_reported_cost_usd": None,
            "calculated_cost_usd": "0.000750",
            "pricing_version": "openrouter-2026-07-01",
            "metadata": {
                **BASE_EVENT["metadata"],
                "provider": "openrouter",
                "model": "openai/gpt-4o-mini",
                "costbasis": "price_snapshot",
                "pricingversion": "openrouter-2026-07-01",
            },
        }
    )

    assert provider_reported["provider_reported_cost_usd"] == "0.001250"
    assert provider_reported["calculated_cost_usd"] is None
    assert locally_calculated["provider_reported_cost_usd"] is None
    assert locally_calculated["calculated_cost_usd"] == "0.000750"
    assert locally_calculated["pricing_version"] == "openrouter-2026-07-01"


@pytest.mark.parametrize(
    "forbidden_key,forbidden_value",
    [
        ("prompt", "classify this private document"),
        ("completion", "private model output"),
        ("image", "data:image/png;base64,AAAA"),
        ("document", "s3://private-bucket/source.pdf"),
        ("authorization", "Bearer fake-secret"),
        ("email", "person@example.invalid"),
        ("user_id", "telegram:123456"),
    ],
)
def test_validation_fails_closed_on_forbidden_content(forbidden_key, forbidden_value):
    event = build_llm_event(**BASE_EVENT)
    event[forbidden_key] = forbidden_value

    with pytest.raises(ValueError, match="forbidden|allowlist"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


def test_metadata_is_an_exact_allowlist_with_compact_string_values():
    assert ALLOWED_METADATA_KEYS == {
        "environment",
        "service",
        "agent",
        "usecase",
        "provider",
        "model",
        "requestkind",
        "queueitemid",
        "agentrunid",
        "symbolid",
        "symboldisplayid",
        "feature",
        "initiatorkind",
        "pricingversion",
        "costbasis",
    }

    event = build_llm_event(**BASE_EVENT)
    event["metadata"]["source_notes"] = "must never leave Symgov"
    with pytest.raises(ValueError, match="metadata|allowlist"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])

    event = build_llm_event(**BASE_EVENT)
    event["metadata"]["feature"] = "x" * 201
    with pytest.raises(ValueError, match="200"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


@pytest.mark.parametrize("metadata_key", sorted(ALLOWED_METADATA_KEYS))
def test_metadata_values_reject_arbitrary_sensitive_content(metadata_key):
    event = build_llm_event(**BASE_EVENT)
    event["metadata"][metadata_key] = "private-document-reference"

    with pytest.raises(ValueError, match="metadata|allowlist"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


def test_retry_and_fallback_attempts_keep_lineage_without_overwriting_costs():
    first = build_llm_event(**{**BASE_EVENT, "observation_id": "attempt-1", "attempt_number": 1, "status": "failed"})
    second = build_llm_event(
        **{
            **BASE_EVENT,
            "observation_id": "attempt-2",
            "attempt_number": 2,
            "provider": "openrouter",
            "requested_model": "openai/gpt-4o-mini",
            "resolved_model": "openai/gpt-4o-mini",
            "status": "succeeded",
            "metadata": {
                **BASE_EVENT["metadata"],
                "provider": "openrouter",
                "model": "openai/gpt-4o-mini",
            },
        }
    )

    assert first["trace_id"] == second["trace_id"]
    assert first["observation_id"] != second["observation_id"]
    assert [first["attempt_number"], second["attempt_number"]] == [1, 2]
    assert first["provider"] == "google"
    assert second["provider"] == "openrouter"


def test_trace_ids_are_deterministic_and_reject_business_or_user_text_seeds():
    seed = "request:00000000-0000-4000-8000-000000000002"
    assert trace_id_from_seed(seed) == trace_id_from_seed(seed)

    for unsafe_seed in ("customer@example.invalid", "drawing-A12.pdf", "classify this prompt"):
        with pytest.raises(ValueError, match="queue:|request:"):
            trace_id_from_seed(unsafe_seed)


def test_validation_rejects_caller_supplied_non_hash_trace_ids():
    event = build_llm_event(**BASE_EVENT)
    event["trace_id"] = "customer-full-name"

    with pytest.raises(ValueError, match="trace_id"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("observation_id", "classify-this-private-document"),
        ("provider", "Bearer-fake-secret"),
        ("requested_model", "private-bucket/source.pdf"),
        ("use_case", "customer-full-name"),
    ],
)
def test_validation_rejects_sensitive_content_hidden_in_allowed_fields(field, value):
    event = build_llm_event(**BASE_EVENT)
    event[field] = value

    with pytest.raises(ValueError, match="forbidden|allowlist"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "customer-environment"),
        ("status", "maybe"),
        ("request_kind", "provider-payload"),
        ("cost_basis", "mixed"),
    ],
)
def test_validation_uses_semantic_allowlists_for_categorical_fields(field, value):
    event = build_llm_event(**BASE_EVENT)
    event[field] = value

    with pytest.raises(ValueError, match="allowlist"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"calculated_cost_usd": "0.1"},
        {"provider_reported_cost_usd": None},
        {
            "cost_basis": "price_snapshot",
            "provider_reported_cost_usd": None,
            "calculated_cost_usd": "0.1",
            "pricing_version": None,
        },
    ],
)
def test_cost_provenance_is_mutually_exclusive_and_complete(overrides):
    with pytest.raises(ValueError, match="cost|pricing"):
        build_llm_event(**{**BASE_EVENT, **overrides})


def test_price_snapshot_rejects_arbitrary_pricing_version_content():
    with pytest.raises(ValueError, match="pricing|allowlist"):
        build_llm_event(
            **{
                **BASE_EVENT,
                "cost_basis": "price_snapshot",
                "provider_reported_cost_usd": None,
                "calculated_cost_usd": "0.1",
                "pricing_version": "private-document-reference",
            }
        )


def test_configuration_requires_an_actual_boolean_and_does_not_store_credentials():
    with pytest.raises(TypeError, match="bool"):
        TelemetryConfig(enabled="false")

    config = TelemetryConfig()
    assert "secret" not in repr(config).lower()
    assert "key" not in repr(config).lower()


def test_record_is_nonfatal_when_async_dispatch_cannot_start(monkeypatch):
    event = build_llm_event(**BASE_EVENT)
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(side_effect=RuntimeError("no threads")))
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False


def test_record_rejects_duplicate_attempts():
    event = build_llm_event(**BASE_EVENT)
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is True
    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False


def test_record_rejects_duplicate_or_nonsequential_attempt_lineage():
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    first = build_llm_event(**BASE_EVENT)
    duplicate_number = build_llm_event(
        **{**BASE_EVENT, "observation_id": "attempt-1", "attempt_number": 1}
    )
    skipped_number = build_llm_event(
        **{**BASE_EVENT, "observation_id": "attempt-3", "attempt_number": 3}
    )

    assert adapter.record(first, trace_seed=BASE_EVENT["trace_seed"]) is True
    assert adapter.record(duplicate_number, trace_seed=BASE_EVENT["trace_seed"]) is False
    assert adapter.record(skipped_number, trace_seed=BASE_EVENT["trace_seed"]) is False


def test_record_uses_one_bounded_dispatch_worker(monkeypatch):
    event = build_llm_event(**BASE_EVENT)
    second = build_llm_event(
        **{**BASE_EVENT, "observation_id": "attempt-2", "attempt_number": 2}
    )
    worker = Mock()
    thread_factory = Mock(return_value=worker)
    monkeypatch.setattr(llm_telemetry, "Thread", thread_factory)
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is True
    assert adapter.record(second, trace_seed=BASE_EVENT["trace_seed"]) is True
    assert thread_factory.call_count == 1
    worker.start.assert_called_once_with()
    assert adapter._queue.maxsize > 0


def test_record_contains_unexpected_mapping_failures():
    class ExplodingMapping(dict):
        def __iter__(self):
            raise RuntimeError("mapping failure")

    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert adapter.record(ExplodingMapping(), trace_seed=BASE_EVENT["trace_seed"]) is False


def test_record_verifies_trace_id_against_trusted_seed():
    event = build_llm_event(**BASE_EVENT)
    event["trace_id"] = "0" * 64
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False


def test_lineage_state_is_bounded_and_fails_closed_for_new_traces(monkeypatch):
    worker = Mock()
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(return_value=worker))
    adapter = LLMTelemetry(
        config=TelemetryConfig(enabled=True),
        transport=Mock(),
        lineage_capacity=2,
    )

    seeds = [
        f"request:00000000-0000-4000-8000-{suffix:012d}"
        for suffix in (1, 2, 3)
    ]
    events = [
        build_llm_event(
            **{
                **BASE_EVENT,
                "trace_seed": seed,
                "metadata": {
                    **BASE_EVENT["metadata"],
                    "queueitemid": seed.split(":", 1)[1],
                },
            }
        )
        for seed in seeds
    ]

    assert adapter.record(events[0], trace_seed=seeds[0]) is True
    assert adapter.record(events[1], trace_seed=seeds[1]) is True
    assert adapter.record(events[2], trace_seed=seeds[2]) is False
    assert adapter.record(events[0], trace_seed=seeds[0]) is False
    assert len(adapter._next_attempt) == 2


def test_lineage_memory_is_constant_per_trace(monkeypatch):
    worker = Mock()
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(return_value=worker))
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())

    assert not hasattr(adapter, "_observation_ids")


@pytest.mark.parametrize("capacity", [True, 0, 4_097, 10**20])
def test_lineage_capacity_has_a_strict_hard_limit(capacity):
    with pytest.raises(ValueError, match="lineage_capacity|bounded"):
        LLMTelemetry(lineage_capacity=capacity)


def test_cost_strings_have_strict_size_and_precision_bounds():
    with pytest.raises(ValueError, match="cost|decimal"):
        build_llm_event(
            **{**BASE_EVENT, "provider_reported_cost_usd": "1" * 1_000}
        )


def test_validation_rejects_contradictory_provenance_metadata():
    event = build_llm_event(**BASE_EVENT)
    event["metadata"]["costbasis"] = "none"

    with pytest.raises(ValueError, match="metadata|cost"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])


def test_observation_id_must_match_attempt_number():
    with pytest.raises(ValueError, match="observation_id|attempt"):
        build_llm_event(
            **{**BASE_EVENT, "observation_id": "attempt-999", "attempt_number": 1}
        )


def test_record_contains_unexpected_queue_put_failures(monkeypatch):
    worker = Mock()
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(return_value=worker))
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    adapter._queue = Mock()
    adapter._queue.put_nowait.side_effect = RuntimeError("queue failure")
    event = build_llm_event(**BASE_EVENT)

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False


def test_record_contains_lock_acquisition_failures():
    class BrokenLock:
        def __enter__(self):
            raise RuntimeError("lock failure")

        def __exit__(self, *args):
            return False

    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    adapter._lock = BrokenLock()
    event = build_llm_event(**BASE_EVENT)

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False


@pytest.mark.parametrize("queue_method", ["get", "task_done"])
def test_dispatch_contains_internal_queue_failures(queue_method):
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    queue = Mock()
    if queue_method == "get":
        queue.get.side_effect = RuntimeError("get failure")
    else:
        queue.get.return_value = build_llm_event(**BASE_EVENT)
        queue.task_done.side_effect = RuntimeError("task_done failure")
    adapter._queue = queue
    adapter._worker = llm_telemetry.current_thread()

    adapter._dispatch()

    assert adapter._worker is None


def test_record_restarts_a_dead_dispatch_worker(monkeypatch):
    dead_worker = Mock()
    dead_worker.is_alive.return_value = False
    new_worker = Mock()
    thread_factory = Mock(return_value=new_worker)
    monkeypatch.setattr(llm_telemetry, "Thread", thread_factory)
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    adapter._worker = dead_worker
    event = build_llm_event(**BASE_EVENT)

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is True
    new_worker.start.assert_called_once_with()


@pytest.mark.parametrize(
    "overrides",
    [
        {"observation_id": "attempt-10000000", "attempt_number": 10_000_000},
        {"latency_ms": 10**20},
    ],
)
def test_integer_fields_have_strict_upper_bounds(overrides):
    with pytest.raises(ValueError, match="attempt|observation|latency|bound"):
        build_llm_event(**{**BASE_EVENT, **overrides})


class StatefulEvent(Mapping):
    def __init__(self, event):
        self._event = event
        self._metadata_reads = 0

    def __iter__(self):
        return iter(self._event)

    def __len__(self):
        return len(self._event)

    def __getitem__(self, key):
        if key != "metadata":
            return self._event[key]
        self._metadata_reads += 1
        if self._metadata_reads == 1:
            return self._event[key]
        return {"prompt": "private model payload"}


def test_record_validates_and_queues_the_same_defensive_snapshot(monkeypatch):
    worker = Mock()
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(return_value=worker))
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    event = StatefulEvent(build_llm_event(**BASE_EVENT))

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is True
    queued = adapter._queue.get_nowait()
    assert queued["metadata"] == BASE_EVENT["metadata"]
    assert "prompt" not in queued["metadata"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "google", "resolved_model": "openai/gpt-4o-mini"},
        {
            "provider": "google",
            "cost_basis": "price_snapshot",
            "provider_reported_cost_usd": None,
            "calculated_cost_usd": "0.01",
            "pricing_version": "openrouter-2026-07-01",
        },
    ],
)
def test_provider_model_and_pricing_provenance_must_be_consistent(overrides):
    event = {**BASE_EVENT, **overrides}
    event["metadata"] = {
        **BASE_EVENT["metadata"],
        "provider": event["provider"],
        "model": event["resolved_model"],
        "costbasis": event["cost_basis"],
    }
    if event["pricing_version"] is None:
        event["metadata"].pop("pricingversion", None)
    else:
        event["metadata"]["pricingversion"] = event["pricing_version"]

    with pytest.raises(ValueError, match="provider|model|pricing"):
        build_llm_event(**event)


def test_openrouter_requested_and_resolved_models_must_be_coherent():
    event = {
        **BASE_EVENT,
        "provider": "openrouter",
        "requested_model": "openai/gpt-4o-mini",
        "resolved_model": "gemini-2.5-flash",
        "metadata": {
            **BASE_EVENT["metadata"],
            "provider": "openrouter",
            "model": "gemini-2.5-flash",
        },
    }

    with pytest.raises(ValueError, match="provider|model"):
        build_llm_event(**event)


@pytest.mark.parametrize(
    "metadata_key",
    [
        "environment",
        "service",
        "agent",
        "usecase",
        "provider",
        "model",
        "requestkind",
        "queueitemid",
        "initiatorkind",
        "costbasis",
    ],
)
def test_required_provenance_metadata_cannot_be_omitted(metadata_key):
    metadata = dict(BASE_EVENT["metadata"])
    metadata.pop(metadata_key)

    with pytest.raises(ValueError, match="metadata|provenance|missing"):
        build_llm_event(**{**BASE_EVENT, "metadata": metadata})


def test_queue_item_provenance_must_match_the_trusted_trace_seed():
    metadata = {
        **BASE_EVENT["metadata"],
        "queueitemid": "00000000-0000-4000-8000-000000000099",
    }

    with pytest.raises(ValueError, match="queueitemid|trace_seed|provenance"):
        build_llm_event(**{**BASE_EVENT, "metadata": metadata})


@pytest.mark.parametrize("forbidden_key", ["initiatorpseudonym", "release"])
def test_unverifiable_hash_shaped_metadata_is_not_allowed(forbidden_key):
    metadata = {
        **BASE_EVENT["metadata"],
        forbidden_key: "a" * 64,
    }

    with pytest.raises(ValueError, match="metadata|allowlist"):
        build_llm_event(**{**BASE_EVENT, "metadata": metadata})


@pytest.mark.parametrize("observation_id", ["attempt-01", "attempt-0001"])
def test_observation_id_has_one_canonical_spelling(observation_id):
    with pytest.raises(ValueError, match="observation_id|attempt"):
        build_llm_event(**{**BASE_EVENT, "observation_id": observation_id})


class StatefulMetadata(Mapping):
    def __init__(self, metadata):
        self._metadata = metadata
        self._feature_reads = 0

    def __getitem__(self, key):
        if key == "feature":
            self._feature_reads += 1
            return "symbol_vision" if self._feature_reads == 1 else "private-document-reference"
        return self._metadata[key]

    def __iter__(self):
        return iter({**self._metadata, "feature": "symbol_vision"})

    def __len__(self):
        return len(self._metadata) + 1


def test_record_normalizes_nested_metadata_to_plain_values(monkeypatch):
    worker = Mock()
    monkeypatch.setattr(llm_telemetry, "Thread", Mock(return_value=worker))
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    event = build_llm_event(**BASE_EVENT)
    event["metadata"] = StatefulMetadata(event["metadata"])

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is True
    queued = adapter._queue.get_nowait()
    assert type(queued["metadata"]) is dict
    assert queued["metadata"]["feature"] == "symbol_vision"


class OversizedMetadata(Mapping):
    def __init__(self):
        self.deepcopy_calls = 0

    def __getitem__(self, key):
        return "x"

    def __iter__(self):
        return iter(f"extra-{index}" for index in range(10_000))

    def __len__(self):
        return 10_000

    def __deepcopy__(self, memo):
        self.deepcopy_calls += 1
        return self


def test_record_rejects_oversized_metadata_before_copying(monkeypatch):
    monkeypatch.setattr(llm_telemetry, "Thread", Mock())
    adapter = LLMTelemetry(config=TelemetryConfig(enabled=True), transport=Mock())
    metadata = OversizedMetadata()
    event = build_llm_event(**BASE_EVENT)
    event["metadata"] = metadata

    assert adapter.record(event, trace_seed=BASE_EVENT["trace_seed"]) is False
    assert metadata.deepcopy_calls == 0


class CountingOversizedMetadata(Mapping):
    def __init__(self):
        self.value_reads = 0

    def __getitem__(self, key):
        self.value_reads += 1
        return "private-model-payload"

    def __iter__(self):
        return iter(f"extra-{index}" for index in range(10_000))

    def __len__(self):
        return 10_000


def test_event_builder_rejects_oversized_metadata_before_traversal():
    metadata = CountingOversizedMetadata()

    with pytest.raises(ValueError, match="metadata|bounded|allowlist"):
        build_llm_event(**{**BASE_EVENT, "metadata": metadata})

    assert metadata.value_reads == 0


class CountingOversizedEvent(Mapping):
    def __init__(self):
        self.iterated_keys = 0

    def __getitem__(self, key):
        raise AssertionError("oversized event values must not be read")

    def __iter__(self):
        for index in range(10_000):
            self.iterated_keys += 1
            yield f"extra-{index}"

    def __len__(self):
        return 10_000


def test_direct_validation_rejects_oversized_event_before_traversal():
    event = CountingOversizedEvent()

    with pytest.raises(ValueError, match="event|bounded|schema"):
        validate_event(event, trace_seed=BASE_EVENT["trace_seed"])

    assert event.iterated_keys == 0
