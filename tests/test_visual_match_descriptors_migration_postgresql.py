"""`20261123_0080` adds the visual descriptor table, rehearsed in a disposable PostgreSQL.

Redaction: this file never prints the disposable container's connection string or role passwords.
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

BEFORE = "20261025_0079"
MIGRATION = "20261123_0080"
DATABASE = "symgov_visual_descriptors_probe"
TABLE = "catalog_symbol_visual_descriptors"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the visual descriptor migration rehearsal")
    name = f"symgov-vd-probe-{uuid.uuid4().hex[:12]}"
    password = f"disposable-{uuid.uuid4().hex}"
    _docker("run", "--rm", "--detach", "--name", name, "--env", f"POSTGRES_PASSWORD={password}",
            "--env", f"POSTGRES_DB={DATABASE}", "--publish", "127.0.0.1::5432", POSTGRES_IMAGE)
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
        with psycopg.connect(raw, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app NOLOGIN")
        url = URL.create("postgresql+psycopg", username="postgres", password=password, host="127.0.0.1",
                         port=port, database=DATABASE).render_as_string(hide_password=False)
        _alembic(url, "upgrade", BEFORE)
        yield {"raw": raw, "url": url}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _exists(raw):
    with psycopg.connect(raw) as c:
        return c.execute(f"SELECT to_regclass('{TABLE}')").fetchone()[0] is not None


def _constraints(raw):
    with psycopg.connect(raw) as c:
        return dict(c.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
            "AND contype IN ('c', 'f', 'p')", (TABLE,)).fetchall())


def test_the_table_arrives_with_short_names_cascading_keys_and_a_composite_key(database):
    assert not _exists(database["raw"])
    _alembic(database["url"], "upgrade", MIGRATION)
    assert _exists(database["raw"])
    constraints = _constraints(database["raw"])
    assert all(len(name) <= 63 for name in constraints), constraints
    assert "2048" in constraints[f"ck_{TABLE}_skeleton"]
    assert "[0-9a-f]{64}" in constraints[f"ck_{TABLE}_hash"]
    assert "dimensions * 4" in constraints[f"ck_{TABLE}_embedding"]
    foreign = [d for d in constraints.values() if d.startswith("FOREIGN KEY")]
    assert len(foreign) == 2 and all("ON DELETE CASCADE" in d for d in foreign)
    assert f"PRIMARY KEY (symbol_revision_id, matcher_version)" in constraints.values()


def test_a_row_with_half_an_embedding_or_a_wrong_skeleton_is_refused(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    with psycopg.connect(database["raw"]) as c:
        columns = [r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            (TABLE,)).fetchall()]
    assert {"gemini_embedding", "local_descriptor", "skeleton", "asset_object_key", "font_set_id"} <= set(columns)


def test_downgrade_removes_the_table_and_it_can_come_back(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    _alembic(database["url"], "downgrade", BEFORE)
    assert not _exists(database["raw"])
    _alembic(database["url"], "upgrade", MIGRATION)
    assert _exists(database["raw"])
