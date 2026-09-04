from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from time import perf_counter
from typing import cast
import uuid
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..asset_manifest import canonical_asset_format, content_type_for_format, list_download_assets
from ..auth import AuthenticatedUser, current_user_from_token
from ..catalog_api_auth import (
    CatalogApiAuthenticationError,
    IntegrationAuthContext,
    authenticate_catalog_api_key,
    require_catalog_feedback_scope,
    require_catalog_scope,
)
from ..catalog_developer import contains_catalog_credentials
from ..catalog_ed import CatalogEdMode, interpret_catalog_ed_prompt
from ..catalog_search import (
    catalog_symbol_filters as _catalog_symbol_filters,
    catalog_symbol_ref as _catalog_symbol_ref,
    catalog_symbol_summary as _catalog_symbol_summary,
    row_taxonomy_input as _row_taxonomy_input,
    search_catalog_symbols_for_context,
)
from ..catalog_symbol_resolution import (
    CatalogSymbolLookupUnavailable,
    resolve_catalog_symbol,
)
from ..catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
    FORMAT_ORDER,
    catalog_taxonomy_for_symbol,
)
from ..catalog_usage import log_catalog_usage_event_best_effort
from ..dependencies import (
    SESSION_COOKIE_NAME,
    enforce_session_access_state,
    get_db_session,
    matched_route_template,
    session_access_decision,
)
from ..image_content import (
    UnsafeImageContentError,
    safe_image_response_headers,
    validate_stored_image,
)
from ..models import (
    AgentQueueItem,
    Attachment,
    AuditEvent,
    CatalogApiKey,
)
from ..published_feedback_gate import (
    published_feedback_claims_paused,
    published_feedback_paused_response_body,
)
from ..published_catalog import (
    PUBLISHED_SYMBOLS_SQL,
    choose_published_preview_asset,
    published_fallback_source_asset,
    published_symbol_display_id,
)
from ..product_usage_events import record_browse_usage_event_best_effort
from ..runtime import download_object_bytes
from ..services.published_feedback import (
    canonical_request_fingerprint,
    CatalogAuditAttribution,
    DEFAULT_ED_RUNTIME_QUEUE_DIR,
    load_ed_user_for_published_feedback,
    load_replay_queue_item,
    materialize_runtime_envelope,
    normalize_publication_target,
    PublishedFeedbackConflict,
    published_feedback_advisory_lock_id,
    published_feedback_request_anchor_id,
    replay_workflow_delivery_state,
    submit_published_feedback,
    validate_published_feedback_review_case,
)
from ..settings import get_settings

router = APIRouter(prefix="/catalog", tags=["catalog"])

CATALOG_READ_SCOPE = "catalog.read"
CATALOG_ED_QUERY_SCOPE = "catalog.ed.query"
CATALOG_FEEDBACK_WRITE_SCOPE = "catalog.feedback.write"
_CATALOG_MAX_BODY_BYTES = 16384
_CATALOG_ED_MAX_BODY_BYTES = _CATALOG_MAX_BODY_BYTES
CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR: Path = DEFAULT_ED_RUNTIME_QUEUE_DIR
_CATALOG_ED_MODES = {"auto", "find_symbols", "question"}
_CATALOG_FEEDBACK_KINDS = {
    "comment",
    "usage_question",
    "issue",
    "request_alternative",
    "not_found",
    "standards_question",
    "send_for_review",
}
_CATALOG_ED_USAGE_HEADER_NAMES = (
    "x-symgov-application",
    "x-symgov-application-version",
    "x-request-id",
    "user-agent",
)
_CATALOG_ED_CONTEXT_FIELDS = {
    "application",
    "applicationVersion",
    "drawingType",
    "selectedLayer",
    "units",
    "preferredFormats",
    "projectRef",
}
_CREDENTIAL_LIKE_CONTEXT = re.compile(
    r"(?i)(authorization\s*:|bearer\s+|api[_ -]?key\s*[=:]|"
    r"pass(?:word|wd)?\s*[=:]|token\s*[=:]|secret\s*[=:]|"
    r"client[_ -]?secret\s*[=:]|private[_ -]?key|"
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s]+)"
)
_BARE_CREDENTIAL = re.compile(
    r"(?ix)(?:"
    r"\bsymgov_(?:live|test)_[a-z0-9_-]{12,}\b|"
    r"\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{4,}\b|"
    r"\bsk-(?:proj-)?[a-z0-9_-]{16,}\b|"
    r"\b(?:xox[baprs]-|gh[pousr]_)[a-z0-9_-]{12,}\b|"
    r"\bAKIA[A-Z0-9]{16}\b"
    r")"
)
_USAGE_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|pass(?:word|wd)?|token|secret|client[_ -]?secret)"
    r"\s*([=:])\s*([^\s&]+)"
)
_USAGE_BEARER = re.compile(r"(?i)\bbearer\s+[^\s&]+")
_USAGE_CONNECTION_URI = re.compile(
    r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s]+"
)
PUBLIC_CATALOG_LINKS = {
    "capabilities": "/api/v1/catalog/capabilities",
    "taxonomy": "/api/v1/catalog/taxonomy",
    "symbols": "/api/v1/catalog/symbols",
    "symbolSearch": "/api/v1/catalog/search",
    "symbolDownload": "/api/v1/catalog/symbols/download",
    "edQuery": "/api/v1/catalog/ed/query",
    "feedback": "/api/v1/catalog/symbols/{symbolRef}/feedback",
}
CATALOG_DOWNLOAD_FORMATS = frozenset(
    format_name
    for value in FORMAT_ORDER
    if (format_name := canonical_asset_format(value)) is not None
)


