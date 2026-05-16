from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..models import Attachment
from ..runtime import download_object_bytes
from ..settings import get_settings


router = APIRouter(prefix="/published", tags=["published"])
legacy_router = APIRouter(tags=["published"])


PUBLISHED_SYMBOLS_SQL = """
    SELECT
        gs.id::text AS symbol_id,
        gs.slug,
        gs.canonical_name,
        gs.category,
        gs.discipline,
        sr.id::text AS symbol_revision_id,
        sr.revision_label,
        sr.payload_json,
        sr.rationale,
        pp.id::text AS page_id,
        pp.page_code,
        pp.title AS page_title,
        pp.effective_date,
        pk.id::text AS pack_id,
        pk.pack_code,
        pk.title AS pack_title,
        pk.audience,
        pe.sort_order
    FROM published_pages pp
    JOIN publication_packs pk ON pk.id = pp.pack_id
    JOIN pack_entries pe ON pe.pack_id = pk.id
        AND pe.published_page_id = pp.id
        AND pe.symbol_revision_id = pp.current_symbol_revision_id
    JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
    JOIN governed_symbols gs ON gs.id = sr.symbol_id
    WHERE pk.status = 'published'
        AND pk.audience = 'public'
        AND sr.lifecycle_state = 'published'
"""


def published_symbol_row(row) -> dict:
    payload = row.payload_json or {}
    keywords = payload.get("keywords") or payload.get("search_terms") or []
    if not isinstance(keywords, list):
        keywords = []
    downloads = payload.get("downloads") or []
    if not isinstance(downloads, list):
        downloads = []

    return {
        "id": row.slug,
        "symbolId": row.symbol_id,
        "displayName": payload.get("display_name") or payload.get("workspace_display_name"),
        "packageDisplayId": payload.get("package_display_id"),
        "packageSymbolSequence": payload.get("package_symbol_sequence"),
        "slug": row.slug,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "category": row.category,
        "discipline": row.discipline,
        "revisionId": row.symbol_revision_id,
        "revision": row.revision_label,
        "status": "Published",
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "rationale": row.rationale or "",
        "effectiveDate": row.effective_date.isoformat(),
        "pageId": row.page_id,
        "pageCode": row.page_code,
        "pageTitle": row.page_title,
        "packId": row.pack_id,
        "packCode": row.pack_code,
        "pack": row.pack_title,
        "keywords": keywords,
        "downloads": downloads,
        "sortOrder": row.sort_order,
        "previewUrl": f"/api/v1/published/symbols/{row.slug}/preview"
        if payload.get("source_object_key")
        else None,
        "payload": payload,
    }


def pack_row(row) -> dict:
    return {
        "id": row.id,
        "packCode": row.pack_code,
        "title": row.title,
        "audience": row.audience,
        "effectiveDate": row.effective_date.isoformat(),
        "status": row.status,
        "symbolCount": row.symbol_count,
    }


@router.get("/symbols")
@legacy_router.get("/published/symbols", include_in_schema=False)
def list_published_symbols(
    q: str | None = Query(default=None),
    pack: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict:
    filters = []
    params = {}
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
            )
            """
        )
        params["query"] = f"%{q}%"
    if pack:
        filters.append("(pk.pack_code = :pack OR pk.id::text = :pack)")
        params["pack"] = pack

    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + where_extension
            + " ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name"
        ),
        params,
    ).all()
    return {"items": [published_symbol_row(row) for row in rows]}


@router.get("/symbols/{symbol_id}")
@legacy_router.get("/published/symbols/{symbol_id}", include_in_schema=False)
def get_published_symbol(symbol_id: str, session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug = :symbol_id OR gs.id::text = :symbol_id)
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published symbol was not found.")
    return {"item": published_symbol_row(rows[0])}


@router.get("/symbols/{symbol_id}/preview")
@legacy_router.get("/published/symbols/{symbol_id}/preview", include_in_schema=False)
def get_published_symbol_preview(symbol_id: str, session: Session = Depends(get_db_session)) -> Response:
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug = :symbol_id OR gs.id::text = :symbol_id)
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published symbol was not found.")

    payload_json = rows[0].payload_json or {}
    object_key = payload_json.get("source_object_key")
    if not object_key:
        raise HTTPException(status_code=404, detail="Published symbol preview was not found.")

    attachment = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)


@router.get("/pages/{page_code}")
@legacy_router.get("/published/pages/{page_code}", include_in_schema=False)
def get_published_page(page_code: str, session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(PUBLISHED_SYMBOLS_SQL + " AND pp.page_code = :page_code LIMIT 1"),
        {"page_code": page_code},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published page was not found.")
    return {"item": published_symbol_row(rows[0])}


@router.get("/packs")
@legacy_router.get("/published/packs", include_in_schema=False)
def list_published_packs(session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(
            """
            SELECT
                pk.id::text AS id,
                pk.pack_code,
                pk.title,
                pk.audience,
                pk.effective_date,
                pk.status,
                count(pe.id)::int AS symbol_count
            FROM publication_packs pk
            LEFT JOIN pack_entries pe ON pe.pack_id = pk.id
            WHERE pk.status = 'published'
                AND pk.audience = 'public'
            GROUP BY pk.id, pk.pack_code, pk.title, pk.audience, pk.effective_date, pk.status
            ORDER BY pk.effective_date DESC, pk.pack_code
            """
        )
    ).all()
    return {"items": [pack_row(row) for row in rows]}
