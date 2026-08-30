from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260810_0028_organization_stage1_invariants.py"


def test_organization_stage1_migration_is_single_linear_head():
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == ["20260829_0033"]
    revision = script.get_revision("20260810_0028")
    assert revision is not None
    assert revision.down_revision == "20260808_0027"


def test_organization_stage1_migration_contains_required_stage1_contracts():
    source = MIGRATION.read_text(encoding="utf-8")

    for table_name in (
        "organizations",
        "organization_memberships",
        "organization_role_assignments",
        "organization_member_capabilities",
        "platform_role_assignments",
        "auth_organization_selection_challenges",
    ):
        assert f'"{table_name}"' in source

    assert 'op.add_column("user_sessions", sa.Column("session_mode"' in source
    assert "server_default=sa.text(\"'personal'\")" in source
    assert "active_organization_id" in source
    assert "recent_step_up_at" in source
    assert "prevent_user_session_org_context_mutation" in source
    assert "validate_user_session_organization_membership" in source

    assert "enforce_active_membership_exactly_one_base_role" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "uq_organizations_normalized_code" in source
    assert "uq_organization_memberships_org_user" in source
    assert "ck_organizations_reserved_identity" in source
    assert "protect_organization_identity" in source
    assert "protect_organization_role_history" in source
    assert "protect_organization_capability_history" in source
    assert "protect_platform_role_history" in source
    assert "enforce_platform_admin_eligibility" in source
    assert "ck_auth_organization_selection_challenges_token_hash" in source
    assert "ck_auth_organization_selection_challenges_eligible_hash" in source
    assert "ck_auth_organization_selection_challenges_expiry" in source

    assert 'op.drop_table("auth_organization_selection_challenges")' in source
    assert 'op.drop_table("platform_role_assignments")' in source
    assert 'op.drop_table("organization_member_capabilities")' in source
    assert 'op.drop_table("organization_role_assignments")' in source
    assert 'op.drop_table("organization_memberships")' in source
    assert 'op.drop_table("organizations")' in source


def test_membership_history_contract_is_database_backed_and_downgrades_symmetrically():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'sa.ForeignKey("users.id", ondelete="RESTRICT")' in source
    assert "protect_organization_membership_history" in source
    assert "trg_organization_membership_history" in source
    for immutable_column in ("OLD.id", "OLD.organization_id", "OLD.user_id", "OLD.created_at"):
        assert immutable_column in source
    for append_only_timestamp in ("OLD.invited_at", "OLD.activated_at", "OLD.deactivated_at"):
        assert append_only_timestamp in source

    for trigger_name, table_name in (
        ("trg_organization_membership_history_truncate", "organization_memberships"),
        ("trg_org_role_assignment_history_truncate", "organization_role_assignments"),
        ("trg_org_capability_history_truncate", "organization_member_capabilities"),
        ("trg_platform_role_history_truncate", "platform_role_assignments"),
    ):
        assert f"CREATE TRIGGER {trigger_name}" in source
        assert f"BEFORE TRUNCATE ON {table_name}" in source
        assert f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}" in source

    assert '"users", "UPDATE OF is_active, deleted_at"' in source
    assert "GRANT SELECT, INSERT ON" in source
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON" in source

    drop_trigger = source.index(
        'DROP TRIGGER IF EXISTS trg_organization_membership_history ON organization_memberships'
    )
    drop_function = source.index(
        "DROP FUNCTION IF EXISTS protect_organization_membership_history()"
    )
    drop_table = source.index('op.drop_table("organization_memberships")')
    assert drop_trigger < drop_function < drop_table
