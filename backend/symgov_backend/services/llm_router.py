from __future__ import annotations

import json
import hashlib
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .llm_telemetry import is_safe_model_identifier


RouterFactory = Callable[..., Any]
_ALLOWED_PROVIDERS = {"openrouter", "google", "ollama"}
_FORBIDDEN_CALLBACK_KEYS = {"callbacks", "success_callback", "failure_callback"}
_PROVIDER_BY_MODEL_PREFIX = {
    "openrouter": "openrouter",
    "gemini": "google",
    "ollama": "ollama",
}
_MAX_PROVIDER_COST = Decimal("1000000")
_ROUTER_CACHE_CAPACITY = 32


def _read_env_var_from_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            return value.strip()
    return ""


def _profile_env_path() -> Path:
    profile = os.environ.get("SYMGOV_HERMES_PROFILE", "symgov").strip() or "symgov"
    return Path(f"/root/.hermes/profiles/{profile}/.env")


def provider_api_key(provider: str) -> str:
    if provider == "openrouter":
        keys = ("SYMGOV_OPENROUTER_API_KEY", "OPENROUTER_API_KEY")
    elif provider == "google":
        keys = ("SYMGOV_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    else:
        return ""
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    profile_env = _profile_env_path()
    for key in keys:
        value = _read_env_var_from_file(profile_env, key)
        if value:
            return value
    return ""


def _provider_model(provider: str, model: str) -> str:
    if provider == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    if provider == "google":
        return model if model.startswith("gemini/") else f"gemini/{model}"
    if provider == "ollama":
        return model if model.startswith("ollama/") else f"ollama/{model}"
    raise ValueError("Unsupported LLM provider.")


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _plain(model_dump())
    if hasattr(value, "items"):
        try:
            return {str(key): _plain(item) for key, item in value.items()}
        except (TypeError, ValueError):
            pass
    return str(value)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _output_images(message: dict[str, Any]) -> list[dict[str, Any]]:
    images = []
    for item in message.get("images") or []:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url") or {}
        if not isinstance(image_url, dict):
            continue
        url = str(image_url.get("url") or "").strip()
        if url:
            images.append({"url": url, "detail": image_url.get("detail")})
    return images


def _safe_error_code(exc: Exception) -> str | None:
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, (str, int)) and str(value).strip():
            code = str(value).strip()
            if len(code) <= 80 and all(character.isalnum() or character in "._-" for character in code):
                return code
    return None


def _first_not_none(*values: Any) -> Any | None:
    return next((value for value in values if value is not None), None)


def _image_input_count(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"image", "image_url", "input_image"}:
                count += 1
    return count


