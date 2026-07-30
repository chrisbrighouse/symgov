"""Contract tests for the normalized, privacy-safe LLM usage event."""

from __future__ import annotations

from copy import deepcopy
from queue import Queue
from threading import Barrier, Event, Lock, Thread
import time
from unittest.mock import Mock

import pytest
import symgov_backend.services.llm_telemetry as telemetry

from symgov_backend.services.llm_telemetry import (
    ALLOWED_METADATA_KEYS,
    LLMTelemetry,
    TelemetryConfig,
    build_llm_event,
    initiator_pseudonym,
    trace_id_from_seed,
    validate_event,
)

TRACE_SEED = "queue:00000000-0000-4000-8000-000000000001"
BASE = {
    "event_id": "11111111-1111-4111-8111-111111111111",
    "occurred_at_utc": "2026-07-16T19:00:00.123Z",
    "environment": "development",
    "trace_seed": TRACE_SEED,
    "observation_id": "attempt-1",
    "use_case": "symbol_property_vision",
    "service_name": "libby",
    "agent_slug": "libby",
    "provider": "google",
    "requested_model": "gemini-2.5-flash",
    "resolved_model": "gemini-2.5-flash-001",
    "request_kind": "vision",
    "attempt_number": 1,
    "status": "succeeded",
    "latency_ms": 125,
    "cost_currency": "USD",
    "cost_basis": "price_snapshot",
    "provider_reported_cost_usd": None,
    "calculated_cost_usd": "0.001250",
    "pricing_version": "google-2026-07-01",
    "input_tokens": 120,
    "output_tokens": 40,
    "cached_input_tokens": 10,
    "cache_write_input_tokens": 2,
    "reasoning_tokens": 3,
    "image_input_units": 1,
    "image_output_units": None,
    "other_usage_json": {"audio_seconds": 1.25},
    "queue_item_id": "00000000-0000-4000-8000-000000000001",
    "agent_run_id": "00000000-0000-4000-8000-000000000002",
    "review_case_id": None,
    "intake_record_id": None,
    "source_package_id": None,
    "symbol_id": "00000000-0000-4000-8000-000000000003",
    "symbol_display_id": "0003-12",
    "feature": "symbol-vision",
    "prompt_version": "vision-v3",
    "release": "2026.07.16",
    "initiator_kind": "scheduled_worker",
    "initiator_pseudonym": None,
    "error_class": None,
    "error_code": None,
    "metadata": {
        "environment": "development",
        "service": "libby",
        "agent": "libby",
        "usecase": "symbol_property_vision",
        "provider": "google",
        "model": "gemini-2.5-flash-001",
        "requestkind": "vision",
        "queueitemid": "00000000-0000-4000-8000-000000000001",
        "agentrunid": "00000000-0000-4000-8000-000000000002",
        "symbolid": "00000000-0000-4000-8000-000000000003",
        "symboldisplayid": "0003-12",
        "feature": "symbol-vision",
        "promptversion": "vision-v3",
        "initiatorkind": "scheduled_worker",
        "pricingversion": "google-2026-07-01",
        "costbasis": "price_snapshot",
        "release": "2026.07.16",
    },
}


def _enabled_config():
    return TelemetryConfig(
        enabled=True,
        endpoint="https://langfuse.invalid/api/public/ingestion",
        public_key="synthetic-public",
        secret_key="synthetic-secret",
        timeout_seconds=1.0,
    )


def event(**overrides):
    values = deepcopy(BASE)
    values.update(overrides)
    if "metadata" not in overrides:
        projection = {
            "environment": "environment", "service": "service_name", "agent": "agent_slug",
            "usecase": "use_case", "provider": "provider", "model": "resolved_model",
            "requestkind": "request_kind", "queueitemid": "queue_item_id",
            "agentrunid": "agent_run_id", "reviewcaseid": "review_case_id",
            "intakerecordid": "intake_record_id", "sourcepackageid": "source_package_id",
            "symbolid": "symbol_id",
            "symboldisplayid": "symbol_display_id", "feature": "feature",
            "promptversion": "prompt_version", "initiatorkind": "initiator_kind",
            "pricingversion": "pricing_version", "costbasis": "cost_basis", "release": "release",
        }
        for metadata_key, event_key in projection.items():
            value = values[event_key]
            values["metadata"][metadata_key] = value if value is not None else "none"
    return build_llm_event(**values)


