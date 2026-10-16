"""`20261015_0073` adds symbol usage events and rollups, and the audit indexes.

Rehearsed in a disposable container: the tables and their constraints arrive with
names inside PostgreSQL's 63-character limit, the CHECKs hold the privacy rules
(an organization's rows name it, a personal row and a personal key's row never
do, search text is for organization rows only, only an API row carries a key), the
rollup's two partial unique indexes behave, and the downgrade puts it all back.

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

BEFORE = "20261014_0072"
MIGRATION = "20261015_0073"
DATABASE = "symgov_symbol_usage_probe"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the symbol usage migration rehearsal")

    name = f"symgov-symbol-usage-probe-{uuid.uuid4().hex[:12]}"
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
        return dict(connection.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass AND contype IN ('c', 'f')",
            (table,),
        ).fetchall())


def _indexes(raw: str, table: str) -> dict[str, str]:
    with psycopg.connect(raw) as connection:
        return dict(connection.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", (table,)).fetchall())


def _seed(raw: str) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("user", "org", "key", "personal_key")}
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
            " VALUES (%s, %s, %s, 'x', now(), now(), now())", (ids["user"], f"u-{ids['user'].hex[:8]}@example.test", f"U {ids['user'].hex[:8]}"),
        )
        connection.execute(
            "INSERT INTO organizations (id, code, normalized_code, display_name, name_key, fallback_icon_svg, is_active, created_at, updated_at)"
            " VALUES (%s, %s, %s, 'Acme', %s, '<svg/>', false, now(), now())",
            (ids["org"], f"ACME-{ids['org'].hex[:6].upper()}", f"acme-{ids['org'].hex[:6]}", f"acme-{ids['org'].hex}"),
        )
        for name in ("key", "personal_key"):
            connection.execute(
                "INSERT INTO catalog_api_keys (id, customer_name, integration_name, key_prefix, key_hash, scopes_json, created_by, created_at, updated_at)"
                " VALUES (%s, 'c', %s, 'p', %s, '[]', %s, now(), now())", (ids[name], name, uuid.uuid4().hex, ids["user"]),
            )
    return ids


def _insert(raw: str, **values):
    columns = {"id": uuid.uuid4(), "occurred_at": "now()", "event_type": "symbol_viewed", "channel": "web", "scope_kind": "organization"}
    columns.update(values)
    names = ", ".join(columns)
    placeholders = ", ".join("now()" if value == "now()" else "%s" for value in columns.values())
    params = [value for value in columns.values() if value != "now()"]
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute(f"INSERT INTO symbol_usage_events ({names}) VALUES ({placeholders})", params)


def test_the_tables_arrive_with_short_names_and_the_audit_indexes(database):
    _alembic(database["url"], "upgrade", MIGRATION)
    for table in ("symbol_usage_events", "symbol_usage_daily_rollups"):
        constraints = _checks(database["raw"], table)
        assert constraints and all(len(name) <= 63 for name in constraints), constraints
        assert all(len(name) <= 63 for name in _indexes(database["raw"], table))
    assert {"ix_audit_events_created_at", "ix_audit_events_entity"} <= set(_indexes(database["raw"], "audit_events"))
    rollup_indexes = _indexes(database["raw"], "symbol_usage_daily_rollups")
    assert "WHERE (organization_id IS NOT NULL)" in rollup_indexes["uq_symbol_usage_daily_rollups_organization_cell"]
    assert "WHERE (organization_id IS NULL)" in rollup_indexes["uq_symbol_usage_daily_rollups_personal_cell"]


def test_the_checks_carry_the_privacy_rules(database):
    ids = _seed(database["raw"])
    raw = database["raw"]
    # Allowed: an organization's web event with search text; a personal web event; an organization key's API event.
    _insert(raw, user_id=ids["user"], organization_id=ids["org"], event_type="catalog_searched", query_text="valve", result_count=0)
    _insert(raw, user_id=ids["user"], scope_kind="personal")
    _insert(raw, user_id=ids["user"], organization_id=ids["org"], channel="api", api_key_id=ids["key"], key_kind="organization")
    _insert(raw, user_id=ids["user"], scope_kind="personal", channel="api", api_key_id=ids["personal_key"], key_kind="personal")
    refused = [
        dict(scope_kind="personal", organization_id=ids["org"]),                                   # personal row naming an organization
        dict(scope_kind="organization"),                                                          # organization row naming none
        dict(organization_id=ids["org"], channel="api", api_key_id=ids["key"], key_kind="personal"),  # personal key counted for an organization
        dict(organization_id=ids["org"], api_key_id=ids["key"]),                                  # key on a web row
        dict(scope_kind="personal", query_text="private words"),                                  # search text on a personal row
        dict(organization_id=ids["org"], query_text="x" * 201),                                   # over-long search text
        dict(organization_id=ids["org"], result_count=-1),
        dict(organization_id=ids["org"], event_type="symbol_deleted"),
        dict(organization_id=ids["org"], channel="mobile"),
    ]
    for values in refused:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(raw, **values)


def test_a_deleted_key_leaves_its_events_behind_and_the_rollup_indexes_enforce_one_cell(database):
    ids = _seed(database["raw"])
    raw = database["raw"]
    _insert(raw, organization_id=ids["org"], channel="api", api_key_id=ids["key"], key_kind="organization")
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute("DELETE FROM catalog_api_keys WHERE id = %s", (ids["key"],))
        row = connection.execute("SELECT api_key_id FROM symbol_usage_events WHERE organization_id = %s", (ids["org"],)).fetchone()
        assert row == (None,)
        cell = "INSERT INTO symbol_usage_daily_rollups (id, occurred_on, event_type, channel, scope_kind, organization_id, pack_code, event_count, updated_at) VALUES (%s, '2026-10-16', 'symbol_viewed', 'web', %s, %s, '0001', 1, now())"
        connection.execute(cell, (uuid.uuid4(), "organization", ids["org"]))
        connection.execute(cell, (uuid.uuid4(), "personal", None))
        for scope, organization in (("organization", ids["org"]), ("personal", None)):
            with pytest.raises(psycopg.errors.UniqueViolation):
                connection.execute(cell, (uuid.uuid4(), scope, organization))


def test_the_downgrade_drops_the_tables_and_the_audit_indexes(database):
    _alembic(database["url"], "downgrade", BEFORE)
    with psycopg.connect(database["raw"]) as connection:
        assert connection.execute("SELECT to_regclass('symbol_usage_events')").fetchone()[0] is None
        assert connection.execute("SELECT to_regclass('symbol_usage_daily_rollups')").fetchone()[0] is None
    assert not {"ix_audit_events_created_at", "ix_audit_events_entity"} & set(_indexes(database["raw"], "audit_events"))
    _alembic(database["url"], "upgrade", MIGRATION)
