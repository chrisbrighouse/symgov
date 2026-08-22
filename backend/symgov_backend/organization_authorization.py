from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from .models import Organization, OrganizationMemberCapability, OrganizationMembership, OrganizationRoleAssignment, PlatformRoleAssignment, User
from .settings import SymgovAPISettings


@dataclass(frozen=True)
class EligibleOrganizationMembership:
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    code: str
    display_name: str
    base_role: str
    capabilities: tuple[str, ...]
    is_platform_admin: bool


def resolve_eligible_organization_memberships(
    session: Session, user: User, settings: SymgovAPISettings
) -> tuple[EligibleOrganizationMembership, ...]:
    if not settings.organizations_enabled or not user.is_active or user.deleted_at is not None:
        return ()
    pilots = frozenset(str(code).strip().lower() for code in settings.organization_pilot_codes if str(code).strip())
    if not pilots:
        return ()
    rows = (
        session.query(OrganizationMembership, Organization, OrganizationRoleAssignment)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .join(OrganizationRoleAssignment, OrganizationRoleAssignment.membership_id == OrganizationMembership.id)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.status == "active",
            Organization.is_active.is_(True),
            Organization.entitlement_status == "active",
            Organization.normalized_code.in_(pilots),
            OrganizationRoleAssignment.is_active.is_(True),
            OrganizationRoleAssignment.revoked_at.is_(None),
        )
        .order_by(Organization.normalized_code, OrganizationMembership.id)
        .all()
    )
    platform_admin_active = session.query(PlatformRoleAssignment.id).filter(
        PlatformRoleAssignment.user_id == user.id,
        PlatformRoleAssignment.role == "platform_admin",
        PlatformRoleAssignment.is_active.is_(True),
        PlatformRoleAssignment.revoked_at.is_(None),
    ).first() is not None
    resolved = []
    for membership, organization, role in rows:
        capabilities = tuple(value for (value,) in session.query(OrganizationMemberCapability.capability).filter(
            OrganizationMemberCapability.membership_id == membership.id,
            OrganizationMemberCapability.is_active.is_(True),
            OrganizationMemberCapability.revoked_at.is_(None),
        ).order_by(OrganizationMemberCapability.capability).all())
        resolved.append(EligibleOrganizationMembership(
            membership_id=membership.id,
            organization_id=organization.id,
            code=organization.code,
            display_name=organization.display_name,
            base_role=role.base_role,
            capabilities=capabilities,
            is_platform_admin=(organization.normalized_code == "symgov" and role.base_role == "admin" and platform_admin_active),
        ))
    return tuple(resolved)


def resolve_bound_organization_context(
    session: Session, user: User, organization_id: uuid.UUID, settings: SymgovAPISettings
) -> EligibleOrganizationMembership | None:
    return next((item for item in resolve_eligible_organization_memberships(session, user, settings) if item.organization_id == organization_id), None)