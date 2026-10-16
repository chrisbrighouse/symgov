"""The Catalog API as an organization key sees it.

An organization key reads the same public Catalog as any key (the existing
`/catalog/...` routes, unchanged) and, here, what only its organization may
read: the organization's own organization-wide private symbols, its Symbol
Sets, its Projects and each Project's effective palette. These routes answer
404 to a personal key, whatever it asks, so a personal key cannot tell they
exist.

Private symbols are a separate namespace on purpose. The public search is one
cursor-paginated SQL over published packs (`PUBLISHED_SYMBOLS_SQL`); private
symbols have no pack, page or Catalog ID, and folding them into that cursor
would change a contract partners already depend on. Responses are built from
named fields, never the stored payload, and carry the symbol's UUID as its ID
because that is the only identifier a private symbol has.

Not here yet: downloading an organization-private symbol. The signed-in
Catalog cannot do that either; both need the same decision first.
"""

from __future__ import annotations

from time import perf_counter
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..catalog_api_auth import IntegrationAuthContext, require_catalog_scope
from ..catalog_organization_context import list_organization_wide_catalog_symbols, resolve_organization_wide_catalog_symbol
from ..dependencies import get_db_session
from ..effective_palette import ORGANIZATION_WIDE_GROUP, _organization_wide_entries, _set_entries
from ..image_content import UnsafeImageContentError, safe_image_response_headers, validate_stored_image
from ..models import GovernedSymbol, Project, ProjectSymbolSet, SymbolRevision, SymbolSet
from ..published_catalog import choose_published_preview_asset
from ..published_preview_authorizations import resolve_authorized_preview_attachment
from ..routes.catalog import CATALOG_READ_SCOPE, _log_successful_catalog_read, _record_catalog_search
from ..runtime import download_object_bytes
from ..settings import SymgovAPISettings, get_settings
from ..symbol_identity import governed_symbol_human_readable_id
from ..symbol_set_service import set_dict

router = APIRouter(prefix="/catalog/organization", tags=["catalog-organization"])

MAX_PAGE = 100


def organization_api_keys_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_api_keys_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


_read_scope = require_catalog_scope(CATALOG_READ_SCOPE)


def require_organization_key(
    context: IntegrationAuthContext = Depends(_read_scope),
) -> uuid.UUID:
    """The organization the key belongs to; 404 for anything that is not an organization key."""
    if context.key_kind != "organization" or not context.organization_id:
        raise HTTPException(status_code=404, detail="Not found.")
    return uuid.UUID(context.organization_id)


def _context(context: IntegrationAuthContext = Depends(_read_scope)) -> IntegrationAuthContext:
    return context


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except ValueError:
        raise HTTPException(status_code=400, detail="cursor is not valid.") from None
    if value < 0:
        raise HTTPException(status_code=400, detail="cursor is not valid.")
    return value


def _symbol_summary(governed: GovernedSymbol, revision: SymbolRevision | None) -> dict:
    payload = (revision.payload_json if revision is not None else None) or {}
    preview = choose_published_preview_asset(payload)
    keywords = payload.get("keywords") or payload.get("search_terms") or []
    return {
        "id": str(governed.id),
        "slug": governed.slug,
        "name": payload.get("name") or payload.get("canonical_name") or governed.canonical_name,
        "category": governed.category,
        "discipline": governed.discipline,
        "summary": payload.get("summary") or payload.get("description") or governed.canonical_name,
        "keywords": [str(word) for word in keywords] if isinstance(keywords, list) else [],
        "revision": revision.revision_label if revision is not None else None,
        "status": revision.lifecycle_state if revision is not None else None,
        "updatedAt": governed.updated_at.isoformat() if governed.updated_at else None,
        "source": "organization_private",
        "previewAvailable": bool(preview and preview.get("object_key")),
        "links": {
            "self": f"/api/v1/catalog/organization/symbols/{governed.id}",
            "preview": f"/api/v1/catalog/organization/symbols/{governed.id}/preview" if preview else None,
        },
    }