def catalog_download_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def catalog_symbol_download_filename(symbol_name: object, display_id: object, format: object) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(symbol_name or "Symbol"))
    name = re.sub(r"[\\/]", " - ", name)
    name = re.sub(r"[:*?<>|]", "-", name).replace('"', "")
    name = re.sub(r"\s+", " ", name).strip(" .") or "Symbol"
    safe_display_id = re.sub(r"[^A-Za-z0-9._-]", "-", str(display_id or "unknown")).strip(".-") or "unknown"
    extension = canonical_asset_format(format)
    if extension not in CATALOG_DOWNLOAD_FORMATS:
        extension = "bin"
    return f"{name} ({safe_display_id}).{extension}"


def catalog_download_header_token(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(value or "unknown")).strip(".-") or "unknown"


def catalog_download_content_disposition(filename: str) -> str:
    ascii_filename = "".join(character if 32 <= ord(character) < 127 else "_" for character in filename)
    basic = f'attachment; filename="{ascii_filename}"'
    if ascii_filename == filename:
        return basic
    return f"{basic}; filename*=UTF-8''{quote(filename, safe='.-_')}"


def require_catalog_download_access(
    request: Request,
    session: Session = Depends(get_db_session),
) -> AuthenticatedUser | IntegrationAuthContext:
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    current_user = current_user_from_token(
        session,
        session_token,
        before_maintenance=lambda must_change_pin, session_purpose: enforce_session_access_state(
            must_change_pin,
            session_purpose,
            request.method,
            matched_route_template(request),
        ),
    )
    if current_user is not None:
        decision = session_access_decision(current_user, request.method, matched_route_template(request))
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.detail)
        session.commit()
        return current_user

    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    api_token = value.strip() if scheme.lower() == "bearer" else request.headers.get("x-symgov-api-key", "").strip()
    try:
        context = authenticate_catalog_api_key(session, api_token)
    except CatalogApiAuthenticationError:
        session.rollback()
        raise HTTPException(
            status_code=503,
            detail="Catalog API key authentication is temporarily unavailable.",
        ) from None
    if context is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if CATALOG_READ_SCOPE not in context.scopes:
        raise HTTPException(status_code=403, detail="Insufficient scope for this operation.")
    session.commit()
    return context


def _latency_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _log_successful_catalog_read(
    session: Session,
    auth_context: IntegrationAuthContext,
    *,
    request: Request,
    route_name: str,
    started_at: float,
    query_text: object = None,
    result_count: int | None = None,
    symbol_ref: str | None = None,
) -> None:
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_READ_SCOPE,
        route_name=route_name,
        status_code=200,
        latency_ms=_latency_ms(started_at),
        query_text=query_text,
        symbol_ref=symbol_ref,
        result_count=result_count,
    )


