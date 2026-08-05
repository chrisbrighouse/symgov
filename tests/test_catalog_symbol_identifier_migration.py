import re
from pathlib import Path

from sqlalchemy import CheckConstraint, PrimaryKeyConstraint

from symgov_backend.models import CatalogSymbolIdentifier

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260802_0026_catalog_symbol_identifiers.py"
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
EXPORTS = ROOT / "backend" / "symgov_backend" / "models" / "__init__.py"


def _compact(source: str) -> str:
    return " ".join(source.split())


def _assert_fragments(source: str, *fragments: str) -> None:
    compact = _compact(source)
    for fragment in fragments:
        assert _compact(fragment) in compact, f"missing contract fragment: {fragment}"


def _class_source(source: str, name: str) -> str:
    match = re.search(rf"^class {name}\(Base\):.*?(?=^class |\Z)", source, re.MULTILINE | re.DOTALL)
    assert match, f"missing ORM model {name}"
    return match.group(0)


def test_0026_adds_catalog_symbol_identifier_storage_contract():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")

    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260802_0026"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260730_0025"', migration)
    _assert_fragments(
        migration,
        'op.create_table( "catalog_symbol_identifiers"',
        'sa.Column("identifier", sa.Text(), primary_key=True)',
        'sa.Column("role", sa.Text(), nullable=False)',
        'sa.Column("governed_symbol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_symbols.id", ondelete="SET NULL"), nullable=True)',
        'sa.Column("allocation_source", sa.Text(), nullable=False)',
        'sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False)',
        'sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True)',
        'sa.Column("changed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)',
        'sa.Column("change_reason", sa.Text(), nullable=True)',
        "role in ('canonical', 'historical_alias', 'tombstone')",
        "allocation_source in ('legacy_backfill', 'global_sequence', 'reviewed_correction')",
        "(role = 'tombstone' and governed_symbol_id is null) or (role in ('canonical', 'historical_alias') and governed_symbol_id is not null)",
        "identifier = upper(identifier) and identifier ~ '^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$'",
        'op.create_index("uq_catalog_symbol_identifiers_canonical_governed_symbol", "catalog_symbol_identifiers", ["governed_symbol_id"], unique=True, postgresql_where=sa.text("role = \'canonical\'"))',
        'op.add_column("governed_symbols", sa.Column("catalog_symbol_id", sa.Text(), nullable=True))',
        'op.create_index("uq_governed_symbols_catalog_symbol_id", "governed_symbols", ["catalog_symbol_id"], unique=True)',
        'op.create_foreign_key("fk_governed_symbols_catalog_symbol_id", "governed_symbols", "catalog_symbol_identifiers", ["catalog_symbol_id"], ["identifier"], ondelete="RESTRICT")',
        "CREATE SEQUENCE catalog_symbol_id_seq START 1 NO CYCLE",
    )


