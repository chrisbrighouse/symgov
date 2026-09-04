from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..organization_service import (
    add_protected_organization_member,
    assign_platform_admin,
    create_organization_with_initial_admin,
    deactivate_protected_membership,
    get_organization_detail,
    get_platform_admin_detail,
    OrganizationMemberDetail,
    list_organization_member_diagnostics,
    list_organization_members,
    list_organizations,
    list_platform_admins,
    reactivate_membership,
    reactivate_organization,
    replace_protected_membership_base_role,
    revoke_platform_admin,
    suspend_organization,
)
from ..schemas import (
    CreateOrganizationRequest,
    GrantPlatformAdminRequest,
    PlatformAdminItem,
    PlatformAdminListResponse,
    PlatformAddSymgovMemberRequest,
    PlatformDeactivateSymgovMemberRequest,
    PlatformOrganizationItem,
    PlatformOrganizationListResponse,
    PlatformPatchSymgovMemberRequest,
    PlatformReactivateMemberRequest,
    OrgMemberCapabilityItem,
    OrgMemberListResponse,
    OrgMemberResponse,
    OrganizationUsageSummaryResponse,
)
from ..product_usage_rollups import get_organization_usage_summary
from ..settings import SymgovAPISettings, get_settings
from ..models import (
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    User,
)

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


