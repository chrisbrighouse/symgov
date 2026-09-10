"""Contract cover for SM-P0-05: standard and source precision.

Scope note: this file is deliberately DB-free. It pins the migration/ORM
storage contract, the two new controlled vocabularies and their distance from
the three that came before, and the pure validators. Behaviour only a real
PostgreSQL server can prove -- check constraints actually rejecting rows, the
replaced unique index actually closing the duplicate-assertion hole, the
section 14.3 index existing, every added column being nullable or
server-defaulted, and downgrade restoring 20260409_0001's index -- lives in
`test_standard_source_precision_postgresql.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend import source_package_acquisition as package_service
from symgov_backend import standard_sources as standard_service
from symgov_backend.classification_assignments import CLASSIFICATION_ASSIGNMENT_METHODS
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.models import (
    SourcePackage,
    SourcePackageEntry,
    Standard,
    StandardVersion,
    SymbolStandardLink,
)
from symgov_backend.source_package_acquisition import (
    LICENSED_ACQUISITION_METHODS,
    PACKAGE_ACQUISITION_METHODS,
    normalize_acquisition,
    normalize_package_code,
    normalize_package_metadata,
    normalize_provider_identifier,
    requires_licence_reference,
)
from symgov_backend.standard_sources import (
    AUTHORITATIVE_RELATIONSHIP_TYPES,
    AUTO_VERIFIABLE_METHODS,
    CLOSED_STANDARD_STATUSES,
    LIVE_ASSERTION_STATUSES,
    SOURCE_RELATIONSHIP_TYPES,
    STANDARD_ASSERTION_STATUSES,
    STANDARD_ASSERTION_TRANSITIONS,
    STANDARD_STATUS_TRANSITIONS,
    STANDARD_STATUSES,
    STANDARD_VERIFICATION_METHODS,
    normalize_assertion_evidence,
    normalize_sha256,
    normalize_source_symbol_identifier,
    normalize_source_uri,
    normalize_standard_code,
)
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "backend" / "alembic" / "versions" / "20260910_0054_standard_and_source_precision.py"
)
INITIAL_MIGRATION = (
    ROOT / "backend" / "alembic" / "versions" / "20260409_0001_initial_symgov_schema.py"
)
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

DIGEST = "a" * 64

# The index 20260409_0001 created, whose nullable `clause_reference` made it
# enforce less than its name claims.
LEGACY_LINK_INDEX = "uq_symbol_standard_links_revision_standard_relationship_clause"

# Section 7.10's field list, verbatim.
LINK_COLUMNS = (
    "source_symbol_identifier",
    "figure_reference",
    "table_reference",
    "source_uri",
    "assertion_status",
    "verification_method",
    "source_asset_sha256",
    "verified_by_user_id",
    "verified_at",
    "evidence_json",
)

# Section 7.11's, for the package envelope.
PACKAGE_COLUMNS = (
    "provider_package_identifier",
    "source_uri",
    "release_version",
    "release_date",
    "acquired_at",
    "acquisition_method",
    "licence_reference",
    "package_sha256",
    "ingestion_profile",
    "metadata_json",
)

ENTRY_COLUMNS = ("provider_entry_identifier", "source_path", "original_asset_sha256")


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
    """Check constraints by their *bare* name.

    `NAMING_CONVENTION` has already prefixed `constraint.name` with
    `ck_<table>_` by the time the metadata is built; stripping it back keeps
    these assertions readable against the bare names the migration passes.
    """
    prefix = f"ck_{model.__tablename__}_"
    names = {}
    for constraint in model.__table__.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        assert constraint.name.startswith(prefix), constraint.name
        assert not constraint.name[len(prefix):].startswith("ck_"), (
            f"double-prefixed constraint name: {constraint.name}"
        )
        names[constraint.name[len(prefix):]] = _compact(str(constraint.sqltext)).lower()
    return names


def _migration_code() -> str:
    """The migration with its module docstring removed.

    The docstring quotes the very patterns several of these tests assert are
    absent from the DDL -- `NULLS NOT DISTINCT`, SM-P0-03's `{32,128}` digest
    grammar, the stage4 function names -- because explaining why they were
    *not* used is the point of writing it down.
    """
    source = MIGRATION.read_text(encoding="utf-8")
    return source.split('"""', 2)[2]


def _added_columns(source: str) -> set[str]:
    return set(re.findall(r'sa\.Column\(\s*"([a-z0-9_]+)"', source))


# --------------------------------------------------------------------------
# Migration storage contract
# --------------------------------------------------------------------------


def test_0054_chains_from_the_classification_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260910_0054"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0053"', migration)


def _added_to(table: str) -> set[str]:
    compact = _compact(_migration_code())
    return {
        column
        for target, column in re.findall(
            r'op\.add_column\(\s*"([a-z_]+)",\s*sa\.Column\(\s*"([a-z0-9_]+)"', compact
        )
        if target == table
    }


@pytest.mark.parametrize("column", LINK_COLUMNS)
def test_0054_adds_every_section_7_10_column(column):
    assert column in _added_to("symbol_standard_links")


@pytest.mark.parametrize("column", PACKAGE_COLUMNS)
def test_0054_adds_every_section_7_11_package_column(column):
    assert column in _added_to("source_packages")


@pytest.mark.parametrize("column", ENTRY_COLUMNS)
def test_0054_adds_every_section_7_11_entry_column(column):
    assert column in _added_to("source_package_entries")


def test_0054_adds_nothing_beyond_the_three_specification_field_lists():
    """Anything here that sections 7.10 and 7.11 do not name would be an
    invented column, and has to be argued for rather than slipped in."""
    assert _added_to("symbol_standard_links") == set(LINK_COLUMNS)
    assert _added_to("source_packages") == set(PACKAGE_COLUMNS)
    assert _added_to("source_package_entries") == set(ENTRY_COLUMNS)


def test_0054_adds_no_column_that_is_not_nullable_or_defaulted():
    """`source_packages` holds production rows, so a bare NOT NULL column
    would fail the migration outright on a deployed database."""
    migration = MIGRATION.read_text(encoding="utf-8")
    compact = _compact(_migration_code())
    specs = re.findall(r'sa\.Column\(\s*"[a-z0-9_]+".*?(?=sa\.Column\(|op\.|$)', compact)
    assert len(specs) == len(LINK_COLUMNS) + len(PACKAGE_COLUMNS) + len(ENTRY_COLUMNS)
    for spec in specs:
        assert "nullable=True" in spec or "server_default=" in spec, spec


def test_0054_touches_no_pre_existing_column():
    """Section 12.2: legacy fields are retained. Nothing here alters, renames
    or drops a column 20260409_0001 created."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "op.alter_column" not in migration
    assert "op.drop_column" not in migration.split("def downgrade")[0]
    assert "op.rename_table" not in migration


def test_0054_constrains_relationship_type_to_the_eight_section_8_3_values():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_check_constraint( "relationship_type", "symbol_standard_links",',
    )
    for value in (
        "normative_definition",
        "normative_equivalent",
        "informative_example",
        "vendor_implementation",
        "owner_variant",
        "project_deviation",
        "derived_from",
        "comparison_only",
    ):
        assert f'"{value}"' in migration


def test_0054_replaces_the_index_that_nulls_made_toothless():
    """Trap: `clause_reference` is nullable and PostgreSQL treats NULLs as
    distinct, so the original index never enforced what its name claims."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.drop_index(_LEGACY_LINK_INDEX, table_name="symbol_standard_links")',
        f'_LEGACY_LINK_INDEX = "{LEGACY_LINK_INDEX}"',
        'sa.text("coalesce(clause_reference, \'\')")',
    )
    # COALESCE rather than NULLS NOT DISTINCT, which needs PostgreSQL 15+.
    assert "NULLS NOT DISTINCT" not in _migration_code().upper()


def test_0054_downgrade_restores_the_original_index():
    """20260409_0001's own downgrade drops that index by name, so leaving the
    replacement in its place would break every rollback past the initial
    schema. 20260909_0050's no-op downgrade is not a precedent here."""
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade")[1]
    _assert_fragments(downgrade, "op.create_index( _LEGACY_LINK_INDEX,")
    for name in (
        "uq_symbol_standard_links_active_assertion",
        "uq_symbol_standard_links_verified_definition",
        "ix_symbol_standard_links_version_source_symbol_identifier",
    ):
        assert f'op.drop_index("{name}"' in downgrade

    initial = INITIAL_MIGRATION.read_text(encoding="utf-8")
    assert f'op.drop_index("{LEGACY_LINK_INDEX}"' in initial, (
        "the premise of the restore -- that 20260409_0001 drops this index by "
        "name -- no longer holds"
    )


def test_0054_downgrade_removes_exactly_what_upgrade_added():
    upgrade, downgrade = _migration_code().split("def downgrade")
    added = _added_columns(upgrade)
    dropped = set(re.findall(r'op\.drop_column\("[a-z_]+", "([a-z0-9_]+)"\)', downgrade))
    dropped |= {
        column.strip().strip('"')
        for block in re.findall(r"for column in \(([^)]*)\):", downgrade)
        for column in block.split(",")
        if column.strip()
    }
    assert added == dropped, (added ^ dropped)


def test_0054_downgrade_leaves_the_original_columns_alone():
    """20260409_0001's own columns must survive a rollback untouched."""
    downgrade = MIGRATION.read_text(encoding="utf-8").split("def downgrade")[1]
    dropped = {
        column.strip().strip('"')
        for block in re.findall(r"for column in \(([^)]*)\):", downgrade)
        for column in block.split(",")
        if column.strip()
    }
    dropped |= set(re.findall(r'op\.drop_column\("[a-z_]+", "([a-z0-9_]+)"\)', downgrade))
    original = {
        "id", "symbol_revision_id", "standard_version_id", "relationship_type",
        "clause_reference", "notes", "created_at", "updated_at", "package_code",
        "title", "provider", "package_type", "status", "source_package_id",
        "sort_order", "source_label",
    }
    assert dropped.isdisjoint(original), sorted(dropped & original)


def test_0054_check_constraint_names_are_bare():
    """`NAMING_CONVENTION` adds `ck_<table>_` exactly once. An already-prefixed
    name gets doubled and then silently hash-truncated -- the defect
    20260909_0053 spent a whole migration repairing."""
    migration = MIGRATION.read_text(encoding="utf-8")
    names = re.findall(r"op\.create_check_constraint\(\s*\"([a-z0-9_]+)\"", migration)
    assert names
    for name in names:
        assert not name.startswith("ck_"), name


def test_0054_identifiers_all_fit_the_postgresql_limit():
    """The convention's own name for the section 14.3 index is 66 characters
    and would be rejected outright, so it is named explicitly and short."""
    migration = MIGRATION.read_text(encoding="utf-8")
    identifiers = re.findall(
        r'(?:op\.create_index\(\s*|op\.drop_index\(\s*|name\s*=\s*)"([A-Za-z_][A-Za-z0-9_]*)"', migration
    )
    over = [name for name in identifiers if len(name) > 63]
    # The one over-long name in the file is 20260409_0001's own index, which
    # this migration only drops and restores by that name.
    assert over == [], over
    assert len("ix_symbol_standard_links_version_source_symbol_identifier") == 57


def test_0054_indexes_the_read_path_section_14_3_names():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_index( "ix_symbol_standard_links_version_source_symbol_identifier", '
        '"symbol_standard_links", ["standard_version_id", "source_symbol_identifier"],',
    )


def test_0054_hashes_are_sha256_only_and_carry_no_algorithm_column():
    """Sections 7.10 and 7.11 name SHA-256, so a fixed 64-character grammar
    rather than SM-P0-03's algorithm-paired `^[0-9a-f]{32,128}$`, which would
    admit a 32-character MD5 digest."""
    code = _migration_code()
    for column in ("source_asset_sha256", "package_sha256", "original_asset_sha256"):
        assert f"{column} ~ '^[0-9a-f]{{64}}$'" in code
    assert "32,128" not in code
    assert "checksum_algorithm" not in code
    assert "algorithm" not in _added_columns(code)


