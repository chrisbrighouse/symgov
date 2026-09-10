"""Contract cover for SM-P0-04: the governed classification data model.

Scope note: this file is deliberately DB-free. It pins the migration/ORM
storage contract, the seeded scheme and node definitions, the controlled
vocabularies and the pure validators. Behaviour only a real PostgreSQL server
can prove -- the composite scheme/node foreign keys, the partial unique
indexes, check constraints actually rejecting rows, the seed landing in the
right order, and downgrade -- lives in
`test_classification_data_model_postgresql.py`.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend import classification_assignments as assignment_service
from symgov_backend import classification_schemes as scheme_service
from symgov_backend.catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
    FORMAT_ORDER,
)
from symgov_backend.classification_assignments import (
    BACKFILL_METHODS,
    CLASSIFICATION_ASSIGNMENT_METHODS,
    CLASSIFICATION_ASSIGNMENT_STATUSES,
    CLASSIFICATION_ASSIGNMENT_TRANSITIONS,
    CONCEPT_CLASSIFICATION_ROLES,
    SYMBOL_CLASSIFICATION_ROLES,
    normalize_classification_confidence,
    normalize_classification_evidence,
)
from symgov_backend.classification_schemes import (
    CLASSIFICATION_SCHEME_SCOPES,
    CLASSIFICATION_STATUS_TRANSITIONS,
    CLASSIFICATION_STATUSES,
    SEED_CLASSIFICATION_SCHEMES,
    SEED_VERSION_LABEL,
    SORT_ORDER_STEP,
    classification_node_seed_id,
    classification_scheme_seed_id,
    derive_classification_node_code,
    normalize_classification_node_code,
    normalize_classification_scheme_code,
    normalize_sort_order,
)
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.models import (
    ClassificationNode,
    ClassificationScheme,
    ConceptClassificationAssignment,
    SymbolRevisionClassificationAssignment,
)
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "backend" / "alembic" / "versions" / "20260909_0051_classification_data_model.py"
)
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
EXPORTS = ROOT / "backend" / "symgov_backend" / "models" / "__init__.py"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

ASSIGNMENT_TABLES = ("concept_classification_assignments", "symbol_revision_classifications")


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


def _checks(model) -> dict[str, str]:
    return {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _migration_module():
    """Import the migration by path, to compare its frozen seed data."""
    spec = importlib.util.spec_from_file_location("sm_p0_04_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Migration storage contract
# --------------------------------------------------------------------------


def test_0051_chains_from_the_constraint_name_repair_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0051"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0050"', migration)


def test_0051_creates_scheme_and_node_storage():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "classification_schemes"',
        'sa.Column("scheme_code", sa.Text(), nullable=False)',
        'sa.Column("scope", sa.Text(), nullable=False, server_default=sa.text("\'platform\'"))',
        'sa.Column("version_label", sa.Text(), nullable=False)',
        'op.create_index( "uq_classification_schemes_scheme_code", "classification_schemes", '
        '["scheme_code"], unique=True,',
        'op.create_table( "classification_nodes"',
        'sa.Column("node_code", sa.Text(), nullable=False)',
        'sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=True)',
        'sa.Column("preferred_label", sa.Text(), nullable=False)',
        'sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"))',
    )


def test_0051_creates_both_assignment_tables_without_collapsing_them():
    """Specification section 7.7 is meaning-oriented and section 7.8 is
    representation-oriented. One table for both would lose the distinction
    the specification draws between a concept's family and a graphic's
    drawing convention."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "concept_classification_assignments"',
        'sa.Column( "semantic_concept_id", postgresql.UUID(as_uuid=True), '
        'sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", '
        'name="fk_concept_classification_assignments_semantic_concept_id"), nullable=False,',
        'op.create_table( "symbol_revision_classifications"',
        'sa.Column( "symbol_revision_id", postgresql.UUID(as_uuid=True), '
        'sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", '
        'name="fk_symbol_revision_classifications_symbol_revision_id"), nullable=False,',
    )
    assert ConceptClassificationAssignment.__tablename__ == "concept_classification_assignments"
    assert SymbolRevisionClassificationAssignment.__tablename__ == "symbol_revision_classifications"


