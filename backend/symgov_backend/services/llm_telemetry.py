"""Privacy-safe, provider-neutral telemetry boundary for LLM attempts.

This module deliberately contains no provider or Langfuse client.  Callers hand an
already allowlisted event to ``LLMTelemetry`` and, when explicitly enabled, an
injected transport receives a defensive copy on a daemon thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import os
from queue import Queue
import re
from threading import Lock, Thread, current_thread
from typing import Any, Callable, Mapping


ALLOWED_METADATA_KEYS = {
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

_REQUIRED_METADATA_KEYS = {
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
}

_ALLOWED_EVENT_KEYS = {
    "environment",
    "trace_id",
    "observation_id",
    "use_case",
    "service_name",
    "agent_slug",
    "provider",
    "requested_model",
    "resolved_model",
    "request_kind",
    "attempt_number",
    "status",
    "latency_ms",
    "cost_basis",
    "provider_reported_cost_usd",
    "calculated_cost_usd",
    "pricing_version",
    "metadata",
}
_REQUIRED_EVENT_KEYS = _ALLOWED_EVENT_KEYS
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{64}$")
_TRACE_SEED = re.compile(
    r"^(?:queue|request):[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_OBSERVATION_ID = re.compile(r"^attempt-[1-9][0-9]{0,4}$")
_SYMBOL_DISPLAY_ID = re.compile(r"^[0-9]{4}-[0-9]{1,6}$")
_COST_STRING = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SEMANTIC_EVENT_VALUES = {
    "environment": {"development", "test", "staging", "production"},
    "use_case": {"libby_symbol_vision"},
    "service_name": {"libby"},
    "agent_slug": {"libby"},
    "provider": {"google", "openrouter"},
    "requested_model": {"gemini-2.5-flash", "openai/gpt-4o-mini"},
    "resolved_model": {"gemini-2.5-flash", "openai/gpt-4o-mini"},
    "request_kind": {"vision"},
    "status": {"started", "succeeded", "failed", "timeout", "cancelled"},
    "cost_basis": {"provider_reported", "price_snapshot", "none"},
}
_SEMANTIC_METADATA_VALUES = {
    "environment": _SEMANTIC_EVENT_VALUES["environment"],
    "service": _SEMANTIC_EVENT_VALUES["service_name"],
    "agent": _SEMANTIC_EVENT_VALUES["agent_slug"],
    "usecase": _SEMANTIC_EVENT_VALUES["use_case"],
    "provider": _SEMANTIC_EVENT_VALUES["provider"],
    "model": _SEMANTIC_EVENT_VALUES["requested_model"],
    "requestkind": _SEMANTIC_EVENT_VALUES["request_kind"],
    "feature": {"symbol_vision"},
    "initiatorkind": {"scheduled_worker", "system", "user_request"},
    "pricingversion": {"openrouter-2026-07-01"},
    "costbasis": _SEMANTIC_EVENT_VALUES["cost_basis"],
}

Transport = Callable[[dict[str, Any]], Any]


def _env_enabled(value: str | None) -> bool:
    return value == "true"


@dataclass(frozen=True)
class TelemetryConfig:
    """Explicit telemetry configuration; absent configuration is disabled."""

    enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        return cls(enabled=_env_enabled(os.getenv("SYMGOV_LLM_TELEMETRY_ENABLED")))


def trace_id_from_seed(seed: str) -> str:
    """Derive a stable opaque trace id from a non-human queue/request UUID."""

    if not isinstance(seed, str) or not _TRACE_SEED.fullmatch(seed):
        raise ValueError("trace seed must be queue:<uuid> or request:<uuid>")
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


def _validate_identifier(name: str, value: Any) -> None:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a compact allowlisted identifier of at most 200 characters")
    lowered = value.lower()
    if "://" in lowered or lowered.startswith("data:") or "bearer " in lowered or "@" in value:
        raise ValueError(f"{name} contains forbidden content")


def _validate_cost(name: str, value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _COST_STRING.fullmatch(value):
        raise ValueError(f"{name} must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal("1000000"):
        raise ValueError(f"{name} must be a finite bounded non-negative decimal")


def validate_event(event: Mapping[str, Any], *, trace_seed: str) -> None:
    """Fail closed unless the complete event matches the safe event schema."""

    event = _plain_event_snapshot(event)
    keys = set(event)
    unknown = keys - _ALLOWED_EVENT_KEYS
    missing = _REQUIRED_EVENT_KEYS - keys
    if unknown:
        raise ValueError(f"event contains forbidden keys outside the allowlist: {sorted(unknown)}")
    if missing:
        raise ValueError(f"event is missing allowlisted keys: {sorted(missing)}")

    trace_id = event["trace_id"]
    if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
        raise ValueError("trace_id must be a deterministic lowercase SHA-256 identifier")
    if trace_id != trace_id_from_seed(trace_seed):
        raise ValueError("trace_id does not match the trusted trace seed")
    observation_id = event["observation_id"]
    if not isinstance(observation_id, str) or not _OBSERVATION_ID.fullmatch(observation_id):
        raise ValueError("observation_id is outside the allowlist")
    for key, allowed_values in _SEMANTIC_EVENT_VALUES.items():
        if event[key] not in allowed_values:
            raise ValueError(f"{key} is outside the semantic allowlist")

    if (
        not isinstance(event["attempt_number"], int)
        or isinstance(event["attempt_number"], bool)
        or not 1 <= event["attempt_number"] <= 10_000
    ):
        raise ValueError("attempt_number must be a bounded positive integer")
    if int(observation_id.removeprefix("attempt-")) != event["attempt_number"]:
        raise ValueError("observation_id must identify the same attempt_number")
    if (
        not isinstance(event["latency_ms"], int)
        or isinstance(event["latency_ms"], bool)
        or not 0 <= event["latency_ms"] <= 86_400_000
    ):
        raise ValueError("latency_ms must be a bounded non-negative integer")

    _validate_cost("provider_reported_cost_usd", event["provider_reported_cost_usd"])
    _validate_cost("calculated_cost_usd", event["calculated_cost_usd"])
    if event["pricing_version"] is not None:
        _validate_identifier("pricing_version", event["pricing_version"])
        if event["pricing_version"] not in _SEMANTIC_METADATA_VALUES["pricingversion"]:
            raise ValueError("pricing_version is outside the semantic allowlist")

    provider_cost = event["provider_reported_cost_usd"]
    calculated_cost = event["calculated_cost_usd"]
    pricing_version = event["pricing_version"]
    if event["provider"] == "google":
        if event["requested_model"] != "gemini-2.5-flash" or event["resolved_model"] != "gemini-2.5-flash":
            raise ValueError("google provider requires the allowlisted Gemini model")
        if pricing_version is not None:
            raise ValueError("google provider cannot use OpenRouter pricing provenance")
    elif event["requested_model"] != event["resolved_model"]:
        raise ValueError("openrouter requested and resolved models must be coherent")

    if event["cost_basis"] == "provider_reported":
        if provider_cost is None or calculated_cost is not None or pricing_version is not None:
            raise ValueError("provider-reported cost provenance is invalid")
    elif event["cost_basis"] == "price_snapshot":
        if provider_cost is not None or calculated_cost is None or pricing_version is None:
            raise ValueError("price-snapshot cost requires a pricing version")
    elif provider_cost is not None or calculated_cost is not None or pricing_version is not None:
        raise ValueError("cost_basis none cannot include cost or pricing values")

    metadata = event["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an allowlisted mapping")
    unknown_metadata = set(metadata) - ALLOWED_METADATA_KEYS
    if unknown_metadata:
        raise ValueError(f"metadata contains keys outside the allowlist: {sorted(unknown_metadata)}")
    required_metadata = set(_REQUIRED_METADATA_KEYS)
    if pricing_version is not None:
        required_metadata.add("pricingversion")
    missing_metadata = required_metadata - set(metadata)
    if missing_metadata:
        raise ValueError(f"metadata is missing required provenance: {sorted(missing_metadata)}")
    for key, value in metadata.items():
        _validate_identifier(f"metadata.{key}", value)
        if key in _SEMANTIC_METADATA_VALUES and value not in _SEMANTIC_METADATA_VALUES[key]:
            raise ValueError(f"metadata.{key} is outside the semantic allowlist")
        if key in {"queueitemid", "agentrunid", "symbolid"} and not _UUID.fullmatch(value):
            raise ValueError(f"metadata.{key} is outside the identifier allowlist")
        if key == "symboldisplayid" and not _SYMBOL_DISPLAY_ID.fullmatch(value):
            raise ValueError("metadata.symboldisplayid is outside the identifier allowlist")

    if metadata["queueitemid"].lower() != trace_seed.split(":", 1)[1].lower():
        raise ValueError("metadata.queueitemid contradicts trusted trace_seed provenance")

    duplicated_provenance = {
        "environment": "environment",
        "service": "service_name",
        "agent": "agent_slug",
        "usecase": "use_case",
        "provider": "provider",
        "model": "resolved_model",
        "requestkind": "request_kind",
        "costbasis": "cost_basis",
        "pricingversion": "pricing_version",
    }
    for metadata_key, event_key in duplicated_provenance.items():
        if metadata_key in metadata and metadata[metadata_key] != event[event_key]:
            raise ValueError(f"metadata.{metadata_key} contradicts {event_key}")


def _plain_event_snapshot(event: Mapping[str, Any]) -> dict[str, Any]:
    """Read each bounded allowlisted field once into plain containers."""

    if not isinstance(event, Mapping):
        raise ValueError("event must be a mapping")
    if len(event) != len(_ALLOWED_EVENT_KEYS):
        raise ValueError("event contains forbidden keys or is missing the bounded allowlist schema")

    snapshot: dict[str, Any] = {}
    for key in _ALLOWED_EVENT_KEYS:
        try:
            value = event[key]
        except KeyError as exc:
            raise ValueError(f"event is missing allowlisted key: {key}") from exc
        if key != "metadata" and value is not None and type(value) not in {str, int}:
            raise ValueError(f"event.{key} must be a plain scalar")
        snapshot[key] = value

    metadata = snapshot["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an allowlisted mapping")
    metadata_size = len(metadata)
    if metadata_size > len(ALLOWED_METADATA_KEYS):
        raise ValueError("metadata exceeds the bounded allowlist")

    plain_metadata: dict[str, str] = {}
    for key in ALLOWED_METADATA_KEYS:
        try:
            value = metadata[key]
        except KeyError:
            continue
        if type(value) is not str:
            raise ValueError(f"metadata.{key} must be a plain string")
        plain_metadata[key] = value
    if len(plain_metadata) != metadata_size:
        raise ValueError("metadata contains keys outside the bounded allowlist")
    snapshot["metadata"] = plain_metadata
    return snapshot


def build_llm_event(
    *,
    environment: str,
    trace_seed: str,
    observation_id: str,
    use_case: str,
    service_name: str,
    agent_slug: str,
    provider: str,
    requested_model: str,
    resolved_model: str,
    request_kind: str,
    attempt_number: int,
    status: str,
    latency_ms: int,
    cost_basis: str,
    provider_reported_cost_usd: str | None,
    calculated_cost_usd: str | None,
    pricing_version: str | None,
    metadata: Mapping[str, str],
) -> dict[str, Any]:
    """Build and validate one immutable-by-convention retry/fallback attempt."""

    event: dict[str, Any] = {
        "environment": environment,
        "trace_id": trace_id_from_seed(trace_seed),
        "observation_id": observation_id,
        "use_case": use_case,
        "service_name": service_name,
        "agent_slug": agent_slug,
        "provider": provider,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "request_kind": request_kind,
        "attempt_number": attempt_number,
        "status": status,
        "latency_ms": latency_ms,
        "cost_basis": cost_basis,
        "provider_reported_cost_usd": provider_reported_cost_usd,
        "calculated_cost_usd": calculated_cost_usd,
        "pricing_version": pricing_version,
        "metadata": metadata,
    }
    event = _plain_event_snapshot(event)
    validate_event(event, trace_seed=trace_seed)
    return event


class LLMTelemetry:
    """Non-blocking, non-fatal adapter around an injected transport."""

    def __init__(
        self,
        *,
        config: TelemetryConfig | None = None,
        transport: Transport | None = None,
        lineage_capacity: int = 4_096,
    ) -> None:
        if (
            not isinstance(lineage_capacity, int)
            or isinstance(lineage_capacity, bool)
            or not 1 <= lineage_capacity <= 4_096
        ):
            raise ValueError("lineage_capacity must be a bounded integer from 1 to 4096")
        self.config = config or TelemetryConfig.from_env()
        self._transport = transport
        self._lineage_capacity = lineage_capacity
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=128)
        self._lock = Lock()
        self._worker: Thread | None = None
        self._next_attempt: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self._transport is not None

    def record(self, event: Mapping[str, Any], *, trace_seed: str | None = None) -> bool:
        """Queue one validated attempt; return False when disabled or invalid."""

        if not self.enabled:
            return False
        try:
            if trace_seed is None:
                return False
            safe_event = _plain_event_snapshot(event)
            validate_event(safe_event, trace_seed=trace_seed)
            trace_id = safe_event["trace_id"]
            attempt_number = safe_event["attempt_number"]
        except Exception:
            return False

        try:
            with self._lock:
                if trace_id not in self._next_attempt and len(self._next_attempt) >= self._lineage_capacity:
                    return False
                if attempt_number != self._next_attempt.get(trace_id, 1):
                    return False
                if self._worker is not None and not self._worker.is_alive():
                    self._worker = None
                if self._worker is None:
                    worker = Thread(target=self._dispatch, daemon=True)
                    self._worker = worker
                    try:
                        worker.start()
                    except Exception:
                        self._worker = None
                        return False
                self._queue.put_nowait(safe_event)
                self._next_attempt[trace_id] = attempt_number + 1
                return True
        except Exception:
            return False

    def _dispatch(self) -> None:
        worker = current_thread()
        try:
            while True:
                try:
                    event = self._queue.get()
                except Exception:
                    return
                self._deliver_safely(event)
                try:
                    self._queue.task_done()
                except Exception:
                    return
        finally:
            try:
                with self._lock:
                    if self._worker is worker:
                        self._worker = None
            except Exception:
                if self._worker is worker:
                    self._worker = None

    def _deliver_safely(self, event: dict[str, Any]) -> None:
        try:
            assert self._transport is not None
            self._transport(event)
        except Exception:
            # Telemetry must never affect the originating LLM operation.
            return
