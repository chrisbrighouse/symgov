from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from sqlalchemy import and_, func, or_, select, text

from symgov_backend.models import (
    AuditEvent,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    User,
    UserSession,
)
from symgov_backend.organization_icons import (
    ICON_UPLOAD_MIN_INTERVAL_SECONDS,
    generate_organization_fallback_icon,
)
from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


COMMERCIAL_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{1,31}$")
BASE_ROLES = frozenset({"admin", "user"})
MAX_LOGIN_MEMBERSHIP_CHOICES = 100
MAX_DUPLICATE_WARNINGS = 10
ADMINISTRATION_LOCK_KEY = (0x53594D47, 0x4F524731)


class OrganizationDuplicateWarning(NamedTuple):
    organization_id: uuid.UUID
    organization_code: str
    matched_fields: tuple[str, ...]


class OrganizationCreationResult(NamedTuple):
    organization: Organization
    membership: OrganizationMembership
    duplicate_warnings: tuple[OrganizationDuplicateWarning, ...]


class OrganizationMembershipChoice(NamedTuple):
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    organization_code: str


class BootstrapSummary(TypedDict):
    apply: bool
    created: bool
    changed: bool
    actions: list[str]


VALID_CAPABILITIES = frozenset({"contributor", "symbol_reviewer"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _emit_audit(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID | None,
    payload: dict,
) -> None:
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            payload_json=payload,
            created_at=_utc_now(),
        )
    )


def _normalize_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


def normalize_organization_code(code: str) -> str:
    if not isinstance(code, str) or not COMMERCIAL_CODE_PATTERN.fullmatch(code):
        raise ValueError(
            "Commercial organization code must start with an uppercase letter and contain "
            "only uppercase alphanumeric characters or hyphens (2-32 characters)."
        )
    normalized_code = code.lower()
    if normalized_code == "symgov":
        raise ValueError("The reserved organization code 'symgov' is bootstrap-only.")
    return normalized_code


