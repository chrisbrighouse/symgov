"""Contract cover for SM-P1-03 WP3.2: governed semantic concept relationships.

Scope note: this file is deliberately DB-free, following the SM-P0-01..-04
pattern. It pins the migration/ORM storage contract, the section 7.3
vocabularies, and the pure validation helpers. Behaviour only a real PostgreSQL
server can prove -- the check constraints actually rejecting rows, the partial
unique index, the review state machine against stored rows, and downgrade --
lives in `test_concept_relationships_postgresql.py`.

The one thing worth reading twice is `test_no_inverse_is_derived_anywhere`:
section 7.3 ships both directions of each pair, and deriving one from the other
would put an assertion nobody reviewed into the record.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend import concept_relationships as service
from symgov_backend.classification_mapping import MAPPING_GAP_REASONS
from symgov_backend.models import SemanticConceptRelationship
from symgov_backend.concept_relationships import (
    CONCEPT_RELATIONSHIP_METHODS,
    CONCEPT_RELATIONSHIP_STATUSES,
    CONCEPT_RELATIONSHIP_TRANSITIONS,
    CONCEPT_RELATIONSHIP_TYPES,
    normalize_relationship_confidence,
    normalize_relationship_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "backend" / "alembic" / "versions"
    / "20260917_0060_semantic_concept_relationships.py"
)
MODEL = ROOT / "backend" / "symgov_backend" / "models" / "schema.py"
EXPORTS = ROOT / "backend" / "symgov_backend" / "models" / "__init__.py"
SERVICE = ROOT / "backend" / "symgov_backend" / "concept_relationships.py"

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


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


def test_0060_chains_from_the_previous_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260917_0060"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260916_0059"', migration)


def test_0060_creates_the_section_7_3_table():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "semantic_concept_relationships"',
        'sa.Column("relationship_type", sa.Text(), nullable=False)',
        'sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("\'proposed\'"))',
        'sa.Column("method", sa.Text(), nullable=False)',
        'sa.Column("confidence", sa.Numeric(5, 4), nullable=True)',
        'sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("\'{}\'::jsonb"))',
        'sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)',
        'sa.PrimaryKeyConstraint("id", name="pk_semantic_concept_relationships")',
    )


def test_0060_restricts_deleting_either_end_and_keeps_the_reviewers_nullable():
    """A concept an assertion names is not deletable out from under it; a
    deleted *user* leaves the assertion standing but unattributed."""
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_semantic_concept_relationships_source_concept_id")',
        'sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_semantic_concept_relationships_target_concept_id")',
        'sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_semantic_concept_relationships_proposed_by_user_id")',
        'sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_semantic_concept_relationships_reviewed_by_user_id")',
    )


def test_0060_indexes_both_ends_and_one_live_assertion_per_triple():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_index( "ix_semantic_concept_relationships_source_status", "semantic_concept_relationships", ["source_concept_id", "status"],',
        'op.create_index( "ix_semantic_concept_relationships_target_status", "semantic_concept_relationships", ["target_concept_id", "status"],',
        'op.create_index( "uq_semantic_concept_relationships_active", "semantic_concept_relationships", ["source_concept_id", "target_concept_id", "relationship_type"], unique=True, postgresql_where=sa.text("status in (\'proposed\', \'verified\')"),',
    )


def test_0060_downgrade_drops_the_table_and_its_indexes():
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()"):]
    _assert_fragments(
        downgrade,
        'op.drop_index( "uq_semantic_concept_relationships_active", table_name="semantic_concept_relationships",',
        'op.drop_table("semantic_concept_relationships")',
    )


def test_0060_issues_no_grant():
    """SM-P0-04..-08 precedent, and section 4.4 item 10: the narrow role reaches
    these tables through ownership, so a per-table GRANT would be new privilege
    nobody asked for."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r"GRANT\s+\w+\s+ON", migration, re.IGNORECASE) is None
    assert "op.execute" not in migration


def test_0060_passes_bare_check_constraint_names():
    """NAMING_CONVENTION prefixes `ck_<table>_`; a pre-prefixed name is prefixed
    twice and hash-truncated where no length guard can see it."""
    migration = MIGRATION.read_text(encoding="utf-8")
    names = re.findall(r'sa\.CheckConstraint\(.*?name="([^"]+)"', migration, re.DOTALL)
    assert names, "expected the section 7.3 check constraints"
    assert [name for name in names if name.startswith("ck_")] == []


# --------------------------------------------------------------------------
# ORM contract
# --------------------------------------------------------------------------


def test_the_model_is_exported():
    assert "SemanticConceptRelationship" in EXPORTS.read_text(encoding="utf-8")
    assert SemanticConceptRelationship.__tablename__ == "semantic_concept_relationships"


def test_model_and_migration_agree_on_the_governed_columns():
    columns = {column.name for column in SemanticConceptRelationship.__table__.columns}
    assert columns == {
        "id",
        "source_concept_id",
        "target_concept_id",
        "relationship_type",
        "status",
        "method",
        "confidence",
        "evidence_json",
        "proposed_by_user_id",
        "created_at",
        "updated_at",
        "reviewed_by_user_id",
        "reviewed_at",
    }


def test_orm_check_names_are_singly_prefixed_and_fit_the_limit():
    table = SemanticConceptRelationship.__tablename__
    names = {
        constraint.name
        for constraint in SemanticConceptRelationship.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert names, "expected the section 7.3 check constraints on the ORM model"
    for name in names:
        assert name.startswith(f"ck_{table}_")
        assert not name.startswith(f"ck_{table}_ck_")
        assert len(name) <= 63


def test_every_explicit_identifier_fits_postgresqls_limit():
    table = SemanticConceptRelationship.__table__
    names = [constraint.name for constraint in table.constraints if constraint.name]
    names += [index.name for index in table.indexes]
    names += [
        key.name
        for column in table.columns
        for key in column.foreign_keys
        if key.constraint is not None and key.constraint.name
    ]
    over_long = [name for name in names if len(str(name)) > 63]
    assert over_long == []


def test_the_model_refuses_a_self_relationship_in_storage():
    source = _class_source(MODEL.read_text(encoding="utf-8"), "SemanticConceptRelationship")
    _assert_fragments(
        source,
        'CheckConstraint( "source_concept_id <> target_concept_id", name="distinct_concepts",',
    )


# --------------------------------------------------------------------------
# Section 7.3 vocabularies
# --------------------------------------------------------------------------


def test_the_relationship_vocabulary_is_section_7_3s_entire():
    assert CONCEPT_RELATIONSHIP_TYPES == {
        "broader",
        "narrower",
        "related",
        "component_of",
        "has_component",
        "function_of",
        "has_function",
        "equivalent_internal",
    }


def test_the_statuses_are_the_governed_four():
    assert CONCEPT_RELATIONSHIP_STATUSES == {"proposed", "verified", "rejected", "retired"}


def test_legacy_backfill_is_not_a_relationship_method():
    """Section 12.1 phase M2 backfills classifications. There is no relationship
    backfill, so carrying the value would create a vocabulary with no writer."""
    assert CONCEPT_RELATIONSHIP_METHODS == {"manual", "source_mapping", "rule", "ai_assisted"}
    assert "legacy_backfill" not in CONCEPT_RELATIONSHIP_METHODS


def test_rejected_and_retired_are_terminal():
    assert CONCEPT_RELATIONSHIP_TRANSITIONS["rejected"] == frozenset()
    assert CONCEPT_RELATIONSHIP_TRANSITIONS["retired"] == frozenset()
    assert CONCEPT_RELATIONSHIP_TRANSITIONS["verified"] == {"retired"}
    assert set(CONCEPT_RELATIONSHIP_TRANSITIONS) == CONCEPT_RELATIONSHIP_STATUSES


def test_the_vocabulary_matches_the_check_constraint_in_the_migration():
    migration = MIGRATION.read_text(encoding="utf-8")
    for value in CONCEPT_RELATIONSHIP_TYPES:
        assert f"'{value}'" in migration
    for value in CONCEPT_RELATIONSHIP_METHODS:
        assert f"'{value}'" in migration
    assert "'legacy_backfill'" not in migration


def test_no_inverse_is_derived_anywhere():
    """Section 7.3 ships both directions of each pair, so a stored row is one
    directed assertion. If a future change mints `narrower(B, A)` from
    `broader(A, B)`, an assertion nobody reviewed reaches the record and
    principle P-07 is broken quietly. There is no inverse map in this module,
    and that absence is the contract."""
    exported = {name for name in dir(service) if not name.startswith("_")}
    assert not [name for name in exported if "inverse" in name.lower()]

    # `narrower` may appear exactly once in the module's code: as a member of
    # the section 7.3 vocabulary. A second occurrence means something reads or
    # writes the inverse of a stored direction.
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in [tree, *ast.walk(tree)]
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    assert literals.count("narrower") == 1
    assert literals.count("has_component") == 1


# --------------------------------------------------------------------------
# Pure validation helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 0.5, Decimal("0.25")])
def test_confidence_accepts_the_closed_unit_interval(value):
    assert normalize_relationship_confidence(value) == Decimal(str(value))


def test_confidence_is_optional():
    assert normalize_relationship_confidence(None) is None


@pytest.mark.parametrize("value", [-0.1, 1.1, "0.5", True])
def test_confidence_refuses_anything_else(value):
    with pytest.raises(ValueError):
        normalize_relationship_confidence(value)


def test_evidence_defaults_to_an_empty_object():
    assert normalize_relationship_evidence(None) == {}


@pytest.mark.parametrize("value", [[], "note", 3])
def test_evidence_must_be_an_object(value):
    with pytest.raises(ValueError):
        normalize_relationship_evidence(value)


def test_a_naive_timestamp_is_refused():
    with pytest.raises(ValueError):
        service._require_aware_timestamp(datetime(2026, 9, 17, 12, 0, 0), "proposal time")


def test_the_nil_uuid_is_not_an_actor():
    with pytest.raises(ValueError):
        service._require_optional_actor(uuid.UUID(int=0), "proposer")
    assert service._require_optional_actor(None, "proposer") is None


def test_broader_walk_refuses_a_nonsense_depth():
    with pytest.raises(ValueError):
        service.broader_concept_ids(None, uuid.uuid4(), max_depth=0)
    with pytest.raises(ValueError):
        service.broader_concept_ids(None, uuid.uuid4(), max_depth=True)


# --------------------------------------------------------------------------
# The mapping gap this package closes
# --------------------------------------------------------------------------


def test_no_relationship_table_has_left_the_gap_vocabulary():
    """The table exists now, so the reason would be a false statement. What
    remains true is that there is no concept to point at until CFIHOS is
    imported (SM-P2-02), and `no_concept_target` says exactly that."""
    assert "no_relationship_table" not in MAPPING_GAP_REASONS
    assert "no_concept_target" in MAPPING_GAP_REASONS
