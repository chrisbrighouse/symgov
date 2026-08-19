from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import (
    get_db_session,
    require_organization_admin,
    require_organization_session,
    require_recent_step_up,
)
from ..organization_service import (
    add_organization_member,
    deactivate_membership,
    get_organization_detail,
    grant_member_capability,
    list_organization_members,
    replace_membership_base_role,
    revoke_member_capability,
    update_organization,
)
from ..schemas import (
    OrgAddMemberRequest,
    OrgDetailResponse,
    OrgMemberCapabilityItem,
    OrgMemberListResponse,
    OrgMemberResponse,
    OrgPatchMemberRequest,
    OrgUpdateRequest,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(tags=["organizations"])

ORG_ICON_PATH = "/org/me/icon"


def _require_org_admin_enabled(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not settings.organization_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


def _active_org_id(current_user: AuthenticatedUser) -> uuid.UUID:
    if current_user.active_organization_id is None:
        raise HTTPException(status_code=403, detail="An organization-bound session is required.")
    try:
        return uuid.UUID(current_user.active_organization_id)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=403, detail="Invalid organization session.") from exc


def _parse_membership_id(membership_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(membership_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Membership not found.") from exc


def _member_response(detail) -> OrgMemberResponse:
    return OrgMemberResponse(
        membershipId=str(detail.membership_id),
        userId=str(detail.user_id),
        email=detail.user_email,
        displayName=detail.user_display_name,
        userIsActive=detail.user_is_active,
        status=detail.status,
        baseRole=detail.base_role,
        capabilities=[
            OrgMemberCapabilityItem(capability=cap, grantedAt=granted_at.isoformat())
            for cap, granted_at in detail.capabilities
        ],
        activatedAt=detail.activated_at.isoformat() if detail.activated_at else None,
        deactivatedAt=detail.deactivated_at.isoformat() if detail.deactivated_at else None,
    )


@router.get(
    "/org/me",
    response_model=OrgDetailResponse,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def get_org_detail(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrgDetailResponse:
    org_id = _active_org_id(current_user)
    org = get_organization_detail(session, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return OrgDetailResponse(
        id=str(org.id),
        code=org.code,
        displayName=org.display_name,
        legalName=org.legal_name,
        locale=org.locale,
        entitlementStatus=org.entitlement_status,
        isActive=bool(org.is_active),
        isProtected=bool(org.is_protected),
        iconUrl=f"/api/v1{ORG_ICON_PATH}",
    )


@router.patch(
    "/org/me",
    response_model=OrgDetailResponse,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def patch_org(
    body: OrgUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgDetailResponse:
    org_id = _active_org_id(current_user)
    try:
        org = update_organization(
            session,
            org_id,
            actor_user_id=uuid.UUID(current_user.id),
            display_name=body.displayName,
            legal_name=body.legalName,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return OrgDetailResponse(
        id=str(org.id),
        code=org.code,
        displayName=org.display_name,
        legalName=org.legal_name,
        locale=org.locale,
        entitlementStatus=org.entitlement_status,
        isActive=bool(org.is_active),
        isProtected=bool(org.is_protected),
        iconUrl=f"/api/v1{ORG_ICON_PATH}",
    )


@router.get(
    "/org/me/members",
    response_model=OrgMemberListResponse,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def list_members(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> OrgMemberListResponse:
    org_id = _active_org_id(current_user)
    members, total = list_organization_members(session, org_id, page=page, page_size=page_size)
    return OrgMemberListResponse(
        items=[_member_response(m) for m in members],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "/org/me/members",
    response_model=OrgMemberResponse,
    status_code=201,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def add_member(
    body: OrgAddMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgMemberResponse:
    org_id = _active_org_id(current_user)
    try:
        actor_id = uuid.UUID(current_user.id)
        user_id = uuid.UUID(body.userId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user ID.") from exc
    try:
        membership = add_organization_member(
            session,
            org_id,
            user_id=user_id,
            base_role=body.baseRole,
            actor_user_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    # Re-fetch through list to get full denormalized view (user email/name, role, capabilities)
    from sqlalchemy import select as sa_select
    from ..models import OrganizationMembership as _OM, User as _U, OrganizationRoleAssignment as _ORA
    row = session.execute(
        sa_select(_OM, _U, _ORA)
        .join(_U, _U.id == _OM.user_id)
        .join(_ORA, (_ORA.membership_id == _OM.id) & _ORA.is_active.is_(True))
        .where(_OM.id == membership.id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve new membership.")
    m, u, r = row
    from ..organization_service import OrganizationMemberDetail
    detail = OrganizationMemberDetail(
        membership_id=m.id,
        user_id=u.id,
        user_email=u.email,
        user_display_name=u.display_name,
        user_is_active=bool(u.is_active),
        status=m.status,
        base_role=r.base_role,
        capabilities=(),
        activated_at=m.activated_at,
        deactivated_at=m.deactivated_at,
    )
    return _member_response(detail)


@router.patch(
    "/org/me/members/{membership_id}",
    response_model=OrgMemberResponse,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def patch_member(
    membership_id: str,
    body: OrgPatchMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgMemberResponse:
    org_id = _active_org_id(current_user)
    mid = _parse_membership_id(membership_id)
    actor_id = uuid.UUID(current_user.id)

    # Verify membership belongs to actor's org before any mutation
    from sqlalchemy import select as sa_select
    from ..models import OrganizationMembership as _OM
    check = session.execute(
        sa_select(_OM.organization_id).where(_OM.id == mid)
    ).scalar_one_or_none()
    if check is None:
        raise HTTPException(status_code=404, detail="Membership not found.")
    if check != org_id:
        raise HTTPException(status_code=404, detail="Membership not found.")

    try:
        if body.baseRole is not None:
            replace_membership_base_role(
                session,
                membership_id=mid,
                new_base_role=body.baseRole,
                actor_user_id=actor_id,
            )
        if body.grantCapability is not None:
            grant_member_capability(
                session,
                mid,
                capability=body.grantCapability,
                actor_user_id=actor_id,
                organization_id=org_id,
            )
        if body.revokeCapability is not None:
            revoke_member_capability(
                session,
                mid,
                capability=body.revokeCapability,
                actor_user_id=actor_id,
                organization_id=org_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    from sqlalchemy import select as sa_select, and_ as sa_and
    from ..models import OrganizationMembership as _OM, User as _U, OrganizationRoleAssignment as _ORA, OrganizationMemberCapability as _OMC
    row = session.execute(
        sa_select(_OM, _U, _ORA)
        .join(_U, _U.id == _OM.user_id)
        .join(_ORA, sa_and(_ORA.membership_id == _OM.id, _ORA.is_active.is_(True)))
        .where(_OM.id == mid)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Membership not found.")
    m, u, r = row
    caps = session.execute(
        sa_select(_OMC)
        .where(_OMC.membership_id == mid, _OMC.is_active.is_(True))
        .order_by(_OMC.capability)
    ).scalars().all()
    from ..organization_service import OrganizationMemberDetail
    detail = OrganizationMemberDetail(
        membership_id=m.id,
        user_id=u.id,
        user_email=u.email,
        user_display_name=u.display_name,
        user_is_active=bool(u.is_active),
        status=m.status,
        base_role=r.base_role,
        capabilities=tuple((c.capability, c.granted_at) for c in caps),
        activated_at=m.activated_at,
        deactivated_at=m.deactivated_at,
    )
    return _member_response(detail)


@router.delete(
    "/org/me/members/{membership_id}",
    status_code=204,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def remove_member(
    membership_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> None:
    org_id = _active_org_id(current_user)
    mid = _parse_membership_id(membership_id)
    actor_id = uuid.UUID(current_user.id)

    from sqlalchemy import select as sa_select
    from ..models import OrganizationMembership as _OM
    check = session.execute(
        sa_select(_OM.organization_id).where(_OM.id == mid)
    ).scalar_one_or_none()
    if check is None:
        raise HTTPException(status_code=404, detail="Membership not found.")
    if check != org_id:
        raise HTTPException(status_code=404, detail="Membership not found.")

    try:
        deactivate_membership(session, membership_id=mid, actor_user_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()


@router.get(
    ORG_ICON_PATH,
    dependencies=[Depends(_require_org_admin_enabled)],
)
def get_org_icon(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_session),
) -> Response:
    org_id = _active_org_id(current_user)
    org = get_organization_detail(session, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return Response(
        content=org.fallback_icon_svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )
