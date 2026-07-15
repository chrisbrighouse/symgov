from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend.models import ClarificationRecord


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "20260714_0020_catalog_feedback_attribution.py"
)
EXACTLY_ONE_SQL = (
    "(submitted_by is not null)::int + "
    "(external_submitter_id is not null)::int + "
    "(catalog_api_key_id is not null)::int = 1"
)


def _normalized_sql(value: object) -> str:
    return " ".join(str(value).lower().split())


def _load_migration():
    spec = spec_from_file_location("catalog_feedback_attribution_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clarification_record_has_catalog_attribution_and_context_metadata():
    table = ClarificationRecord.__table__
    catalog_column = table.columns["catalog_api_key_id"]
    context_column = table.columns["context_json"]

    assert catalog_column.nullable is True
    assert len(catalog_column.foreign_keys) == 1
    foreign_key = next(iter(catalog_column.foreign_keys))
    assert foreign_key.target_fullname == "catalog_api_keys.id"
    assert foreign_key.ondelete is None

    assert context_column.nullable is False
    assert context_column.default is None
    assert context_column.server_default is not None
    assert _normalized_sql(context_column.server_default.arg) == "'{}'::jsonb"


def test_clarification_record_enforces_exactly_one_submitter():
    constraints = {
        constraint.name: constraint
        for constraint in ClarificationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_clarification_records_clarification_records_one_submitter" not in constraints
    assert "ck_clarification_records_exactly_one_submitter" in constraints
    assert (
        _normalized_sql(constraints["ck_clarification_records_exactly_one_submitter"].sqltext)
        == EXACTLY_ONE_SQL
    )


@pytest.mark.parametrize(
    ("submitted_by", "external_submitter_id", "catalog_api_key_id", "is_valid"),
    [
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
        (False, False, False, False),
        (True, True, False, False),
        (True, False, True, False),
        (False, True, True, False),
        (True, True, True, False),
    ],
)
def test_exactly_one_submitter_truth_table(
    submitted_by: bool,
    external_submitter_id: bool,
    catalog_api_key_id: bool,
    is_valid: bool,
):
    assert (sum((submitted_by, external_submitter_id, catalog_api_key_id)) == 1) is is_valid


def test_clarification_record_has_catalog_key_created_at_index():
    index = next(
        index
        for index in ClarificationRecord.__table__.indexes
        if index.name == "ix_clarification_records_catalog_api_key_created_at"
    )
    assert [column.name for column in index.columns] == ["catalog_api_key_id", "created_at"]


def test_catalog_feedback_migration_links_to_current_head_and_has_safe_operations():
    migration = _load_migration()
    source = MIGRATION_PATH.read_text()

    assert migration.revision == "20260714_0020"
    assert migration.down_revision == "20260710_0019"
    assert 'op.add_column(\n        "clarification_records",\n        sa.Column("catalog_api_key_id"' in source
    assert 'sa.Column("context_json", JSONB, nullable=False, server_default=sa.text("\'{}\'::jsonb"))' in source
    assert 'op.drop_constraint("ck_clarification_records_one_submitter"' in source
    assert 'op.create_check_constraint(\n        "ck_clarification_records_exactly_one_submitter"' in source
    assert _normalized_sql(migration.EXACTLY_ONE_SUBMITTER) == EXACTLY_ONE_SQL
    assert 'op.create_foreign_key(\n        "fk_clarification_records_catalog_api_key_id"' in source
    assert 'ondelete=' not in source
    assert '"ix_clarification_records_catalog_api_key_created_at"' in source

    downgrade = source.split("def downgrade() -> None:", maxsplit=1)[1]
    assert downgrade.index('op.drop_index("ix_clarification_records_catalog_api_key_created_at"') < downgrade.index(
        'op.drop_column("clarification_records", "catalog_api_key_id")'
    )
    assert 'op.drop_constraint("ck_clarification_records_exactly_one_submitter"' in downgrade
    assert 'op.create_check_constraint(\n        "ck_clarification_records_one_submitter"' in downgrade
    assert 'op.drop_column("clarification_records", "context_json")' in downgrade