def test_0051_shortens_the_section_7_8_table_name():
    """The specification's logical name is
    `symbol_revision_classification_assignments` (42 characters). Every
    foreign key on it exceeds PostgreSQL's 63-character limit even when named
    explicitly, and so does one check constraint. Section 7 asks that final
    migration naming follow repository conventions, so the table ships as
    `symbol_revision_classifications` (31) while the ORM class keeps the
    specification's name."""
    assert len("symbol_revision_classification_assignments") == 42
    assert len(SymbolRevisionClassificationAssignment.__tablename__) == 31
    assert SymbolRevisionClassificationAssignment.__name__ == (
        "SymbolRevisionClassificationAssignment"
    )
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):]
    assert "symbol_revision_classification_assignments" not in upgrade


def test_0051_ties_every_assignment_to_a_node_of_the_scheme_it_names():
    """The composite key is what makes a cross-scheme assignment impossible
    rather than merely discouraged: `classification_scheme_id` is redundant
    with the node, and this foreign key holds the two in agreement."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'sa.UniqueConstraint("id", "scheme_id", name="uq_classification_nodes_id_scheme_id")',
        'sa.ForeignKeyConstraint( ["classification_node_id", "classification_scheme_id"], '
        '["classification_nodes.id", "classification_nodes.scheme_id"], ondelete="RESTRICT", '
        'name="fk_concept_classification_assignments_classification_node_id",',
        'sa.ForeignKeyConstraint( ["classification_node_id", "classification_scheme_id"], '
        '["classification_nodes.id", "classification_nodes.scheme_id"], ondelete="RESTRICT", '
        'name="fk_symbol_revision_classifications_classification_node_id",',
    )
    for model in (ConceptClassificationAssignment, SymbolRevisionClassificationAssignment):
        assert model.__table__.columns["classification_scheme_id"].nullable is False
        assert model.__table__.columns["classification_node_id"].nullable is False


def test_0051_keeps_a_node_parent_inside_its_own_scheme():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'sa.ForeignKeyConstraint( ["parent_node_id", "scheme_id"], '
        '["classification_nodes.id", "classification_nodes.scheme_id"], ondelete="RESTRICT", '
        'name="fk_classification_nodes_parent_node_id_scheme_id",',
        '"parent_node_id is null or parent_node_id <> id", name="parent_not_self",',
    )


def test_0051_parent_check_never_evaluates_to_null():
    """PostgreSQL accepts a check constraint that evaluates to NULL, so a bare
    `parent_node_id <> id` would silently pass for every root node. This bit
    SM-P0-03's checksum pairing; the null test here is load-bearing."""
    expression = _checks(ClassificationNode)["ck_classification_nodes_parent_not_self"]
    assert expression.startswith("parent_node_id is null or")


def test_0051_names_the_long_foreign_keys_explicitly():
    """The convention would generate names of 63 to 89 characters across these
    two tables. 63 is legal but has no headroom, so all of them are explicit."""
    migration = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_classification_nodes_parent_node_id_scheme_id",
        "fk_concept_classification_assignments_semantic_concept_id",
        "fk_concept_classification_assignments_classification_node_id",
        "fk_concept_classification_assignments_proposed_by_user_id",
        "fk_concept_classification_assignments_reviewed_by_user_id",
        "fk_symbol_revision_classifications_symbol_revision_id",
        "fk_symbol_revision_classifications_classification_node_id",
        "fk_symbol_revision_classifications_proposed_by_user_id",
        "fk_symbol_revision_classifications_reviewed_by_user_id",
    ):
        assert f'name="{name}"' in migration
        assert len(name) <= 63


def test_0051_check_constraint_names_are_bare():
    """A pre-prefixed name passed to sa.CheckConstraint inside op.create_table
    is silently double-prefixed and hash-truncated by SQLAlchemy. Every check
    constraint here passes a bare name and lets NAMING_CONVENTION prefix it."""
    migration = MIGRATION.read_text(encoding="utf-8")
    block = migration[migration.index("def upgrade()"):migration.index("def _seed_schemes")]
    found = 0
    for match in re.finditer(r"sa\.CheckConstraint\((.*?)\n        \)", block, re.DOTALL):
        name = re.search(r'name="([^"]+)"', match.group(1))
        assert name, f"unnamed check constraint: {match.group(1)[:60]}"
        assert not name.group(1).startswith("ck_"), (
            f"check constraint name must be bare, not pre-prefixed: {name.group(1)}"
        )
        found += 1
    assert found == 26, found


def test_0051_indexes_the_read_paths_the_specification_names():
    """Specification section 14.3: "Index classification assignments by target
    and classification_node_id"."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        '"ix_concept_classification_assignments_concept_status", '
        '"concept_classification_assignments", ["semantic_concept_id", "status"],',
        '"ix_concept_classification_assignments_node_status", '
        '"concept_classification_assignments", ["classification_node_id", "status"],',
        '"ix_symbol_revision_classifications_revision_status", '
        '"symbol_revision_classifications", ["symbol_revision_id", "status"],',
        '"ix_symbol_revision_classifications_node_status", '
        '"symbol_revision_classifications", ["classification_node_id", "status"],',
        '"ix_classification_nodes_scheme_id_sort_order", "classification_nodes", '
        '["scheme_id", "sort_order"],',
    )


def test_0051_constrains_one_verified_primary_per_scheme():
    """Not per target: a primary discipline and a primary category are both
    legitimate. A second verified primary category is not."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        '"uq_concept_classification_assignments_verified_primary", '
        '"concept_classification_assignments", '
        '["semantic_concept_id", "classification_scheme_id"], unique=True, '
        "postgresql_where=sa.text(\"assignment_role = 'primary' and status = 'verified'\"),",
        '"uq_symbol_revision_classifications_verified_primary", '
        '"symbol_revision_classifications", '
        '["symbol_revision_id", "classification_scheme_id"], unique=True, '
        "postgresql_where=sa.text(\"assignment_role = 'primary' and status = 'verified'\"),",
        '"uq_concept_classification_assignments_active_node", '
        '"concept_classification_assignments", '
        '["semantic_concept_id", "classification_node_id"], unique=True, '
        "postgresql_where=sa.text(\"status in ('proposed', 'verified')\"),",
        '"uq_symbol_revision_classifications_active_node", '
        '"symbol_revision_classifications", '
        '["symbol_revision_id", "classification_node_id"], unique=True, '
        "postgresql_where=sa.text(\"status in ('proposed', 'verified')\"),",
    )


@pytest.mark.parametrize(
    "model", [ConceptClassificationAssignment, SymbolRevisionClassificationAssignment],
    ids=lambda m: m.__tablename__,
)
def test_verified_primary_index_is_partial_on_role_and_status(model):
    index = next(
        candidate
        for candidate in model.__table__.indexes
        if candidate.name.endswith("_verified_primary")
    )
    assert index.unique is True
    assert [column.name for column in index.columns][-1] == "classification_scheme_id"
    assert _compact(str(index.dialect_options["postgresql"]["where"])).lower() == (
        "assignment_role = 'primary' and status = 'verified'"
    )


@pytest.mark.parametrize(
    "model", [ConceptClassificationAssignment, SymbolRevisionClassificationAssignment],
    ids=lambda m: m.__tablename__,
)
def test_active_node_index_excludes_closed_governance_states(model):
    """Rejected and retired rows stay out, so an assignment's governance
    history survives alongside whatever replaced it."""
    index = next(
        candidate for candidate in model.__table__.indexes if candidate.name.endswith("_active_node")
    )
    assert index.unique is True
    assert [column.name for column in index.columns][-1] == "classification_node_id"
    assert _compact(str(index.dialect_options["postgresql"]["where"])).lower() == (
        "status in ('proposed', 'verified')"
    )


def test_0051_is_purely_additive():
    """Section 12.1 phase M0: pure additive migration, no behaviour change.
    The dual-write of legacy category/discipline is SM-P0-09, so nothing here
    may touch an existing table."""
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    for forbidden in ("op.alter_column(", "op.drop_column(", "op.drop_table(", "op.add_column("):
        assert forbidden not in upgrade, f"upgrade must stay additive: {forbidden}"
    for table in ("governed_symbols", "symbol_revisions"):
        assert f'INSERT INTO {table}' not in upgrade
        assert f'UPDATE {table}' not in upgrade


def test_0051_downgrade_reverses_every_created_object():
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()"):]
    for table in (
        "symbol_revision_classifications",
        "concept_classification_assignments",
        "classification_nodes",
        "classification_schemes",
    ):
        assert f'op.drop_table("{table}")' in downgrade
    for index_name in (
        "uq_symbol_revision_classifications_active_node",
        "uq_symbol_revision_classifications_verified_primary",
        "ix_symbol_revision_classifications_node_status",
        "ix_symbol_revision_classifications_revision_status",
        "uq_concept_classification_assignments_active_node",
        "uq_concept_classification_assignments_verified_primary",
        "ix_concept_classification_assignments_node_status",
        "ix_concept_classification_assignments_concept_status",
        "ix_classification_nodes_parent_node_id",
        "ix_classification_nodes_scheme_id_sort_order",
        "ix_classification_schemes_status_scheme_code",
        "uq_classification_schemes_scheme_code",
    ):
        assert index_name in downgrade
    # Children before parents, or the RESTRICT foreign keys refuse the drop.
    assert (
        downgrade.index('op.drop_table("symbol_revision_classifications")')
        < downgrade.index('op.drop_table("classification_nodes")')
    )
    assert (
        downgrade.index('op.drop_table("concept_classification_assignments")')
        < downgrade.index('op.drop_table("classification_nodes")')
        < downgrade.index('op.drop_table("classification_schemes")')
    )


# --------------------------------------------------------------------------
# Seed definitions (section 12.1 phase M1)
# --------------------------------------------------------------------------


def test_the_seed_covers_the_three_schemes_with_a_hard_coded_order():
    codes = {definition["scheme_code"] for definition in SEED_CLASSIFICATION_SCHEMES}
    assert codes == {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY", "USE-CASE"}
    for definition in SEED_CLASSIFICATION_SCHEMES:
        assert (
            normalize_classification_scheme_code(definition["scheme_code"])
            == definition["scheme_code"]
        )
    assert SEED_VERSION_LABEL == "1"


def test_the_seed_labels_are_the_catalogue_orders_verbatim():
    """`sort_order` reproducing these lists is the whole point of phase M1:
    that ordering is what the catalogue UI renders today, so losing it would
    be a visible regression."""
    labels = {
        definition["scheme_code"]: list(definition["labels"])
        for definition in SEED_CLASSIFICATION_SCHEMES
    }
    assert labels["ENGINEERING-DISCIPLINE"] == CATALOG_DISCIPLINE_ORDER
    assert labels["SYMBOL-CATEGORY-FAMILY"] == CATALOG_CATEGORY_ORDER
    assert labels["USE-CASE"] == CATALOG_USE_CASE_ORDER
    assert [len(value) for value in (
        CATALOG_DISCIPLINE_ORDER, CATALOG_CATEGORY_ORDER, CATALOG_USE_CASE_ORDER
    )] == [11, 20, 6]


def test_the_migration_seed_matches_the_service_seed():
    """The migration repeats the label lists literally, because it must keep
    applying after the application constants move on. This test is what stops
    that from becoming a silent divergence."""
    module = _migration_module()
    frozen = {code: (name, description, list(labels)) for code, name, description, labels in module._SEED_SCHEMES}
    assert set(frozen) == {definition["scheme_code"] for definition in SEED_CLASSIFICATION_SCHEMES}
    for definition in SEED_CLASSIFICATION_SCHEMES:
        name, description, labels = frozen[definition["scheme_code"]]
        assert name == definition["name"]
        assert description == definition["description"]
        assert labels == list(definition["labels"])
    assert module._SEED_VERSION_LABEL == SEED_VERSION_LABEL
    assert module._SORT_ORDER_STEP == SORT_ORDER_STEP


def test_the_migration_derives_node_codes_the_same_way_the_service_does():
    """Two copies of the derivation, one frozen in the migration and one live
    in the service. The seed identifiers are uuid5 of the resulting code, so a
    divergence would silently produce a second set of nodes."""
    module = _migration_module()
    for definition in SEED_CLASSIFICATION_SCHEMES:
        for label in definition["labels"]:
            assert module._node_code(label) == derive_classification_node_code(label)


def test_seed_identifiers_are_reproducible_from_the_codes():
    for definition in SEED_CLASSIFICATION_SCHEMES:
        scheme_code = definition["scheme_code"]
        assert classification_scheme_seed_id(scheme_code) == uuid.uuid5(
            uuid.NAMESPACE_URL, f"urn:symgov:classification-scheme:{scheme_code}"
        )
        for label in definition["labels"]:
            node_code = derive_classification_node_code(label)
            assert classification_node_seed_id(scheme_code, node_code) == uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:symgov:classification-node:{scheme_code}:{node_code}",
            )


