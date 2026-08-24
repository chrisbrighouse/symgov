from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .auth import hash_session_token
from .models import Organization, OrganizationMembership, OrganizationRoleAssignment, User, UserSession
from .settings import SymgovAPISettings


NOT_FOUND = "Not found."


@dataclass(frozen=True)
class Stage4Principal:
    user: User
    session: UserSession
    organization: Organization
    membership: OrganizationMembership
    role: OrganizationRoleAssignment

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def is_admin(self) -> bool:
        return self.role.base_role == "admin"


def _fail(status: int, detail: str = NOT_FOUND) -> None:
    raise HTTPException(status_code=status, detail=detail)


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def require_stage4_principal(
    session: Session, request: Request, settings: SymgovAPISettings, *, admin: bool = False
) -> Stage4Principal:
    """Revalidate the cookie-bound authority in the same transaction as domain work."""
    if not settings.organizations_enabled or not settings.symbol_sets_enabled:
        _fail(404)
    token = request.cookies.get("symgov_session", "")
    if not token:
        _fail(401, "Authentication required.")
    token_hash = hash_session_token(token)
    probe = session.query(UserSession).filter(UserSession.token_hash == token_hash).one_or_none()
    if probe is None:
        _fail(401, "Authentication required.")
    now = datetime.now(timezone.utc)
    if probe.revoked_at is not None or _aware(probe.expires_at) <= now or probe.purpose != "application":
        _fail(401, "Authentication required.")
    if probe.session_mode != "organization" or probe.active_organization_id is None:
        _fail(403, "An organization-bound session is required.")
    user = session.query(User).filter(User.id == probe.auth_user_id).with_for_update().one_or_none()
    organization = session.query(Organization).filter(Organization.id == probe.active_organization_id).with_for_update().one_or_none()
    membership = session.query(OrganizationMembership).filter(
        OrganizationMembership.organization_id == probe.active_organization_id,
        OrganizationMembership.user_id == probe.auth_user_id,
        OrganizationMembership.status == "active",
    ).with_for_update().one_or_none()
    role = None
    if membership is not None:
        role = session.query(OrganizationRoleAssignment).filter(
            OrganizationRoleAssignment.membership_id == membership.id,
            OrganizationRoleAssignment.is_active.is_(True),
            OrganizationRoleAssignment.revoked_at.is_(None),
        ).with_for_update().one_or_none()
    pilots = {str(value).strip().lower() for value in settings.organization_pilot_codes if str(value).strip()}
    if (
        user is None or not user.is_active or user.deleted_at is not None
        or organization is None or not organization.is_active or organization.entitlement_status != "active"
        or organization.normalized_code not in pilots or membership is None or role is None
    ):
        _fail(404)
    current = session.query(UserSession).filter(UserSession.id == probe.id, UserSession.token_hash == token_hash).with_for_update().one_or_none()
    if current is None:
        _fail(401, "Authentication required.")
    assert current is not None
    if (
        current.auth_user_id != probe.auth_user_id
        or current.active_organization_id != probe.active_organization_id
        or current.token_hash != probe.token_hash
        or current.purpose != probe.purpose
        or current.session_mode != probe.session_mode
        or current.revoked_at != probe.revoked_at
        or _aware(current.expires_at) != _aware(probe.expires_at)
        or current.revoked_at is not None
        or _aware(current.expires_at) <= now
        or current.purpose != "application"
        or current.session_mode != "organization"
        or current.active_organization_id != organization.id
    ):
        _fail(401, "Authentication required.")
    principal = Stage4Principal(user, current, organization, membership, role)
    if admin and not principal.is_admin:
        _fail(403, "Organization Admin privileges are required.")
    return principal


def route_stage4_principal(request: Request, session: Session, settings: SymgovAPISettings, *, admin: bool = False) -> Stage4Principal:
    return require_stage4_principal(session, request, settings, admin=admin)
