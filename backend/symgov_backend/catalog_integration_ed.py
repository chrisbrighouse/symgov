from __future__ import annotations

import json
import re


from fastapi import HTTPException, Request

from .catalog_developer import GUIDES, contains_catalog_credentials

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

MAX_ED_BODY_BYTES = 16_384

_GUIDE_IDS = {guide["id"] for guide in GUIDES}

_TOPICS = (
    ("examples", ("example", "curl", "python", "javascript", "typescript", "c#", "csharp"), "Use a placeholder key in the Authorization bearer header and replace it only at runtime. Never persist the key."),
    ("authentication", ("auth", "scope", "api key", "bearer", "login"), "Developer Hub content requires both an existing Symgov login session and an active Catalog API key. Production operations require the exact documented scope; these credentials are independent and are not a user/customer association."),
    ("pagination", ("pagin", "cursor", "nextcursor"), "GET /api/v1/catalog/symbols uses cursor pagination. Request at most 100 items, then pass a non-null nextCursor as the next cursor value."),
    ("previews", ("preview", "thumbnail", "image"), "Symbol summaries and detail can expose thumbnail and preview routes when an asset exists. These are render-only; Catalog downloads are not available."),
    ("errors", ("error", "401", "403", "validation", "not found", "404"), "A 401 means a login or API-key credential is missing or invalid; 403 means the key lacks scope; 400/422 means bounded input or validation failed; 404 means the symbol was not found."),
    ("sandbox", ("sandbox", "simulate", "try it"), "The sandbox is an in-process deterministic simulator with synthetic IDs. It is read-only, allowlisted, makes no production mutation, and does not call external services."),
    ("feedback", ("feedback", "support", "issue", "review"), "Submit integration feedback with POST /api/v1/catalog/symbols/{symbol_ref}/feedback and catalog.feedback.write. For unresolved integration help, use /support."),
    ("search", ("search", "keyword", "contextual", "find symbol"), "Use GET /api/v1/catalog/symbols for keyword/facet queries and cursor pagination. Use POST /api/v1/catalog/search when application, drawing, layer, units, or format context should influence ranking."),
)


def _code_example(message: str) -> str:
    lowered = message.lower()
    if "python" in lowered:
        return 'requests.get(BASE + "/api/v1/catalog/symbols", headers={"Authorization": "Bearer <CATALOG_API_KEY>"}, params={"q": "smoke detector", "limit": 25})'
    if "c#" in lowered or "csharp" in lowered:
        return 'client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "<CATALOG_API_KEY>");\nawait client.GetAsync(baseUrl + "/api/v1/catalog/symbols?q=smoke%20detector");'
    if "javascript" in lowered or "typescript" in lowered:
        return 'fetch(`${baseUrl}/api/v1/catalog/symbols?q=smoke%20detector`, { headers: { Authorization: "Bearer <CATALOG_API_KEY>" } });'
    return 'curl -H "Authorization: Bearer <CATALOG_API_KEY>" "${BASE_URL}/api/v1/catalog/symbols?q=smoke%20detector&limit=25"'


async def read_integration_ed_body(request: Request) -> dict:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_ED_BODY_BYTES:
                raise HTTPException(status_code=400, detail="Request body is too large.")
        except ValueError:
            pass
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_ED_BODY_BYTES:
            raise HTTPException(status_code=400, detail="Request body is too large.")
        body.extend(chunk)
    try:
        value = json.loads(bytes(body))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return value


def answer_integration_question(body: dict) -> dict:
    if set(body) != {"message"}:
        raise HTTPException(status_code=400, detail="Integration Ed accepts a message only and is stateless.")
    message = body.get("message")
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 2000:
        raise HTTPException(status_code=400, detail="message must contain 1 to 2000 characters.")
    normalized = message.strip()
    if contains_catalog_credentials(_scrub_documentation_placeholders(normalized)):
        raise HTTPException(status_code=400, detail="Remove credentials before asking Integration Ed.")

    lowered = normalized.lower()
    topic = next((name for name, keywords, _ in _TOPICS if any(keyword in lowered for keyword in keywords)), None)
    if topic is None:
        return {
            "answer": "I cannot resolve that from the current Catalog developer documentation. Use /support for integration help.",
            "citations": ["developer://support"],
            "suggestedFollowups": ["Open /support", "Ask about authentication, search, pagination, previews, errors, sandbox, or feedback"],
            "code": None,
            "resolved": False,
            "supportRoute": "/support",
            "stateless": True,
            "conversationMemory": False,
            "standardsApproval": False,
            "networkCalls": False,
        }
    answer = next(text for name, _, text in _TOPICS if name == topic)
    citation_topic = topic if topic in _GUIDE_IDS else "quickstart"
    return {
        "answer": answer,
        "citations": [f"developer://guides/{citation_topic}"],
        "suggestedFollowups": ["Show the relevant endpoint", "Try the deterministic sandbox", "Open /support if this does not resolve the issue"],
        "code": _code_example(normalized) if topic == "examples" else None,
        "resolved": True,
        "supportRoute": "/support",
        "stateless": True,
        "conversationMemory": False,
        "standardsApproval": False,
        "networkCalls": False,
    }
