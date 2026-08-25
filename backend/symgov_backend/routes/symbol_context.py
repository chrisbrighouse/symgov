from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..schemas import (
    APIErrorResponse,
    APIValidationErrorResponse,
    ActiveSetSelectionRequest,
    ProjectSelectionRequest,
    SymbolContextResponse,
)
from ..settings import SymgovAPISettings, get_settings
from ..symbol_context_service import clear_active_set, clear_project, get_context, select_active_set, select_project

router = APIRouter(prefix="/org/me/symbol-context", tags=["symbol-context"])
ERRORS = {
    401: {"model": APIErrorResponse},
    403: {"model": APIErrorResponse},
    404: {"model": APIErrorResponse},
    409: {"model": APIErrorResponse},
    422: {"model": APIValidationErrorResponse},
}


def _reject_query(request: Request) -> None:
    if request.query_params:
        raise RequestValidationError([{
            "type": "extra_forbidden", "loc": ("query",),
            "msg": "Query parameters are not permitted.", "input": str(request.query_params),
        }])


async def _reject_body(request: Request) -> None:
    body = await request.body()
    if body:
        raise RequestValidationError([{
            "type": "extra_forbidden", "loc": ("body",),
            "msg": "Request body is not permitted.", "input": body.decode("utf-8", "replace"),
        }])


@router.get("", response_model=SymbolContextResponse, responses=ERRORS)
def context(request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    _reject_query(request)
    result = get_context(session, request, settings)
    session.commit()
    return result


@router.put("/project", response_model=SymbolContextResponse, responses=ERRORS)
def put_project(data: ProjectSelectionRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    result = select_project(session, request, settings, data.projectId)
    session.commit()
    return result


@router.delete("/project", status_code=204, responses=ERRORS)
async def delete_project(request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    await _reject_body(request)
    _reject_query(request)
    clear_project(session, request, settings)
    session.commit()
    return Response(status_code=204)


@router.put("/active-set", response_model=SymbolContextResponse, responses=ERRORS)
def put_active_set(data: ActiveSetSelectionRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        result = select_active_set(session, request, settings, data.setCode)
    except ValueError as exc:
        raise RequestValidationError([{
            "type": "value_error", "loc": ("body", "setCode"), "msg": str(exc), "input": data.setCode,
        }]) from exc
    session.commit()
    return result


@router.delete("/active-set", response_model=SymbolContextResponse, responses=ERRORS)
async def delete_active_set(request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    await _reject_body(request)
    _reject_query(request)
    result = clear_active_set(session, request, settings)
    session.commit()
    return result
