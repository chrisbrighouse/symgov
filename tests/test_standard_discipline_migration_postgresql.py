"""`20260926_0065` stores every discipline under its standard name (X-04).

Rehearsed on the eleven spellings production held on 2026-09-26, plus one
value the frozen mapping does not know, in a disposable container.

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

BEFORE = "20260925_0064"
MIGRATION = "20260926_0065"
DATABASE = "symgov_discipline_probe"

# Production's stored spellings on 2026-09-26, and what each must become.
EXPECTED = {
    "Piping / P&ID": "Piping / P&ID",
    "Mechanical": "Mechanical",
    "general": "General / Annotation",
    "Instrumentation & Controls": "Instrumentation & Controls",
    "Piping": "Piping / P&ID",
    "Process": "Process",
    "Instrumentation": "Instrumentation & Controls",
    "Process_instrumentation": "Instrumentation & Controls",
    "process_instrumentation": "Instrumentation & Controls",
    "instrumentation": "Instrumentation & Controls",
    "Electrical": "Electrical",
    # Not in the frozen mapping: left exactly as it is.
    "Process Control": "Process Control",
}


@pytest.fixture(scope="module")
def migrated():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the discipline migration rehearsal")

    name = f"symgov-discipline-probe-{uuid.uuid4().hex[:12]}"
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
        ids: dict[str, uuid.UUID] = {}
        with psycopg.connect(raw, autocommit=True) as connection:
            owner = uuid.uuid4()
            connection.execute(
                "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
                " VALUES (%s, 'owner@example.test', 'Owner', 'x', now(), now(), now())",
                (owner,),
            )
            for index, stored in enumerate(EXPECTED):
                ids[stored] = uuid.uuid4()
                connection.execute(
                    "INSERT INTO governed_symbols (id, slug, canonical_name, category, discipline, owner_id,"
                    " created_at, updated_at) VALUES (%s, %s, 'Symbol', 'Valves', %s, %s,"
                    " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    (ids[stored], f"symbol-{index}", stored, owner),
                )
            generations = dict(connection.execute(
                "SELECT id, catalog_facet_generation FROM governed_symbols"
            ).fetchall())
        _alembic(url, "upgrade", MIGRATION)
        yield {"raw": raw, "ids": ids, "generations": generations}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _rows(raw: str) -> dict:
    with psycopg.connect(raw) as connection:
        return {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT id, discipline, catalog_facet_generation, updated_at FROM governed_symbols"
            ).fetchall()
        }


def test_every_stored_spelling_becomes_its_standard_name(migrated):
    rows = _rows(migrated["raw"])
    for stored, expected in EXPECTED.items():
        assert rows[migrated["ids"][stored]][0] == expected, stored


def test_only_changed_rows_are_audited_with_their_previous_value(migrated):
    changed = {stored: expected for stored, expected in EXPECTED.items() if stored != expected}
    with psycopg.connect(migrated["raw"]) as connection:
        audited = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT entity_id, payload_json FROM audit_events WHERE action = 'discipline_standardized'"
            ).fetchall()
        }
    assert set(audited) == {migrated["ids"][stored] for stored in changed}
    for stored, expected in changed.items():
        payload = audited[migrated["ids"][stored]]
        assert payload["previous"] == stored
        assert payload["updated"] == expected
        assert payload["migration"] == MIGRATION


def test_changed_rows_get_fresh_facets_but_keep_their_update_time(migrated):
    rows = _rows(migrated["raw"])
    for stored, expected in EXPECTED.items():
        symbol_id = migrated["ids"][stored]
        discipline, generation, updated_at = rows[symbol_id]
        before = migrated["generations"][symbol_id]
        assert generation == (before + 1 if stored != expected else before), stored
        assert updated_at.isoformat().startswith("2026-01-01"), stored
