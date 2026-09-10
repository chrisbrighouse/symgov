from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, PrimaryKeyConstraint, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("uq_users_display_name_lower", text("lower(display_name)"), unique=True),
        Index("ix_users_deleted_display_name", "deleted_at", "display_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)
    pin_set_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    must_change_pin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        CheckConstraint("role in ('admin', 'integrator', 'submitter', 'reviewer')", name="ck_user_roles_role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("purpose in ('application', 'credential_change')", name="purpose"),
        CheckConstraint("session_mode in ('personal', 'organization')", name="mode"),
        CheckConstraint(
            "(session_mode = 'personal' and active_organization_id is null) or "
            "(session_mode = 'organization' and active_organization_id is not null)",
            name="mode_active_org",
        ),
        Index("uq_user_sessions_token_hash", "token_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'application'"))
    session_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'personal'"))
    active_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    recent_step_up_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("normalized_code ~ '^[a-z][a-z0-9-]{1,31}$'", name="normalized_code_format"),
        CheckConstraint(
            "(code = 'symgov' and normalized_code = 'symgov') or "
            "(code ~ '^[A-Z][A-Z0-9-]{1,31}$' and normalized_code = lower(code))",
            name="code_format",
        ),
        CheckConstraint(
            "(normalized_code = 'symgov' and code = 'symgov' and is_protected = true) or "
            "(normalized_code <> 'symgov' and is_protected = false)",
            name="reserved_identity",
        ),
        CheckConstraint("entitlement_status in ('active', 'suspended')", name="status"),
        UniqueConstraint("normalized_code", name="uq_organizations_normalized_code"),
        Index("ix_organizations_active_status", "is_active", "entitlement_status", "normalized_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_code: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_key: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    locale: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en-US'"))
    entitlement_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    icon_seed_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'v1'"))
    fallback_icon_svg: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_icon_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_icon_content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_icon_uploaded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    default_symbol_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbol_sets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{0,31}$'", name="ck_projects_code_format"),
        CheckConstraint("normalized_code = lower(code)", name="ck_projects_normalized_code"),
        CheckConstraint("char_length(short_description) <= 50", name="ck_projects_short_description_length"),
        CheckConstraint("btrim(name) <> '' AND char_length(name) <= 200", name="ck_projects_name_bounds"),
        CheckConstraint("external_reference is null or char_length(external_reference) <= 200", name="ck_projects_external_reference_length"),
        CheckConstraint("status in ('active', 'closed')", name="ck_projects_status"),
        CheckConstraint("jsonb_typeof(metadata_json) = 'object'", name="ck_projects_metadata_object"),
        CheckConstraint("octet_length(convert_to(metadata_json::text, 'UTF8')) <= 16384", name="ck_projects_metadata_bounds"),
        UniqueConstraint("organization_id", "normalized_code", name="uq_projects_organization_normalized_code"),
        Index("ix_projects_organization_status_code_id", "organization_id", "status", "normalized_code", "id"),
        Index("uq_projects_organization_external_reference", "organization_id", "normalized_external_reference", unique=True, postgresql_where=text("normalized_external_reference is not null")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    external_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_external_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SymbolSet(Base):
    __tablename__ = "symbol_sets"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{0,31}$'", name="ck_symbol_sets_code_format"),
        CheckConstraint("normalized_code = lower(code)", name="ck_symbol_sets_normalized_code"),
        CheckConstraint("status in ('draft', 'active', 'superseded', 'archived')", name="ck_symbol_sets_status"),
        CheckConstraint("description is null or char_length(description) <= 2000", name="ck_symbol_sets_description_length"),
        CheckConstraint("btrim(name) <> '' AND char_length(name) <= 200", name="ck_symbol_sets_name_bounds"),
        CheckConstraint("jsonb_typeof(disciplines_json) = 'array' AND jsonb_array_length(disciplines_json) <= 32", name="ck_symbol_sets_disciplines_bounds"),
        CheckConstraint("jsonb_typeof(use_cases_json) = 'array' AND jsonb_array_length(use_cases_json) <= 32", name="ck_symbol_sets_use_cases_bounds"),
        CheckConstraint("copied_from_symbol_set_id IS NULL OR copied_from_symbol_set_id <> id", name="ck_symbol_sets_copy_not_self"),
        UniqueConstraint("owner_organization_id", "normalized_code", name="uq_symbol_sets_owner_normalized_code"),
        Index("ix_symbol_sets_owner_status_code_id", "owner_organization_id", "status", "normalized_code", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    disciplines_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    use_cases_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    copied_from_symbol_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectSymbolSet(Base):
    __tablename__ = "project_symbol_sets"
    __table_args__ = (
        CheckConstraint("status in ('active', 'inactive')", name="ck_project_symbol_sets_status"),
        UniqueConstraint("project_id", "symbol_set_id", name="uq_project_symbol_sets_project_set"),
        Index("uq_project_symbol_sets_active_default", "project_id", unique=True, postgresql_where=text("status = 'active' AND is_default = true")),
        Index("ix_project_symbol_sets_project_status_set", "project_id", "status", "symbol_set_id"),
        Index("ix_project_symbol_sets_set_status_project", "symbol_set_id", "status", "project_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    symbol_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolSetItem(Base):
    __tablename__ = "symbol_set_items"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_symbol_set_items_sort_order"),
        CheckConstraint("availability_status in ('active', 'unavailable')", name="ck_symbol_set_items_availability_status"),
        CheckConstraint("jsonb_typeof(provenance_json) = 'object'", name="ck_symbol_set_items_provenance_object"),
        CheckConstraint("group_name is null or char_length(group_name) <= 200", name="ck_symbol_set_items_group_name_length"),
        CheckConstraint("display_label is null or char_length(display_label) <= 200", name="ck_symbol_set_items_display_label_length"),
        CheckConstraint("preferred_format is null or char_length(preferred_format) <= 200", name="ck_symbol_set_items_preferred_format_length"),
        CheckConstraint("notes is null or char_length(notes) <= 2000", name="ck_symbol_set_items_notes_length"),
        CheckConstraint("availability_reason is null or char_length(availability_reason) <= 500", name="ck_symbol_set_items_availability_reason_length"),
        CheckConstraint("octet_length(convert_to(provenance_json::text, 'UTF8')) <= 16384", name="ck_symbol_set_items_provenance_bounds"),
        UniqueConstraint("symbol_set_id", "governed_symbol_id", name="uq_symbol_set_items_set_symbol"),
        Index("ix_symbol_set_items_set_order_symbol", "symbol_set_id", "sort_order", "governed_symbol_id"),
        Index("ix_symbol_set_items_symbol_set", "governed_symbol_id", "symbol_set_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False)
    governed_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    group_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    availability_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    availability_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserProjectSetSelection(Base):
    __tablename__ = "user_project_set_selections"
    __table_args__ = (
        Index("ix_user_project_set_selections_active_set_project_user", "active_symbol_set_id", "project_id", "user_id"),
        Index("ix_user_project_set_selections_project_user", "project_id", "user_id"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True)
    active_symbol_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False)
    selected_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSessionProjectContext(Base):
    __tablename__ = "user_session_project_contexts"
    __table_args__ = (Index("ix_user_session_project_contexts_project_session", "project_id", "user_session_id"),)
    user_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    selected_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        CheckConstraint("status in ('active', 'invited', 'inactive', 'suspended')", name="status"),
        UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),
        Index("ix_org_memberships_user_status", "user_id", "status", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    invited_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationRoleAssignment(Base):
    __tablename__ = "organization_role_assignments"
    __table_args__ = (
        CheckConstraint("base_role in ('admin', 'user')", name="base_role"),
        CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name="active_revoked",
        ),
        Index("uq_org_role_active_membership", "membership_id", unique=True, postgresql_where=text("is_active = true")),
        Index("ix_org_role_membership_active", "membership_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    assigned_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrganizationMemberCapability(Base):
    __tablename__ = "organization_member_capabilities"
    __table_args__ = (
        CheckConstraint("capability in ('contributor', 'symbol_reviewer')", name="capability"),
        CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name="active_revoked",
        ),
        Index(
            "uq_org_capability_active_membership",
            "membership_id",
            "capability",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("ix_org_capability_membership_active", "membership_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    granted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlatformRoleAssignment(Base):
    __tablename__ = "platform_role_assignments"
    __table_args__ = (
        CheckConstraint("role in ('platform_admin')", name="role"),
        CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name="active_revoked",
        ),
        Index(
            "uq_platform_role_active_user_role",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("ix_platform_role_user_active", "user_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    assigned_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthOrganizationSelectionChallenge(Base):
    __tablename__ = "auth_organization_selection_challenges"
    __table_args__ = (
        CheckConstraint("max_attempts = 5", name="max_attempts"),
        CheckConstraint("attempt_count >= 0 and attempt_count <= max_attempts", name="attempt_bounds"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        CheckConstraint(
            "eligible_organizations_hash ~ '^[0-9a-f]{64}$'",
            name="eligible_hash",
        ),
        CheckConstraint(
            "expires_at = created_at + interval '10 minutes'",
            name="expiry",
        ),
        Index("uq_org_selection_token_hash", "token_hash", unique=True),
        Index("ix_org_selection_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    eligible_organizations_hash: Mapped[str] = mapped_column(Text, nullable=False)
    eligible_organizations_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthLoginThrottleBucket(Base):
    __tablename__ = "auth_login_throttle_buckets"
    __table_args__ = (
        CheckConstraint("scope in ('account', 'ip')", name="ck_scope"),
        CheckConstraint("failure_count >= 0", name="ck_failure_count"),
        Index("uq_auth_login_throttle_scope_key", "scope", "bucket_key_hash", unique=True),
        Index("ix_auth_login_throttle_blocked_until", "blocked_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    bucket_key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    window_started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthLoginAttemptEvent(Base):
    __tablename__ = "auth_login_attempt_events"
    __table_args__ = (
        CheckConstraint("outcome in ('success', 'failure', 'throttled')", name="ck_outcome"),
        CheckConstraint(
            "failure_reason is null or failure_reason in "
            "('invalid_credentials', 'inactive_or_deleted', 'throttled_account', 'throttled_ip')",
            name="ck_failure_reason",
        ),
        Index("ix_auth_login_attempt_occurred", "occurred_at"),
        Index("ix_auth_login_attempt_user_occurred", "resolved_user_id", "occurred_at"),
        Index("ix_auth_login_attempt_email_occurred", "email_key_hash", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    email_key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))


class AuthThrottleRecoveryEvent(Base):
    __tablename__ = "auth_throttle_recovery_events"
    __table_args__ = (
        CheckConstraint("scope in ('account', 'ip')", name="ck_scope"),
        Index("ix_auth_throttle_recovery_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    target_key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    cleared_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        CheckConstraint("tier in ('free', 'plus')", name="ck_user_subscriptions_tier"),
        CheckConstraint(
            "(tier = 'free' and expires_on is null and is_protected = false) or "
            "(tier = 'plus' and ((is_protected = true and expires_on is null) or expires_on is not null))",
            name="ck_user_subscriptions_tier_expiry",
        ),
        CheckConstraint("expires_on is null or expires_on > started_on", name="ck_user_subscriptions_dates"),
        CheckConstraint("anchor_day between 1 and 31", name="ck_user_subscriptions_anchor_day"),
        Index("ix_user_subscriptions_tier_expiry", "tier", "expires_on"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    started_on: Mapped[object] = mapped_column(Date, nullable=False)
    expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SubscriptionEvent(Base):
    __tablename__ = "subscription_events"
    __table_args__ = (
        CheckConstraint(
            "action in ('created', 'upgraded', 'adjusted', 'cancelled', 'expired', 'user_removed', 'owner_repaired')",
            name="ck_subscription_events_action",
        ),
        CheckConstraint("origin in ('admin', 'self_service', 'system', 'expiry')", name="ck_subscription_events_origin"),
        Index("ix_subscription_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'system'"))
    previous_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_tier: Mapped[str] = mapped_column(Text, nullable=False)
    previous_expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    new_expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'sent')", name="ck_email_outbox_status"),
        CheckConstraint("recipient_kind in ('customer', 'admin')", name="ck_email_outbox_recipient_kind"),
        Index("uq_email_outbox_event_recipient", "subscription_event_id", "recipient_kind", unique=True),
        Index("ix_email_outbox_pending", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscription_events.id", ondelete="CASCADE"), nullable=False
    )
    recipient_kind: Mapped[str] = mapped_column(Text, nullable=False)
    to_email: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CatalogFavourite(Base):
    __tablename__ = "catalog_favourites"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("governed_symbols.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogApiKey(Base):
    __tablename__ = "catalog_api_keys"
    __table_args__ = (
        CheckConstraint("status in ('active', 'disabled', 'revoked')", name="status"),
        Index("uq_catalog_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_catalog_api_keys_key_prefix", "key_prefix"),
        Index("ix_catalog_api_keys_customer_integration", "customer_name", "integration_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_name: Mapped[str] = mapped_column(Text, nullable=False)
    integration_name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_origins_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogApiUsageEvent(Base):
    __tablename__ = "catalog_api_usage_events"
    __table_args__ = (
        Index("ix_catalog_api_usage_events_api_key_created", "api_key_id", "created_at"),
        Index("ix_catalog_api_usage_events_customer_created", "customer_name_snapshot", "integration_name_snapshot", "created_at"),
        Index("ix_catalog_api_usage_events_route_created", "route_name", "status_code", "created_at"),
        Index("ix_catalog_api_usage_events_symbol_created", "symbol_ref", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("catalog_api_keys.id", ondelete="CASCADE"), nullable=False)
    customer_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    integration_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    scope_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    route_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ed_query_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class LLMUsageEvent(Base):
    """Authoritative, append-only record of one sanitized LLM attempt."""

    __tablename__ = "llm_usage_events"
    __table_args__ = (
        CheckConstraint("environment in ('development', 'test', 'staging', 'production')", name="llm_usage_events_environment"),
        CheckConstraint("use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit')", name="llm_usage_events_use_case"),
        CheckConstraint("service_name in ('symgov-api', 'libby', 'vlad')", name="llm_usage_events_service_name"),
        CheckConstraint("agent_slug is null or agent_slug in ('libby', 'vlad', 'ed')", name="llm_usage_events_agent_slug"),
        CheckConstraint("provider in ('openrouter', 'google', 'ollama')", name="llm_usage_events_provider"),
        CheckConstraint("request_kind in ('text', 'vision', 'image_generation')", name="llm_usage_events_request_kind"),
        CheckConstraint("status in ('succeeded', 'failed', 'timed_out', 'cancelled')", name="llm_usage_events_status"),
        CheckConstraint("cost_currency = 'USD'", name="llm_usage_events_cost_currency"),
        CheckConstraint("cost_basis in ('provider_reported', 'price_snapshot', 'local_policy', 'estimated', 'unknown')", name="llm_usage_events_cost_basis"),
        CheckConstraint("initiator_kind in ('user', 'api_key', 'admin', 'scheduled_worker', 'system')", name="llm_usage_events_initiator_kind"),
        CheckConstraint("attempt_number >= 1 and attempt_number <= 10000", name="llm_usage_events_attempt_number"),
        CheckConstraint("latency_ms is null or (latency_ms >= 0 and latency_ms <= 604800000)", name="llm_usage_events_latency_ms"),
        CheckConstraint("provider_reported_cost_usd is null or (provider_reported_cost_usd >= 0 and provider_reported_cost_usd <= 1000000)", name="llm_usage_events_provider_cost"),
        CheckConstraint("calculated_cost_usd is null or (calculated_cost_usd >= 0 and calculated_cost_usd <= 1000000)", name="llm_usage_events_calculated_cost"),
        CheckConstraint(
            "(cost_basis = 'provider_reported' and provider_reported_cost_usd is not null and calculated_cost_usd is null and pricing_version is null) or "
            "(cost_basis in ('price_snapshot', 'local_policy', 'estimated') and provider_reported_cost_usd is null and calculated_cost_usd is not null and pricing_version is not null) or "
            "(cost_basis = 'unknown' and provider_reported_cost_usd is null and calculated_cost_usd is null and pricing_version is null)",
            name="llm_usage_events_cost_provenance",
        ),
        CheckConstraint("(status = 'succeeded' and error_class is null and error_code is null) or (status <> 'succeeded' and (error_class is not null or error_code is not null))", name="llm_usage_events_status_errors"),
        *(
            CheckConstraint(f"{field} is null or ({field} >= 0 and {field} <= 1000000000000)", name=f"llm_usage_events_{field}")
            for field in (
                "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "reasoning_tokens", "image_input_units", "image_output_units",
            )
        ),
        CheckConstraint("jsonb_typeof(other_usage_json) = 'object'", name="llm_usage_events_other_usage_object"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="llm_usage_events_metadata_object"),
        UniqueConstraint("trace_id", "observation_id", name="llm_usage_events_trace_observation"),
        Index("ix_llm_usage_events_occurred_at_utc", "occurred_at_utc"),
        Index("ix_llm_usage_events_provider_occurred", "provider", "occurred_at_utc"),
        Index("ix_llm_usage_events_provider_model_occurred", "provider", "resolved_model", "occurred_at_utc"),
        Index("ix_llm_usage_events_use_case_occurred", "use_case", "occurred_at_utc"),
        Index("ix_llm_usage_events_agent_slug_occurred", "agent_slug", "occurred_at_utc"),
        Index("ix_llm_usage_events_feature_occurred", "feature", "occurred_at_utc"),
        Index("ix_llm_usage_events_trace_id", "trace_id"),
        Index("ix_llm_usage_events_trace_attempt", "trace_id", "attempt_number"),
        Index("ix_llm_usage_events_initiator_occurred", "initiator_pseudonym", "occurred_at_utc"),
        Index("ix_llm_usage_events_symbol_display_occurred", "symbol_display_id", "occurred_at_utc"),
        Index("ix_llm_usage_events_queue_item_id", "queue_item_id"),
        Index("ix_llm_usage_events_agent_run_id", "agent_run_id"),
        Index("ix_llm_usage_events_review_case_id", "review_case_id"),
        Index("ix_llm_usage_events_intake_record_id", "intake_record_id"),
        Index("ix_llm_usage_events_source_package_id", "source_package_id"),
        Index("ix_llm_usage_events_symbol_id", "symbol_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurred_at_utc: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    observation_id: Mapped[str] = mapped_column(Text, nullable=False)
    use_case: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    requested_model: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_model: Mapped[str] = mapped_column(Text, nullable=False)
    request_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_currency: Mapped[str] = mapped_column(Text, nullable=False)
    cost_basis: Mapped[str] = mapped_column(Text, nullable=False)
    provider_reported_cost_usd: Mapped[object | None] = mapped_column(Numeric(20, 9), nullable=True)
    calculated_cost_usd: Mapped[object | None] = mapped_column(Numeric(20, 9), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_write_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_input_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_output_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    other_usage_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    intake_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol_display_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    release: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiator_kind: Mapped[str] = mapped_column(Text, nullable=False)
    initiator_pseudonym: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    recorded_at_utc: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class GovernedSymbol(Base):
    __tablename__ = "governed_symbols"
    __table_args__ = (
        CheckConstraint(
            "visibility in ('organization_private', 'public')",
            name="visibility",
        ),
        CheckConstraint(
            "not organization_wide or owner_organization_id is not null",
            name="organization_wide_scope",
        ),
        CheckConstraint(
            "catalog_symbol_id is null or visibility = 'public'",
            name="catalog_symbol_visibility_barrier",
        ),
        Index(
            "ix_governed_symbols_owner_visibility_organization_wide",
            "owner_organization_id",
            "visibility",
            "organization_wide",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_symbol_id: Mapped[str | None] = mapped_column(Text, ForeignKey("catalog_symbol_identifiers.identifier", ondelete="RESTRICT"), nullable=True, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    discipline: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    owner_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'public'"))
    organization_wide: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogSymbolIdentifier(Base):
    __tablename__ = "catalog_symbol_identifiers"
    __table_args__ = (
        PrimaryKeyConstraint(
            "identifier",
            name="pk_catalog_symbol_identifiers",
        ),
        CheckConstraint(
            "role in ('canonical', 'historical_alias', 'tombstone')",
            name="role",
        ),
        CheckConstraint(
            "allocation_source in ('legacy_backfill', 'global_sequence', 'reviewed_correction')",
            name="allocation_source",
        ),
        CheckConstraint(
            "(role = 'tombstone' and governed_symbol_id is null) or (role in ('canonical', 'historical_alias') and governed_symbol_id is not null)",
            name="role_target",
        ),
        CheckConstraint(
            "identifier = upper(identifier) and identifier ~ '^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$'",
            name="grammar",
        ),
        Index(
            "uq_catalog_symbol_identifiers_canonical_governed_symbol",
            "governed_symbol_id",
            unique=True,
            postgresql_where=text("role = 'canonical'"),
        ),
    )

    identifier: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    governed_symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="SET NULL"), nullable=True)
    allocation_source: Mapped[str] = mapped_column(Text, nullable=False)
    allocated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_type: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductUsageEvent(Base):
    """Stage 9 WP9.1/WP9.2 -- append-only, server-derived authenticated
    browser/product-usage event ledger, kept as a domain separate from
    `AuditEvent` (governance-mutation audit trail), `CatalogApiUsageEvent`
    (API-key traffic only) and future contribution-reputation events, per
    the Stage 9 plan §1.1/§4 Q1 and decision addendum I-13. `event_type`
    covers WP9.1's browse-facing core subset (session start, context
    resolution, set selection, preview, download, Favorite change) plus
    WP9.2's governance-lifecycle additions (organization review, promotion,
    demotion, project/set lifecycle and selection, organization icon/role/
    capability/platform-admin changes) -- Stage 10's agent-finding events are
    not present, that stage's own concern. Rows are immutable once inserted
    (an `UPDATE` trigger enforces this in Postgres); `DELETE` remains
    permitted at the database level for the 90-day retention purge
    (`product_usage_retention.purge_expired_product_usage_events`), which is
    the one intentional way this table differs from `LLMUsageEvent`'s own
    fully append-only (UPDATE-or-DELETE-blocking) trigger."""

    __tablename__ = "product_usage_events"
    __table_args__ = (
        CheckConstraint(
            "event_type in ("
            "'personal_session_started', 'organization_selected', 'context_resolved', "
            "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed', "
            "'organization_review_submitted', 'organization_review_decided', 'organization_wide_changed', "
            "'publication_submitted', 'publication_decided', 'public_symbol_demoted', "
            "'project_created', 'project_updated', 'project_archived', 'project_selected', "
            "'set_created', 'set_updated', 'set_archived', 'set_project_availability_changed', "
            "'organization_role_changed', 'platform_admin_assigned', 'platform_admin_removed', "
            "'organization_icon_uploaded', 'organization_icon_removed'"
            ")",
            name="ck_product_usage_events_event_type",
        ),
        CheckConstraint("session_mode in ('personal', 'organization')", name="ck_product_usage_events_session_mode"),
        CheckConstraint(
            "(session_mode = 'personal' and organization_id is null) or (session_mode = 'organization' and organization_id is not null)",
            name="ck_product_usage_events_session_mode_organization",
        ),
        CheckConstraint(
            "symbol_source is null or symbol_source in ('public', 'organization_private')",
            name="ck_product_usage_events_symbol_source",
        ),
        CheckConstraint(
            "favourite_action is null or favourite_action in ('added', 'removed')",
            name="ck_product_usage_events_favourite_action",
        ),
        CheckConstraint(
            "context_resolution_basis is null or context_resolution_basis in "
            "('explicit', 'user_preference', 'project_default', 'organization_default', 'none')",
            name="ck_product_usage_events_context_resolution_basis",
        ),
        CheckConstraint(
            "(event_type = 'symbol_downloaded') = (format is not null)",
            name="ck_product_usage_events_format_only_on_download",
        ),
        CheckConstraint(
            "(event_type = 'favorite_changed') = (favourite_action is not null)",
            name="ck_product_usage_events_favourite_action_only_on_favorite_changed",
        ),
        CheckConstraint(
            "(event_type in ('context_resolved', 'set_selected')) = (context_resolution_basis is not null)",
            name="ck_product_usage_events_context_basis_only_on_context_events",
        ),
        Index("ix_product_usage_events_occurred_at", "occurred_at"),
        Index("ix_product_usage_events_org_event_occurred", "organization_id", "event_type", "occurred_at"),
        Index("ix_product_usage_events_event_occurred", "event_type", "occurred_at"),
        Index("ix_product_usage_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_product_usage_events_governed_symbol_occurred", "governed_symbol_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    session_mode: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    symbol_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_sets.id"), nullable=True)
    governed_symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=True)
    symbol_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    symbol_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    favourite_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_resolution_basis: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProductUsageDailyRollup(Base):
    """Stage 9 WP9.4 -- indefinitely-retained daily aggregate rollup of
    `ProductUsageEvent` rows, one row per (organization, event_type, day).
    Built by `product_usage_rollups.refresh_product_usage_rollups`, a
    standalone callable -- not wired to any scheduler, mirroring
    `product_usage_retention.purge_expired_product_usage_events`'s own
    precedent -- that re-aggregates raw rows on demand. WP9.4's own
    aggregate-read endpoints query only this table, never the raw
    `product_usage_events` table directly, so dashboard history survives
    the confirmed 90-day raw-row retention purge (Stage 9 plan §4 Q7).

    Only organization-scoped activity (`organization_id is not null` on the
    source row) is rolled up: WP9.4's endpoints are inherently per-
    organization dashboards (an Organization Admin's own org, or a Platform
    Admin's chosen org), and a `'personal'`-mode event with no organization
    has no per-org dashboard to appear on.

    `distinct_user_count` is stored per cell (not just `event_count`) so the
    confirmed 3-distinct-user minimum aggregation threshold (§4 Q7) can be
    enforced at read time without ever re-touching raw rows -- a dashboard
    must suppress any cell whose `distinct_user_count < 3`."""

    __tablename__ = "product_usage_daily_rollups"
    __table_args__ = (
        UniqueConstraint("organization_id", "event_type", "occurred_on", name="uq_product_usage_daily_rollups_cell"),
        CheckConstraint(
            "event_type in ("
            "'personal_session_started', 'organization_selected', 'context_resolved', "
            "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed', "
            "'organization_review_submitted', 'organization_review_decided', 'organization_wide_changed', "
            "'publication_submitted', 'publication_decided', 'public_symbol_demoted', "
            "'project_created', 'project_updated', 'project_archived', 'project_selected', "
            "'set_created', 'set_updated', 'set_archived', 'set_project_availability_changed', "
            "'organization_role_changed', 'platform_admin_assigned', 'platform_admin_removed', "
            "'organization_icon_uploaded', 'organization_icon_removed'"
            ")",
            name="ck_product_usage_daily_rollups_event_type",
        ),
        CheckConstraint("event_count >= 0", name="ck_product_usage_daily_rollups_event_count_non_negative"),
        CheckConstraint("distinct_user_count >= 0", name="ck_product_usage_daily_rollups_distinct_user_count_non_negative"),
        CheckConstraint("distinct_user_count <= event_count", name="ck_product_usage_daily_rollups_distinct_le_event_count"),
        Index("ix_product_usage_daily_rollups_org_occurred", "organization_id", "occurred_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_on: Mapped[object] = mapped_column(Date, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_user_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ContributionEvent(Base):
    """Stage 9 WP9.5 -- append-only contribution/reputation ledger, a
    structurally separate table/domain from `ProductUsageEvent` (spec §8
    line 411, Stage 9 plan §2 item 5), imitating WP9.1's own frozen-
    `CheckConstraint`-vocabulary/immutable-row pattern rather than being
    built on top of WP9.2-9.4. Per Q2 there is deliberately no `points`
    column -- counts and badges only, pending a later versioned scoring
    policy. `event_type` uses the exact two names spec Appendix B's own
    event catalog gives this domain (`contribution_awarded` /
    `contribution_reversed`), not an invented category vocabulary --
    today's only wired trigger for `contribution_awarded` is a symbol's
    public promotion being accepted (`organization_promotion_handoff.
    execute_organization_promotion_handoff`); more categories (accepted
    significant revision, format/accessibility improvement, etc. -- spec
    §12.1's illustrative, not-yet-built list) can extend this
    `CheckConstraint` additively later exactly as WP9.2 extended WP9.1's own
    `event_type` vocabulary, without redesigning this table.

    Corrections use reversal entries (spec §12.4/§12.2's "demotion or
    invalidation may reverse contribution events"), never in-place
    mutation: a `contribution_reversed` row carries `reversed_event_id`
    pointing back at the original award row, which itself stays immutable
    (an `UPDATE`-blocking trigger, mirroring WP9.1's own). `reversed_event_id`
    is deliberately a plain UUID column with no foreign-key constraint --
    both rows already carry independent copies of every dimension column
    (`organization_id`/`user_id`/`submission_id`/`governed_symbol_id`/
    `symbol_revision_id`), so `reversed_event_id` is traceability only, not
    load-bearing for any query. This is also why it is safe for it to point
    at an id that no longer exists once the original award row ages past
    this table's own 90-day retention purge (Q7, mirroring WP9.1's) --
    an enforced `ON DELETE SET NULL` foreign key here would instead collide
    with the immutability trigger, which must reject that in-place `UPDATE`
    on every other row.

    Badge state (`OrganizationBadge`) and lifetime accepted/reversed
    counters (`OrganizationContributionTotal`) are both written
    synchronously, in the same transaction as the row that triggers them,
    and are never re-derived from this (purgeable) ledger afterward --
    mirroring WP9.4's own "an aggregate must outlive the raw-row purge"
    precedent (Q10), scaled down to a running counter/one-shot badge shape
    since contribution volume needs no daily-granularity/distinct-user
    tracking the way browse-event dashboards do.

    Badges are not revoked when a contribution is reversed -- deciding
    whether/how to do that is left to WP9.6's own anti-gaming scope, not
    decided here, per `CLAUDE.md`'s prohibition on inventing an
    invalidation policy without confirmation."""

    __tablename__ = "contribution_events"
    __table_args__ = (
        CheckConstraint(
            "event_type in ('contribution_awarded', 'contribution_reversed')",
            name="ck_contribution_events_event_type",
        ),
        CheckConstraint(
            "(event_type = 'contribution_reversed') = (reason is not null)",
            name="ck_contribution_events_reason_only_on_reversal",
        ),
        Index("ix_contribution_events_organization_occurred", "organization_id", "occurred_at"),
        Index("ix_contribution_events_occurred_at", "occurred_at"),
        Index("ix_contribution_events_submission_id", "submission_id"),
        Index("ix_contribution_events_governed_symbol_occurred", "governed_symbol_id", "occurred_at"),
        Index("ix_contribution_events_reversed_event_id", "reversed_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("promotion_requests.id"), nullable=False)
    governed_symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=True)
    symbol_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    reversed_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationBadge(Base):
    """Stage 9 WP9.5 -- indefinitely-retained badge-award record, mirroring
    `ProductUsageDailyRollup`'s own "outlive the raw ledger's purge" design
    (Q10 precedent). Once an organization's `ContributionEvent` rows age
    out at 90 days, this is the only place the fact "this organization
    earned badge X" survives -- each row is written once, when the
    organization first meets a badge's trigger, and never re-derived from
    the ledger afterward.

    `badge_type` is deliberately scoped to only the two badges this
    package actually computes (Q3: First Contribution, Contributor
    Organization -- both share the identical trigger, an organization's
    first-ever `contribution_awarded` row, so both rows are always written
    together). Community Partner and the two already-deferred badges
    (Multi-Discipline Contributor, Metadata Improver) are not in this
    vocabulary yet; each can be added additively, exactly as WP9.2
    additively extended WP9.1's own `event_type` `CheckConstraint`, once its
    own trigger is defined.

    `source_event_id` is traceability only (which ledger row triggered the
    award) and is nulled out (`ondelete="SET NULL"`) rather than blocking
    that row's own retention purge -- unlike `ContributionEvent.
    reversed_event_id`, this is safe as a real foreign key because this
    table carries no immutability trigger of its own to collide with the
    resulting `UPDATE`."""

    __tablename__ = "organization_badges"
    __table_args__ = (
        UniqueConstraint("organization_id", "badge_type", name="uq_organization_badges_org_badge"),
        CheckConstraint(
            "badge_type in ('first_contribution', 'contributor_organization')",
            name="ck_organization_badges_badge_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    badge_type: Mapped[str] = mapped_column(Text, nullable=False)
    awarded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contribution_events.id", ondelete="SET NULL"), nullable=True
    )


class OrganizationContributionTotal(Base):
    """Stage 9 WP9.5 -- one row per organization, a lifetime running total
    of accepted/reversed `ContributionEvent` rows that survives this
    ledger's own 90-day retention purge (see `ContributionEvent`'s own
    docstring). Incremented synchronously in the same transaction as the
    ledger row that causes it (`contribution_events.record_contribution_
    awarded`/`reverse_contributions_for_symbol`) via `INSERT ... ON
    CONFLICT ... DO UPDATE`, never recomputed by re-scanning the (purgeable)
    raw ledger -- the counter itself is this package's read model for
    `GET /org/me/contributions` / `GET /platform/organizations/{id}/
    contributions`'s own count fields."""

    __tablename__ = "organization_contribution_totals"
    __table_args__ = (
        CheckConstraint("accepted_count >= 0", name="ck_organization_contribution_totals_accepted_non_negative"),
        CheckConstraint("reversed_count >= 0", name="ck_organization_contribution_totals_reversed_non_negative"),
        CheckConstraint("reversed_count <= accepted_count", name="ck_organization_contribution_totals_reversed_le_accepted"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reversed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class UserContributionTotal(Base):
    """Stage 9 WP9.8 -- one row per user, a lifetime running total of
    accepted/reversed `ContributionEvent` rows attributed to that user
    (`ContributionEvent.user_id`), mirroring `OrganizationContributionTotal`'s
    own "must outlive the ledger's 90-day retention purge" design exactly.
    Incremented synchronously in the same transaction as the ledger row that
    causes it (`contribution_events.record_contribution_awarded`/
    `reverse_contributions_for_symbol`), never recomputed by re-scanning the
    (purgeable) raw ledger.

    This is the read model behind `GET /profile/contributions` -- spec
    §12.2's "individual users may see private contribution/activity
    statistics in their profile" -- which is deliberately self-service
    (any authenticated user reads only their own row via their own session
    identity) rather than gated behind Organization/Platform Admin the way
    `organization_contribution_totals` is. Only accepted/reversed counts are
    exposed here, not badges -- §12.2 lists badges under "Organization
    badges", a separate, already-shipped organization-level concept."""

    __tablename__ = "user_contribution_totals"
    __table_args__ = (
        CheckConstraint("accepted_count >= 0", name="ck_user_contribution_totals_accepted_non_negative"),
        CheckConstraint("reversed_count >= 0", name="ck_user_contribution_totals_reversed_non_negative"),
        CheckConstraint("reversed_count <= accepted_count", name="ck_user_contribution_totals_reversed_le_accepted"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reversed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        CheckConstraint(
            "identity_type in ('engineer', 'contractor', 'submitter', 'external_reviewer', 'other')",
            name="identity_type",
        ),
        CheckConstraint("status in ('active', 'inactive')", name="status"),
        Index(
            "uq_external_identities_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolRevision(Base):
    __tablename__ = "symbol_revisions"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn')",
            name="lifecycle_state",
        ),
        Index("uq_symbol_revisions_symbol_revision_label", "symbol_id", "revision_label", unique=True),
        Index("ix_symbol_revisions_symbol_created_at", "symbol_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    revision_label: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationSymbolReviewSubmission(Base):
    __tablename__ = "organization_symbol_review_submissions"
    __table_args__ = (
        CheckConstraint(
            "(status = 'active' and closed_at is null) or (status = 'closed' and closed_at is not null)",
            name="status",
        ),
        CheckConstraint(
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="rationale",
        ),
        Index(
            "uq_organization_symbol_review_submissions_active_revision",
            "symbol_revision_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_org_symbol_review_submissions_tenant_symbol_revision",
            "organization_id",
            "governed_symbol_id",
            "symbol_revision_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    governed_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id", ondelete="RESTRICT"), nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrganizationSymbolReviewDecision(Base):
    __tablename__ = "organization_symbol_review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision in ('approved', 'rejected', 'changes_requested')",
            name="decision",
        ),
        CheckConstraint(
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="rationale",
        ),
        Index(
            "ix_org_symbol_review_decisions_tenant_symbol_revision",
            "organization_id",
            "governed_symbol_id",
            "symbol_revision_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization_symbol_review_submissions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    governed_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id", ondelete="RESTRICT"), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionRequest(Base):
    """Stage 7 WP7.2 -- dedicated organization-side public-promotion
    submission record (programme plan §13, decision addendum I-10). Snapshots
    the organization-approved revision at submission time; does not overload
    `SymbolRevision.lifecycle_state` or public `ReviewCase`."""

    __tablename__ = "promotion_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('submitted', 'triage', 'in_review', 'changes_requested', 'accepted', 'rejected', 'withdrawn')",
            name="status",
        ),
        CheckConstraint(
            "(status in ('submitted', 'triage', 'in_review', 'changes_requested') and closed_at is null) "
            "or (status in ('accepted', 'rejected', 'withdrawn') and closed_at is not null)",
            name="closed_state",
        ),
        CheckConstraint("btrim(reason) <> '' and char_length(reason) <= 2000", name="reason"),
        CheckConstraint("sharing_acknowledgment = true", name="sharing_acknowledgment"),
        Index(
            "uq_promotion_requests_active_symbol",
            "governed_symbol_id",
            unique=True,
            postgresql_where=text("status in ('submitted', 'triage', 'in_review', 'changes_requested')"),
        ),
        Index("ix_promotion_requests_organization_symbol", "organization_id", "governed_symbol_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    governed_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    sharing_acknowledgment: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id", ondelete="RESTRICT"), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stage 9 WP9.6 -- populated at submission time (submit_promotion_request)
    # when an existing *different* public GovernedSymbol shares this
    # symbol's canonical_name/category/discipline (spec §12.4's "deduplicate
    # submissions before review"). Informational only -- per Chris's
    # confirmed design the submission is still accepted, not blocked; the
    # reviewer sees this flag via PromotionRequestResponse.
    possible_duplicate_governed_symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionRequestDecision(Base):
    """Append-only transition log for a `PromotionRequest` -- one row per
    state transition (unlike Stage 5's 1:1 submission/decision pair, a
    promotion request can move through several transitions over its
    lifetime). WP7.2 only ever writes a `withdrawn` transition; later work
    packages add the reviewer-facing transitions on this same table."""

    __tablename__ = "promotion_request_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_code in ('triage', 'in_review', 'changes_requested', 'accepted', 'rejected', 'withdrawn')",
            name="decision_code",
        ),
        CheckConstraint("note is null or (btrim(note) <> '' and char_length(note) <= 2000)", name="note"),
        Index("ix_promotion_request_decisions_request_created_at", "promotion_request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("promotion_requests.id", ondelete="RESTRICT"), nullable=False)
    decision_code: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str] = mapped_column(Text, nullable=False)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    decider_name: Mapped[str] = mapped_column(Text, nullable=False)
    decider_role: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePackage(Base):
    """The durable acquisition envelope for an ingestion batch or library release.

    Specification section 7.11. The five columns above `provider_package_-`
    `identifier` pre-date the semantic model and are written by the live
    submission-intake path in `runtime.ensure_source_package_for_intake`; none
    of them is governed by a vocabulary here, because a check constraint on a
    pre-existing column would be validated against rows that path already
    wrote. Everything SM-P0-05 adds is optional acquisition provenance.
    """

    __tablename__ = "source_packages"
    __table_args__ = (
        # Section 7.11. The fifth method vocabulary in this model, and the
        # only one that shares no value with the other four. Deliberately not
        # unified; see `source_package_acquisition.PACKAGE_ACQUISITION_METHODS`.
        CheckConstraint(
            "acquisition_method is null or acquisition_method in "
            "('manual_upload', 'public_download', 'licensed_download', 'api', 'contributed', 'generated')",
            name="acquisition_method",
        ),
        # Half an acquisition event is not a record of one. Both sides are
        # boolean, so this can never evaluate to NULL -- the three-valued-logic
        # trap that bit SM-P0-03's checksum pairing.
        CheckConstraint(
            "(acquired_at is null) = (acquisition_method is null)",
            name="acquisition_pairing",
        ),
        # Sections 7.10 and 7.11 name SHA-256 specifically, so this is a fixed
        # 64-character grammar rather than SM-P0-03's algorithm-paired
        # `^[0-9a-f]{32,128}$`, which would admit an MD5 digest.
        CheckConstraint(
            "package_sha256 is null or package_sha256 ~ '^[0-9a-f]{64}$'",
            name="package_sha256",
        ),
        CheckConstraint(
            "package_sha256 is null or acquired_at is not null",
            name="package_integrity_context",
        ),
        CheckConstraint(
            "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
            name="source_uri",
        ),
        CheckConstraint(
            "provider_package_identifier is null or (btrim(provider_package_identifier) <> '' "
            "and char_length(provider_package_identifier) <= 512)",
            name="provider_package_identifier",
        ),
        CheckConstraint(
            "release_version is null or (btrim(release_version) <> '' and char_length(release_version) <= 128)",
            name="release_version",
        ),
        CheckConstraint(
            "licence_reference is null or (btrim(licence_reference) <> '' and char_length(licence_reference) <= 512)",
            name="licence_reference",
        ),
        CheckConstraint(
            "ingestion_profile is null or (btrim(ingestion_profile) <> '' and char_length(ingestion_profile) <= 256)",
            name="ingestion_profile",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="metadata_json_object",
        ),
        Index(
            "ix_source_packages_provider_package_identifier",
            "provider",
            "provider_package_identifier",
            postgresql_where=text("provider_package_identifier is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    # Section 7.11 acquisition provenance (SM-P0-05). All optional: the
    # submission-intake path records none of it, and section 7.11 marks every
    # one of these "recommended", not required.
    provider_package_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    acquired_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A reference to terms/contract/rights record, never the licence text
    # (section 7.12). The structured rights record it will point at is
    # SM-P0-06; nothing here models rights.
    licence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingestion_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Standard(Base):
    __tablename__ = "standards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    standard_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class StandardVersion(Base):
    """One edition of a standard.

    Specification section 7.10 preserves this entity and extends none of it.
    `provider_identifier` is the one addition (migration 20260910_0055): the
    identifier a reference-data provider gives this edition in its own
    register. It sits on the edition, not the standard, because that is what
    such registers enumerate -- CFIHOS carries two rows for API Spec 17D, one
    per edition, numbered separately.
    """

    __tablename__ = "standard_versions"
    __table_args__ = (
        CheckConstraint(
            "provider_identifier is null or (btrim(provider_identifier) <> '' "
            "and char_length(provider_identifier) <= 512)",
            name="provider_identifier",
        ),
        Index("uq_standard_versions_standard_version_label", "standard_id", "version_label", unique=True),
        Index("ix_standard_versions_standard_effective_date", "standard_id", "effective_date"),
        # Partial so the editions that have no provider identifier -- every
        # one registered by hand -- stay out of it entirely.
        Index(
            "uq_standard_versions_provider_identifier",
            "provider_identifier",
            unique=True,
            postgresql_where=text("provider_identifier is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    standard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False)
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePackageEntry(Base):
    """One symbol's place inside an acquired package.

    Specification section 7.11's second half: `source_label` is retained, and
    SM-P0-05 adds the three columns that let an individual symbol be traced
    within the package -- Appendix B.2's "exact provider entry/symbol ID".
    """

    __tablename__ = "source_package_entries"
    __table_args__ = (
        CheckConstraint(
            "provider_entry_identifier is null or (btrim(provider_entry_identifier) <> '' "
            "and char_length(provider_entry_identifier) <= 512)",
            name="provider_entry_identifier",
        ),
        CheckConstraint(
            "source_path is null or (btrim(source_path) <> '' and char_length(source_path) <= 1024)",
            name="source_path",
        ),
        CheckConstraint(
            "original_asset_sha256 is null or original_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="original_asset_sha256",
        ),
        # A hash that names no asset traces nothing. Every branch tests a
        # column against NULL explicitly, so the constraint is true or false
        # and never NULL.
        CheckConstraint(
            "original_asset_sha256 is null or source_path is not null "
            "or provider_entry_identifier is not null",
            name="original_asset_context",
        ),
        Index("uq_source_package_entries_package_revision", "source_package_id", "symbol_revision_id", unique=True),
        Index("ix_source_package_entries_package_sort_order", "source_package_id", "sort_order"),
        Index("ix_source_package_entries_revision_package", "symbol_revision_id", "source_package_id"),
        Index(
            "ix_source_package_entries_provider_entry_identifier",
            "source_package_id",
            "provider_entry_identifier",
            postgresql_where=text("provider_entry_identifier is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_packages.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_entry_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Section 7.11 offers `source_path/source_locator`. This addresses the
    # entry *inside* the package; the package's own location is
    # `source_packages.source_uri`, so a second "locator" would duplicate it.
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_asset_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)


class SymbolStandardLink(Base):
    """A governed assertion about a symbol revision's relationship to a source.

    Specification section 7.10. The point of SM-P0-05 is that this row can now
    distinguish a precise normative graphical source from a loose reference:
    which relationship (section 8.3), which symbol number inside the standard,
    which figure or table, and whether anyone has verified it.
    """

    __tablename__ = "symbol_standard_links"
    __table_args__ = (
        # Section 8.3, in full. The column has been NOT NULL and unconstrained
        # since 20260409_0001 with no writer anywhere, so there is no legacy
        # value to accommodate.
        CheckConstraint(
            "relationship_type in ('normative_definition', 'normative_equivalent', "
            "'informative_example', 'vendor_implementation', 'owner_variant', "
            "'project_deviation', 'derived_from', 'comparison_only')",
            name="relationship_type",
        ),
        CheckConstraint(
            "assertion_status in ('proposed', 'verified', 'rejected', 'retired')",
            name="assertion_status",
        ),
        # Section 7.10. The fourth method vocabulary in this model, kept
        # distinct from the other four on purpose; see
        # `standard_sources.STANDARD_VERIFICATION_METHODS`.
        CheckConstraint(
            "verification_method is null or verification_method in "
            "('manual', 'import_manifest', 'source_api', 'ai_assisted')",
            name="verification_method",
        ),
        # A verification must record when it happened and what it relied on.
        # `verified_by_user_id` stays optional so a deterministic import
        # verified under explicit policy (section 8.4) needs no invented user.
        # `assertion_status` is NOT NULL, so neither branch can be NULL.
        CheckConstraint(
            "assertion_status <> 'verified' or (verified_at is not null and verification_method is not null)",
            name="verified_decision",
        ),
        # Forbidding the empty string is what makes COALESCE(clause_reference,
        # '') in the active-assertion index below unambiguous.
        CheckConstraint(
            "clause_reference is null or (btrim(clause_reference) <> '' and char_length(clause_reference) <= 256)",
            name="clause_reference",
        ),
        CheckConstraint(
            "source_symbol_identifier is null or (btrim(source_symbol_identifier) <> '' "
            "and char_length(source_symbol_identifier) <= 512)",
            name="source_symbol_identifier",
        ),
        CheckConstraint(
            "figure_reference is null or (btrim(figure_reference) <> '' and char_length(figure_reference) <= 256)",
            name="figure_reference",
        ),
        CheckConstraint(
            "table_reference is null or (btrim(table_reference) <> '' and char_length(table_reference) <= 256)",
            name="table_reference",
        ),
        CheckConstraint(
            "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
            name="source_uri",
        ),
        # Section 7.10 names SHA-256 specifically, so a fixed 64-character
        # grammar and no algorithm column.
        CheckConstraint(
            "source_asset_sha256 is null or source_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="source_asset_sha256",
        ),
        CheckConstraint(
            "source_asset_sha256 is null or source_uri is not null",
            name="source_asset_provenance",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # Replaces 20260409_0001's
        # `uq_symbol_standard_links_revision_standard_relationship_clause`,
        # which included the nullable `clause_reference` and so enforced
        # nothing for the NULL-clause case PostgreSQL treats as distinct.
        # COALESCE rather than NULLS NOT DISTINCT because the latter needs
        # PostgreSQL 15+ and the deployed server version is not recorded here.
        # Partial on the live states so a rejected assertion does not block
        # re-proposing, and so supersession can retire a predecessor.
        Index(
            "uq_symbol_standard_links_active_assertion",
            "symbol_revision_id",
            "standard_version_id",
            "relationship_type",
            text("coalesce(clause_reference, '')"),
            unique=True,
            postgresql_where=text("assertion_status in ('proposed', 'verified')"),
        ),
        # One standard version formally defines a symbol revision at most
        # once: section 9.2's gate wants the graphical authority asserted
        # unambiguously, and Appendix B.2's chain carries a single normative
        # definition. Proposals stay unconstrained so candidates can compete.
        Index(
            "uq_symbol_standard_links_verified_definition",
            "symbol_revision_id",
            "standard_version_id",
            unique=True,
            postgresql_where=text(
                "relationship_type = 'normative_definition' and assertion_status = 'verified'"
            ),
        ),
        Index("ix_symbol_standard_links_revision_standard", "symbol_revision_id", "standard_version_id"),
        # Section 14.3 names this read path explicitly. Spelled short: the
        # convention's own name for these two columns is 66 characters.
        Index(
            "ix_symbol_standard_links_version_source_symbol_identifier",
            "standard_version_id",
            "source_symbol_identifier",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    standard_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standard_versions.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    clause_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    # Section 7.10 source precision (SM-P0-05).
    source_symbol_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    figure_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    table_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    assertion_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    verification_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_asset_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_standard_links_verified_by_user_id"),
        nullable=True,
    )
    verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class PublicationPack(Base):
    __tablename__ = "publication_packs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[object] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PublishedPage(Base):
    __tablename__ = "published_pages"
    __table_args__ = (
        CheckConstraint(
            "publication_state in ('active', 'retired')",
            name="publication_state",
        ),
        CheckConstraint(
            "(publication_state = 'active' and retired_by is null and retired_at is null) "
            "or (publication_state = 'retired' and retired_at is not null)",
            name="retirement_metadata",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    current_symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    effective_date: Mapped[object] = mapped_column(Date, nullable=False)
    publication_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    retired_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    retired_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retirement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    __table_args__ = (
        Index("ix_change_requests_status_priority_due_date", "status", "priority", "due_date"),
        Index("ix_change_requests_proposed_revision_status_created_at", "proposed_revision_id", "status", "created_at"),
        Index("ix_change_requests_base_revision_created_at", "base_revision_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    proposed_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    revision_delta: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    due_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_change_request_created_at", "change_request_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PackEntry(Base):
    __tablename__ = "pack_entries"
    __table_args__ = (
        Index("uq_pack_entries_pack_revision_page", "pack_id", "symbol_revision_id", "published_page_id", unique=True),
        Index("ix_pack_entries_pack_sort_order", "pack_id", "sort_order"),
        CheckConstraint(
            "publication_state in ('active', 'retired')",
            name="publication_state",
        ),
        CheckConstraint(
            "(publication_state = 'active' and retired_by is null and retired_at is null) "
            "or (publication_state = 'retired' and retired_at is not null)",
            name="retirement_metadata",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    publication_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    retired_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    retired_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retirement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ImpactedPageLink(Base):
    __tablename__ = "impacted_page_links"
    __table_args__ = (Index("ix_impacted_page_links_change_request_published_page", "change_request_id", "published_page_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    impact_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClarificationRecord(Base):
    __tablename__ = "clarification_records"
    __table_args__ = (
        CheckConstraint(
            "(submitted_by is not null)::int + (external_submitter_id is not null)::int + (catalog_api_key_id is not null)::int = 1",
            name="exactly_one_submitter",
        ),
        Index("ix_clarification_records_symbol_page_created_at", "symbol_id", "published_page_id", "created_at"),
        Index("ix_clarification_records_external_submitter_created_at", "external_submitter_id", "created_at"),
        Index("ix_clarification_records_catalog_api_key_created_at", "catalog_api_key_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    external_submitter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("external_identities.id"), nullable=True)
    catalog_api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("catalog_api_keys.id"), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClarificationLink(Base):
    __tablename__ = "clarification_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clarification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clarification_records.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    linked_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    queue_family: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQueueItem(Base):
    __tablename__ = "agent_queue_items"
    __table_args__ = (Index("ix_agent_queue_items_agent_status_priority_created_at", "agent_id", "status", "priority", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentFeedbackEvent(Base):
    __tablename__ = "agent_feedback_events"
    __table_args__ = (
        Index("ix_agent_feedback_events_agent_created", "agent_slug", "created_at"),
        Index("ix_agent_feedback_events_source", "source_entity_type", "source_entity_id", "created_at"),
        Index("ix_agent_feedback_events_type_created", "feedback_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_slug: Mapped[str] = mapped_column(Text, nullable=False)
    feedback_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    original_value_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    corrected_value_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    applied_to_rules_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_to_prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ScottSourceDiscoverySite(Base):
    __tablename__ = "scott_source_discovery_sites"
    __table_args__ = (
        Index("uq_scott_source_discovery_sites_domain", text("lower(domain)"), unique=True),
        Index("ix_scott_source_discovery_sites_status_score_seen", "status", "relevance_score", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    process: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_next_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    requires_auth: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    auth_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'no_auth'"))
    auth_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_formats_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_session_queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)


class HannahSymbolCurationState(Base):
    __tablename__ = "hannah_symbol_curation_states"
    __table_args__ = (
        Index("uq_hannah_symbol_curation_states_symbol", "symbol_id", unique=True),
        Index("ix_hannah_symbol_curation_states_status_attempt", "status", "last_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class HannahPhotoCandidate(Base):
    __tablename__ = "hannah_photo_candidates"
    __table_args__ = (
        Index("ix_hannah_photo_candidates_symbol_status_score", "symbol_id", "status", "relevance_score"),
        Index("ix_hannah_photo_candidates_last_seen", "last_seen_at"),
        Index("uq_hannah_photo_candidates_image_symbol", "symbol_id", "image_url", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    published_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=True)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False)
    license_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"), nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class WhitneyMarketIntelligenceReport(Base):
    __tablename__ = "whitney_market_intelligence_reports"
    __table_args__ = (Index("ix_whitney_reports_queue_completed", "queue_item_id", "completed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    signals_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    recommendations_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class WhitneyDemandSignal(Base):
    __tablename__ = "whitney_demand_signals"
    __table_args__ = (
        Index("ix_whitney_demand_signals_type_score_seen", "signal_type", "demand_score", "last_seen_at"),
        Index("ix_whitney_demand_signals_segment_seen", "market_segment", "last_seen_at"),
        Index("uq_whitney_demand_signals_source", "source_type", "source_ref", "signal_type", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whitney_market_intelligence_reports.id"),
        nullable=True,
    )
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=True)
    published_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=True)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    market_segment: Mapped[str | None] = mapped_column(Text, nullable=True)
    discipline: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    demand_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_queue_item_started_at", "queue_item_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tool_trace_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentOutputArtifact(Base):
    __tablename__ = "agent_output_artifacts"
    __table_args__ = (Index("ix_agent_output_artifacts_queue_type_created_at", "queue_item_id", "artifact_type", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class HannahCurationSourceSite(Base):
    __tablename__ = "hannah_curation_source_sites"
    __table_args__ = (
        Index("uq_hannah_curation_source_sites_domain", text("lower(domain)"), unique=True),
        Index("ix_hannah_curation_source_sites_status_score", "status", "usefulness_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    usefulness_score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default=text("1.00"))
    reliability_score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default=text("1.00"))
    feedback_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_search_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntakeRecord(Base):
    __tablename__ = "intake_records"
    __table_args__ = (Index("ix_intake_records_status_eligibility_created_at", "intake_status", "eligibility_status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    submitter: Mapped[str] = mapped_column(Text, nullable=False)
    submission_kind: Mapped[str] = mapped_column(Text, nullable=False)
    intake_status: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    source_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("source_packages.id"), nullable=True)
    raw_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_submission_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    routing_recommendation_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ProvenanceAssessment(Base):
    __tablename__ = "provenance_assessments"
    __table_args__ = (
        # Both enumerations have been enforced in PostgreSQL since this table
        # was created; they were simply never declared here, so anything built
        # from this metadata got a laxer schema than production. Only these two
        # are declared: `rights_status` and `risk_level` carry no database
        # constraint, and inventing one here would make the ORM *stricter* than
        # production, which is the same defect pointing the other way.
        CheckConstraint(
            "processing_outcome in ('pass', 'review_required', 'failed')",
            name="processing_outcome",
        ),
        CheckConstraint(
            "rights_disposition in ('cleared', 'unknown_warning', 'restricted', 'conflict', 'failed')",
            name="rights_disposition",
        ),
        Index("ix_provenance_assessments_intake_assessed_at", "intake_record_id", "assessed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    intake_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_records.id"), nullable=False)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False)
    rights_disposition: Mapped[str] = mapped_column(Text, nullable=False)
    processing_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assessed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ValidationReport(Base):
    __tablename__ = "validation_reports"
    __table_args__ = (Index("ix_validation_reports_source_created_at", "source_type", "source_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False)
    defect_count: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationRecord(Base):
    __tablename__ = "classification_records"
    __table_args__ = (
        Index("ix_classification_records_symbol_status_created_at", "symbol_key", "status", "created_at"),
        Index("ix_classification_records_review_case_created_at", "review_case_id", "created_at"),
        Index("ix_classification_records_validation_report_created_at", "validation_report_id", "created_at"),
        Index("ix_classification_records_provenance_assessment_created_at", "provenance_assessment_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    intake_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_records.id"), nullable=True)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("validation_reports.id"), nullable=True)
    provenance_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("provenance_assessments.id"), nullable=True)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=True)
    origin_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"), nullable=True)
    origin_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=True)
    symbol_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_region_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'current'"))
    classification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'provisional'"))
    supersedes_classification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classification_records.id"),
        nullable=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    discipline: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_family: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_equipment_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    standards_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_provenance_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    search_terms_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_refs_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    taxonomy_terms_created_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    review_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    libby_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ReviewCase(Base):
    __tablename__ = "review_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    current_stage: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    escalation_level: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HumanReviewDecision(Base):
    __tablename__ = "human_review_decisions"
    __table_args__ = (Index("ix_human_review_decisions_case_created_at", "review_case_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    decision_code: Mapped[str] = mapped_column(Text, nullable=False)
    decision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decider_name: Mapped[str] = mapped_column(Text, nullable=False)
    decider_role: Mapped[str] = mapped_column(Text, nullable=False)
    from_stage: Mapped[str] = mapped_column(Text, nullable=False)
    to_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationApprovalTarget(Base):
    __tablename__ = "publication_approval_targets"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(revision_targets_json) = 'array' "
            "AND jsonb_array_length(revision_targets_json) > 0",
            name="publication_approval_targets_nonempty_revisions",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="publication_approval_targets_sha256",
        ),
        Index(
            "ix_publication_approval_targets_case_created_at",
            "review_case_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_review_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    review_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_targets_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewCaseAction(Base):
    __tablename__ = "review_case_actions"
    __table_args__ = (
        Index("ix_review_case_actions_case_status_created_at", "review_case_id", "action_status", "created_at"),
        Index("ix_review_case_actions_decision_created_at", "decision_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_review_decisions.id"), nullable=True)
    action_code: Mapped[str] = mapped_column(Text, nullable=False)
    action_status: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    target_agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewSplitItem(Base):
    __tablename__ = "review_split_items"
    __table_args__ = (
        Index("uq_review_split_items_case_child", "review_case_id", "child_key", unique=True),
        Index("ix_review_split_items_case_status", "review_case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    child_key: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_symbol_id: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_symbol_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_file_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'awaiting_decision'"))
    latest_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_review_decisions.id"), nullable=True)
    latest_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_case_actions.id"), nullable=True)
    downstream_agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    downstream_queue_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewSymbolProperty(Base):
    __tablename__ = "review_symbol_properties"
    __table_args__ = (
        Index("uq_review_symbol_properties_case_key", "review_case_id", "symbol_record_key", unique=True),
        Index("ix_review_symbol_properties_split_item", "review_split_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    review_split_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_split_items.id"), nullable=True)
    symbol_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    discipline: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'agent_initial'"))
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewSymbolPropertyOption(Base):
    __tablename__ = "review_symbol_property_options"
    __table_args__ = (
        CheckConstraint("field_name in ('category', 'discipline')", name="review_symbol_property_options_field_name"),
        Index("uq_review_symbol_property_options_field_key", "field_name", "normalized_key", unique=True),
        Index("ix_review_symbol_property_options_field_value", "field_name", "display_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationJob(Base):
    __tablename__ = "publication_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    artifact_manifest_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ControlException(Base):
    __tablename__ = "control_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentConfiguration(Base):
    """Stage 10 WP10.1 -- spec §8's "Future-compatible Hermes agent binding
    and scope policy" entity. `logical_agent_name` is frozen to the two O4
    logical capability names Stage 10 defines (`organization_steward`,
    `platform_governance`); per O4 these are deliberately not final Hermes
    agent/persona identities and this repository must not invent one.

    `model_alias` is an allowlisted-shape column only -- per Stage 10's own
    confirmed Q3, no `llm_router.py` resolution wiring exists yet, since v1
    finding-generation is entirely deterministic (Q4) and never depends on a
    model. It carries a format constraint, not a fixed enum, since a real
    Hermes-profile-backed alias vocabulary does not exist in this repository
    (I-21: the active Hermes `symgov` profile is the only future resolver).

    Exactly one configuration row may exist per (capability, scope): a
    partial unique index covers the single platform-scoped row per
    capability, a second covers at most one row per (capability,
    organization) pair. `scope_id` is null iff `scope_type = 'platform'` --
    platform scope is a distinct concept from the reserved `symgov`
    Organization row (mirroring `PlatformRoleAssignment`, which is likewise
    not organization-scoped), not an alias for it."""

    __tablename__ = "agent_configurations"
    __table_args__ = (
        CheckConstraint(
            "logical_agent_name in ('organization_steward', 'platform_governance')",
            name="ck_agent_configurations_logical_agent_name",
        ),
        CheckConstraint("scope_type in ('platform', 'organization')", name="ck_agent_configurations_scope_type"),
        CheckConstraint(
            "(scope_type = 'organization' and scope_id is not null) or (scope_type = 'platform' and scope_id is null)",
            name="ck_agent_configurations_scope_id_matches_type",
        ),
        CheckConstraint(
            "model_alias is null or model_alias ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_agent_configurations_model_alias_format",
        ),
        CheckConstraint(
            "jsonb_typeof(allowed_capabilities_json) = 'array'",
            name="ck_agent_configurations_allowed_capabilities_array",
        ),
        CheckConstraint(
            "octet_length(convert_to(allowed_capabilities_json::text, 'UTF8')) <= 8192",
            name="ck_agent_configurations_allowed_capabilities_size",
        ),
        Index(
            "uq_agent_configurations_platform_scope",
            "logical_agent_name",
            unique=True,
            postgresql_where=text("scope_type = 'platform'"),
        ),
        Index(
            "uq_agent_configurations_org_scope",
            "logical_agent_name",
            "scope_id",
            unique=True,
            postgresql_where=text("scope_type = 'organization'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logical_agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    model_alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_capabilities_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AgentFinding(Base):
    """Stage 10 WP10.1 -- spec §8's "Auditable advisory finding or issue;
    not a governed decision" entity. `finding_type` is frozen to exactly
    seven slugs: five of Organization Steward's six in-scope categories
    plus Platform Governance's two, per Stage 10's confirmed I-22
    vocabulary round -- `cross_tenant_authorization_failure` and
    `unresolved_governance_exception` are deliberately absent, not
    included-but-unpopulated, since neither has a durable data source
    (auth failures are raised as HTTP 403s today but never durably logged)
    or a defined meaning anywhere in the spec/addendum. `icon_generation_
    missing` is likewise absent -- `Organization.fallback_icon_svg` is
    never null and no icon-generation-attempt/failure tracking exists
    anywhere in the repository, confirmed during WP10.2's own design round,
    so this category has no data source either. Extending this
    `CheckConstraint` additively is a follow-up once each is separately
    resolved, mirroring WP9.2's own additive extension of WP9.1's
    `event_type` vocabulary.

    `fingerprint` is a deterministic hash over (capability scope, finding
    type, target entity, policy version) computed by the generating
    service, not by this table -- a partial unique index enforces I-22's
    one-active-finding rule (`status in ('open', 'acknowledged')`) so a
    repeated detection re-touches the existing row's `last_seen_at` via
    `INSERT ... ON CONFLICT` rather than creating a duplicate. `policy_version`
    versions the deterministic detection rule itself (there is no live model
    in v1 per Q4), giving the dashboard's "model/policy version" display
    (programme plan §16 UI requirement) a real, meaningful value even
    though no LLM is invoked.

    Findings are advisory only (FR-AGT-005/007): resolution requires a
    human actor and never itself performs a governed mutation. No
    retention/purge policy exists yet for this table -- unlike
    `ProductUsageEvent`/`ContributionEvent`, no number was confirmed this
    round, so no DELETE grant is issued and no purge job exists; this is a
    deliberately deferred follow-up, not an oversight."""

    __tablename__ = "agent_findings"
    __table_args__ = (
        CheckConstraint(
            "finding_type in ("
            "'reviewer_coverage_gap', 'review_backlog_stale', "
            "'project_health_issue', 'symbol_set_health_issue', 'unresolved_reference', "
            "'platform_admin_continuity_risk', 'duplicate_organization_suspected'"
            ")",
            name="ck_agent_findings_finding_type",
        ),
        CheckConstraint("severity in ('low', 'medium', 'high', 'critical')", name="ck_agent_findings_severity"),
        CheckConstraint(
            "status in ('open', 'acknowledged', 'dismissed', 'resolved', 'superseded')",
            name="ck_agent_findings_status",
        ),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_agent_findings_fingerprint_format"),
        CheckConstraint("btrim(summary) <> '' and char_length(summary) <= 2000", name="ck_agent_findings_summary_bounds"),
        CheckConstraint("jsonb_typeof(evidence_json) = 'object'", name="ck_agent_findings_evidence_object"),
        CheckConstraint(
            "octet_length(convert_to(evidence_json::text, 'UTF8')) <= 16384",
            name="ck_agent_findings_evidence_size",
        ),
        CheckConstraint("(acknowledged_at is null) = (acknowledged_by_user_id is null)", name="ck_agent_findings_acknowledged_pair"),
        CheckConstraint("(dismissed_at is null) = (dismissed_by_user_id is null)", name="ck_agent_findings_dismissed_pair"),
        CheckConstraint("(resolved_at is null) = (resolved_by_user_id is null)", name="ck_agent_findings_resolved_pair"),
        CheckConstraint(
            "(status = 'open' and dismissed_at is null and resolved_at is null and superseded_by_finding_id is null) or "
            "(status = 'acknowledged' and acknowledged_at is not null and dismissed_at is null and resolved_at is null and superseded_by_finding_id is null) or "
            "(status = 'dismissed' and dismissed_at is not null) or "
            "(status = 'resolved' and resolved_at is not null) or "
            "(status = 'superseded' and superseded_by_finding_id is not null)",
            name="ck_agent_findings_status_consistency",
        ),
        Index(
            "uq_agent_findings_active_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status in ('open', 'acknowledged')"),
        ),
        Index("ix_agent_findings_agent_config_status", "agent_config_id", "status"),
        Index("ix_agent_findings_entity", "entity_type", "entity_id"),
        Index("ix_agent_findings_last_seen_at", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_configurations.id", ondelete="RESTRICT"), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    finding_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    dismissed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    superseded_by_finding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_findings.id", ondelete="SET NULL"), nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issue_reference: Mapped[str | None] = mapped_column(Text, nullable=True)


class SemanticConcept(Base):
    """Stable identity for an engineering meaning, independent of any graphic."""

    __tablename__ = "semantic_concepts"
    __table_args__ = (
        CheckConstraint(
            "concept_code ~ '^SGC-[0-9]{8}$'",
            name="concept_code_grammar",
        ),
        CheckConstraint(
            "concept_kind in ('physical_equipment', 'function', 'property', 'state', 'action', 'annotation', 'connection', 'safety_function', 'other')",
            name="concept_kind",
        ),
        CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        Index("uq_semantic_concepts_concept_code", "concept_code", unique=True),
        Index("ix_semantic_concepts_status_concept_code", "status", "concept_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    concept_code: Mapped[str] = mapped_column(Text, nullable=False)
    concept_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    # The convention-generated name would be 67 characters; PostgreSQL truncates
    # identifiers at 63, so this foreign key is named explicitly.
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("semantic_concept_revisions.id", name="fk_semantic_concepts_current_revision_id"), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SemanticConceptRevision(Base):
    """Governed revision carrying the descriptive content of a semantic concept."""

    __tablename__ = "semantic_concept_revisions"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn')",
            name="lifecycle_state",
        ),
        CheckConstraint(
            "btrim(revision_label) <> '' and char_length(revision_label) <= 64",
            name="revision_label",
        ),
        CheckConstraint(
            "btrim(preferred_name) <> '' and char_length(preferred_name) <= 256",
            name="preferred_name",
        ),
        CheckConstraint(
            "btrim(definition) <> '' and char_length(definition) <= 8000",
            name="definition",
        ),
        CheckConstraint(
            "jsonb_typeof(aliases_json) = 'array'",
            name="aliases_json_array",
        ),
        CheckConstraint(
            "notes is null or (btrim(notes) <> '' and char_length(notes) <= 4000)",
            name="notes",
        ),
        CheckConstraint(
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="rationale",
        ),
        Index("uq_semantic_concept_revisions_concept_revision_label", "concept_id", "revision_label", unique=True),
        Index("ix_semantic_concept_revisions_concept_created_at", "concept_id", "created_at"),
        Index("ix_semantic_concept_revisions_lifecycle_state", "lifecycle_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    concept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("semantic_concepts.id", ondelete="RESTRICT"), nullable=False)
    revision_label: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_name: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    aliases_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SymbolSemanticAssignment(Base):
    """Bridge from a graphical symbol revision to the engineering meaning it carries."""

    __tablename__ = "symbol_semantic_assignments"
    __table_args__ = (
        CheckConstraint(
            "assignment_role in ('primary', 'qualifier', 'component')",
            name="assignment_role",
        ),
        CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted')",
            name="method",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        # A verification decision must record when it happened. reviewed_by stays
        # nullable so a deterministic import auto-verified under explicit policy
        # (specification section 8.4) is representable without inventing a user.
        CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # Specification section 7.9: at most one verified primary concept per
        # symbol revision. Proposals are deliberately unconstrained so competing
        # candidates can sit side by side for review.
        Index(
            "uq_symbol_semantic_assignments_verified_primary",
            "symbol_revision_id",
            unique=True,
            postgresql_where=text("assignment_role = 'primary' and status = 'verified'"),
        ),
        Index("ix_symbol_semantic_assignments_revision_status", "symbol_revision_id", "status"),
        Index("ix_symbol_semantic_assignments_concept_status", "semantic_concept_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Both foreign keys are named explicitly: the convention would generate 66-
    # and 68-character names, past PostgreSQL's 63-character identifier limit.
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_symbol_revision_id"),
        nullable=False,
    )
    semantic_concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_semantic_concept_id"),
        nullable=False,
    )
    assignment_role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalSemanticScheme(Base):
    """An external reference-data library SymGov maps concepts into."""

    __tablename__ = "external_semantic_schemes"
    __table_args__ = (
        CheckConstraint(
            "scheme_code ~ '^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$'",
            name="scheme_code",
        ),
        CheckConstraint(
            "btrim(title) <> '' and char_length(title) <= 256",
            name="title",
        ),
        CheckConstraint(
            "btrim(issuing_body) <> '' and char_length(issuing_body) <= 256",
            name="issuing_body",
        ),
        CheckConstraint(
            "base_uri is null or (base_uri ~ '^https?://' and char_length(base_uri) <= 1024)",
            name="base_uri",
        ),
        CheckConstraint(
            "status in ('active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        Index("uq_external_semantic_schemes_scheme_code", "scheme_code", unique=True),
        Index("ix_external_semantic_schemes_status_scheme_code", "status", "scheme_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheme_code: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_body: Mapped[str] = mapped_column(Text, nullable=False)
    base_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalSemanticSchemeVersion(Base):
    """One release of an external scheme; the unit every mapping must name."""

    __tablename__ = "external_semantic_scheme_versions"
    __table_args__ = (
        CheckConstraint(
            "btrim(version_label) <> '' and char_length(version_label) <= 128",
            name="version_label",
        ),
        CheckConstraint(
            "source_uri is null or (source_uri ~ '^https?://' and char_length(source_uri) <= 1024)",
            name="source_uri",
        ),
        CheckConstraint(
            # Both `is not null` tests are load-bearing: without them a
            # checksum with a NULL algorithm makes the second branch NULL
            # rather than false, and PostgreSQL accepts a check constraint
            # that evaluates to NULL.
            "(checksum is null and checksum_algorithm is null) or "
            "(checksum is not null and checksum_algorithm is not null "
            "and checksum ~ '^[0-9a-f]{32,128}$' "
            "and checksum_algorithm in ('md5', 'sha1', 'sha256', 'sha512'))",
            name="checksum_pairing",
        ),
        CheckConstraint(
            "etag is null or (btrim(etag) <> '' and char_length(etag) <= 256)",
            name="etag",
        ),
        # A hash or etag with no retrieval time cannot be reproduced, which
        # defeats the configuration-management traceability specification
        # section 3.2 asks of a versioned external dependency.
        CheckConstraint(
            "(checksum is null and etag is null) or retrieved_at is not null",
            name="integrity_retrieval",
        ),
        CheckConstraint(
            "status in ('active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        Index(
            "uq_external_semantic_scheme_versions_scheme_version_label",
            "scheme_id",
            "version_label",
            unique=True,
        ),
        Index("ix_external_semantic_scheme_versions_scheme_status", "scheme_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Both foreign keys are named explicitly: the convention would generate 72-
    # and 61-character names against PostgreSQL's 63-character limit.
    scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_semantic_schemes.id", ondelete="RESTRICT", name="fk_external_semantic_scheme_versions_scheme_id"),
        nullable=False,
    )
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    release_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum_algorithm: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_external_semantic_scheme_versions_created_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ConceptExternalReference(Base):
    """A governed mapping from a SymGov concept into one external release.

    The external identifier lives here and never on the concept: specification
    section 7.5 and principle P-04 keep SymGov identity stable when an external
    scheme splits, merges, renames or deprecates a class.
    """

    __tablename__ = "concept_external_references"
    __table_args__ = (
        CheckConstraint(
            "btrim(external_identifier) <> '' and char_length(external_identifier) <= 512",
            name="external_identifier",
        ),
        CheckConstraint(
            "external_label is null or (btrim(external_label) <> '' and char_length(external_label) <= 512)",
            name="external_label",
        ),
        CheckConstraint(
            "mapping_type in ('exact', 'close', 'broader', 'narrower', 'related')",
            name="mapping_type",
        ),
        CheckConstraint(
            "mapping_status in ('proposed', 'verified', 'rejected', 'retired')",
            name="mapping_status",
        ),
        CheckConstraint(
            "mapping_method in ('manual', 'imported', 'rule', 'ai_assisted')",
            name="mapping_method",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        # A verification decision must record when it happened. reviewed_by
        # stays nullable so a deterministic import auto-verified under explicit
        # policy (specification section 8.4) needs no invented user.
        CheckConstraint(
            "mapping_status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # Specification section 16.2, as far as a constraint can carry it: the
        # strongest mapping type may not reach `verified` anonymously, and may
        # not do so with no evidence at all.
        CheckConstraint(
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or reviewed_by_user_id is not null or mapping_method = 'imported'",
            name="verified_exact_reviewer",
        ),
        CheckConstraint(
            "mapping_status <> 'verified' or mapping_type <> 'exact' "
            "or evidence_json <> '{}'::jsonb",
            name="verified_exact_evidence",
        ),
        # Section 14.3 asks for these two read paths explicitly.
        Index(
            "ix_concept_external_references_scheme_version_identifier",
            "scheme_version_id",
            "external_identifier",
        ),
        Index("ix_concept_external_references_concept_status", "semantic_concept_id", "mapping_status"),
        # One live assertion per (concept, release, external identifier).
        # Rejected and retired rows stay out of the index so the governance
        # history of a mapping survives alongside its replacement.
        Index(
            "uq_concept_external_references_active_mapping",
            "semantic_concept_id",
            "scheme_version_id",
            "external_identifier",
            unique=True,
            postgresql_where=text("mapping_status in ('proposed', 'verified')"),
        ),
        # Two classes of one release cannot both be exactly this concept
        # without asserting those two classes are themselves identical --
        # the false equivalence section 8.1 says `exact` must avoid.
        Index(
            "uq_concept_external_references_verified_exact",
            "semantic_concept_id",
            "scheme_version_id",
            unique=True,
            postgresql_where=text("mapping_type = 'exact' and mapping_status = 'verified'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Both foreign keys are named explicitly: the convention would generate 68-
    # and 82-character names, past PostgreSQL's 63-character identifier limit.
    semantic_concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_concept_external_references_semantic_concept_id"),
        nullable=False,
    )
    # NOT NULL is the acceptance criterion in specification section 16.2: no
    # external mapping may be stored without the release it was observed in.
    scheme_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_semantic_scheme_versions.id", ondelete="RESTRICT", name="fk_concept_external_references_scheme_version_id"),
        nullable=False,
    )
    external_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    external_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_type: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    mapping_method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClassificationScheme(Base):
    """A governed browse/reporting facet: discipline, category, use case.

    Specification section 7.6 is explicit that a classification scheme is a
    browse/reporting system and *not necessarily an ontology*. Concept
    semantics live in `semantic_concepts`; nodes here are a display hierarchy.

    `scope` carries a single value in P0. Section 14.1 places scheme
    management with a platform admin and names organisation-specific schemes
    as a future extension, so the deferral is structural rather than implied.
    """

    __tablename__ = "classification_schemes"
    __table_args__ = (
        CheckConstraint(
            "scheme_code ~ '^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$'",
            name="scheme_code",
        ),
        CheckConstraint(
            "btrim(name) <> '' and char_length(name) <= 256",
            name="name",
        ),
        CheckConstraint(
            "scope in ('platform')",
            name="scope",
        ),
        CheckConstraint(
            "btrim(version_label) <> '' and char_length(version_label) <= 64",
            name="version_label",
        ),
        CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        CheckConstraint(
            "description is null or (btrim(description) <> '' and char_length(description) <= 4000)",
            name="description",
        ),
        Index("uq_classification_schemes_scheme_code", "scheme_code", unique=True),
        Index("ix_classification_schemes_status_scheme_code", "status", "scheme_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheme_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'platform'"))
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationNode(Base):
    """One node of a classification scheme's display hierarchy.

    The composite unique key on (id, scheme_id) is not decorative: it is the
    target of the self-referencing parent foreign key and of both assignment
    tables' node foreign keys, which is what stops a node from being parented
    into -- or assigned through -- a *different* scheme.
    """

    __tablename__ = "classification_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_code ~ '^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$'",
            name="node_code",
        ),
        CheckConstraint(
            "btrim(preferred_label) <> '' and char_length(preferred_label) <= 256",
            name="preferred_label",
        ),
        CheckConstraint(
            "description is null or (btrim(description) <> '' and char_length(description) <= 4000)",
            name="description",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="sort_order",
        ),
        CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="status",
        ),
        # `parent_node_id is null` first keeps every branch true/false: a bare
        # `parent_node_id <> id` would evaluate to NULL for a root node, and
        # PostgreSQL accepts a check constraint that evaluates to NULL.
        CheckConstraint(
            "parent_node_id is null or parent_node_id <> id",
            name="parent_not_self",
        ),
        # The composite target both the parent link and the assignment tables
        # point at. Convention-generated: uq_classification_nodes_id_scheme_id.
        UniqueConstraint("id", "scheme_id"),
        UniqueConstraint("scheme_id", "node_code"),
        # A parent must live in the same scheme. Named explicitly: the
        # convention would generate a 69-character name.
        ForeignKeyConstraint(
            ["parent_node_id", "scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_classification_nodes_parent_node_id_scheme_id",
        ),
        Index("ix_classification_nodes_scheme_id_sort_order", "scheme_id", "sort_order"),
        Index("ix_classification_nodes_parent_node_id", "parent_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classification_schemes.id", ondelete="RESTRICT"), nullable=False)
    node_code: Mapped[str] = mapped_column(Text, nullable=False)
    parent_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preferred_label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ConceptClassificationAssignment(Base):
    """A governed, meaning-oriented classification of a semantic concept.

    Specification section 7.7. `classification_scheme_id` is denormalized from
    the node so "at most one verified primary per scheme" can be a partial
    unique index: a primary discipline and a primary category are both
    legitimate, a second primary category is not.
    """

    __tablename__ = "concept_classification_assignments"
    __table_args__ = (
        # Section 7.7 lists `primary | secondary | inherited | proposed`.
        # `proposed` is a governance *status* everywhere else in this model
        # (section 8.4, and every delivered semantic package), so it is carried
        # by `status` here and the role vocabulary stops at `inherited`.
        CheckConstraint(
            "assignment_role in ('primary', 'secondary', 'inherited')",
            name="assignment_role",
        ),
        CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        # Section 12.1 phase M2 adds `legacy_backfill` to the section 7.9
        # vocabulary. Deliberately a third vocabulary: `concept_external_references`
        # names `imported` where this names `source_mapping`, and unifying any
        # of the three would be a specification change.
        CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')",
            name="method",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        # A verification decision must record when it happened. reviewed_by
        # stays nullable so a deterministic assignment auto-verified under
        # explicit policy (section 8.4) needs no invented user.
        CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        # Section 12.3: backfilled classifications must not be labelled
        # verified. Both columns are NOT NULL, so neither branch can be NULL.
        CheckConstraint(
            "method <> 'legacy_backfill' or status <> 'verified'",
            name="backfill_not_verified",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # The node must belong to the scheme this row claims. Named explicitly:
        # the convention would generate an 81-character name.
        ForeignKeyConstraint(
            ["classification_node_id", "classification_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_concept_classification_assignments_classification_node_id",
        ),
        # Section 14.3: index by target and by classification node.
        Index("ix_concept_classification_assignments_concept_status", "semantic_concept_id", "status"),
        Index("ix_concept_classification_assignments_node_status", "classification_node_id", "status"),
        # At most one verified primary per (concept, scheme). Proposals are
        # left unconstrained so competing candidates can sit side by side.
        Index(
            "uq_concept_classification_assignments_verified_primary",
            "semantic_concept_id",
            "classification_scheme_id",
            unique=True,
            postgresql_where=text("assignment_role = 'primary' and status = 'verified'"),
        ),
        # One live assertion per (concept, node). Rejected and retired rows
        # stay out so an assignment's governance history survives its successor.
        Index(
            "uq_concept_classification_assignments_active_node",
            "semantic_concept_id",
            "classification_node_id",
            unique=True,
            postgresql_where=text("status in ('proposed', 'verified')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Every foreign key here is named explicitly: the convention would generate
    # 75, 81 and two 63-character names against PostgreSQL's 63-character limit.
    semantic_concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_concept_classification_assignments_semantic_concept_id"),
        nullable=False,
    )
    classification_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    classification_scheme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assignment_role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_concept_classification_assignments_proposed_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_concept_classification_assignments_reviewed_by_user_id"),
        nullable=True,
    )
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SymbolRevisionClassificationAssignment(Base):
    """A governed, representation-oriented classification of a symbol revision.

    Specification section 7.8: discipline, drawing application, symbol family
    or graphical context, attached to the exact graphic rather than to the
    meaning. Section 7.7 is the meaning-oriented counterpart, and the two are
    deliberately not collapsed.

    The table is named `symbol_revision_classifications` rather than the
    specification's logical `symbol_revision_classification_assignments` (42
    characters): every foreign key on the longer name breaks PostgreSQL's
    63-character identifier limit even when named explicitly. Section 7 asks
    that final migration naming follow repository conventions.
    """

    __tablename__ = "symbol_revision_classifications"
    __table_args__ = (
        # Section 7.8 lists `primary | secondary | proposed`; as in section
        # 7.7, `proposed` is carried by `status`. There is no `inherited` here
        # -- a revision inherits nothing, its concept does.
        CheckConstraint(
            "assignment_role in ('primary', 'secondary')",
            name="assignment_role",
        ),
        CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')",
            name="method",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        # Section 12.3: the phase M2 backfill must not label its own output
        # verified. This is the constraint that makes that structural.
        CheckConstraint(
            "method <> 'legacy_backfill' or status <> 'verified'",
            name="backfill_not_verified",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        ForeignKeyConstraint(
            ["classification_node_id", "classification_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT",
            name="fk_symbol_revision_classifications_classification_node_id",
        ),
        Index("ix_symbol_revision_classifications_revision_status", "symbol_revision_id", "status"),
        Index("ix_symbol_revision_classifications_node_status", "classification_node_id", "status"),
        Index(
            "uq_symbol_revision_classifications_verified_primary",
            "symbol_revision_id",
            "classification_scheme_id",
            unique=True,
            postgresql_where=text("assignment_role = 'primary' and status = 'verified'"),
        ),
        Index(
            "uq_symbol_revision_classifications_active_node",
            "symbol_revision_id",
            "classification_node_id",
            unique=True,
            postgresql_where=text("status in ('proposed', 'verified')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Every foreign key here is named explicitly: the convention would generate
    # names of 71 characters and more against the 63-character limit.
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_symbol_revision_classifications_symbol_revision_id"),
        nullable=False,
    )
    classification_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    classification_scheme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assignment_role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_revision_classifications_proposed_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_symbol_revision_classifications_reviewed_by_user_id"),
        nullable=True,
    )
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RightsRecord(Base):
    """A durable, governed rights disposition for one provenance subject.

    Specification section 7.12. Section 7.12 asks to reuse an equivalent
    durable rights entity if one exists; none does. `provenance_assessments`
    is the only rights-bearing table with a governed vocabulary, and it is
    intake-scoped on both of its NOT NULL foreign keys, shares not one value
    with section 7.12's disposition vocabulary, and is written by the live
    intake pipeline. `hannah_photo_candidates.rights_status` is
    candidate-scoped. Neither can carry a rights decision about a source
    package, a standard edition or a governed symbol revision, which is what
    authoritative ingestion needs; see migration 20260910_0056.

    Exactly one of the three subject columns is set. A source package is the
    primary anchor -- it is section 7.11's acquisition envelope, and
    `source_packages.licence_reference` is the forward reference into this
    table -- but a standard edition needs its own, because whether SymGov may
    derive a symbol from one edition is a question about the standard and not
    about any package, and a symbol revision needs its own, because section
    9.2's publication gate is per-symbol and a redrawn asset's disposition
    can differ from its source's.

    One table with three nullable subjects rather than SM-P0-04's
    three-tables-per-target shape: those two tables carry different role
    vocabularies, whereas the record here is identical for all three
    subjects, and three copies of one governed decision is three places for
    section 7.12's vocabularies to drift apart.
    """

    __tablename__ = "rights_records"
    __table_args__ = (
        # A sum of `case` expressions, so the comparison is between two
        # integers and can never evaluate to NULL. PostgreSQL accepts a check
        # constraint that evaluates to NULL -- the trap that bit SM-P0-03's
        # checksum pairing and SM-P0-04's parent check.
        CheckConstraint(
            "(case when source_package_id is null then 0 else 1 end "
            "+ case when standard_version_id is null then 0 else 1 end "
            "+ case when symbol_revision_id is null then 0 else 1 end) = 1",
            name="subject_exactly_one",
        ),
        # Section 7.12, verbatim. Also section 13.1's "Rights" dimension.
        CheckConstraint(
            "rights_status in ('unknown', 'open', 'licensed', 'restricted', 'prohibited', 'expired')",
            name="rights_status",
        ),
        # Section 7.12, verbatim. Shares no value with the deployed
        # `provenance_assessments.rights_disposition` enumeration.
        CheckConstraint(
            "disposition in ('display', 'distribute', 'transform', 'compare_only', "
            "'metadata_only', 'reject')",
            name="disposition",
        ),
        # The sixth `method` vocabulary in this model, kept distinct from the
        # other five on purpose; see `rights_provenance.RIGHTS_DETERMINATION_METHODS`.
        CheckConstraint(
            "determination_method in ('manual', 'licence_document', 'ai_assisted')",
            name="determination_method",
        ),
        # `approved` rather than `verified`: section 7.12's own word is
        # "approved", and a rights disposition is a legal approval rather
        # than a verification of fact.
        CheckConstraint(
            "decision_status in ('proposed', 'approved', 'rejected', 'retired')",
            name="decision_status",
        ),
        # Section 7.12: "a reference to terms/contract/rights record; not the
        # licence text itself". The bound is SM-P0-05's, and is what keeps it
        # a reference.
        CheckConstraint(
            "licence_reference is null or (btrim(licence_reference) <> '' "
            "and char_length(licence_reference) <= 512)",
            name="licence_reference",
        ),
        CheckConstraint(
            "decision_reason is null or (btrim(decision_reason) <> '' "
            "and char_length(decision_reason) <= 2000)",
            name="decision_reason",
        ),
        # Section 7.12's who and when. `retired` is excluded because
        # supersession retires a predecessor when its successor is approved;
        # the actor of record is the successor's approver.
        CheckConstraint(
            "decision_status in ('proposed', 'retired') "
            "or (decided_at is not null and decided_by_user_id is not null)",
            name="decision_actor",
        ),
        # Section 7.12's why. No other table in this model requires a reason.
        CheckConstraint(
            "decision_status <> 'approved' or decision_reason is not null",
            name="approved_reason",
        ),
        # Section 8.4, with no controlled-system exception: no deterministic
        # reading of a licence is itself a rights decision.
        CheckConstraint(
            "decision_status <> 'approved' or determination_method <> 'ai_assisted'",
            name="approved_not_ai_determined",
        ),
        CheckConstraint(
            "decision_status <> 'approved' "
            "or rights_status not in ('licensed', 'restricted') "
            "or licence_reference is not null",
            name="approved_licence_reference",
        ),
        # No constraint pairs `rights_status` against `disposition`. That a
        # permissive disposition may only be *approved* on a status that
        # supports it is real, and it is service policy in
        # `rights_provenance.disposition_is_permitted`: rights gating is
        # SM-P0-08's, and a storage-level rule would put a second gate in a
        # second place. The same reasoning keeps `standard_sources`'
        # status vocabulary out of the database.
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        # At most one *approved* record per subject. Proposals stay
        # unconstrained so competing candidates can sit side by side;
        # approving a successor retires its predecessor rather than being
        # refused -- the supersession shape SM-P0-01 through -05 all use.
        Index(
            "uq_rights_records_approved_source_package",
            "source_package_id",
            unique=True,
            postgresql_where=text("decision_status = 'approved' and source_package_id is not null"),
        ),
        Index(
            "uq_rights_records_approved_standard_version",
            "standard_version_id",
            unique=True,
            postgresql_where=text("decision_status = 'approved' and standard_version_id is not null"),
        ),
        Index(
            "uq_rights_records_approved_symbol_revision",
            "symbol_revision_id",
            unique=True,
            postgresql_where=text("decision_status = 'approved' and symbol_revision_id is not null"),
        ),
        # Section 14.3's shape for assignment tables -- index by target. Also
        # the indexes the RESTRICT foreign keys need.
        Index(
            "ix_rights_records_source_package_id",
            "source_package_id",
            "decision_status",
            postgresql_where=text("source_package_id is not null"),
        ),
        Index(
            "ix_rights_records_standard_version_id",
            "standard_version_id",
            "decision_status",
            postgresql_where=text("standard_version_id is not null"),
        ),
        Index(
            "ix_rights_records_symbol_revision_id",
            "symbol_revision_id",
            "decision_status",
            postgresql_where=text("symbol_revision_id is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Every foreign key is named explicitly and short: the convention's own
    # name for the entry-level key on the sibling table would be 72
    # characters against PostgreSQL's 63-character limit.
    source_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_packages.id", ondelete="RESTRICT", name="fk_rights_records_source_package_id"),
        nullable=True,
    )
    standard_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_versions.id", ondelete="RESTRICT", name="fk_rights_records_standard_version_id"),
        nullable=True,
    )
    symbol_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_rights_records_symbol_revision_id"),
        nullable=True,
    )
    rights_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    disposition: Mapped[str] = mapped_column(Text, nullable=False)
    licence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    determination_method: Mapped[str] = mapped_column(Text, nullable=False)
    decision_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'"))
    # RESTRICT rather than SET NULL, and the only such key in the semantic
    # model. Section 14.4 retains the governance decision history with the
    # governed data, and an approved rights record that has lost its approver
    # is precisely the record that must not exist -- `decision_actor` would
    # refuse the NULL anyway, so RESTRICT reports the real reason.
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_rights_records_decided_by_user_id"),
        nullable=True,
    )
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_rights_records_proposed_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetTransformation(Base):
    """One step of section 7.12's source asset -> tool/version -> derived asset.

    Specification section 7.12's transformation lineage, and Appendix B.2's
    "source file SHA-256 -> approved transformation -> SVG SHA-256". One row
    per step, ordered by `step_index` within a symbol revision, so the chain
    the specification asks for is representable and not just its endpoints.

    One table rather than a lineage header plus steps: a header would carry
    no field of its own -- the chain is fully described by
    `(symbol_revision_id, step_index)` -- and would exist only to hold an id.

    Chain *continuity*, that each step starts from the digest the previous
    step produced, is a cross-row property and cannot be a check constraint.
    It is enforced in `rights_provenance.record_asset_transformation`. The
    database enforces every per-row property, including that a step which
    changed nothing cannot be recorded.

    Existing hash columns are joined to rather than duplicated:
    `source_package_entries.original_asset_sha256`,
    `source_packages.package_sha256`,
    `symbol_standard_links.source_asset_sha256` and `attachments.sha256`.

    No foreign key points at `rights_records`. Whether a transformation was
    permitted depends on the source subject's approved disposition including
    `transform`, and that judgement is section 9.2's publication gate --
    SM-P0-08, not this package.
    """

    __tablename__ = "asset_transformations"
    __table_args__ = (
        CheckConstraint("step_index >= 1", name="step_index"),
        CheckConstraint(
            "btrim(tool_name) <> '' and char_length(tool_name) <= 128",
            name="tool_name",
        ),
        CheckConstraint(
            "btrim(tool_version) <> '' and char_length(tool_version) <= 64",
            name="tool_version",
        ),
        # Sections 7.10, 7.11 and 7.12 all name SHA-256, so a fixed
        # 64-character grammar and no algorithm column. Deliberately not
        # SM-P0-03's algorithm-paired `^[0-9a-f]{32,128}$`, which would admit
        # a 32-character MD5 digest.
        CheckConstraint(
            "source_asset_sha256 is null or source_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="source_asset_sha256",
        ),
        CheckConstraint(
            "derived_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="derived_asset_sha256",
        ),
        # A derived asset with no identified source is not lineage. Both
        # branches test a column against NULL explicitly.
        CheckConstraint(
            "source_asset_sha256 is not null or source_package_entry_id is not null",
            name="source_asset_identified",
        ),
        CheckConstraint(
            "source_asset_sha256 is null or derived_asset_sha256 <> source_asset_sha256",
            name="transformation_changed_asset",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
        Index(
            "uq_asset_transformations_revision_step",
            "symbol_revision_id",
            "step_index",
            unique=True,
        ),
        # Which symbol a stored asset digest belongs to: section 13.3's
        # lineage reporting, and a signal section 10.2's duplicate detection
        # can use without a name comparison.
        Index("ix_asset_transformations_derived_asset_sha256", "derived_asset_sha256"),
        Index(
            "ix_asset_transformations_source_asset_sha256",
            "source_asset_sha256",
            postgresql_where=text("source_asset_sha256 is not null"),
        ),
        Index(
            "ix_asset_transformations_source_package_entry_id",
            "source_package_entry_id",
            postgresql_where=text("source_package_entry_id is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_asset_transformations_symbol_revision_id"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_package_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "source_package_entries.id",
            ondelete="RESTRICT",
            name="fk_asset_transformations_source_package_entry_id",
        ),
        nullable=True,
    )
    source_asset_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_version: Mapped[str] = mapped_column(Text, nullable=False)
    derived_asset_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    performed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_asset_transformations_recorded_by_user_id"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
