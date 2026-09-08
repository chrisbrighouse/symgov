"""Prove a production dump survives an ordinary `pg_restore`.

**Why this file exists.** The 2026-09-08 deployment's mandated
backup-restore check failed. A plain `pg_restore` of the pre-migration
production dump exited non-zero having restored `projects` with 0 of 1
rows, while every other table came back intact -- the kind of partial
success that is easy to accept as "3 harmless errors" during an
emergency recovery.

The cause is `stage4_jsonb_max_depth` (20260822_0030): a recursive SQL
function calling itself unqualified, with no `SET search_path`.
`pg_dump` writes `set_config('search_path', '', false)`, so the
`ck_projects_metadata_bounds` CHECK constraint cannot resolve the
inlined self-call while `COPY` loads the table. 20260908_0046 pins the
function's own search_path.

The dump/restore round trip was never covered by any test -- the
migration rehearsal (WP11.7) exercises `alembic upgrade`/`downgrade`,
which never evaluates a CHECK constraint against restored data under
pg_dump's empty search_path.

Both tests run `pg_dump`/`pg_restore` *inside* the disposable container
so the client binaries always match the server, and neither depends on
what is installed on the host.

Redaction: this file never prints the disposable container's connection
string or role password, and the password is passed via PGPASSWORD /
`psycopg.connect(password=...)` rather than embedded in any URL.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CURRENT_HEAD = "20260908_0046"
# The revision immediately before the fix, used to prove these tests
# actually detect the defect rather than passing vacuously.
PRE_FIX_REVISION = "20260907_0045"

DUMP_PATH = "/tmp/symgov-roundtrip.dump"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def _alembic(url: str, password: str, *args: str):
    env = {
        "PATH": os.environ.get("PATH", ""),
        # libpq reads PGPASSWORD, so the credential never enters the URL.
        "PGPASSWORD": password,
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": url,
        "SYMGOV_MIGRATION_DATABASE_URL": url,
        "SYMGOV_ALEMBIC_USE_MIGRATION_DB": "1",
    }
    return subprocess.run(
        ["alembic", *args], cwd=BACKEND, env=env, check=True,
        capture_output=True, text=True, timeout=600,
    )


def _seed_project(raw_url: str, password: str) -> None:
    """One project row whose metadata_json is nested deeply enough that the
    recursive function is genuinely exercised by the CHECK constraint."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user_id, org_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(raw_url, password=password, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
            "must_change_pin,is_active,created_at,updated_at) "
            "VALUES (%s,%s,%s,'test',%s,false,true,%s,%s)",
            (user_id, f"restore-{user_id.hex[:8]}@example.test", "Restore Probe", now, now, now),
        )
        # is_active = false deliberately: an *active* organization must
        # retain an Organization Administrator
        # (enforce_active_organization_admin_minimum), and a full membership
        # and role-assignment fixture would add nothing here. This test needs
        # only a projects row to survive a dump/restore.
        connection.execute(
            "INSERT INTO organizations (id,code,normalized_code,display_name,name_key,"
            "fallback_icon_svg,is_protected,is_active,created_at,updated_at) "
            "VALUES (%s,'ACME','acme','Acme','acme','<svg/>',false,false,%s,%s)",
            (org_id, now, now),
        )
        connection.execute(
            "INSERT INTO projects (id,organization_id,code,normalized_code,name,"
            "metadata_json,created_by_user_id,created_at,updated_at) "
            "VALUES (%s,%s,'P1','p1','Restore Probe',"
            "'{\"a\": {\"b\": [1, 2]}}'::jsonb,%s,%s,%s)",
            (project_id, org_id, user_id, now, now),
        )


@contextmanager
def _dump_restore_container(target_revision: str):
    if not _docker("info", check=False).returncode == 0:
        pytest.skip("Docker is required for the dump/restore round trip")

    name = f"symgov-roundtrip-{uuid.uuid4().hex[:12]}"
    password = f"disposable-{uuid.uuid4().hex}"
    _docker(
        "run", "--rm", "--detach", "--name", name,
        "--env", f"POSTGRES_PASSWORD={password}",
        "--env", "POSTGRES_DB=symgov_src",
        "--publish", "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw = f"postgresql://postgres@127.0.0.1:{port}/symgov_src"

        deadline = time.monotonic() + 90
        while True:
            try:
                with psycopg.connect(raw, password=password, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

        # The migrations GRANT to this role from 20260810_0028 onward; it
        # needs no LOGIN here, only to exist as a grant target.
        with psycopg.connect(raw, password=password, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app")

        _alembic(raw.replace("postgresql://", "postgresql+psycopg://", 1),
                 password, "upgrade", target_revision)
        _seed_project(raw, password)

        # Dump and restore inside the container, exactly as an operator
        # recovering this database would.
        _docker("exec", name, "pg_dump", "-U", "postgres", "-d", "symgov_src",
                "-Fc", "-f", DUMP_PATH)
        _docker("exec", name, "psql", "-U", "postgres", "-d", "postgres",
                "-c", "CREATE DATABASE symgov_dst")
        restore = _docker("exec", name, "pg_restore", "-U", "postgres",
                          "-d", "symgov_dst", "--no-owner", "--no-privileges",
                          DUMP_PATH, check=False)

        restored = f"postgresql://postgres@127.0.0.1:{port}/symgov_dst"
        with psycopg.connect(restored, password=password) as connection:
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("projects", "organizations", "users")
            }
        with psycopg.connect(raw, password=password) as connection:
            proconfig = connection.execute(
                "SELECT proconfig FROM pg_proc WHERE proname = 'stage4_jsonb_max_depth'"
            ).fetchone()[0]

        yield {"restore": restore, "counts": counts, "proconfig": proconfig}
    finally:
        _docker("rm", "--force", name, check=False)


@pytest.fixture(scope="module")
def head_roundtrip():
    with _dump_restore_container(CURRENT_HEAD) as result:
        yield result


def test_plain_restore_loses_projects_before_the_fix():
    """The defect itself, pinned to the revision before the fix. `projects`
    is silently emptied while its neighbours restore fine -- the asymmetry
    is what made this survive a deployment's backup verification."""
    with _dump_restore_container(PRE_FIX_REVISION) as result:
        assert result["restore"].returncode != 0
        assert "stage4_jsonb_max_depth" in result["restore"].stderr
        assert result["counts"]["projects"] == 0
        assert result["counts"]["organizations"] == 1
        assert result["counts"]["users"] == 1


def test_plain_restore_round_trips_at_head(head_roundtrip):
    """20260908_0046: an ordinary pg_restore, with no operator
    intervention and no --section juggling, preserves every row."""
    assert head_roundtrip["restore"].returncode == 0, head_roundtrip["restore"].stderr
    assert head_roundtrip["restore"].stderr.strip() == ""
    assert head_roundtrip["counts"] == {"projects": 1, "organizations": 1, "users": 1}


def test_the_function_carries_an_explicit_search_path_at_head(head_roundtrip):
    """Guards the mechanism rather than the symptom: a later
    CREATE OR REPLACE of this function that drops the setting reopens the
    restore hole, and fails here even if no dump is taken."""
    assert head_roundtrip["proconfig"] == ["search_path=public, pg_catalog"]
