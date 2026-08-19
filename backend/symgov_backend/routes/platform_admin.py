from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..organization_service import (
    assign_platform_admin,
    create_organization_with_initial_admin,
    get_organization_detail,
    get_platform_admin_detail,
    list_organizations,
    list_platform_admins,
    reactivate_organization,
    revoke_platform_admin,
    suspend_organization,
)
from ..schemas import (
    CreateOrganizationRequest,
    GrantPlatformAdminRequest,
    PlatformAdminItem,
    PlatformAdminListResponse,
    PlatformOrganizationItem,
    PlatformOrganizationListResponse,
)
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


def _organization_item(org) -> PlatformOrganizationItem:
    return PlatformOrganizationItem(
        id=str(org.id),
        code=org.code,
        displayName=org.display_name,
        legalName=org.legal_name,
        entitlementStatus=org.entitlement_status,
        isActive=bool(org.is_active),
        isProtected=bool(org.is_protected),
    )


def _parse_organization_id(organization_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Organization not found.") from exc


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
    detail = get_platform_admin_detail(session, user_id)
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


@router.get(
    "/platform/organizations",
    response_model=PlatformOrganizationListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def list_organizations_route(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> PlatformOrganizationListResponse:
    try:
        orgs, total = list_organizations(
            session, actor_user_id=uuid.UUID(current_user.id), page=page, page_size=page_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return PlatformOrganizationListResponse(
        items=[_organization_item(o) for o in orgs],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "/platform/organizations",
    response_model=PlatformOrganizationItem,
    status_code=201,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def create_organization_route(
    body: CreateOrganizationRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> PlatformOrganizationItem:
    try:
        actor_id = uuid.UUID(current_user.id)
        initial_admin_id = uuid.UUID(body.initialAdminUserId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user ID.") from exc
    try:
        result = create_organization_with_initial_admin(
            session,
            code=body.code,
            display_name=body.displayName,
            legal_name=body.legalName,
            locale=body.locale,
            initial_admin_user_id=initial_admin_id,
            actor_user_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _organization_item(result.organization)


@router.post(
    "/platform/organizations/{organization_id}/suspend",
    response_model=PlatformOrganizationItem,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def suspend_organization_route(
    organization_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> PlatformOrganizationItem:
    org_id = _parse_organization_id(organization_id)
    if get_organization_detail(session, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    try:
        org = suspend_organization(session, org_id, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _organization_item(org)


@router.post(
    "/platform/organizations/{organization_id}/reactivate",
    response_model=PlatformOrganizationItem,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def reactivate_organization_route(
    organization_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> PlatformOrganizationItem:
    org_id = _parse_organization_id(organization_id)
    if get_organization_detail(session, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    try:
        org = reactivate_organization(session, org_id, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _organization_item(org)