def test_0054_leaves_the_two_preserved_entities_unconstrained():
    """Section 7.10 preserves Standard and StandardVersion and extends
    neither, and names no status vocabulary for them. Declaring one in the
    database would be inventing a workflow state."""
    migration = MIGRATION.read_text(encoding="utf-8")
    touched = set(
        re.findall(r'op\.(?:add_column|create_check_constraint)\(\s*"?([a-z_]*)"?,?\s*"([a-z_]+)"', migration)
    )
    tables = {table for _first, table in touched} | {
        first for first, _table in touched if first in {"standards", "standard_versions"}
    }
    assert "standards" not in tables
    assert "standard_versions" not in tables


def test_0054_places_no_constraint_on_a_live_intake_column():
    """`source_packages` is written today by
    `runtime.ensure_source_package_for_intake`. A check constraint on one of
    its columns is validated against rows that path already wrote."""
    migration = MIGRATION.read_text(encoding="utf-8")
    constrained = re.findall(
        r'op\.create_check_constraint\(\s*"([a-z0-9_]+)",\s*"source_packages"', migration
    )
    assert constrained
    assert set(constrained).isdisjoint({"package_code", "title", "provider", "package_type", "status"})


def test_0054_uses_no_function_create_all_cannot_provide():
    """A JSONB bound spelled with the stage4 PL/pgSQL functions would break
    `Base.metadata.create_all()`, which is what
    INTENTIONAL_ORM_EXPRESSION_DIVERGENCE exists to record. Builtins only."""
    ddl = re.sub(r"#[^\n]*", "", _migration_code())
    assert "stage4_jsonb_max_depth" not in ddl
    assert "stage4_string_array_bounds" not in ddl
    assert "jsonb_typeof" in ddl, "the builtin the bounds are expressed with"


# --------------------------------------------------------------------------
# ORM / migration parity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "columns"),
    [
        (SymbolStandardLink, LINK_COLUMNS),
        (SourcePackage, PACKAGE_COLUMNS),
        (SourcePackageEntry, ENTRY_COLUMNS),
    ],
    ids=lambda value: getattr(value, "__tablename__", "columns"),
)
def test_the_orm_carries_every_added_column(model, columns):
    for column in columns:
        assert column in model.__table__.c, f"{model.__tablename__}.{column}"


@pytest.mark.parametrize(
    ("model", "columns"),
    [
        (SymbolStandardLink, LINK_COLUMNS),
        (SourcePackage, PACKAGE_COLUMNS),
        (SourcePackageEntry, ENTRY_COLUMNS),
    ],
    ids=lambda value: getattr(value, "__tablename__", "columns"),
)
def test_every_added_column_is_nullable_or_server_defaulted(model, columns):
    for name in columns:
        column = model.__table__.c[name]
        assert column.nullable or column.server_default is not None, name


def test_link_orm_constraints_match_the_migration():
    checks = _checks(SymbolStandardLink)
    assert set(checks) == {
        "relationship_type",
        "assertion_status",
        "verification_method",
        "verified_decision",
        "clause_reference",
        "source_symbol_identifier",
        "figure_reference",
        "table_reference",
        "source_uri",
        "source_asset_sha256",
        "source_asset_provenance",
        "evidence_json_object",
    }
    assert "normative_definition" in checks["relationship_type"]
    assert "import_manifest" in checks["verification_method"]
    assert "^[0-9a-f]{64}$" in checks["source_asset_sha256"]


def test_package_orm_constraints_match_the_migration():
    checks = _checks(SourcePackage)
    assert set(checks) == {
        "acquisition_method",
        "acquisition_pairing",
        "package_sha256",
        "package_integrity_context",
        "source_uri",
        "provider_package_identifier",
        "release_version",
        "licence_reference",
        "ingestion_profile",
        "metadata_json_object",
    }
    assert "licensed_download" in checks["acquisition_method"]


def test_entry_orm_constraints_match_the_migration():
    assert set(_checks(SourcePackageEntry)) == {
        "provider_entry_identifier",
        "source_path",
        "original_asset_sha256",
        "original_asset_context",
    }


def test_the_two_preserved_entities_gained_no_constraint():
    assert _checks(Standard) == {}
    assert _checks(StandardVersion) == {}


def test_no_added_check_constraint_can_evaluate_to_null():
    """PostgreSQL accepts a check constraint that evaluates to NULL, so a
    branch reading a nullable column must test it against NULL explicitly.
    This bit SM-P0-03's checksum pairing and SM-P0-04's parent check."""
    for model in (SymbolStandardLink, SourcePackage, SourcePackageEntry):
        for name, expression in _checks(model).items():
            nullable = {
                column.name
                for column in model.__table__.c
                if column.nullable and column.name in expression
            }
            if not nullable:
                continue
            assert "is null" in expression or "is not null" in expression, (
                f"{model.__tablename__}.{name} reads nullable columns {sorted(nullable)} "
                "without an explicit NULL test"
            )


def test_the_replacement_index_is_partial_and_collapses_the_null_clause():
    indexes = {index.name: index for index in SymbolStandardLink.__table__.indexes}
    assert LEGACY_LINK_INDEX not in indexes, "the toothless index is still declared"

    active = indexes["uq_symbol_standard_links_active_assertion"]
    assert active.unique
    predicate = _compact(str(active.dialect_options["postgresql"]["where"]))
    assert predicate == "assertion_status in ('proposed', 'verified')"
    expressions = [_compact(str(expression)) for expression in active.expressions]
    assert "coalesce(clause_reference, '')" in expressions
    assert "clause_reference" not in expressions, "the bare nullable column is still indexed"


def test_the_verified_definition_index_is_partial_on_type_and_status():
    indexes = {index.name: index for index in SymbolStandardLink.__table__.indexes}
    definition = indexes["uq_symbol_standard_links_verified_definition"]
    assert definition.unique
    predicate = _compact(str(definition.dialect_options["postgresql"]["where"]))
    assert predicate == "relationship_type = 'normative_definition' and assertion_status = 'verified'"


def test_the_section_14_3_index_is_declared_on_the_model():
    names = {index.name for index in SymbolStandardLink.__table__.indexes}
    assert "ix_symbol_standard_links_version_source_symbol_identifier" in names


def test_the_orm_documents_why_source_path_and_not_source_locator():
    """Section 7.11 offers both spellings; only one ships, and the reason has
    to survive in the model rather than only in a commit message."""
    model = _class_source(MODEL.read_text(encoding="utf-8"), "SourcePackageEntry")
    assert "source_locator" in model
    assert "source_path" in model
    assert "source_path" in SourcePackageEntry.__table__.c
    assert "source_locator" not in SourcePackageEntry.__table__.c


# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------


def test_the_eight_section_8_3_relationship_types_are_complete_and_exact():
    assert SOURCE_RELATIONSHIP_TYPES == frozenset(
        {
            "normative_definition",
            "normative_equivalent",
            "informative_example",
            "vendor_implementation",
            "owner_variant",
            "project_deviation",
            "derived_from",
            "comparison_only",
        }
    )
    assert AUTHORITATIVE_RELATIONSHIP_TYPES < SOURCE_RELATIONSHIP_TYPES


def test_assertion_status_carries_proposed_rather_than_a_role():
    """SM-P0-04 settled that `proposed` is a governance status, never a role.
    Section 7.10's `assertion_status` already gets this right."""
    assert STANDARD_ASSERTION_STATUSES == frozenset({"proposed", "verified", "rejected", "retired"})
    assert LIVE_ASSERTION_STATUSES == frozenset({"proposed", "verified"})