def test_seeded_node_codes_are_unique_within_each_scheme():
    """Punctuation collapses to underscores, so a collision is possible in
    principle -- "Piping / P&ID" and "Piping P ID" would produce one code."""
    for definition in SEED_CLASSIFICATION_SCHEMES:
        codes = [derive_classification_node_code(label) for label in definition["labels"]]
        assert len(set(codes)) == len(codes), definition["scheme_code"]


def test_the_seed_leaves_the_format_list_alone():
    """FORMAT_ORDER is a file-format list, not a classification facet."""
    seeded_labels = {
        label for definition in SEED_CLASSIFICATION_SCHEMES for label in definition["labels"]
    }
    assert seeded_labels.isdisjoint(set(FORMAT_ORDER))
    migration = MIGRATION.read_text(encoding="utf-8")
    seed = migration[migration.index("_SEED_SCHEMES:"):migration.index("def upgrade()")]
    for format_name in FORMAT_ORDER:
        assert f'"{format_name}"' not in seed


def test_the_seed_invents_no_vocabulary_for_industry_or_drawing_type():
    """Specification section 7.6 names Industry/Application and Drawing Type
    as desirable schemes, but neither has a hard-coded list to seed from.
    Creating them empty, or inventing node labels, is exactly what CLAUDE.md
    forbids -- the gap is reported to the product owner instead."""
    codes = {definition["scheme_code"] for definition in SEED_CLASSIFICATION_SCHEMES}
    for absent in ("INDUSTRY", "INDUSTRY-APPLICATION", "DRAWING-TYPE"):
        assert absent not in codes
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):]
    assert "DRAWING-TYPE" not in upgrade
    assert "INDUSTRY" not in upgrade


def test_the_seed_creates_no_assignment_rows():
    """Backfilling proposed assignments from the current category/discipline
    is section 12.1 phase M2 / SM-P0-09, not phase M1."""
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):]
    assert "INSERT INTO classification_schemes" in upgrade
    assert "INSERT INTO classification_nodes" in upgrade
    for table in ASSIGNMENT_TABLES:
        assert f"INSERT INTO {table}" not in upgrade
    assert upgrade.count("ON CONFLICT DO NOTHING") == 2


