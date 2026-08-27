from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from symgov_backend.models import (
    Organization,
    Project,
    ProjectSymbolSet,
    SymbolSet,
    SymbolSetItem,
    UserProjectSetSelection,
    UserSessionProjectContext,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend/alembic/versions/20260822_0030_project_symbol_sets.py"


def test_project_symbol_set_migration_is_linear_head_and_models_are_exported():
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == ["20260826_0032"]
    revision = script.get_revision("20260822_0030")
    assert revision is not None
    assert revision.down_revision == "20260821_0029"
    assert MIGRATION.exists()


def test_stage4_orm_tables_have_frozen_columns_constraints_and_indexes():
    assert {
        "projects",
        "symbol_sets",
        "project_symbol_sets",
        "symbol_set_items",
        "user_project_set_selections",
        "user_session_project_contexts",
    } <= set(Project.metadata.tables)
    assert "default_symbol_set_id" in Organization.__table__.c
    assert {"id", "organization_id", "code", "normalized_code", "name", "short_description", "metadata_json"} <= set(Project.__table__.c.keys())
    assert {"id", "owner_organization_id", "code", "normalized_code", "disciplines_json", "use_cases_json"} <= set(SymbolSet.__table__.c.keys())
    assert {"project_id", "symbol_set_id", "is_default"} <= set(ProjectSymbolSet.__table__.c.keys())
    assert {"symbol_set_id", "governed_symbol_id", "sort_order", "provenance_json"} <= set(SymbolSetItem.__table__.c.keys())
    assert {"user_id", "project_id", "active_symbol_set_id"} <= set(UserProjectSetSelection.__table__.c.keys())
    assert {"user_session_id", "project_id"} <= set(UserSessionProjectContext.__table__.c.keys())

    assert any(isinstance(c, CheckConstraint) and "char_length(short_description)" in str(c.sqltext) for c in Project.__table__.constraints)
    assert any(isinstance(c, UniqueConstraint) and set(c.columns.keys()) == {"project_id", "symbol_set_id"} for c in ProjectSymbolSet.__table__.constraints)
    assert any(isinstance(c, ForeignKeyConstraint) and any(f.ondelete == "RESTRICT" for f in c.elements) for c in SymbolSetItem.__table__.constraints)
    assert any(i.name == "uq_project_symbol_sets_active_default" for i in ProjectSymbolSet.__table__.indexes)
    assert any(c.name == "uq_projects_organization_normalized_code" for c in Project.__table__.constraints)


def test_migration_contains_database_backed_invariants_and_old_writer_cleanup():
    source = MIGRATION.read_text(encoding="utf-8")
    for table in ("projects", "symbol_sets", "project_symbol_sets", "symbol_set_items", "user_project_set_selections", "user_session_project_contexts"):
        assert f'"{table}"' in source
    for marker in (
        "validate_project_symbol_set_owner",
        "validate_user_project_set_selection",
        "validate_user_session_project_context",
        "protect_project_identity",
        "protect_symbol_set_identity",
        "lock_governed_symbol_boundary",
        "trg_user_sessions_project_context_cleanup",
        "DEFERRABLE INITIALLY DEFERRED",
        "cannot downgrade while Stage 4 rows exist",
        "GRANT SELECT, INSERT, UPDATE ON",
    ):
        assert marker in source
    assert "char_length(short_description) <= 50" in source
    for marker in (
        "stage4_string_array_bounds",
        "ck_projects_name_bounds",
        "ck_projects_external_reference_length",
        "ck_symbol_sets_name_bounds",
        "ck_symbol_set_items_group_name_length",
        "ck_symbol_set_items_display_label_length",
        "ck_symbol_set_items_preferred_format_length",
        "ck_symbol_set_items_notes_length",
        "ck_symbol_set_items_availability_reason_length",
        "us.purpose = 'application'",
        "REVOKE EXECUTE ON FUNCTION lock_governed_symbol_boundary() FROM PUBLIC",
        "SET search_path = pg_catalog, public",
    ):
        assert marker in source
    assert "normalized_external_reference" in source
    assert "status = 'active' AND is_default = true" in source
    assert "ON UPDATE" not in source
    assert 'op.drop_table("user_session_project_contexts")' in source


def test_runtime_grants_allow_only_contractual_transient_deletes():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "GRANT DELETE ON project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts TO symgov_app" in source
    assert "REVOKE DELETE, TRUNCATE ON projects, symbol_sets FROM symgov_app" in source
    assert "REVOKE DELETE ON project_symbol_sets, symbol_set_items, user_project_set_selections, user_session_project_contexts FROM symgov_app" in source


def test_governed_symbol_mutation_has_delete_and_deterministic_batch_boundary():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION lock_governed_symbol_boundary()" in source
    assert "OLD.governed_symbol_id" in source
    assert "BEFORE INSERT OR UPDATE OR DELETE" in source
    assert "lock_governed_symbols_deterministically" in source
    assert "ORDER BY governed_symbol_id" in source


def test_symbol_set_copy_lineage_is_self_and_owner_safe():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "copied_from_symbol_set_id IS NULL OR copied_from_symbol_set_id <> id" in source
    assert "NEW.copied_from_symbol_set_id IS NOT NULL" in source
    assert "copied-from symbol set owner must match" in source


def test_orm_metadata_keeps_create_all_safe_provenance_boundary():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "stage4_jsonb_max_depth" in source
    assert "ck_symbol_set_items_provenance_bounds" in source
    assert not any(
        isinstance(c, CheckConstraint) and "stage4_jsonb_max_depth" in str(c.sqltext)
        for c in SymbolSetItem.__table__.constraints
    )
