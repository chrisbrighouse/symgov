"""additive Stage 4 Project and Symbol Set persistence

Revision ID: 20260822_0030
Revises: 20260821_0029
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_0030"
down_revision = "20260821_0029"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION stage4_jsonb_max_depth(value jsonb) RETURNS integer
    LANGUAGE sql IMMUTABLE AS $$
      SELECT CASE
        WHEN jsonb_typeof(value) = 'object' THEN 1 + COALESCE((SELECT max(stage4_jsonb_max_depth(v)) FROM jsonb_each(value) e(k,v)), 0)
        WHEN jsonb_typeof(value) = 'array' THEN 1 + COALESCE((SELECT max(stage4_jsonb_max_depth(v)) FROM jsonb_array_elements(value) a(v)), 0)
        ELSE 0 END
    $$;
    CREATE OR REPLACE FUNCTION stage4_string_array_bounds(value jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_typeof(value) = 'array'
        AND jsonb_array_length(value) <= 32
        AND NOT EXISTS (
          SELECT 1 FROM jsonb_array_elements(value) AS element(value)
          WHERE jsonb_typeof(element.value) <> 'string'
             OR btrim(element.value #>> '{}') = ''
             OR char_length(btrim(element.value #>> '{}')) > 100
        )
    $$;
    """)
    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("normalized_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_description", sa.Text()),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("external_reference", sa.Text()),
        sa.Column("normalized_external_reference", sa.Text()),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("closed_at", TS),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{0,31}$'", name="ck_projects_code_format"),
        sa.CheckConstraint("normalized_code = lower(code)", name="ck_projects_normalized_code"),
        sa.CheckConstraint("char_length(short_description) <= 50", name="ck_projects_short_description_length"),
        sa.CheckConstraint("btrim(name) <> '' AND char_length(name) <= 200", name="ck_projects_name_bounds"),
        sa.CheckConstraint("external_reference is null or char_length(external_reference) <= 200", name="ck_projects_external_reference_length"),
        sa.CheckConstraint("status in ('active', 'closed')", name="ck_projects_status"),
        sa.CheckConstraint("jsonb_typeof(metadata_json) = 'object'", name="ck_projects_metadata_object"),
        sa.CheckConstraint("octet_length(convert_to(metadata_json::text, 'UTF8')) <= 16384 AND stage4_jsonb_max_depth(metadata_json) <= 4", name="ck_projects_metadata_bounds"),
        sa.UniqueConstraint("organization_id", "normalized_code", name="uq_projects_organization_normalized_code"),
    )
    op.create_index("ix_projects_organization_status_code_id", "projects", ["organization_id", "status", "normalized_code", "id"])
    op.create_index("uq_projects_organization_external_reference", "projects", ["organization_id", "normalized_external_reference"], unique=True, postgresql_where=sa.text("normalized_external_reference is not null"))

    op.create_table(
        "symbol_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("normalized_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("disciplines_json", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("use_cases_json", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("copied_from_symbol_set_id", UUID, sa.ForeignKey("symbol_sets.id", ondelete="RESTRICT")),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("superseded_at", TS),
        sa.Column("archived_at", TS),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{0,31}$'", name="ck_symbol_sets_code_format"),
        sa.CheckConstraint("normalized_code = lower(code)", name="ck_symbol_sets_normalized_code"),
        sa.CheckConstraint("description is null or char_length(description) <= 2000", name="ck_symbol_sets_description_length"),
        sa.CheckConstraint("btrim(name) <> '' AND char_length(name) <= 200", name="ck_symbol_sets_name_bounds"),
        sa.CheckConstraint("status in ('draft', 'active', 'superseded', 'archived')", name="ck_symbol_sets_status"),
        sa.CheckConstraint("stage4_string_array_bounds(disciplines_json)", name="ck_symbol_sets_disciplines_bounds"),
        sa.CheckConstraint("stage4_string_array_bounds(use_cases_json)", name="ck_symbol_sets_use_cases_bounds"),
        sa.CheckConstraint("copied_from_symbol_set_id IS NULL OR copied_from_symbol_set_id <> id", name="ck_symbol_sets_copy_not_self"),
        sa.UniqueConstraint("owner_organization_id", "normalized_code", name="uq_symbol_sets_owner_normalized_code"),
    )
    op.create_index("ix_symbol_sets_owner_status_code_id", "symbol_sets", ["owner_organization_id", "status", "normalized_code", "id"])
    op.add_column("organizations", sa.Column("default_symbol_set_id", UUID, nullable=True))
    op.create_foreign_key("fk_organizations_default_symbol_set", "organizations", "symbol_sets", ["default_symbol_set_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "project_symbol_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol_set_id", UUID, sa.ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("status in ('active', 'inactive')", name="ck_project_symbol_sets_status"),
        sa.UniqueConstraint("project_id", "symbol_set_id", name="uq_project_symbol_sets_project_set"),
    )
    op.create_index("uq_project_symbol_sets_active_default", "project_symbol_sets", ["project_id"], unique=True, postgresql_where=sa.text("status = 'active' AND is_default = true"))
    op.create_index("ix_project_symbol_sets_project_status_set", "project_symbol_sets", ["project_id", "status", "symbol_set_id"])
    op.create_index("ix_project_symbol_sets_set_status_project", "project_symbol_sets", ["symbol_set_id", "status", "project_id"])

    op.create_table(
        "symbol_set_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("symbol_set_id", UUID, sa.ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("governed_symbol_id", UUID, sa.ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("group_name", sa.Text()), sa.Column("display_label", sa.Text()),
        sa.Column("notes", sa.Text()), sa.Column("preferred_format", sa.Text()),
        sa.Column("provenance_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("availability_status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("availability_reason", sa.Text()), sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False), sa.Column("last_resolved_at", TS),
        sa.CheckConstraint("sort_order >= 0", name="ck_symbol_set_items_sort_order"),
        sa.CheckConstraint("availability_status in ('active', 'unavailable')", name="ck_symbol_set_items_availability_status"),
        sa.CheckConstraint("jsonb_typeof(provenance_json) = 'object'", name="ck_symbol_set_items_provenance_object"),
        sa.CheckConstraint("group_name is null or char_length(group_name) <= 200", name="ck_symbol_set_items_group_name_length"),
        sa.CheckConstraint("display_label is null or char_length(display_label) <= 200", name="ck_symbol_set_items_display_label_length"),
        sa.CheckConstraint("preferred_format is null or char_length(preferred_format) <= 200", name="ck_symbol_set_items_preferred_format_length"),
        sa.CheckConstraint("notes is null or char_length(notes) <= 2000", name="ck_symbol_set_items_notes_length"),
        sa.CheckConstraint("availability_reason is null or char_length(availability_reason) <= 500", name="ck_symbol_set_items_availability_reason_length"),
        sa.CheckConstraint("octet_length(convert_to(provenance_json::text, 'UTF8')) <= 16384 AND stage4_jsonb_max_depth(provenance_json) <= 4", name="ck_symbol_set_items_provenance_bounds"),
        sa.UniqueConstraint("symbol_set_id", "governed_symbol_id", name="uq_symbol_set_items_set_symbol"),
    )
    op.create_index("ix_symbol_set_items_set_order_symbol", "symbol_set_items", ["symbol_set_id", "sort_order", "governed_symbol_id"])
    op.create_index("ix_symbol_set_items_symbol_set", "symbol_set_items", ["governed_symbol_id", "symbol_set_id"])

    op.create_table(
        "user_project_set_selections",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("active_symbol_set_id", UUID, sa.ForeignKey("symbol_sets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_user_project_set_selections_active_set_project_user", "user_project_set_selections", ["active_symbol_set_id", "project_id", "user_id"])
    op.create_index("ix_user_project_set_selections_project_user", "user_project_set_selections", ["project_id", "user_id"])

    op.create_table(
        "user_session_project_contexts",
        sa.Column("user_session_id", UUID, sa.ForeignKey("user_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_user_session_project_contexts_project_session", "user_session_project_contexts", ["project_id", "user_session_id"])

    op.execute("""
    CREATE OR REPLACE FUNCTION validate_symbol_set_copy_owner() RETURNS TRIGGER AS $$
    BEGIN
      IF NEW.copied_from_symbol_set_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM symbol_sets parent
        WHERE parent.id = NEW.copied_from_symbol_set_id
          AND parent.owner_organization_id = NEW.owner_organization_id
      ) THEN RAISE EXCEPTION 'copied-from symbol set owner must match'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_symbol_set_copy_owner
    AFTER INSERT OR UPDATE OF copied_from_symbol_set_id, owner_organization_id ON symbol_sets DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_symbol_set_copy_owner();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION validate_project_symbol_set_owner() RETURNS TRIGGER AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM projects p JOIN symbol_sets s ON s.id = NEW.symbol_set_id WHERE p.id = NEW.project_id AND p.organization_id = s.owner_organization_id)
      THEN RAISE EXCEPTION 'project and symbol set owners must match'; END IF;
      IF NEW.status = 'active' AND NOT EXISTS (SELECT 1 FROM projects p JOIN symbol_sets s ON s.id = NEW.symbol_set_id WHERE p.id = NEW.project_id AND p.status = 'active' AND s.status = 'active')
      THEN RAISE EXCEPTION 'active availability requires active project and symbol set'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_project_symbol_sets_owner
    AFTER INSERT OR UPDATE ON project_symbol_sets DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_project_symbol_set_owner();
    CREATE OR REPLACE FUNCTION validate_symbol_set_dependents() RETURNS TRIGGER AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM organizations WHERE default_symbol_set_id = NEW.id AND (NEW.status <> 'active')) THEN RAISE EXCEPTION 'organization default requires active symbol set'; END IF;
      IF EXISTS (SELECT 1 FROM project_symbol_sets ps JOIN projects p ON p.id = ps.project_id WHERE ps.symbol_set_id = NEW.id AND ps.status = 'active' AND (NEW.status <> 'active' OR p.status <> 'active' OR p.organization_id <> NEW.owner_organization_id)) THEN RAISE EXCEPTION 'active availability requires active same-owner symbol set'; END IF;
      IF EXISTS (SELECT 1 FROM user_project_set_selections us JOIN project_symbol_sets ps ON ps.project_id = us.project_id AND ps.symbol_set_id = NEW.id WHERE us.active_symbol_set_id = NEW.id AND (NEW.status <> 'active' OR ps.status <> 'active')) THEN RAISE EXCEPTION 'selection requires active symbol set availability'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_symbol_sets_dependents_valid
    AFTER UPDATE OF id, owner_organization_id, status ON symbol_sets DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_symbol_set_dependents();
    CREATE OR REPLACE FUNCTION validate_project_dependents() RETURNS TRIGGER AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM project_symbol_sets ps JOIN symbol_sets s ON s.id = ps.symbol_set_id WHERE ps.project_id = NEW.id AND ps.status = 'active' AND (NEW.status <> 'active' OR NEW.organization_id <> s.owner_organization_id)) THEN RAISE EXCEPTION 'active availability requires active project and symbol set'; END IF;
      IF EXISTS (SELECT 1 FROM user_session_project_contexts c WHERE c.project_id = NEW.id AND NEW.status <> 'active') THEN RAISE EXCEPTION 'session project context requires active project'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_projects_dependents_valid
    AFTER UPDATE OF id, organization_id, status ON projects DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_project_dependents();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION validate_user_project_set_selection() RETURNS TRIGGER AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM project_symbol_sets ps JOIN projects p ON p.id = ps.project_id JOIN symbol_sets s ON s.id = ps.symbol_set_id WHERE ps.project_id = NEW.project_id AND ps.symbol_set_id = NEW.active_symbol_set_id AND ps.status = 'active' AND p.status = 'active' AND s.status = 'active')
      THEN RAISE EXCEPTION 'selection requires active project availability'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_user_project_set_selection_valid
    AFTER INSERT OR UPDATE ON user_project_set_selections DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_user_project_set_selection();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION validate_user_session_project_context() RETURNS TRIGGER AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM user_sessions us JOIN projects p ON p.id = NEW.project_id WHERE us.id = NEW.user_session_id AND us.revoked_at IS NULL AND us.expires_at > CURRENT_TIMESTAMP AND us.purpose = 'application' AND us.session_mode = 'organization' AND us.active_organization_id = p.organization_id AND p.status = 'active')
      THEN RAISE EXCEPTION 'session project context is not valid for session organization'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_user_session_project_context_valid
    AFTER INSERT OR UPDATE ON user_session_project_contexts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_user_session_project_context();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION validate_organization_symbol_set_default() RETURNS TRIGGER AS $$
    BEGIN
      IF NEW.default_symbol_set_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM symbol_sets s WHERE s.id = NEW.default_symbol_set_id AND s.owner_organization_id = NEW.id AND s.status = 'active')
      THEN RAISE EXCEPTION 'organization default requires active same-owner symbol set'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER trg_organization_symbol_set_default_valid
    AFTER INSERT OR UPDATE OF id, default_symbol_set_id ON organizations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_organization_symbol_set_default();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION protect_project_identity() RETURNS TRIGGER AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'project history cannot be deleted'; END IF;
      IF OLD.id IS DISTINCT FROM NEW.id OR OLD.organization_id IS DISTINCT FROM NEW.organization_id OR OLD.code IS DISTINCT FROM NEW.code OR OLD.normalized_code IS DISTINCT FROM NEW.normalized_code OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN RAISE EXCEPTION 'project identity is immutable'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_projects_identity BEFORE UPDATE OR DELETE ON projects FOR EACH ROW EXECUTE FUNCTION protect_project_identity();
    CREATE OR REPLACE FUNCTION protect_symbol_set_identity() RETURNS TRIGGER AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'symbol set history cannot be deleted'; END IF;
      IF OLD.id IS DISTINCT FROM NEW.id OR OLD.owner_organization_id IS DISTINCT FROM NEW.owner_organization_id OR OLD.code IS DISTINCT FROM NEW.code OR OLD.normalized_code IS DISTINCT FROM NEW.normalized_code OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN RAISE EXCEPTION 'symbol set identity is immutable'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_symbol_sets_identity BEFORE UPDATE OR DELETE ON symbol_sets FOR EACH ROW EXECUTE FUNCTION protect_symbol_set_identity();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION lock_governed_symbols_deterministically(symbol_ids uuid[]) RETURNS void
    SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    BEGIN
      PERFORM 1 FROM governed_symbols
      WHERE id IN (SELECT DISTINCT ids.id AS governed_symbol_id FROM unnest(symbol_ids) AS ids(id) ORDER BY governed_symbol_id)
      ORDER BY id FOR UPDATE;
    END; $$ LANGUAGE plpgsql;
    CREATE OR REPLACE FUNCTION lock_governed_symbol_boundary() RETURNS TRIGGER
    SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN PERFORM 1 FROM governed_symbols WHERE id = OLD.governed_symbol_id FOR UPDATE;
      ELSE PERFORM 1 FROM governed_symbols WHERE id = NEW.governed_symbol_id FOR UPDATE;
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_symbol_set_items_governed_symbol_lock BEFORE INSERT OR UPDATE OR DELETE ON symbol_set_items FOR EACH ROW EXECUTE FUNCTION lock_governed_symbol_boundary();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION cleanup_user_session_project_context() RETURNS TRIGGER AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR NEW.revoked_at IS NOT NULL THEN DELETE FROM user_session_project_contexts WHERE user_session_id = COALESCE(NEW.id, OLD.id); END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_user_sessions_project_context_cleanup AFTER UPDATE OF revoked_at OR DELETE ON user_sessions FOR EACH ROW EXECUTE FUNCTION cleanup_user_session_project_context();
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE ON projects, symbol_sets, project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts TO symgov_app")
    op.execute("GRANT DELETE ON project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts TO symgov_app")
    op.execute("GRANT EXECUTE ON FUNCTION lock_governed_symbols_deterministically(uuid[]) TO symgov_app")
    op.execute("REVOKE EXECUTE ON FUNCTION lock_governed_symbols_deterministically(uuid[]) FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON FUNCTION lock_governed_symbol_boundary() FROM PUBLIC")
    op.execute("GRANT SELECT, UPDATE ON organizations TO symgov_app")
    op.execute("REVOKE DELETE, TRUNCATE ON projects, symbol_sets FROM symgov_app")


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM projects LIMIT 1) OR EXISTS (SELECT 1 FROM symbol_sets LIMIT 1) OR EXISTS (SELECT 1 FROM project_symbol_sets LIMIT 1) OR EXISTS (SELECT 1 FROM symbol_set_items LIMIT 1) OR EXISTS (SELECT 1 FROM user_project_set_selections LIMIT 1) OR EXISTS (SELECT 1 FROM user_session_project_contexts LIMIT 1) OR EXISTS (SELECT 1 FROM organizations WHERE default_symbol_set_id IS NOT NULL)
      THEN RAISE EXCEPTION 'cannot downgrade while Stage 4 rows exist'; END IF;
    END $$;
    """)
    op.execute("REVOKE SELECT, INSERT, UPDATE ON projects, symbol_sets, project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts FROM symgov_app")
    op.execute("REVOKE DELETE ON project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts FROM symgov_app")
    op.execute("REVOKE EXECUTE ON FUNCTION lock_governed_symbols_deterministically(uuid[]) FROM symgov_app")
    op.execute("REVOKE EXECUTE ON FUNCTION lock_governed_symbols_deterministically(uuid[]) FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON FUNCTION lock_governed_symbol_boundary() FROM PUBLIC")
    op.execute("REVOKE SELECT, UPDATE ON organizations FROM symgov_app")
    op.execute("DROP TRIGGER IF EXISTS trg_user_sessions_project_context_cleanup ON user_sessions")
    op.execute("DROP TRIGGER IF EXISTS trg_symbol_set_items_governed_symbol_lock ON symbol_set_items")
    op.execute("DROP TRIGGER IF EXISTS trg_symbol_sets_identity ON symbol_sets")
    op.execute("DROP TRIGGER IF EXISTS trg_projects_identity ON projects")
    for trigger, table in (("trg_projects_dependents_valid", "projects"), ("trg_symbol_sets_dependents_valid", "symbol_sets"), ("trg_organization_symbol_set_default_valid", "organizations"), ("trg_user_session_project_context_valid", "user_session_project_contexts"), ("trg_user_project_set_selection_valid", "user_project_set_selections"), ("trg_project_symbol_sets_owner", "project_symbol_sets"), ("trg_symbol_set_copy_owner", "symbol_sets")):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in ("cleanup_user_session_project_context", "lock_governed_symbol_boundary", "protect_symbol_set_identity", "protect_project_identity", "validate_project_dependents", "validate_symbol_set_dependents", "validate_organization_symbol_set_default", "validate_user_session_project_context", "validate_user_project_set_selection", "validate_project_symbol_set_owner", "validate_symbol_set_copy_owner"):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
    op.execute("DROP FUNCTION IF EXISTS lock_governed_symbols_deterministically(uuid[])")
    op.drop_table("user_session_project_contexts")
    op.drop_table("user_project_set_selections")
    op.drop_table("symbol_set_items")
    op.drop_table("project_symbol_sets")
    op.drop_constraint("fk_organizations_default_symbol_set", "organizations", type_="foreignkey")
    op.drop_column("organizations", "default_symbol_set_id")
    op.drop_table("symbol_sets")
    op.drop_table("projects")
    op.execute("DROP FUNCTION IF EXISTS stage4_string_array_bounds(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS stage4_jsonb_max_depth(jsonb)")
