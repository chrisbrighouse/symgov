"""`20261125_0081` adds the organization-private scope to the visual descriptors, rehearsed in a disposable PostgreSQL.

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

BEFORE = "20261123_0080"
MIGRATION = "20261125_0081"
DATABASE = "symgov_visual_private_probe"
TABLE = "catalog_symbol_visual_descriptors"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the visual descriptor migration rehearsal")
    name = f"symgov-vp-probe-{uuid.uuid4().hex[:12]}"
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


def _constraints(raw):
    with psycopg.connect(raw) as c:
        return dict(c.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
            "AND contype IN ('c', 'f')", (TABLE,)).fetchall())


def _seed_symbol(c):
    """Ids for one organization, symbol and three revisions. Foreign keys are switched off for this
    connection (the probe runs as the superuser) so the rows need no users, sets or organizations;
    check constraints, which are what this file tests, still apply."""
    c.execute("SET session_replication_role = replica")
    return uuid.uuid4(), uuid.uuid4(), [uuid.uuid4() for _ in range(3)]


def _insert(c, rev, sym, scope, owner):
    c.execute(
        f"INSERT INTO {TABLE} (symbol_revision_id, governed_symbol_id, matcher_version, scope, owner_organization_id, "
        "local_descriptor, skeleton, skeleton_fine, content_aspect, asset_object_key, source_sha256, renderer_version, "
        "font_set_id, built_at) VALUES (%s, %s, 'v1', %s, %s, '\\x00000000'::bytea, decode(repeat('00', 2048), 'hex'), "
        "decode(repeat('00', 8192), 'hex'), 1.0, 'k', repeat('a', 64), 'r', 'f', now())",
        (rev, sym, scope, owner),
    )


def test_the_private_scope_requires_an_owner_and_public_forbids_one(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    constraints = _constraints(database["raw"])
    assert all(len(name) <= 63 for name in constraints), constraints
    assert "organization_private" in constraints[f"ck_{TABLE}_scope"]
    assert "owner_organization_id IS NULL" in constraints[f"ck_{TABLE}_owner"]
    assert "ON DELETE CASCADE" in constraints["fk_catalog_symbol_visual_descriptors_owner"]
    with psycopg.connect(database["raw"]) as c:
        org, sym, revs = _seed_symbol(c)
        _insert(c, revs[0], sym, "public", None)
        _insert(c, revs[1], sym, "organization_private", org)
        for scope, owner in (("organization_private", None), ("public", org), ("elsewhere", org)):
            c.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert(c, revs[2], sym, scope, owner)
            c.execute("ROLLBACK TO SAVEPOINT s")
        c.commit()


def test_downgrade_drops_private_rows_keeps_public_ones_and_upgrade_comes_back(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    _alembic(database["url"], "downgrade", BEFORE)
    with psycopg.connect(database["raw"]) as c:
        scopes = [r[0] for r in c.execute(f"SELECT scope FROM {TABLE} ORDER BY scope").fetchall()]
        columns = {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (TABLE,)).fetchall()}
    assert scopes == ["public"] and "owner_organization_id" not in columns
    assert "organization_private" not in _constraints(database["raw"])[f"ck_{TABLE}_scope"]
    _alembic(database["url"], "upgrade", MIGRATION)
    assert f"ck_{TABLE}_owner" in _constraints(database["raw"])