def _acquire_administration_lock(session: Session) -> None:
    """Enter the shared database administration order before taking row locks."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:classid, :objid)"),
            {"classid": ADMINISTRATION_LOCK_KEY[0], "objid": ADMINISTRATION_LOCK_KEY[1]},
        )


def _locked_active_users(
    session: Session,
    user_labels: dict[uuid.UUID, str],
) -> dict[uuid.UUID, User]:
    """Lock every participating User row in deterministic UUID order."""
    ordered_ids = sorted(user_labels, key=str)
    users = {
        user.id: user
        for user in session.execute(
            select(User).where(User.id.in_(ordered_ids)).order_by(User.id).with_for_update()
        ).scalars()
    }
    for user_id in ordered_ids:
        user = users.get(user_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            raise ValueError(f"{user_labels[user_id]} must be an active user.")
    return users


def _locked_active_user(session: Session, user_id: uuid.UUID, *, label: str) -> User:
    return _locked_active_users(session, {user_id: label})[user_id]


def _symgov_admin_membership(session: Session, user_id: uuid.UUID, *, lock: bool) -> OrganizationMembership | None:
    statement = (
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .join(
            OrganizationRoleAssignment,
            OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
        )
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
            Organization.is_active.is_(True),
            Organization.entitlement_status == "active",
            Organization.is_protected.is_(True),
            Organization.code == "symgov",
            Organization.normalized_code == "symgov",
            OrganizationRoleAssignment.base_role == "admin",
            OrganizationRoleAssignment.is_active.is_(True),
        )
    )
    if lock:
        statement = statement.with_for_update(of=OrganizationMembership)
    membership = session.execute(statement).scalar_one_or_none()
    if membership is not None and lock:
        _active_base_role(session, membership.id, lock=True)
    return membership


def _active_platform_assignment(
    session: Session,
    user_id: uuid.UUID,
    *,
    lock: bool,
) -> PlatformRoleAssignment | None:
    statement = select(PlatformRoleAssignment).where(
        PlatformRoleAssignment.user_id == user_id,
        PlatformRoleAssignment.role == "platform_admin",
        PlatformRoleAssignment.is_active.is_(True),
    )
    if lock:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def _require_effective_platform_admin(
    session: Session,
    user_id: uuid.UUID,
    *,
    user_locked: bool = False,
) -> None:
    if not user_locked:
        _locked_active_user(session, user_id, label="Actor")
    symgov_org = session.execute(
        select(Organization)
        .where(Organization.normalized_code == "symgov")
        .with_for_update()
    ).scalar_one_or_none()
    if symgov_org is None:
        raise ValueError("The protected Symgov organization must exist.")
    if _active_platform_assignment(session, user_id, lock=False) is None:
        raise ValueError("Actor must be an active Platform Administrator.")
    if _symgov_admin_membership(session, user_id, lock=True) is None:
        raise ValueError("Actor must remain an active Symgov Organization Admin.")
    if _active_platform_assignment(session, user_id, lock=True) is None:
        raise ValueError("Actor must be an active Platform Administrator.")


def _locked_membership(session: Session, membership_id: uuid.UUID) -> OrganizationMembership:
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")
    organization = session.execute(
        select(Organization)
        .where(Organization.id == membership_probe.organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if organization is None:
        raise ValueError("Membership organization not found.")
    membership = session.execute(
        select(OrganizationMembership)
        .where(OrganizationMembership.id == membership_id)
        .with_for_update()
    ).scalar_one_or_none()
    if membership is None:
        raise ValueError("Membership not found.")
    return membership


def _active_base_role(
    session: Session,
    membership_id: uuid.UUID,
    *,
    lock: bool,
) -> OrganizationRoleAssignment | None:
    statement = select(OrganizationRoleAssignment).where(
        OrganizationRoleAssignment.membership_id == membership_id,
        OrganizationRoleAssignment.is_active.is_(True),
    )
    if lock:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def _active_admin_membership_ids(
    session: Session,
    organization_id: uuid.UUID,
) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(OrganizationMembership.id)
            .join(User, User.id == OrganizationMembership.user_id)
            .join(
                OrganizationRoleAssignment,
                OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
            )
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == "active",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                OrganizationRoleAssignment.base_role == "admin",
                OrganizationRoleAssignment.is_active.is_(True),
            )
            .order_by(OrganizationMembership.id)
            .with_for_update(of=OrganizationMembership)
        ).scalars()
    )


def _protect_platform_admin_eligibility(
    session: Session,
    membership: OrganizationMembership,
) -> None:
    organization = session.get(Organization, membership.organization_id)
    if organization is None:
        raise ValueError("Membership organization not found.")
    if organization.normalized_code != "symgov":
        return
    if _active_platform_assignment(session, membership.user_id, lock=True) is not None:
        raise ValueError(
            "An active Platform Administrator cannot lose Symgov Organization Admin eligibility; "
            "revoke the platform role first."
        )


def reconcile_symgov_organization_bootstrap(
    session: Session,
    *,
    apply: bool = False,
) -> BootstrapSummary:
    """Inventory or reconcile the protected Symgov organization without committing."""
    now = _utc_now()
    summary: BootstrapSummary = {
        "apply": apply,
        "created": False,
        "changed": False,
        "actions": [],
    }
    if apply:
        _acquire_administration_lock(session)

    owner_statement = select(User).where(func.lower(User.email) == PROTECTED_OWNER_EMAIL)
    if apply:
        owner_statement = owner_statement.with_for_update()
    owner = session.execute(owner_statement).scalar_one_or_none()
    if owner is None or not owner.is_active or owner.deleted_at is not None:
        summary["actions"].append("protected owner active account required")
        if apply:
            raise ValueError("An active protected owner account is required before bootstrap apply.")
        return summary

    organization_statement = select(Organization).where(
        Organization.normalized_code == "symgov"
    )
    if apply:
        organization_statement = organization_statement.with_for_update()
    symgov_org = session.execute(organization_statement).scalar_one_or_none()
    if symgov_org is None:
        summary["actions"].append("create organization symgov")
        if apply:
            organization_id = uuid.uuid4()
            symgov_org = Organization(
                id=organization_id,
                code="symgov",
                normalized_code="symgov",
                display_name="Symgov Platform",
                legal_name="Symgov Governance",
                name_key=_normalize_name("Symgov Platform", field_name="display_name"),
                legal_name_key=_normalize_name("Symgov Governance", field_name="legal_name"),
                locale="en-US",
                entitlement_status="active",
                is_active=True,
                is_protected=True,
                fallback_icon_svg=generate_organization_fallback_icon(organization_id),
                created_at=now,
                updated_at=now,
            )
            session.add(symgov_org)
            session.flush()
            summary["created"] = True

    if symgov_org is None:  # Defensive: apply=False returned no row but did not create one.
        return summary

    if not symgov_org.is_active:
        summary["actions"].append("reactivate protected Symgov organization")
        if apply:
            symgov_org.is_active = True
            symgov_org.updated_at = now
            summary["changed"] = True
    if symgov_org.entitlement_status != "active":
        summary["actions"].append("restore protected Symgov organization entitlement")
        if apply:
            symgov_org.entitlement_status = "active"
            symgov_org.updated_at = now
            summary["changed"] = True
    if apply:
        session.flush()

    membership_statement = select(OrganizationMembership).where(
        OrganizationMembership.organization_id == symgov_org.id,
        OrganizationMembership.user_id == owner.id,
    )
    if apply:
        membership_statement = membership_statement.with_for_update()
    membership = session.execute(membership_statement).scalar_one_or_none()
    if membership is None:
        summary["actions"].append("activate protected owner Symgov membership")
        if apply:
            membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=symgov_org.id,
                user_id=owner.id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(membership)
            session.flush()
            summary["changed"] = True
    elif membership.status != "active":
        summary["actions"].append("reactivate protected owner Symgov membership")
        if apply:
            membership.status = "active"
            if membership.activated_at is None:
                membership.activated_at = now
            membership.updated_at = now
            summary["changed"] = True

    if membership is not None:
        active_role = _active_base_role(session, membership.id, lock=apply)
        if active_role is None or active_role.base_role != "admin":
            summary["actions"].append("assign protected owner Symgov admin role")
            if apply:
                if active_role is not None:
                    active_role.is_active = False
                    active_role.revoked_at = now
                    active_role.revoke_reason = "bootstrap_reconciliation"
                session.add(
                    OrganizationRoleAssignment(
                        id=uuid.uuid4(),
                        membership_id=membership.id,
                        base_role="admin",
                        is_active=True,
                        assigned_at=now,
                        assigned_by_user_id=None,
                    )
                )
                session.flush()
                summary["changed"] = True

    platform_role = _active_platform_assignment(session, owner.id, lock=apply)
    if platform_role is None:
        summary["actions"].append("assign protected owner platform admin role")
        if apply:
            session.add(
                PlatformRoleAssignment(
                    id=uuid.uuid4(),
                    user_id=owner.id,
                    role="platform_admin",
                    is_active=True,
                    assigned_at=now,
                    assigned_by_user_id=None,
                )
            )
            session.flush()
            summary["changed"] = True

    return summary


def create_organization_with_initial_admin(
    session: Session,
    *,
    code: str,
    display_name: str,
    legal_name: str | None = None,
    locale: str = "en-US",
    initial_admin_user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> OrganizationCreationResult:
    """Add an organization, membership, and first admin role in one transaction."""
    normalized_code = normalize_organization_code(code)
    display_name_key = _normalize_name(display_name, field_name="display_name")
    legal_name_key = (
        _normalize_name(legal_name, field_name="legal_name") if legal_name is not None else None
    )
    _acquire_administration_lock(session)
    _locked_active_users(
        session,
        {
            actor_user_id: "Actor",
            initial_admin_user_id: "Initial administrator",
        },
    )
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)

    collision = session.execute(
        select(Organization.id)
        .where(Organization.normalized_code == normalized_code)
        .with_for_update()
    ).scalar_one_or_none()
    if collision is not None:
        raise ValueError("Organization code already exists (case-insensitive).")

    duplicate_rows = session.execute(
        select(Organization)
        .where(
            or_(
                Organization.name_key == display_name_key,
                and_(
                    legal_name_key is not None,
                    Organization.legal_name_key == legal_name_key,
                ),
            )
        )
        .order_by(Organization.normalized_code, Organization.id)
        .limit(MAX_DUPLICATE_WARNINGS)
    ).scalars()
    duplicate_warnings = tuple(
        OrganizationDuplicateWarning(
            organization_id=duplicate.id,
            organization_code=duplicate.code,
            matched_fields=tuple(
                field_name
                for field_name, matches in (
                    ("display_name", duplicate.name_key == display_name_key),
                    (
                        "legal_name",
                        legal_name_key is not None and duplicate.legal_name_key == legal_name_key,
                    ),
                )
                if matches
            ),
        )
        for duplicate in duplicate_rows
    )

    now = _utc_now()
    organization_id = uuid.uuid4()
    organization = Organization(
        id=organization_id,
        code=code,
        normalized_code=normalized_code,
        display_name=display_name,
        legal_name=legal_name,
        name_key=display_name_key,
        legal_name_key=legal_name_key,
        locale=locale,
        entitlement_status="active",
        is_active=True,
        is_protected=False,
        fallback_icon_svg=generate_organization_fallback_icon(organization_id),
        created_at=now,
        updated_at=now,
    )
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=initial_admin_user_id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            organization,
            membership,
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
                assigned_by_user_id=actor_user_id,
            ),
        ]
    )
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.created",
        actor_id=actor_user_id,
        payload={
            "normalized_code": normalized_code,
            "initial_admin_user_id": str(initial_admin_user_id),
        },
    )
    _emit_audit(
        session,
        entity_type="organization_membership",
        entity_id=membership.id,
        action="membership.added",
        actor_id=actor_user_id,
        payload={
            "organization_id": str(organization_id),
            "user_id": str(initial_admin_user_id),
            "base_role": "admin",
        },
    )
    return OrganizationCreationResult(
        organization=organization,
        membership=membership,
        duplicate_warnings=duplicate_warnings,
    )


def list_memberships_for_login_choice(
    session: Session,
    *,
    user_id: uuid.UUID,
    limit: int = MAX_LOGIN_MEMBERSHIP_CHOICES,
) -> list[OrganizationMembershipChoice]:
    """Return bounded active, entitled memberships in deterministic code order."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LOGIN_MEMBERSHIP_CHOICES:
        raise ValueError(f"limit must be between 1 and {MAX_LOGIN_MEMBERSHIP_CHOICES}.")
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return []

    results = session.execute(
        select(OrganizationMembership, Organization.code)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
            Organization.is_active.is_(True),
            Organization.entitlement_status == "active",
        )
        .order_by(Organization.normalized_code, Organization.id)
        .limit(limit)
    ).all()
    return [
        OrganizationMembershipChoice(
            membership_id=membership.id,
            organization_id=membership.organization_id,
            organization_code=code,
        )
        for membership, code in results
    ]