def test_builder_emits_complete_plain_normalized_schema():
    built = event()
    expected = set(BASE) - {"trace_seed"} | {"trace_id"}
    assert set(built) == expected
    assert built["trace_id"] == trace_id_from_seed(TRACE_SEED)
    assert type(built) is dict
    assert type(built["metadata"]) is dict
    assert type(built["other_usage_json"]) is dict
    assert built["cached_input_tokens"] == 10
    assert built["queue_item_id"] == BASE["queue_item_id"]


@pytest.mark.parametrize("use_case", ["workspace_chat", "admin_llm_test", "symbol_property_vision", "vlad_graphic_edit"])
@pytest.mark.parametrize("service", ["symgov-api", "libby", "vlad"])
@pytest.mark.parametrize("agent", [None, "libby", "vlad", "ed"])
def test_approved_use_cases_services_and_optional_agents(use_case, service, agent):
    assert event(use_case=use_case, service_name=service, agent_slug=agent)["agent_slug"] == agent


@pytest.mark.parametrize("provider", ["openrouter", "google", "ollama"])
@pytest.mark.parametrize("request_kind", ["text", "vision", "image_generation"])
@pytest.mark.parametrize("status", ["succeeded", "failed", "timed_out", "cancelled"])
def test_approved_provider_request_and_status_categories(provider, request_kind, status):
    overrides = {"provider": provider, "request_kind": request_kind, "status": status}
    if status != "succeeded":
        overrides.update(error_class="ProviderError", error_code="timeout")
    assert event(**overrides)["provider"] == provider


@pytest.mark.parametrize("basis", ["provider_reported", "price_snapshot", "local_policy", "estimated", "unknown"])
def test_cost_basis_has_explicit_provenance(basis):
    overrides = {"cost_basis": basis}
    if basis == "provider_reported":
        overrides.update(provider_reported_cost_usd="0.1", calculated_cost_usd=None, pricing_version=None)
    elif basis == "unknown":
        overrides.update(provider_reported_cost_usd=None, calculated_cost_usd=None, pricing_version=None)
    else:
        overrides.update(provider_reported_cost_usd=None, calculated_cost_usd="0.1", pricing_version="prices-v1")
    assert event(**overrides)["cost_basis"] == basis


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens", "image_input_units", "image_output_units"])
def test_usage_counts_are_bounded_nonnegative_plain_integers(field):
    with pytest.raises(ValueError, match="usage|bounded|integer"):
        event(**{field: True})
    with pytest.raises(ValueError, match="usage|bounded|integer"):
        event(**{field: 10**20})


def test_other_usage_is_numeric_only_bounded_and_plain():
    assert event(other_usage_json={"audio_seconds": 2, "characters": 3.5})["other_usage_json"] == {
        "audio_seconds": 2,
        "characters": 3.5,
    }
    for unsafe in ({"audio": "private transcript"}, {"prompt": 1}, {"x": float("nan")}, {str(i): i for i in range(33)}):
        with pytest.raises(ValueError, match="other_usage|numeric|bounded|allowlist"):
            event(other_usage_json=unsafe)


@pytest.mark.parametrize("field", ["queue_item_id", "agent_run_id", "review_case_id", "intake_record_id", "source_package_id", "symbol_id"])
def test_lineage_accepts_only_uuid_or_null(field):
    with pytest.raises(ValueError, match=field):
        event(**{field: "private-document-name.pdf"})


def test_queue_lineage_must_match_trusted_trace_seed():
    with pytest.raises(ValueError, match="queue_item_id|trace"):
        event(queue_item_id="00000000-0000-4000-8000-000000000099")


def test_request_trace_requires_null_queue_item_id():
    request_seed = "request:00000000-0000-4000-8000-000000000009"
    assert event(trace_seed=request_seed, queue_item_id=None)["queue_item_id"] is None
    with pytest.raises(ValueError, match="queue_item_id|request"):
        event(trace_seed=request_seed, queue_item_id="00000000-0000-4000-8000-000000000009")


def test_latency_may_be_null():
    assert event(latency_ms=None)["latency_ms"] is None


def test_exact_phase_zero_names_exclude_superseded_fields():
    built = event()
    assert {"cache_write_input_tokens", "other_usage_json", "review_case_id", "intake_record_id", "source_package_id"} <= set(built)
    assert not {"cache_write_tokens", "other_usage", "other_units", "review_id", "intake_id", "package_id"} & set(built)
    assert {"reviewcaseid", "intakerecordid", "sourcepackageid"} <= ALLOWED_METADATA_KEYS
    assert not {"reviewid", "intakeid", "packageid"} & ALLOWED_METADATA_KEYS