def test_verification_method_is_a_fourth_distinct_vocabulary():
    """Five `method` vocabularies now exist and are deliberately not unified.
    Unifying them is a specification change, not an implementation tidy-up."""
    assert STANDARD_VERIFICATION_METHODS == frozenset(
        {"manual", "import_manifest", "source_api", "ai_assisted"}
    )
    assert STANDARD_VERIFICATION_METHODS != SEMANTIC_ASSIGNMENT_METHODS
    assert STANDARD_VERIFICATION_METHODS != EXTERNAL_MAPPING_METHODS
    assert STANDARD_VERIFICATION_METHODS != CLASSIFICATION_ASSIGNMENT_METHODS
    # The two values section 7.10 introduces exist in no other vocabulary.
    for value in ("import_manifest", "source_api"):
        assert value not in SEMANTIC_ASSIGNMENT_METHODS
        assert value not in EXTERNAL_MAPPING_METHODS
        assert value not in CLASSIFICATION_ASSIGNMENT_METHODS


def test_acquisition_method_is_a_fifth_vocabulary_sharing_nothing():
    """Section 7.11's six values overlap none of the other four at all: how a
    package was procured is not how an assertion was arrived at."""
    assert PACKAGE_ACQUISITION_METHODS == frozenset(
        {"manual_upload", "public_download", "licensed_download", "api", "contributed", "generated"}
    )
    for other in (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
    ):
        assert PACKAGE_ACQUISITION_METHODS.isdisjoint(other)


def test_ai_assisted_can_never_be_verified_without_a_person():
    """Section 8.4: an AI classification is a proposal with evidence, not a
    self-certifying decision."""
    assert "ai_assisted" in STANDARD_VERIFICATION_METHODS
    assert "ai_assisted" not in AUTO_VERIFIABLE_METHODS
    assert "manual" not in AUTO_VERIFIABLE_METHODS
    assert AUTO_VERIFIABLE_METHODS == frozenset({"import_manifest", "source_api"})


def test_assertion_lifecycle_covers_every_status_and_treats_rejection_as_final():
    assert set(STANDARD_ASSERTION_TRANSITIONS) == set(STANDARD_ASSERTION_STATUSES)
    assert STANDARD_ASSERTION_TRANSITIONS["rejected"] == frozenset()
    assert STANDARD_ASSERTION_TRANSITIONS["retired"] == frozenset()
    assert STANDARD_ASSERTION_TRANSITIONS["verified"] == frozenset({"retired"})
    for reachable in STANDARD_ASSERTION_TRANSITIONS.values():
        assert reachable <= STANDARD_ASSERTION_STATUSES
        assert "proposed" not in reachable


def test_standard_status_policy_lives_in_the_service_not_the_database():
    """Called out deliberately: section 7.10 names no status vocabulary for
    Standard or StandardVersion, so this one is policy and this test is its
    only enforcement."""
    assert STANDARD_STATUSES == frozenset({"active", "deprecated", "withdrawn"})
    assert CLOSED_STANDARD_STATUSES == frozenset({"withdrawn"})
    assert set(STANDARD_STATUS_TRANSITIONS) == set(STANDARD_STATUSES)
    assert STANDARD_STATUS_TRANSITIONS["withdrawn"] == frozenset()
    assert _checks(Standard) == {} and _checks(StandardVersion) == {}


def test_licensed_acquisition_methods_point_forward_at_sm_p0_06():
    assert LICENSED_ACQUISITION_METHODS < PACKAGE_ACQUISITION_METHODS
    assert requires_licence_reference("licensed_download") is True
    assert requires_licence_reference("public_download") is False
    assert requires_licence_reference(None) is False


# --------------------------------------------------------------------------
# Pure validators
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("api spec 6d", "API SPEC 6D"),
        ("  ISO 10628-2:2012  ", "ISO 10628-2:2012"),
        ("ANSI/ISA-75.05.01-2019", "ANSI/ISA-75.05.01-2019"),
        ("api   spec   6d", "API SPEC 6D"),
    ],
)
def test_standard_code_normalizes_to_upper_case_and_single_spaces(value, expected):
    assert normalize_standard_code(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "-LEADING", "TRAILING-", "café", 7, None, "x" * 65])
def test_standard_code_rejects_what_is_not_a_code(value):
    with pytest.raises(ValueError):
        normalize_standard_code(value)


@pytest.mark.parametrize("value", [DIGEST, DIGEST.upper(), f"  {DIGEST}  "])
def test_sha256_accepts_a_64_character_digest_in_either_case(value):
    assert normalize_sha256(value, "hash") == DIGEST


@pytest.mark.parametrize(
    "value",
    [
        "a" * 63,
        "a" * 65,
        "g" * 64,  # not hexadecimal
        "d41d8cd98f00b204e9800998ecf8427e",  # a valid MD5, and still refused
        "",
        7,
    ],
)
def test_sha256_refuses_anything_that_is_not_a_sha256_digest(value):
    with pytest.raises(ValueError):
        normalize_sha256(value, "hash")


def test_sha256_passes_none_through():
    assert normalize_sha256(None, "hash") is None


@pytest.mark.parametrize("value", ["https://example.test/a", "http://example.test/a"])
def test_source_uri_accepts_http_and_https(value):
    assert normalize_source_uri(value) == value


@pytest.mark.parametrize("value", ["ftp://example.test/a", "example.test/a", "s3://bucket/key", "", 7])
def test_source_uri_refuses_a_locator_nobody_can_dereference(value):
    with pytest.raises(ValueError):
        normalize_source_uri(value)


def test_source_symbol_identifier_preserves_case():
    """The standard is the authority on its own identifiers: `A-123a` and
    `A-123A` may be two different symbols."""
    assert normalize_source_symbol_identifier("  A-123a  ") == "A-123a"
    assert normalize_source_symbol_identifier(None) is None


@pytest.mark.parametrize("value", ["", "   ", 7, "x" * 513])
def test_source_symbol_identifier_rejects_empty_and_over_long(value):
    with pytest.raises(ValueError):
        normalize_source_symbol_identifier(value)


def test_provider_identifiers_preserve_case_too():
    assert normalize_provider_identifier(" CFIHOS-90001234 ", "identifier") == "CFIHOS-90001234"
    assert normalize_provider_identifier(None, "identifier") is None


@pytest.mark.parametrize("value", [{}, {"figure": "6.2"}])
def test_evidence_accepts_any_json_object(value):
    assert normalize_assertion_evidence(value) == value


def test_evidence_defaults_to_an_empty_object():
    assert normalize_assertion_evidence(None) == {}


@pytest.mark.parametrize("value", ["{}", [], 7, True])
def test_evidence_refuses_anything_that_is_not_an_object(value):
    with pytest.raises(ValueError):
        normalize_assertion_evidence(value)


@pytest.mark.parametrize("value", ["{}", [], 7])
def test_package_metadata_refuses_anything_that_is_not_an_object(value):
    with pytest.raises(ValueError):
        normalize_package_metadata(value)


def test_acquisition_requires_both_halves_or_neither():
    assert normalize_acquisition(None, None) == (None, None)
    assert normalize_acquisition(NOW, "public_download") == (NOW, "public_download")
    with pytest.raises(ValueError):
        normalize_acquisition(NOW, None)
    with pytest.raises(ValueError):
        normalize_acquisition(None, "public_download")


def test_acquisition_refuses_an_invented_method_and_a_naive_timestamp():
    with pytest.raises(ValueError):
        normalize_acquisition(NOW, "telepathy")
    with pytest.raises(ValueError):
        normalize_acquisition(datetime(2026, 9, 10, 12, 0, 0), "public_download")


@pytest.mark.parametrize(
    ("value", "expected"), [("cfihos-core-2.0", "CFIHOS-CORE-2.0"), ("A1B2", "A1B2")]
)
def test_package_code_normalizes_to_upper_case(value, expected):
    assert normalize_package_code(value) == expected


@pytest.mark.parametrize("value", ["", "_LEADING", "TRAILING_", "a b", 7, "x" * 65])
def test_package_code_rejects_what_is_not_a_code(value):
    with pytest.raises(ValueError):
        normalize_package_code(value)


