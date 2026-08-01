"""Privacy-safe normalized LLM usage events and optional Langfuse export.

Telemetry is disabled unless explicitly and completely configured.  Only the exact,
bounded schema in this module may cross the transport boundary; prompts, responses,
images, documents, user identifiers, and provider payloads are never accepted.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import ipaddress
import json
import math
import os
from queue import Full, Queue
import re
from threading import Lock, Thread, current_thread
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid


_APPROVED_INTERNAL_INGESTION_ENDPOINT = (
    "http://symgov-langfuse:3000/api/public/ingestion"
)


ALLOWED_METADATA_KEYS = {
    "environment", "service", "agent", "usecase", "provider", "model",
    "requestkind", "queueitemid", "agentrunid", "reviewcaseid", "intakerecordid",
    "sourcepackageid", "symbolid", "symboldisplayid", "feature", "promptversion",
    "initiatorkind", "initiatorpseudonym", "pricingversion", "costbasis", "release",
}
_REQUIRED_METADATA_KEYS = {
    "environment", "service", "agent", "usecase", "provider", "model",
    "requestkind", "queueitemid", "initiatorkind", "costbasis",
}

_EVENT_KEYS = {
    "event_id", "occurred_at_utc", "environment", "trace_id", "observation_id",
    "use_case", "service_name", "agent_slug", "provider", "requested_model",
    "resolved_model", "request_kind", "attempt_number", "status", "latency_ms",
    "cost_currency", "cost_basis", "provider_reported_cost_usd",
    "calculated_cost_usd", "pricing_version", "input_tokens", "output_tokens",
    "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens",
    "image_input_units", "image_output_units", "other_usage_json",
    "queue_item_id", "agent_run_id", "review_case_id", "intake_record_id", "source_package_id",
    "symbol_id", "symbol_display_id", "feature", "prompt_version", "release",
    "initiator_kind", "initiator_pseudonym", "error_class", "error_code", "metadata",
}
_USAGE_FIELDS = {
    "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "reasoning_tokens", "image_input_units", "image_output_units",
}
_LINEAGE_FIELDS = {
    "queue_item_id", "agent_run_id", "review_case_id", "intake_record_id", "source_package_id", "symbol_id",
}
_CATEGORIES = {
    "environment": {"development", "test", "staging", "production"},
    "use_case": {"workspace_chat", "admin_llm_test", "symbol_property_vision", "vlad_graphic_edit"},
    "service_name": {"symgov-api", "libby", "vlad"},
    "agent_slug": {None, "libby", "vlad", "ed"},
    "provider": {"openrouter", "google", "ollama"},
    "request_kind": {"text", "vision", "image_generation"},
    "status": {"succeeded", "failed", "timed_out", "cancelled"},
    "cost_basis": {"provider_reported", "price_snapshot", "local_policy", "estimated", "unknown"},
    "initiator_kind": {"user", "api_key", "admin", "scheduled_worker", "system"},
}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_TRACE_RE = re.compile(r"^[0-9a-f]{64}$")
_TRACE_SEED_RE = re.compile(r"^(queue|request):([0-9a-fA-F-]{36})$")
_OBSERVATION_RE = re.compile(r"^attempt-([1-9][0-9]{0,4})$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ERROR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL_DISPLAY_RE = re.compile(r"^[0-9]{4}-[0-9]{1,6}$")
_COST_RE = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_OTHER_USAGE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_NONCANONICAL_IP_LABEL_RE = re.compile(r"^(?:[0-9]+|0[xX][0-9A-Fa-f]+)$")
_FORBIDDEN_OTHER_KEYS = {"prompt", "completion", "content", "text", "document", "authorization", "email", "user_id"}
_RESERVED_LANGFUSE_USAGE_KEYS = {
    "input", "output", "cachedInput", "cacheWrite", "reasoning", "inputImage", "outputImage",
}
_MAX_USAGE = 1_000_000_000_000
_STOP = object()

Transport = Callable[[dict[str, Any]], Any]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Fail closed on redirects so Basic credentials never leave the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


def urlopen(request: Request, *, timeout: float):
    """Open one request without the standard library's automatic redirect handling."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _canonical_uuid(name: str, value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not _UUID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical UUID or null")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a canonical UUID or null") from exc
    return value