@pytest.mark.parametrize("kind", ["user", "api_key", "admin", "scheduled_worker", "system"])
def test_exact_approved_initiator_kinds(kind):
    assert event(initiator_kind=kind)["initiator_kind"] == kind


@pytest.mark.parametrize("kind", ["user_request", "agent"])
def test_superseded_initiator_kinds_are_rejected(kind):
    with pytest.raises(ValueError, match="initiator_kind|allowlist"):
        event(initiator_kind=kind)


@pytest.mark.parametrize("reserved", ["input", "output", "cachedInput", "cacheWrite", "reasoning", "inputImage", "outputImage"])
def test_other_usage_cannot_overwrite_normalized_langfuse_buckets(reserved):
    with pytest.raises(ValueError, match="other_usage|reserved|allowlist"):
        event(other_usage_json={reserved: 999})


@pytest.mark.parametrize("field", ["requested_model", "resolved_model"])
@pytest.mark.parametrize("value", ["write a poem about a private customer", "Bearer secret", "https://host/model", "x" * 129])
def test_dynamic_model_ids_are_compact_provider_safe_identifiers(field, value):
    with pytest.raises(ValueError, match="model|identifier|forbidden"):
        event(**{field: value})


def test_exact_keys_plain_containers_and_no_raw_content():
    built = event()
    built["prompt"] = "private prompt"
    with pytest.raises(ValueError, match="forbidden|schema|keys"):
        validate_event(built, trace_seed=TRACE_SEED)
    built = event()
    with pytest.raises(ValueError, match="metadata"):
        validate_event({**built, "metadata": {"notes": ["nested"]}}, trace_seed=TRACE_SEED)


def test_metadata_is_only_a_complete_coherent_provenance_projection():
    with pytest.raises(ValueError, match="metadata|allowlist"):
        event(metadata={**BASE["metadata"], "deployment": "private-document-reference"})
    incomplete = dict(BASE["metadata"])
    incomplete.pop("provider")
    with pytest.raises(ValueError, match="metadata|missing|provenance"):
        event(metadata=incomplete)
    contradictory = dict(BASE["metadata"])
    contradictory["model"] = "other-safe-model"
    with pytest.raises(ValueError, match="metadata|model|provenance"):
        event(metadata=contradictory)


def test_cost_provenance_is_mutually_exclusive_and_complete():
    with pytest.raises(ValueError, match="cost|pricing"):
        event(provider_reported_cost_usd="0.1")
    with pytest.raises(ValueError, match="cost|pricing"):
        event(cost_basis="unknown", calculated_cost_usd=None, pricing_version="prices-v1")


def test_failure_error_fields_are_safe_and_success_has_no_error():
    with pytest.raises(ValueError, match="error"):
        event(error_class="ProviderError")
    with pytest.raises(ValueError, match="error|identifier|forbidden"):
        event(status="failed", error_class="customer@example.invalid", error_code="bad")


def test_hmac_pseudonym_is_stable_scoped_and_null_when_unconfigured():
    principal = "00000000-0000-4000-8000-000000000004"
    first = initiator_pseudonym(principal, "synthetic-secret-a")
    assert first == initiator_pseudonym(principal, "synthetic-secret-a")
    assert first != initiator_pseudonym(principal, "synthetic-secret-b")
    assert len(first) == 64
    assert initiator_pseudonym(principal, None) is None
    assert initiator_pseudonym(principal, "") is None


def test_hmac_helper_errors_and_repr_do_not_leak_secret():
    secret = "synthetic-do-not-render"
    with pytest.raises(ValueError) as exc:
        initiator_pseudonym("not-a-uuid", secret)
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)


def test_telemetry_preserves_sequential_attempts_and_flushes():
    delivered = []
    adapter = LLMTelemetry(config=_enabled_config(), transport=delivered.append)
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert not adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.record(event(event_id="22222222-2222-4222-8222-222222222222", observation_id="attempt-2", attempt_number=2), trace_seed=TRACE_SEED)
    assert adapter.flush(timeout=1.0)
    assert [item["attempt_number"] for item in delivered] == [1, 2]
    assert adapter.close(timeout=1.0)
    assert not adapter.record(event(event_id="33333333-3333-4333-8333-333333333333", observation_id="attempt-3", attempt_number=3), trace_seed=TRACE_SEED)