def replace_membership_base_role(
    session: Session,
    *,
    membership_id: uuid.UUID,
    new_base_role: str,
    actor_user_id: uuid.UUID,
) -> OrganizationRoleAssignment | None:
    """Replace one active membership role under lock without committing."""
    if new_base_role not in BASE_ROLES:
        raise ValueError("Base role must be 'admin' or 'user'.")
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")
    _locked_active_users(
        session,
        {actor_user_id: "Actor", membership_probe.user_id: "Membership user"},
    )
    membership = _locked_membership(session, membership_id)
    if membership.status != "active":
        raise ValueError("Only an active membership may have an active base role.")
    current_role = _active_base_role(session, membership_id, lock=True)
    if current_role is None:
        raise ValueError("Active membership must already have one active base role.")
    if current_role.base_role == new_base_role:
        return current_role

    if current_role.base_role == "admin" and new_base_role != "admin":
        _protect_platform_admin_eligibility(session, membership)
        if len(_active_admin_membership_ids(session, membership.organization_id)) <= 1:
            raise ValueError("Cannot demote the last active organization admin.")

    now = _utc_now()
    current_role.is_active = False
    current_role.revoked_at = now
    current_role.revoked_by_user_id = actor_user_id
    current_role.revoke_reason = "role_replacement"
    replacement = OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=membership_id,
        base_role=new_base_role,
        is_active=True,
        assigned_at=now,
        assigned_by_user_id=actor_user_id,
    )
    session.add(replacement)
    session.flush()
    _emit_audit(
        session,
        entity_type="organization_role_assignment",
        entity_id=replacement.id,
        action="membership.base_role_replaced",
        actor_id=actor_user_id,
        payload={
            "membership_id": str(membership_id),
            "previous_role": current_role.base_role,
            "new_role": new_base_role,
        },
    )
    return replacement


def deactivate_membership(
    session: Session,
    *,
    membership_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Deactivate a membership and revoke its base role while preserving history."""
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")
    _locked_active_users(
        session,
        {actor_user_id: "Actor", membership_probe.user_id: "Membership user"},
    )
    membership = _locked_membership(session, membership_id)
    if membership.status != "active":
        return
    active_role = _active_base_role(session, membership_id, lock=True)
    if active_role is None:
        raise ValueError("Active membership must already have one active base role.")
    if active_role.base_role == "admin":
        _protect_platform_admin_eligibility(session, membership)
        if len(_active_admin_membership_ids(session, membership.organization_id)) <= 1:
            raise ValueError("Cannot deactivate the last active organization admin.")

    now = _utc_now()
    active_role.is_active = False
    active_role.revoked_at = now
    active_role.revoked_by_user_id = actor_user_id
    active_role.revoke_reason = "membership_deactivated"
    membership.status = "inactive"
    membership.deactivated_at = now
    membership.updated_at = now
    session.query(UserSession).filter(
        UserSession.auth_user_id == membership.user_id,
        UserSession.active_organization_id == membership.organization_id,
        UserSession.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    session.flush()
    _emit_audit(
        session,
        entity_type="organization_membership",
        entity_id=membership.id,
        action="membership.deactivated",
        actor_id=actor_user_id,
        payload={"organization_id": str(membership.organization_id), "user_id": str(membership.user_id)},
    )


def assign_platform_admin(
    session: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> PlatformRoleAssignment:
    """Assign Platform Admin only to an active Symgov Organization Admin."""
    _acquire_administration_lock(session)
    _locked_active_users(
        session,
        {actor_user_id: "Actor", user_id: "Platform administrator candidate"},
    )
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)
    if _symgov_admin_membership(session, user_id, lock=True) is None:
        raise ValueError("Platform administrator candidate must be an active Symgov Organization Admin.")
    existing = _active_platform_assignment(session, user_id, lock=True)
    if existing is not None:
        return existing

    assignment = PlatformRoleAssignment(
        id=uuid.uuid4(),
        user_id=user_id,
        role="platform_admin",
        is_active=True,
        assigned_at=_utc_now(),
        assigned_by_user_id=actor_user_id,
    )
    session.add(assignment)
    session.flush()
    _emit_audit(
        session,
        entity_type="platform_role_assignment",
        entity_id=assignment.id,
        action="platform_admin.assigned",
        actor_id=actor_user_id,
        payload={"user_id": str(user_id)},
    )
    return assignment


def revoke_platform_admin(
    session: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Revoke Platform Admin while retaining at least one other eligible assignee."""
    _acquire_administration_lock(session)
    _locked_active_users(
        session,
        {actor_user_id: "Actor", user_id: "Platform administrator"},
    )
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)
    assignment = _active_platform_assignment(session, user_id, lock=True)
    if assignment is None:
        return

    session.execute(
        select(PlatformRoleAssignment.id)
        .where(
            PlatformRoleAssignment.role == "platform_admin",
            PlatformRoleAssignment.is_active.is_(True),
        )
        .order_by(PlatformRoleAssignment.id)
        .with_for_update()
    ).all()
    eligible_other_count = session.execute(
        select(func.count())
        .select_from(PlatformRoleAssignment)
        .join(User, User.id == PlatformRoleAssignment.user_id)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == PlatformRoleAssignment.user_id,
        )
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .join(
            OrganizationRoleAssignment,
            OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
        )
        .where(
            PlatformRoleAssignment.role == "platform_admin",
            PlatformRoleAssignment.is_active.is_(True),
            PlatformRoleAssignment.user_id != user_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            OrganizationMembership.status == "active",
            Organization.code == "symgov",
            Organization.normalized_code == "symgov",
            Organization.is_active.is_(True),
            Organization.entitlement_status == "active",
            OrganizationRoleAssignment.base_role == "admin",
            OrganizationRoleAssignment.is_active.is_(True),
        )
    ).scalar_one()
    if eligible_other_count < 1:
        raise ValueError("Cannot revoke the last eligible Platform Administrator.")

    now = _utc_now()
    assignment.is_active = False
    assignment.revoked_at = now
    assignment.revoked_by_user_id = actor_user_id
    assignment.revoke_reason = "platform_role_revoked"
    session.flush()
    _emit_audit(
        session,
        entity_type="platform_role_assignment",
        entity_id=assignment.id,
        action="platform_admin.revoked",
        actor_id=actor_user_id,
        payload={"user_id": str(user_id)},
    )


class PlatformAdminDetail(NamedTuple):
    user_id: uuid.UUID
    user_email: str
    user_display_name: str
    user_is_active: bool
    granted_at: datetime


def list_platform_admins(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[PlatformAdminDetail], int]:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer.")
    if not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be between 1 and 200.")

    base = (
        select(PlatformRoleAssignment, User)
        .join(User, User.id == PlatformRoleAssignment.user_id)
        .where(
            PlatformRoleAssignment.role == "platform_admin",
            PlatformRoleAssignment.is_active.is_(True),
        )
        .order_by(PlatformRoleAssignment.assigned_at, PlatformRoleAssignment.id)
    )
    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    rows = session.execute(
        base.offset((page - 1) * page_size).limit(page_size)
    ).all()

    return (
        [
            PlatformAdminDetail(
                user_id=assignment.user_id,
                user_email=user.email,
                user_display_name=user.display_name,
                user_is_active=bool(user.is_active),
                granted_at=assignment.assigned_at,
            )
            for assignment, user in rows
        ],
        total,
    )


def get_platform_admin_detail(
    session: Session,
    user_id: uuid.UUID,
) -> PlatformAdminDetail | None:
    row = session.execute(
        select(PlatformRoleAssignment, User)
        .join(User, User.id == PlatformRoleAssignment.user_id)
        .where(
            PlatformRoleAssignment.role == "platform_admin",
            PlatformRoleAssignment.is_active.is_(True),
            PlatformRoleAssignment.user_id == user_id,
        )
    ).one_or_none()
    if row is None:
        return None
    assignment, user = row
    return PlatformAdminDetail(
        user_id=assignment.user_id,
        user_email=user.email,
        user_display_name=user.display_name,
        user_is_active=bool(user.is_active),
        granted_at=assignment.assigned_at,
    )


