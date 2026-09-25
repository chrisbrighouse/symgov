"""Rehearsal for 20260925_0062 against real PostgreSQL.

20260919_0061 passed pre-prefixed check-constraint names, so the database holds
double-prefixed, hash-truncated names that only a real server can show. This
proves the defect is there at 0061, that 0062 renames all three to the names
`schema.py` now emits, and that it renamed rather than recreated them: the same
constraint OIDs carry the same definitions.

`test_semantic_constraint_name_repair_postgresql.py` holds the mapped-table
guard, but its fixture stops at 20260909_0053, before this table existed.

Redaction: this file never prints the disposable container's connection string.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_constraint_name_repair_postgresql import _expected_names  # noqa: E402

from symgov_backend.models import PublishedPreviewAuthorization  # noqa: E402

TABLE = PublishedPreviewAuthorization.__tablename__
DEFECTIVE_REVISION = "20260919_0061"
REPAIR_REVISION = "20260925_0062"


def _constraints(engine) -> dict[str, tuple[int, str]]:
    """name -> (oid, definition) for the table's check constraints."""
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT c.conname, c.oid::int, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :table AND c.contype = 'c'"
            ),
            {"table": TABLE},
        ).all()
    return {name: (oid, definition) for name, oid, definition in rows}


@pytest.fixture(scope="module")
def database():
    with _database("symgov-preview-auth-names") as (engine, url, _raw_url):
        _alembic(url, "upgrade", DEFECTIVE_REVISION)
        before = _constraints(engine)
        _alembic(url, "upgrade", REPAIR_REVISION)
        yield engine, url, before


def test_0061_really_left_double_prefixed_truncated_names(database):
    _engine, _url, before = database
    assert len(before) == 3
    assert all(name.startswith(f"ck_{TABLE}_ck_") for name in before), sorted(before)


def test_0062_leaves_exactly_the_names_the_orm_emits(database):
    engine, _url, _before = database
    expected = _expected_names(PublishedPreviewAuthorization.__table__)
    assert set(_constraints(engine)) == expected
    assert all(len(name) <= 63 and "_ck_" not in name for name in expected)


def test_0062_renamed_rather_than_recreated(database):
    engine, _url, before = database
    after = _constraints(engine)
    # Same constraint objects, same expressions: only the names moved.
    assert sorted(before.values()) == sorted(after.values())


def test_0062_is_idempotent_across_a_downgrade_and_re_upgrade(database):
    engine, url, _before = database
    repaired = _constraints(engine)
    _alembic(url, "downgrade", DEFECTIVE_REVISION)
    # The downgrade is a documented no-op: the corrected names stay.
    assert _constraints(engine) == repaired
    _alembic(url, "upgrade", REPAIR_REVISION)
    assert _constraints(engine) == repaired
