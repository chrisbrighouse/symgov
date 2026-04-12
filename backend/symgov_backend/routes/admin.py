from __future__ import annotations

from fastapi import APIRouter

from ..schemas import APIHealthResponse
from ..services.external_submissions import iso_now
from ..settings import get_settings


router = APIRouter(tags=["admin"])
legacy_router = APIRouter(tags=["admin"])


@router.get("/health", response_model=APIHealthResponse)
@legacy_router.get("/health", response_model=APIHealthResponse, include_in_schema=False)
def health() -> APIHealthResponse:
    settings = get_settings()
    return APIHealthResponse(ok=True, service=settings.service_name, time=iso_now())
