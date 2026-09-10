"""Rehearsal for the semantic-model constraint-name repair against real PostgreSQL.

The defect this migration fixes was invisible to source-level checks: passing
an already-prefixed name to `sa.CheckConstraint` gets it prefixed a second time
by `NAMING_CONVENTION` and then silently truncated with a hash, so the wrong
name exists only in the database. Only a real server can show it, and only a
real server can show it has gone.

This file also carries the guard that would have caught it in the first place:
for every mapped table, the check-constraint names PostgreSQL holds must match
the names SQLAlchemy would emit from `schema.py`.

Redaction: this file never prints the disposable container's connection string.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.models import (  # noqa: E402
    SemanticConcept,
    SemanticConceptRevision,
    SymbolSemanticAssignment,
)
from symgov_backend.models.base import Base  # noqa: E402

REPAIR_REVISION = "20260909_0050"
PREVIOUS_REVISION = "20260909_0049"

REPAIRED_MODELS = (SemanticConcept, SemanticConceptRevision, SymbolSemanticAssignment)
REPAIRED_TABLES = frozenset(model.__tablename__ for model in REPAIRED_MODELS)

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

# Tables whose check-constraint names disagreed with `schema.py` before this
# work started, and which this migration deliberately does not touch. Each is a
# separate decision: some are deployed, none are part of the semantic model.
# Recorded rather than fixed so the guard below can be exact instead of
# approximate -- shrinking this set is the point, growing it is a regression.
PRE_EXISTING_NAME_DRIFT = frozenset(
    {
        "catalog_symbol_identifiers",
        "external_identities",
        "governed_symbols",
        "organization_symbol_review_decisions",
        "organization_symbol_review_submissions",
        "projects",
        "provenance_assessments",
        "symbol_set_items",
        "symbol_sets",
        "user_sessions",
    }
)


def _expected_names(table) -> set[str]:
    """The names SQLAlchemy would actually emit, truncation included."""
    preparer = postgresql.dialect().identifier_preparer
    return {
        preparer.format_constraint(constraint, _alembic_quote=False)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _database_names(connection) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for table_name, constraint_name in connection.execute(
        text(
            "SELECT r.relname, c.conname FROM pg_constraint c "
            "JOIN pg_class r ON r.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = r.relnamespace "
            "WHERE n.nspname = 'public' AND c.contype = 'c'"
        )
    ):
        names.setdefault(table_name, set()).add(constraint_name)
    return names


@pytest.fixture(scope="module")
def repaired_database():
    with _database("symgov-name-repair") as (engine, url, raw_url):
        _alembic(url, "upgrade", REPAIR_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(repaired_database) -> uuid.UUID:
    engine, _ = repaired_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "name-repair@example.test", "now": NOW},
        )
    return identifier


# --------------------------------------------------------------------------
# The repair itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("model", REPAIRED_MODELS, ids=lambda m: m.__tablename__)
def test_repaired_tables_carry_exactly_the_orm_constraint_names(repaired_database, model):
    engine, _ = repaired_database
    with engine.begin() as connection:
        actual = _database_names(connection).get(model.__tablename__, set())
    assert actual == _expected_names(model.__table__)


@pytest.mark.parametrize("table_name", sorted(REPAIRED_TABLES))
def test_no_double_prefixed_or_truncated_name_remains(repaired_database, table_name):
    engine, _ = repaired_database
    with engine.begin() as connection:
        names = _database_names(connection).get(table_name, set())
    assert names, f"no check constraints found on {table_name}"
    for name in names:
        assert not name.startswith(f"ck_{table_name}_ck_"), f"still double-prefixed: {name}"
        assert len(name) <= 63


def test_the_repair_renamed_rather_than_recreated_the_constraints(repaired_database, author_id):
    """A rename keeps the expression. Dropping and recreating one would be an
    easy way to lose a constraint silently, so prove each still bites."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid "
                "WHERE r.relname IN ('semantic_concepts','semantic_concept_revisions',"
                "'symbol_semantic_assignments') AND c.contype = 'c'"
            )
        ).scalar_one()
    assert count == 16

    with engine.begin() as connection:
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            connection.execute(
                text(
                    "INSERT INTO semantic_concepts (id,concept_code,concept_kind,status,"
                    "created_by_user_id,created_at,updated_at) "
                    "VALUES (:id,'NOT-A-CODE','physical_equipment','draft',:actor,:now,:now)"
                ),
                {"id": uuid.uuid4(), "actor": author_id, "now": NOW},
            )
    # The violation must now be reported under the repaired name.
    assert "ck_semantic_concepts_concept_code_grammar" in str(caught.value)


