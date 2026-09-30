"""`20260930_0067` lets the usage ledger hold Ed's `ed_guru` calls.

Rehearsed in a disposable container: the constraint keeps the name the ORM
declares, gains `ed_guru`, and the downgrade puts the old list back.

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

BEFORE = "20260926_0066"
MIGRATION = "20260930_0067"
DATABASE = "symgov_llm_usage_ed_guru_probe"
# The naming convention prefixes the bare name 0025 and the ORM both use.
CONSTRAINT = "ck_llm_usage_events_llm_usage_events_use_case"


def _use_case_checks(raw: str) -> list[tuple[str, str]]:
    with psycopg.connect(raw) as connection:
        return connection.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'llm_usage_events'::regclass AND contype = 'c'"
            " AND pg_get_constraintdef(oid) LIKE '%%use_case%%'"
        ).fetchall()


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the llm usage ed_guru migration rehearsal")

    name = f"symgov-ed-guru-probe-{uuid.uuid4().hex[:12]}"
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


def test_the_use_case_check_gains_ed_guru_and_loses_it_on_downgrade(database):
    [(name, before)] = _use_case_checks(database["raw"])
    assert name == CONSTRAINT
    assert "ed_guru" not in before

    _alembic(database["url"], "upgrade", MIGRATION)
    [(name, after)] = _use_case_checks(database["raw"])
    assert name == CONSTRAINT
    assert "'ed_guru'" in after
    for existing in ("workspace_chat", "admin_llm_test", "symbol_property_vision", "vlad_graphic_edit"):
        assert f"'{existing}'" in after

    _alembic(database["url"], "downgrade", BEFORE)
    [(name, restored)] = _use_case_checks(database["raw"])
    assert name == CONSTRAINT
    assert "ed_guru" not in restored

    _alembic(database["url"], "upgrade", MIGRATION)
    assert "'ed_guru'" in _use_case_checks(database["raw"])[0][1]
