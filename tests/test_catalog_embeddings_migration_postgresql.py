"""`20261010_0071` adds the Catalog embeddings table and the ledger values for it.

Rehearsed in a disposable container: the table and its constraints exist with
names inside PostgreSQL's 63-character limit, the foreign keys cascade, the
usage ledger's `use_case` and `request_kind` checks gain `catalog_embedding`
and `embedding`, and the downgrade puts everything back.

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

BEFORE = "20261007_0070"
MIGRATION = "20261010_0071"
DATABASE = "symgov_catalog_embeddings_probe"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the catalog embeddings migration rehearsal")

    name = f"symgov-catalog-emb-probe-{uuid.uuid4().hex[:12]}"
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


def _checks(raw: str, table: str) -> dict[str, str]:
    with psycopg.connect(raw) as connection:
        return dict(
            connection.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = %s::regclass AND contype IN ('c', 'f')",
                (table,),
            ).fetchall()
        )


def _table_exists(raw: str) -> bool:
    with psycopg.connect(raw) as connection:
        return connection.execute("SELECT to_regclass('catalog_symbol_embeddings')").fetchone()[0] is not None


def test_the_table_arrives_with_short_named_constraints_and_cascading_keys(database):
    assert not _table_exists(database["raw"])

    _alembic(database["url"], "upgrade", MIGRATION)

    assert _table_exists(database["raw"])
    constraints = _checks(database["raw"], "catalog_symbol_embeddings")
    assert all(len(name) <= 63 for name in constraints), constraints
    assert "dimensions" in constraints["ck_catalog_symbol_embeddings_dimensions"]
    assert "dimensions * 4" in constraints["ck_catalog_symbol_embeddings_length"]
    assert "[0-9a-f]{64}" in constraints["ck_catalog_symbol_embeddings_hash"]
    foreign = [definition for definition in constraints.values() if definition.startswith("FOREIGN KEY")]
    assert len(foreign) == 2 and all("ON DELETE CASCADE" in definition for definition in foreign)
    with psycopg.connect(database["raw"]) as connection:
        primary = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'catalog_symbol_embeddings'::regclass AND contype = 'p'"
        ).fetchone()[0]
    assert primary == "PRIMARY KEY (symbol_revision_id, model)"


def test_the_usage_ledger_accepts_the_embedding_values_and_loses_them_on_downgrade(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    after = _checks(database["raw"], "llm_usage_events")
    assert "'catalog_embedding'" in after["ck_llm_usage_events_llm_usage_events_use_case"]
    assert "'embedding'" in after["ck_llm_usage_events_llm_usage_events_request_kind"]
    for existing in ("workspace_chat", "admin_llm_test", "symbol_property_vision", "vlad_graphic_edit", "ed_guru"):
        assert f"'{existing}'" in after["ck_llm_usage_events_llm_usage_events_use_case"]

    _alembic(database["url"], "downgrade", BEFORE)

    assert not _table_exists(database["raw"])
    restored = _checks(database["raw"], "llm_usage_events")
    assert "catalog_embedding" not in restored["ck_llm_usage_events_llm_usage_events_use_case"]
    assert "'embedding'" not in restored["ck_llm_usage_events_llm_usage_events_request_kind"]
    assert "'ed_guru'" in restored["ck_llm_usage_events_llm_usage_events_use_case"]

    _alembic(database["url"], "upgrade", MIGRATION)
    assert _table_exists(database["raw"])