def trace_id_from_seed(seed: str) -> str:
    """Derive a stable opaque trace id from a queue/request UUID."""
    if type(seed) is not str:
        raise ValueError("trace seed must be queue:<uuid> or request:<uuid>")
    match = _TRACE_SEED_RE.fullmatch(seed)
    if not match:
        raise ValueError("trace seed must be queue:<uuid> or request:<uuid>")
    _canonical_uuid("trace seed UUID", match.group(2).lower())
    return hashlib.sha256(seed.lower().encode("ascii")).hexdigest()


def initiator_pseudonym(principal_uuid: str, secret: str | bytes | None) -> str | None:
    """Return a scoped HMAC pseudonym without retaining the supplied secret."""
    if secret is None or secret == "" or secret == b"":
        return None
    principal = _canonical_uuid("principal_uuid", principal_uuid)
    assert principal is not None
    if type(secret) is str:
        key = secret.encode("utf-8")
    elif type(secret) is bytes:
        key = secret
    else:
        raise TypeError("pseudonym secret must be text or bytes")
    if not 1 <= len(key) <= 4096:
        raise ValueError("pseudonym secret must have a bounded non-zero length")
    return hmac.new(key, principal.encode("ascii"), hashlib.sha256).hexdigest()


def _validate_identifier(name: str, value: Any, *, optional: bool = False, pattern: re.Pattern[str] = _IDENTIFIER_RE) -> None:
    if value is None and optional:
        return
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"{name} must be a compact safe identifier")
    lowered = value.lower()
    if "://" in lowered or lowered.startswith("data:") or "bearer" in lowered or "@" in value:
        raise ValueError(f"{name} contains forbidden content")


def _validate_cost(name: str, value: Any) -> None:
    if value is None:
        return
    if type(value) is not str or not _COST_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded decimal cost string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a bounded decimal cost string") from exc
    if not number.is_finite() or number < 0 or number > Decimal("1000000"):
        raise ValueError(f"{name} must be a bounded decimal cost string")


