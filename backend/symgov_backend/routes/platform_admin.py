from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..organization_service import assign_platform_admin, list_platform_admins, revoke_platform_admin
from ..schemas import GrantPlatformAdminRequest, PlatformAdminItem, PlatformAdminListResponse
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(tags=["platform-admin"])


def _require_platform_admin_enabled(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not settings.platform_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


def _admin_item(detail) -> PlatformAdminItem:
    return PlatformAdminItem(
        userId=str(detail.user_id),
        email=detail.user_email,
        displayName=detail.user_display_name,
        userIsActive=detail.user_is_active,
        grantedAt=detail.granted_at.isoformat(),
    )


@router.get(
    "/platform/admins",
    response_model=PlatformAdminListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def list_admins(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> PlatformAdminListResponse:
    admins, total = list_platform_admins(session, page=page, page_size=page_size)
    return PlatformAdminListResponse(
        items=[_admin_item(a) for a in admins],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "/platform/admins",
    response_model=PlatformAdminItem,
    status_code=201,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def grant_admin(
    body: GrantPlatformAdminRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> PlatformAdminItem:
    try:
        actor_id = uuid.UUID(current_user.id)
        user_id = uuid.UUID(body.userId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user ID.") from exc
    try:
        assign_platform_admin(session, user_id=user_id, actor_user_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    admins, _total = list_platform_admins(session, page=1, page_size=200)
    detail = next((a for a in admins if a.user_id == user_id), None)
    if detail is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve granted platform admin.")
    return _admin_item(detail)


@router.delete(
    "/platform/admins/{user_id}",
    status_code=204,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def revoke_admin(
    user_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> None:
    try:
        actor_id = uuid.UUID(current_user.id)
        target_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user ID.") from exc
    try:
        revoke_platform_admin(session, user_id=target_id, actor_user_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
