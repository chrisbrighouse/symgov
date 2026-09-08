"""Exercise the two-role database privilege model as a real, logged-in
`symgov_app`, against a disposable PostgreSQL container.

**Why this file exists.** The migrations construct a deliberate privilege
split: `symgov_app` runs the application with narrow grants, while a
separate privileged role (`symgov_migrator` in production) owns the
schema and runs DDL. `20260810_0028` makes the membership and role
history tables append-only for `symgov_app` -- `GRANT SELECT, INSERT`
followed by `REVOKE UPDATE, DELETE, TRUNCATE`.

**That split was never exercised by any test.** Every other PostgreSQL
test points `SYMGOV_DATABASE_URL` and `SYMGOV_MIGRATION_DATABASE_URL` at
the *same* superuser connection (see
`test_f0_5_postgresql_security.py:58-60`), and the shared `_database`
helper creates `symgov_app` with `CREATE ROLE symgov_app` -- **no
`LOGIN`** -- so the role exists purely as a target for `GRANT`
statements and nothing ever connects as it. The grants were created but
never used.

Three separate production failures on 2026-09-07 came out of that blind
spot:

1. `alembic upgrade` as the app role: `permission denied for schema
   public` -- migrations need the migration role.
2. `bootstrap-symgov-organization --apply` as the app role: `permission
   denied for table organization_memberships` -- it takes
   `SELECT ... FOR UPDATE` on an append-only table.
3. Every organization-scoped request (`POST /org/me/projects` and
   friends): the same denial from `require_stage4_principal`, which takes
   `SELECT ... FOR SHARE` on `organization_memberships` and
   `organization_role_assignments`. PostgreSQL requires UPDATE privilege
   for *any* row-locking clause, `FOR SHARE` included. Fixed by
   `20260907_0045`.

The tests below connect as a genuinely logged-in `symgov_app` so that
each of those failures is reproducible here rather than in production.

Redaction: this file never prints the disposable container's connection
string or role passwords.
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

from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL  # noqa: E402

CURRENT_HEAD = "20260908_0046"

# The four tables 20260810_0028 makes append-only for symgov_app.
HISTORY_TABLES = (
    "organization_memberships",
    "organization_role_assignments",
    "organization_member_capabilities",
    "platform_role_assignments",
)

# Exactly the rows require_stage4_principal locks on every organization
# request (stage4_authorization.py), in the same order.
AUTHORIZATION_LOCKS = (
    "SELECT id FROM users WHERE id = %s FOR SHARE",
    "SELECT id FROM organizations WHERE id = %s FOR SHARE",
    "SELECT id FROM organization_memberships WHERE user_id = %s AND status = 'active' FOR SHARE",
    "SELECT id FROM organization_role_assignments WHERE is_active AND revoked_at IS NULL FOR SHARE",
)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def _alembic(url: str, *args: str, migration_role: bool = True):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": url,
        "SYMGOV_MIGRATION_DATABASE_URL": url,
    }
    if migration_role:
        env["SYMGOV_ALEMBIC_USE_MIGRATION_DB"] = "1"
    return subprocess.run(
        ["alembic", *args], cwd=BACKEND, env=env, check=True,
        capture_output=True, text=True, timeout=600,
    )


def _management(app_url: str, migration_url: str, *args: str, use_migration_role: bool):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": app_url,
        "SYMGOV_MIGRATION_DATABASE_URL": migration_url,
    }
    if use_migration_role:
        env["SYMGOV_ALEMBIC_USE_MIGRATION_DB"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "symgov_backend.management", *args],
        cwd=BACKEND, env=env, check=False, capture_output=True, text=True, timeout=300,
    )


@contextmanager
def _role_split_database():
    if not _docker("info", check=False).returncode == 0:
        pytest.skip("Docker is required for the two-role privilege rehearsal")

    name = f"symgov-role-split-{uuid.uuid4().hex[:12]}"
    superuser_password = f"disposable-{uuid.uuid4().hex}"
    app_password = f"disposable-app-{uuid.uuid4().hex}"

    _docker(
        "run", "--rm", "--detach", "--name", name,
        "--env", f"POSTGRES_PASSWORD={superuser_password}",
        "--env", "POSTGRES_DB=symgov_roles",
        "--publish", "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        privileged_raw = f"postgresql://postgres:{superuser_password}@127.0.0.1:{port}/symgov_roles"
        app_raw = f"postgresql://symgov_app:{app_password}@127.0.0.1:{port}/symgov_roles"

        deadline = time.monotonic() + 90
        while True:
            try:
                with psycopg.connect(privileged_raw, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

        with psycopg.connect(privileged_raw, autocommit=True) as connection:
            # The distinguishing detail: LOGIN. The shared _database helper
            # creates this role without it, which is why nothing has ever
            # connected as the application role.
            connection.execute(f"CREATE ROLE symgov_app LOGIN PASSWORD '{app_password}'")

        _alembic(privileged_raw.replace("postgresql://", "postgresql+psycopg://", 1),
                 "upgrade", CURRENT_HEAD)

        # The protected owner must exist and be active before bootstrap.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        owner_id = uuid.uuid4()
        with psycopg.connect(privileged_raw, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (%s,%s,%s,'test',%s,false,true,%s,%s)",
                (owner_id, PROTECTED_OWNER_EMAIL, "Protected Owner", now, now, now),
            )

        yield {
            "app_raw": app_raw,
            "app_url": app_raw.replace("postgresql://", "postgresql+psycopg://", 1),
            "privileged_url": privileged_raw.replace("postgresql://", "postgresql+psycopg://", 1),
            "privileged_raw": privileged_raw,
            "owner_id": owner_id,
        }
    finally:
        _docker("rm", "--force", name, check=False)


@pytest.fixture(scope="module")
def role_split_database():
    with _role_split_database() as context:
        yield context


def test_the_app_role_is_genuinely_unprivileged_for_ddl(role_split_database):
    """Sanity check that the split is real: if the app role could run DDL,
    every other assertion in this file would be vacuous."""
    with psycopg.connect(role_split_database["app_raw"], autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE privilege_probe (id int)")


def test_bootstrap_as_the_app_role_is_refused(role_split_database):
    """Reproduces the 2026-09-07 step 8 failure: the bootstrap takes
    SELECT ... FOR UPDATE on append-only tables, which the app role cannot
    do. It must be run with the migration role."""
    result = _management(
        role_split_database["app_url"],
        role_split_database["privileged_url"],
        "bootstrap-symgov-organization", "--apply",
        use_migration_role=False,
    )
    assert result.returncode != 0
    assert "InsufficientPrivilege" in result.stderr or "permission denied" in result.stderr

    with psycopg.connect(role_split_database["privileged_raw"]) as connection:
        count = connection.execute("SELECT count(*) FROM organizations").fetchone()[0]
    assert count == 0, "a refused bootstrap must not leave a partial organization"


def test_bootstrap_with_the_migration_role_succeeds(role_split_database):
    """The documented fix: SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 routes the
    command through SYMGOV_MIGRATION_DATABASE_URL (db.py:35-38), despite
    the Alembic-flavoured name."""
    result = _management(
        role_split_database["app_url"],
        role_split_database["privileged_url"],
        "bootstrap-symgov-organization", "--apply",
        use_migration_role=True,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    with psycopg.connect(role_split_database["privileged_raw"]) as connection:
        assert connection.execute("SELECT count(*) FROM organizations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM organization_memberships"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM platform_role_assignments WHERE role='platform_admin' AND is_active"
        ).fetchone()[0] == 1


def test_app_role_can_take_every_row_lock_the_authorization_path_requires(role_split_database):
    """The regression that matters most: this is the exact failure that
    broke every organization-scoped request in production on 2026-09-07.

    `require_stage4_principal` resolves each request by locking four rows
    with `SELECT ... FOR SHARE`. PostgreSQL requires UPDATE privilege for
    any row lock, so before 20260907_0045 the two append-only tables
    raised `permission denied for table organization_memberships` and the
    route returned 500."""
    owner_id = role_split_database["owner_id"]
    with psycopg.connect(role_split_database["app_raw"]) as connection:
        organization_id = connection.execute(
            "SELECT id FROM organizations WHERE normalized_code = 'symgov'"
        ).fetchone()[0]
        parameters = (owner_id, organization_id, owner_id, None)
        for statement, parameter in zip(AUTHORIZATION_LOCKS, parameters):
            if parameter is None:
                connection.execute(statement)
            else:
                connection.execute(statement, (parameter,))
        connection.rollback()


def test_history_tables_remain_protected_against_deletion(role_split_database):
    """20260907_0045 grants only UPDATE -- the minimum the row locks need.
    DELETE and TRUNCATE stay revoked, so the application still cannot
    erase membership or role history."""
    with psycopg.connect(role_split_database["app_raw"]) as connection:
        for table in HISTORY_TABLES:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"DELETE FROM {table}")
            connection.rollback()


def test_app_role_privilege_matrix_matches_the_intended_model(role_split_database):
    """Pin the whole grant model so an accidental widening is visible in a
    diff rather than discovered in production."""
    with psycopg.connect(role_split_database["app_raw"]) as connection:
        for table in HISTORY_TABLES:
            row = connection.execute(
                "SELECT has_table_privilege('symgov_app', %s, 'SELECT'),"
                "       has_table_privilege('symgov_app', %s, 'INSERT'),"
                "       has_table_privilege('symgov_app', %s, 'UPDATE'),"
                "       has_table_privilege('symgov_app', %s, 'DELETE')",
                (table, table, table, table),
            ).fetchone()
            assert row == (True, True, True, False), f"unexpected privileges on {table}: {row}"
