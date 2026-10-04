from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_platform_admin, require_recent_step_up
from ..models import Organization
from ..organization_member_roles import (
    ORG_SCOPED_ROLES,
    grant_member_role,
    list_organization_member_roles,
    revoke_member_role,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(tags=["platform-admin"])


def _require_platform_admin_enabled(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not settings.platform_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


class MemberRoleItem(BaseModel):
    membershipId: str
    email: str
    displayName: str
    role: str
    assignedAt: str


class MemberRoleListResponse(BaseModel):
    organizationId: str
    assignableRoles: list[str]
    items: list[MemberRoleItem]


class MemberRoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=1000)


def _parse_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found.") from exc


def _listing(session: Session, organization_id: uuid.UUID) -> MemberRoleListResponse:
    return MemberRoleListResponse(
        organizationId=str(organization_id),
        assignableRoles=list(ORG_SCOPED_ROLES),
        items=[
            MemberRoleItem(
                membershipId=str(membership_id), email=email, displayName=display_name, role=role,
                assignedAt=assigned_at.isoformat(),
            )
            for membership_id, email, display_name, role, assigned_at in list_organization_member_roles(session, organization_id)
        ],
    )


def _require_organization(session: Session, organization_id: uuid.UUID) -> None:
    if session.get(Organization, organization_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found.")


@router.get(
    "/platform/organizations/{organization_id}/member-roles",
    response_model=MemberRoleListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def list_member_roles_route(
    organization_id: str,
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> MemberRoleListResponse:
    org_id = _parse_uuid(organization_id, "Organization")
    _require_organization(session, org_id)
    return _listing(session, org_id)


@router.put(
    "/platform/organizations/{organization_id}/members/{membership_id}/roles/{role}",
    response_model=MemberRoleListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def grant_member_role_route(
    organization_id: str,
    membership_id: str,
    role: str,
    payload: MemberRoleChangeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> MemberRoleListResponse:
    org_id = _parse_uuid(organization_id, "Organization")
    _require_organization(session, org_id)
    try:
        grant_member_role(
            session, org_id, _parse_uuid(membership_id, "Membership"), role=role,
            actor_id=uuid.UUID(current_user.id), reason=payload.reason,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _listing(session, org_id)


@router.post(
    "/platform/organizations/{organization_id}/members/{membership_id}/roles/{role}/revoke",
    response_model=MemberRoleListResponse,
    dependencies=[Depends(_require_platform_admin_enabled)],
)
def revoke_member_role_route(
    organization_id: str,
    membership_id: str,
    role: str,
    payload: MemberRoleChangeRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> MemberRoleListResponse:
    org_id = _parse_uuid(organization_id, "Organization")
    _require_organization(session, org_id)
    try:
        revoke_member_role(
            session, org_id, _parse_uuid(membership_id, "Membership"), role=role,
            actor_id=uuid.UUID(current_user.id), reason=payload.reason,
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _listing(session, org_id)
