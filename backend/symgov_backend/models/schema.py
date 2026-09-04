from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, PrimaryKeyConstraint, Text, UniqueConstraint, text
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
        CheckConstraint("octet_length(convert_to(metadata_json::text, 'UTF8')) <= 16384", name="ck_projects_metadata_size"),
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
        CheckConstraint("jsonb_typeof(disciplines_json) = 'array' AND jsonb_array_length(disciplines_json) <= 32", name="ck_symbol_sets_disciplines_array_bounds"),
        CheckConstraint("jsonb_typeof(use_cases_json) = 'array' AND jsonb_array_length(use_cases_json) <= 32", name="ck_symbol_sets_use_cases_array_bounds"),
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
        CheckConstraint("octet_length(convert_to(provenance_json::text, 'UTF8')) <= 16384", name="ck_symbol_set_items_provenance_size"),
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


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        CheckConstraint(
            "identity_type in ('engineer', 'contractor', 'submitter', 'external_reviewer', 'other')",
            name="external_identities_identity_type",
        ),
        CheckConstraint("status in ('active', 'inactive')", name="external_identities_status"),
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
    __tablename__ = "source_packages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


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
    __tablename__ = "standard_versions"
    __table_args__ = (
        Index("uq_standard_versions_standard_version_label", "standard_id", "version_label", unique=True),
        Index("ix_standard_versions_standard_effective_date", "standard_id", "effective_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    standard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False)
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePackageEntry(Base):
    __tablename__ = "source_package_entries"
    __table_args__ = (
        Index("uq_source_package_entries_package_revision", "source_package_id", "symbol_revision_id", unique=True),
        Index("ix_source_package_entries_package_sort_order", "source_package_id", "sort_order"),
        Index("ix_source_package_entries_revision_package", "symbol_revision_id", "source_package_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_packages.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolStandardLink(Base):
    __tablename__ = "symbol_standard_links"
    __table_args__ = (
        Index(
            "uq_symbol_standard_links_revision_standard_relationship_clause",
            "symbol_revision_id",
            "standard_version_id",
            "relationship_type",
            "clause_reference",
            unique=True,
        ),
        Index("ix_symbol_standard_links_revision_standard", "symbol_revision_id", "standard_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    standard_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standard_versions.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    clause_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


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
    __table_args__ = (Index("ix_provenance_assessments_intake_assessed_at", "intake_record_id", "assessed_at"),)

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
