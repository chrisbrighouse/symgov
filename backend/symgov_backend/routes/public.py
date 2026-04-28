from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from ..dependencies import get_runtime_bridge
from ..runtime import RuntimePersistenceBridge
from ..schemas import APIErrorResponse, ExternalSubmissionRequest, ExternalSubmissionResponse
from ..services.external_submissions import ExternalSubmissionService, SubmissionError
from ..settings import get_settings


router = APIRouter(prefix="/public", tags=["public"])
legacy_router = APIRouter(tags=["public"])


@router.post(
    "/external-submissions",
    response_model=ExternalSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
)
@legacy_router.post(
    "/external-submissions",
    response_model=ExternalSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
    include_in_schema=False,
)
def create_external_submission(
    request: dict[str, Any] = Body(...),
    bridge: RuntimePersistenceBridge = Depends(get_runtime_bridge),
) -> ExternalSubmissionResponse:
    settings = get_settings()
    service = ExternalSubmissionService(
        bridge=bridge,
        pin=settings.submission_pin,
        db_env_file=settings.db_env_file,
        storage_env_file=settings.storage_env_file,
    )
    try:
        payload = request.get("request") if isinstance(request.get("request"), dict) else request
        validated_request = ExternalSubmissionRequest.model_validate(payload)
        result = service.submit(validated_request.model_dump())
    except SubmissionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ExternalSubmissionResponse(**result)
