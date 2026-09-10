"""Contract cover for SM-P0-06: rights and transformation provenance.

Scope note: this file is deliberately DB-free. It pins the migration/ORM
storage contract, section 7.12's two vocabularies and the sixth `method`
vocabulary's distance from the five before it, the pure validators, and --
because the whole package rests on it -- the premise that
`provenance_assessments` still cannot be the durable rights record.

Behaviour only a real PostgreSQL server can prove -- every check constraint
actually *rejecting* a row, the exactly-one-subject constraint refusing both
zero and two subjects, the approved-record uniqueness per subject, the
supersession, and a clean downgrade -- lives in
`test_rights_provenance_postgresql.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql

from symgov_backend import rights_provenance as rights_service
from symgov_backend.classification_assignments import CLASSIFICATION_ASSIGNMENT_METHODS
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.models import (
    AssetTransformation,
    ProvenanceAssessment,
    RightsRecord,
    SourcePackage,
    SourcePackageEntry,
)
from symgov_backend.rights_provenance import (
    LICENCE_BACKED_RIGHTS_STATUSES,
    LIVE_RIGHTS_DECISION_STATUSES,
    NON_APPROVING_DETERMINATION_METHODS,
    NON_PERMISSIVE_DISPOSITIONS,
    PERMISSIVE_DISPOSITIONS,
    PERMITTING_RIGHTS_STATUSES,
    RIGHTS_DECISION_STATUSES,
    RIGHTS_DECISION_TRANSITIONS,
    RIGHTS_DETERMINATION_METHODS,
    RIGHTS_DISPOSITIONS,
    RIGHTS_STATUSES,
    RIGHTS_SUBJECTS,
    disposition_is_permitted,
    normalize_decision_reason,
    normalize_licence_reference,
    normalize_rights_evidence,
)
from symgov_backend.source_package_acquisition import (
    LICENSED_ACQUISITION_METHODS,
    PACKAGE_ACQUISITION_METHODS,
)
from symgov_backend.standard_sources import STANDARD_VERIFICATION_METHODS
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260910_0056_rights_and_transformation_provenance.py"
)
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
SERVICE = ROOT / "backend" / "symgov_backend" / "rights_provenance.py"

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64

# Section 7.12's two field lists, as columns.
RIGHTS_COLUMNS = (
    "source_package_id",
    "standard_version_id",
    "symbol_revision_id",
    "rights_status",
    "disposition",
    "licence_reference",
    "determination_method",
    "decision_status",
    "decided_by_user_id",
    "decided_at",
    "decision_reason",
    "evidence_json",
    "proposed_by_user_id",
    "created_at",
    "updated_at",
)

TRANSFORMATION_COLUMNS = (
    "symbol_revision_id",
    "step_index",
    "source_package_entry_id",
    "source_asset_sha256",
    "tool_name",
    "tool_version",
    "derived_asset_sha256",
    "performed_at",
    "evidence_json",
    "recorded_by_user_id",
    "created_at",
)

# The deployed `provenance_assessments.rights_disposition` enumeration. Its
# complete disjointness from section 7.12's is one of the three reasons that
# table could not be extended into the durable record.
DEPLOYED_ASSESSMENT_DISPOSITIONS = frozenset(
    {"cleared", "unknown_warning", "restricted", "conflict", "failed"}
)

NEW_TABLES = ("rights_records", "asset_transformations")


class _StubSession:
    """The smallest session these argument-validation tests need.

    `record_asset_transformation` has to read the existing chain before it can
    tell a first step from a later one, so a few of its refusals cannot be
    proven with `session=None`. An empty chain is exactly the state that makes
    the first-step rules apply.
    """

    def __init__(self):
        self.added: list[object] = []

    def add(self, row):
        self.added.append(row)

    def execute(self, *_args, **_kwargs):
        return self

    def scalars(self):
        return []


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
    names: dict[str, str] = {}
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

    The docstring names the very things several of these tests assert are
    absent from the DDL -- `provenance_assessments`, the stage4 function
    names, SM-P0-03's `{32,128}` digest grammar -- because explaining why they
    were *not* used is the point of writing it down.
    """
    return MIGRATION.read_text(encoding="utf-8").split('"""', 2)[2]


def _service_code() -> str:
    """The service module with its own docstring removed, for the same reason."""
    return SERVICE.read_text(encoding="utf-8").split('"""', 2)[2]


def _ddl() -> str:
    """The migration's executable text: no module docstring, no comments.

    The comments explain at length why `provenance_assessments` could not be
    extended and why the stage4 functions are not used, so a search for
    either has to look at the statements rather than the prose.
    """
    return re.sub(r"#[^\n]*", "", _migration_code())


