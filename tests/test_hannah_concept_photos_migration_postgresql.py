"""`20261020_0074` adds Hannah's concept targets and concept photos.

Rehearsed in a disposable container:
- both tables arrive with constraint names inside PostgreSQL's 63-character
  limit and exactly the names the ORM emits;
- the concept foreign keys refuse deletes, and the user and queue keys null out;
- the usage ledger accepts Hannah's use cases and agent slug;
- the downgrade puts everything back.

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
from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.engine import URL

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from postgres_image import POSTGRES_IMAGE, POSTGRES_READY_TIMEOUT  # noqa: E402
from test_two_role_privilege_model_postgresql import _alembic, _docker  # noqa: E402

from symgov_backend.models.schema import HannahConceptPhoto, HannahConceptTarget  # noqa: E402

BEFORE = "20261015_0073"
MIGRATION = "20261020_0074"
DATABASE = "symgov_hannah_concept_photos_probe"
TABLES = ("hannah_concept_targets", "hannah_concept_photos")


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the Hannah concept photos migration rehearsal")

    name = f"symgov-hannah-probe-{uuid.uuid4().hex[:12]}"
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


def _constraints(raw: str, table: str) -> dict[str, str]:
    with psycopg.connect(raw) as connection:
        return dict(
            connection.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = %s::regclass AND contype IN ('c', 'f')",
                (table,),
            ).fetchall()
        )


def _table_exists(raw: str, table: str) -> bool:
    with psycopg.connect(raw) as connection:
        return connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


def _orm_constraint_names(model) -> set[str]:
    names = set()
    for constraint in model.__table__.constraints:
        if isinstance(constraint, CheckConstraint):
            # The metadata naming convention has usually already applied the
            # prefix by the time the table is built.
            prefix = f"ck_{model.__tablename__}_"
            name = str(constraint.name)
            names.add(name if name.startswith(prefix) else prefix + name)
        elif isinstance(constraint, ForeignKeyConstraint):
            names.add(str(constraint.name))
    return names


def test_both_tables_arrive_with_the_names_the_orm_emits(database):
    assert not any(_table_exists(database["raw"], table) for table in TABLES)

    _alembic(database["url"], "upgrade", MIGRATION)

    for model in (HannahConceptTarget, HannahConceptPhoto):
        constraints = _constraints(database["raw"], model.__tablename__)
        assert all(len(name) <= 63 for name in constraints), constraints
        assert set(constraints) == _orm_constraint_names(model)

    targets = _constraints(database["raw"], "hannah_concept_targets")
    assert "ON DELETE RESTRICT" in targets["fk_hannah_concept_targets_concept"]
    assert "ON DELETE SET NULL" in targets["fk_hannah_concept_targets_updated_by"]
    photos = _constraints(database["raw"], "hannah_concept_photos")
    assert "ON DELETE RESTRICT" in photos["fk_hannah_concept_photos_concept"]
    assert "ON DELETE SET NULL" in photos["fk_hannah_concept_photos_queue_item"]
    assert "object_key IS NOT NULL" in photos["ck_hannah_concept_photos_approved_has_object"]

    with psycopg.connect(database["raw"]) as connection:
        unique = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = ANY(%s) AND indexdef LIKE 'CREATE UNIQUE%%'",
                (list(TABLES),),
            ).fetchall()
        }
    assert {"uq_hannah_concept_targets_concept", "uq_hannah_concept_photos_concept_image"} <= unique


def test_the_usage_ledger_accepts_hannah_and_loses_her_on_downgrade(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    after = _constraints(database["raw"], "llm_usage_events")
    use_case = after["ck_llm_usage_events_llm_usage_events_use_case"]
    assert "'hannah_vision'" in use_case and "'hannah_targets'" in use_case
    for existing in ("workspace_chat", "admin_llm_test", "symbol_property_vision", "vlad_graphic_edit", "ed_guru", "catalog_embedding"):
        assert f"'{existing}'" in use_case
    assert "'hannah'" in after["ck_llm_usage_events_llm_usage_events_agent_slug"]

    _alembic(database["url"], "downgrade", BEFORE)

    assert not any(_table_exists(database["raw"], table) for table in TABLES)
    restored = _constraints(database["raw"], "llm_usage_events")
    assert "hannah" not in restored["ck_llm_usage_events_llm_usage_events_use_case"]
    assert "hannah" not in restored["ck_llm_usage_events_llm_usage_events_agent_slug"]
    assert "'catalog_embedding'" in restored["ck_llm_usage_events_llm_usage_events_use_case"]

    _alembic(database["url"], "upgrade", MIGRATION)
    assert all(_table_exists(database["raw"], table) for table in TABLES)