@router.get("/capabilities")
def get_catalog_capabilities(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    response = {
        "apiVersion": "v1",
        "catalogName": "Symgov Catalog",
        "downloadAvailable": True,
        "auth": {
            "methods": ["api_key"],
            "preferredHeader": "Authorization: Bearer ***",
            "requiredScopes": [CATALOG_READ_SCOPE],
        },
        "supports": {
            "keywordSearch": True,
            "facetSearch": True,
            "contextualSearch": True,
            "taxonomy": True,
            "edQuestions": True,
            "previews": True,
            "feedback": True,
            "usageReporting": True,
            "download": True,
        },
        "currentEndpoints": [
            {
                "method": "GET",
                "path": "/api/v1/catalog/capabilities",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/taxonomy",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/symbols",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "POST",
                "path": "/api/v1/catalog/search",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/symbols/{symbol_ref}",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/symbols/{symbol_ref}/thumbnail",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "GET",
                "path": "/api/v1/catalog/symbols/{symbol_ref}/preview",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "POST",
                "path": "/api/v1/catalog/symbols/download",
                "scope": CATALOG_READ_SCOPE,
            },
            {
                "method": "POST",
                "path": "/api/v1/catalog/ed/query",
                "scope": CATALOG_ED_QUERY_SCOPE,
            },
            {
                "method": "POST",
                "path": "/api/v1/catalog/symbols/{symbol_ref}/feedback",
                "scope": CATALOG_FEEDBACK_WRITE_SCOPE,
            },
        ],
        "futureCapabilities": [
            "customer usage reporting",
        ],
        "scopes": [
            "catalog.read",
            "catalog.preview",
            "catalog.ed.query",
            "catalog.feedback.write",
            "catalog.usage.read",
        ],
        "links": PUBLIC_CATALOG_LINKS,
    }
    _log_successful_catalog_read(session, auth_context, request=request, route_name="catalog_capabilities", started_at=started_at)
    return response


@router.get("/taxonomy")
def get_catalog_taxonomy(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    response = {
        "apiVersion": "v1",
        "catalogName": "Symgov Catalog",
        "downloadAvailable": True,
        "facets": {
            "disciplines": CATALOG_DISCIPLINE_ORDER,
            "categories": CATALOG_CATEGORY_ORDER,
            "formats": FORMAT_ORDER,
            "useCases": CATALOG_USE_CASE_ORDER,
        },
        "metadata": {
            "source": "symgov_backend.catalog_taxonomy",
            "canonical": True,
        },
        "links": {
            "capabilities": "/api/v1/catalog/capabilities",
            "symbols": "/api/v1/catalog/symbols",
            "symbolDownload": PUBLIC_CATALOG_LINKS["symbolDownload"],
        },
    }
    _log_successful_catalog_read(session, auth_context, request=request, route_name="catalog_taxonomy", started_at=started_at)
    return response


def _parse_include(include: str | None) -> list[str]:
    allowed = {"taxonomy", "preview", "evidence", "facets"}
    values: list[str] = []
    for value in str(include or "").split(","):
        item = value.strip()
        if item and item in allowed and item not in values:
            values.append(item)
    return values


def _pagination_offset(cursor: str | None) -> int:
    try:
        return max(0, int(str(cursor or "0")))
    except ValueError:
        return 0



def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


def _preview_response(display_id: str, preview_asset: dict | None) -> dict | None:
    if not preview_asset:
        return None
    preview = {
        "thumbnailUrl": f"/api/v1/catalog/symbols/{display_id}/thumbnail",
        "previewUrl": f"/api/v1/catalog/symbols/{display_id}/preview",
    }
    if preview_asset.get("format"):
        preview["format"] = str(preview_asset.get("format")).upper()
    return preview


def _catalog_links(display_id: str, preview_asset: dict | None, *, download_available: bool = False) -> dict:
    links = {
        "api": f"/api/v1/catalog/symbols/{display_id}",
        "web": f"/#/s/{display_id}",
    }
    if preview_asset:
        links["thumbnail"] = f"/api/v1/catalog/symbols/{display_id}/thumbnail"
        links["preview"] = f"/api/v1/catalog/symbols/{display_id}/preview"
    if download_available:
        links["download"] = PUBLIC_CATALOG_LINKS["symbolDownload"]
    return links


def _catalog_symbol_detail(row) -> dict:
    payload = row.payload_json or {}
    display_id = _catalog_symbol_ref(row)
    taxonomy = catalog_taxonomy_for_symbol(_row_taxonomy_input(row))
    preview_asset = choose_published_preview_asset(payload)
    download_available = bool(
        list_download_assets(payload, fallback_source_asset=published_fallback_source_asset(payload))
    )
    provenance = {
        "sourceRef": payload.get("source_ref"),
        "submittedBy": payload.get("submitted_by"),
        "submissionKind": payload.get("submission_kind"),
    }
    provenance = {key: value for key, value in provenance.items() if value is not None}
    return {
        "displayId": display_id,
        "catalogSymbolId": display_id,
        "symbolId": str(row.symbol_id),
        "slug": row.slug,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "taxonomy": {
            "disciplines": taxonomy["disciplines"],
            "categories": taxonomy["categories"],
            "useCases": taxonomy["use_cases"],
        },
        "rawAudit": {
            "category": row.category,
            "discipline": row.discipline,
            "rawCategories": taxonomy["raw_categories"],
            "rawDisciplines": taxonomy["raw_disciplines"],
        },
        "governance": {
            "status": "published",
            "revisionId": str(row.symbol_revision_id),
            "revision": row.revision_label,
            "revisionCreatedAt": _isoformat(row.revision_created_at),
            "rationale": row.rationale or "",
            "effectiveDate": _isoformat(row.effective_date),
            "lastUpdatedAt": _isoformat(row.last_updated_at),
            "packCode": row.pack_code,
            "packTitle": row.pack_title,
            "pageCode": row.page_code,
            "pageTitle": row.page_title,
        },
        "availableFormats": taxonomy["available_formats"],
        "downloadAvailable": download_available,
        "preview": _preview_response(display_id, preview_asset),
        "curated": bool(payload.get("curated", True)),
        "provenance": provenance,
        "links": _catalog_links(display_id, preview_asset, download_available=download_available),
    }


def _load_catalog_symbol_row(session: Session, symbol_ref: str):
    try:
        resolved = resolve_catalog_symbol(
            session,
            symbol_ref,
            route_family="catalog.symbol_detail",
        )
    except CatalogSymbolLookupUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "catalog_symbol_not_found", "message": "Catalog symbol was not found."},
        )
    try:
        rows = session.execute(
            text(
                PUBLISHED_SYMBOLS_SQL
                + """
                AND gs.id = :symbol_id
                ORDER BY pp.effective_date DESC, pk.effective_date DESC
                LIMIT 1
                """
            ),
            {"symbol_id": resolved.symbol_id, "symbol_ref": symbol_ref},
        ).all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "catalog_symbol_unavailable", "message": "This Catalog symbol is not currently available."},
        )
    return rows[0]


def _load_catalog_symbol_rows_for_feedback(session: Session, symbol_ref: str):
    try:
        resolved = resolve_catalog_symbol(
            session,
            symbol_ref,
            route_family="catalog.feedback",
        )
    except CatalogSymbolLookupUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "catalog_symbol_not_found", "message": "Catalog symbol was not found."},
        )
    try:
        rows = session.execute(
            text(
                PUBLISHED_SYMBOLS_SQL
                + """
                AND gs.id = :symbol_id
                ORDER BY pk.pack_code ASC,
                pe.sort_order ASC,
                pp.id ASC
                """
            ),
            {"symbol_id": resolved.symbol_id, "symbol_ref": symbol_ref},
        ).all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "catalog_symbol_lookup_unavailable",
                "message": "Catalog symbol lookup is temporarily unavailable. Please retry.",
            },
        ) from exc
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "catalog_symbol_unavailable", "message": "This Catalog symbol is not currently available."},
        )
    return rows


