from __future__ import annotations

import re


_PLACEHOLDER = re.compile(
    r"(?i)(?:<[^>\r\n]{1,64}>|\*{3,}|\[(?:redacted|placeholder)\]|"
    r"\b(?:your|example|sample|dummy|fake)[_-]?(?:api[_-]?)?(?:key|token|secret|password)\b)"
)
_CREDENTIAL_KEY = re.compile(
    r"(?i)^(?:authorization|credentials?|api[_ -]?key|access[_ -]?key(?:[_ -]?id)?|"
    r"pass(?:word|wd)?|(?:access|refresh|id)?[_ -]?token|secret|client[_ -]?secret|private[_ -]?key)$"
)
_CREDENTIAL_TEXT = re.compile(
    r"(?ix)(?:"
    r"\bauthorization\s*:\s*(?:bearer|basic)\s+[a-z0-9._~+/-]{6,}|"
    r"\bbearer\s+[a-z0-9._~+/-]{12,}|"
    r"\b(?:api[_ -]?key|access[_ -]?key(?:[_ -]?id)?|pass(?:word|wd)?|"
    r"(?:access|refresh|id)?[_ -]?token|secret|client[_ -]?secret)\s*[=:]\s*[^\s,;&}\]]{4,}|"
    r"[\"'](?:authorization|credentials?|api[_ -]?key|access[_ -]?key(?:[_ -]?id)?|"
    r"pass(?:word|wd)?|(?:access|refresh|id)?[_ -]?token|secret|client[_ -]?secret|private[_ -]?key)"
    r"[\"']\s*:\s*[\"'][^\"']{4,}[\"']|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{4,}\b|"
    r"\bgh[pousr]_[a-z0-9]{20,}\b|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\bsymgov_(?:live|test)_[a-z0-9_-]{12,}\b|"
    r"\bsk-(?:proj-)?[a-z0-9_-]{16,}\b|"
    r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])|"
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s/:@]+:[^\s/@]+@[^\s]+"
    r")"
)


def _is_documentation_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_PLACEHOLDER.fullmatch(stripped)) or stripped == "AKIAIOSFODNN7EXAMPLE"


