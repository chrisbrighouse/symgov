"""`20260926_0066` renumbers `S-000001` to `S-1` and retires the padded IDs.

Rehearsed in a disposable container on padded IDs of several widths, an
already-unpadded ID, a demotion's historical alias and a saved clipboard.

Redaction: this file never prints the disposable container's connection
string or role passwords.
"""

from __future__ import annotations

import json
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

BEFORE = "20260926_0065"
MIGRATION = "20260926_0066"
DATABASE = "symgov_unpadded_id_probe"

# Canonical ID before, and after.
CANONICAL = {
    "S-000001": "S-1",
    "S-000012": "S-12",
    "S-123456": "S-123456",
    "S-1000000": "S-1000000",
}
ALIAS = "S-000077"


@pytest.fixture(scope="module")
def migrated():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the catalog symbol ID migration rehearsal")

    name = f"symgov-unpadded-probe-{uuid.uuid4().hex[:12]}"
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
        owner = uuid.uuid4()
        with psycopg.connect(raw) as connection:
            connection.execute(
                "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
                " VALUES (%s, 'owner@example.test', 'Owner', 'x', now(), now(), now())",
                (owner,),
            )
            for index, identifier in enumerate([*CANONICAL, ALIAS]):
                ids[identifier] = uuid.uuid4()
                connection.execute(
                    "INSERT INTO governed_symbols (id, slug, canonical_name, category, discipline, owner_id,"
                    " created_at, updated_at) VALUES (%s, %s, 'Symbol', 'Valves', 'Process', %s, now(), now())",
                    (ids[identifier], f"symbol-{index}", owner),
                )
                role = "historical_alias" if identifier == ALIAS else "canonical"
                connection.execute(
                    "INSERT INTO catalog_symbol_identifiers"
                    " (identifier, role, governed_symbol_id, allocation_source, allocated_at)"
                    " VALUES (%s, %s, %s, 'global_sequence', now())",
                    (identifier, role, ids[identifier]),
                )
                if role == "canonical":
                    connection.execute(
                        "UPDATE governed_symbols SET catalog_symbol_id = %s WHERE id = %s",
                        (identifier, ids[identifier]),
                    )
            connection.execute(
                "INSERT INTO catalog_workbench_clipboards (id, user_id, organization_id, items_json, created_at, updated_at)"
                " VALUES (%s, %s, NULL, %s, now(), now())",
                (uuid.uuid4(), owner, json.dumps([
                    {"id": "symbol-0", "displayName": "S-000001", "name": "Symbol"},
                    {"id": "private-draft", "displayName": "Draft", "name": "Other"},
                ])),
            )
        _alembic(url, "upgrade", MIGRATION)
        yield {"raw": raw, "ids": ids}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _registry(raw: str) -> dict:
    with psycopg.connect(raw) as connection:
        return {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT identifier, role, governed_symbol_id, allocation_source, change_reason"
                " FROM catalog_symbol_identifiers"
            ).fetchall()
        }


def test_each_symbol_is_renumbered_to_the_same_number_without_zeros(migrated):
    with psycopg.connect(migrated["raw"]) as connection:
        current = dict(connection.execute("SELECT id, catalog_symbol_id FROM governed_symbols").fetchall())
    for before, after in CANONICAL.items():
        assert current[migrated["ids"][before]] == after, before
    registry = _registry(migrated["raw"])
    assert registry["S-1"][:3] == ("canonical", migrated["ids"]["S-000001"], "reviewed_correction")
    assert "replaces S-000001" in registry["S-1"][3]


def test_the_padded_ids_are_retired_and_stop_resolving(migrated):
    registry = _registry(migrated["raw"])
    for before in ("S-000001", "S-000012"):
        role, target, _, reason = registry[before]
        assert role == "tombstone" and target is None, before
        assert f"replaced by {CANONICAL[before]}" in reason
    # Already unpadded: untouched, and nothing retired for it.
    assert registry["S-123456"][0] == "canonical"
    assert registry["S-1000000"][0] == "canonical"
    # A demotion's alias is not a canonical ID and is left alone.
    assert registry[ALIAS][:2] == ("historical_alias", migrated["ids"][ALIAS])


def test_saved_clipboard_labels_follow_the_new_ids(migrated):
    with psycopg.connect(migrated["raw"]) as connection:
        (items,) = connection.execute("SELECT items_json FROM catalog_workbench_clipboards").fetchone()
    assert [item["displayName"] for item in items] == ["S-1", "Draft"]
    assert [item["id"] for item in items] == ["symbol-0", "private-draft"]
