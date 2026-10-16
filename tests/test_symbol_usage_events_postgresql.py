"""The usage maintenance pass against real PostgreSQL (WP-B).

A whole pass over rows written before it: symbol events roll up and the old
ones are purged after being counted, the Stage 9 rollups (raw `ON CONFLICT`
SQL) are refreshed for the first time, old rate windows go, and the advisory
lock makes a second pass skip.

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
DATABASE = "symgov_usage_maintenance_probe"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the symbol usage migration rehearsal")

    name = f"symgov-usage-maintenance-probe-{uuid.uuid4().hex[:12]}"
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
        _alembic(url, "upgrade", MIGRATION)
        yield {"raw": raw, "url": url}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def test_a_full_pass_rolls_up_purges_refreshes_stage9_and_sweeps(database):
    import uuid
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from sqlalchemy import create_engine, text

    from symgov_backend.usage_maintenance_worker import maintain_once

    now = datetime.now(timezone.utc).replace(microsecond=0)
    ids = {name: uuid.uuid4() for name in ("user", "org", "key")}
    old, recent = now - timedelta(days=70), now - timedelta(hours=3)
    with psycopg.connect(database["raw"], autocommit=True) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
            " VALUES (%s, 'maint@example.test', 'Maint', 'x', now(), now(), now())", (ids["user"],),
        )
        connection.execute(
            "INSERT INTO organizations (id, code, normalized_code, display_name, name_key, fallback_icon_svg, is_active, created_at, updated_at)"
            " VALUES (%s, 'MAINT', 'maint', 'Maint', 'maint', '<svg/>', false, now(), now())", (ids["org"],),
        )
        connection.execute(
            "INSERT INTO catalog_api_keys (id, customer_name, integration_name, key_prefix, key_hash, scopes_json, created_by, created_at, updated_at)"
            " VALUES (%s, 'c', 'i', 'p', %s, '[]', %s, now(), now())", (ids["key"], uuid.uuid4().hex, ids["user"]),
        )
        for when, count in ((old, 2), (recent, 3)):
            for _ in range(count):
                connection.execute(
                    "INSERT INTO symbol_usage_events (id, occurred_at, event_type, channel, scope_kind, user_id, organization_id, pack_code)"
                    " VALUES (%s, %s, 'symbol_viewed', 'web', 'organization', %s, %s, '0001')", (uuid.uuid4(), when, ids["user"], ids["org"]),
                )
        connection.execute(
            "INSERT INTO product_usage_events (id, event_type, occurred_at, session_mode, user_id, organization_id)"
            " VALUES (%s, 'symbol_previewed', %s, 'organization', %s, %s)", (uuid.uuid4(), recent, ids["user"], ids["org"]),
        )
        connection.execute("INSERT INTO catalog_api_rate_windows VALUES (%s, %s, 5)", (ids["key"], now - timedelta(days=3)))
        connection.execute("INSERT INTO catalog_api_rate_windows VALUES (%s, %s, 2)", (ids["key"], now - timedelta(minutes=1)))

    engine = create_engine(database["url"])
    settings = SimpleNamespace(db_env_file=None)
    result = maintain_once(settings, engine=engine)
    assert result.status == "ran"
    counts = result.counts
    assert (counts.purged_symbol_events, counts.purged_rate_windows, counts.product_rollup_cells) == (2, 1, 1)

    with psycopg.connect(database["raw"]) as connection:
        cells = connection.execute(
            "SELECT occurred_on, event_count FROM symbol_usage_daily_rollups WHERE organization_id = %s ORDER BY occurred_on", (ids["org"],)
        ).fetchall()
        assert [count for _, count in cells] == [2, 3] and cells[0][0] == old.date()
        assert connection.execute("SELECT count(*) FROM symbol_usage_events WHERE organization_id = %s", (ids["org"],)).fetchone()[0] == 3
        assert connection.execute(
            "SELECT event_count, distinct_user_count FROM product_usage_daily_rollups WHERE organization_id = %s", (ids["org"],)
        ).fetchone() == (1, 1)
        assert connection.execute("SELECT count(*) FROM catalog_api_rate_windows WHERE api_key_id = %s", (ids["key"],)).fetchone()[0] == 1

    # A second pass changes nothing, and while one holds the lock another skips.
    again = maintain_once(settings, engine=engine)
    assert again.status == "ran" and again.counts.purged_symbol_events == 0
    from symgov_backend.usage_maintenance_worker import _ADVISORY_LOCK_KEY

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}).scalar()
        assert maintain_once(settings, engine=engine).status == "skipped_locked"
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    engine.dispose()
