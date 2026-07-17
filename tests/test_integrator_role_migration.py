from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260717_0021_integrator_role.py"
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"


def test_integrator_role_is_supported_by_model_and_reversible_migration():
    assert MIGRATION.exists()
    migration = MIGRATION.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")

    assert 'revision: str = "20260717_0021"' in migration
    assert 'down_revision: Union[str, None] = "20260714_0020"' in migration
    assert "'admin', 'integrator', 'submitter', 'reviewer'" in migration
    assert "DELETE FROM user_roles WHERE role = 'integrator'" in migration
    assert "'admin', 'integrator', 'submitter', 'reviewer'" in model