def test_export_failures_are_nonfatal_and_close_is_bounded():
    adapter = LLMTelemetry(config=_enabled_config(), transport=Mock(side_effect=RuntimeError("offline")))
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.close(timeout=1.0)


def _request_event(suffix, **overrides):
    trace_seed = f"request:00000000-0000-4000-8000-{suffix:012d}"
    return trace_seed, event(trace_seed=trace_seed, queue_item_id=None, **overrides)


def test_completed_lineage_is_evicted_on_capacity_pressure_and_replay_is_safe():
    delivered = []
    adapter = LLMTelemetry(config=_enabled_config(), transport=delivered.append, lineage_capacity=1)
    first_seed, first = _request_event(11)
    second_seed, second = _request_event(12)

    assert adapter.record(first, trace_seed=first_seed)
    assert adapter.flush(timeout=1.0)
    assert adapter.record(second, trace_seed=second_seed)
    assert adapter.flush(timeout=1.0)
    assert not adapter.record(
        event(
            trace_seed=first_seed,
            queue_item_id=None,
            event_id="22222222-2222-4222-8222-222222222222",
            observation_id="attempt-2",
            attempt_number=2,
        ),
        trace_seed=first_seed,
    )
    assert adapter.record(first, trace_seed=first_seed)
    assert adapter.close(timeout=1.0)
    assert [item["trace_id"] for item in delivered] == [
        first["trace_id"], second["trace_id"], first["trace_id"],
    ]
    assert len(adapter._next_attempt) == 1


def test_active_lineage_is_not_evicted_on_capacity_pressure():
    entered = Event()
    release = Event()

    def blocking_transport(_item):
        entered.set()
        assert release.wait(1.0)

    adapter = LLMTelemetry(config=_enabled_config(), transport=blocking_transport, lineage_capacity=1)
    first_seed, first = _request_event(21)
    second_seed, second = _request_event(22)
    assert adapter.record(first, trace_seed=first_seed)
    assert entered.wait(1.0)
    assert not adapter.record(second, trace_seed=second_seed)
    release.set()
    assert adapter.close(timeout=1.0)


