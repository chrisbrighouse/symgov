"""Embedding calls, with the same native usage telemetry as `llm_router`.

`llm_router` is chat-shaped (messages in, a message out), and LiteLLM has no
OpenRouter embeddings route, so this calls OpenRouter's embeddings endpoint
directly: one fixed HTTPS URL, no redirect, no proxy, no retry, a bounded
batch and a bounded response. Each call records one usage event with
use_case `catalog_embedding` and request_kind `embedding`, and carries no
input text in it.

Vectors come back unit length, so a dot product is a cosine.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable

import httpx2

from .llm import openrouter_headers
from .llm_router import provider_api_key
from .llm_telemetry import is_safe_model_identifier, trace_id_from_seed


EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
MAX_BATCH = 64
MAX_TEXT_CHARS = 8_000
MAX_DIMENSIONS = 8_192
_MAX_RESPONSE_BYTES = 16_000_000
USE_CASE = "catalog_embedding"


class EmbeddingError(RuntimeError):
    """A failed embedding call. The message never carries provider detail."""


def _safe_error_code(error: Exception) -> str | None:
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, (str, int)) and str(value).strip():
            code = str(value).strip()
            if len(code) <= 80 and all(character.isalnum() or character in "._-" for character in code):
                # The telemetry validator wants an identifier that starts with a
                # letter, so an HTTP status is recorded as "http_500".
                return code if code[0].isalpha() else f"http_{code}"
    return None


def _normalised(values: Sequence[Any]) -> list[float]:
    floats = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise EmbeddingError("The embedding response held a non-numeric value.")
        floats.append(float(value))
    length = math.sqrt(sum(value * value for value in floats))
    if length == 0.0:
        raise EmbeddingError("The embedding response held a zero vector.")
    return [value / length for value in floats]


def _record_event(
    *,
    model: str,
    resolved_model: str,
    status: str,
    elapsed_ms: int,
    usage: dict[str, Any],
    error: Exception | None,
    seed: str,
    feature: str,
    initiator_kind: str,
    initiator_pseudonym: str | None,
    service_name: str,
    session_factory_provider: Callable[[], Any] | None,
    occurred_at: str,
) -> None:
    cost = usage.get("cost") if isinstance(usage, dict) else None
    try:
        from .llm_router import _normalized_provider_cost

        provider_cost = _normalized_provider_cost(cost)
    except Exception:
        provider_cost = None
    cost_basis = "provider_reported" if provider_cost is not None else "unknown"
    environment = os.environ.get("SYMGOV_ENV", "development")
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    payload = {
        "event_id": str(uuid.uuid4()),
        "occurred_at_utc": occurred_at,
        "environment": environment,
        "trace_id": trace_id_from_seed(seed),
        "observation_id": "attempt-1",
        "use_case": USE_CASE,
        "service_name": service_name,
        "agent_slug": None,
        "provider": "openrouter",
        "requested_model": model,
        "resolved_model": resolved_model,
        "request_kind": "embedding",
        "attempt_number": 1,
        "status": status,
        "latency_ms": elapsed_ms,
        "cost_currency": "USD",
        "cost_basis": cost_basis,
        "provider_reported_cost_usd": provider_cost,
        "calculated_cost_usd": None,
        "pricing_version": None,
        "input_tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "cache_write_input_tokens": None,
        "reasoning_tokens": None,
        "image_input_units": None,
        "image_output_units": None,
        "other_usage_json": {},
        "queue_item_id": None,
        "agent_run_id": None,
        "review_case_id": None,
        "intake_record_id": None,
        "source_package_id": None,
        "symbol_id": None,
        "symbol_display_id": None,
        "feature": feature,
        "prompt_version": None,
        "release": None,
        "initiator_kind": initiator_kind,
        "initiator_pseudonym": initiator_pseudonym,
        "error_class": type(error).__name__ if error is not None else None,
        "error_code": _safe_error_code(error) if error is not None else None,
        "metadata": {
            "environment": environment,
            "service": service_name,
            "agent": "none",
            "usecase": USE_CASE,
            "provider": "openrouter",
            "model": resolved_model,
            "requestkind": "embedding",
            "queueitemid": "none",
            "initiatorkind": initiator_kind,
            "costbasis": cost_basis,
        },
    }
    if session_factory_provider is not None:
        try:
            from .llm_usage_ledger import record_llm_usage_event_best_effort

            record_llm_usage_event_best_effort(session_factory_provider(), payload, trace_seed=seed)
        except Exception:
            pass
    try:
        from .llm_telemetry import export_llm_event_best_effort

        export_llm_event_best_effort(payload, trace_seed=seed)
    except Exception:
        pass


def request_llm_embeddings(
    *,
    texts: Sequence[str],
    model: str,
    feature: str = USE_CASE,
    service_name: str = "symgov-api",
    initiator_kind: str = "system",
    initiator_pseudonym: str | None = None,
    timeout: float = 30.0,
    session_factory_provider: Callable[[], Any] | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    """One unit-length vector per text, in order.

    Returns `{"model", "resolvedModel", "dimensions", "vectors", "usage",
    "latencyMs"}`. Raises `EmbeddingError` with no provider detail.
    """
    model = str(model).strip()
    if not is_safe_model_identifier(model):
        raise ValueError("The embedding model must be a bounded provider model identifier.")
    items = [str(text) for text in texts]
    if not items or len(items) > MAX_BATCH:
        raise ValueError(f"Send between 1 and {MAX_BATCH} texts per embedding call.")
    if any(not text.strip() or len(text) > MAX_TEXT_CHARS for text in items):
        raise ValueError(f"Each text must be non-empty and at most {MAX_TEXT_CHARS} characters.")
    api_key = provider_api_key("openrouter")
    if not api_key:
        raise EmbeddingError("No OpenRouter API key is configured.")

    started = time.monotonic()
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    seed = f"request:{uuid.uuid4()}"
    status = "succeeded"
    error: Exception | None = None
    usage: dict[str, Any] = {}
    resolved_model = model
    result: dict[str, Any] | None = None
    try:
        with httpx2.Client(
            transport=transport, verify=True, trust_env=False, follow_redirects=False, timeout=timeout
        ) as client:
            response = client.post(
                EMBEDDINGS_URL,
                headers=openrouter_headers(api_key),
                json={"model": model, "input": items, "encoding_format": "float"},
            )
        if response.status_code != 200:
            error = EmbeddingError("The embedding provider refused the request.")
            error.status_code = response.status_code  # type: ignore[attr-defined]
            raise error
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise EmbeddingError("The embedding response was too large.")
        body = response.json()
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list) or len(rows) != len(items):
            raise EmbeddingError("The embedding response did not match the request.")
        ordered = sorted(rows, key=lambda row: row.get("index", 0) if isinstance(row, dict) else 0)
        vectors = []
        for row in ordered:
            values = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(values, list) or not 1 <= len(values) <= MAX_DIMENSIONS:
                raise EmbeddingError("The embedding response held an unusable vector.")
            vectors.append(_normalised(values))
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise EmbeddingError("The embedding response mixed vector sizes.")
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        reported = str(body.get("model") or "").strip()
        if is_safe_model_identifier(reported):
            resolved_model = reported
        result = {
            "model": model,
            "resolvedModel": resolved_model,
            "dimensions": dimensions,
            "vectors": vectors,
            "usage": usage,
        }
    except Exception as exc:
        error = exc
        status = "timed_out" if "timeout" in type(exc).__name__.lower() else "failed"
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _record_event(
            model=model,
            resolved_model=resolved_model,
            status=status,
            elapsed_ms=elapsed_ms,
            usage=usage,
            error=error,
            seed=seed,
            feature=feature,
            initiator_kind=initiator_kind,
            initiator_pseudonym=initiator_pseudonym,
            service_name=service_name,
            session_factory_provider=session_factory_provider,
            occurred_at=occurred_at,
        )
        if result is not None:
            result["latencyMs"] = elapsed_ms
    if error is not None:
        raise EmbeddingError("The embedding request failed.") from None
    assert result is not None
    return result
