"""Contract tests for the authoritative immutable LLM usage ledger."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import BigInteger, Numeric
from sqlalchemy.exc import IntegrityError

from symgov_backend.models import LLMUsageEvent
from symgov_backend.services.llm_telemetry import build_llm_event
from symgov_backend.services.llm_usage_ledger import (
    LLMUsageConflictError,
    persist_llm_usage_event,
    record_llm_usage_event_best_effort,
)

TRACE_SEED = "queue:00000000-0000-4000-8000-000000000001"
EVENT_FIELDS = {
    "event_id": "11111111-1111-4111-8111-111111111111",
    "occurred_at_utc": "2026-07-16T19:00:00.123Z",
    "environment": "development",
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
        "environment": "development", "service": "libby", "agent": "libby",
        "usecase": "symbol_property_vision", "provider": "google",
        "model": "gemini-2.5-flash-001", "requestkind": "vision",
        "queueitemid": "00000000-0000-4000-8000-000000000001",
        "agentrunid": "00000000-0000-4000-8000-000000000002",
        "symbolid": "00000000-0000-4000-8000-000000000003",
        "symboldisplayid": "0003-12", "feature": "symbol-vision",
        "promptversion": "vision-v3", "initiatorkind": "scheduled_worker",
        "pricingversion": "google-2026-07-01", "costbasis": "price_snapshot",
        "release": "2026.07.16",
    },
}


def event(**overrides):
    fields = deepcopy(EVENT_FIELDS)
    fields.update(overrides)
    return build_llm_event(trace_seed=TRACE_SEED, **fields)


def empty_result(*rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


def test_model_exactly_mirrors_sanitized_event_plus_server_recording_time():
    from symgov_backend.services import llm_telemetry

    table = LLMUsageEvent.__table__
    assert set(table.columns.keys()) == llm_telemetry._EVENT_KEYS | {"recorded_at_utc"}
    assert table.primary_key.columns.keys() == ["event_id"]
    assert table.c.recorded_at_utc.server_default is not None
    assert table.c.recorded_at_utc.type.timezone is True
    assert table.c.other_usage_json.type.__class__.__name__ == "JSONB"
    assert table.c.metadata.type.__class__.__name__ == "JSONB"
    assert isinstance(table.c.input_tokens.type, BigInteger)
    assert isinstance(table.c.calculated_cost_usd.type, Numeric)
    assert (table.c.calculated_cost_usd.type.precision, table.c.calculated_cost_usd.type.scale) == (20, 9)
    assert not ({"prompt", "response", "content", "payload", "prompt_text", "response_text"} & set(table.columns.keys()))


def test_model_has_soft_lineage_constraints_and_useful_indexes():
    table = LLMUsageEvent.__table__
    lineage = {"queue_item_id", "agent_run_id", "review_case_id", "intake_record_id", "source_package_id", "symbol_id"}
    assert not table.foreign_keys
    indexed_columns = {column.name for index in table.indexes for column in index.columns}
    assert lineage <= indexed_columns
    assert {"occurred_at_utc", "provider", "use_case", "agent_slug", "feature", "trace_id"} <= indexed_columns
    assert any(
        constraint.__class__.__name__ == "UniqueConstraint"
        and [column.name for column in constraint.columns] == ["trace_id", "observation_id"]
        for constraint in table.constraints
    )
    checks = "\n".join(str(constraint.sqltext) for constraint in table.constraints if constraint.__class__.__name__ == "CheckConstraint")
    for value in ("development", "production", "workspace_chat", "vlad_graphic_edit", "openrouter", "ollama", "succeeded", "cancelled", "provider_reported", "unknown"):
        assert value in checks
    for field in ("attempt_number", "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens", "image_input_units", "image_output_units"):
        assert field in checks and f"{field} >=" in checks


def test_persist_maps_exact_snapshot_and_converts_typed_values_without_commit():
    session = MagicMock()
    session.execute.return_value = empty_result()
    session.begin_nested.return_value = nullcontext()
    source = event()

    row = persist_llm_usage_event(session, source, TRACE_SEED)

    assert isinstance(row, LLMUsageEvent)
    assert row.event_id == uuid.UUID(source["event_id"])
    assert row.occurred_at_utc == datetime(2026, 7, 16, 19, 0, 0, 123000, tzinfo=timezone.utc)
    assert row.calculated_cost_usd == Decimal("0.001250")
    assert row.provider_reported_cost_usd is None
    assert row.queue_item_id == uuid.UUID(source["queue_item_id"])
    assert row.other_usage_json == source["other_usage_json"] and row.other_usage_json is not source["other_usage_json"]
    assert row.metadata_json == source["metadata"] and row.metadata_json is not source["metadata"]
    assert "recorded_at_utc" not in row.__dict__
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()


def test_persist_rejects_forbidden_fields_before_touching_session():
    session = MagicMock()
    unsafe = event()
    unsafe["prompt"] = "must never persist"
    with pytest.raises(ValueError, match="forbidden|schema|keys"):
        persist_llm_usage_event(session, unsafe, TRACE_SEED)
    session.execute.assert_not_called()
    session.add.assert_not_called()


def test_persist_inserts_only_the_same_defensive_snapshot_that_was_validated():
    class MutatingMapping(Mapping):
        def __init__(self, values):
            self.data = values
            self.metadata_reads = 0

        def __iter__(self):
            return iter(self.data)

        def __len__(self):
            return len(self.data)

        def __getitem__(self, key):
            if key == "metadata":
                self.metadata_reads += 1
                if self.metadata_reads > 1:
                    return {"notes": "unvalidated content"}
            return self.data[key]

    session = MagicMock()
    session.execute.return_value = empty_result()
    session.begin_nested.return_value = nullcontext()

    row = persist_llm_usage_event(session, MutatingMapping(event()), TRACE_SEED)

    assert row.metadata_json == EVENT_FIELDS["metadata"]


def test_identical_event_id_and_trace_observation_duplicate_is_idempotent():
    duplicate = event()
    existing = LLMUsageEvent(**_converted_values(duplicate))
    session = MagicMock()
    session.execute.return_value = empty_result(existing)

    assert persist_llm_usage_event(session, duplicate, TRACE_SEED) is existing
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_conflicting_duplicate_raises_safe_specific_error():
    incoming = event()
    existing_values = _converted_values(incoming)
    existing_values["status"] = "failed"
    existing = LLMUsageEvent(**existing_values)
    session = MagicMock()
    session.execute.return_value = empty_result(existing)

    with pytest.raises(LLMUsageConflictError) as caught:
        persist_llm_usage_event(session, incoming, TRACE_SEED)

    assert str(caught.value) == "LLM usage event conflicts with an existing ledger record"
    assert incoming["trace_id"] not in str(caught.value)
    assert incoming["metadata"]["model"] not in repr(caught.value)


def test_integrity_race_uses_savepoint_and_preserves_caller_transaction():
    incoming = event()
    existing = LLMUsageEvent(**_converted_values(incoming))
    session = MagicMock()
    session.execute.side_effect = [empty_result(), empty_result(existing)]
    session.begin_nested.return_value = nullcontext()
    session.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    assert persist_llm_usage_event(session, incoming, TRACE_SEED) is existing
    session.begin_nested.assert_called_once_with()
    session.rollback.assert_not_called()
    session.commit.assert_not_called()


def test_best_effort_owns_session_commit_and_close():
    session = MagicMock()
    factory = MagicMock(return_value=session)
    session.execute.return_value = empty_result()
    session.begin_nested.return_value = nullcontext()

    assert record_llm_usage_event_best_effort(factory, event(), TRACE_SEED) is True
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_best_effort_is_nonfatal_and_logs_no_event_or_exception_values(caplog):
    marker = "synthetic-secret-marker"
    session = MagicMock()
    session.execute.side_effect = RuntimeError(marker)
    factory = MagicMock(return_value=session)

    with caplog.at_level(logging.ERROR):
        assert record_llm_usage_event_best_effort(factory, event(), TRACE_SEED) is False

    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    assert marker not in caplog.text
    assert EVENT_FIELDS["resolved_model"] not in caplog.text
    assert "LLM usage event persistence failed" in caplog.text


def _converted_values(item):
    uuid_fields = {"event_id", "queue_item_id", "agent_run_id", "review_case_id", "intake_record_id", "source_package_id", "symbol_id"}
    cost_fields = {"provider_reported_cost_usd", "calculated_cost_usd"}
    values = deepcopy(item)
    values["occurred_at_utc"] = datetime.fromisoformat(values["occurred_at_utc"].replace("Z", "+00:00"))
    for key in uuid_fields:
        values[key] = uuid.UUID(values[key]) if values[key] is not None else None
    for key in cost_fields:
        values[key] = Decimal(values[key]) if values[key] is not None else None
    values["metadata_json"] = values.pop("metadata")
    return values