def _plain_numeric_mapping(name: str, value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise ValueError(f"{name} must be a bounded numeric-only mapping")
    result: dict[str, int | float] = {}
    for key in value:
        if key in _RESERVED_LANGFUSE_USAGE_KEYS:
            raise ValueError(f"{name} key is reserved for a normalized Langfuse usage bucket")
        if type(key) is not str or not _OTHER_USAGE_KEY_RE.fullmatch(key) or key in _FORBIDDEN_OTHER_KEYS:
            raise ValueError(f"{name} key is outside the safe allowlist")
        item = value[key]
        if type(item) not in {int, float} or not math.isfinite(item) or item < 0 or item > _MAX_USAGE:
            raise ValueError(f"{name} values must be bounded numeric values")
        result[key] = item
    return result


def _plain_metadata(value: Any) -> dict[str, str | int | float | bool]:
    if not isinstance(value, Mapping) or len(value) > len(ALLOWED_METADATA_KEYS):
        raise ValueError("metadata must be a bounded allowlisted mapping")
    result: dict[str, str | int | float | bool] = {}
    for key in value:
        if type(key) is not str or key not in ALLOWED_METADATA_KEYS:
            raise ValueError("metadata contains a key outside the allowlist")
        item = value[key]
        if type(item) is str:
            _validate_identifier(f"metadata.{key}", item)
        elif type(item) in {int, float}:
            if not math.isfinite(item) or abs(item) > _MAX_USAGE:
                raise ValueError(f"metadata.{key} must be bounded")
        elif type(item) is not bool:
            raise ValueError(f"metadata.{key} must be a plain compact scalar")
        result[key] = item
    return result


def _plain_event_snapshot(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping) or len(event) != len(_EVENT_KEYS):
        raise ValueError("event contains forbidden keys or is missing the exact bounded schema")
    snapshot: dict[str, Any] = {}
    for key in _EVENT_KEYS:
        try:
            value = event[key]
        except KeyError as exc:
            raise ValueError(f"event is missing required key {key}") from exc
        if key == "metadata":
            snapshot[key] = _plain_metadata(value)
        elif key == "other_usage_json":
            snapshot[key] = _plain_numeric_mapping(key, value)
        elif key in _USAGE_FIELDS and value is not None and type(value) is not int:
            raise ValueError(f"event.{key} usage must be a bounded integer or null")
        elif value is not None and type(value) not in {str, int}:
            raise ValueError(f"event.{key} must be a plain scalar")
        else:
            snapshot[key] = value
    return snapshot


def validate_event(event: Mapping[str, Any], *, trace_seed: str) -> None:
    """Fail closed unless an event exactly matches the approved Phase 0 schema."""
    item = _plain_event_snapshot(event)
    _canonical_uuid("event_id", item["event_id"])
    if type(item["occurred_at_utc"]) is not str or not _TIMESTAMP_RE.fullmatch(item["occurred_at_utc"]):
        raise ValueError("occurred_at_utc must be a bounded ISO-8601 UTC timestamp")
    try:
        datetime.fromisoformat(item["occurred_at_utc"].removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("occurred_at_utc must be a valid UTC timestamp") from exc
    if type(item["trace_id"]) is not str or not _TRACE_RE.fullmatch(item["trace_id"]):
        raise ValueError("trace_id must be a lowercase SHA-256 identifier")
    if item["trace_id"] != trace_id_from_seed(trace_seed):
        raise ValueError("trace_id does not match trusted trace provenance")
    observation = item["observation_id"]
    match = _OBSERVATION_RE.fullmatch(observation) if type(observation) is str else None
    if not match:
        raise ValueError("observation_id must identify a canonical attempt")

    for name, allowed in _CATEGORIES.items():
        if item[name] not in allowed:
            raise ValueError(f"{name} is outside the categorical allowlist")
    if type(item["attempt_number"]) is not int or not 1 <= item["attempt_number"] <= 10_000:
        raise ValueError("attempt_number must be a bounded positive integer")
    if int(match.group(1)) != item["attempt_number"]:
        raise ValueError("observation_id must match attempt_number")
    if item["latency_ms"] is not None and (
        type(item["latency_ms"]) is not int or not 0 <= item["latency_ms"] <= 604_800_000
    ):
        raise ValueError("latency_ms must be a bounded non-negative integer or null")
    if item["cost_currency"] != "USD":
        raise ValueError("cost_currency is outside the categorical allowlist")

    _validate_identifier("requested_model", item["requested_model"], pattern=_MODEL_RE)
    _validate_identifier("resolved_model", item["resolved_model"], pattern=_MODEL_RE)
    _validate_cost("provider_reported_cost_usd", item["provider_reported_cost_usd"])
    _validate_cost("calculated_cost_usd", item["calculated_cost_usd"])
    _validate_identifier("pricing_version", item["pricing_version"], optional=True)
    basis = item["cost_basis"]
    provider_cost = item["provider_reported_cost_usd"]
    calculated_cost = item["calculated_cost_usd"]
    pricing = item["pricing_version"]
    if basis == "provider_reported":
        valid_cost = provider_cost is not None and calculated_cost is None and pricing is None
    elif basis in {"price_snapshot", "local_policy", "estimated"}:
        valid_cost = provider_cost is None and calculated_cost is not None and pricing is not None
    else:
        valid_cost = provider_cost is None and calculated_cost is None and pricing is None
    if not valid_cost:
        raise ValueError("cost and pricing provenance is incomplete or contradictory")

    for name in _USAGE_FIELDS:
        value = item[name]
        if value is not None and (type(value) is not int or not 0 <= value <= _MAX_USAGE):
            raise ValueError(f"{name} usage must be a bounded non-negative integer or null")
    for name in _LINEAGE_FIELDS:
        _canonical_uuid(name, item[name], optional=True)
    seed_kind, seed_uuid = trace_seed.split(":", 1)
    if seed_kind == "queue" and item["queue_item_id"] != seed_uuid.lower():
        raise ValueError("queue_item_id contradicts trusted trace provenance")
    if seed_kind == "request" and item["queue_item_id"] is not None:
        raise ValueError("queue_item_id must be null for a request trace")
    if item["symbol_display_id"] is not None and (
        type(item["symbol_display_id"]) is not str or not _SYMBOL_DISPLAY_RE.fullmatch(item["symbol_display_id"])
    ):
        raise ValueError("symbol_display_id must be a human-readable symbol identifier or null")
    for name in ("feature", "prompt_version", "release"):
        _validate_identifier(name, item[name], optional=True)
    if item["initiator_pseudonym"] is not None and (
        type(item["initiator_pseudonym"]) is not str or not _TRACE_RE.fullmatch(item["initiator_pseudonym"])
    ):
        raise ValueError("initiator_pseudonym must be an HMAC-SHA256 pseudonym or null")
    for name in ("error_class", "error_code"):
        _validate_identifier(name, item[name], optional=True, pattern=_ERROR_RE)
    has_error = item["error_class"] is not None or item["error_code"] is not None
    if item["status"] == "succeeded" and has_error:
        raise ValueError("succeeded events cannot contain error fields")
    if item["status"] != "succeeded" and not has_error:
        raise ValueError("non-success events require a safe error class or code")

    if not _REQUIRED_METADATA_KEYS.issubset(item["metadata"]):
        raise ValueError("metadata is missing required provenance keys")
    provenance = {
        "environment": "environment", "service": "service_name", "agent": "agent_slug",
        "usecase": "use_case", "provider": "provider", "model": "resolved_model",
        "requestkind": "request_kind", "queueitemid": "queue_item_id",
        "agentrunid": "agent_run_id", "reviewcaseid": "review_case_id", "intakerecordid": "intake_record_id",
        "sourcepackageid": "source_package_id", "symbolid": "symbol_id", "symboldisplayid": "symbol_display_id",
        "feature": "feature", "promptversion": "prompt_version", "initiatorkind": "initiator_kind",
        "initiatorpseudonym": "initiator_pseudonym", "pricingversion": "pricing_version",
        "costbasis": "cost_basis", "release": "release",
    }
    for metadata_key, event_key in provenance.items():
        if metadata_key in item["metadata"]:
            expected = item[event_key] if item[event_key] is not None else "none"
            if item["metadata"][metadata_key] != expected:
                raise ValueError(f"metadata.{metadata_key} contradicts {event_key} provenance")


def build_llm_event(*, trace_seed: str, **fields: Any) -> dict[str, Any]:
    """Build and validate one normalized retry/fallback attempt."""
    event = dict(fields)
    event["trace_id"] = trace_id_from_seed(trace_seed)
    snapshot = _plain_event_snapshot(event)
    validate_event(snapshot, trace_seed=trace_seed)
    return snapshot


def _valid_endpoint(value: str | None) -> str | None:
    if type(value) is not str or not 1 <= len(value) <= 2048:
        return None
    if any(not 0x21 <= ord(character) <= 0x7E for character in value) or "\\" in value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return None
    if not hostname or parsed.netloc.endswith(":") or "%" in hostname or "_" in hostname:
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if (
            ":" in hostname
            or len(hostname) > 253
            or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
            or all(_NONCANONICAL_IP_LABEL_RE.fullmatch(label) for label in labels)
        ):
            return None
    normalized = value.rstrip("/")
    if normalized == _APPROVED_INTERNAL_INGESTION_ENDPOINT:
        return normalized
    if parsed.scheme == "http" and hostname not in {"127.0.0.1", "::1"}:
        return None
    return normalized


def _valid_credential(value: Any) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 512
        and all(0x21 <= ord(character) <= 0x7E and character != ":" for character in value)
    )


def _valid_timeout(value: Any) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.05 <= value <= 30.0
    )


