"""`20261021_0075` drops Hannah's per-symbol photo tables (WP-H7).

Rehearsed in a disposable container:
- the upgrade drops all three tables and leaves the concept tables alone;
- the downgrade rebuilds them, empty, with the same columns and indexes
  `20260519_0010` and `20260530_0014` created, and the upgrade runs again.

Redaction: this file never prints the disposable container's connection
string or role passwords.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import URL

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from postgres_image import POSTGRES_IMAGE, POSTGRES_READY_TIMEOUT  # noqa: E402
from test_two_role_privilege_model_postgresql import _alembic, _docker  # noqa: E402

BEFORE = "20261020_0074"
MIGRATION = "20261021_0075"
DATABASE = "symgov_hannah_retire_probe"
RETIRED = ("hannah_curation_source_sites", "hannah_photo_candidates", "hannah_symbol_curation_states")
KEPT = ("hannah_concept_photos", "hannah_concept_targets")


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the Hannah per-symbol retirement rehearsal")

    name = f"symgov-hannah-retire-{uuid.uuid4().hex[:12]}"
    password = f"disposable-{uuid.uuid4().hex}"
    _docker(
        "run", "--rm", "--detach", "--name", name,
        "--env", f"POSTGRES_PASSWORD={password}",
        "--env", f"POSTGRES_DB={DATABASE}",
        "--publish", "127.0.0.1::5432",
        POSTGRES_IMAGE,
    )
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw = make_conninfo(host="127.0.0.1", port=port, user="postgres", password=password, dbname=DATABASE)
        deadline = time.monotonic() + POSTGRES_READY_TIMEOUT
        while True:
            try:
                with psycopg.connect(raw, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

        # Earlier migrations grant to the app role, so it must exist first.
        with psycopg.connect(raw, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app NOLOGIN")
        url = URL.create(
            "postgresql+psycopg", username="postgres", password=password, host="127.0.0.1", port=port, database=DATABASE
        ).render_as_string(hide_password=False)
        _alembic(url, "upgrade", BEFORE)
        yield {"raw": raw, "url": url}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _tables(raw: str) -> set[str]:
    with psycopg.connect(raw) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'hannah_%'"
        ).fetchall()
    return {row[0] for row in rows}


def _shape(raw: str) -> dict[str, tuple[list, list]]:
    """Each retired table's columns and index definitions, for comparison."""
    shape = {}
    with psycopg.connect(raw) as connection:
        for table in RETIRED:
            columns = connection.execute(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            ).fetchall()
            indexes = connection.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s ORDER BY indexname", (table,)
            ).fetchall()
            shape[table] = (columns, indexes)
    return shape


def test_the_upgrade_drops_the_per_symbol_tables_and_the_downgrade_restores_them(database):
    assert set(RETIRED) | set(KEPT) <= _tables(database["raw"])
    original = _shape(database["raw"])

    _alembic(database["url"], "upgrade", MIGRATION)
    assert _tables(database["raw"]) == set(KEPT)

    _alembic(database["url"], "downgrade", BEFORE)
    assert set(RETIRED) | set(KEPT) <= _tables(database["raw"])
    assert _shape(database["raw"]) == original

    _alembic(database["url"], "upgrade", MIGRATION)
    assert _tables(database["raw"]) == set(KEPT)
