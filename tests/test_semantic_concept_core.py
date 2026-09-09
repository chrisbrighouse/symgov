"""Contract cover for SM-P0-01: the governed semantic concept layer.

Scope note: this file is deliberately DB-free. It pins the migration/ORM
storage contract and the pure validation helpers. Behaviour that only a real
PostgreSQL server can prove -- sequence allocation, the unique concept_code
index, check constraints actually rejecting rows, and downgrade -- lives in
`test_semantic_concept_core_postgresql.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend import semantic_concepts as semantic_concepts_service
from symgov_backend.models import SemanticConcept, SemanticConceptRevision
from symgov_backend.semantic_concepts import (
    SEMANTIC_CONCEPT_CODE_MAX_SEQUENCE_VALUE,
    SEMANTIC_CONCEPT_CODE_PATTERN,
    SEMANTIC_CONCEPT_KINDS,
    SEMANTIC_CONCEPT_REVISION_STATES,
    SEMANTIC_CONCEPT_REVISION_TRANSITIONS,
    SEMANTIC_CONCEPT_STATUSES,
    format_allocated_semantic_concept_code,
    normalize_semantic_concept_aliases,
    normalize_semantic_concept_code,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260909_0047_semantic_concept_core.py"
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


# --------------------------------------------------------------------------
# Migration storage contract
# --------------------------------------------------------------------------


def test_0047_chains_from_the_previous_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0047"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260908_0046"', migration)


def test_0047_creates_semantic_concept_identity_storage():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "semantic_concepts"',
        'sa.Column("concept_code", sa.Text(), nullable=False)',
        'sa.Column("concept_kind", sa.Text(), nullable=False)',
        'sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("\'draft\'"))',
        'sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True)',
        'sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)',
        "\"concept_code ~ '^SGC-[0-9]{8}$'\"",
        'op.create_index("uq_semantic_concepts_concept_code", "semantic_concepts", ["concept_code"], unique=True)',
    )


def test_0047_creates_semantic_concept_revision_storage():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "semantic_concept_revisions"',
        'sa.Column("concept_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT"), nullable=False)',
        'sa.Column("preferred_name", sa.Text(), nullable=False)',
        'sa.Column("definition", sa.Text(), nullable=False)',
        'sa.Column("aliases_json", postgresql.JSONB(), nullable=False, server_default=sa.text("\'[]\'::jsonb"))',
        'sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)',
        '"uq_semantic_concept_revisions_concept_revision_label", "semantic_concept_revisions", ["concept_id", "revision_label"], unique=True,',
    )


def test_0047_defers_the_circular_current_revision_foreign_key():
    """semantic_concepts.current_revision_id can only be constrained once the
    revisions table exists, mirroring governed_symbols.current_revision_id."""
    migration = MIGRATION.read_text(encoding="utf-8")
    create_concepts = migration.index('op.create_table(\n        "semantic_concepts"')
    create_revisions = migration.index('op.create_table(\n        "semantic_concept_revisions"')
    add_foreign_key = migration.index("fk_semantic_concepts_current_revision_id")
    assert create_concepts < create_revisions < add_foreign_key

    concepts_block = migration[create_concepts:create_revisions]
    assert "sa.ForeignKey(\"semantic_concept_revisions" not in concepts_block


def test_0047_caps_the_sequence_at_the_concept_code_grammar():
    """A sequence value above 99999999 would produce a code the check
    constraint rejects, so the sequence must refuse to issue one."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        "CREATE SEQUENCE semantic_concept_code_seq START 1 MAXVALUE 99999999 NO CYCLE",
    )
    assert SEMANTIC_CONCEPT_CODE_MAX_SEQUENCE_VALUE == 99_999_999


def test_0047_is_purely_additive():
    """SM-P0-01 must not touch any pre-existing table, or the backward
    compatibility promise in specification section 12 is broken."""
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    for forbidden in ("op.alter_column(", "op.drop_column(", "op.drop_table(", "op.execute(\n        \"\"\"\n        UPDATE"):
        assert forbidden not in upgrade, f"upgrade must stay additive: {forbidden}"
    for touched in re.findall(r'op\.add_column\(\s*"([a-z_]+)"', upgrade):
        pytest.fail(f"upgrade adds a column to the pre-existing table {touched}")


def test_0047_downgrade_reverses_every_created_object():
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()"):]
    _assert_fragments(
        downgrade,
        "DROP SEQUENCE semantic_concept_code_seq",
        'op.drop_table("semantic_concept_revisions")',
        'op.drop_table("semantic_concepts")',
    )
    # The dependent table and the circular FK must go before the parent table.
    assert downgrade.index("fk_semantic_concepts_current_revision_id") < downgrade.index(
        'op.drop_table("semantic_concept_revisions")'
    )
    assert downgrade.index('op.drop_table("semantic_concept_revisions")') < downgrade.index(
        'op.drop_table("semantic_concepts")'
    )


def test_every_migration_identifier_fits_the_postgresql_limit():
    """PostgreSQL truncates identifiers at 63 characters.

    SM-P0-01 was first written with a 67-character convention-generated foreign
    key name, and `alembic upgrade` failed outright. The remaining semantic-model
    work packages introduce longer table names still
    (symbol_revision_classification_assignments, concept_external_references),
    so this guard covers every migration rather than only this one.
    """
    # Pre-dates this guard. Note the two different failure modes:
    #
    #   * An explicit name passed to op.create_foreign_key / op.create_index is
    #     validated, and alembic raises IdentifierError outright. That is what
    #     SM-P0-01 hit, and it is what this guard is for.
    #   * A name passed to sa.CheckConstraint inside op.create_table is instead
    #     silently truncated by SQLAlchemy with a 4-character hash suffix. The
    #     entry below took that path, so it applied cleanly and is deployed as
    #     `ck_product_usage_events_ck_product_usage_events_favouri_5b87` -- also
    #     double-prefixed, because the name already carried the `ck_<table>_`
    #     that the naming convention prepends.
    #
    # It is left alone deliberately: renaming it would be cosmetic churn against
    # a deployed constraint, and the same double-prefixing runs through all nine
    # check constraints on that table, so it is a pattern to stop repeating
    # rather than a defect to retrofit. New models should pass a bare constraint
    # name (e.g. name="status") and let the convention add the prefix.
    known_pre_existing = {
        "ck_product_usage_events_favourite_action_only_on_favorite_changed",
    }
    pattern = re.compile(
        r'(?:op\.create_index\(\s*|op\.create_foreign_key\(\s*'
        r'|op\.create_unique_constraint\(\s*|op\.create_check_constraint\(\s*'
        r'|name\s*=\s*)"([A-Za-z_][A-Za-z0-9_]*)"'
    )
    versions = ROOT / "backend" / "alembic" / "versions"
    over_limit = sorted(
        {
            (path.name, identifier)
            for path in versions.glob("*.py")
            for identifier in pattern.findall(path.read_text(encoding="utf-8"))
            if len(identifier) > 63 and identifier not in known_pre_existing
        }
    )
    assert over_limit == [], f"migration identifiers exceed 63 characters: {over_limit}"


# --------------------------------------------------------------------------
# ORM mapping parity
# --------------------------------------------------------------------------


def test_semantic_concept_orm_mapping_exists():
    model = MODEL.read_text(encoding="utf-8")
    mapping = _class_source(model, "SemanticConcept")
    _assert_fragments(
        mapping,
        '__tablename__ = "semantic_concepts"',
        "concept_code: Mapped[str] = mapped_column(Text, nullable=False)",
        "concept_kind: Mapped[str] = mapped_column(Text, nullable=False)",
        'current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("semantic_concept_revisions.id", name="fk_semantic_concepts_current_revision_id"), nullable=True)',
    )


def test_semantic_concept_revision_orm_mapping_exists():
    model = MODEL.read_text(encoding="utf-8")
    mapping = _class_source(model, "SemanticConceptRevision")
    _assert_fragments(
        mapping,
        '__tablename__ = "semantic_concept_revisions"',
        'concept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("semantic_concepts.id", ondelete="RESTRICT"), nullable=False)',
        "preferred_name: Mapped[str] = mapped_column(Text, nullable=False)",
        "definition: Mapped[str] = mapped_column(Text, nullable=False)",
    )


def test_semantic_concept_orm_metadata_matches_migration_integrity():
    checks = {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in SemanticConcept.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_semantic_concepts_concept_code_grammar": "concept_code ~ '^sgc-[0-9]{8}$'",
        "ck_semantic_concepts_concept_kind": (
            "concept_kind in ('physical_equipment', 'function', 'property', 'state', "
            "'action', 'annotation', 'connection', 'safety_function', 'other')"
        ),
        "ck_semantic_concepts_status": "status in ('draft', 'active', 'deprecated', 'withdrawn')",
    }

    code_index = next(
        index
        for index in SemanticConcept.__table__.indexes
        if index.name == "uq_semantic_concepts_concept_code"
    )
    assert code_index.unique is True
    assert [column.name for column in code_index.columns] == ["concept_code"]


def test_semantic_concept_revision_orm_metadata_matches_migration_integrity():
    checks = {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in SemanticConceptRevision.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_semantic_concept_revisions_lifecycle_state": (
            "lifecycle_state in ('draft', 'review', 'approved', 'published', "
            "'deprecated', 'withdrawn')"
        ),
        "ck_semantic_concept_revisions_revision_label": (
            "btrim(revision_label) <> '' and char_length(revision_label) <= 64"
        ),
        "ck_semantic_concept_revisions_preferred_name": (
            "btrim(preferred_name) <> '' and char_length(preferred_name) <= 256"
        ),
        "ck_semantic_concept_revisions_definition": (
            "btrim(definition) <> '' and char_length(definition) <= 8000"
        ),
        "ck_semantic_concept_revisions_aliases_json_array": "jsonb_typeof(aliases_json) = 'array'",
        "ck_semantic_concept_revisions_notes": (
            "notes is null or (btrim(notes) <> '' and char_length(notes) <= 4000)"
        ),
        "ck_semantic_concept_revisions_rationale": (
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)"
        ),
    }

    label_index = next(
        index
        for index in SemanticConceptRevision.__table__.indexes
        if index.name == "uq_semantic_concept_revisions_concept_revision_label"
    )
    assert label_index.unique is True
    assert [column.name for column in label_index.columns] == ["concept_id", "revision_label"]


def test_semantic_concept_models_are_exported():
    exports = EXPORTS.read_text(encoding="utf-8")
    for name in ("SemanticConcept", "SemanticConceptRevision"):
        assert re.search(rf"\b{name},", exports), f"{name} missing from model imports"
        assert f'"{name}"' in exports, f"{name} missing from __all__"


# --------------------------------------------------------------------------
# Concept code grammar
# --------------------------------------------------------------------------


def test_concept_code_format_is_zero_padded_to_eight_digits():
    assert format_allocated_semantic_concept_code(1) == "SGC-00000001"
    assert format_allocated_semantic_concept_code(1284) == "SGC-00001284"
    assert format_allocated_semantic_concept_code(99_999_999) == "SGC-99999999"


def test_every_formatted_concept_code_satisfies_the_stored_grammar():
    for sequence_value in (1, 2, 99, 1284, 12_345_678, 99_999_999):
        code = format_allocated_semantic_concept_code(sequence_value)
        assert SEMANTIC_CONCEPT_CODE_PATTERN.fullmatch(code)


@pytest.mark.parametrize("sequence_value", [0, -1, 100_000_000, True, "12", 1.0, None])
def test_concept_code_format_rejects_unusable_sequence_values(sequence_value):
    with pytest.raises(ValueError):
        format_allocated_semantic_concept_code(sequence_value)


def test_concept_code_normalization_upcases_and_validates():
    assert normalize_semantic_concept_code("sgc-00001284") == "SGC-00001284"
    assert normalize_semantic_concept_code("SGC-00001284") == "SGC-00001284"


@pytest.mark.parametrize(
    "value",
    [
        " SGC-00001284",
        "SGC-00001284 ",
        "SGC-1284",
        "SGC-000012840",
        "SG-00001284",
        "SGC00001284",
        "SGC-0000128A",
        "SGC-٠٠٠٠١٢٨٤",
        "",
        None,
        12_84,
    ],
)
def test_concept_code_normalization_rejects_invalid_codes(value):
    with pytest.raises(ValueError):
        normalize_semantic_concept_code(value)


# --------------------------------------------------------------------------
# Alias normalization
# --------------------------------------------------------------------------


def test_aliases_default_to_an_empty_list():
    assert normalize_semantic_concept_aliases(None) == []
    assert normalize_semantic_concept_aliases([]) == []


def test_aliases_are_trimmed_and_case_insensitively_deduplicated():
    assert normalize_semantic_concept_aliases(
        ["Gate valve", "  sluice valve  ", "GATE VALVE", "gate valve"]
    ) == ["Gate valve", "sluice valve"]


def test_alias_list_rejects_non_list_and_blank_entries():
    for value in ("Gate valve", {"alias": "x"}, 7):
        with pytest.raises(ValueError):
            normalize_semantic_concept_aliases(value)
    for value in ([""], ["   "], [None], [["nested"]]):
        with pytest.raises(ValueError):
            normalize_semantic_concept_aliases(value)


def test_alias_list_is_bounded():
    with pytest.raises(ValueError):
        normalize_semantic_concept_aliases([f"alias-{index}" for index in range(65)])
    assert len(normalize_semantic_concept_aliases([f"alias-{index}" for index in range(64)])) == 64


# --------------------------------------------------------------------------
# Controlled vocabularies and lifecycle
# --------------------------------------------------------------------------


def test_controlled_vocabularies_match_the_specification():
    assert SEMANTIC_CONCEPT_KINDS == {
        "physical_equipment",
        "function",
        "property",
        "state",
        "action",
        "annotation",
        "connection",
        "safety_function",
        "other",
    }
    assert SEMANTIC_CONCEPT_STATUSES == {"draft", "active", "deprecated", "withdrawn"}
    assert SEMANTIC_CONCEPT_REVISION_STATES == {
        "draft",
        "review",
        "approved",
        "published",
        "deprecated",
        "withdrawn",
    }


def test_lifecycle_transition_map_covers_every_state_and_invents_none():
    assert set(SEMANTIC_CONCEPT_REVISION_TRANSITIONS) == SEMANTIC_CONCEPT_REVISION_STATES
    for state, targets in SEMANTIC_CONCEPT_REVISION_TRANSITIONS.items():
        assert targets <= SEMANTIC_CONCEPT_REVISION_STATES, state


def test_withdrawn_is_terminal_and_publication_requires_approval():
    assert SEMANTIC_CONCEPT_REVISION_TRANSITIONS["withdrawn"] == frozenset()
    # 'published' is reachable only from 'approved': nothing may skip review.
    publishers = {
        state
        for state, targets in SEMANTIC_CONCEPT_REVISION_TRANSITIONS.items()
        if "published" in targets
    }
    assert publishers == {"approved"}
    approvers = {
        state
        for state, targets in SEMANTIC_CONCEPT_REVISION_TRANSITIONS.items()
        if "approved" in targets
    }
    assert approvers == {"review"}


def test_service_rejects_naive_timestamps_and_placeholder_actors():
    naive = datetime(2026, 9, 9, 12, 0, 0)
    aware = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        semantic_concepts_service._require_aware_timestamp(naive, "timestamp")
    assert semantic_concepts_service._require_aware_timestamp(aware, "timestamp") is aware
    with pytest.raises(ValueError):
        semantic_concepts_service._require_actor(uuid.UUID(int=0), "actor")
    with pytest.raises(ValueError):
        semantic_concepts_service._require_actor("not-a-uuid", "actor")
