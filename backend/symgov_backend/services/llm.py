from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request


DEFAULT_LLM_SETTINGS_PATH = Path("/data/.openclaw/workspace/symgov/symgov-llm-settings.json")
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def llm_settings_path() -> Path:
    configured = os.environ.get("SYMGOV_LLM_SETTINGS_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_LLM_SETTINGS_PATH


def _read_env_var_from_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, value = line.split("=", 1)
        if k.strip() == key:
            return value.strip()
    return ""


def openrouter_api_key() -> str:
    direct = os.environ.get("SYMGOV_OPENROUTER_API_KEY", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
    if direct:
        return direct

    profile = os.environ.get("SYMGOV_HERMES_PROFILE", "symgov").strip() or "symgov"
    profile_env = Path(f"/root/.hermes/profiles/{profile}/.env")
    return _read_env_var_from_file(profile_env, "SYMGOV_OPENROUTER_API_KEY") or _read_env_var_from_file(profile_env, "OPENROUTER_API_KEY")


def hermes_profile_config_path() -> Path:
    configured = os.environ.get("SYMGOV_HERMES_PROFILE_CONFIG", "").strip()
    if configured:
        return Path(configured)
    profile = os.environ.get("SYMGOV_HERMES_PROFILE", "symgov").strip() or "symgov"
    return Path(f"/root/.hermes/profiles/{profile}/config.yaml")


def configured_openrouter_models_from_profile() -> list[str]:
    path = hermes_profile_config_path()
    if not path.exists():
        return []

    models: list[str] = []
    provider: str | None = None
    current_fallback_provider: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        provider_match = re.match(r"^provider:\s*(.+)$", line)
        if provider_match:
            value = provider_match.group(1).strip().strip('"\'')
            provider = value.lower()
            current_fallback_provider = value.lower()
            continue

        default_match = re.match(r"^default:\s*(.+)$", line)
        if default_match and provider == "openrouter":
            value = default_match.group(1).strip().strip('"\'')
            if value:
                models.append(value)
            continue

        fallback_model_match = re.match(r"^model:\s*(.+)$", line)
        if fallback_model_match and current_fallback_provider == "openrouter":
            value = fallback_model_match.group(1).strip().strip('"\'')
            if value:
                models.append(value)

    deduped = sorted({model for model in models if model})
    return deduped


def default_llm_settings() -> dict[str, Any]:
    return {
        "provider": "openrouter",
        "defaultModel": DEFAULT_OPENROUTER_MODEL,
        "featureModels": {},
        "updatedAt": None,
    }


def normalize_llm_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_llm_settings()
    candidate = payload if isinstance(payload, dict) else {}

    provider = str(candidate.get("provider") or defaults["provider"]).strip().lower()
    if provider != "openrouter":
        provider = "openrouter"

    default_model = str(candidate.get("defaultModel") or defaults["defaultModel"]).strip()
    if not default_model:
        default_model = defaults["defaultModel"]

    raw_feature_models = candidate.get("featureModels")
    feature_models: dict[str, str] = {}
    if isinstance(raw_feature_models, dict):
        for key, value in raw_feature_models.items():
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if normalized_key and normalized_value:
                feature_models[normalized_key] = normalized_value

    updated_at = str(candidate.get("updatedAt") or "").strip() or None

    return {
        "provider": provider,
        "defaultModel": default_model,
        "featureModels": feature_models,
        "updatedAt": updated_at,
    }


def load_llm_settings() -> dict[str, Any]:
    path = llm_settings_path()
    if not path.exists():
        return default_llm_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_llm_settings()
    return normalize_llm_settings(payload)


def save_llm_settings(settings_payload: dict[str, Any]) -> dict[str, Any]:
    path = llm_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_llm_settings(settings_payload)
    normalized["updatedAt"] = utc_now_iso()
    path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return normalized


def resolve_model_for_feature(feature: str | None = None) -> str:
    settings = load_llm_settings()
    if feature:
        feature_key = str(feature).strip()
        if feature_key and feature_key in settings["featureModels"]:
            return str(settings["featureModels"][feature_key]).strip() or settings["defaultModel"]
    return settings["defaultModel"]


def openrouter_headers(api_key: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Title": "Symgov",
    }
    referer = os.environ.get("SYMGOV_PUBLIC_BASE_URL", "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    return headers


def _parse_openrouter_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def request_openrouter_completion(
    *,
    prompt: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 700,
) -> dict[str, Any]:
    api_key = openrouter_api_key()
    if not api_key:
        raise RuntimeError("OpenRouter API key is not configured on the server.")

    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": "You are Symgov's assistant. Be concise, practical, and safe for engineering governance workflows.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    url = f"{DEFAULT_OPENROUTER_BASE_URL}/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=openrouter_headers(api_key),
    )

    started_at = time.time()
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"OpenRouter request failed ({exc.code}): {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc.reason}") from exc

    choices = payload.get("choices") or []
    first_choice = choices[0] if choices else {}
    content = _parse_openrouter_text((first_choice.get("message") or {}).get("content"))

    elapsed_ms = int((time.time() - started_at) * 1000)
    return {
        "provider": "openrouter",
        "model": model,
        "outputText": content,
        "latencyMs": elapsed_ms,
        "usage": payload.get("usage") or {},
    }


def fetch_openrouter_models() -> list[dict[str, Any]]:
    api_key = openrouter_api_key()
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(
        f"{DEFAULT_OPENROUTER_BASE_URL}/models",
        method="GET",
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"Could not load OpenRouter models ({exc.code}): {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not load OpenRouter models: {exc.reason}") from exc

    rows = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        pricing = row.get("pricing") or {}
        rows.append(
            {
                "id": str(row.get("id") or "").strip(),
                "name": str(row.get("name") or row.get("id") or "").strip(),
                "contextLength": int(row.get("context_length") or 0),
                "promptPricePerToken": str(pricing.get("prompt") or ""),
                "completionPricePerToken": str(pricing.get("completion") or ""),
                "description": str(row.get("description") or "").strip(),
            }
        )

    rows = [row for row in rows if row["id"]]
    rows.sort(key=lambda item: item["id"])
    return rows
