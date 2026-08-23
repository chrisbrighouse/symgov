from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..schemas import APIErrorResponse, APIValidationErrorResponse, PagedSymbolSetResponse, SymbolSetCreateRequest, SymbolSetPatchRequest, SymbolSetResponse
from ..settings import SymgovAPISettings, get_settings
from ..symbol_set_service import create_set, get_set, list_sets, patch_set, set_dict

router = APIRouter(prefix="/org/me/symbol-sets", tags=["symbol-sets"])


def page_args(page: int = Query(1, ge=1), page_size: int = Query(50, alias="pageSize", ge=1, le=200)):
    return page, page_size


@router.get("", response_model=PagedSymbolSetResponse)
def sets(request: Request, page_data=Depends(page_args), status: str | None = None, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    page, page_size = page_data
    _, result = list_sets(session, request, settings, page=page, page_size=page_size, status=status)
    return result


@router.post("", status_code=201, response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def create(data: SymbolSetCreateRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = create_set(session, request, settings, data); session.commit(); return set_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.get("/{set_id}", response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def detail(set_id: uuid.UUID, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    _, row = get_set(session, request, settings, set_id)
    return set_dict(row)


@router.patch("/{set_id}", response_model=SymbolSetResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def update(set_id: uuid.UUID, data: SymbolSetPatchRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = patch_set(session, request, settings, set_id, data); session.commit(); return set_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc
