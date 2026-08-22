from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import aliased

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
PROTECTED_MUTATION_REASON_MIN_LENGTH = 10
PROTECTED_MUTATION_REASON_MAX_LENGTH = 1000


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


def _mutation_audit_payload(
    *,
    organization_id: uuid.UUID | None,
    effective_authority: str,
    before: dict[str, object],
    after: dict[str, object],
    source: str = "organization_service",
    reason: str | None = None,
    details: dict[str, object] | None = None,
    recent_step_up_at: datetime | None = None,
) -> dict[str, object]:
    """Build the bounded, allowlisted context shared by Stage 3 mutations."""
    payload: dict[str, object] = {
        "effective_authority": effective_authority,
        "before": before,
        "after": after,
        "source": source,
    }
    if organization_id is not None:
        payload["organization_id"] = str(organization_id)
    if reason is not None:
        payload["reason"] = reason
    if recent_step_up_at is not None:
        payload["recent_step_up_at"] = recent_step_up_at.isoformat()
    if details:
        payload.update(details)
    return payload


def _normalize_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


def _bounded_protected_reason(reason: str) -> str:
    normalized = " ".join(reason.split()) if isinstance(reason, str) else ""
    if not PROTECTED_MUTATION_REASON_MIN_LENGTH <= len(normalized) <= PROTECTED_MUTATION_REASON_MAX_LENGTH:
        raise ValueError("Reason must be between 10 and 1000 characters.")
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
            _emit_audit(
                session,
                entity_type="organization",
                entity_id=organization_id,
                action="organization.created",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=organization_id,
                    effective_authority="system_bootstrap",
                    before={"exists": False},
                    after={"exists": True, "is_protected": True},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                    details={"code": "symgov"},
                ),
            )
            summary["created"] = True

    if symgov_org is None:  # Defensive: apply=False returned no row but did not create one.
        return summary

    if not symgov_org.is_active:
        summary["actions"].append("reactivate protected Symgov organization")
        if apply:
            symgov_org.is_active = True
            symgov_org.updated_at = now
            _emit_audit(
                session,
                entity_type="organization",
                entity_id=symgov_org.id,
                action="organization.reactivated",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=symgov_org.id,
                    effective_authority="system_bootstrap",
                    before={"is_active": False},
                    after={"is_active": True},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                ),
            )
            summary["changed"] = True
    if symgov_org.entitlement_status != "active":
        summary["actions"].append("restore protected Symgov organization entitlement")
        if apply:
            previous_entitlement_status = symgov_org.entitlement_status
            symgov_org.entitlement_status = "active"
            symgov_org.updated_at = now
            _emit_audit(
                session,
                entity_type="organization",
                entity_id=symgov_org.id,
                action="organization.entitlement_restored",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=symgov_org.id,
                    effective_authority="system_bootstrap",
                    before={"entitlement_status": previous_entitlement_status},
                    after={"entitlement_status": "active"},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                ),
            )
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
            membership_id = uuid.uuid4()
            membership = OrganizationMembership(
                id=membership_id,
                organization_id=symgov_org.id,
                user_id=owner.id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(membership)
            session.flush()
            _emit_audit(
                session,
                entity_type="organization_membership",
                entity_id=membership_id,
                action="membership.added",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=symgov_org.id,
                    effective_authority="system_bootstrap",
                    before={"status": None},
                    after={"status": "active"},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                    details={"user_id": str(owner.id)},
                ),
            )
            summary["changed"] = True
    elif membership.status != "active":
        summary["actions"].append("reactivate protected owner Symgov membership")
        if apply:
            previous_membership_status = membership.status
            membership.status = "active"
            if membership.activated_at is None:
                membership.activated_at = now
            membership.updated_at = now
            _emit_audit(
                session,
                entity_type="organization_membership",
                entity_id=membership.id,
                action="membership.reactivated",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=symgov_org.id,
                    effective_authority="system_bootstrap",
                    before={"status": previous_membership_status},
                    after={"status": "active"},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                    details={"user_id": str(owner.id)},
                ),
            )
            summary["changed"] = True

    if membership is not None:
        active_role = _active_base_role(session, membership.id, lock=apply)
        if active_role is None or active_role.base_role != "admin":
            summary["actions"].append("assign protected owner Symgov admin role")
            if apply:
                previous_base_role = active_role.base_role if active_role is not None else None
                previous_role_active = bool(active_role and active_role.is_active)
                if active_role is not None:
                    active_role.is_active = False
                    active_role.revoked_at = now
                    active_role.revoke_reason = "bootstrap_reconciliation"
                role_id = uuid.uuid4()
                session.add(
                    OrganizationRoleAssignment(
                        id=role_id,
                        membership_id=membership.id,
                        base_role="admin",
                        is_active=True,
                        assigned_at=now,
                        assigned_by_user_id=None,
                    )
                )
                session.flush()
                _emit_audit(
                    session,
                    entity_type="organization_role_assignment",
                    entity_id=role_id,
                    action="membership.base_role_assigned",
                    actor_id=None,
                    payload=_mutation_audit_payload(
                        organization_id=symgov_org.id,
                        effective_authority="system_bootstrap",
                        before={
                            "base_role": previous_base_role,
                            "is_active": previous_role_active,
                        },
                        after={"base_role": "admin", "is_active": True},
                        source="management.bootstrap_symgov_organization",
                        reason="bootstrap_reconciliation",
                        details={"membership_id": str(membership.id)},
                    ),
                )
                summary["changed"] = True

    platform_role = _active_platform_assignment(session, owner.id, lock=apply)
    if platform_role is None:
        summary["actions"].append("assign protected owner platform admin role")
        if apply:
            assignment_id = uuid.uuid4()
            session.add(
                PlatformRoleAssignment(
                    id=assignment_id,
                    user_id=owner.id,
                    role="platform_admin",
                    is_active=True,
                    assigned_at=now,
                    assigned_by_user_id=None,
                )
            )
            session.flush()
            _emit_audit(
                session,
                entity_type="platform_role_assignment",
                entity_id=assignment_id,
                action="platform_admin.assigned",
                actor_id=None,
                payload=_mutation_audit_payload(
                    organization_id=symgov_org.id,
                    effective_authority="system_bootstrap",
                    before={"role": None, "is_active": False},
                    after={"role": "platform_admin", "is_active": True},
                    source="management.bootstrap_symgov_organization",
                    reason="bootstrap_reconciliation",
                    details={"user_id": str(owner.id)},
                ),
            )
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
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
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
    role_assignment = OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=membership.id,
        base_role="admin",
        is_active=True,
        assigned_at=now,
        assigned_by_user_id=actor_user_id,
    )
    session.add_all(
        [
            organization,
            membership,
            role_assignment,
        ]
    )
    session.flush()
    _emit_audit(
        session,
        entity_type="organization",
        entity_id=organization_id,
        action="organization.created",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="platform_admin",
            before={"exists": False},
            after={"exists": True, "entitlement_status": "active"},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={
                "normalized_code": normalized_code,
                "initial_admin_user_id": str(initial_admin_user_id),
            },
        ),
    )
    _emit_audit(
        session,
        entity_type="organization_membership",
        entity_id=membership.id,
        action="membership.added",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="platform_admin",
            before={"status": None},
            after={"status": "active"},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(initial_admin_user_id), "base_role": "admin"},
        ),
    )
    _emit_audit(
        session,
        entity_type="organization_role_assignment",
        entity_id=role_assignment.id,
        action="membership.base_role_assigned",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="platform_admin",
            before={"base_role": None, "is_active": False},
            after={"base_role": "admin", "is_active": True},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership.id)},
        ),
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
    _bypass_admin_check: bool = False,
    reason: str | None = None,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationRoleAssignment | None:
    """Replace one active membership role under lock without committing."""
    if new_base_role not in BASE_ROLES:
        raise ValueError("Base role must be 'admin' or 'user'.")
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")

    if not _bypass_admin_check:
        _require_active_organization_admin(session, membership_probe.organization_id, actor_user_id)

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
        payload=_mutation_audit_payload(
            organization_id=membership.organization_id,
            effective_authority=(
                "platform_admin" if _bypass_admin_check else "organization_admin"
            ),
            before={"base_role": current_role.base_role, "is_active": True},
            after={"base_role": new_base_role, "is_active": True},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership_id)},
        ),
    )
    return replacement


def deactivate_membership(
    session: Session,
    *,
    membership_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    _bypass_admin_check: bool = False,
    reason: str | None = None,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> None:
    """Deactivate a membership and revoke its base role while preserving history."""
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")

    if not _bypass_admin_check:
        _require_active_organization_admin(session, membership_probe.organization_id, actor_user_id)

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
    active_role.revoke_reason = reason or "membership_deactivated"
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
        payload=_mutation_audit_payload(
            organization_id=membership.organization_id,
            effective_authority=(
                "platform_admin" if _bypass_admin_check else "organization_admin"
            ),
            before={"status": "active"},
            after={"status": "inactive"},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(membership.user_id)},
        ),
    )
    _emit_audit(
        session,
        entity_type="organization_role_assignment",
        entity_id=active_role.id,
        action="membership.base_role_revoked",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=membership.organization_id,
            effective_authority=(
                "platform_admin" if _bypass_admin_check else "organization_admin"
            ),
            before={"base_role": active_role.base_role, "is_active": True},
            after={"base_role": active_role.base_role, "is_active": False},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership.id)},
        ),
    )


def assign_platform_admin(
    session: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> PlatformRoleAssignment:
    """Assign Platform Admin only to an active Symgov Organization Admin."""
    _acquire_administration_lock(session)
    _locked_active_users(
        session,
        {actor_user_id: "Actor", user_id: "Platform administrator candidate"},
    )
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)
    candidate_membership = _symgov_admin_membership(session, user_id, lock=True)
    if candidate_membership is None:
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
        payload=_mutation_audit_payload(
            organization_id=candidate_membership.organization_id,
            effective_authority="platform_admin",
            before={"role": None, "is_active": False},
            after={"role": "platform_admin", "is_active": True},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(user_id)},
        ),
    )
    return assignment


def revoke_platform_admin(
    session: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
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
    symgov_organization_id = session.execute(
        select(Organization.id).where(Organization.normalized_code == "symgov")
    ).scalar_one()

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
        payload=_mutation_audit_payload(
            organization_id=symgov_organization_id,
            effective_authority="platform_admin",
            before={"role": "platform_admin", "is_active": True},
            after={"role": "platform_admin", "is_active": False},
            source=audit_source,
            reason="platform_role_revoked",
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(user_id)},
        ),
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
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
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

    previous_entitlement_status = org.entitlement_status
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="platform_admin",
            before={"entitlement_status": previous_entitlement_status},
            after={"entitlement_status": "suspended"},
            source=audit_source,
            reason="platform_entitlement_suspended",
            recent_step_up_at=recent_step_up_at,
        ),
    )
    return org


def reactivate_organization(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
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

    previous_entitlement_status = org.entitlement_status
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="platform_admin",
            before={"entitlement_status": previous_entitlement_status},
            after={"entitlement_status": "active"},
            source=audit_source,
            reason="platform_entitlement_reactivated",
            recent_step_up_at=recent_step_up_at,
        ),
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
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> Organization:
    _acquire_administration_lock(session)
    _locked_active_user(session, actor_user_id, label="Actor")
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
    org = session.execute(
        select(Organization)
        .where(Organization.id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be updated via this endpoint.")

    before = {"display_name": org.display_name, "legal_name": org.legal_name}
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
            payload=_mutation_audit_payload(
                organization_id=organization_id,
                effective_authority="organization_admin",
                before={field: before[field] for field in changed},
                after={field: getattr(org, field) for field in changed},
                source=audit_source,
                recent_step_up_at=recent_step_up_at,
                details={"changed_fields": changed},
            ),
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


def preflight_organization_icon_upload(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> Organization:
    """Lock and revalidate live upload authority before object storage is touched."""
    _acquire_administration_lock(session)
    _locked_active_user(session, actor_user_id, label="Actor")
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    enforce_icon_upload_rate_limit(org)
    return org


def finalize_organization_icon_upload(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    storage_key: str,
    content_type: str,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> Organization:
    """Activate an already-uploaded, already-verified icon object transactionally.

    The caller must upload and verify the immutable object first; this only
    switches the active database reference. A failure here leaves whichever
    icon (custom or generated fallback) was previously active untouched.
    """
    _acquire_administration_lock(session)
    _locked_active_user(session, actor_user_id, label="Actor")
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if org.is_protected:
        raise ValueError("The protected Symgov organization cannot be updated via this endpoint.")
    enforce_icon_upload_rate_limit(org)

    had_custom_icon = org.uploaded_icon_storage_key is not None
    previous_content_type = org.uploaded_icon_content_type
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="organization_admin",
            before={
                "has_custom_icon": had_custom_icon,
                "content_type": previous_content_type,
            },
            after={"has_custom_icon": True, "content_type": content_type},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={"content_type": content_type},
        ),
    )
    return org


def remove_organization_icon(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> Organization:
    _acquire_administration_lock(session)
    _locked_active_user(session, actor_user_id, label="Actor")
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
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
    removed_content_type = org.uploaded_icon_content_type
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="organization_admin",
            before={"has_custom_icon": True, "content_type": removed_content_type},
            after={"has_custom_icon": False, "content_type": None},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
        ),
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

    # Subquery to pick the 'current' role assignment for each membership.
    # We prefer the active one, then the most recently revoked one.
    role_rn_sub = (
        select(
            OrganizationRoleAssignment,
            func.row_number()
            .over(
                partition_by=OrganizationRoleAssignment.membership_id,
                order_by=[
                    OrganizationRoleAssignment.is_active.desc(),
                    OrganizationRoleAssignment.assigned_at.desc(),
                    OrganizationRoleAssignment.id.desc(),
                ],
            )
            .label("rn"),
        )
        .subquery()
    )
    current_role = aliased(OrganizationRoleAssignment, role_rn_sub)

    base = (
        select(OrganizationMembership, User, current_role)
        .join(User, User.id == OrganizationMembership.user_id)
        .outerjoin(
            current_role,
            and_(
                current_role.membership_id == OrganizationMembership.id,
                role_rn_sub.c.rn == 1,
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
            base_role=role.base_role if role else "user",
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
    _bypass_admin_check: bool = False,
    reason: str | None = None,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationMembership:
    if base_role not in BASE_ROLES:
        raise ValueError("Base role must be 'admin' or 'user'.")
    _acquire_administration_lock(session)

    if not _bypass_admin_check:
        _require_active_organization_admin(session, organization_id, actor_user_id)

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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority=(
                "platform_admin" if _bypass_admin_check else "organization_admin"
            ),
            before={"status": None},
            after={"status": "active"},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(user_id), "base_role": base_role},
        ),
    )
    _emit_audit(
        session,
        entity_type="organization_role_assignment",
        entity_id=role_assignment.id,
        action="membership.base_role_assigned",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority=(
                "platform_admin" if _bypass_admin_check else "organization_admin"
            ),
            before={"base_role": None, "is_active": False},
            after={"base_role": base_role, "is_active": True},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership.id)},
        ),
    )
    return membership


