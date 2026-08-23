from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..project_service import create_project, get_project, list_projects, patch_project, project_dict
from ..schemas import APIErrorResponse, APIValidationErrorResponse, PagedProjectResponse, ProjectCreateRequest, ProjectPatchRequest, ProjectResponse
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(prefix="/org/me/projects", tags=["projects"])


def stage4_route_guard(settings: SymgovAPISettings = Depends(get_settings)):
    if not (settings.organizations_enabled and settings.symbol_sets_enabled):
        raise HTTPException(404, "Not found.")


def page_args(page: int = Query(1, ge=1), page_size: int = Query(50, alias="pageSize", ge=1, le=200)):
    return page, page_size


@router.get("", response_model=PagedProjectResponse)
def projects(request: Request, page_data=Depends(page_args), include_closed: bool = False, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    page, page_size = page_data
    _, result = list_projects(session, request, settings, page=page, page_size=page_size, include_closed=include_closed)
    return result


@router.post("", status_code=201, response_model=ProjectResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def create(data: ProjectCreateRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = create_project(session, request, settings, data); session.commit(); return project_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc


@router.get("/{project_id}", response_model=ProjectResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def detail(project_id: uuid.UUID, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    _, row = get_project(session, request, settings, project_id)
    return project_dict(row)


@router.patch("/{project_id}", response_model=ProjectResponse, responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 404: {"model": APIErrorResponse}, 409: {"model": APIErrorResponse}, 422: {"model": APIValidationErrorResponse}})
def update(project_id: uuid.UUID, data: ProjectPatchRequest, request: Request, session: Session = Depends(get_db_session), settings: SymgovAPISettings = Depends(get_settings)):
    try:
        row = patch_project(session, request, settings, project_id, data); session.commit(); return project_dict(row)
    except ValueError as exc: raise RequestValidationError([{"loc": ("body",), "msg": str(exc), "type": "value_error"}]) from exc