def test_the_seed_is_a_flat_hierarchy():
    """The hard-coded orders are flat lists. Inventing a parent hierarchy over
    them would be inventing vocabulary the catalogue does not have."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(migration, "(id, scheme_id, node_code, parent_node_id, preferred_label,")
    assert ":preferred_label, " in migration
    assert _compact("VALUES (:id, :scheme_id, :node_code, NULL, :preferred_label,") in _compact(
        migration
    )


# --------------------------------------------------------------------------
# ORM mapping parity
# --------------------------------------------------------------------------


def test_classification_orm_mappings_exist():
    model = MODEL.read_text(encoding="utf-8")
    _assert_fragments(
        _class_source(model, "ClassificationScheme"),
        '__tablename__ = "classification_schemes"',
        "scheme_code: Mapped[str] = mapped_column(Text, nullable=False)",
        "name: Mapped[str] = mapped_column(Text, nullable=False)",
        "version_label: Mapped[str] = mapped_column(Text, nullable=False)",
        "description: Mapped[str | None] = mapped_column(Text, nullable=True)",
    )
    _assert_fragments(
        _class_source(model, "ClassificationNode"),
        '__tablename__ = "classification_nodes"',
        "node_code: Mapped[str] = mapped_column(Text, nullable=False)",
        "parent_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)",
        "preferred_label: Mapped[str] = mapped_column(Text, nullable=False)",
        'sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))',
    )
    for name in ("ConceptClassificationAssignment", "SymbolRevisionClassificationAssignment"):
        _assert_fragments(
            _class_source(model, name),
            "classification_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)",
            "classification_scheme_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)",
            "assignment_role: Mapped[str] = mapped_column(Text, nullable=False)",
            "method: Mapped[str] = mapped_column(Text, nullable=False)",
            "confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)",
        )


def test_scheme_orm_metadata_matches_the_migration():
    # `_checks` lower-cases the whole expression, so the stored `[A-Z0-9]`
    # character classes read as `[a-z0-9]` here. The constraint itself is
    # case-sensitive; `test_scheme_code_normalizes_to_upper_case` and the
    # PostgreSQL rehearsal cover that.
    assert _checks(ClassificationScheme) == {
        "ck_classification_schemes_scheme_code": (
            "scheme_code ~ '^[a-z0-9][a-z0-9.-]{0,62}[a-z0-9]$'"
        ),
        "ck_classification_schemes_name": "btrim(name) <> '' and char_length(name) <= 256",
        "ck_classification_schemes_scope": "scope in ('platform')",
        "ck_classification_schemes_version_label": (
            "btrim(version_label) <> '' and char_length(version_label) <= 64"
        ),
        "ck_classification_schemes_status": (
            "status in ('draft', 'active', 'deprecated', 'withdrawn')"
        ),
        "ck_classification_schemes_description": (
            "description is null or (btrim(description) <> '' "
            "and char_length(description) <= 4000)"
        ),
    }


def test_node_orm_metadata_matches_the_migration():
    assert _checks(ClassificationNode) == {
        "ck_classification_nodes_node_code": "node_code ~ '^[a-z0-9][a-z0-9_]{0,62}[a-z0-9]$'",
        "ck_classification_nodes_preferred_label": (
            "btrim(preferred_label) <> '' and char_length(preferred_label) <= 256"
        ),
        "ck_classification_nodes_description": (
            "description is null or (btrim(description) <> '' "
            "and char_length(description) <= 4000)"
        ),
        "ck_classification_nodes_sort_order": "sort_order >= 0",
        "ck_classification_nodes_status": (
            "status in ('draft', 'active', 'deprecated', 'withdrawn')"
        ),
        "ck_classification_nodes_parent_not_self": (
            "parent_node_id is null or parent_node_id <> id"
        ),
    }


def test_concept_assignment_orm_metadata_matches_the_migration():
    assert _checks(ConceptClassificationAssignment) == {
        "ck_concept_classification_assignments_assignment_role": (
            "assignment_role in ('primary', 'secondary', 'inherited')"
        ),
        "ck_concept_classification_assignments_status": (
            "status in ('proposed', 'verified', 'rejected', 'retired')"
        ),
        "ck_concept_classification_assignments_method": (
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')"
        ),
        "ck_concept_classification_assignments_confidence": (
            "confidence is null or (confidence >= 0 and confidence <= 1)"
        ),
        "ck_concept_classification_assignments_review_decision": (
            "status in ('proposed', 'retired') or reviewed_at is not null"
        ),
        "ck_concept_classification_assignments_backfill_not_verified": (
            "method <> 'legacy_backfill' or status <> 'verified'"
        ),
        "ck_concept_classification_assignments_evidence_json_object": (
            "jsonb_typeof(evidence_json) = 'object'"
        ),
    }


def test_symbol_assignment_orm_metadata_matches_the_migration():
    assert _checks(SymbolRevisionClassificationAssignment) == {
        "ck_symbol_revision_classifications_assignment_role": (
            "assignment_role in ('primary', 'secondary')"
        ),
        "ck_symbol_revision_classifications_status": (
            "status in ('proposed', 'verified', 'rejected', 'retired')"
        ),
        "ck_symbol_revision_classifications_method": (
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill')"
        ),
        "ck_symbol_revision_classifications_confidence": (
            "confidence is null or (confidence >= 0 and confidence <= 1)"
        ),
        "ck_symbol_revision_classifications_review_decision": (
            "status in ('proposed', 'retired') or reviewed_at is not null"
        ),
        "ck_symbol_revision_classifications_backfill_not_verified": (
            "method <> 'legacy_backfill' or status <> 'verified'"
        ),
        "ck_symbol_revision_classifications_evidence_json_object": (
            "jsonb_typeof(evidence_json) = 'object'"
        ),
    }


def test_new_models_are_exported():
    exports = EXPORTS.read_text(encoding="utf-8")
    for name in (
        "ClassificationScheme",
        "ClassificationNode",
        "ConceptClassificationAssignment",
        "SymbolRevisionClassificationAssignment",
    ):
        assert re.search(rf"\b{name},", exports)
        assert f'"{name}"' in exports


# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------


def test_classification_roles_leave_proposed_to_the_status_column():
    """Specification section 7.7 lists `primary | secondary | inherited |
    proposed` and section 7.8 lists `primary | secondary | proposed`.
    `proposed` is a governance status everywhere else in this model (section
    8.4, SM-P0-01, -02 and -03), so this is read as a specification slip:
    `status` carries it, and it is not a role."""
    assert CONCEPT_CLASSIFICATION_ROLES == {"primary", "secondary", "inherited"}
    assert SYMBOL_CLASSIFICATION_ROLES == {"primary", "secondary"}
    assert "proposed" not in CONCEPT_CLASSIFICATION_ROLES
    assert "proposed" not in SYMBOL_CLASSIFICATION_ROLES
    assert "proposed" in CLASSIFICATION_ASSIGNMENT_STATUSES
    # A revision inherits nothing; its concept does.
    assert "inherited" not in SYMBOL_CLASSIFICATION_ROLES


def test_classification_statuses_and_scheme_lifecycle_match_the_model():
    assert CLASSIFICATION_ASSIGNMENT_STATUSES == {"proposed", "verified", "rejected", "retired"}
    assert CLASSIFICATION_STATUSES == {"draft", "active", "deprecated", "withdrawn"}
    assert set(CLASSIFICATION_STATUS_TRANSITIONS) == CLASSIFICATION_STATUSES
    assert CLASSIFICATION_STATUS_TRANSITIONS["withdrawn"] == set()
    assert "active" in CLASSIFICATION_STATUS_TRANSITIONS["deprecated"]


def test_scheme_scope_is_platform_only_in_p0():
    """Specification section 14.1 places scheme management with a platform
    admin and names organisation-specific schemes as a future extension. The
    deferral is structural: widening this set and the `scope` check constraint
    is the change that lands organisation-scoped schemes."""
    assert CLASSIFICATION_SCHEME_SCOPES == {"platform"}
    assert (
        _checks(ClassificationScheme)["ck_classification_schemes_scope"] == "scope in ('platform')"
    )


def test_classification_method_is_a_third_distinct_vocabulary():
    """Specification section 12.1 phase M2 adds `legacy_backfill`, which
    neither of the other two method vocabularies carries. All three are
    deliberately separate: unifying them is a specification change, not an
    implementation tidy-up."""
    assert CLASSIFICATION_ASSIGNMENT_METHODS == {
        "manual", "source_mapping", "rule", "ai_assisted", "legacy_backfill",
    }
    assert "legacy_backfill" not in SEMANTIC_ASSIGNMENT_METHODS
    assert "legacy_backfill" not in EXTERNAL_MAPPING_METHODS
    assert "imported" not in CLASSIFICATION_ASSIGNMENT_METHODS
    assert "source_mapping" not in EXTERNAL_MAPPING_METHODS
    assert SEMANTIC_ASSIGNMENT_METHODS < CLASSIFICATION_ASSIGNMENT_METHODS


def test_backfilled_assignments_can_never_be_verified():
    """Specification section 12.3. Enforced twice: the check constraint keeps
    the row out of the database, and the transition refuses the decision with
    an explanation."""
    assert BACKFILL_METHODS == {"legacy_backfill"}
    assert BACKFILL_METHODS <= CLASSIFICATION_ASSIGNMENT_METHODS
    for model in (ConceptClassificationAssignment, SymbolRevisionClassificationAssignment):
        expression = _checks(model)[f"ck_{model.__tablename__}_backfill_not_verified"]
        assert expression == "method <> 'legacy_backfill' or status <> 'verified'"


def test_assignment_lifecycle_covers_every_status_and_starts_only_at_proposed():
    assert set(CLASSIFICATION_ASSIGNMENT_TRANSITIONS) == CLASSIFICATION_ASSIGNMENT_STATUSES
    for reachable in CLASSIFICATION_ASSIGNMENT_TRANSITIONS.values():
        assert reachable <= CLASSIFICATION_ASSIGNMENT_STATUSES
    assert CLASSIFICATION_ASSIGNMENT_TRANSITIONS["proposed"] == {
        "verified", "rejected", "retired",
    }
    assert CLASSIFICATION_ASSIGNMENT_TRANSITIONS["verified"] == {"retired"}
    assert CLASSIFICATION_ASSIGNMENT_TRANSITIONS["rejected"] == set()
    assert CLASSIFICATION_ASSIGNMENT_TRANSITIONS["retired"] == set()


# --------------------------------------------------------------------------
# Pure validators
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["USE-CASE", "use-case", " ENGINEERING-DISCIPLINE ", "ISO.1"])
def test_scheme_code_normalizes_to_upper_case(value):
    assert normalize_classification_scheme_code(value) == value.strip().upper()


@pytest.mark.parametrize(
    "value", ["", "  ", "-USE", "USE-", "USE CASE", "USE/CASE", "U", 7, None, "USE_CASE"]
)
def test_scheme_code_rejects_codes_outside_the_grammar(value):
    with pytest.raises(ValueError):
        normalize_classification_scheme_code(value)


def test_scheme_code_rejects_a_code_past_the_length_limit():
    with pytest.raises(ValueError):
        normalize_classification_scheme_code("A" * 65)


@pytest.mark.parametrize("value", ["VALVES", "valves", " FIRE_LIFE_SAFETY "])
def test_node_code_normalizes_to_upper_case(value):
    assert normalize_classification_node_code(value) == value.strip().upper()


@pytest.mark.parametrize(
    "value", ["", "   ", "_VALVES", "VALVES_", "FIRE SAFETY", "FIRE-SAFETY", "V", 7, None]
)
def test_node_code_rejects_codes_outside_the_grammar(value):
    with pytest.raises(ValueError):
        normalize_classification_node_code(value)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Piping / P&ID", "PIPING_P_ID"),
        ("Fire & Life Safety", "FIRE_LIFE_SAFETY"),
        ("Miscellaneous / Unclassified", "MISCELLANEOUS_UNCLASSIFIED"),
        ("Use in PDF/report", "USE_IN_PDF_REPORT"),
        ("HVAC", "HVAC"),
    ],
)
def test_node_code_derivation_collapses_punctuation(label, expected):
    assert derive_classification_node_code(label) == expected


@pytest.mark.parametrize("label", ["", "   ", "///", "&", 7, None])
def test_node_code_derivation_rejects_a_label_with_no_code_in_it(label):
    with pytest.raises(ValueError):
        derive_classification_node_code(label)


@pytest.mark.parametrize("value", [0, 1, 10, 990])
def test_sort_order_accepts_non_negative_integers(value):
    assert normalize_sort_order(value) == value


@pytest.mark.parametrize("value", [-1, 1.5, "10", True, None])
def test_sort_order_rejects_negatives_and_wrong_types(value):
    with pytest.raises(ValueError):
        normalize_sort_order(value)


def test_sort_order_step_leaves_room_between_seeded_nodes():
    """Multiples of ten, so a node can later be inserted between two seeded
    ones without renumbering the scheme."""
    assert SORT_ORDER_STEP == 10


@pytest.mark.parametrize("value", [0, 1, 0.5, Decimal("0.9999")])
def test_confidence_accepts_the_closed_unit_interval(value):
    assert normalize_classification_confidence(value) == Decimal(str(value))


@pytest.mark.parametrize("value", [-0.0001, 1.0001, "0.5", True, object()])
def test_confidence_rejects_values_outside_the_unit_interval_or_wrong_type(value):
    with pytest.raises(ValueError):
        normalize_classification_confidence(value)


def test_evidence_must_be_an_object_when_present():
    assert normalize_classification_evidence(None) == {}
    assert normalize_classification_evidence({"legacy_value": "Valves"}) == {
        "legacy_value": "Valves"
    }
    with pytest.raises(ValueError):
        normalize_classification_evidence(["not", "an", "object"])


def test_confidence_is_not_a_governance_state():
    """Specification section 8.4: a 0.99 machine confidence stays proposed
    until policy allows otherwise. The review-decision path must therefore
    never read a confidence value."""
    source = Path(assignment_service.__file__).read_text(encoding="utf-8")
    body = source[source.index("def _transition("):source.index("def _retire_superseded_primary")]
    assert "confidence" not in body


def test_service_modules_expose_no_backfill_entry_point():
    """Section 12.1 phase M2 / SM-P0-09 owns the backfill. A helper that wrote
    legacy_backfill rows landing here would be that package arriving early."""
    for module in (scheme_service, assignment_service):
        for name in dir(module):
            if name.startswith("_"):
                continue
            assert "backfill" not in name.lower() or name == "BACKFILL_METHODS", name
            assert "dual_write" not in name.lower()
