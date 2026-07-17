from __future__ import annotations

import json
import re


from fastapi import HTTPException, Request

from .catalog_developer import contains_catalog_credentials

_DOCUMENTATION_ASSIGNMENT = re.compile(
    r"(?i)\bauthorization\s*:\s*bearer\s+(?:\*{3,}|<[^>\r\n]{1,64}>)|"
    r"\bapi[_ -]?key\s*=\s*<YOUR_API_KEY>"
)
_DOCUMENTATION_CONNECTION_URI = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s/:@]+:"
    r"(?:\*{3,}|<[^>\r\n]{1,64}>)@[^\s]+"
)


def _scrub_documentation_placeholders(value: str) -> str:
    scrubbed = _DOCUMENTATION_ASSIGNMENT.sub("DOCUMENTATION_PLACEHOLDER", value)
    uri_match = _DOCUMENTATION_CONNECTION_URI.search(scrubbed)
    if uri_match and uri_match.group(0) != scrubbed.strip():
        scrubbed = _DOCUMENTATION_CONNECTION_URI.sub("DOCUMENTATION_PLACEHOLDER", scrubbed)
    return scrubbed

MAX_SANDBOX_BODY_BYTES = 16_384
ALLOWED_OPERATIONS = {
    "capabilities": set(),
    "taxonomy": set(),
    "symbol_search": {"query", "limit"},
    "symbol_detail": {"symbolRef"},
    "contextual_search": {"query", "context", "limit"},
    "ed_query": {"message"},
}

_SYMBOLS = (
    {"displayId": "SANDBOX-FA-001", "name": "Synthetic Smoke Detector", "summary": "Synthetic fire alarm symbol.", "previewUrl": "/synthetic/previews/SANDBOX-FA-001", "downloadAvailable": False},
    {"displayId": "SANDBOX-FA-002", "name": "Synthetic Manual Call Point", "summary": "Synthetic fire alarm call point.", "previewUrl": "/synthetic/previews/SANDBOX-FA-002", "downloadAvailable": False},
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


async def read_sandbox_body(request: Request) -> dict:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SANDBOX_BODY_BYTES:
                raise HTTPException(status_code=400, detail="Request body is too large.")
        except ValueError:
            pass
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_SANDBOX_BODY_BYTES:
            raise HTTPException(status_code=400, detail="Request body is too large.")
        body.extend(chunk)
    try:
        value = json.loads(bytes(body), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return value


def _validate(body: dict) -> tuple[str, dict]:
    if set(body) - {"operation", "input"}:
        raise HTTPException(status_code=400, detail="Sandbox accepts operation and input fields only.")
    operation = body.get("operation")
    if not isinstance(operation, str) or operation not in ALLOWED_OPERATIONS:
        raise HTTPException(status_code=400, detail="Sandbox operation is not allowlisted.")
    input_data = body.get("input", {})
    if not isinstance(input_data, dict):
        raise HTTPException(status_code=400, detail="Sandbox input must be an object.")
    if set(input_data) - ALLOWED_OPERATIONS[operation]:
        raise HTTPException(status_code=400, detail="Sandbox input contains unsupported fields.")
    serialized = json.dumps(input_data, ensure_ascii=False, separators=(",", ":"))
    credential_input = dict(input_data)
    for name in ("query", "message"):
        if isinstance(credential_input.get(name), str):
            credential_input[name] = _scrub_documentation_placeholders(credential_input[name])
    if contains_catalog_credentials(credential_input):
        raise HTTPException(status_code=400, detail="Sandbox input must not contain credentials.")
    for name in ("query", "message"):
        value = input_data.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
            raise HTTPException(status_code=400, detail=f"{name} must contain 1 to 2000 characters.")
    limit = input_data.get("limit", 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be an integer from 1 to 100.")
    context = input_data.get("context")
    if context is not None and (not isinstance(context, dict) or len(serialized.encode("utf-8")) > 8192):
        raise HTTPException(status_code=400, detail="context must be a bounded object.")
    symbol_ref = input_data.get("symbolRef")
    if symbol_ref is not None and symbol_ref not in {item["displayId"] for item in _SYMBOLS}:
        raise HTTPException(status_code=400, detail="Sandbox symbolRef must identify a synthetic symbol.")
    return operation, input_data


def _result(operation: str, input_data: dict) -> dict:
    if operation == "capabilities":
        return {"syntheticDisplayId": "SANDBOX-CAPABILITIES", "supports": list(ALLOWED_OPERATIONS), "downloadAvailable": False}
    if operation == "taxonomy":
        return {"syntheticDisplayId": "SANDBOX-TAXONOMY", "disciplines": ["Fire & Life Safety"], "categories": ["Fire Alarm Devices"], "downloadAvailable": False}
    if operation == "symbol_detail":
        return next(item for item in _SYMBOLS if item["displayId"] == input_data["symbolRef"])
    if operation in {"symbol_search", "contextual_search"}:
        query = str(input_data.get("query", "")).lower()
        matches = [item for item in _SYMBOLS if not query or query in (item["name"] + " " + item["summary"]).lower()]
        return {"syntheticDisplayId": "SANDBOX-SEARCH", "items": matches[: input_data.get("limit", 20)], "nextCursor": None, "downloadAvailable": False}
    return {"syntheticDisplayId": "SANDBOX-ED", "answer": "Synthetic Ed recommends symbol search; no production request was made.", "citations": ["developer://guides/search"], "symbols": [_SYMBOLS[0]], "downloadAvailable": False}


def run_sandbox(body: dict) -> dict:
    operation, input_data = _validate(body)
    return {
        "sandbox": {"simulated": True, "deterministic": True, "readOnly": True, "syntheticData": True},
        "operation": operation,
        "mutatesRecords": False,
        "result": _result(operation, input_data),
    }