# ---------------------------------------------------------------------------
# Stage 3, Slice 3C — Platform Admin organization directory
# ---------------------------------------------------------------------------


def list_organizations(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Organization], int]:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer.")
    if not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be between 1 and 200.")
    _require_effective_platform_admin(session, actor_user_id)

    base = select(Organization).order_by(Organization.normalized_code, Organization.id)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(base.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return list(rows), total


def suspend_organization(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> Organization:
    """Suspend an organization's entitlement and revoke its bound sessions."""
    _acquire_administration_lock(session)
    _locked_active_users(session, {actor_user_id: "Actor"})
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be suspended.")
    if org.entitlement_status == "suspended":
        return org

    now = _utc_now()
    org.entitlement_status = "suspended"
    org.updated_at = now
    session.query(UserSession).filter(
        UserSession.active_organization_id == organization_id,
        UserSession.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.suspended",
        actor_id=actor_user_id,
        payload={},
    )
    return org


def reactivate_organization(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> Organization:
    """Restore an organization's entitlement to active."""
    _acquire_administration_lock(session)
    _locked_active_users(session, {actor_user_id: "Actor"})
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.entitlement_status == "active":
        return org

    now = _utc_now()
    org.entitlement_status = "active"
    org.updated_at = now
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.reactivated",
        actor_id=actor_user_id,
        payload={},
    )
    return org


# ---------------------------------------------------------------------------
# Stage 3 — Organization Admin service functions
# ---------------------------------------------------------------------------

class OrganizationMemberDetail(NamedTuple):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    user_display_name: str
    user_is_active: bool
    status: str
    base_role: str
    capabilities: tuple[tuple[str, datetime], ...]
    activated_at: datetime | None
    deactivated_at: datetime | None


def get_organization_detail(
    session: Session,
    organization_id: uuid.UUID,
) -> Organization | None:
    return session.get(Organization, organization_id)


def update_organization(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    display_name: str | None = None,
    legal_name: str | None = None,
) -> Organization:
    _acquire_administration_lock(session)
    org = session.execute(
        select(Organization)
        .where(Organization.id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be updated via this endpoint.")

    changed: list[str] = []
    now = _utc_now()
    if display_name is not None:
        display_name_key = _normalize_name(display_name, field_name="display_name")
        collision = session.execute(
            select(Organization.id)
            .where(Organization.name_key == display_name_key, Organization.id != organization_id)
        ).scalar_one_or_none()
        if collision is not None:
            raise ValueError("An organization with that display name already exists.")
        org.display_name = display_name
        org.name_key = display_name_key
        changed.append("display_name")
    if legal_name is not None:
        legal_name_key = _normalize_name(legal_name, field_name="legal_name")
        org.legal_name = legal_name
        org.legal_name_key = legal_name_key
        changed.append("legal_name")
    if changed:
        org.updated_at = now
        session.flush()
        _emit_audit(
            session,
            entity_type="organization",
            entity_id=organization_id,
            action="organization.updated",
            actor_id=actor_user_id,
            payload={"changed_fields": changed},
        )
    return org


def enforce_icon_upload_rate_limit(org: Organization) -> None:
    """Reject an icon upload attempt that arrives too soon after the last one.

    A simple per-organization cooldown, checked both before the expensive
    validate/store work (read-only) and again under the row lock in
    ``finalize_organization_icon_upload`` (authoritative), bounds repeated
    upload abuse without a dedicated throttle table/migration.
    """
    if org.uploaded_icon_uploaded_at is None:
        return
    ts = org.uploaded_icon_uploaded_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    elapsed = (_utc_now() - ts).total_seconds()
    if elapsed < ICON_UPLOAD_MIN_INTERVAL_SECONDS:
        raise ValueError("Icon uploads are rate-limited; wait a few seconds before trying again.")


def finalize_organization_icon_upload(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    storage_key: str,
    content_type: str,
) -> Organization:
    """Activate an already-uploaded, already-verified icon object transactionally.

    The caller must upload and verify the immutable object first; this only
    switches the active database reference. A failure here leaves whichever
    icon (custom or generated fallback) was previously active untouched.
    """
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be updated via this endpoint.")
    enforce_icon_upload_rate_limit(org)

    now = _utc_now()
    org.uploaded_icon_storage_key = storage_key
    org.uploaded_icon_content_type = content_type
    org.uploaded_icon_uploaded_at = now
    org.updated_at = now
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.icon_uploaded",
        actor_id=actor_user_id,
        payload={"content_type": content_type},
    )
    return org


def remove_organization_icon(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> Organization:
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be updated via this endpoint.")
    if org.uploaded_icon_storage_key is None:
        raise ValueError("This organization has no custom icon to remove.")

    now = _utc_now()
    removed_storage_key = org.uploaded_icon_storage_key
    org.uploaded_icon_storage_key = None
    org.uploaded_icon_content_type = None
    org.uploaded_icon_uploaded_at = None
    org.updated_at = now
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.icon_removed",
        actor_id=actor_user_id,
        payload={"removed_storage_key": removed_storage_key},
    )
    return org


def list_organization_members(
    session: Session,
    organization_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[OrganizationMemberDetail], int]:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer.")
    if not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be between 1 and 200.")

    base = (
        select(OrganizationMembership, User, OrganizationRoleAssignment)
        .join(User, User.id == OrganizationMembership.user_id)
        .join(
            OrganizationRoleAssignment,
            and_(
                OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
                OrganizationRoleAssignment.is_active.is_(True),
            ),
        )
        .where(OrganizationMembership.organization_id == organization_id)
        .order_by(OrganizationMembership.created_at, OrganizationMembership.id)
    )
    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    rows = session.execute(
        base.offset((page - 1) * page_size).limit(page_size)
    ).all()

    membership_ids = [m.id for m, _u, _r in rows]
    cap_rows = session.execute(
        select(OrganizationMemberCapability)
        .where(
            OrganizationMemberCapability.membership_id.in_(membership_ids),
            OrganizationMemberCapability.is_active.is_(True),
        )
        .order_by(OrganizationMemberCapability.membership_id, OrganizationMemberCapability.capability)
    ).scalars().all()
    caps_by_membership: dict[uuid.UUID, list[tuple[str, datetime]]] = {}
    for cap in cap_rows:
        caps_by_membership.setdefault(cap.membership_id, []).append(
            (cap.capability, cap.granted_at)
        )

    return [
        OrganizationMemberDetail(
            membership_id=membership.id,
            user_id=user.id,
            user_email=user.email,
            user_display_name=user.display_name,
            user_is_active=bool(user.is_active),
            status=membership.status,
            base_role=role.base_role,
            capabilities=tuple(caps_by_membership.get(membership.id, [])),
            activated_at=membership.activated_at,
            deactivated_at=membership.deactivated_at,
        )
        for membership, user, role in rows
    ], total


def add_organization_member(
    session: Session,
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    base_role: str,
    actor_user_id: uuid.UUID,
) -> OrganizationMembership:
    if base_role not in BASE_ROLES:
        raise ValueError("Base role must be 'admin' or 'user'.")
    _acquire_administration_lock(session)
    _locked_active_users(
        session,
        {actor_user_id: "Actor", user_id: "New member"},
    )
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None or not org.is_active or org.entitlement_status != "active":
        raise ValueError("Organization is not active.")

    existing = session.execute(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        raise ValueError("User is already an active member of this organization.")
    if existing is not None:
        raise ValueError("A membership record already exists for this user; contact platform admin to reactivate.")

    now = _utc_now()
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    role_assignment = OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=membership.id,
        base_role=base_role,
        is_active=True,
        assigned_at=now,
        assigned_by_user_id=actor_user_id,
    )
    session.add_all([membership, role_assignment])
    session.flush()
    _emit_audit(
        session,
        entity_type="organization_membership",
        entity_id=membership.id,
        action="membership.added",
        actor_id=actor_user_id,
        payload={
            "organization_id": str(organization_id),
            "user_id": str(user_id),
            "base_role": base_role,
        },
    )
    return membership


def grant_member_capability(
    session: Session,
    membership_id: uuid.UUID,
    *,
    capability: str,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> OrganizationMemberCapability:
    if capability not in VALID_CAPABILITIES:
        raise ValueError(f"Unknown capability '{capability}'. Valid: {sorted(VALID_CAPABILITIES)}.")
    _acquire_administration_lock(session)
    membership = session.execute(
        select(OrganizationMembership)
        .where(OrganizationMembership.id == membership_id)
        .with_for_update()
    ).scalar_one_or_none()
    if membership is None:
        raise ValueError("Membership not found.")
    if membership.organization_id != organization_id:
        raise ValueError("Membership does not belong to the current organization.")
    if membership.status != "active":
        raise ValueError("Cannot grant capability to an inactive membership.")

    existing = session.execute(
        select(OrganizationMemberCapability)
        .where(
            OrganizationMemberCapability.membership_id == membership_id,
            OrganizationMemberCapability.capability == capability,
            OrganizationMemberCapability.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    now = _utc_now()
    cap = OrganizationMemberCapability(
        id=uuid.uuid4(),
        membership_id=membership_id,
        capability=capability,
        is_active=True,
        granted_at=now,
        granted_by_user_id=actor_user_id,
    )
    session.add(cap)
    session.flush()
    _emit_audit(
        session,
        entity_type="organization_member_capability",
        entity_id=cap.id,
        action="capability.granted",
        actor_id=actor_user_id,
        payload={
            "membership_id": str(membership_id),
            "capability": capability,
        },
    )
    return cap


def revoke_member_capability(
    session: Session,
    membership_id: uuid.UUID,
    *,
    capability: str,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    if capability not in VALID_CAPABILITIES:
        raise ValueError(f"Unknown capability '{capability}'. Valid: {sorted(VALID_CAPABILITIES)}.")
    _acquire_administration_lock(session)
    membership = session.execute(
        select(OrganizationMembership)
        .where(OrganizationMembership.id == membership_id)
        .with_for_update()
    ).scalar_one_or_none()
    if membership is None:
        raise ValueError("Membership not found.")
    if membership.organization_id != organization_id:
        raise ValueError("Membership does not belong to the current organization.")

    cap = session.execute(
        select(OrganizationMemberCapability)
        .where(
            OrganizationMemberCapability.membership_id == membership_id,
            OrganizationMemberCapability.capability == capability,
            OrganizationMemberCapability.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if cap is None:
        return

    now = _utc_now()
    cap.is_active = False
    cap.revoked_at = now
    cap.revoked_by_user_id = actor_user_id
    cap.revoke_reason = "capability_revoked_by_admin"
    session.flush()
    _emit_audit(
        session,
        entity_type="organization_member_capability",
        entity_id=cap.id,
        action="capability.revoked",
        actor_id=actor_user_id,
        payload={
            "membership_id": str(membership_id),
            "capability": capability,
        },
    )