def _normalized_provider_cost(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not cost.is_finite() or cost < 0 or cost > _MAX_PROVIDER_COST:
        return None
    normalized = cost.normalize()
    exponent = normalized.as_tuple().exponent
    if type(exponent) is not int or exponent < -9:
        return None
    fixed = format(normalized, "f")
    if fixed == "-0":
        return "0"
    return fixed


def _has_retry_or_fallback_control(*mappings: dict[str, Any]) -> bool:
    return any(
        "retry" in str(key).lower()
        or "retri" in str(key).lower()
        or "fallback" in str(key).lower()
        for mapping in mappings
        for key in mapping
    )


class LiteLLMRouterService:
    """Provider-neutral LiteLLM Router boundary with native Symgov telemetry."""

    def __init__(self, *, router_factory: RouterFactory | None = None) -> None:
        self._router_factory = router_factory
        self._routers: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
        self._lock = threading.Lock()

    def _factory(self) -> RouterFactory:
        if self._router_factory is not None:
            return self._router_factory
        try:
            from litellm import Router
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("LiteLLM Router SDK is not installed.") from exc
        return Router

    def _model_list(self, *, model: str, provider: str) -> list[dict[str, Any]]:
        configured = os.environ.get("SYMGOV_LITELLM_MODEL_LIST", "").strip()
        if configured:
            try:
                rows = json.loads(configured)
            except json.JSONDecodeError as exc:
                raise RuntimeError("SYMGOV_LITELLM_MODEL_LIST must be valid JSON.") from exc
            if not isinstance(rows, list):
                raise RuntimeError("SYMGOV_LITELLM_MODEL_LIST must be a JSON list.")
            selected = [row for row in rows if isinstance(row, dict) and row.get("model_name") == model]
            if selected:
                deployments = []
                for row in selected:
                    params = row.get("litellm_params")
                    if not isinstance(params, dict) or not str(params.get("model") or "").strip():
                        raise RuntimeError("Each LiteLLM deployment requires litellm_params.model.")
                    deployment_model = str(params["model"]).strip()
                    deployment_prefix, separator, provider_model = deployment_model.partition("/")
                    if (
                        not separator
                        or not is_safe_model_identifier(deployment_model)
                        or not is_safe_model_identifier(provider_model)
                    ):
                        raise RuntimeError("LiteLLM deployment model must be a bounded provider model identifier.")
                    deployment_prefix = deployment_prefix.lower()
                    if _PROVIDER_BY_MODEL_PREFIX.get(deployment_prefix) != provider:
                        raise RuntimeError("Configured LiteLLM deployment provider does not match the request.")
                    if _FORBIDDEN_CALLBACK_KEYS.intersection(row) or _FORBIDDEN_CALLBACK_KEYS.intersection(params):
                        raise RuntimeError("LiteLLM callbacks are not allowed; Symgov native telemetry owns traces.")
                    if _has_retry_or_fallback_control(row, params):
                        raise RuntimeError("LiteLLM retry and fallback controls are not allowed.")
                    deployments.append(_plain(row))
                api_key = provider_api_key(provider)
                if api_key:
                    for deployment in deployments:
                        if not deployment["litellm_params"].get("api_key"):
                            deployment["litellm_params"]["api_key"] = api_key
                return deployments

        litellm_params: dict[str, Any] = {"model": _provider_model(provider, model)}
        api_key = provider_api_key(provider)
        if api_key:
            litellm_params["api_key"] = api_key
        return [{"model_name": model, "litellm_params": litellm_params}]

    def _router(self, *, model: str, provider: str) -> Any:
        model_list = self._model_list(model=model, provider=provider)
        fingerprint = hashlib.sha256(
            json.dumps(model_list, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_key = (provider, model, fingerprint)
        with self._lock:
            router = self._routers.pop(cache_key, None)
            if router is None:
                # Retries are deliberately disabled here so each native usage event
                # represents exactly one provider attempt. Deployment load balancing
                # remains available through SYMGOV_LITELLM_MODEL_LIST.
                router = self._factory()(model_list=model_list, num_retries=0)
            self._routers[cache_key] = router
            while len(self._routers) > _ROUTER_CACHE_CAPACITY:
                self._routers.popitem(last=False)
            return router

    def completion(
        self,
        *,
        model: str,
        provider: str,
        messages: list[dict[str, Any]],
        use_case: str,
        service_name: str,
        agent_slug: str | None = None,
        request_kind: str = "text",
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        modalities: list[str] | None = None,
        timeout: float | None = None,
        session_factory: Any | None = None,
        session_factory_provider: Callable[[], Any] | None = None,
        initiator_kind: str = "user",
        initiator_pseudonym: str | None = None,
        prompt_version: str | None = None,
        feature: str | None = None,
    ) -> dict[str, Any]:
        provider = str(provider).strip().lower()
        model = str(model).strip()
        if provider not in _ALLOWED_PROVIDERS:
            raise ValueError("Unsupported LLM provider.")
        if not is_safe_model_identifier(model):
            raise ValueError("LLM model must be a bounded provider model identifier.")

        call: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            call["temperature"] = temperature
        if max_tokens is not None:
            call["max_tokens"] = max_tokens
        if response_format is not None:
            call["response_format"] = response_format
        if modalities is not None:
            call["modalities"] = modalities
        if timeout is not None:
            call["timeout"] = timeout

        started_at = time.monotonic()
        occurred_at_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        request_seed = f"request:{uuid.uuid4()}"
        from .llm_telemetry import trace_id_from_seed

        trace_id = trace_id_from_seed(request_seed)
        observation_id = "attempt-1"
        status = "succeeded"
        error: Exception | None = None
        payload: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        hidden_cost: Any | None = None
        result: dict[str, Any] | None = None
        resolved_model = model

        try:
            raw_response = self._router(model=model, provider=provider).completion(**call)
            hidden_params = _plain(getattr(raw_response, "_hidden_params", {}))
            if isinstance(hidden_params, dict):
                hidden_cost = hidden_params.get("response_cost")
            payload = _plain(raw_response)
            if not isinstance(payload, dict):
                raise TypeError("LiteLLM Router returned an unsupported response.")
            usage_value = payload.get("usage") or {}
            usage = usage_value if isinstance(usage_value, dict) else {}
            choices = payload.get("choices") or []
            first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            message = first_choice.get("message") or {}
            message = message if isinstance(message, dict) else {}
            provider_resolved_model = str(payload.get("model") or "").strip()
            if is_safe_model_identifier(provider_resolved_model):
                resolved_model = provider_resolved_model
            result = {
                "provider": provider,
                "model": model,
                "resolvedModel": resolved_model,
                "outputText": _text_content(message.get("content")),
                "outputImages": _output_images(message),
                "usage": usage,
            }
        except Exception as exc:  # provider details are intentionally not propagated
            error = exc
            name = type(exc).__name__.lower()
            status = "timed_out" if "timeout" in name else "failed"
        finally:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if result is not None:
                result["latencyMs"] = elapsed_ms
            provider_cost = _first_not_none(usage.get("cost"), usage.get("total_cost"), hidden_cost)
            normalized_provider_cost = _normalized_provider_cost(provider_cost)
            cost_basis = "provider_reported" if normalized_provider_cost is not None else "unknown"
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            event_payload = {
                "event_id": str(uuid.uuid4()),
                "occurred_at_utc": occurred_at_utc,
                "environment": os.environ.get("SYMGOV_ENV", "development"),
                "trace_id": trace_id,
                "observation_id": observation_id,
                "use_case": use_case,
                "service_name": service_name,
                "agent_slug": agent_slug,
                "provider": provider,
                "requested_model": model,
                "resolved_model": resolved_model,
                "request_kind": request_kind,
                "attempt_number": 1,
                "status": status,
                "latency_ms": elapsed_ms,
                "cost_currency": "USD",
                "cost_basis": cost_basis,
                "provider_reported_cost_usd": normalized_provider_cost,
                "calculated_cost_usd": None,
                "pricing_version": None,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cached_input_tokens": prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None,
                "cache_write_input_tokens": None,
                "reasoning_tokens": completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None,
                "image_input_units": _image_input_count(messages),
                "image_output_units": len(result["outputImages"]) if result is not None else None,
                "other_usage_json": {},
                "queue_item_id": None,
                "agent_run_id": None,
                "review_case_id": None,
                "intake_record_id": None,
                "source_package_id": None,
                "symbol_id": None,
                "symbol_display_id": None,
                "feature": feature or use_case,
                "prompt_version": prompt_version,
                "release": None,
                "initiator_kind": initiator_kind,
                "initiator_pseudonym": initiator_pseudonym,
                "error_class": type(error).__name__ if error is not None else None,
                "error_code": _safe_error_code(error) if error is not None else None,
                "metadata": {
                    "environment": os.environ.get("SYMGOV_ENV", "development"),
                    "service": service_name,
                    "agent": agent_slug or "none",
                    "usecase": use_case,
                    "provider": provider,
                    "model": resolved_model,
                    "requestkind": request_kind,
                    "queueitemid": "none",
                    "initiatorkind": initiator_kind,
                    "costbasis": cost_basis,
                },
            }
            resolved_session_factory = session_factory
            if resolved_session_factory is None and session_factory_provider is not None:
                try:
                    resolved_session_factory = session_factory_provider()
                except Exception:
                    resolved_session_factory = None
            if resolved_session_factory is not None:
                try:
                    from .llm_usage_ledger import record_llm_usage_event_best_effort

                    record_llm_usage_event_best_effort(
                        resolved_session_factory,
                        event_payload,
                        trace_seed=request_seed,
                    )
                except Exception:
                    pass
            try:
                from .llm_telemetry import export_llm_event_best_effort

                export_llm_event_best_effort(event_payload, trace_seed=request_seed)
            except Exception:
                pass

        if error is not None:
            raise RuntimeError("LiteLLM Router request failed.") from None
        assert result is not None
        return result


_default_service = LiteLLMRouterService()


def request_llm_completion(**kwargs: Any) -> dict[str, Any]:
    return _default_service.completion(**kwargs)
