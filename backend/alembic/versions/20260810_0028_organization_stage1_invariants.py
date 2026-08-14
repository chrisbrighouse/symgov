"""organization Stage 1 additive schema and invariants

Revision ID: 20260810_0028
Revises: 20260808_0027
Create Date: 2026-08-10 21:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Migration ownership: Stage 1 was allocated only after the isolated source
# graph confirmed 20260808_0027 as the sole live head. 20260810_0028 is unique
# across the repository and deliberately supersedes stale pre-0027 proposals.

# revision identifiers, used by Alembic.
revision = "20260810_0028"
down_revision = "20260808_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. organizations
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("normalized_code", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("legal_name_key", sa.Text(), nullable=True),
        sa.Column("locale", sa.Text(), server_default=sa.text("'en-US'"), nullable=False),
        sa.Column("entitlement_status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_protected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("icon_seed_version", sa.Text(), server_default=sa.text("'v1'"), nullable=False),
        sa.Column("fallback_icon_svg", sa.Text(), nullable=False),
        sa.Column("uploaded_icon_storage_key", sa.Text(), nullable=True),
        sa.Column("uploaded_icon_content_type", sa.Text(), nullable=True),
        sa.Column("uploaded_icon_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("normalized_code ~ '^[a-z][a-z0-9-]{1,31}$'", name=op.f("ck_organizations_normalized_code_format")),
        sa.CheckConstraint(
            "(code = 'symgov' and normalized_code = 'symgov') or "
            "(code ~ '^[A-Z][A-Z0-9-]{1,31}$' and normalized_code = lower(code))",
            name=op.f("ck_organizations_code_format"),
        ),
        sa.CheckConstraint(
            "(normalized_code = 'symgov' and code = 'symgov' and is_protected = true) or "
            "(normalized_code <> 'symgov' and is_protected = false)",
            name=op.f("ck_organizations_reserved_identity"),
        ),
        sa.CheckConstraint("entitlement_status in ('active', 'suspended')", name=op.f("ck_organizations_status")),
        sa.UniqueConstraint("normalized_code", name="uq_organizations_normalized_code"),
    )
    op.create_index("ix_organizations_active_status", "organizations", ["is_active", "entitlement_status", "normalized_code"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_organization_identity()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.is_protected THEN
                    RAISE EXCEPTION 'protected symgov organization cannot be deleted';
                END IF;
                RAISE EXCEPTION 'organization history cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id THEN
                RAISE EXCEPTION 'organization UUID is immutable';
            END IF;
            IF OLD.code IS DISTINCT FROM NEW.code
               OR OLD.normalized_code IS DISTINCT FROM NEW.normalized_code THEN
                RAISE EXCEPTION 'organization code is immutable';
            END IF;
            IF OLD.is_protected IS DISTINCT FROM NEW.is_protected THEN
                RAISE EXCEPTION 'organization protected identity is immutable';
            END IF;
            IF OLD.is_protected
               AND (NEW.is_active IS DISTINCT FROM true
                    OR NEW.entitlement_status IS DISTINCT FROM 'active') THEN
                RAISE EXCEPTION 'protected symgov organization cannot be deactivated';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organizations_protected_identity
        BEFORE UPDATE OR DELETE ON organizations
        FOR EACH ROW EXECUTE FUNCTION protect_organization_identity();
        """
    )

    # 2. organization_memberships
    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('active', 'invited', 'inactive', 'suspended')", name=op.f("ck_organization_memberships_status")),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),
    )
    op.create_index("ix_org_memberships_user_status", "organization_memberships", ["user_id", "status", "organization_id"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_organization_membership_history()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'membership history cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
               OR OLD.user_id IS DISTINCT FROM NEW.user_id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'membership identity is immutable';
            END IF;
            IF (OLD.invited_at IS NOT NULL AND OLD.invited_at IS DISTINCT FROM NEW.invited_at)
               OR (OLD.activated_at IS NOT NULL AND OLD.activated_at IS DISTINCT FROM NEW.activated_at)
               OR (OLD.deactivated_at IS NOT NULL AND OLD.deactivated_at IS DISTINCT FROM NEW.deactivated_at) THEN
                RAISE EXCEPTION 'membership lifecycle timestamps are append-only';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_membership_history
        BEFORE UPDATE OR DELETE ON organization_memberships
        FOR EACH ROW EXECUTE FUNCTION protect_organization_membership_history();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_membership_history_truncate
        BEFORE TRUNCATE ON organization_memberships
        FOR EACH STATEMENT EXECUTE FUNCTION protect_organization_membership_history();
        """
    )

    # 3. organization_role_assignments
    op.create_table(
        "organization_role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("base_role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("base_role in ('admin', 'user')", name=op.f("ck_organization_role_assignments_base_role")),
        sa.CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name=op.f("ck_organization_role_assignments_active_revoked"),
        ),
    )
    op.create_index("uq_org_role_active_membership", "organization_role_assignments", ["membership_id"], unique=True, postgresql_where=sa.text("is_active = true"))
    op.create_index("ix_org_role_membership_active", "organization_role_assignments", ["membership_id", "is_active"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_organization_role_history()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'assignment history cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.membership_id IS DISTINCT FROM NEW.membership_id
               OR OLD.base_role IS DISTINCT FROM NEW.base_role
               OR OLD.assigned_at IS DISTINCT FROM NEW.assigned_at
               OR OLD.assigned_by_user_id IS DISTINCT FROM NEW.assigned_by_user_id THEN
                RAISE EXCEPTION 'assignment identity is immutable';
            END IF;
            IF OLD.is_active = false THEN
                RAISE EXCEPTION 'revoked assignment history is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_org_role_assignment_history
        BEFORE UPDATE OR DELETE ON organization_role_assignments
        FOR EACH ROW EXECUTE FUNCTION protect_organization_role_history();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_org_role_assignment_history_truncate
        BEFORE TRUNCATE ON organization_role_assignments
        FOR EACH STATEMENT EXECUTE FUNCTION protect_organization_role_history();
        """
    )

    # 4. organization_member_capabilities
    op.create_table(
        "organization_member_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("capability in ('contributor', 'symbol_reviewer')", name=op.f("ck_organization_member_capabilities_capability")),
        sa.CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name=op.f("ck_organization_member_capabilities_active_revoked"),
        ),
    )
    op.create_index("uq_org_capability_active_membership", "organization_member_capabilities", ["membership_id", "capability"], unique=True, postgresql_where=sa.text("is_active = true"))
    op.create_index("ix_org_capability_membership_active", "organization_member_capabilities", ["membership_id", "is_active"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_organization_capability_history()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'capability history cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.membership_id IS DISTINCT FROM NEW.membership_id
               OR OLD.capability IS DISTINCT FROM NEW.capability
               OR OLD.granted_at IS DISTINCT FROM NEW.granted_at
               OR OLD.granted_by_user_id IS DISTINCT FROM NEW.granted_by_user_id THEN
                RAISE EXCEPTION 'capability identity is immutable';
            END IF;
            IF OLD.is_active = false THEN
                RAISE EXCEPTION 'revoked capability history is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_org_capability_history
        BEFORE UPDATE OR DELETE ON organization_member_capabilities
        FOR EACH ROW EXECUTE FUNCTION protect_organization_capability_history();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_org_capability_history_truncate
        BEFORE TRUNCATE ON organization_member_capabilities
        FOR EACH STATEMENT EXECUTE FUNCTION protect_organization_capability_history();
        """
    )

    # 5. platform_role_assignments
    op.create_table(
        "platform_role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("role in ('platform_admin')", name=op.f("ck_platform_role_assignments_role")),
        sa.CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name=op.f("ck_platform_role_assignments_active_revoked"),
        ),
    )
    op.create_index("uq_platform_role_active_user_role", "platform_role_assignments", ["user_id", "role"], unique=True, postgresql_where=sa.text("is_active = true"))
    op.create_index("ix_platform_role_user_active", "platform_role_assignments", ["user_id", "is_active"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_platform_role_history()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'platform-role history cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.user_id IS DISTINCT FROM NEW.user_id
               OR OLD.role IS DISTINCT FROM NEW.role
               OR OLD.assigned_at IS DISTINCT FROM NEW.assigned_at
               OR OLD.assigned_by_user_id IS DISTINCT FROM NEW.assigned_by_user_id THEN
                RAISE EXCEPTION 'platform-role identity is immutable';
            END IF;
            IF OLD.is_active = false THEN
                RAISE EXCEPTION 'revoked platform-role history is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_platform_role_history
        BEFORE UPDATE OR DELETE ON platform_role_assignments
        FOR EACH ROW EXECUTE FUNCTION protect_platform_role_history();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_platform_role_history_truncate
        BEFORE TRUNCATE ON platform_role_assignments
        FOR EACH STATEMENT EXECUTE FUNCTION protect_platform_role_history();
        """
    )
    op.execute(
        """
        GRANT SELECT, UPDATE ON organizations, users TO symgov_app;
        GRANT SELECT, INSERT ON
            organization_memberships,
            organization_role_assignments,
            organization_member_capabilities,
            platform_role_assignments
        TO symgov_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON
            organization_memberships,
            organization_role_assignments,
            organization_member_capabilities,
            platform_role_assignments
        FROM symgov_app;
        """
    )

    # 6. user_sessions updates
    op.add_column("user_sessions", sa.Column("session_mode", sa.Text(), server_default=sa.text("'personal'"), nullable=False))
    op.add_column("user_sessions", sa.Column("active_organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True))
    op.add_column("user_sessions", sa.Column("recent_step_up_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f("ck_user_sessions_mode"), "user_sessions", "session_mode in ('personal', 'organization')")
    op.create_check_constraint(
        op.f("ck_user_sessions_mode_active_org"),
        "user_sessions",
        "(session_mode = 'personal' and active_organization_id is null) or (session_mode = 'organization' and active_organization_id is not null)",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_user_session_org_context_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF (OLD.session_mode IS DISTINCT FROM NEW.session_mode OR OLD.active_organization_id IS DISTINCT FROM NEW.active_organization_id) THEN
                RAISE EXCEPTION 'user session organization context is immutable after creation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_user_sessions_immutable_org_context
        BEFORE UPDATE ON user_sessions
        FOR EACH ROW EXECUTE FUNCTION prevent_user_session_org_context_mutation();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_user_session_organization_membership()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.session_mode = 'organization' AND NOT EXISTS (
                SELECT 1
                FROM organization_memberships membership
                JOIN organizations organization ON organization.id = membership.organization_id
                JOIN users account ON account.id = membership.user_id
                WHERE membership.organization_id = NEW.active_organization_id
                  AND membership.user_id = NEW.auth_user_id
                  AND membership.status = 'active'
                  AND organization.is_active = true
                  AND organization.entitlement_status = 'active'
                  AND account.is_active = true
                  AND account.deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'organization session requires an active organization membership';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_user_sessions_active_org_membership
        BEFORE INSERT ON user_sessions
        FOR EACH ROW EXECUTE FUNCTION validate_user_session_organization_membership();
        """
    )

    # 7. auth_organization_selection_challenges
    op.create_table(
        "auth_organization_selection_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("eligible_organizations_hash", sa.Text(), nullable=False),
        sa.Column("eligible_organizations_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_attempts = 5", name=op.f("ck_auth_organization_selection_challenges_max_attempts")),
        sa.CheckConstraint("attempt_count >= 0 and attempt_count <= max_attempts", name=op.f("ck_auth_organization_selection_challenges_attempt_bounds")),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_auth_organization_selection_challenges_token_hash")),
        sa.CheckConstraint(
            "eligible_organizations_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_auth_organization_selection_challenges_eligible_hash"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '10 minutes'",
            name=op.f("ck_auth_organization_selection_challenges_expiry"),
        ),
    )
    op.create_index("uq_org_selection_token_hash", "auth_organization_selection_challenges", ["token_hash"], unique=True)
    op.create_index("ix_org_selection_user_expires", "auth_organization_selection_challenges", ["user_id", "expires_at"])

    # 8. Serialize administration before PostgreSQL acquires any affected row.
    #
    # Canonical order for service and direct-SQL paths is: this transaction-level
    # advisory lock, User rows by UUID, Organization rows by UUID, membership rows
    # by UUID, organization-role rows by UUID, then platform-role rows by UUID.
    # The BEFORE STATEMENT triggers are essential for users UPDATE: waiting until a
    # deferred constraint trigger would leave its User row locked before the shared
    # administration gate and recreate a User <-> Symgov deadlock cycle.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION acquire_organization_administration_lock()
        RETURNS TRIGGER AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(1398361415, 1330792241);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for trigger_name, table_name, events in (
        ("trg_users_organization_administration_lock", "users", "UPDATE OF is_active, deleted_at"),
        ("trg_organizations_administration_lock", "organizations", "INSERT OR UPDATE OR DELETE"),
        ("trg_memberships_administration_lock", "organization_memberships", "INSERT OR UPDATE OR DELETE"),
        ("trg_organization_roles_administration_lock", "organization_role_assignments", "INSERT OR UPDATE OR DELETE"),
        ("trg_platform_roles_administration_lock", "platform_role_assignments", "INSERT OR UPDATE OR DELETE"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {events} ON {table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION acquire_organization_administration_lock();
            """
        )

    # 9. Every active Platform Admin remains an active Symgov Organization Admin.
    # The service separately protects the last eligible assignment after bootstrap;
    # this deferred database rule prevents direct writes from creating ineligible rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_platform_admin_eligibility()
        RETURNS TRIGGER AS $$
        DECLARE
            v_protected_symgov_exists boolean;
        BEGIN
            PERFORM 1
            FROM organizations
            WHERE code = 'symgov'
              AND normalized_code = 'symgov'
              AND is_protected = true
              AND is_active = true
              AND entitlement_status = 'active'
            FOR UPDATE;
            v_protected_symgov_exists := FOUND;

            IF EXISTS (
                SELECT 1
                FROM platform_role_assignments platform_role
                JOIN users account ON account.id = platform_role.user_id
                WHERE platform_role.role = 'platform_admin'
                  AND platform_role.is_active = true
                  AND (
                      account.is_active = false
                      OR account.deleted_at IS NOT NULL
                      OR NOT EXISTS (
                          SELECT 1
                          FROM organization_memberships membership
                          JOIN organizations organization ON organization.id = membership.organization_id
                          JOIN organization_role_assignments organization_role
                            ON organization_role.membership_id = membership.id
                          WHERE membership.user_id = platform_role.user_id
                            AND membership.status = 'active'
                            AND organization.code = 'symgov'
                            AND organization.normalized_code = 'symgov'
                            AND organization.is_protected = true
                            AND organization.is_active = true
                            AND organization.entitlement_status = 'active'
                            AND organization_role.base_role = 'admin'
                            AND organization_role.is_active = true
                      )
                  )
            ) THEN
                RAISE EXCEPTION 'active Platform Administrator must be an active Symgov organization admin';
            END IF;
            IF v_protected_symgov_exists AND NOT EXISTS (
                SELECT 1
                FROM platform_role_assignments platform_role
                JOIN users account ON account.id = platform_role.user_id
                JOIN organization_memberships membership ON membership.user_id = platform_role.user_id
                JOIN organizations organization ON organization.id = membership.organization_id
                JOIN organization_role_assignments organization_role
                  ON organization_role.membership_id = membership.id
                WHERE platform_role.role = 'platform_admin'
                  AND platform_role.is_active = true
                  AND account.is_active = true
                  AND account.deleted_at IS NULL
                  AND membership.status = 'active'
                  AND organization.code = 'symgov'
                  AND organization.normalized_code = 'symgov'
                  AND organization.is_protected = true
                  AND organization.is_active = true
                  AND organization.entitlement_status = 'active'
                  AND organization_role.base_role = 'admin'
                  AND organization_role.is_active = true
            ) THEN
                RAISE EXCEPTION 'active Symgov organization must retain at least one active Platform Administrator';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for trigger_name, table_name, events in (
        ("trg_platform_role_eligibility", "platform_role_assignments", "INSERT OR UPDATE"),
        ("trg_org_membership_platform_eligibility", "organization_memberships", "INSERT OR UPDATE OR DELETE"),
        ("trg_org_role_platform_eligibility", "organization_role_assignments", "INSERT OR UPDATE OR DELETE"),
        ("trg_user_platform_eligibility", "users", "UPDATE"),
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER {trigger_name}
            AFTER {events} ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_platform_admin_eligibility();
            """
        )

    # 10. Every active organization retains at least one active Organization Admin.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION assert_active_organization_has_admin(v_organization_id uuid)
        RETURNS void AS $$
        DECLARE
            v_requires_admin boolean;
        BEGIN
            SELECT is_active AND entitlement_status = 'active'
            INTO v_requires_admin
            FROM organizations
            WHERE id = v_organization_id
            FOR UPDATE;

            IF v_requires_admin AND NOT EXISTS (
                SELECT 1
                FROM organization_memberships membership
                JOIN users account ON account.id = membership.user_id
                JOIN organization_role_assignments organization_role
                  ON organization_role.membership_id = membership.id
                WHERE membership.organization_id = v_organization_id
                  AND membership.status = 'active'
                  AND account.is_active = true
                  AND account.deleted_at IS NULL
                  AND organization_role.base_role = 'admin'
                  AND organization_role.is_active = true
            ) THEN
                RAISE EXCEPTION 'active organization must retain at least one active Organization Administrator';
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_active_organization_admin_minimum()
        RETURNS TRIGGER AS $$
        DECLARE
            v_old_organization_id uuid;
            v_new_organization_id uuid;
            v_organization_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'organizations' THEN
                IF TG_OP <> 'INSERT' THEN
                    v_old_organization_id := OLD.id;
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    v_new_organization_id := NEW.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'organization_memberships' THEN
                IF TG_OP <> 'INSERT' THEN
                    v_old_organization_id := OLD.organization_id;
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    v_new_organization_id := NEW.organization_id;
                END IF;
            ELSIF TG_TABLE_NAME = 'users' THEN
                FOR v_organization_id IN
                    SELECT DISTINCT membership.organization_id
                    FROM organization_memberships membership
                    JOIN organization_role_assignments organization_role
                      ON organization_role.membership_id = membership.id
                    WHERE (membership.user_id = OLD.id
                       OR membership.user_id = NEW.id)
                      AND membership.status = 'active'
                      AND organization_role.base_role = 'admin'
                      AND organization_role.is_active = true
                    ORDER BY membership.organization_id
                LOOP
                    PERFORM assert_active_organization_has_admin(v_organization_id);
                END LOOP;
                RETURN NULL;
            ELSE
                IF TG_OP <> 'INSERT' THEN
                    SELECT organization_id INTO v_old_organization_id
                    FROM organization_memberships WHERE id = OLD.membership_id;
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    SELECT organization_id INTO v_new_organization_id
                    FROM organization_memberships WHERE id = NEW.membership_id;
                END IF;
            END IF;

            IF v_old_organization_id IS NOT NULL THEN
                PERFORM assert_active_organization_has_admin(v_old_organization_id);
            END IF;
            IF v_new_organization_id IS NOT NULL
               AND v_new_organization_id IS DISTINCT FROM v_old_organization_id THEN
                PERFORM assert_active_organization_has_admin(v_new_organization_id);
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for trigger_name, table_name, events in (
        ("trg_organization_admin_minimum", "organizations", "INSERT OR UPDATE"),
        ("trg_membership_organization_admin_minimum", "organization_memberships", "INSERT OR UPDATE OR DELETE"),
        ("trg_role_organization_admin_minimum", "organization_role_assignments", "INSERT OR UPDATE OR DELETE"),
        ("trg_user_organization_admin_minimum", "users", "UPDATE"),
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER {trigger_name}
            AFTER {events} ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_active_organization_admin_minimum();
            """
        )

    # 11. Exactly one active base role invariant
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_active_membership_exactly_one_base_role()
        RETURNS TRIGGER AS $$
        DECLARE
            v_status text;
            v_count integer;
            v_membership_id uuid;
        BEGIN
            IF (TG_TABLE_NAME = 'organization_memberships') THEN
                IF (TG_OP = 'DELETE') THEN
                    v_membership_id := OLD.id;
                ELSE
                    v_membership_id := NEW.id;
                END IF;
            ELSIF (TG_OP = 'DELETE') THEN
                v_membership_id := OLD.membership_id;
            ELSE
                v_membership_id := NEW.membership_id;
            END IF;

            SELECT status INTO v_status FROM organization_memberships WHERE id = v_membership_id;
            SELECT count(*) INTO v_count FROM organization_role_assignments WHERE membership_id = v_membership_id AND is_active = true;

            IF (v_status = 'active') THEN
                IF (v_count <> 1) THEN
                    RAISE EXCEPTION 'active organization membership % must have exactly one active base role (found %)', v_membership_id, v_count;
                END IF;
            ELSIF (v_status IS NOT NULL AND v_count <> 0) THEN
                RAISE EXCEPTION 'inactive organization membership cannot retain an active base role';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_org_role_assignment_exactly_one
        AFTER INSERT OR UPDATE OR DELETE ON organization_role_assignments
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_active_membership_exactly_one_base_role();
        """
    )
    # Also trigger when membership status changes to active
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_org_membership_active_must_have_role
        AFTER INSERT OR UPDATE ON organization_memberships
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_active_membership_exactly_one_base_role();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_org_membership_active_must_have_role ON organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_org_role_assignment_exactly_one ON organization_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS enforce_active_membership_exactly_one_base_role()")

    op.execute("DROP TRIGGER IF EXISTS trg_user_organization_admin_minimum ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_role_organization_admin_minimum ON organization_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_membership_organization_admin_minimum ON organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_organization_admin_minimum ON organizations")
    op.execute("DROP FUNCTION IF EXISTS enforce_active_organization_admin_minimum()")
    op.execute("DROP FUNCTION IF EXISTS assert_active_organization_has_admin(uuid)")

    op.execute("DROP TRIGGER IF EXISTS trg_user_platform_eligibility ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_org_role_platform_eligibility ON organization_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_org_membership_platform_eligibility ON organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_platform_role_eligibility ON platform_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS enforce_platform_admin_eligibility()")

    op.execute("DROP TRIGGER IF EXISTS trg_platform_roles_administration_lock ON platform_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_organization_roles_administration_lock ON organization_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_memberships_administration_lock ON organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_organizations_administration_lock ON organizations")
    op.execute("DROP TRIGGER IF EXISTS trg_users_organization_administration_lock ON users")
    op.execute("DROP FUNCTION IF EXISTS acquire_organization_administration_lock()")

    op.drop_table("auth_organization_selection_challenges")

    op.execute("DROP TRIGGER IF EXISTS trg_user_sessions_active_org_membership ON user_sessions")
    op.execute("DROP FUNCTION IF EXISTS validate_user_session_organization_membership()")
    op.execute("DROP TRIGGER IF EXISTS trg_user_sessions_immutable_org_context ON user_sessions")
    op.execute("DROP FUNCTION IF EXISTS prevent_user_session_org_context_mutation()")

    op.drop_constraint(op.f("ck_user_sessions_mode_active_org"), "user_sessions", type_="check")
    op.drop_constraint(op.f("ck_user_sessions_mode"), "user_sessions", type_="check")
    op.drop_column("user_sessions", "recent_step_up_at")
    op.drop_column("user_sessions", "active_organization_id")
    op.drop_column("user_sessions", "session_mode")

    op.execute("DROP TRIGGER IF EXISTS trg_platform_role_history_truncate ON platform_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_platform_role_history ON platform_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS protect_platform_role_history()")
    op.drop_table("platform_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_org_capability_history_truncate ON organization_member_capabilities")
    op.execute("DROP TRIGGER IF EXISTS trg_org_capability_history ON organization_member_capabilities")
    op.execute("DROP FUNCTION IF EXISTS protect_organization_capability_history()")
    op.drop_table("organization_member_capabilities")
    op.execute("DROP TRIGGER IF EXISTS trg_org_role_assignment_history_truncate ON organization_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_org_role_assignment_history ON organization_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS protect_organization_role_history()")
    op.drop_table("organization_role_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_organization_membership_history_truncate ON organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_organization_membership_history ON organization_memberships")
    op.execute("DROP FUNCTION IF EXISTS protect_organization_membership_history()")
    op.drop_table("organization_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_organizations_protected_identity ON organizations")
    op.execute("DROP FUNCTION IF EXISTS protect_organization_identity()")
    op.drop_table("organizations")