def grant_member_capability(
    session: Session,
    membership_id: uuid.UUID,
    *,
    capability: str,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationMemberCapability:
    if capability not in VALID_CAPABILITIES:
        raise ValueError(f"Unknown capability '{capability}'. Valid: {sorted(VALID_CAPABILITIES)}.")
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")
    _locked_active_users(
        session,
        {actor_user_id: "Actor", membership_probe.user_id: "Membership user"},
    )
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="organization_admin",
            before={"capability": capability, "is_active": False},
            after={"capability": capability, "is_active": True},
            source=audit_source,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership_id)},
        ),
    )
    return cap


def revoke_member_capability(
    session: Session,
    membership_id: uuid.UUID,
    *,
    capability: str,
    actor_user_id: uuid.UUID,
    organization_id: uuid.UUID,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> None:
    if capability not in VALID_CAPABILITIES:
        raise ValueError(f"Unknown capability '{capability}'. Valid: {sorted(VALID_CAPABILITIES)}.")
    _acquire_administration_lock(session)
    membership_probe = session.get(OrganizationMembership, membership_id)
    if membership_probe is None:
        raise ValueError("Membership not found.")
    _locked_active_users(
        session,
        {actor_user_id: "Actor", membership_probe.user_id: "Membership user"},
    )
    _require_active_organization_admin(
        session, organization_id, actor_user_id, user_locked=True
    )
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
        payload=_mutation_audit_payload(
            organization_id=organization_id,
            effective_authority="organization_admin",
            before={"capability": capability, "is_active": True},
            after={"capability": capability, "is_active": False},
            source=audit_source,
            reason="capability_revoked_by_admin",
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership_id)},
        ),
    )

