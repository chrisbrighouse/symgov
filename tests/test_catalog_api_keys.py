from pathlib import Path

from symgov_backend.models import CatalogApiKey


EXPECTED_COLUMNS = {
    "id",
    "customer_name",
    "integration_name",
    "key_prefix",
    "key_hash",
    "scopes_json",
    "status",
    "contact_name",
    "contact_email",
    "allowed_origins_json",
    "rate_limit_per_minute",
    "expires_at",
    "last_used_at",
    "created_by",
    "created_at",
    "updated_at",
    "revoked_at",
    "notes",
}

FUTURE_SCOPES = {
    "catalog.read",
    "catalog.preview",
    "catalog.ed.query",
    "catalog.feedback.write",
    "catalog.usage.read",
}


def test_catalog_api_key_model_stores_integration_credentials_not_raw_keys():
    columns = CatalogApiKey.__table__.columns

    assert CatalogApiKey.__tablename__ == "catalog_api_keys"
    assert EXPECTED_COLUMNS.issubset(set(columns.keys()))
    assert "api_key" not in columns
    assert "raw_key" not in columns
    assert "token" not in columns
    assert columns["customer_name"].nullable is False
    assert columns["integration_name"].nullable is False
    assert columns["key_hash"].nullable is False
    assert columns["key_prefix"].nullable is False
    assert columns["scopes_json"].nullable is False
    assert columns["status"].nullable is False
    assert columns["created_at"].nullable is False
    assert columns["updated_at"].nullable is False


def test_catalog_api_key_model_has_safe_lookup_indexes_and_status_constraint():
    index_names = {index.name for index in CatalogApiKey.__table__.indexes}
    constraints = {constraint.name for constraint in CatalogApiKey.__table__.constraints}

    assert "uq_catalog_api_keys_key_hash" in index_names
    assert "ix_catalog_api_keys_key_prefix" in index_names
    assert "ix_catalog_api_keys_customer_integration" in index_names
    assert "ck_catalog_api_keys_status" in constraints


def test_catalog_api_key_model_supports_planned_scopes_as_metadata():
    scopes_column = CatalogApiKey.__table__.columns["scopes_json"]
    metadata_column = CatalogApiKey.__table__.columns["allowed_origins_json"]

    assert scopes_column.default is None
    assert scopes_column.server_default is not None
    assert metadata_column.server_default is not None
    assert FUTURE_SCOPES == {
        "catalog.read",
        "catalog.preview",
        "catalog.ed.query",
        "catalog.feedback.write",
        "catalog.usage.read",
    }


def test_catalog_api_key_migration_creates_storage_without_raw_key_columns():
    migration_dir = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    migration_texts = [path.read_text() for path in migration_dir.glob("*_catalog_api_keys.py")]

    assert migration_texts, "Expected a catalog API keys Alembic migration"
    migration_text = "\n".join(migration_texts)

    assert "catalog_api_keys" in migration_text
    assert "key_hash" in migration_text
    assert "key_prefix" in migration_text
    assert "customer_name" in migration_text
    assert "integration_name" in migration_text
    assert "scopes_json" in migration_text
    assert "catalog.read" in migration_text
    assert "catalog.preview" in migration_text
    assert "catalog.ed.query" in migration_text
    assert "catalog.feedback.write" in migration_text
    assert "catalog.usage.read" in migration_text
    assert "raw_key" not in migration_text
    assert "raw_api_key" not in migration_text
    assert "plaintext" not in migration_text