def _service_statements() -> str:
    """The service module's statements: no module docstring, no comments, no
    function docstrings."""
    code = re.sub(r"#[^\n]*", "", _service_code())
    return re.sub(r'"""(?:.|\n)*?"""', "", code)


def _created_columns(table: str) -> set[str]:
    return set(re.findall(r'sa\.Column\(\s*"([a-z0-9_]+)"', _table_block(table)))


def _created_tables() -> set[str]:
    return set(re.findall(r'op\.create_table\(\s*"([a-z_]+)"', _compact(_migration_code())))


def _table_block(table: str) -> str:
    """The `op.create_table` call for one table, up to the next op. call."""
    compact = _compact(_migration_code())
    start = compact.index(f'op.create_table( "{table}"')
    remainder = compact[start + 1:]
    end = remainder.find("op.create_index(")
    return remainder[:end] if end != -1 else remainder


def _migration_check_names(table: str) -> set[str]:
    return set(re.findall(r'name\s*=\s*"([a-z0-9_]+)"', _table_block(table))) - {
        f"pk_{table}"
    } - set(re.findall(r'name\s*=\s*"(fk_[a-z0-9_]+)"', _table_block(table)))


# --------------------------------------------------------------------------
# The premise: no durable rights entity exists to extend
# --------------------------------------------------------------------------


def test_provenance_assessments_is_still_intake_scoped_on_both_keys():
    """Section 7.12 says to extend an equivalent durable rights entity if one
    exists. `provenance_assessments` is the only rights-bearing table with a
    governed vocabulary, and both of its foreign keys are NOT NULL and
    intake-scoped -- so no rights decision can be attached to a source
    package, a standard edition or a governed symbol revision. If this ever
    stops being true, SM-P0-06's premise needs revisiting."""
    columns = ProvenanceAssessment.__table__.c
    assert columns["queue_item_id"].nullable is False
    assert columns["intake_record_id"].nullable is False
    for subject in RIGHTS_SUBJECTS:
        assert subject not in columns, subject


def test_the_deployed_disposition_vocabulary_shares_nothing_with_section_7_12():
    """The second reason. Section 7.12 requires `display | distribute |
    transform | compare_only | metadata_only | reject`; the deployed
    enumeration shares not one value with it."""
    expression = _checks(ProvenanceAssessment)["rights_disposition"]
    for value in DEPLOYED_ASSESSMENT_DISPOSITIONS:
        assert f"'{value}'" in expression, value
    assert DEPLOYED_ASSESSMENT_DISPOSITIONS.isdisjoint(RIGHTS_DISPOSITIONS)
    for value in RIGHTS_DISPOSITIONS:
        assert f"'{value}'" not in expression, value


def test_this_package_writes_neither_rejected_candidate_table():
    """The third reason: `provenance_assessments` is written by the live
    intake pipeline, and section 12.2 / SM-P0-09 reserve any change to an
    existing write path. Neither rejected candidate is named in the DDL or in
    the service module's code."""
    for table in ("provenance_assessments", "hannah_photo_candidates"):
        assert table not in _ddl(), table
        assert table not in _service_statements(), table


# --------------------------------------------------------------------------
# Migration storage contract
# --------------------------------------------------------------------------


def test_0056_chains_from_the_cfihos_seed_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260910_0056"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260910_0055"', migration)


def test_0056_creates_exactly_the_two_new_entities():
    assert _created_tables() == set(NEW_TABLES)


def test_0056_is_purely_additive():
    """Section 12.2: legacy fields are retained, and SM-P0-09 reserves any
    change to an existing write path. Nothing here adds a column to, alters,
    renames or drops anything that already exists."""
    upgrade = _migration_code().split("def downgrade")[0]
    for forbidden in ("op.add_column(", "op.alter_column(", "op.drop_column(", "op.rename_table("):
        assert forbidden not in upgrade, f"upgrade must stay additive: {forbidden}"
    assert "op.drop_table(" not in upgrade
    # `source_packages.licence_reference` stays exactly as SM-P0-05 left it:
    # the new record points at the package, not the other way round.
    assert "source_packages" in _table_block("rights_records")


@pytest.mark.parametrize("column", RIGHTS_COLUMNS)
def test_0056_creates_every_rights_record_column(column):
    assert column in _created_columns("rights_records")


@pytest.mark.parametrize("column", TRANSFORMATION_COLUMNS)
def test_0056_creates_every_transformation_column(column):
    assert column in _created_columns("asset_transformations")


