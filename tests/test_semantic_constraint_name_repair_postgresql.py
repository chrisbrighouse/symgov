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

REPAIR_REVISION = "20260909_0053"
PREVIOUS_REVISION = "20260909_0049"
# The head immediately before 20260909_0053, for the reversibility rehearsal.
PREVIOUS_REPAIR_REVISION = "20260909_0052"
# The revision 20260829_0033 revises -- rolling back to it runs that
# migration's downgrade, which is what a one-way rename broke.
ROLLBACK_PAST_0033 = "20260826_0032"

REPAIRED_MODELS = (SemanticConcept, SemanticConceptRevision, SymbolSemanticAssignment)
REPAIRED_TABLES = frozenset(model.__tablename__ for model in REPAIRED_MODELS)

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

# Tables whose check-constraint names disagree with `schema.py`. Empty, and it
# must stay empty: 20260909_0053 repaired the last ten entries. Shrinking this
# set was the point; growing it is a regression, and the guard below is now
# exact for every mapped table.
PRE_EXISTING_NAME_DRIFT: frozenset[str] = frozenset()

# Tables where the ORM and the database deliberately enforce *different
# expressions* under the same constraint name. This is not drift and must not
# be "repaired".
#
# 20260822_0030 implements these bounds with the `stage4_jsonb_max_depth` and
# `stage4_string_array_bounds` PL/pgSQL functions. A CheckConstraint in
# `schema.py` referencing either would make `Base.metadata.create_all()` fail
# against a database that has never been migrated -- and
# `tests/test_f0_4_review_without_unpublication.py` calls exactly that. So the
# ORM carries a weaker builtin-only approximation on purpose, and
# `test_project_symbol_set_migration.py::
# test_orm_metadata_keeps_create_all_safe_provenance_boundary` asserts it.
#
# The database is the stronger side in every case here, so production
# integrity is unaffected. The hazard runs the other way: a create_all schema
# is laxer than production, so these expressions are the ones to check by hand
# when changing either side.
INTENTIONAL_ORM_EXPRESSION_DIVERGENCE: dict[str, frozenset[str]] = {
    "projects": frozenset({"ck_projects_ck_projects_metadata_bounds"}),
    "symbol_sets": frozenset(
        {
            "ck_symbol_sets_ck_symbol_sets_disciplines_bounds",
            "ck_symbol_sets_ck_symbol_sets_use_cases_bounds",
        }
    ),
    "symbol_set_items": frozenset(
        {"ck_symbol_set_items_ck_symbol_set_items_provenance_bounds"}
    ),
}

# The ten tables 20260909_0053 renames constraints on.
_REPAIRED_TABLE_NAMES = frozenset(
    {
        "catalog_symbol_identifiers",
        "external_identities",
        "governed_symbols",
        "organization_symbol_review_decisions",
        "organization_symbol_review_submissions",
        "provenance_assessments",
        "user_sessions",
    }
)

# The custom SQL functions that may never appear in an ORM CheckConstraint.
CREATE_ALL_UNSAFE_FUNCTIONS = ("stage4_jsonb_max_depth", "stage4_string_array_bounds")


def _compact_sql(expression: str) -> str:
    """Normalise whitespace, casing and PostgreSQL's rendering noise.

    `pg_get_constraintdef` wraps in `CHECK (...)`, adds `::text` casts and
    parenthesises aggressively, so a literal comparison would report every
    constraint as divergent.
    """
    text_value = " ".join(expression.split()).lower()
    for noise in ("check (", "::text", "::name", "(", ")", " "):
        text_value = text_value.replace(noise, "")
    return text_value


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
    silently stops covering that table. The set is empty as of
    20260909_0053, so this now also asserts nothing crept back in."""
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
    assert PRE_EXISTING_NAME_DRIFT == frozenset(), (
        "20260909_0053 emptied this allowlist. A new entry means a migration "
        "introduced name drift instead of passing a bare CheckConstraint name."
    )


# --------------------------------------------------------------------------
# The three tables that diverge by expression on purpose
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", sorted(INTENTIONAL_ORM_EXPRESSION_DIVERGENCE))
def test_the_divergent_tables_still_agree_on_every_name(repaired_database, table_name):
    """Divergence is in the expressions only. If a name disagrees too, the
    register is hiding real drift."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        actual = _database_names(connection).get(table_name, set())
    assert actual == _expected_names(Base.metadata.tables[table_name])


