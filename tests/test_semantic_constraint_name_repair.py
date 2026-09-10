"""Contract cover for the semantic-model constraint-name repair (20260909_0050).

Scope note: this file is deliberately DB-free. It pins what the migration
claims to do and, more usefully, adds the source-level guard that would have
caught the original defect: no migration may pass an already-prefixed name to
`sa.CheckConstraint`, because `NAMING_CONVENTION` prefixes it a second time and
SQLAlchemy then truncates the result silently, so the over-long name never
reaches PostgreSQL and the existing length guard cannot see it.

Whether the rename actually lands, and whether the renamed constraints still
reject the rows they were written to reject, is proven in
`test_semantic_constraint_name_repair_postgresql.py`.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from symgov_backend.models import (
    SemanticConcept,
    SemanticConceptRevision,
    SymbolSemanticAssignment,
)

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "backend" / "alembic" / "versions"
MIGRATION = VERSIONS / "20260909_0050_repair_semantic_constraint_names.py"

REPAIRED_MODELS = (SemanticConcept, SemanticConceptRevision, SymbolSemanticAssignment)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0050", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _orm_check_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


# --------------------------------------------------------------------------
# Migration contract
# --------------------------------------------------------------------------


def test_0050_chains_from_the_external_scheme_head():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'revision(?:\s*:\s*str)?\s*=\s*"20260909_0050"', migration)
    assert re.search(r'down_revision(?:\s*:\s*[^=]+)?\s*=\s*"20260909_0049"', migration)


def test_0050_changes_nothing_but_constraint_names():
    """A rename repair that also altered a column or an expression would be a
    schema change wearing a repair's name."""
    migration = MIGRATION.read_text(encoding="utf-8")
    body = migration[migration.index("def upgrade()"):]
    for forbidden in (
        "op.create_table(",
        "op.drop_table(",
        "op.add_column(",
        "op.drop_column(",
        "op.alter_column(",
        "op.create_index(",
        "op.drop_index(",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
    ):
        assert forbidden not in body, f"a name repair must not do this: {forbidden}"
    assert "RENAME CONSTRAINT" in migration


def test_0050_targets_exactly_the_orm_names_for_the_drifted_tables():
    """The point of the migration: after it runs the database agrees with
    `schema.py`. If a model gains or renames a check constraint without the
    repair list following, this fails."""
    repairs = _load_migration()._REPAIRS
    targeted: dict[str, set[str]] = {}
    for table, _marker, target in repairs:
        targeted.setdefault(table, set()).add(target)

    expected = {model.__tablename__: _orm_check_names(model) for model in REPAIRED_MODELS}
    assert targeted == expected


def test_0050_markers_are_unique_within_each_table():
    """Two constraints on one table matched by the same marker would make the
    rename ambiguous. The migration's `INTO STRICT` aborts if that happens at
    run time; this catches it at review time."""
    repairs = _load_migration()._REPAIRS
    seen: dict[tuple[str, str], int] = {}
    for table, marker, _target in repairs:
        seen[(table, marker)] = seen.get((table, marker), 0) + 1
    assert [key for key, count in seen.items() if count > 1] == []


def test_0050_locates_constraints_by_definition_not_by_current_name():
    """Seven of the sixteen current names carry a truncation hash, which only
    exists because the name was too long. Matching on the definition keeps the
    migration working against a database built from the ORM metadata too."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "pg_get_constraintdef" in migration
    assert "INTO STRICT" in migration
    # No hash-suffixed name should be written into the migration by hand.
    assert not re.search(r'"ck_\w+_ck_\w+"', migration)


def test_0050_downgrade_is_an_intentional_no_op():
    """The names it replaced were defects, so restoring them would mean
    writing SQLAlchemy's truncation hashes into a migration by hand."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    downgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    assert len(downgrade.body) == 1
    only = downgrade.body[0]
    assert isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant)
    assert "no-op" in only.value.value.lower()


# --------------------------------------------------------------------------
# Keeping the semantic model from drifting again
# --------------------------------------------------------------------------


def test_semantic_model_migrations_from_0049_pass_bare_constraint_names():
    """`NAMING_CONVENTION` renders `ck` as `ck_%(table_name)s_%(constraint_name)s`,
    so a name that already starts with `ck_` is prefixed twice and then
    truncated with a hash if the result passes 63 characters. PostgreSQL never
    sees an over-long name, so the length guard in
    `test_semantic_concept_core.py` cannot detect it -- the truncated name
    exists only in the database.

    Scope note: this checks the semantic-model migrations only. Passing a
    pre-prefixed name is a long-standing house pattern across this repository,
    and most of those tables carry the same prefix in `schema.py`, so the two
    sides agree and nothing is broken. 20260909_0047 and 20260909_0048 are the
    ones where the ORM went bare and the migration did not; 20260909_0050
    repairs the result, and this guard keeps the next semantic-model migration
    from repeating it.
    """
    semantic_migrations = sorted(
        path
        for path in VERSIONS.glob("*.py")
        if path.name >= "20260909_0049"
    )
    assert semantic_migrations, "expected at least the SM-P0-03 migration"

    offenders: list[tuple[str, str]] = []
    for path in semantic_migrations:
        source = path.read_text(encoding="utf-8")
        for block in re.finditer(r"sa\.CheckConstraint\((.*?)\n(\s*)\)", source, re.DOTALL):
            name = re.search(r'name\s*=\s*"([^"]+)"', block.group(1))
            if name and name.group(1).startswith("ck_"):
                offenders.append((path.name, name.group(1)))
    assert offenders == [], (
        "check constraint names must be bare -- the naming convention adds the "
        f"ck_<table>_ prefix: {offenders}"
    )


@pytest.mark.parametrize("model", REPAIRED_MODELS, ids=lambda m: m.__tablename__)
def test_repaired_orm_names_are_singly_prefixed_and_fit_the_limit(model):
    table = model.__tablename__
    for name in _orm_check_names(model):
        assert name.startswith(f"ck_{table}_")
        assert not name.startswith(f"ck_{table}_ck_")
        assert len(name) <= 63