def _catalog_symbol_preview_bytes(symbol_ref: str, session: Session) -> Response:
    row = _load_catalog_symbol_row(session, symbol_ref)
    preview_asset = choose_published_preview_asset(row.payload_json or {})
    object_key = preview_asset.get("object_key") if preview_asset else None
    if not object_key:
        raise HTTPException(status_code=404, detail="Catalog symbol preview was not found.")
    try:
        revision_id = uuid.UUID(str(row.symbol_revision_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Catalog symbol preview was not found.") from exc
    attachment = (
        session.query(Attachment)
        .filter(
            Attachment.object_key == object_key,
            Attachment.parent_type == "symbol_revision",
            Attachment.parent_id == revision_id,
        )
        .one_or_none()
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="Catalog symbol preview was not found.")
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    try:
        media_type = validate_stored_image(
            payload["payload"],
            attachment.content_type,
            payload.get("content_type"),
        )
    except UnsafeImageContentError as exc:
        raise HTTPException(status_code=404, detail="Catalog symbol preview was not found.") from exc
    return Response(
        content=payload["payload"],
        media_type=media_type,
        headers=safe_image_response_headers(),
    )


async def _read_bounded_catalog_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _CATALOG_MAX_BODY_BYTES:
                raise HTTPException(status_code=400, detail="Request body is too large.")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _CATALOG_MAX_BODY_BYTES:
            raise HTTPException(status_code=400, detail="Request body is too large.")
        body.extend(chunk)
    return bytes(body)


# Compatibility for tests and callers that referenced the original Ed-specific helper.
_read_bounded_catalog_ed_body = _read_bounded_catalog_body


class _CatalogBoundedJsonRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                setattr(request, "_body", await _read_bounded_catalog_body(request))
                return await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Request body must be valid JSON."},
                )

        return handle


def _validate_catalog_ed_context(value: object) -> dict:
    if value is None:
        raise HTTPException(status_code=400, detail="context must be a JSON object.")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="context must be a JSON object.")
    unknown_fields = set(value) - _CATALOG_ED_CONTEXT_FIELDS
    if unknown_fields:
        raise HTTPException(status_code=400, detail="context contains unknown fields.")
    if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 8192:
        raise HTTPException(status_code=400, detail="context is too large.")

    normalized = {}
    for key, item in value.items():
        if key == "preferredFormats":
            if not isinstance(item, list) or len(item) > 20:
                raise HTTPException(status_code=400, detail="preferredFormats must be a bounded list.")
            if any(not isinstance(entry, str) or len(entry) > 64 for entry in item):
                raise HTTPException(status_code=400, detail="preferredFormats must contain bounded strings.")
            strings = item
        else:
            if item is None:
                normalized[key] = None
                continue
            if not isinstance(item, str) or len(item) > 256:
                raise HTTPException(status_code=400, detail=f"{key} must be a bounded string.")
            strings = [item]
        if any(
            _CREDENTIAL_LIKE_CONTEXT.search(entry) or _BARE_CREDENTIAL.search(entry)
            for entry in strings
        ):
            raise HTTPException(status_code=400, detail="context must not contain credentials.")
        normalized[key] = item
    return normalized


def _validate_catalog_ed_usage_headers(request: Request) -> None:
    for header_name in _CATALOG_ED_USAGE_HEADER_NAMES:
        value = request.headers.get(header_name, "")
        if value and (
            _CREDENTIAL_LIKE_CONTEXT.search(value) or _BARE_CREDENTIAL.search(value)
        ):
            raise HTTPException(
                status_code=400,
                detail="Usage metadata headers must not contain credentials.",
            )


def _sanitize_catalog_ed_usage_message(message: str) -> str:
    sanitized = _USAGE_SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        message,
    )
    sanitized = _USAGE_BEARER.sub("Bearer [REDACTED]", sanitized)
    sanitized = _USAGE_CONNECTION_URI.sub(
        lambda match: f"{match.group(1)}://[REDACTED]",
        sanitized,
    )
    return _BARE_CREDENTIAL.sub("[REDACTED]", sanitized)


def _catalog_ed_citations(symbols: list[dict]) -> list[dict]:
    return [
        {
            "displayId": symbol["displayId"],
            "href": symbol["links"]["api"],
        }
        for symbol in symbols
    ]


def _reject_nonfinite_json_constant(value: str):
    raise ValueError(f"Non-finite JSON constant is not allowed: {value}")