@pytest.mark.parametrize(
    ("table_name", "constraint_names"), sorted(INTENTIONAL_ORM_EXPRESSION_DIVERGENCE.items())
)
def test_the_registered_divergence_is_real(repaired_database, table_name, constraint_names):
    """A register entry that no longer diverges should be deleted, exactly as a
    stale allowlist entry should."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        database_definitions = {
            name: definition
            for name, definition in connection.execute(
                text(
                    "SELECT c.conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
                    "JOIN pg_class r ON r.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = r.relnamespace "
                    "WHERE n.nspname = 'public' AND c.contype = 'c' AND r.relname = :table"
                ),
                {"table": table_name},
            )
        }
    preparer = postgresql.dialect().identifier_preparer
    orm = {
        preparer.format_constraint(constraint, _alembic_quote=False): str(constraint.sqltext)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }
    for name in constraint_names:
        assert name in database_definitions, f"{name} is gone from the database"
        assert name in orm, f"{name} is gone from the ORM"
        assert _compact_sql(database_definitions[name]) != _compact_sql(orm[name]), (
            f"{name} no longer diverges; remove it from "
            "INTENTIONAL_ORM_EXPRESSION_DIVERGENCE"
        )


def test_no_orm_constraint_references_a_function_create_all_cannot_provide(repaired_database):
    """The reason the divergence exists. A CheckConstraint naming one of these
    functions would make Base.metadata.create_all() fail on an unmigrated
    database, which
    tests/test_f0_4_review_without_unpublication.py depends on."""
    offenders = []
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if not isinstance(constraint, CheckConstraint):
                continue
            expression = str(constraint.sqltext)
            if any(function in expression for function in CREATE_ALL_UNSAFE_FUNCTIONS):
                offenders.append(f"{table.name}.{constraint.name}")
    assert offenders == [], (
        "these ORM check constraints reference a migration-only SQL function and "
        f"will break create_all(): {offenders}"
    )


def test_the_repair_round_trips_through_a_downgrade():
    """20260909_0053 must be reversible, and this is what proves its fifteen
    restore names are right.

    Ten of them carry a SQLAlchemy truncation hash reproduced verbatim from the
    deployed schema. If any were wrong, the downgrade would leave a constraint
    under the repaired name and the comparison below would catch it. The reason
    it matters: alembic applies NAMING_CONVENTION to `op.drop_constraint`, so
    20260829_0033's and 20260808_0027's downgrades ask for the *doubled* names,
    and a one-way rename breaks those rollback paths.
    """
    with _database("symgov-name-repair-reverse") as (engine, url, _raw):
        _alembic(url, "upgrade", PREVIOUS_REPAIR_REVISION)
        with engine.begin() as connection:
            before = _database_names(connection)

        _alembic(url, "upgrade", REPAIR_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REPAIR_REVISION)
        with engine.begin() as connection:
            after = _database_names(connection)

        for table in sorted(_REPAIRED_TABLE_NAMES):
            assert after[table] == before[table], (
                f"downgrade did not restore {table}'s deployed constraint names"
            )

        # And a re-upgrade must land back on the ORM's names.
        _alembic(url, "upgrade", REPAIR_REVISION)
        with engine.begin() as connection:
            final = _database_names(connection)
        for table in sorted(_REPAIRED_TABLE_NAMES):
            assert final[table] == _expected_names(Base.metadata.tables[table])


def test_rolling_back_past_the_older_migrations_still_works():
    """The regression that caught this: `alembic downgrade` past
    20260829_0033 failed once the constraints it drops by name had been
    renamed."""
    with _database("symgov-name-repair-rollback") as (engine, url, _raw):
        _alembic(url, "upgrade", REPAIR_REVISION)
        result = _alembic(url, "downgrade", ROLLBACK_PAST_0033, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        with engine.begin() as connection:
            remaining = _database_names(connection)
        # 20260829_0033's downgrade drops both of governed_symbols' check
        # constraints, which leaves the table with none at all -- so it drops
        # out of this map entirely rather than appearing with an empty set.
        assert remaining.get("governed_symbols", set()) == set()
        # user_sessions' `purpose` constraint is dropped by 20260808_0027,
        # which is earlier than this rollback target, so it is still here --
        # and it must be back under its deployed doubled name, which is the
        # direct demonstration that downgrade restored it.
        session_names = remaining.get("user_sessions", set())
        assert "ck_user_sessions_ck_user_sessions_purpose" in session_names
        assert "ck_user_sessions_purpose" not in session_names


def test_provenance_assessments_is_now_declared_in_the_orm(repaired_database):
    """The one entry in the old allowlist that was not a naming problem: the
    database enforced two enumerations the model never declared, so a
    create_all schema was laxer than production."""
    engine, _ = repaired_database
    with engine.begin() as connection:
        actual = _database_names(connection).get("provenance_assessments", set())
    expected = _expected_names(Base.metadata.tables["provenance_assessments"])
    assert actual == expected
    assert expected == {
        "ck_provenance_assessments_processing_outcome",
        "ck_provenance_assessments_rights_disposition",
    }