def contains_catalog_credentials(value: object) -> bool:
    """Detect real-looking credentials without rejecting explicit documentation placeholders."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _CREDENTIAL_KEY.fullmatch(str(key)) and not _is_documentation_placeholder(item):
                return True
            if contains_catalog_credentials(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(contains_catalog_credentials(item) for item in value)
    if not isinstance(value, str):
        return False
    scrubbed = _PLACEHOLDER.sub("DOCUMENTATION_PLACEHOLDER", value)
    scrubbed = scrubbed.replace("AKIAIOSFODNN7EXAMPLE", "AWS_DOCUMENTATION_PLACEHOLDER")
    return bool(_CREDENTIAL_TEXT.search(scrubbed))


def redact_catalog_credential_label(value: str) -> str:
    """Defensively hide credential material from legacy label surfaces."""
    return "[REDACTED]" if contains_catalog_credentials(value) else value

CATALOG_INTEGRATION_ENDPOINTS = (
    {"method": "GET", "path": "/api/v1/catalog/capabilities", "scope": "catalog.read", "summary": "Discover current Catalog capabilities."},
    {"method": "GET", "path": "/api/v1/catalog/taxonomy", "scope": "catalog.read", "summary": "Read canonical Catalog facets."},
    {"method": "GET", "path": "/api/v1/catalog/symbols", "scope": "catalog.read", "summary": "Search published symbols with cursor pagination."},
    {"method": "POST", "path": "/api/v1/catalog/search", "scope": "catalog.read", "summary": "Run a contextual symbol search."},
    {"method": "GET", "path": "/api/v1/catalog/symbols/{symbol_ref}", "scope": "catalog.read", "summary": "Read one published symbol."},
    {"method": "GET", "path": "/api/v1/catalog/symbols/{symbol_ref}/thumbnail", "scope": "catalog.read", "summary": "Render the available thumbnail."},
    {"method": "GET", "path": "/api/v1/catalog/symbols/{symbol_ref}/preview", "scope": "catalog.read", "summary": "Render the available preview."},
    {"method": "POST", "path": "/api/v1/catalog/ed/query", "scope": "catalog.ed.query", "summary": "Ask production Catalog Ed a symbol question."},
    {"method": "POST", "path": "/api/v1/catalog/symbols/{symbol_ref}/feedback", "scope": "catalog.feedback.write", "summary": "Submit integration feedback."},
)

GUIDES = (
    {"id": "quickstart", "title": "Five-minute quickstart"},
    {"id": "authentication", "title": "Authentication and key handling"},
    {"id": "search", "title": "Choose keyword or contextual search"},
    {"id": "pagination", "title": "Cursor pagination"},
    {"id": "previews", "title": "Thumbnails and previews"},
    {"id": "errors", "title": "Errors and troubleshooting"},
    {"id": "sandbox", "title": "Safe deterministic sandbox"},
    {"id": "feedback", "title": "Feedback and support"},
    {"id": "examples", "title": "Language examples"},
)

SCOPES = (
    {"name": "catalog.read", "description": "Read metadata, search, detail, and previews."},
    {"name": "catalog.ed.query", "description": "Ask production Catalog Ed questions."},
    {"name": "catalog.feedback.write", "description": "Submit Catalog integration feedback."},
)

_RESPONSE_SCHEMA_BY_ENDPOINT = {
    ("GET", "/api/v1/catalog/capabilities"): "CapabilitiesResponse",
    ("GET", "/api/v1/catalog/taxonomy"): "TaxonomyResponse",
    ("GET", "/api/v1/catalog/symbols"): "SymbolSearchResponse",
    ("POST", "/api/v1/catalog/search"): "ContextualSearchResponse",
    ("GET", "/api/v1/catalog/symbols/{symbol_ref}"): "SymbolDetailResponse",
    ("POST", "/api/v1/catalog/ed/query"): "EdQueryResponse",
    ("POST", "/api/v1/catalog/symbols/{symbol_ref}/feedback"): "FeedbackResponse",
}


def developer_manifest() -> dict:
    return {
        "title": "Catalog Integrator Hub",
        "version": "v1",
        "security": {
            "requiresLoginSession": True,
            "requiresCatalogApiKey": True,
            "authorizationBoundary": "The login and API key are independent credentials; there is no user/customer association in this milestone.",
            "keyHandling": "Keep the API key in component memory only; never persist it in local or session storage.",
        },
        "guides": list(GUIDES),
        "scopes": list(SCOPES),
        "endpoints": list(CATALOG_INTEGRATION_ENDPOINTS),
        "sandbox": {"available": True, "deterministic": True, "readOnly": True, "syntheticData": True},
        "support": {"route": "/support", "submission": "local experience"},
        "downloadAvailable": False,
        "noDownloadNotice": "Production Catalog downloads are not available in this milestone.",
    }


def _operation(endpoint: dict) -> dict:
    scope = endpoint["scope"]
    response_schema = _RESPONSE_SCHEMA_BY_ENDPOINT.get((endpoint["method"], endpoint["path"]))
    operation = {
        "summary": endpoint["summary"],
        "description": endpoint["summary"] + " Production Catalog downloads are not available.",
        "security": [{"CatalogApiKey": [scope]}],
        "x-required-scope": scope,
        "responses": {
            "200": {
                "description": "Successful Catalog response.",
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{response_schema}"}}},
            },
            "400": {"$ref": "#/components/responses/ValidationError"},
            "401": {"$ref": "#/components/responses/AuthenticationError"},
            "403": {"$ref": "#/components/responses/ScopeError"},
            "404": {"$ref": "#/components/responses/NotFoundError"},
        },
    }
    path = endpoint["path"]
    parameters = []
    if "{symbol_ref}" in path:
        parameters.append({
            "name": "symbol_ref", "in": "path", "required": True,
            "schema": {"type": "string", "maxLength": 256}, "example": "0003-12",
        })
    if path == "/api/v1/catalog/symbols" and endpoint["method"] == "GET":
        string_filter_names = (
            "q", "discipline", "category", "useCase", "format", "pack",
            "symbolFamily", "updatedSince",
        )
        parameters.extend([
            *[
                {"name": name, "in": "query", "schema": {"type": "string", "maxLength": 256}}
                for name in string_filter_names
            ],
            {"name": "hasPreview", "in": "query", "schema": {"type": "boolean"}},
            {
                "name": "limit", "in": "query",
                "description": "Values above 100 are accepted and capped at 100.",
                "schema": {"type": "integer", "minimum": 1, "default": 25},
            },
            {"name": "cursor", "in": "query", "schema": {"type": "string"}},
            {
                "name": "include", "in": "query",
                "description": "Comma-separated values: taxonomy, preview, evidence, facets.",
                "schema": {"type": "string", "example": "taxonomy,preview"},
            },
        ])
        operation["responses"]["422"] = {"$ref": "#/components/responses/QueryValidationError"}
    if path.endswith(("/thumbnail", "/preview")):
        binary_schema = {"schema": {"type": "string", "format": "binary"}}
        operation["responses"]["200"] = {
            "description": "Authenticated preview bytes in the stored asset media type.",
            "content": {
                media_type: dict(binary_schema)
                for media_type in (
                    "image/svg+xml", "image/png", "image/jpeg", "application/pdf", "application/octet-stream"
                )
            },
        }
    if path.endswith("/feedback") and endpoint["method"] == "POST":
        operation["responses"]["201"] = operation["responses"].pop("200")
        operation["responses"]["201"]["description"] = "Feedback recorded."
    if parameters:
        operation["parameters"] = parameters
    if endpoint["method"] == "POST":
        schema_name = (
            "ContextualSearchRequest" if path == "/api/v1/catalog/search"
            else "EdQueryRequest" if path == "/api/v1/catalog/ed/query"
            else "FeedbackRequest"
        )
        example_name = schema_name.removesuffix("Request")
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"},
                "example": EXAMPLES[example_name]["value"],
            }},
        }
    return operation


EXAMPLES = {
    "ContextualSearch": {"summary": "Contextual search", "value": {"query": "smoke detector", "context": {"application": "AutoCAD"}, "limit": 20}},
    "EdQuery": {"summary": "Catalog question", "value": {"message": "Find smoke detector symbols", "mode": "auto", "context": {}, "limit": 10}},
    "Feedback": {"summary": "Integration feedback", "value": {"kind": "comment", "message": "Preview is clear.", "context": {}}},
}


def catalog_openapi_document() -> dict:
    paths: dict[str, dict] = {}
    for endpoint in CATALOG_INTEGRATION_ENDPOINTS:
        paths.setdefault(endpoint["path"], {})[endpoint["method"].lower()] = _operation(endpoint)
    error_schema = {
        "type": "object", "required": ["error", "detail"], "additionalProperties": False,
        "properties": {
            "error": {"type": "string"},
            "detail": {"type": "string"},
        },
    }
    query_validation_error_schema = {
        "type": "object", "required": ["error", "detail", "issues"], "additionalProperties": False,
        "properties": {
            "error": {"type": "string"},
            "detail": {"type": "string"},
            "issues": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            }
        },
    }
    object_schema = {"type": "object", "additionalProperties": True}
    response_schemas = {
        "CapabilitiesResponse": {
            "type": "object", "required": ["apiVersion", "catalogName", "downloadAvailable", "auth", "supports", "currentEndpoints", "futureCapabilities", "scopes", "links"],
            "properties": {
                "apiVersion": {"type": "string"}, "catalogName": {"type": "string"},
                "downloadAvailable": {"type": "boolean", "const": False}, "auth": object_schema,
                "supports": object_schema, "currentEndpoints": {"type": "array", "items": object_schema},
                "futureCapabilities": {"type": "array", "items": {"type": "string"}},
                "scopes": {"type": "array", "items": {"type": "string"}}, "links": object_schema,
            },
        },
        "TaxonomyResponse": {
            "type": "object", "required": ["apiVersion", "catalogName", "downloadAvailable", "facets", "metadata", "links"],
            "properties": {"apiVersion": {"type": "string"}, "catalogName": {"type": "string"}, "downloadAvailable": {"type": "boolean", "const": False}, "facets": object_schema, "metadata": object_schema, "links": object_schema},
        },
        "SymbolSearchResponse": {
            "type": "object", "required": ["items", "nextCursor", "totalEstimate", "query"],
            "properties": {"items": {"type": "array", "items": object_schema}, "nextCursor": {"type": ["string", "null"]}, "totalEstimate": {"type": "integer"}, "query": object_schema},
        },
        "ContextualSearchResponse": {
            "type": "object", "required": ["query", "items", "interpretedFilters", "rankingExplanation", "warnings", "downloadAvailable", "noDownloadNotice"],
            "properties": {"query": {"type": "string"}, "items": {"type": "array", "items": object_schema}, "interpretedFilters": object_schema, "rankingExplanation": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}, "downloadAvailable": {"type": "boolean", "const": False}, "noDownloadNotice": {"type": "string"}},
        },
        "SymbolDetailResponse": {
            "type": "object", "required": ["displayId", "symbolId", "slug", "name", "summary", "taxonomy", "rawAudit", "governance", "availableFormats", "downloadAvailable", "preview", "curated", "provenance", "links"],
            "properties": {"displayId": {"type": "string"}, "symbolId": {"type": "string", "format": "uuid"}, "slug": {"type": "string"}, "name": {"type": "string"}, "summary": {"type": "string"}, "taxonomy": object_schema, "rawAudit": object_schema, "governance": object_schema, "availableFormats": {"type": "array", "items": {"type": "string"}}, "downloadAvailable": {"type": "boolean", "const": False}, "preview": {"anyOf": [object_schema, {"type": "null"}]}, "curated": {"type": "boolean"}, "provenance": object_schema, "links": object_schema},
        },
        "EdQueryResponse": {
            "type": "object", "required": ["conversationId", "mode", "answer", "searchQuery", "interpretedFilters", "symbols", "citations", "suggestedFollowups", "warnings", "downloadAvailable", "mutatesRecords"],
            "properties": {"conversationId": {"type": ["string", "null"]}, "mode": {"type": "string"}, "answer": {"type": "string"}, "searchQuery": {"type": "string"}, "interpretedFilters": object_schema, "symbols": {"type": "array", "items": object_schema}, "citations": {"type": "array", "items": object_schema}, "suggestedFollowups": {"type": "array", "items": {"type": "string"}}, "warnings": {"type": "array", "items": {"type": "string"}}, "downloadAvailable": {"type": "boolean", "const": False}, "mutatesRecords": {"type": "boolean", "const": False}},
        },
        "FeedbackResponse": {
            "type": "object", "required": ["status", "feedbackId", "kind", "symbol", "reviewRequested", "mutatesPublishedState"],
            "properties": {"status": {"type": "string", "const": "recorded"}, "feedbackId": {"type": "string", "format": "uuid"}, "kind": {"type": "string"}, "symbol": object_schema, "reviewRequested": {"type": "boolean"}, "mutatesPublishedState": {"type": "boolean"}},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Symgov Catalog Integration API", "version": "v1",
            "description": "Current Catalog integration surface. Production Catalog downloads are not available.",
        },
        "x-download-available": False,
        "security": [{"CatalogApiKey": ["catalog.read"]}],
        "paths": paths,
        "components": {
            "securitySchemes": {"CatalogApiKey": {"type": "http", "scheme": "bearer", "bearerFormat": "Catalog API key"}},
            "schemas": {
                "Error": error_schema,
                "HTTPValidationError": query_validation_error_schema,
                **response_schemas,
                "ContextualSearchRequest": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "context": {"type": "object"},
                        "limit": {
                            "type": "integer", "default": 20,
                            "description": "Values are clamped to the inclusive range 1 through 100.",
                        },
                    },
                },
                "EdQueryRequest": {
                    "type": "object",
                    "required": ["message"],
                    "additionalProperties": False,
                    "properties": {
                        "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "mode": {"type": "string", "enum": ["auto", "find_symbols", "question"], "default": "auto"},
                        "context": {
                            "type": "object",
                            "additionalProperties": False,
                            "maxProperties": 7,
                            "properties": {
                                "application": {"type": ["string", "null"], "maxLength": 256},
                                "applicationVersion": {"type": ["string", "null"], "maxLength": 256},
                                "drawingType": {"type": ["string", "null"], "maxLength": 256},
                                "selectedLayer": {"type": ["string", "null"], "maxLength": 256},
                                "units": {"type": ["string", "null"], "maxLength": 256},
                                "preferredFormats": {
                                    "type": "array", "maxItems": 20,
                                    "items": {"type": "string", "maxLength": 64},
                                },
                                "projectRef": {"type": ["string", "null"], "maxLength": 256},
                            },
                        },
                        "conversationId": {"type": ["string", "null"], "maxLength": 256},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    },
                },
                "FeedbackRequest": {
                    "type": "object", "required": ["kind", "message"], "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": ["comment", "usage_question", "issue", "request_alternative", "not_found", "standards_question", "send_for_review"]},
                        "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "context": {
                            "type": "object", "additionalProperties": False, "maxProperties": 7,
                            "properties": {
                                "application": {"type": ["string", "null"], "maxLength": 256},
                                "applicationVersion": {"type": ["string", "null"], "maxLength": 256},
                                "drawingType": {"type": ["string", "null"], "maxLength": 256},
                                "selectedLayer": {"type": ["string", "null"], "maxLength": 256},
                                "units": {"type": ["string", "null"], "maxLength": 256},
                                "preferredFormats": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 64}},
                                "projectRef": {"type": ["string", "null"], "maxLength": 256},
                            },
                        },
                    },
                },
            },
            "examples": EXAMPLES,
            "responses": {
                "AuthenticationError": {"description": "Missing or invalid Catalog API key.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                "ScopeError": {"description": "The API key lacks the required scope.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                "ValidationError": {"description": "Invalid or bounded-input request.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                "QueryValidationError": {"description": "FastAPI query-parameter validation failed.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}}},
                "NotFoundError": {"description": "Catalog symbol not found.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
            },
        },
    }
