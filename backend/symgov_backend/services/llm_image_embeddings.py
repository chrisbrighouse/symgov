"""Image embeddings for visual symbol matching, through OpenRouter with no-retention routing.

Design D17/D18/D20: the model is config (default `google/gemini-embedding-2`), every request carries
`provider: {zdr: true, data_collection: deny, only: [...], allow_fallbacks: false}`, and a response
served by any provider outside the allow-list is a failure. One image per request: multi-image
batches lose the Vertex route and are refused by `only`.

Same posture as `llm_embeddings`: one fixed HTTPS URL, no redirect, no proxy, no retry (the caller
decides; the query path falls back to the local descriptor, D19), bounded response, usage telemetry
with no image content in it. The returned vector is the leading `dimensions` values re-normalised to
unit length (the Gemini embedding is a prefix-truncatable one), so a dot product is a cosine.
"""
from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable

import httpx2

from .llm import openrouter_headers
from .llm_embeddings import (
    EMBEDDINGS_URL,
    MAX_DIMENSIONS,
    EmbeddingError,
    _MAX_RESPONSE_BYTES,
    _normalised,
    _record_event,
)
from .llm_router import provider_api_key
from .llm_telemetry import is_safe_model_identifier

DEFAULT_MODEL = "google/gemini-embedding-2"
DEFAULT_DIMENSIONS = 768
DEFAULT_PROVIDERS = ("google-vertex",)
FEATURE = "visual_match"
MAX_IMAGE_BYTES = 4_000_000


def routing(providers: Sequence[str]) -> dict[str, Any]:
    return {"zdr": True, "data_collection": "deny", "only": list(providers), "allow_fallbacks": False}


def request_image_embedding(
    *,
    png: bytes,
    model: str = DEFAULT_MODEL,
    dimensions: int = DEFAULT_DIMENSIONS,
    providers: Sequence[str] = DEFAULT_PROVIDERS,
    feature: str = FEATURE,
    service_name: str = "symgov-api",
    initiator_kind: str = "system",
    initiator_pseudonym: str | None = None,
    timeout: float = 10.0,
    session_factory_provider: Callable[[], Any] | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    """One unit-length vector for one PNG. Raises `EmbeddingError` with no provider detail."""
    model = str(model).strip()
    if not is_safe_model_identifier(model):
        raise ValueError("The embedding model must be a bounded provider model identifier.")
    if not 1 <= dimensions <= MAX_DIMENSIONS:
        raise ValueError("Unsupported embedding dimensions.")
    allowed = [str(p).strip().lower() for p in providers if str(p).strip()]
    if not allowed:
        raise ValueError("At least one approved provider is required.")
    if not png or len(png) > MAX_IMAGE_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The image must be a PNG of at most 4 MB.")
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
    body = {
        "model": model,
        "input": [{"content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}],
        "provider": routing(allowed),
    }
    try:
        with httpx2.Client(
            transport=transport, verify=True, trust_env=False, follow_redirects=False, timeout=timeout
        ) as client:
            response = client.post(EMBEDDINGS_URL, headers=openrouter_headers(api_key), json=body)
        if response.status_code != 200:
            error = EmbeddingError("The embedding provider refused the request.")
            error.status_code = response.status_code  # type: ignore[attr-defined]
            raise error
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise EmbeddingError("The embedding response was too large.")
        payload = response.json()
        served_by = str(payload.get("provider") or "").strip().lower() if isinstance(payload, dict) else ""
        if not any(served_by == p or served_by.startswith(p + "/") for p in allowed):
            raise EmbeddingError("The embedding was served by a provider that is not approved.")
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise EmbeddingError("The embedding response did not match the request.")
        values = rows[0].get("embedding")
        if not isinstance(values, list) or len(values) < dimensions:
            raise EmbeddingError("The embedding response held an unusable vector.")
        vector = _normalised(values[:dimensions])
        usage_value = payload.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        reported = str(payload.get("model") or "").strip()
        if is_safe_model_identifier(reported):
            resolved_model = reported
        result = {"model": model, "resolvedModel": resolved_model, "dimensions": dimensions, "vector": vector, "usage": usage}
    except Exception as exc:
        error = exc
        status = "timed_out" if "timeout" in type(exc).__name__.lower() else "failed"
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _record_event(
            model=model, resolved_model=resolved_model, status=status, elapsed_ms=elapsed_ms, usage=usage, error=error,
            seed=seed, feature=feature, initiator_kind=initiator_kind, initiator_pseudonym=initiator_pseudonym,
            service_name=service_name, session_factory_provider=session_factory_provider, occurred_at=occurred_at,
        )
        if result is not None:
            result["latencyMs"] = elapsed_ms
    if error is not None:
        raise EmbeddingError("The embedding request failed.") from None
    assert result is not None
    return result