def test_a_repaired_constraint_name_is_usable_for_error_dispatch(repaired_database, author_id):
    """The reason the drift mattered: `semantic_concepts.py` already dispatches
    on a constraint name for the concept-code index, and any module doing the
    same for a check constraint would previously never have matched."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        with pytest.raises((IntegrityError, DBAPIError)) as caught:
            connection.execute(
                text(
                    "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,"
                    "semantic_concept_id,assignment_role,status,method,created_at,updated_at) "
                    "VALUES (:id,:rev,:concept,'not_a_role','proposed','manual',:now,:now)"
                ),
                {"id": uuid.uuid4(), "rev": uuid.uuid4(), "concept": uuid.uuid4(), "now": NOW},
            )
    diagnostic = getattr(caught.value.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    assert constraint_name in {
        "ck_symbol_semantic_assignments_assignment_role",
        # A foreign key can fail first depending on constraint evaluation order.
        "fk_symbol_semantic_assignments_symbol_revision_id",
        "fk_symbol_semantic_assignments_semantic_concept_id",
    }, constraint_name


def test_the_repair_is_idempotent_across_a_downgrade_and_re_upgrade():
    """`downgrade` is a documented no-op, so re-upgrading must find every
    constraint already correctly named and issue no rename."""
    with _database("symgov-name-repair-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", REPAIR_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", REPAIR_REVISION)
        with engine.begin() as connection:
            names = _database_names(connection)
        for model in REPAIRED_MODELS:
            assert names.get(model.__tablename__, set()) == _expected_names(model.__table__)


def test_the_repair_applies_from_a_fresh_database_at_the_previous_head():
    """Proves the definition markers still locate the constraints when the
    names carry SQLAlchemy's truncation hashes, which is the state every
    existing database is in."""
    with _database("symgov-name-repair-fresh") as (engine, url, _raw):
        _alembic(url, "upgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            before = _database_names(connection)
        assert any(
            name.startswith("ck_symbol_semantic_assignments_ck_")
            for name in before["symbol_semantic_assignments"]
        ), "expected the pre-repair database to carry the double-prefixed names"

        _alembic(url, "upgrade", REPAIR_REVISION)
        with engine.begin() as connection:
            after = _database_names(connection)
        for model in REPAIRED_MODELS:
            assert after[model.__tablename__] == _expected_names(model.__table__)


# --------------------------------------------------------------------------
# The guard that would have caught the original defect
# --------------------------------------------------------------------------


def test_every_table_outside_the_recorded_drift_matches_the_orm(repaired_database):
    """For each mapped table, PostgreSQL's check-constraint names must match
    what SQLAlchemy would emit from `schema.py`.

    Comparing against the *emitted* names matters: `constraint.name` in the
    metadata is the pre-truncation name, so a naive comparison reports drift
    on every long name whether or not the two sides actually disagree.
    """
    engine, _ = repaired_database
    with engine.begin() as connection:
        actual = _database_names(connection)

    drifted = []
    for table in Base.metadata.tables.values():
        if table.name not in actual or table.name in PRE_EXISTING_NAME_DRIFT:
            continue
        if actual[table.name] != _expected_names(table):
            drifted.append(table.name)
    assert drifted == [], (
        "check constraint names in the database disagree with schema.py for: "
        f"{drifted}. Pass a bare name to CheckConstraint in both the model and "
        "the migration, or record the table in PRE_EXISTING_NAME_DRIFT with a reason."
    )


def test_the_semantic_model_is_no_longer_in_the_recorded_drift(repaired_database):
    """The three semantic tables are what this migration removed from that
    list. If one comes back, the guard above is being weakened rather than the
    defect fixed."""
    assert REPAIRED_TABLES.isdisjoint(PRE_EXISTING_NAME_DRIFT)


def test_the_recorded_drift_is_real_and_not_stale(repaired_database):
    """An allowlist entry that no longer drifts should be deleted, or the guard
    silently stops covering that table."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        actual = _database_names(connection)

    stale = []
    for table in Base.metadata.tables.values():
        if table.name not in PRE_EXISTING_NAME_DRIFT or table.name not in actual:
            continue
        if actual[table.name] == _expected_names(table):
            stale.append(table.name)
    assert stale == [], f"no longer drifting, remove from PRE_EXISTING_NAME_DRIFT: {stale}"