def test_close_can_retry_after_timeout_and_enqueues_one_stop():
    entered = Event()
    release = Event()

    def blocking_transport(_item):
        entered.set()
        assert release.wait(1.0)

    class CountingQueue(Queue):
        def __init__(self):
            super().__init__(maxsize=128)
            self.stop_count = 0

        def put_nowait(self, item):
            if item is telemetry._STOP:
                self.stop_count += 1
            return super().put_nowait(item)

    adapter = LLMTelemetry(config=_enabled_config(), transport=blocking_transport)
    queue = CountingQueue()
    adapter._queue = queue
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert entered.wait(1.0)

    assert adapter.close(timeout=0.01) is False
    release.set()
    assert adapter.close(timeout=1.0) is True

    assert queue.stop_count == 1
    assert queue.unfinished_tasks == 0
    assert adapter._worker is None

    results = []
    callers = [Thread(target=lambda: results.append(adapter.close(timeout=1.0))) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(1.0)
    assert results == [True, True]
    assert queue.stop_count == 1


def test_close_retry_does_not_duplicate_an_enqueued_stop():
    stop_task_done = Event()
    release_stop_task_done = Event()

    class BlockingStopQueue(Queue):
        def __init__(self):
            super().__init__(maxsize=128)
            self.stop_count = 0

        def put_nowait(self, item):
            if item is telemetry._STOP:
                self.stop_count += 1
            return super().put_nowait(item)

        def task_done(self):
            if self.stop_count:
                stop_task_done.set()
                assert release_stop_task_done.wait(1.0)
            return super().task_done()

    adapter = LLMTelemetry(config=_enabled_config(), transport=lambda _item: None)
    queue = BlockingStopQueue()
    adapter._queue = queue
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert adapter.flush(timeout=1.0)

    assert adapter.close(timeout=0.01) is False
    assert stop_task_done.wait(1.0)
    assert queue.stop_count == 1
    release_stop_task_done.set()

    assert adapter.close(timeout=1.0) is True
    assert queue.stop_count == 1
    assert queue.unfinished_tasks == 0
    assert adapter._worker is None


def test_concurrent_close_callers_share_one_complete_shutdown_result():
    entered = Event()
    release = Event()
    start = Barrier(3)

    def blocking_transport(_item):
        entered.set()
        assert release.wait(1.0)

    class CountingQueue(Queue):
        def __init__(self):
            super().__init__(maxsize=128)
            self.stop_count = 0

        def put_nowait(self, item):
            if item is telemetry._STOP:
                self.stop_count += 1
            return super().put_nowait(item)

    adapter = LLMTelemetry(config=_enabled_config(), transport=blocking_transport)
    queue = CountingQueue()
    adapter._queue = queue
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert entered.wait(1.0)
    results = []

    def close_adapter():
        start.wait()
        results.append(adapter.close(timeout=1.0))

    callers = [Thread(target=close_adapter) for _ in range(2)]
    for caller in callers:
        caller.start()
    start.wait()
    release.set()
    for caller in callers:
        caller.join(1.0)

    assert results == [True, True]
    assert queue.stop_count == 1
    assert queue.unfinished_tasks == 0
    assert adapter._worker is None


def test_concurrent_close_timeout_includes_waiting_for_shutdown_lock():
    transport_entered = Event()
    release_transport = Event()

    def blocking_transport(_item):
        transport_entered.set()
        assert release_transport.wait(1.0)

    class ObservableLock:
        def __init__(self):
            self._lock = Lock()
            self.acquired = Event()

        def acquire(self, *args, **kwargs):
            acquired = self._lock.acquire(*args, **kwargs)
            if acquired:
                self.acquired.set()
            return acquired

        def release(self):
            self._lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_args):
            self.release()

    adapter = LLMTelemetry(config=_enabled_config(), transport=blocking_transport)
    close_lock = ObservableLock()
    adapter._close_lock = close_lock
    assert adapter.record(event(), trace_seed=TRACE_SEED)
    assert transport_entered.wait(1.0)

    first_result = []
    first_caller = Thread(target=lambda: first_result.append(adapter.close(timeout=1.0)))
    first_caller.start()
    assert close_lock.acquired.wait(1.0)

    second_result = []

    def close_without_waiting():
        started = time.monotonic()
        second_result.append((adapter.close(timeout=0), time.monotonic() - started))

    second_caller = Thread(target=close_without_waiting)
    second_caller.start()
    second_caller.join(0.1)
    second_exceeded_budget = second_caller.is_alive()

    release_transport.set()
    first_caller.join(1.0)
    second_caller.join(1.0)

    assert not second_exceeded_budget
    assert second_result and second_result[0][0] is False
    assert second_result[0][1] < 0.1
    assert first_result == [True]
    assert adapter.close(timeout=0) is True


@pytest.mark.parametrize("timeout", [0, 0.02])
def test_close_timeout_includes_waiting_for_internal_state_lock(timeout):
    adapter = LLMTelemetry(config=_enabled_config(), transport=lambda _item: None)
    entered = Event()
    results = []

    def close_adapter():
        entered.set()
        started = time.monotonic()
        results.append((adapter.close(timeout=timeout), time.monotonic() - started))

    adapter._lock.acquire()
    caller = Thread(target=close_adapter)
    caller.start()
    assert entered.wait(1.0)
    caller.join(0.15)
    exceeded_budget = caller.is_alive()
    adapter._lock.release()
    caller.join(1.0)

    assert not exceeded_budget
    assert results and results[0][0] is False
    assert results[0][1] < 0.15
    assert adapter.close(timeout=1.0) is True


@pytest.mark.parametrize("value", ["1", "TRUE", "yes", " false ", ""])
def test_activation_requires_exact_true_and_complete_configuration(monkeypatch, value):
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENABLED", value)
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENDPOINT", "https://langfuse.invalid/api/public/ingestion")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY", "pk")
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY", "sk")
    assert not TelemetryConfig.from_env().enabled


def test_missing_transport_configuration_disables(monkeypatch):
    monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENABLED", "true")
    for missing in ("SYMGOV_LLM_TELEMETRY_ENDPOINT", "SYMGOV_LLM_TELEMETRY_PUBLIC_KEY", "SYMGOV_LLM_TELEMETRY_SECRET_KEY"):
        monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_ENDPOINT", "https://langfuse.invalid/api/public/ingestion")
        monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY", "pk")
        monkeypatch.setenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY", "sk")
        monkeypatch.delenv(missing)
        assert not TelemetryConfig.from_env().enabled