async def _parse_strict_catalog_json_object(request: Request) -> dict:
    raw_body = await _read_bounded_catalog_body(request)
    try:
        body = json.loads(raw_body, parse_constant=_reject_nonfinite_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return body


async def catalog_ed_query(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_ED_QUERY_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    body = await _parse_strict_catalog_json_object(request)
    _validate_catalog_ed_usage_headers(request)

    message_value = body.get("message")
    if not isinstance(message_value, str):
        raise HTTPException(status_code=400, detail="message must be a string.")
    message = message_value.strip()
    if not message or len(message) > 2000:
        raise HTTPException(status_code=400, detail="message must contain 1 to 2000 characters.")

    mode = body.get("mode", "auto")
    if not isinstance(mode, str) or mode not in _CATALOG_ED_MODES:
        raise HTTPException(status_code=400, detail="mode must be one of: auto, find_symbols, question.")
    context = _validate_catalog_ed_context(body.get("context", {}))

    limit = body.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be an integer from 1 to 100.")

    conversation_id = body.get("conversationId")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or len(conversation_id) > 256
    ):
        raise HTTPException(status_code=400, detail="conversationId must be a bounded opaque string.")

    interpretation = interpret_catalog_ed_prompt(message, mode=cast(CatalogEdMode, mode))
    symbols: list[dict] = []
    search_warnings: list[str] = []
    if interpretation.selected_mode == "find_symbols":
        search_result = search_catalog_symbols_for_context(
            session,
            query=interpretation.search_query,
            context=context,
            limit=limit,
        )
        symbols = search_result.items
        search_warnings = search_result.warnings

    answer = interpretation.answer
    if interpretation.selected_mode == "find_symbols":
        answer += (
            f" Ed found {len(symbols)} likely Catalog symbol result(s)."
            if symbols
            else " Ed found no matching Catalog symbols."
        )
    response = {
        "conversationId": conversation_id,
        "mode": interpretation.selected_mode,
        "answer": answer,
        "searchQuery": interpretation.search_query,
        "interpretedFilters": interpretation.interpreted_filters,
        "symbols": symbols,
        "citations": _catalog_ed_citations(symbols),
        "suggestedFollowups": interpretation.suggested_followups,
        "warnings": interpretation.warnings + search_warnings,
        "downloadAvailable": False,
        "mutatesRecords": False,
    }
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_ED_QUERY_SCOPE,
        route_name="catalog_ed_query",
        status_code=200,
        latency_ms=_latency_ms(started_at),
        query_text=_sanitize_catalog_ed_usage_message(message),
        result_count=len(symbols),
        ed_query_type=interpretation.selected_mode,
    )
    return response


router.add_api_route(
    "/ed/query",
    catalog_ed_query,
    methods=["POST"],
    route_class_override=_CatalogBoundedJsonRoute,
)


def _catalog_feedback_replay_response(
    session: Session,
    *,
    anchor: AuditEvent,
    api_key_id: uuid.UUID,
    auth_context: IntegrationAuthContext,
    request: Request,
    started_at: float,
    symbol_ref: str,
    kind_value: str,
) -> JSONResponse:
    anchor_payload = anchor.payload_json or {}
    stored_response = anchor_payload.get("response")
    if not isinstance(stored_response, dict):
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    replay_response = dict(stored_response)
    replay_response["requestReplayed"] = True
    queue_id = anchor_payload.get("ed_queue_item_id")
    pending = False
    if queue_id:
        symbol = replay_response.get("symbol")
        if not isinstance(symbol, dict):
            raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
        queue_item = load_replay_queue_item(
            session,
            request_anchor_id=anchor.id,
            queue_item_id=queue_id,
            symbol_id=symbol.get("symbolId"),
        )
        replay_response["workflowDeliveryState"] = replay_workflow_delivery_state(
            queue_item,
            CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR,
            materialize=materialize_runtime_envelope,
        )
        pending = replay_response["workflowDeliveryState"] == "pending"
        replay_response["status"] = "accepted_pending_delivery" if pending else "recorded"
    elif kind_value == "send_for_review":
        raise PublishedFeedbackConflict("published_feedback_workflow_integrity")
    key_row = session.get(CatalogApiKey, api_key_id)
    if key_row is not None:
        key_row.last_used_at = datetime.now(timezone.utc).replace(microsecond=0)
    session.commit()
    replay_status = 202 if pending else 201
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_FEEDBACK_WRITE_SCOPE,
        route_name="catalog_symbol_feedback",
        status_code=replay_status,
        latency_ms=_latency_ms(started_at),
        symbol_ref=symbol_ref,
        result_count=1,
        ed_query_type=kind_value,
    )
    return JSONResponse(status_code=replay_status, content=replay_response)