# --------------------------------------------------------------------------
# Service argument validation, without a database
# --------------------------------------------------------------------------


def test_asserting_a_link_refuses_an_invented_relationship_type():
    with pytest.raises(ValueError, match="relationship type"):
        standard_service.assert_symbol_standard_link(
            session=None,
            symbol_revision_id=uuid.uuid4(),
            standard_version_id=uuid.uuid4(),
            relationship_type="standard_associated",
            asserted_at=NOW,
        )


def test_asserting_a_link_refuses_a_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        standard_service.assert_symbol_standard_link(
            session=None,
            symbol_revision_id=uuid.uuid4(),
            standard_version_id=uuid.uuid4(),
            relationship_type="derived_from",
            asserted_at=datetime(2026, 9, 10, 12, 0, 0),
        )


def test_asserting_a_link_refuses_a_hash_with_nowhere_it_came_from():
    with pytest.raises(ValueError, match="where the asset was obtained"):
        standard_service.assert_symbol_standard_link(
            session=None,
            symbol_revision_id=uuid.uuid4(),
            standard_version_id=uuid.uuid4(),
            relationship_type="derived_from",
            asserted_at=NOW,
            source_asset_sha256=DIGEST,
        )


def test_transitioning_a_link_refuses_an_invented_status():
    with pytest.raises(ValueError, match="assertion status"):
        standard_service.transition_symbol_standard_link(
            None, uuid.uuid4(), target_status="approved", occurred_at=NOW
        )


def test_registering_a_standard_refuses_an_invented_status():
    with pytest.raises(ValueError, match="standard status"):
        standard_service.register_standard(
            None, standard_code="ISO 10628", title="Diagrams", registered_at=NOW, status="live"
        )


def test_registering_a_version_refuses_a_non_date_effective_date():
    with pytest.raises(ValueError, match="effective date"):
        standard_service.register_standard_version(
            None,
            standard_id=uuid.uuid4(),
            version_label="2012",
            registered_at=NOW,
            effective_date="2012-01-01",
        )


def test_adding_an_entry_refuses_a_hash_that_names_no_asset():
    with pytest.raises(ValueError, match="must name the entry"):
        package_service.add_source_package_entry(
            session=None,
            source_package_id=uuid.uuid4(),
            symbol_revision_id=uuid.uuid4(),
            added_at=NOW,
            original_asset_sha256=DIGEST,
        )


@pytest.mark.parametrize("sort_order", [-1, "3", True])
def test_adding_an_entry_refuses_a_nonsense_sort_order(sort_order):
    with pytest.raises(ValueError, match="sort order"):
        package_service.add_source_package_entry(
            session=None,
            source_package_id=uuid.uuid4(),
            symbol_revision_id=uuid.uuid4(),
            added_at=NOW,
            sort_order=sort_order,
        )


def test_registering_a_package_refuses_a_bad_release_date():
    with pytest.raises(ValueError, match="release date"):
        package_service.register_source_package(
            None,
            package_code="CFIHOS-CORE-2.0",
            title="CFIHOS CORE 2.0",
            registered_at=NOW,
            release_date="2023-01-01",
        )


def test_registering_a_package_validates_before_touching_the_session():
    """A rejected argument must leave the session untouched rather than
    half-populated, which is why validation runs before `session.add`."""
    with pytest.raises(ValueError, match="acquisition time and method"):
        package_service.register_source_package(
            None,
            package_code="CFIHOS-CORE-2.0",
            title="CFIHOS CORE 2.0",
            registered_at=NOW,
            release_date=date(2023, 1, 1),
            acquisition_method="public_download",
        )


def test_registering_a_package_refuses_a_licence_text_rather_than_a_reference():
    """Section 7.12: "a reference to terms/contract/rights record; not the
    licence text itself". The length bound is what keeps it one."""
    with pytest.raises(ValueError, match="licence reference"):
        package_service.register_source_package(
            None,
            package_code="CFIHOS-CORE-2.0",
            title="CFIHOS CORE 2.0",
            registered_at=NOW,
            licence_reference="x" * 513,
        )
