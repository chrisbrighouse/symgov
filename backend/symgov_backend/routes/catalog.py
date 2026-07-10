from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, Query, Request
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
