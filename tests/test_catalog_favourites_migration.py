from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260718_0022_catalog_favourites.py"
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
MODEL_EXPORTS = ROOT / "backend" / "symgov_backend" / "models" / "__init__.py"


def test_catalog_favourites_have_account_scoped_model_and_reversible_migration():
    assert MIGRATION.exists()
    migration = MIGRATION.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")
    exports = MODEL_EXPORTS.read_text(encoding="utf-8")

    assert 'revision: str = "20260718_0022"' in migration
    assert 'down_revision: Union[str, None] = "20260717_0021"' in migration
    assert '"catalog_favourites"' in migration
    assert 'sa.ForeignKey("users.id", ondelete="CASCADE")' in migration
    assert 'sa.ForeignKey("governed_symbols.id", ondelete="CASCADE")' in migration
    assert 'sa.PrimaryKeyConstraint("user_id", "symbol_id")' in migration
    assert 'op.drop_table("catalog_favourites")' in migration

    assert "class CatalogFavourite(Base):" in model
    assert '__tablename__ = "catalog_favourites"' in model
    assert 'ForeignKey("users.id", ondelete="CASCADE")' in model
    assert 'ForeignKey("governed_symbols.id", ondelete="CASCADE")' in model
    assert "CatalogFavourite" in exports