async def catalog_symbol_feedback(
    symbol_ref: str,
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_feedback_scope(CATALOG_FEEDBACK_WRITE_SCOPE)),
    session: Session = Depends(get_db_session),
) -> JSONResponse:
    started_at = perf_counter()
    if published_feedback_claims_paused():
        return JSONResponse(
            status_code=503,
            content=published_feedback_paused_response_body(),
            headers={"Retry-After": "60"},
        )
    body = await _parse_strict_catalog_json_object(request)
    if set(body) - {"kind", "message", "context"}:
        raise HTTPException(status_code=400, detail="Request body contains unknown fields.")
    _validate_catalog_ed_usage_headers(request)

    kind_value = body.get("kind")
    if not isinstance(kind_value, str) or kind_value not in _CATALOG_FEEDBACK_KINDS:
        raise HTTPException(
            status_code=400,
            detail="kind must be one of: comment, usage_question, issue, request_alternative, not_found, standards_question, send_for_review.",
        )
    message_value = body.get("message")
    if not isinstance(message_value, str):
        raise HTTPException(status_code=400, detail="message must be a string.")
    message = message_value.strip()
    if not message or len(message) > 2000:
        raise HTTPException(status_code=400, detail="message must contain 1 to 2000 characters.")
    context = _validate_catalog_ed_context(body.get("context", {}))
    try:
        request_id = uuid.UUID(str(request.headers.get("Idempotency-Key") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be a UUID.") from exc

    if contains_catalog_credentials(message):
        raise HTTPException(status_code=400, detail="message must not contain credentials.")

    api_key_id = uuid.UUID(auth_context.api_key_id)
    review_requested = kind_value == "send_for_review"
    anchor_id = published_feedback_request_anchor_id(
        principal_type="catalog_api_key", principal_id=api_key_id, request_key=request_id
    )
    fingerprint = canonical_request_fingerprint(
        {
            "route_family": "catalog_published_feedback",
            "target": symbol_ref.strip().casefold(),
            "command_or_kind": kind_value,
            "normalized_message": message,
            "normalized_bounded_context": context,
            "principal_type": "catalog_api_key",
            "principal_id": str(api_key_id).lower(),
        }
    )
    requester_payload = {
        "type": "catalog_api_key",
        "id": str(api_key_id),
        "key_prefix": auth_context.key_prefix,
        "customer_name": auth_context.customer_name,
        "integration_name": auth_context.integration_name,
    }
    try:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": published_feedback_advisory_lock_id("published-feedback-request:", anchor_id)},
        )
        existing_anchor = session.get(AuditEvent, anchor_id)
        if existing_anchor is not None:
            anchor_payload = existing_anchor.payload_json or {}
            if anchor_payload.get("request_fingerprint") != fingerprint:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            return _catalog_feedback_replay_response(
                session,
                anchor=existing_anchor,
                api_key_id=api_key_id,
                auth_context=auth_context,
                request=request,
                started_at=started_at,
                symbol_ref=symbol_ref,
                kind_value=kind_value,
            )

        rows = _load_catalog_symbol_rows_for_feedback(session, symbol_ref)
        for symbol_id in sorted({uuid.UUID(str(row.symbol_id)) for row in rows}, key=str):
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": published_feedback_advisory_lock_id("published-feedback-symbol:", symbol_id)},
            )
        target = normalize_publication_target(rows)
        row = next(row for row in rows if uuid.UUID(str(row.page_id)) == target.canonical_page_id)
        ed_user = load_ed_user_for_published_feedback(session) if review_requested else None
        review_case = (
            validate_published_feedback_review_case(
                session, symbol_id=target.symbol_id, workflow_owner_id=ed_user.id
            )
            if ed_user is not None
            else None
        )
        result = submit_published_feedback(
            session,
            row=row,
            source="catalog_integration_api",
            kind=kind_value,
            message=message,
            context_json=context,
            catalog_api_key_id=api_key_id,
            audit_action="catalog_symbol_feedback",
            catalog_audit_attribution=CatalogAuditAttribution(
                api_key_id=api_key_id,
                key_prefix=auth_context.key_prefix,
                customer_name=auth_context.customer_name,
                integration_name=auth_context.integration_name,
            ),
            request_review=review_requested,
            runtime_queue_dir=CATALOG_FEEDBACK_RUNTIME_QUEUE_DIR,
            request_anchor_id=anchor_id,
            publication_target=target,
            requester_payload=requester_payload,
            workflow_owner_id=ed_user.id if ed_user is not None else None,
            validated_review_case=review_case,
            review_case_validated=ed_user is not None,
        )
        response = {
            "status": "recorded",
            "feedbackId": str(result.record.id),
            "kind": kind_value,
            "symbol": {
                "displayId": published_symbol_display_id(row),
                "symbolId": str(target.symbol_id),
            },
            "reviewRequested": review_requested,
            "mutatesPublishedState": False,
            "remainsPublished": True,
            "requestReplayed": False,
            "workflowDeliveryState": "materialized" if result.queue_item else "not_applicable",
        }
        session.add(
            AuditEvent(
                id=anchor_id,
                entity_type="published_feedback_request",
                entity_id=anchor_id,
                action="published_feedback_request_accepted",
                actor_id=None,
                payload_json={
                    "idempotency_key": str(request_id),
                    "request_fingerprint": fingerprint,
                    "route_family": "catalog_published_feedback",
                    "resolved_symbol_ids": [str(target.symbol_id)],
                    "requester": requester_payload,
                    "ed_queue_item_id": str(result.queue_item.id) if result.queue_item else None,
                    "response": response,
                },
                created_at=datetime.now(timezone.utc).replace(microsecond=0),
            )
        )
        key_row = session.get(CatalogApiKey, api_key_id)
        if key_row is not None:
            key_row.last_used_at = datetime.now(timezone.utc).replace(microsecond=0)
        session.flush()
        session.commit()
    except PublishedFeedbackConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": published_feedback_advisory_lock_id("published-feedback-request:", anchor_id)},
        )
        winner = session.get(AuditEvent, anchor_id)
        if winner is None or (winner.payload_json or {}).get("request_fingerprint") != fingerprint:
            raise HTTPException(status_code=409, detail="idempotency_conflict") from exc
        return _catalog_feedback_replay_response(
            session,
            anchor=winner,
            api_key_id=api_key_id,
            auth_context=auth_context,
            request=request,
            started_at=started_at,
            symbol_ref=symbol_ref,
            kind_value=kind_value,
        )
    except Exception:
        session.rollback()
        raise

    pending = False
    if result.runtime_envelope is not None:
        try:
            materialize_runtime_envelope(result.runtime_envelope)
        except OSError:
            response["workflowDeliveryState"] = "pending"
            response["status"] = "accepted_pending_delivery"
            pending = True
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_FEEDBACK_WRITE_SCOPE,
        route_name="catalog_symbol_feedback",
        status_code=202 if pending else 201,
        latency_ms=_latency_ms(started_at),
        symbol_ref=symbol_ref,
        result_count=1,
        ed_query_type=kind_value,
    )
    return JSONResponse(status_code=202 if pending else 201, content=response)