def test_catalog_symbol_identifier_primary_key_has_stable_explicit_name():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'sa.PrimaryKeyConstraint("identifier", name="pk_catalog_symbol_identifiers")',
    )

    primary_key = next(
        constraint
        for constraint in CatalogSymbolIdentifier.__table__.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    assert primary_key.name == "pk_catalog_symbol_identifiers"
    assert [column.name for column in primary_key.columns] == ["identifier"]


def test_0026_defers_same_symbol_cross_table_validation():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = _compact(MIGRATION.read_text(encoding="utf-8")).lower()
    function = re.search(
        r"create (?:or replace )?function validate_catalog_symbol_identifier_consistency\(\).*?language plpgsql",
        migration,
    )
    assert function, "missing catalog identifier validation function"
    body = function.group(0)
    assert "governed symbol catalog identifier is not its canonical registry identifier" in body
    assert "canonical catalog identifier is not linked by its governed symbol" in body
    assert body.count("raise exception") >= 2

    triggers = {
        "trg_catalog_symbol_identifiers_validate_consistency": "catalog_symbol_identifiers",
        "trg_governed_symbols_validate_catalog_symbol_consistency": "governed_symbols",
    }
    for name, table in triggers.items():
        assert re.search(
            rf"create constraint trigger {name}\b after insert or update on {table}\b.*?"
            r"deferrable initially deferred.*?for each row.*?"
            r"execute function validate_catalog_symbol_identifier_consistency\(\)",
            migration,
        ), f"missing INSERT OR UPDATE deferred trigger {name} on {table}"


def test_0026_deferred_validation_targets_only_queued_row_associations():
    migration = _compact(MIGRATION.read_text(encoding="utf-8")).lower()
    function = re.search(
        r"create (?:or replace )?function validate_catalog_symbol_identifier_consistency\(\).*?language plpgsql",
        migration,
    )
    assert function, "missing catalog identifier validation function"
    body = function.group(0)

    _assert_fragments(
        body,
        "tg_table_name = 'catalog_symbol_identifiers'",
        "tg_table_name = 'governed_symbols'",
        "tg_op = 'update'",
        "gs.catalog_symbol_id = any (affected_identifiers)",
        "csi.identifier = any (affected_identifiers)",
        "gs.id = any (affected_governed_symbol_ids)",
        "csi.governed_symbol_id = any (affected_governed_symbol_ids)",
    )
    assert "where gs.catalog_symbol_id is not null" not in body
    assert "where csi.role = 'canonical'" not in body


def test_0026_refuses_identity_losing_downgrades_before_drops():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = _compact(MIGRATION.read_text(encoding="utf-8")).lower()
    marker = "def downgrade() -> none:"
    assert marker in migration, "missing downgrade() declaration"
    downgrade = migration.partition(marker)[2]
    drop_positions = [
        downgrade.find(token)
        for token in ("drop trigger", "drop function", "drop constraint", "drop index")
    ]
    drop_positions = [position for position in drop_positions if position >= 0]
    assert drop_positions, "downgrade must drop triggers, constraints, or indexes"
    guarded_prefix = downgrade[: min(drop_positions)]

    catalog_lock = "lock table catalog_symbol_identifiers in access exclusive mode"
    governed_lock = "lock table governed_symbols in access exclusive mode"
    catalog_guard = "if exists (select 1 from catalog_symbol_identifiers limit 1) then"
    governed_guard = (
        "if exists (select 1 from governed_symbols where catalog_symbol_id is not null limit 1) then"
    )
    assert catalog_lock in guarded_prefix
    assert governed_lock in guarded_prefix
    assert guarded_prefix.index(catalog_lock) < guarded_prefix.index(catalog_guard)
    assert guarded_prefix.index(governed_lock) < guarded_prefix.index(governed_guard)

    assert catalog_guard in guarded_prefix
    assert (
        "if exists (select 1 from governed_symbols where catalog_symbol_id is not null limit 1) then"
        in guarded_prefix
    )
    assert guarded_prefix.count("raise exception") >= 2
    _assert_fragments(
        downgrade,
        "drop trigger if exists trg_catalog_symbol_identifiers_validate_consistency on catalog_symbol_identifiers",
        "drop trigger if exists trg_governed_symbols_validate_catalog_symbol_consistency on governed_symbols",
        "drop function if exists validate_catalog_symbol_identifier_consistency()",
        'op.drop_column("governed_symbols", "catalog_symbol_id")',
        "drop sequence catalog_symbol_id_seq",
        'op.drop_table("catalog_symbol_identifiers")',
    )


def test_catalog_symbol_identifier_orm_mapping_exists():
    model = MODEL.read_text(encoding="utf-8")
    mapping = _class_source(model, "CatalogSymbolIdentifier")
    _assert_fragments(
        mapping,
        '__tablename__ = "catalog_symbol_identifiers"',
        'identifier: Mapped[str] = mapped_column(Text, primary_key=True)',
        'role: Mapped[str] = mapped_column(Text, nullable=False)',
        'governed_symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id", ondelete="SET NULL"), nullable=True)',
        'allocation_source: Mapped[str] = mapped_column(Text, nullable=False)',
        'allocated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)',
        'changed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)',
        'changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)',
        'change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)',
    )


def test_catalog_symbol_identifier_orm_metadata_matches_migration_integrity():
    table = CatalogSymbolIdentifier.__table__
    checks = {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_catalog_symbol_identifiers_role": "role in ('canonical', 'historical_alias', 'tombstone')",
        "ck_catalog_symbol_identifiers_allocation_source": "allocation_source in ('legacy_backfill', 'global_sequence', 'reviewed_correction')",
        "ck_catalog_symbol_identifiers_role_target": "(role = 'tombstone' and governed_symbol_id is null) or (role in ('canonical', 'historical_alias') and governed_symbol_id is not null)",
        "ck_catalog_symbol_identifiers_grammar": "identifier = upper(identifier) and identifier ~ '^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$'",
    }

    canonical_index = next(
        index
        for index in table.indexes
        if index.name == "uq_catalog_symbol_identifiers_canonical_governed_symbol"
    )
    assert canonical_index.unique is True
    assert [column.name for column in canonical_index.columns] == ["governed_symbol_id"]
    assert _compact(str(canonical_index.dialect_options["postgresql"]["where"])).lower() == (
        "role = 'canonical'"
    )


def test_governed_symbol_has_nullable_unique_catalog_identifier_field():
    model = MODEL.read_text(encoding="utf-8")
    governed_symbol = _class_source(model, "GovernedSymbol")
    _assert_fragments(
        governed_symbol,
        'catalog_symbol_id: Mapped[str | None] = mapped_column(Text, ForeignKey("catalog_symbol_identifiers.identifier", ondelete="RESTRICT"), nullable=True, unique=True)',
    )


def test_catalog_symbol_identifier_is_exported_from_models():
    exports = EXPORTS.read_text(encoding="utf-8")
    assert re.search(r"\bCatalogSymbolIdentifier,", exports)
    assert '"CatalogSymbolIdentifier"' in exports