def _get_member_detail(
    session: Session, membership_id: uuid.UUID
) -> OrganizationMemberDetail | None:
    row = session.execute(
        select(OrganizationMembership, User, OrganizationRoleAssignment)
        .join(User, User.id == OrganizationMembership.user_id)
        .join(
            OrganizationRoleAssignment,
            and_(
                OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
                OrganizationRoleAssignment.is_active.is_(True),
            ),
        )
        .where(OrganizationMembership.id == membership_id)
    ).one_or_none()
    if row is None:
        return None
    membership, user, role = row
    capabilities = session.execute(
        select(OrganizationMemberCapability)
        .where(
            OrganizationMemberCapability.membership_id == membership_id,
            OrganizationMemberCapability.is_active.is_(True),
        )
        .order_by(OrganizationMemberCapability.capability)
    ).scalars().all()
    return OrganizationMemberDetail(
        membership_id=membership.id,
        user_id=user.id,
        user_email=user.email,
        user_display_name=user.display_name,
        user_is_active=bool(user.is_active),
        status=membership.status,
        base_role=role.base_role,
        capabilities=tuple(
            (cap.capability, cast(datetime, cap.granted_at)) for cap in capabilities
        ),
        activated_at=membership.activated_at,
        deactivated_at=membership.deactivated_at,
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
        assign_platform_admin(
            session,
            user_id=user_id,
            actor_user_id=actor_id,
            audit_source="api.grant_platform_admin",
            recent_step_up_at=current_user.recent_step_up_at,
        )
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
        revoke_platform_admin(
            session,
            user_id=target_id,
            actor_user_id=actor_id,
            audit_source="api.revoke_platform_admin",
            recent_step_up_at=current_user.recent_step_up_at,
        )
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


@router.get(
    "/platform/organizations/{organization_id}/usage-summary",
    response_model=OrganizationUsageSummaryResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def get_organization_usage_summary_route(
    organization_id: str,
    since: date | None = Query(default=None),
    until: date | None = Query(default=None),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> OrganizationUsageSummaryResponse:
    org_id = _parse_organization_id(organization_id)
    if get_organization_detail(session, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return get_organization_usage_summary(session, org_id, since=since, until=until)


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
            audit_source="api.create_organization",
            recent_step_up_at=current_user.recent_step_up_at,
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
        org = suspend_organization(
            session,
            org_id,
            actor_user_id=uuid.UUID(current_user.id),
            audit_source="api.suspend_organization",
            recent_step_up_at=current_user.recent_step_up_at,
        )
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
        org = reactivate_organization(
            session,
            org_id,
            actor_user_id=uuid.UUID(current_user.id),
            audit_source="api.reactivate_organization",
            recent_step_up_at=current_user.recent_step_up_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _organization_item(org)


# ---------------------------------------------------------------------------
# Stage 3, Slice 3F — Platform Admin protected Symgov member management
# ---------------------------------------------------------------------------


@router.get(
    "/platform/organizations/symgov/members",
    response_model=OrgMemberListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def list_symgov_members(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> OrgMemberListResponse:
    from sqlalchemy import select
    from ..models import Organization
    symgov_org = session.execute(
        select(Organization.id).where(Organization.normalized_code == "symgov")
    ).scalar_one_or_none()
    if symgov_org is None:
        raise HTTPException(status_code=404, detail="Symgov organization not found.")

    members, total = list_organization_members(session, symgov_org, page=page, page_size=page_size)
    return OrgMemberListResponse(
        items=[_member_response(m) for m in members],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "/platform/organizations/symgov/members",
    response_model=OrgMemberResponse,
    status_code=201,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def add_symgov_member(
    body: PlatformAddSymgovMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgMemberResponse:
    try:
        user_id = uuid.UUID(body.userId)
        actor_id = uuid.UUID(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid ID format.") from exc

    symgov_org_id = session.execute(
        select(Organization.id).where(Organization.normalized_code == "symgov")
    ).scalar_one_or_none()
    if symgov_org_id is None:
        raise HTTPException(status_code=404, detail="Symgov organization not found.")

    try:
        membership = add_protected_organization_member(
            session,
            symgov_org_id,
            user_id=user_id,
            base_role=body.baseRole,
            actor_user_id=actor_id,
            reason=body.reason,
            audit_source="api.add_protected_organization_member",
            recent_step_up_at=current_user.recent_step_up_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    detail = _get_member_detail(session, membership.id)
    if detail is None:
         raise HTTPException(status_code=500, detail="Failed to retrieve new membership detail.")
    return _member_response(detail)


@router.patch(
    "/platform/organizations/symgov/members/{membership_id}",
    response_model=OrgMemberResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def patch_symgov_member(
    membership_id: str,
    body: PlatformPatchSymgovMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgMemberResponse:
    mid = _parse_membership_id(membership_id)
    actor_id = uuid.UUID(current_user.id)

    try:
        replace_protected_membership_base_role(
            session,
            membership_id=mid,
            new_base_role=body.baseRole,
            actor_user_id=actor_id,
            reason=body.reason,
            audit_source="api.patch_protected_organization_member",
            recent_step_up_at=current_user.recent_step_up_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    detail = _get_member_detail(session, mid)
    if detail is None:
         raise HTTPException(status_code=404, detail="Membership not found.")
    return _member_response(detail)


@router.post(
    "/platform/organizations/symgov/members/{membership_id}/deactivate",
    status_code=204,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def deactivate_symgov_member(
    membership_id: str,
    body: PlatformDeactivateSymgovMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> None:
    mid = _parse_membership_id(membership_id)
    try:
        deactivate_protected_membership(
            session,
            membership_id=mid,
            actor_user_id=uuid.UUID(current_user.id),
            reason=body.reason,
            audit_source="api.deactivate_protected_organization_member",
            recent_step_up_at=current_user.recent_step_up_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()

@router.get(
    "/platform/organizations/{organization_id}/members",
    response_model=OrgMemberListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def list_organization_members_diagnostics(
    organization_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> OrgMemberListResponse:
    oid = _parse_organization_id(organization_id)
    actor_id = uuid.UUID(current_user.id)

    members, total = list_organization_member_diagnostics(
        session, oid, actor_user_id=actor_id, page=page, page_size=page_size
    )
    return OrgMemberListResponse(
        items=[_member_response(m) for m in members],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "/platform/memberships/{membership_id}/reactivate",
    response_model=OrgMemberResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def reactivate_organization_membership(
    membership_id: str,
    body: PlatformReactivateMemberRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> OrgMemberResponse:
    mid = _parse_membership_id(membership_id)
    actor_id = uuid.UUID(current_user.id)

    try:
        membership = reactivate_membership(
            session,
            membership_id=mid,
            actor_user_id=actor_id,
            reason=body.reason,
            audit_source="api.reactivate_organization_membership",
            recent_step_up_at=current_user.recent_step_up_at,
        )
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detail = _get_member_detail(session, mid)
    if detail is None:
         raise HTTPException(status_code=404, detail="Membership not found after reactivation.")
    return _member_response(detail)
