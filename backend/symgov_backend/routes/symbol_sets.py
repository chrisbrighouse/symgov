from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..schemas import (APIErrorResponse, APIValidationErrorResponse, OrganizationDefaultSymbolSetRequest, OrganizationDefaultSymbolSetResponse, PagedSymbolSetResponse,
                       SymbolSetBuilderSearchResponse, SymbolSetCopyRequest, SymbolSetCreateRequest, SymbolSetItemsRequest, SymbolSetItemsResponse,
                       SymbolSetPatchRequest, SymbolSetProjectsRequest, SymbolSetProjectsResponse, SymbolSetResponse)
from ..settings import SymgovAPISettings, get_settings
from ..symbol_set_builder import search_symbol_set_builder
from ..symbol_set_service import (clear_organization_default, copy_set, create_set, get_set, list_items, list_projects_for_set,
                                   list_sets, patch_set, replace_items, replace_projects, set_dict, set_organization_default)

router = APIRouter(prefix="/org/me/symbol-sets", tags=["symbol-sets"])
default_router = APIRouter(prefix="/org/me", tags=["symbol-sets"])


def page_args(page: int = Query(1, ge=1), page_size: int = Query(50, alias="pageSize", ge=1, le=200)):
    return page, page_size


@router.get("", response_model=PagedSymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def sets(request: Request, page_data=Depends(page_args), status: str | None = None, project_id: uuid.UUID | None = Query(None, alias="projectId"), session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    page, page_size = page_data
    _, result = list_sets(session, request, settings, page=page, page_size=page_size, status=status, project_id=project_id)
    return result


# Registered ahead of `/{setId}` (below) so a literal "builder-search"
# path segment is never captured as a `setId` path parameter.
@router.get("/builder-search", response_model=SymbolSetBuilderSearchResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def builder_search(request: Request, page_data=Depends(page_args), q: str | None = Query(None), session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    page, page_size = page_data
    _, result = search_symbol_set_builder(session, request, settings, query_text=q, page=page, page_size=page_size)
    return result


@default_router.put("/default-symbol-set", response_model=OrganizationDefaultSymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def set_default(data: OrganizationDefaultSymbolSetRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    result = set_organization_default(session, request, settings, data.setId); session.commit(); return result


@default_router.delete("/default-symbol-set", status_code=204, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def clear_default(request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    clear_organization_default(session, request, settings); session.commit()


@router.post("/{setId}/copy", status_code=201, response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def copy(setId: uuid.UUID, data: SymbolSetCopyRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = copy_set(session, request, settings, setId, data); session.commit(); return set_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.get("/{setId}/items", response_model=SymbolSetItemsResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def items(setId: uuid.UUID, request: Request, page_data=Depends(page_args), session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    page, page_size = page_data
    _, result = list_items(session, request, settings, setId, page=page, page_size=page_size); return result


@router.put("/{setId}/items", response_model=SymbolSetItemsResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def replace_set_items(setId: uuid.UUID, data: SymbolSetItemsRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        result = replace_items(session, request, settings, setId, data); session.commit(); return result
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.get("/{setId}/projects", response_model=SymbolSetProjectsResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def projects_for_set(setId: uuid.UUID, request: Request, page_data=Depends(page_args), session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    principal, row = get_set(session, request, settings, setId)
    page, page_size = page_data
    return list_projects_for_set(session, row.id, principal, page=page, page_size=page_size)


@router.put("/{setId}/projects", response_model=SymbolSetProjectsResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def replace_set_projects(setId: uuid.UUID, data: SymbolSetProjectsRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        result = replace_projects(session, request, settings, setId, data); session.commit(); return result
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.post("", status_code=201, response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def create(data: SymbolSetCreateRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = create_set(session, request, settings, data); session.commit(); return set_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.get("/{setId}", response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def detail(setId: uuid.UUID, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    _, row = get_set(session, request, settings, setId)
    return set_dict(row)


@router.patch("/{setId}", response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def update(setId: uuid.UUID, data: SymbolSetPatchRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = patch_set(session, request, settings, setId, data); session.commit(); return set_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc
