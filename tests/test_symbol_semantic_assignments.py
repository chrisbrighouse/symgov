"""Contract cover for SM-P0-02: symbol-to-concept semantic assignments.

DB-free by design. The storage guarantees that matter here -- the one-verified-
primary-per-revision partial index, the review-decision check, the confidence
bounds -- are all server-side and are proven against a real PostgreSQL server in
`test_symbol_semantic_assignments_postgresql.py`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend.models import SymbolSemanticAssignment
from symgov_backend.symbol_semantic_assignments import (
    SEMANTIC_ASSIGNMENT_METHODS,
    SEMANTIC_ASSIGNMENT_ROLES,
    SEMANTIC_ASSIGNMENT_STATUSES,
    SEMANTIC_ASSIGNMENT_TRANSITIONS,
    normalize_semantic_assignment_confidence,
    normalize_semantic_assignment_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20260909_0048_symbol_semantic_assignments.py"
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


def test_0048_chains_from_the_semantic_concept_core():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0048"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0047"', migration)


def test_0048_creates_assignment_storage_with_both_parents():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        'op.create_table( "symbol_semantic_assignments"',
        'sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_symbol_revision_id")',
        'sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_semantic_concept_id")',
        'sa.Column("assignment_role", sa.Text(), nullable=False)',
        'sa.Column("method", sa.Text(), nullable=False)',
        'sa.Column("confidence", sa.Numeric(5, 4), nullable=True)',
        'sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("\'{}\'::jsonb"))',
    )


def test_0048_names_the_long_foreign_keys_explicitly():
    """The convention would generate 66- and 68-character names, which alembic
    refuses outright. Guarded generally by
    test_semantic_concept_core.py::test_every_migration_identifier_fits_the_postgresql_limit;
    asserted here so the intent is visible at the point of use."""
    migration = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_symbol_semantic_assignments_symbol_revision_id",
        "fk_symbol_semantic_assignments_semantic_concept_id",
    ):
        assert f'name="{name}"' in migration
        assert len(name) <= 63


def test_0048_constrains_one_verified_primary_per_revision():
    migration = MIGRATION.read_text(encoding="utf-8")
    _assert_fragments(
        migration,
        '"uq_symbol_semantic_assignments_verified_primary", "symbol_semantic_assignments", ["symbol_revision_id"], unique=True,',
        "postgresql_where=sa.text(\"assignment_role = 'primary' and status = 'verified'\")",
    )


def test_0048_is_purely_additive():
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration[migration.index("def upgrade()"):migration.index("def downgrade()")]
    for forbidden in ("op.alter_column(", "op.drop_column(", "op.drop_table(", "op.add_column("):
        assert forbidden not in upgrade, f"upgrade must stay additive: {forbidden}"


def test_0048_downgrade_reverses_every_created_object():
    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()"):]
    _assert_fragments(downgrade, 'op.drop_table("symbol_semantic_assignments")')
    for index_name in (
        "uq_symbol_semantic_assignments_verified_primary",
        "ix_symbol_semantic_assignments_revision_status",
        "ix_symbol_semantic_assignments_concept_status",
    ):
        assert index_name in downgrade
        assert downgrade.index(index_name) < downgrade.index('op.drop_table("symbol_semantic_assignments")')


# --------------------------------------------------------------------------
# ORM mapping parity
# --------------------------------------------------------------------------


def test_assignment_orm_mapping_exists():
    model = MODEL.read_text(encoding="utf-8")
    mapping = _class_source(model, "SymbolSemanticAssignment")
    _assert_fragments(
        mapping,
        '__tablename__ = "symbol_semantic_assignments"',
        "assignment_role: Mapped[str] = mapped_column(Text, nullable=False)",
        "method: Mapped[str] = mapped_column(Text, nullable=False)",
        "confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)",
    )


def test_assignment_orm_metadata_matches_migration_integrity():
    checks = {
        constraint.name: _compact(str(constraint.sqltext)).lower()
        for constraint in SymbolSemanticAssignment.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_symbol_semantic_assignments_assignment_role": (
            "assignment_role in ('primary', 'qualifier', 'component')"
        ),
        "ck_symbol_semantic_assignments_status": (
            "status in ('proposed', 'verified', 'rejected', 'retired')"
        ),
        "ck_symbol_semantic_assignments_method": (
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted')"
        ),
        "ck_symbol_semantic_assignments_confidence": (
            "confidence is null or (confidence >= 0 and confidence <= 1)"
        ),
        "ck_symbol_semantic_assignments_review_decision": (
            "status in ('proposed', 'retired') or reviewed_at is not null"
        ),
        "ck_symbol_semantic_assignments_evidence_json_object": (
            "jsonb_typeof(evidence_json) = 'object'"
        ),
    }


def test_verified_primary_index_is_partial_on_role_and_status():
    index = next(
        candidate
        for candidate in SymbolSemanticAssignment.__table__.indexes
        if candidate.name == "uq_symbol_semantic_assignments_verified_primary"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["symbol_revision_id"]
    assert _compact(str(index.dialect_options["postgresql"]["where"])).lower() == (
        "assignment_role = 'primary' and status = 'verified'"
    )


def test_assignment_model_is_exported():
    exports = EXPORTS.read_text(encoding="utf-8")
    assert re.search(r"\bSymbolSemanticAssignment,", exports)
    assert '"SymbolSemanticAssignment"' in exports


# --------------------------------------------------------------------------
# Controlled vocabularies and lifecycle
# --------------------------------------------------------------------------


def test_controlled_vocabularies_match_the_specification():
    assert SEMANTIC_ASSIGNMENT_ROLES == {"primary", "qualifier", "component"}
    assert SEMANTIC_ASSIGNMENT_STATUSES == {"proposed", "verified", "rejected", "retired"}
    assert SEMANTIC_ASSIGNMENT_METHODS == {"manual", "source_mapping", "rule", "ai_assisted"}


def test_lifecycle_covers_every_status_and_starts_only_at_proposed():
    assert set(SEMANTIC_ASSIGNMENT_TRANSITIONS) == SEMANTIC_ASSIGNMENT_STATUSES
    for status, targets in SEMANTIC_ASSIGNMENT_TRANSITIONS.items():
        assert targets <= SEMANTIC_ASSIGNMENT_STATUSES, status
    # Verification is reachable only from a proposal: principle P-07 keeps
    # machine output out of the verified record until a decision is taken.
    verifiers = {
        status
        for status, targets in SEMANTIC_ASSIGNMENT_TRANSITIONS.items()
        if "verified" in targets
    }
    assert verifiers == {"proposed"}


def test_rejected_and_retired_are_terminal():
    assert SEMANTIC_ASSIGNMENT_TRANSITIONS["rejected"] == frozenset()
    assert SEMANTIC_ASSIGNMENT_TRANSITIONS["retired"] == frozenset()


# --------------------------------------------------------------------------
# Confidence and evidence
# --------------------------------------------------------------------------


def test_confidence_accepts_the_closed_unit_interval():
    for value in (0, 1, 0.5, Decimal("0.9999")):
        assert normalize_semantic_assignment_confidence(value) is not None
    assert normalize_semantic_assignment_confidence(None) is None


@pytest.mark.parametrize("value", [-0.0001, 1.0001, -1, 2, True, "0.5", None if False else object()])
def test_confidence_rejects_values_outside_the_unit_interval_or_wrong_type(value):
    with pytest.raises(ValueError):
        normalize_semantic_assignment_confidence(value)


def test_confidence_is_not_a_governance_state():
    """Specification section 8.4: a 0.99 machine confidence stays proposed.

    Enforced structurally -- `propose_symbol_semantic_assignment` hard-codes
    status='proposed' and the transition map makes 'verified' reachable only by
    an explicit decision -- so no confidence value can shortcut review.
    """
    source = (
        ROOT / "backend" / "symgov_backend" / "symbol_semantic_assignments.py"
    ).read_text(encoding="utf-8")
    propose = source[source.index("def propose_symbol_semantic_assignment"):source.index("def transition_symbol_semantic_assignment")]
    assert 'status="proposed"' in propose
    assert "verified" not in propose, "proposal must never set a verified status"


def test_evidence_must_be_an_object_when_present():
    assert normalize_semantic_assignment_evidence(None) == {}
    assert normalize_semantic_assignment_evidence({"source": "x"}) == {"source": "x"}
    for value in ([], "x", 7, [{"source": "x"}]):
        with pytest.raises(ValueError):
            normalize_semantic_assignment_evidence(value)