@dataclass(frozen=True)
class TelemetryConfig:
    """Explicit configuration with credential fields excluded from repr."""
    enabled: bool = False
    endpoint: str | None = None
    public_key: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a bool")
        if not _valid_timeout(self.timeout_seconds):
            raise ValueError("timeout_seconds must be finite and between 0.05 and 30")
        endpoint_valid = _valid_endpoint(self.endpoint)
        if self.endpoint is not None and endpoint_valid is None:
            raise ValueError("endpoint configuration is invalid")
        if self.public_key is not None and not _valid_credential(self.public_key):
            raise ValueError("public key credential configuration is invalid")
        if self.secret_key is not None and not _valid_credential(self.secret_key):
            raise ValueError("secret key credential configuration is invalid")
        if self.enabled and (
            endpoint_valid is None
            or not _valid_credential(self.public_key)
            or not _valid_credential(self.secret_key)
        ):
            raise ValueError("enabled telemetry requires complete safe configuration")

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        endpoint = _valid_endpoint(os.getenv("SYMGOV_LLM_TELEMETRY_ENDPOINT"))
        public_key = os.getenv("SYMGOV_LLM_TELEMETRY_PUBLIC_KEY")
        secret_key = os.getenv("SYMGOV_LLM_TELEMETRY_SECRET_KEY")
        try:
            timeout = float(os.getenv("SYMGOV_LLM_TELEMETRY_TIMEOUT_SECONDS", "3"))
        except ValueError:
            timeout = 0.0
        complete = (
            os.getenv("SYMGOV_LLM_TELEMETRY_ENABLED") == "true"
            and endpoint is not None
            and _valid_credential(public_key)
            and _valid_credential(secret_key)
            and _valid_timeout(timeout)
        )
        if not complete:
            return cls()
        return cls(
            enabled=True, endpoint=endpoint, public_key=public_key,
            secret_key=secret_key, timeout_seconds=timeout,
        )


