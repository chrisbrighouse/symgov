from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from ..auth import AuthenticatedUser
from ..dependencies import get_runtime_bridge, require_any_role
from ..runtime import RuntimePersistenceBridge
from ..schemas import APIErrorResponse, ExternalSubmissionRequest, ExternalSubmissionResponse
from ..services.external_submissions import ExternalSubmissionService, SubmissionError
from ..settings import get_settings


router = APIRouter(prefix="/public", tags=["public"])
legacy_router = APIRouter(tags=["public"])


@router.post(
    "/external-submissions",
    response_model=ExternalSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
)
@legacy_router.post(
    "/external-submissions",
    response_model=ExternalSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
    include_in_schema=False,
)
def create_external_submission(
    request: dict[str, Any] = Body(...),
    bridge: RuntimePersistenceBridge = Depends(get_runtime_bridge),
    current_user: AuthenticatedUser = Depends(require_any_role({"admin", "submitter"})),
) -> ExternalSubmissionResponse:
    service = ExternalSubmissionService(
        bridge=bridge,
        db_env_file=get_settings().db_env_file,
        storage_env_file=get_settings().storage_env_file,
    )
    try:
        raw_payload = request.get("request") if isinstance(request.get("request"), dict) else request
        payload: dict[str, Any] = dict(raw_payload or {})
        payload["submitter_name"] = current_user.display_name
        payload["submitter_email"] = current_user.email
        validated_request = ExternalSubmissionRequest.model_validate(payload)
        result = service.submit(validated_request.model_dump())
    except SubmissionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ExternalSubmissionResponse(**result)