def test_0056_creates_no_column_beyond_the_two_declared_field_lists():
    """Anything here that is not in the list above would be an invented
    column, and has to be argued for in the migration docstring rather than
    slipped in."""
    for table, columns in (
        ("rights_records", RIGHTS_COLUMNS),
        ("asset_transformations", TRANSFORMATION_COLUMNS),
    ):
        created = _created_columns(table)
        assert created == {"id", *columns}, created ^ {"id", *columns}


def test_0056_carries_section_7_12s_rights_status_vocabulary_verbatim():
    expression = _checks(RightsRecord)["rights_status"]
    for value in ("unknown", "open", "licensed", "restricted", "prohibited", "expired"):
        assert f"'{value}'" in expression, value
    assert len(re.findall(r"'[a-z_]+'", expression)) == 6


def test_0056_carries_section_7_12s_disposition_vocabulary_verbatim():
    expression = _checks(RightsRecord)["disposition"]
    for value in ("display", "distribute", "transform", "compare_only", "metadata_only", "reject"):
        assert f"'{value}'" in expression, value
    assert len(re.findall(r"'[a-z_]+'", expression)) == 6


def test_0056_copies_no_licence_text_only_a_reference():
    """Section 7.12: "a reference to evidence, not copied copyrighted terms".
    The 512-character bound -- SM-P0-05's, on the package column this one
    mirrors -- is what keeps it a reference."""
    code = _migration_code()
    assert "licence_text" not in code
    assert "licence_body" not in code
    assert "char_length(licence_reference) <= 512" in _compact(code)
    assert "licence_reference" not in AssetTransformation.__table__.c


def test_0056_adds_no_second_disposition_vocabulary_to_source_packages():
    """Trap: rights must not be modelled twice.
    `source_packages.licence_reference` is a reference and stays one."""
    assert "disposition" not in SourcePackage.__table__.c
    assert "rights_status" not in SourcePackage.__table__.c
    assert "disposition" not in SourcePackageEntry.__table__.c


def test_0056_links_the_two_new_tables_by_no_foreign_key():
    """Whether a transformation was *permitted* depends on the source
    subject's approved disposition including `transform`, and that judgement
    is section 9.2's publication gate -- SM-P0-08. Joining them is reporting,
    and the service does it read-only."""
    assert "rights_records" not in _table_block("asset_transformations")
    assert "asset_transformations" not in _table_block("rights_records")
    for column in AssetTransformation.__table__.c:
        for key in column.foreign_keys:
            assert key.column.table.name != "rights_records"


def test_0056_check_constraint_names_are_bare():
    """`NAMING_CONVENTION` adds `ck_<table>_` exactly once. An already-prefixed
    name passed to `sa.CheckConstraint` inside `op.create_table` is doubled
    and then silently hash-truncated -- the defect 20260909_0050 and
    20260909_0053 spent two migrations repairing."""
    for table, model in (
        ("rights_records", RightsRecord),
        ("asset_transformations", AssetTransformation),
    ):
        declared = _migration_check_names(table)
        # Both tables declare an `evidence_json_object`, so count per table.
        assert len(declared) == len(_checks(model)), sorted(declared)
        for name in declared:
            assert not name.startswith("ck_"), name


def test_0056_every_effective_identifier_fits_the_postgresql_limit():
    """The bare name is not what PostgreSQL sees -- the convention's prefix
    is. `ck_asset_transformations_transformation_changed_asset` is the longest
    at 53, and a name over 63 would be silently hash-truncated rather than
    rejected."""
    preparer = postgresql.dialect().identifier_preparer
    for model in (RightsRecord, AssetTransformation):
        table = model.__table__
        for constraint in table.constraints:
            emitted = preparer.format_constraint(constraint, _alembic_quote=False)
            assert len(emitted) <= 63, (emitted, len(emitted))
            if isinstance(constraint, CheckConstraint):
                # If SQLAlchemy had truncated it, the emitted name would carry
                # a 4-character hash suffix instead of the declared name.
                assert emitted == f"ck_{table.name}_{constraint.name.split('_', 2)[-1]}" or (
                    emitted.endswith(constraint.name[len(f"ck_{table.name}_"):])
                ), emitted
        for index in table.indexes:
            assert len(index.name) <= 63, (index.name, len(index.name))

    identifiers = re.findall(
        r'(?:op\.create_index\(\s*|op\.drop_index\(\s*|name\s*=\s*)"([A-Za-z_][A-Za-z0-9_]*)"',
        _migration_code(),
    )
    assert identifiers
    assert [name for name in identifiers if len(name) > 63] == []


def test_0056_names_every_foreign_key_explicitly():
    """The convention's own name for the entry key would be
    `fk_asset_transformations_source_package_entry_id_source_package_entries`
    at 72 characters, and alembic raises `IdentifierError` outright for an
    explicit name over the limit rather than truncating it."""
    code = _compact(_migration_code())
    for expected in (
        "fk_rights_records_source_package_id",
        "fk_rights_records_standard_version_id",
        "fk_rights_records_symbol_revision_id",
        "fk_rights_records_decided_by_user_id",
        "fk_rights_records_proposed_by_user_id",
        "fk_asset_transformations_symbol_revision_id",
        "fk_asset_transformations_source_package_entry_id",
        "fk_asset_transformations_recorded_by_user_id",
    ):
        assert f'name="{expected}"' in code, expected
    # No `sa.ForeignKey(` without a name= in the same call.
    for call in re.findall(r"sa\.ForeignKey\([^)]*\)", code):
        assert "name=" in call, call


def test_0056_hashes_are_sha256_only_and_carry_no_algorithm_column():
    """Sections 7.10, 7.11 and 7.12 all name SHA-256, so a fixed 64-character
    grammar rather than SM-P0-03's algorithm-paired `^[0-9a-f]{32,128}$`,
    which would admit a 32-character MD5 digest."""
    code = _ddl()
    for column in ("source_asset_sha256", "derived_asset_sha256"):
        assert f"{column} ~ '^[0-9a-f]{{64}}$'" in code
    assert "32,128" not in code
    assert "checksum_algorithm" not in code
    assert "algorithm" not in _created_columns("asset_transformations")


def test_0056_uses_no_function_create_all_cannot_provide():
    """A JSONB bound spelled with the stage4 PL/pgSQL functions would break
    `Base.metadata.create_all()`, which is what
    INTENTIONAL_ORM_EXPRESSION_DIVERGENCE exists to record and what
    tests/test_f0_4_review_without_unpublication.py calls. Builtins only."""
    ddl = _ddl()
    assert "stage4_jsonb_max_depth" not in ddl
    assert "stage4_string_array_bounds" not in ddl
    assert "jsonb_typeof" in ddl, "the builtin the bounds are expressed with"


def test_0056_downgrade_drops_exactly_what_upgrade_created():
    upgrade, downgrade = _migration_code().split("def downgrade")
    created_tables = set(re.findall(r'op\.create_table\(\s*"([a-z_]+)"', _compact(upgrade)))
    dropped_tables = set(re.findall(r'op\.drop_table\("([a-z_]+)"\)', downgrade))
    assert created_tables == dropped_tables

    created_indexes = set(re.findall(r'op\.create_index\(\s*"([a-z0-9_]+)"', _compact(upgrade)))
    dropped_indexes = set(re.findall(r'op\.drop_index\("([a-z0-9_]+)"', downgrade))
    assert created_indexes == dropped_indexes

    # Nothing else: no pre-existing object is recreated on the way down, as
    # 20260910_0054 had to do for 20260409_0001's index.
    assert "op.create_index(" not in downgrade
    assert "op.create_table(" not in downgrade
    assert "op.add_column(" not in downgrade


def test_0056_creates_the_indexes_the_supersession_rule_needs():
    code = _compact(_migration_code())
    for name in (
        "uq_rights_records_approved_source_package",
        "uq_rights_records_approved_standard_version",
        "uq_rights_records_approved_symbol_revision",
    ):
        assert f'op.create_index( "{name}"' in code, name
    assert code.count("decision_status = 'approved' and") == 3
    assert 'op.create_index( "uq_asset_transformations_revision_step"' in code


# --------------------------------------------------------------------------
# ORM / migration parity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "columns"),
    [(RightsRecord, RIGHTS_COLUMNS), (AssetTransformation, TRANSFORMATION_COLUMNS)],
    ids=lambda value: getattr(value, "__tablename__", "columns"),
)
def test_the_orm_carries_every_created_column(model, columns):
    assert set(model.__table__.c.keys()) == {"id", *columns}


@pytest.mark.parametrize("table", NEW_TABLES)
def test_the_orm_constraint_names_match_the_migrations(table):
    model = {"rights_records": RightsRecord, "asset_transformations": AssetTransformation}[table]
    assert set(_checks(model)) == _migration_check_names(table)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_the_orm_index_names_match_the_migrations(table):
    model = {"rights_records": RightsRecord, "asset_transformations": AssetTransformation}[table]
    declared = {index.name for index in model.__table__.indexes}
    in_migration = {
        name
        for name, target in re.findall(
            r'op\.create_index\(\s*"([a-z0-9_]+)",\s*"([a-z_]+)"', _compact(_migration_code())
        )
        if target == table
    }
    assert declared == in_migration, declared ^ in_migration


def test_rights_record_orm_constraints_are_the_expected_set():
    checks = _checks(RightsRecord)
    assert set(checks) == {
        "subject_exactly_one",
        "rights_status",
        "disposition",
        "determination_method",
        "decision_status",
        "licence_reference",
        "decision_reason",
        "decision_actor",
        "approved_reason",
        "approved_not_ai_determined",
        "approved_licence_reference",
        "evidence_json_object",
    }
    assert "'metadata_only'" in checks["disposition"]
    assert "'licence_document'" in checks["determination_method"]


def test_transformation_orm_constraints_are_the_expected_set():
    checks = _checks(AssetTransformation)
    assert set(checks) == {
        "step_index",
        "tool_name",
        "tool_version",
        "source_asset_sha256",
        "derived_asset_sha256",
        "source_asset_identified",
        "transformation_changed_asset",
        "evidence_json_object",
    }
    assert "^[0-9a-f]{64}$" in checks["derived_asset_sha256"]


def test_no_added_check_constraint_can_evaluate_to_null():
    """PostgreSQL accepts a check constraint that evaluates to NULL, so a
    branch reading a nullable column must test it against NULL explicitly.
    This bit SM-P0-03's checksum pairing and SM-P0-04's parent check."""
    for model in (RightsRecord, AssetTransformation):
        for name, expression in _checks(model).items():
            nullable = {
                column.name
                for column in model.__table__.c
                if column.nullable and column.name in expression
            }
            if not nullable:
                continue
            assert (
                "is null" in expression
                or "is not null" in expression
                or "case when" in expression
            ), (
                f"{model.__tablename__}.{name} reads nullable columns {sorted(nullable)} "
                "without an explicit NULL test"
            )


def test_the_subject_constraint_is_an_integer_comparison():
    """`(a is null) = (b is null)` works for a pair, but three subjects need a
    count. A sum of `case` expressions compares two integers, so it is true or
    false and can never be NULL."""
    expression = _checks(RightsRecord)["subject_exactly_one"]
    assert expression.count("case when") == 3
    assert expression.endswith("= 1")
    for subject in RIGHTS_SUBJECTS:
        assert subject in expression, subject


def test_every_subject_column_is_nullable_and_restricted():
    """Exactly one is set per row, so all three must be nullable. RESTRICT in
    every direction: a rights decision must not disappear because its subject
    was deleted out from under it (section 14.4)."""
    for subject in RIGHTS_SUBJECTS:
        column = RightsRecord.__table__.c[subject]
        assert column.nullable, subject
        assert [key.ondelete for key in column.foreign_keys] == ["RESTRICT"], subject


def test_the_approver_is_the_one_restrict_actor_key_in_the_model():
    """Section 14.4 retains the governance decision history with the governed
    data, and an approved rights record that has lost its approver is
    precisely the record that must not exist. Every other actor column in the
    semantic model is SET NULL, and the two here that are not decisions stay
    that way."""
    assert [
        key.ondelete for key in RightsRecord.__table__.c["decided_by_user_id"].foreign_keys
    ] == ["RESTRICT"]
    assert [
        key.ondelete for key in RightsRecord.__table__.c["proposed_by_user_id"].foreign_keys
    ] == ["SET NULL"]
    assert [
        key.ondelete
        for key in AssetTransformation.__table__.c["recorded_by_user_id"].foreign_keys
    ] == ["SET NULL"]


def test_the_two_status_columns_carry_the_defaults_the_migration_declares():
    assert "unknown" in str(RightsRecord.__table__.c["rights_status"].server_default.arg)
    assert "proposed" in str(RightsRecord.__table__.c["decision_status"].server_default.arg)
    assert RightsRecord.__table__.c["disposition"].server_default is None, (
        "a disposition has no honest default; the caller must state one"
    )


def test_the_derived_digest_is_required_and_the_source_digest_is_not():
    """Section 9.2's integrity minimum is "at least the final stored asset
    hash; source hash where available". SymGov produced the derived asset, so
    its digest is always available."""
    assert AssetTransformation.__table__.c["derived_asset_sha256"].nullable is False
    assert AssetTransformation.__table__.c["source_asset_sha256"].nullable is True
    assert AssetTransformation.__table__.c["step_index"].nullable is False


def test_the_model_documents_why_a_new_entity_rather_than_an_extension():
    """The reasoning has to survive in the model rather than only in a commit
    message: the next person to read section 7.12 will ask the same question."""
    model = _class_source(MODEL.read_text(encoding="utf-8"), "RightsRecord")
    assert "provenance_assessments" in model
    assert "hannah_photo_candidates" in model
    assert "7.12" in model