class LangfuseTransport:
    """Minimal secret-safe Langfuse ingestion transport."""
    __slots__ = ("_endpoint", "_authorization", "_timeout")

    def __init__(self, endpoint: str, public_key: str, secret_key: str, timeout_seconds: float) -> None:
        normalized = _valid_endpoint(endpoint)
        if normalized is None:
            raise ValueError("Langfuse endpoint configuration is invalid")
        if not _valid_credential(public_key) or not _valid_credential(secret_key):
            raise ValueError("Langfuse key credential configuration is invalid")
        if not _valid_timeout(timeout_seconds):
            raise ValueError("Langfuse timeout must be finite and between 0.05 and 30")
        self._endpoint = normalized
        credentials = f"{public_key}:{secret_key}".encode("utf-8")
        self._authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self._timeout = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"LangfuseTransport(endpoint={self._endpoint!r}, timeout_seconds={self._timeout!r})"

    @classmethod
    def from_config(cls, config: TelemetryConfig) -> "LangfuseTransport | None":
        if not config.enabled or config.endpoint is None or config.public_key is None or config.secret_key is None:
            return None
        return cls(config.endpoint, config.public_key, config.secret_key, config.timeout_seconds)

    def __call__(self, event: dict[str, Any]) -> None:
        timestamp = event["occurred_at_utc"]
        usage_names = {
            "input_tokens": "input", "output_tokens": "output",
            "cached_input_tokens": "cachedInput", "cache_write_input_tokens": "cacheWrite",
            "reasoning_tokens": "reasoning", "image_input_units": "inputImage",
            "image_output_units": "outputImage",
        }
        usage = {target: event[source] for source, target in usage_names.items() if event[source] is not None}
        usage.update(event["other_usage_json"])
        trace_body = {
            "id": event["trace_id"], "timestamp": timestamp,
            "name": event["use_case"], "metadata": event["metadata"],
        }
        generation_body = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event['trace_id']}:{event['observation_id']}")),
            "traceId": event["trace_id"],
            "name": f"{event['use_case']}-attempt-{event['attempt_number']}",
            "startTime": timestamp, "endTime": timestamp, "model": event["resolved_model"],
            "usageDetails": usage,
        }
        total = event["provider_reported_cost_usd"]
        if total is None:
            total = event["calculated_cost_usd"]
        if total is not None:
            generation_body["costDetails"] = {"total": float(total)}
        batch = [
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event['trace_id']}:{event['observation_id']}:trace-create")), "timestamp": timestamp, "type": "trace-create", "body": trace_body},
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event['trace_id']}:{event['observation_id']}:generation-create")), "timestamp": timestamp, "type": "generation-create", "body": generation_body},
        ]
        request = Request(
            self._endpoint, data=json.dumps({"batch": batch}, separators=(",", ":")).encode("utf-8"),
            method="POST", headers={"Authorization": self._authorization, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=self._timeout) as response:
            if getattr(response, "status", 0) not in {200, 201, 207}:
                raise RuntimeError("Langfuse ingestion returned a non-success status")
            response.read(4096)


class LLMTelemetry:
    """Bounded, sequential, non-fatal asynchronous telemetry adapter."""
    def __init__(self, *, config: TelemetryConfig | None = None, transport: Transport | None = None, lineage_capacity: int = 4096) -> None:
        if type(lineage_capacity) is not int or not 1 <= lineage_capacity <= 4096:
            raise ValueError("lineage_capacity must be a bounded integer from 1 to 4096")
        self.config = config or TelemetryConfig.from_env()
        self._transport = transport
        self._lineage_capacity = lineage_capacity
        self._queue: Queue[Any] = Queue(maxsize=128)
        self._lock = Lock()
        self._close_lock = Lock()
        self._worker: Thread | None = None
        self._next_attempt: OrderedDict[str, int] = OrderedDict()
        self._pending_by_trace: dict[str, int] = {}
        self._closed = False
        self._close_result: bool | None = None
        self._stop_enqueued = False

    @classmethod
    def from_env(cls) -> "LLMTelemetry":
        config = TelemetryConfig.from_env()
        return cls(config=config, transport=LangfuseTransport.from_config(config))

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self._transport is not None and not self._closed

    def record(self, event: Mapping[str, Any], *, trace_seed: str | None = None) -> bool:
        if not self.enabled or trace_seed is None:
            return False
        try:
            safe_event = _plain_event_snapshot(event)
            validate_event(safe_event, trace_seed=trace_seed)
            trace_id = safe_event["trace_id"]
            attempt = safe_event["attempt_number"]
        except Exception:
            return False
        try:
            with self._lock:
                if self._closed:
                    return False
                if trace_id not in self._next_attempt and len(self._next_attempt) >= self._lineage_capacity:
                    completed = next(
                        (old_trace for old_trace in self._next_attempt if self._pending_by_trace.get(old_trace, 0) == 0),
                        None,
                    )
                    if completed is None:
                        return False
                    del self._next_attempt[completed]
                    self._pending_by_trace.pop(completed, None)
                if attempt != self._next_attempt.get(trace_id, 1):
                    return False
                if self._worker is not None and not self._worker.is_alive():
                    self._worker = None
                if self._worker is None:
                    self._worker = Thread(target=self._dispatch, daemon=True)
                    self._worker.start()
                self._queue.put_nowait(safe_event)
                self._next_attempt[trace_id] = attempt + 1
                self._pending_by_trace[trace_id] = self._pending_by_trace.get(trace_id, 0) + 1
            return True
        except Exception:
            return False

    def _dispatch(self) -> None:
        worker = current_thread()
        try:
            while True:
                try:
                    item = self._queue.get()
                except Exception:
                    return
                try:
                    if item is _STOP:
                        return
                    try:
                        assert self._transport is not None
                        self._transport(item)
                    except Exception:
                        pass
                finally:
                    if item is not _STOP:
                        try:
                            trace_id = item["trace_id"]
                            with self._lock:
                                pending = self._pending_by_trace.get(trace_id, 0) - 1
                                if pending <= 0:
                                    self._pending_by_trace.pop(trace_id, None)
                                    if trace_id in self._next_attempt:
                                        self._next_attempt.move_to_end(trace_id)
                                else:
                                    self._pending_by_trace[trace_id] = pending
                        except Exception:
                            pass
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
                pass

    def flush(self, timeout: float = 1.0) -> bool:
        """Wait up to ``timeout`` seconds for queued exports; never raise."""
        try:
            if type(timeout) not in {int, float} or isinstance(timeout, bool) or not 0 <= timeout <= 30:
                return False
            deadline = time.monotonic() + timeout
            while self._queue.unfinished_tasks:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
            return True
        except Exception:
            return False

    def close(self, timeout: float = 1.0) -> bool:
        """Bounded flush and worker shutdown for short-lived processes."""
        try:
            if type(timeout) not in {int, float} or isinstance(timeout, bool) or not 0 <= timeout <= 30:
                return False
            deadline = time.monotonic() + timeout
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0:
                acquired = self._close_lock.acquire(blocking=False)
            else:
                acquired = self._close_lock.acquire(timeout=remaining)
            if not acquired:
                return False
            try:
                if self._close_result is True:
                    return True
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    state_acquired = self._lock.acquire(blocking=False)
                else:
                    state_acquired = self._lock.acquire(timeout=remaining)
                if not state_acquired:
                    return False
                try:
                    self._closed = True
                    worker = self._worker
                finally:
                    self._lock.release()
                if worker is None:
                    self._close_result = True
                    return True
                if not self._stop_enqueued:
                    remaining = max(0.0, deadline - time.monotonic())
                    if not self.flush(remaining):
                        return False
                    try:
                        self._queue.put_nowait(_STOP)
                    except Full:
                        return False
                    self._stop_enqueued = True
                remaining = max(0.0, deadline - time.monotonic())
                worker.join(remaining)
                if worker.is_alive():
                    return False
                self._close_result = True
                return True
            finally:
                self._close_lock.release()
        except Exception:
            return False