def _require_active_organization_admin(
    session: Session,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    *,
    user_locked: bool = False,
) -> None:
    """Revalidate that the actor remains an active administrator of the organization."""
    if not user_locked:
        _locked_active_user(session, actor_user_id, label="Actor")
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")

    if org.is_protected:
        # Ordinary Organization Admin paths must fail closed for protected Symgov governance.
        # Exceptional administration must use dedicated Platform Admin services.
        raise ValueError("The protected Symgov organization cannot be managed through ordinary services.")

    membership = session.execute(
        select(OrganizationMembership)
        .join(
            OrganizationRoleAssignment,
            OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
        )
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == actor_user_id,
            OrganizationMembership.status == "active",
            OrganizationRoleAssignment.base_role == "admin",
            OrganizationRoleAssignment.is_active.is_(True),
        )
        .with_for_update(of=OrganizationMembership)
    ).scalar_one_or_none()

    if membership is None:
        raise ValueError("Actor must be an active administrator of the organization.")

def add_protected_organization_member(
    session: Session,
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    base_role: str,
    actor_user_id: uuid.UUID,
    reason: str,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationMembership:
    """Exceptional Platform Admin path to add a member to the protected Symgov organization."""
    reason = _bounded_protected_reason(reason)
    _acquire_administration_lock(session)
    _require_effective_platform_admin(session, actor_user_id)
    org = session.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Organization not found.")
    if not org.is_protected:
        raise ValueError("Use ordinary services for non-protected organizations.")

    return add_organization_member(
        session,
        organization_id,
        user_id=user_id,
        base_role=base_role,
        actor_user_id=actor_user_id,
        _bypass_admin_check=True,
        reason=reason,
        audit_source=audit_source,
        recent_step_up_at=recent_step_up_at,
    )


def deactivate_protected_membership(
    session: Session,
    *,
    membership_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> None:
    """Exceptional Platform Admin path to deactivate a member in the protected Symgov organization."""
    reason = _bounded_protected_reason(reason)
    _acquire_administration_lock(session)
    _require_effective_platform_admin(session, actor_user_id)
    membership = _locked_membership(session, membership_id)
    org = session.get(Organization, membership.organization_id)
    if org is None or not org.is_protected:
        raise ValueError("Use ordinary services for non-protected organizations.")

    return deactivate_membership(
        session,
        membership_id=membership_id,
        actor_user_id=actor_user_id,
        _bypass_admin_check=True,
        reason=reason,
        audit_source=audit_source,
        recent_step_up_at=recent_step_up_at,
    )


def replace_protected_membership_base_role(
    session: Session,
    *,
    membership_id: uuid.UUID,
    new_base_role: str,
    actor_user_id: uuid.UUID,
    reason: str,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationRoleAssignment | None:
    """Exceptional Platform Admin path to replace a role in the protected Symgov organization."""
    reason = _bounded_protected_reason(reason)
    _acquire_administration_lock(session)
    _require_effective_platform_admin(session, actor_user_id)
    membership = _locked_membership(session, membership_id)
    org = session.get(Organization, membership.organization_id)
    if org is None or not org.is_protected:
        raise ValueError("Use ordinary services for non-protected organizations.")

    return replace_membership_base_role(
        session,
        membership_id=membership_id,
        new_base_role=new_base_role,
        actor_user_id=actor_user_id,
        _bypass_admin_check=True,
        reason=reason,
        audit_source=audit_source,
        recent_step_up_at=recent_step_up_at,
    )

def reactivate_membership(
    session: Session,
    *,
    membership_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str,
    audit_source: str = "organization_service",
    recent_step_up_at: datetime | None = None,
) -> OrganizationMembership:
    """Reactivate an inactive membership and assign a default 'user' role."""
    reason = _bounded_protected_reason(reason)
    _acquire_administration_lock(session)
    _locked_active_users(session, {actor_user_id: "Actor"})
    _require_effective_platform_admin(session, actor_user_id, user_locked=True)

    membership = _locked_membership(session, membership_id)
    if membership.status == "active":
        return membership

    org = session.execute(
        select(Organization).where(Organization.id == membership.organization_id).with_for_update()
    ).scalar_one_or_none()
    if org is None:
        raise ValueError("Membership organization not found.")
    if not org.is_active or org.entitlement_status != "active":
        raise ValueError("Membership organization must be active and entitled.")

    # Ensure the user being reactivated is still active and not deleted
    target_user = _locked_active_user(session, membership.user_id, label="Target user")

    # Duplicate protection: check if there's already an active membership for this user/org
    # (Though the unique constraint should handle this, we check explicitly for a better error)
    existing_active = session.execute(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == membership.organization_id,
            OrganizationMembership.user_id == membership.user_id,
            OrganizationMembership.status == "active",
            OrganizationMembership.id != membership_id
        )
    ).scalar_one_or_none()
    if existing_active:
        raise ValueError("User already has an active membership in this organization.")

    now = _utc_now()
    membership.status = "active"
    membership.updated_at = now

    # Assign default 'user' role
    role = OrganizationRoleAssignment(
        id=uuid.uuid4(),
        membership_id=membership.id,
        base_role="user",
        is_active=True,
        assigned_at=now,
        assigned_by_user_id=actor_user_id,
    )
    session.add(role)
    session.flush()

    _emit_audit(
        session,
        entity_type="organization_membership",
        entity_id=membership.id,
        action="membership.reactivated",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=membership.organization_id,
            effective_authority="platform_admin",
            before={"status": "inactive"},
            after={"status": "active"},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"user_id": str(membership.user_id)},
        ),
    )
    _emit_audit(
        session,
        entity_type="organization_role_assignment",
        entity_id=role.id,
        action="membership.base_role_assigned",
        actor_id=actor_user_id,
        payload=_mutation_audit_payload(
            organization_id=membership.organization_id,
            effective_authority="platform_admin",
            before={"base_role": None, "is_active": False},
            after={"base_role": "user", "is_active": True},
            source=audit_source,
            reason=reason,
            recent_step_up_at=recent_step_up_at,
            details={"membership_id": str(membership.id)},
        ),
    )
    return membership


def list_organization_member_diagnostics(
    session: Session,
    organization_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[OrganizationMemberDetail], int]:
    """Platform Admin view of all memberships (active and inactive) for an organization."""
    _require_effective_platform_admin(session, actor_user_id)
    # This is similar to list_organization_members but for Platform Admins and shows everything
    return list_organization_members(session, organization_id, page=page, page_size=page_size)