router.add_api_route(
    "/symbols/{symbol_ref}/feedback",
    catalog_symbol_feedback,
    methods=["POST"],
    status_code=201,
)


async def contextual_catalog_search(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    body = await _parse_strict_catalog_json_object(request)
    query_value = body.get("query")
    if not isinstance(query_value, str):
        raise HTTPException(status_code=400, detail="query must be a string.")
    query = query_value.strip()
    if not query or len(query) > 2000:
        raise HTTPException(status_code=400, detail="query must contain 1 to 2000 characters.")
    context = body["context"] if "context" in body else {}
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail="context must be a JSON object.")
    if contains_catalog_credentials(query) or contains_catalog_credentials(context):
        raise HTTPException(status_code=400, detail="query and context must not contain credentials.")
    requested_limit = body.get("limit", 20)
    if isinstance(requested_limit, bool) or not isinstance(requested_limit, int):
        raise HTTPException(status_code=400, detail="limit must be an integer.")
    limit = min(max(requested_limit, 1), 100)

    result = search_catalog_symbols_for_context(session, query=query, context=context, limit=limit)
    items = result.items

    download_available = any(item.get("downloadAvailable") for item in items)
    response = {
        "query": query,
        "items": items,
        "interpretedFilters": result.interpreted_filters,
        "rankingExplanation": result.ranking_explanation,
        "warnings": result.warnings,
        "downloadAvailable": download_available,
        "downloadEndpoint": PUBLIC_CATALOG_LINKS["symbolDownload"],
    }
    if not download_available:
        response["noDownloadNotice"] = "The requested format is not available for these symbols."
    log_catalog_usage_event_best_effort(
        session,
        auth_context,
        request=request,
        scope_used=CATALOG_READ_SCOPE,
        route_name="catalog_contextual_search",
        status_code=200,
        latency_ms=_latency_ms(started_at),
        query_text=query,
        result_count=len(items),
    )
    return response


router.add_api_route(
    "/search",
    contextual_catalog_search,
    methods=["POST"],
)


