"""`20261014_0072` adds key holders and rate windows, and retires ownerless keys.

Rehearsed in a disposable container against rows that exist before the
migration: an owned key becomes its owner's personal key, an ownerless active
key is revoked with one audit event, an already-revoked ownerless key is left
alone, the constraints enforce the kind/organization pairing, the limiter's
upsert counts on real PostgreSQL, and the downgrade drops the tables without
un-revoking anything.

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

BEFORE = "20261010_0071"
MIGRATION = "20261014_0072"
DATABASE = "symgov_catalog_api_key_holders_probe"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the API key holders migration rehearsal")

    name = f"symgov-api-key-holders-probe-{uuid.uuid4().hex[:12]}"
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


def _insert_fixtures(raw: str) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("owner", "owned", "ownerless", "already_revoked")}
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
            " VALUES (%s, 'owner@example.test', 'Owner', 'x', now(), now(), now())",
            (ids["owner"],),
        )
        for name, owner, status in (
            ("owned", ids["owner"], "active"),
            ("ownerless", None, "active"),
            ("already_revoked", None, "revoked"),
        ):
            connection.execute(
                "INSERT INTO catalog_api_keys (id, customer_name, integration_name, key_prefix, key_hash,"
                " scopes_json, status, created_by, created_at, updated_at, revoked_at)"
                " VALUES (%s, 'Acme', %s, %s, %s, '[\"catalog.read\"]', %s, %s, now(), now(),"
                " CASE WHEN %s = 'revoked' THEN now() END)",
                (ids[name], name, f"symgov_live_{name[:6]}", uuid.uuid4().hex, status, owner, status),
            )
    return ids


def test_existing_keys_are_given_holders_and_ownerless_active_keys_are_revoked(database):
    ids = _insert_fixtures(database["raw"])

    _alembic(database["url"], "upgrade", MIGRATION)

    with psycopg.connect(database["raw"]) as connection:
        holders = connection.execute(
            "SELECT api_key_id, key_kind, holder_user_id, organization_id, claimed_at IS NOT NULL"
            " FROM catalog_api_key_holders"
        ).fetchall()
        assert holders == [(ids["owned"], "personal", ids["owner"], None, True)]
        statuses = dict(connection.execute("SELECT integration_name, status FROM catalog_api_keys").fetchall())
        assert statuses == {"owned": "active", "ownerless": "revoked", "already_revoked": "revoked"}
        events = connection.execute(
            "SELECT entity_id, action, payload_json ->> 'reason' FROM audit_events"
            " WHERE entity_type = 'catalog_api_key'"
        ).fetchall()
        assert events == [(ids["ownerless"], "catalog_api_key.revoked", "legacy_key_without_owner")]


def test_the_constraints_pair_the_kind_with_the_organization_and_cascade_with_the_key(database):
    with psycopg.connect(database["raw"], autocommit=True) as connection:
        owner = connection.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        key = connection.execute("SELECT id FROM catalog_api_keys WHERE integration_name = 'owned'").fetchone()[0]
        organization = uuid.uuid4()
        connection.execute(
            "INSERT INTO organizations (id, code, normalized_code, display_name, name_key, fallback_icon_svg, is_active,"
            " created_at, updated_at) VALUES (%s, 'ACME', 'acme', 'Acme', 'acme', '<svg/>', false, now(), now())",
            (organization,),
        )
        second = uuid.uuid4()
        connection.execute(
            "INSERT INTO catalog_api_keys (id, customer_name, integration_name, key_prefix, key_hash, scopes_json,"
            " created_by, created_at, updated_at) VALUES (%s, 'Acme', 'org', 'symgov_live_org', %s, '[]', %s, now(), now())",
            (second, uuid.uuid4().hex, owner),
        )
        for kind, organization_id in (("personal", organization), ("organization", None), ("shared", None)):
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "INSERT INTO catalog_api_key_holders (api_key_id, key_kind, holder_user_id, organization_id)"
                    " VALUES (%s, %s, %s, %s)",
                    (second, kind, owner, organization_id),
                )
        connection.execute(
            "INSERT INTO catalog_api_key_holders (api_key_id, key_kind, holder_user_id, organization_id)"
            " VALUES (%s, 'organization', %s, %s)",
            (second, owner, organization),
        )
        connection.execute("DELETE FROM catalog_api_keys WHERE id = %s", (second,))
        assert connection.execute(
            "SELECT count(*) FROM catalog_api_key_holders WHERE api_key_id = %s", (second,)
        ).fetchone()[0] == 0
        assert key is not None


def test_the_limiter_counts_with_a_real_upsert(database):
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from symgov_backend.catalog_api_auth import _count_request

    with psycopg.connect(database["raw"]) as connection:
        key = connection.execute("SELECT id FROM catalog_api_keys WHERE integration_name = 'owned'").fetchone()[0]
    engine = create_engine(database["url"])
    window = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    with sessionmaker(bind=engine)() as session:
        counts = [_count_request(session, str(key), window) for _ in range(3)]
        later = _count_request(session, str(key), datetime(2026, 10, 16, 12, 1, tzinfo=timezone.utc))
        stale = _count_request(session, str(key), datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc))
        session.commit()
        assert counts == [1, 2, 3] and later == 1 and stale == 1
        assert session.execute(
            __import__("sqlalchemy").text("SELECT count(*) FROM catalog_api_rate_windows")
        ).scalar_one() == 1
    engine.dispose()


def test_the_downgrade_drops_the_tables_and_leaves_revoked_keys_revoked(database):
    _alembic(database["url"], "downgrade", BEFORE)

    with psycopg.connect(database["raw"]) as connection:
        assert connection.execute("SELECT to_regclass('catalog_api_key_holders')").fetchone()[0] is None
        assert connection.execute("SELECT to_regclass('catalog_api_rate_windows')").fetchone()[0] is None
        statuses = dict(connection.execute("SELECT integration_name, status FROM catalog_api_keys").fetchall())
        assert statuses["ownerless"] == "revoked"

    _alembic(database["url"], "upgrade", MIGRATION)
