from __future__ import annotations

import re
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..catalog_api_auth import IntegrationAuthContext, require_catalog_scope
from ..catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
    FORMAT_ORDER,
    catalog_taxonomy_for_symbol,
)
from ..catalog_usage import log_catalog_usage_event_best_effort
from ..dependencies import get_db_session
from ..models import Attachment
from ..runtime import download_object_bytes
from ..settings import get_settings
from .published import PUBLISHED_SYMBOLS_SQL, choose_published_preview_asset, published_symbol_display_id

router = APIRouter(prefix="/catalog", tags=["catalog"])

CATALOG_READ_SCOPE = "catalog.read"
PUBLIC_CATALOG_LINKS = {
    "capabilities": "/api/v1/catalog/capabilities",
    "taxonomy": "/api/v1/catalog/taxonomy",
    "symbols": "/api/v1/catalog/symbols",
    "symbolSearch": "/api/v1/catalog/search",
    "edQuery": "/api/v1/catalog/ed/query",
    "feedback": "/api/v1/catalog/symbols/{symbolRef}/feedback",
}


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
        "downloadAvailable": False,
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
            "download": False,
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
        ],
        "futureCapabilities": [
            "paginated symbol search",
            "symbol detail and preview aliases",
            "contextual Catalog search",
            "Ed question and symbol-finding support",
            "integration feedback submission",
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
        "downloadAvailable": False,
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


def _catalog_symbol_ref(row) -> str:
    return published_symbol_display_id(row)


def _row_taxonomy_input(row) -> dict:
    payload = row.payload_json or {}
    return {
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "displayName": published_symbol_display_id(row),
        "category": row.category,
        "discipline": row.discipline,
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "keywords": payload.get("keywords") or payload.get("search_terms") or [],
        "downloads": payload.get("downloads") or [],
        "payload": payload,
    }


def _catalog_symbol_summary(row) -> dict:
    payload = row.payload_json or {}
    display_id = _catalog_symbol_ref(row)
    taxonomy = catalog_taxonomy_for_symbol(_row_taxonomy_input(row))
    preview_asset = choose_published_preview_asset(payload)
    preview = None
    links = {"api": f"/api/v1/catalog/symbols/{display_id}"}
    if preview_asset:
        preview = {
            "thumbnailUrl": f"/api/v1/catalog/symbols/{display_id}/thumbnail",
            "previewUrl": f"/api/v1/catalog/symbols/{display_id}/preview",
        }
        links["thumbnail"] = preview["thumbnailUrl"]
        links["preview"] = preview["previewUrl"]

    return {
        "displayId": display_id,
        "symbolId": str(row.symbol_id),
        "slug": row.slug,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "catalogDisciplines": taxonomy["disciplines"],
        "catalogCategories": taxonomy["categories"],
        "useCases": taxonomy["use_cases"],
        "availableFormats": taxonomy["available_formats"],
        "downloadAvailable": False,
        "preview": preview,
        "links": links,
    }


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


def _catalog_links(display_id: str, preview_asset: dict | None) -> dict:
    links = {"api": f"/api/v1/catalog/symbols/{display_id}"}
    if preview_asset:
        links["thumbnail"] = f"/api/v1/catalog/symbols/{display_id}/thumbnail"
        links["preview"] = f"/api/v1/catalog/symbols/{display_id}/preview"
    return links


def _catalog_symbol_detail(row) -> dict:
    payload = row.payload_json or {}
    display_id = _catalog_symbol_ref(row)
    taxonomy = catalog_taxonomy_for_symbol(_row_taxonomy_input(row))
    preview_asset = choose_published_preview_asset(payload)
    provenance = {
        "sourceRef": payload.get("source_ref"),
        "submittedBy": payload.get("submitted_by"),
        "submissionKind": payload.get("submission_kind"),
    }
    provenance = {key: value for key, value in provenance.items() if value is not None}
    return {
        "displayId": display_id,
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
        "downloadAvailable": False,
        "preview": _preview_response(display_id, preview_asset),
        "curated": bool(payload.get("curated", True)),
        "provenance": provenance,
        "links": _catalog_links(display_id, preview_asset),
    }


def _published_symbol_ref_filter_sql() -> str:
    return """
        AND (
            gs.slug = :symbol_ref
            OR gs.id::text = :symbol_ref
            OR ((sr.payload_json ->> 'package_display_id') || '-' || (sr.payload_json ->> 'package_symbol_sequence')) = :symbol_ref
            OR (sr.payload_json ->> 'display_name') = :symbol_ref
            OR (sr.payload_json ->> 'workspace_display_name') = :symbol_ref
            OR (sr.payload_json ->> 'symbol_display_id') = :symbol_ref
        )
    """


def _load_catalog_symbol_row(session: Session, symbol_ref: str):
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + _published_symbol_ref_filter_sql()
            + """
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_ref": symbol_ref},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Catalog symbol was not found.")
    return rows[0]


def _catalog_symbol_preview_bytes(symbol_ref: str, session: Session) -> Response:
    row = _load_catalog_symbol_row(session, symbol_ref)
    preview_asset = choose_published_preview_asset(row.payload_json or {})
    object_key = preview_asset.get("object_key") if preview_asset else None
    if not object_key:
        raise HTTPException(status_code=404, detail="Catalog symbol preview was not found.")
    attachment = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)


def _catalog_symbol_filters(
    *,
    q: str | None,
    discipline: str | None,
    category: str | None,
    use_case: str | None,
    format_: str | None,
    pack: str | None,
    symbol_family: str | None,
    has_preview: bool | None,
    updated_since: str | None,
) -> tuple[list[str], dict, dict]:
    filters: list[str] = []
    params: dict = {}
    response_filters: dict = {}
    if q:
        filters.append(
            """
            (
                gs.slug ILIKE :query
                OR gs.canonical_name ILIKE :query
                OR gs.category ILIKE :query
                OR gs.discipline ILIKE :query
                OR pk.pack_code ILIKE :query
                OR pk.title ILIKE :query
                OR pp.page_code ILIKE :query
                OR CAST(sr.payload_json AS TEXT) ILIKE :query
            )
            """
        )
        params["query"] = f"%{q}%"
    if discipline:
        filters.append("(gs.discipline ILIKE :discipline OR CAST(sr.payload_json AS TEXT) ILIKE :discipline)")
        params["discipline"] = f"%{discipline}%"
        response_filters["discipline"] = discipline
    if category:
        filters.append("(gs.category ILIKE :category OR CAST(sr.payload_json AS TEXT) ILIKE :category)")
        params["category"] = f"%{category}%"
        response_filters["category"] = category
    if use_case:
        filters.append("CAST(sr.payload_json AS TEXT) ILIKE :use_case")
        params["use_case"] = f"%{use_case}%"
        response_filters["useCase"] = use_case
    if format_:
        filters.append("CAST(sr.payload_json AS TEXT) ILIKE :format")
        params["format"] = f"%{format_}%"
        response_filters["format"] = format_
    if pack:
        filters.append("(pk.pack_code = :pack OR pk.id::text = :pack)")
        params["pack"] = pack
        response_filters["pack"] = pack
    if symbol_family:
        filters.append("(gs.slug ILIKE :symbol_family OR gs.canonical_name ILIKE :symbol_family OR CAST(sr.payload_json AS TEXT) ILIKE :symbol_family)")
        params["symbol_family"] = f"%{symbol_family}%"
        response_filters["symbolFamily"] = symbol_family
    if has_preview is not None:
        preview_filter = """
        (
            sr.payload_json ? 'preview_object_key'
            OR sr.payload_json #> '{visual_assets,preview}' IS NOT NULL
            OR CAST(sr.payload_json AS TEXT) ILIKE '%preview%'
        )
        """
        filters.append(preview_filter if has_preview else f"NOT {preview_filter}")
        response_filters["hasPreview"] = has_preview
    if updated_since:
        filters.append("GREATEST(gs.updated_at, sr.created_at, pp.updated_at, pk.updated_at) >= CAST(:updated_since AS timestamptz)")
        params["updated_since"] = updated_since
        response_filters["updatedSince"] = updated_since
    return filters, params, response_filters


def _contextual_search_context(context: object) -> tuple[dict, list[str]]:
    context = context if isinstance(context, dict) else {}
    warnings: list[str] = []
    discipline = context.get("discipline")
    catalog_disciplines = catalog_taxonomy_for_symbol({"discipline": discipline})["disciplines"] if discipline else []
    preferred_formats = [str(value).strip().upper() for value in context.get("preferredFormats", []) if str(value).strip()]
    preferred_formats = list(dict.fromkeys(preferred_formats))
    interpreted = {
        "application": str(context.get("application") or "").strip() or None,
        "catalogDisciplines": catalog_disciplines,
        "drawingType": str(context.get("drawingType") or "").strip() or None,
        "selectedLayer": str(context.get("selectedLayer") or "").strip() or None,
        "units": str(context.get("units") or "").strip() or None,
        "preferredFormats": preferred_formats,
    }
    return {key: value for key, value in interpreted.items() if value not in (None, [], "")}, warnings


def _contextual_search_score(row, summary: dict, *, query: str, interpreted_filters: dict) -> int:
    searchable = " ".join(
        [
            str(row.slug or ""),
            str(row.canonical_name or ""),
            str(summary.get("summary") or ""),
            " ".join(str(value) for value in (row.payload_json or {}).get("keywords", [])),
        ]
    ).lower()
    score = sum(3 for token in re.findall(r"[a-z0-9]+", query.lower()) if token in searchable)
    requested_disciplines = set(interpreted_filters.get("catalogDisciplines", []))
    score += 10 * len(requested_disciplines.intersection(summary.get("catalogDisciplines", [])))
    requested_formats = set(interpreted_filters.get("preferredFormats", []))
    score += 4 * len(requested_formats.intersection(summary.get("availableFormats", [])))
    selected_layer = str(interpreted_filters.get("selectedLayer") or "").replace("_", " ").lower()
    score += sum(1 for token in re.findall(r"[a-z0-9]+", selected_layer) if token in searchable)
    return score


@router.post("/search")
async def contextual_catalog_search(
    request: Request,
    auth_context: IntegrationAuthContext = Depends(require_catalog_scope(CATALOG_READ_SCOPE)),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    query = str(body.get("query") or "").strip()
    context = body.get("context") or {}
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail="context must be a JSON object.")
    try:
        limit = min(max(int(body.get("limit", 20)), 1), 100)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="limit must be an integer.") from exc

    interpreted_filters, warnings = _contextual_search_context(context)
    discipline = (interpreted_filters.get("catalogDisciplines") or [None])[0]
    filters, params, _ = _catalog_symbol_filters(
        q=None,
        discipline=discipline,
        category=None,
        use_case=None,
        format_=None,
        pack=None,
        symbol_family=None,
        has_preview=None,
        updated_since=None,
    )
    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    params["limit"] = 100
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + where_extension
            + """
            ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name
            LIMIT :limit
            """
        ),
        params,
    ).all()
    ranked = [(_contextual_search_score(row, _catalog_symbol_summary(row), query=query, interpreted_filters=interpreted_filters), row) for row in rows]
    ranked.sort(key=lambda entry: (-entry[0], str(entry[1].canonical_name).lower()))
    items = [_catalog_symbol_summary(row) for _, row in ranked[:limit]]

    requested_formats = interpreted_filters.get("preferredFormats", [])
    available_formats = {format_ for item in items for format_ in item["availableFormats"]}
    for format_ in requested_formats:
        if format_ not in available_formats:
            warnings.append(f"Preferred format {format_} is not available among the ranked results.")
    ranking_explanation = ["Results are ranked by query term matches."]
    if interpreted_filters.get("catalogDisciplines"):
        ranking_explanation.append("Requested discipline is applied as a Catalog filter and ranking preference.")
    if requested_formats:
        ranking_explanation.append("Preferred formats boost matching symbols but do not enable downloads.")
    if interpreted_filters.get("selectedLayer"):
        ranking_explanation.append("Selected layer terms provide an additional ranking signal.")

    response = {
        "query": query,
        "items": items,
        "interpretedFilters": interpreted_filters,
        "rankingExplanation": ranking_explanation,
        "warnings": warnings,
        "downloadAvailable": False,
        "noDownloadNotice": "Symbol downloads are not available through the Catalog integration API.",
    }
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