@router.get("/symbols")
def search_catalog_symbols(
    request: Request,
    q: str | None = Query(default=None),
    discipline: str | None = Query(default=None),
    category: str | None = Query(default=None),
    use_case: str | None = Query(default=None, alias="useCase"),
    format_: str | None = Query(default=None, alias="format"),
    pack: str | None = Query(default=None),
    symbol_family: str | None = Query(default=None, alias="symbolFamily"),
    has_preview: bool | None = Query(default=None, alias="hasPreview"),
    updated_since: str | None = Query(default=None, alias="updatedSince"),
    limit: int = Query(default=25, ge=1),
    cursor: str | None = Query(default=None),
    include: str | None = Query(default=None),
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    capped_limit = min(max(int(limit), 1), 100)
    offset = _pagination_offset(cursor)
    include_values = _parse_include(include)
    filters, params, response_filters = _catalog_symbol_filters(
        q=q,
        discipline=discipline,
        category=category,
        use_case=use_case,
        format_=format_,
        pack=pack,
        symbol_family=symbol_family,
        has_preview=has_preview,
        updated_since=updated_since,
    )
    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    params.update({"limit": capped_limit + 1, "offset": offset})
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + where_extension
            + """
            ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()
    page_rows = rows[:capped_limit]
    next_cursor = str(offset + capped_limit) if len(rows) > capped_limit else None
    response = {
        "items": [_catalog_symbol_summary(row) for row in page_rows],
        "nextCursor": next_cursor,
        "totalEstimate": offset + len(page_rows),
        "query": {
            "q": q,
            "filters": response_filters,
            "limit": capped_limit,
            "cursor": cursor,
            "include": include_values,
        },
    }
    _log_successful_catalog_read(
        session,
        auth_context,
        request=request,
        route_name="catalog_symbol_search",
        started_at=started_at,
        query_text=q,
        result_count=len(page_rows),
    )
    return response


@router.post("/symbols/download")
async def download_catalog_symbols(
    request: Request,
    auth_context: AuthenticatedUser | IntegrationAuthContext = Depends(require_catalog_download_access),
    session: Session = Depends(get_db_session),
) -> Response:
    started_at = perf_counter()
    body = await _parse_strict_catalog_json_object(request)
    if set(body) != {"symbolIds", "format"}:
        raise HTTPException(status_code=400, detail="Request must contain symbolIds and format only.")
    symbol_ids = body.get("symbolIds")
    raw_format = body.get("format")
    requested_format = canonical_asset_format(raw_format)
    if (
        not isinstance(symbol_ids, list)
        or not 1 <= len(symbol_ids) <= 10
        or not isinstance(raw_format, str)
        or not 1 <= len(raw_format.strip()) <= 64
        or requested_format not in CATALOG_DOWNLOAD_FORMATS
    ):
        raise HTTPException(status_code=400, detail="Select 1 to 10 symbols and one format.")
    if any(not isinstance(symbol_id, str) or not symbol_id.strip() or len(symbol_id.strip()) > 256 for symbol_id in symbol_ids):
        raise HTTPException(status_code=400, detail="Each symbol ID must be a non-empty string.")
    symbol_ids = [symbol_id.strip() for symbol_id in symbol_ids]
    if len(set(symbol_ids)) != len(symbol_ids):
        raise HTTPException(status_code=400, detail="Each selected symbol must be unique.")

    resolved_rows = []
    resolved_symbol_ids: set[str] = set()
    for symbol_id in symbol_ids:
        row = _load_catalog_symbol_row(session, symbol_id)
        resolved_symbol_id = str(row.symbol_id)
        if resolved_symbol_id in resolved_symbol_ids:
            raise HTTPException(status_code=400, detail="Each selected symbol must be unique.")
        resolved_symbol_ids.add(resolved_symbol_id)
        resolved_rows.append(row)

    selected: list[tuple[str, bytes, str | None]] = []
    skipped: list[str] = []
    for row in resolved_rows:
        display_id = published_symbol_display_id(row)
        asset = next(
            (
                candidate
                for candidate in list_download_assets(
                    row.payload_json or {},
                    fallback_source_asset=published_fallback_source_asset(row.payload_json or {}),
                )
                if canonical_asset_format(candidate.get("format")) == requested_format
            ),
            None,
        )
        if asset is None:
            skipped.append(catalog_download_header_token(display_id))
            continue
        stored = download_object_bytes(
            object_key=asset["object_key"],
            env_file=str(get_settings().storage_env_file),
        )
        symbol_name = str((row.payload_json or {}).get("name") or row.canonical_name).strip()
        filename = catalog_symbol_download_filename(symbol_name, display_id, requested_format)
        selected.append(
            (
                filename,
                stored["payload"],
                content_type_for_format(
                    requested_format,
                    filename=filename,
                    content_type=asset.get("content_type") or stored.get("content_type"),
                ),
            )
        )
        if isinstance(auth_context, AuthenticatedUser):
            # This route's own `PUBLISHED_SYMBOLS_SQL` join means every row
            # here is a currently-public symbol -- `symbol_source` is always
            # `"public"`, never `"organization_private"` (unlike the
            # organization-bound preview/Favorite routes in `published.py`).
            record_browse_usage_event_best_effort(
                session,
                event_type="symbol_downloaded",
                current_user=auth_context,
                governed_symbol_id=uuid.UUID(str(row.symbol_id)),
                symbol_revision_id=uuid.UUID(str(row.symbol_revision_id)),
                symbol_source="public",
                format=requested_format,
            )

    if not selected:
        raise HTTPException(status_code=422, detail="The selected format is not available for any selected symbol.")

    headers = {
        "X-Symgov-Selected-Count": str(len(symbol_ids)),
        "X-Symgov-Downloaded-Count": str(len(selected)),
        "X-Symgov-Skipped-Symbols": ",".join(skipped),
    }
    if len(symbol_ids) == 1:
        filename, content, media_type = selected[0]
        headers["Content-Disposition"] = catalog_download_content_disposition(filename)
        if isinstance(auth_context, IntegrationAuthContext):
            _log_successful_catalog_read(
                session,
                auth_context,
                request=request,
                route_name="catalog_symbol_download",
                started_at=started_at,
                query_text=requested_format,
                result_count=len(selected),
            )
        return Response(content=content, media_type=media_type, headers=headers)

    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for filename, content, _ in selected:
            output.writestr(filename, content)
    timestamp = catalog_download_now().astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"symgov-{requested_format}-{timestamp}.zip"
    headers["Content-Disposition"] = catalog_download_content_disposition(filename)
    if isinstance(auth_context, IntegrationAuthContext):
        _log_successful_catalog_read(
            session,
            auth_context,
            request=request,
            route_name="catalog_symbol_download",
            started_at=started_at,
            query_text=requested_format,
            result_count=len(selected),
        )
    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers=headers,
    )


@router.get("/symbols/{symbol_ref}")
def get_catalog_symbol_detail(
    symbol_ref: str,
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    row = _load_catalog_symbol_row(session, symbol_ref)
    response = _catalog_symbol_detail(row)
    _log_successful_catalog_read(
        session,
        auth_context,
        request=request,
        route_name="catalog_symbol_detail",
        started_at=started_at,
        symbol_ref=symbol_ref,
        result_count=1,
    )
    return response


@router.get("/symbols/{symbol_ref}/thumbnail")
def get_catalog_symbol_thumbnail(
    symbol_ref: str,
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> Response:
    started_at = perf_counter()
    response = _catalog_symbol_preview_bytes(symbol_ref, session)
    _log_successful_catalog_read(
        session,
        auth_context,
        request=request,
        route_name="catalog_symbol_thumbnail",
        started_at=started_at,
        symbol_ref=symbol_ref,
        result_count=1,
    )
    return response


@router.get("/symbols/{symbol_ref}/preview")
def get_catalog_symbol_preview(
    symbol_ref: str,
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> Response:
    started_at = perf_counter()
    response = _catalog_symbol_preview_bytes(symbol_ref, session)
    _log_successful_catalog_read(
        session,
        auth_context,
        request=request,
        route_name="catalog_symbol_preview",
        started_at=started_at,
        symbol_ref=symbol_ref,
        result_count=1,
    )
    return response