@router.get("/symbols")
def list_organization_symbols(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1),
    cursor: str | None = Query(default=None),
    organization_id: uuid.UUID = Depends(require_organization_key),
    auth_context: IntegrationAuthContext = Depends(_context),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    capped = min(limit, MAX_PAGE)
    offset = _offset(cursor)
    rows = list_organization_wide_catalog_symbols(session, organization_id, query=(q or "").strip() or None)
    page = rows[offset:offset + capped]
    response = {
        "items": [_symbol_summary(governed, revision) for governed, revision in page],
        "nextCursor": str(offset + capped) if len(rows) > offset + capped else None,
        "total": len(rows),
    }
    _log_successful_catalog_read(
        session, auth_context, request=request, route_name="catalog_organization_symbols",
        started_at=started_at, query_text=q, result_count=len(page),
    )
    if offset == 0 and (q or "").strip():
        _record_catalog_search(session, auth_context, q, len(rows))
    return response


@router.get("/symbols/{symbol_id}")
def get_organization_symbol(
    symbol_id: str,
    request: Request,
    organization_id: uuid.UUID = Depends(require_organization_key),
    auth_context: IntegrationAuthContext = Depends(_context),
    session: Session = Depends(get_db_session),
) -> dict:
    started_at = perf_counter()
    resolved = resolve_organization_wide_catalog_symbol(session, symbol_id, organization_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Not found.")
    governed, revision = resolved
    _log_successful_catalog_read(
        session, auth_context, request=request, route_name="catalog_organization_symbol_detail",
        started_at=started_at, symbol_ref=str(governed.id), result_count=1, governed_symbol_id=governed.id,
    )
    return _symbol_summary(governed, revision)


@router.get("/symbols/{symbol_id}/preview")
def get_organization_symbol_preview(
    symbol_id: str,
    request: Request,
    organization_id: uuid.UUID = Depends(require_organization_key),
    auth_context: IntegrationAuthContext = Depends(_context),
    session: Session = Depends(get_db_session),
    settings: SymgovAPISettings = Depends(get_settings),
) -> Response:
    started_at = perf_counter()
    resolved = resolve_organization_wide_catalog_symbol(session, symbol_id, organization_id)
    if resolved is None or resolved[1] is None:
        raise HTTPException(status_code=404, detail="Not found.")
    governed, revision = resolved
    asset = choose_published_preview_asset(revision.payload_json or {})
    object_key = asset.get("object_key") if asset else None
    if not object_key:
        raise HTTPException(status_code=404, detail="Not found.")
    attachment = resolve_authorized_preview_attachment(session, revision_id=revision.id, object_key=object_key)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Not found.")
    stored = download_object_bytes(object_key=object_key, env_file=str(settings.storage_env_file))
    try:
        media_type = validate_stored_image(stored["payload"], attachment.content_type, stored.get("content_type"))
    except UnsafeImageContentError:
        raise HTTPException(status_code=404, detail="Not found.") from None
    _log_successful_catalog_read(
        session, auth_context, request=request, route_name="catalog_organization_symbol_preview",
        started_at=started_at, symbol_ref=str(governed.id), result_count=1,
    )
    return Response(content=stored["payload"], media_type=media_type, headers=safe_image_response_headers())


@router.get("/symbol-sets")
def list_organization_symbol_sets(
    limit: int = Query(default=50, ge=1),
    cursor: str | None = Query(default=None),
    organization_id: uuid.UUID = Depends(require_organization_key),
    session: Session = Depends(get_db_session),
) -> dict:
    capped = min(limit, MAX_PAGE)
    offset = _offset(cursor)
    query = session.query(SymbolSet).filter(
        SymbolSet.owner_organization_id == organization_id, SymbolSet.status == "active",
    ).order_by(SymbolSet.normalized_code, SymbolSet.id)
    total = query.count()
    rows = query.offset(offset).limit(capped).all()
    return {
        "items": [set_dict(row) for row in rows],
        "nextCursor": str(offset + capped) if total > offset + capped else None,
        "total": total,
    }


def _active_set_by_code(session: Session, organization_id: uuid.UUID, code: str) -> SymbolSet:
    row = session.query(SymbolSet).filter(
        SymbolSet.owner_organization_id == organization_id,
        SymbolSet.normalized_code == code.strip().lower(),
        SymbolSet.status == "active",
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return row


def _entry_view(session: Session, entry: dict) -> dict:
    governed = entry["_governed"]
    return {
        "id": str(entry["governedSymbolId"]),
        "displayId": governed_symbol_human_readable_id(session, governed),
        "name": entry["displayLabel"] or entry["canonicalName"],
        "category": entry["category"],
        "discipline": entry["discipline"],
        "group": entry["groupName"],
        "sortOrder": entry["sortOrder"],
        "source": entry["source"],
        "preferredFormat": entry["preferredFormat"],
    }


@router.get("/symbol-sets/{code}/items")
def list_organization_symbol_set_items(
    code: str,
    settings: SymgovAPISettings = Depends(get_settings),
    organization_id: uuid.UUID = Depends(require_organization_key),
    session: Session = Depends(get_db_session),
) -> dict:
    symbol_set = _active_set_by_code(session, organization_id, code)
    entries = _set_entries(
        session, symbol_set, organization_id,
        organization_symbols_enabled=bool(getattr(settings, "organization_symbols_enabled", False)),
    )
    return {"symbolSet": set_dict(symbol_set), "items": [_entry_view(session, entry) for entry in entries], "total": len(entries)}


@router.get("/projects")
def list_organization_projects(
    limit: int = Query(default=50, ge=1),
    cursor: str | None = Query(default=None),
    organization_id: uuid.UUID = Depends(require_organization_key),
    session: Session = Depends(get_db_session),
) -> dict:
    capped = min(limit, MAX_PAGE)
    offset = _offset(cursor)
    query = session.query(Project).filter(Project.organization_id == organization_id, Project.status == "active").order_by(
        Project.normalized_code, Project.id
    )
    total = query.count()
    rows = query.offset(offset).limit(capped).all()
    return {
        "items": [{"id": str(row.id), "code": row.code, "name": row.name, "shortDescription": row.short_description} for row in rows],
        "nextCursor": str(offset + capped) if total > offset + capped else None,
        "total": total,
    }


@router.get("/projects/{project_id}/palette")
def get_organization_project_palette(
    project_id: uuid.UUID,
    set_code: str | None = Query(default=None, alias="setCode"),
    settings: SymgovAPISettings = Depends(get_settings),
    organization_id: uuid.UUID = Depends(require_organization_key),
    session: Session = Depends(get_db_session),
) -> dict:
    """A Project's effective palette: the set made available to it, plus the organization-wide symbols.

    A key has no user, so there is no personal set preference to honour: the
    set is the one asked for with `setCode`, else the Project's default, else
    the organization's default, exactly the order the signed-in palette uses
    after a person's own choice.
    """
    project = session.query(Project).filter(
        Project.id == project_id, Project.organization_id == organization_id, Project.status == "active",
    ).one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Not found.")
    available = session.query(SymbolSet, ProjectSymbolSet.is_default).join(
        ProjectSymbolSet, ProjectSymbolSet.symbol_set_id == SymbolSet.id,
    ).filter(
        ProjectSymbolSet.project_id == project.id, ProjectSymbolSet.status == "active",
        SymbolSet.owner_organization_id == organization_id, SymbolSet.status == "active",
    ).all()
    symbol_set, reason = None, "none"
    if set_code is not None:
        symbol_set = next((row for row, _ in available if row.normalized_code == set_code.strip().lower()), None)
        if symbol_set is None:
            raise HTTPException(status_code=404, detail="Not found.")
        reason = "explicit"
    else:
        default = next((row for row, is_default in available if is_default), None)
        if default is not None:
            symbol_set, reason = default, "project_default"
        else:
            from ..models import Organization

            organization = session.get(Organization, organization_id)
            organization_default = next(
                (row for row, _ in available if organization is not None and row.id == organization.default_symbol_set_id), None
            )
            if organization_default is not None:
                symbol_set, reason = organization_default, "organization_default"

    symbols_enabled = bool(getattr(settings, "organization_symbols_enabled", False))
    entries = _set_entries(session, symbol_set, organization_id, organization_symbols_enabled=symbols_enabled)
    if symbols_enabled:
        seen = {entry["governedSymbolId"] for entry in entries}
        start = max((entry["sortOrder"] for entry in entries), default=-1) + 1
        entries.extend(
            entry for entry in _organization_wide_entries(session, organization_id, start_sort_order=start)
            if entry["governedSymbolId"] not in seen
        )
    entries.sort(key=lambda entry: (entry["sortOrder"], str(entry["governedSymbolId"])))
    return {
        "project": {"id": str(project.id), "code": project.code, "name": project.name},
        "activeSet": set_dict(symbol_set) if symbol_set is not None else None,
        "reason": reason,
        "items": [_entry_view(session, entry) for entry in entries],
        "total": len(entries),
        "organizationWideGroup": ORGANIZATION_WIDE_GROUP,
    }