def test_the_lineage_model_documents_the_one_table_decision():
    model = _class_source(MODEL.read_text(encoding="utf-8"), "AssetTransformation")
    assert "step_index" in model
    assert "original_asset_sha256" in model, "the SM-P0-05 hash it joins to rather than duplicates"


# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------


def test_the_six_section_7_12_rights_statuses_are_complete_and_exact():
    assert RIGHTS_STATUSES == frozenset(
        {"unknown", "open", "licensed", "restricted", "prohibited", "expired"}
    )


def test_the_six_section_7_12_dispositions_are_complete_and_exact():
    assert RIGHTS_DISPOSITIONS == frozenset(
        {"display", "distribute", "transform", "compare_only", "metadata_only", "reject"}
    )


def test_the_two_vocabularies_reject_each_others_values():
    """Section 7.12 keeps status and disposition separate: `restricted` is a
    status, never a disposition, and `reject` is a disposition, never a
    status. Only `restricted` appears in both the deployed assessment
    vocabulary and section 7.12's, and it means a status in one and a
    disposition in neither."""
    assert RIGHTS_STATUSES.isdisjoint(RIGHTS_DISPOSITIONS)
    assert "restricted" in RIGHTS_STATUSES
    assert "restricted" not in RIGHTS_DISPOSITIONS
    assert DEPLOYED_ASSESSMENT_DISPOSITIONS & RIGHTS_STATUSES == {"restricted"}
    assert DEPLOYED_ASSESSMENT_DISPOSITIONS.isdisjoint(RIGHTS_DISPOSITIONS)


def test_the_dispositions_partition_into_permissive_and_not():
    assert PERMISSIVE_DISPOSITIONS | NON_PERMISSIVE_DISPOSITIONS == RIGHTS_DISPOSITIONS
    assert PERMISSIVE_DISPOSITIONS.isdisjoint(NON_PERMISSIVE_DISPOSITIONS)
    # `compare_only` is permissive: comparing two graphics means drawing both.
    assert "compare_only" in PERMISSIVE_DISPOSITIONS
    assert NON_PERMISSIVE_DISPOSITIONS == frozenset({"metadata_only", "reject"})


def test_the_permitting_statuses_exclude_every_unresolved_one():
    assert PERMITTING_RIGHTS_STATUSES == frozenset({"open", "licensed", "restricted"})
    assert RIGHTS_STATUSES - PERMITTING_RIGHTS_STATUSES == frozenset(
        {"unknown", "prohibited", "expired"}
    )
    assert LICENCE_BACKED_RIGHTS_STATUSES < PERMITTING_RIGHTS_STATUSES


def test_determination_method_is_a_sixth_distinct_vocabulary():
    """Six `method` vocabularies now exist and are deliberately not unified.
    Unifying them is a specification change, not an implementation tidy-up."""
    assert RIGHTS_DETERMINATION_METHODS == frozenset(
        {"manual", "licence_document", "ai_assisted"}
    )
    others = (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
    )
    for other in others:
        assert RIGHTS_DETERMINATION_METHODS != other
    # The value section 7.12's decision introduces exists in no other one.
    for other in others:
        assert "licence_document" not in other


def test_the_decision_status_vocabulary_says_approved_not_verified():
    """Section 7.12's own word is "approved", and section 13.1's Governance
    dimension reads "organisation approved | public approved". A rights
    disposition is a legal approval, not a verification of fact. This is the
    one deliberate divergence from the shared
    `proposed | verified | rejected | retired` shape, and it is recorded
    rather than accidental."""
    assert RIGHTS_DECISION_STATUSES == frozenset(
        {"proposed", "approved", "rejected", "retired"}
    )
    assert "verified" not in RIGHTS_DECISION_STATUSES
    assert LIVE_RIGHTS_DECISION_STATUSES == frozenset({"proposed", "approved"})
    # `proposed` is a status, never a role -- the rule SM-P0-04 settled.
    assert "proposed" in RIGHTS_DECISION_STATUSES
    assert "proposed" not in RIGHTS_DISPOSITIONS


def test_the_decision_lifecycle_covers_every_status_and_rejection_is_final():
    assert set(RIGHTS_DECISION_TRANSITIONS) == set(RIGHTS_DECISION_STATUSES)
    assert RIGHTS_DECISION_TRANSITIONS["rejected"] == frozenset()
    assert RIGHTS_DECISION_TRANSITIONS["retired"] == frozenset()
    assert RIGHTS_DECISION_TRANSITIONS["approved"] == frozenset({"retired"})
    for reachable in RIGHTS_DECISION_TRANSITIONS.values():
        assert reachable <= RIGHTS_DECISION_STATUSES
        assert "proposed" not in reachable


def test_an_ai_determination_can_never_approve_itself():
    """Section 8.4, and stricter than section 7.10: there is no
    controlled-system rights decision, because reading a licence is not
    deciding what SymGov may do under it."""
    assert NON_APPROVING_DETERMINATION_METHODS == frozenset({"ai_assisted"})
    assert NON_APPROVING_DETERMINATION_METHODS < RIGHTS_DETERMINATION_METHODS
    assert "licence_document" not in NON_APPROVING_DETERMINATION_METHODS


def test_the_licensed_acquisition_methods_now_have_something_to_point_at():
    """SM-P0-05 left `requires_licence_reference` a reporting predicate with
    no record behind it. It is wired into this package's reporting, read-only:
    the publication gate itself is SM-P0-08."""
    assert LICENSED_ACQUISITION_METHODS < PACKAGE_ACQUISITION_METHODS
    assert "requires_licence_reference" in _service_code()
    for forbidden in ("raise PermissionError", "publication_gate", "def gate_"):
        assert forbidden not in _service_code(), forbidden


# --------------------------------------------------------------------------
# Pure validators
# --------------------------------------------------------------------------


def test_the_disposition_basis_rule_lives_in_the_service_not_the_database():
    """Called out deliberately: the rule that a permissive disposition may only
    be *approved* on a status that supports it is real, and by decision it is
    service policy rather than a check constraint. Rights gating is SM-P0-08's,
    and a storage-level copy would need a migration to loosen. This test and
    the matrix below are that rule's only enforcement -- the same shape as
    `standard_sources.STANDARD_STATUSES`, which section 7.10 likewise leaves
    out of the database."""
    checks = _checks(RightsRecord)
    assert "approved_disposition_basis" not in checks
    assert "approved_disposition_basis" not in _ddl()
    # No constraint anywhere on this table pairs the two vocabularies.
    for name, expression in checks.items():
        pairs_them = "rights_status" in expression and "disposition" in expression
        assert not pairs_them, f"{name} pairs rights_status against disposition"
    # The rule itself is still applied, by the service.
    assert "disposition_is_permitted" in _service_statements()


@pytest.mark.parametrize(
    ("rights_status", "disposition"),
    [
        (status, disposition)
        for status in sorted(RIGHTS_STATUSES)
        for disposition in sorted(RIGHTS_DISPOSITIONS)
    ],
)
def test_the_permitted_pairs_are_exactly_the_permitting_statuses(rights_status, disposition):
    """A permissive disposition needs a status that supports it; the two
    non-permissive ones survive every status, so an orphan work can be
    recorded as `unknown` + `metadata_only`."""
    expected = (
        disposition in NON_PERMISSIVE_DISPOSITIONS
        or rights_status in PERMITTING_RIGHTS_STATUSES
    )
    assert disposition_is_permitted(rights_status, disposition) is expected


def test_an_unresolved_status_can_still_record_metadata_or_a_rejection():
    for status in ("unknown", "prohibited", "expired"):
        assert disposition_is_permitted(status, "metadata_only") is True
        assert disposition_is_permitted(status, "reject") is True
        for disposition in sorted(PERMISSIVE_DISPOSITIONS):
            assert disposition_is_permitted(status, disposition) is False


def test_licence_reference_normalizes_and_refuses_a_pasted_licence():
    assert normalize_licence_reference("  CONTRACT-2026-0031  ") == "CONTRACT-2026-0031"
    assert normalize_licence_reference(None) is None
    for value in ("", "   ", 7, "x" * 513):
        with pytest.raises(ValueError, match="licence reference"):
            normalize_licence_reference(value)


def test_decision_reason_normalizes_and_is_bounded():
    assert normalize_decision_reason("  reviewed the contract  ") == "reviewed the contract"
    assert normalize_decision_reason(None) is None
    for value in ("", "   ", 7, "x" * 2001):
        with pytest.raises(ValueError, match="reason"):
            normalize_decision_reason(value)


@pytest.mark.parametrize("value", [{}, {"contract": "2026-0031"}])
def test_rights_evidence_accepts_any_json_object(value):
    assert normalize_rights_evidence(value) == value


def test_rights_evidence_defaults_to_an_empty_object():
    assert normalize_rights_evidence(None) == {}


@pytest.mark.parametrize("value", ["{}", [], 7, True])
def test_rights_evidence_refuses_anything_that_is_not_an_object(value):
    with pytest.raises(ValueError, match="evidence"):
        normalize_rights_evidence(value)


# --------------------------------------------------------------------------
# Service argument validation, without a database
# --------------------------------------------------------------------------


def _propose(**overrides):
    arguments = {
        "session": None,
        "disposition": "metadata_only",
        "determination_method": "manual",
        "proposed_at": NOW,
        "source_package_id": uuid.uuid4(),
    }
    arguments.update(overrides)
    return rights_service.propose_rights_record(**arguments)


def test_proposing_refuses_an_invented_disposition():
    with pytest.raises(ValueError, match="disposition"):
        _propose(disposition="cleared")


def test_proposing_refuses_a_disposition_from_the_deployed_assessment_vocabulary():
    """The two vocabularies must not blur into each other."""
    for value in sorted(DEPLOYED_ASSESSMENT_DISPOSITIONS - RIGHTS_STATUSES):
        with pytest.raises(ValueError, match="disposition"):
            _propose(disposition=value)


def test_proposing_refuses_an_invented_rights_status():
    with pytest.raises(ValueError, match="rights status"):
        _propose(rights_status="unknown_warning")


def test_proposing_refuses_a_method_from_another_vocabulary():
    for value in ("source_mapping", "imported", "legacy_backfill", "import_manifest", "contributed"):
        with pytest.raises(ValueError, match="determination method"):
            _propose(determination_method=value)


def test_proposing_refuses_a_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        _propose(proposed_at=datetime(2026, 9, 10, 12, 0, 0))


def test_proposing_refuses_no_subject_and_refuses_two():
    with pytest.raises(ValueError, match="exactly one subject"):
        _propose(source_package_id=None)
    with pytest.raises(ValueError, match="exactly one subject"):
        _propose(standard_version_id=uuid.uuid4())
    with pytest.raises(ValueError, match="exactly one subject"):
        _propose(
            source_package_id=None,
            standard_version_id=uuid.uuid4(),
            symbol_revision_id=uuid.uuid4(),
        )


def test_proposing_refuses_a_pasted_licence_rather_than_a_reference():
    with pytest.raises(ValueError, match="licence reference"):
        _propose(licence_reference="x" * 513)


def test_proposing_a_permissive_disposition_is_allowed_before_the_decision():
    """Only *approving* one is constrained, so a reviewer can put "I believe
    we may distribute this" forward and have the evidence demanded at the
    decision rather than at the proposal."""
    assert disposition_is_permitted("unknown", "distribute") is False

    session = _StubSession()
    record = rights_service.propose_rights_record(
        session=session,
        disposition="distribute",
        determination_method="manual",
        proposed_at=NOW,
        source_package_id=uuid.uuid4(),
    )
    assert record.decision_status == "proposed"
    assert record.rights_status == "unknown"
    assert record.decided_by_user_id is None
    assert record.decided_at is None
    assert session.added == [record]


def test_transitioning_refuses_an_invented_status():
    with pytest.raises(ValueError, match="decision status"):
        rights_service.transition_rights_record(
            None, uuid.uuid4(), target_status="verified", occurred_at=NOW
        )


def test_transitioning_refuses_a_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        rights_service.transition_rights_record(
            None,
            uuid.uuid4(),
            target_status="approved",
            occurred_at=datetime(2026, 9, 10, 12, 0, 0),
        )


def _record_transformation(**overrides):
    arguments = {
        "session": _StubSession(),
        "symbol_revision_id": uuid.uuid4(),
        "tool_name": "svgtool",
        "tool_version": "1.2.3",
        "derived_asset_sha256": DIGEST,
        "performed_at": NOW,
        "source_asset_sha256": OTHER_DIGEST,
    }
    arguments.update(overrides)
    return rights_service.record_asset_transformation(**arguments)


def test_a_transformation_refuses_a_derived_asset_with_no_source():
    with pytest.raises(ValueError, match="must identify its source"):
        _record_transformation(source_asset_sha256=None)


def test_a_transformation_refuses_a_digest_that_is_not_sha256():
    for value in ("a" * 63, "g" * 64, "d41d8cd98f00b204e9800998ecf8427e", ""):
        with pytest.raises(ValueError, match="hash"):
            _record_transformation(derived_asset_sha256=value)
        with pytest.raises(ValueError, match="hash"):
            _record_transformation(source_asset_sha256=value)


def test_a_transformation_refuses_a_missing_tool_or_version():
    with pytest.raises(ValueError, match="tool name"):
        _record_transformation(tool_name="   ")
    with pytest.raises(ValueError, match="tool version"):
        _record_transformation(tool_version=None)
    with pytest.raises(ValueError, match="tool version"):
        _record_transformation(tool_version="x" * 65)


def test_a_transformation_refuses_a_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        _record_transformation(performed_at=datetime(2026, 9, 10, 12, 0, 0))
