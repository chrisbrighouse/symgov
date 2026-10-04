"""`20261004_0069` adds organization-scoped member roles.

Rehearsed in a disposable container: the table's checks and the partial unique
index are exercised directly, and the downgrade removes it.

Redaction: this file never prints the disposable container's connection
string or role passwords.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import URL

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from postgres_image import POSTGRES_IMAGE, POSTGRES_READY_TIMEOUT  # noqa: E402
from symgov_backend.models import OrganizationMemberRole  # noqa: E402
from test_organization_subscriptions_migration_postgresql import _seed_organizations  # noqa: E402
from test_two_role_privilege_model_postgresql import _alembic, _docker  # noqa: E402

BEFORE = "20261004_0068"
MIGRATION = "20261004_0069"
DATABASE = "symgov_org_member_roles_probe"


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the organization member roles migration rehearsal")

    name = f"symgov-org-roles-probe-{uuid.uuid4().hex[:12]}"
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
        with psycopg.connect(raw, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app NOLOGIN")
        url = URL.create(
            "postgresql+psycopg", username="postgres", password=password, host="127.0.0.1", port=port, database=DATABASE
        ).render_as_string(hide_password=False)
        _alembic(url, "upgrade", BEFORE)
        _seed_organizations(raw)
        yield {"raw": raw, "url": url}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _membership(raw: str) -> uuid.UUID:
    with psycopg.connect(raw) as connection:
        return connection.execute(
            "SELECT m.id FROM organization_memberships m JOIN organizations o ON o.id = m.organization_id"
            " WHERE o.code = 'ACME'"
        ).fetchone()[0]


def _insert(connection, membership_id, role, *, active=True):
    connection.execute(
        "INSERT INTO organization_member_roles (id, membership_id, role, is_active, assigned_at, revoked_at)"
        " VALUES (%s, %s, %s, %s, now(), %s)",
        (uuid.uuid4(), membership_id, role, active, None if active else datetime.now(timezone.utc)),
    )


def test_migration_adds_an_empty_table_whose_check_names_match_the_orm(database):
    raw = database["raw"]
    _alembic(database["url"], "upgrade", MIGRATION)
    assert psycopg.connect(raw).execute("SELECT count(*) FROM organization_member_roles").fetchone()[0] == 0
    expected = {c.name for c in OrganizationMemberRole.__table__.constraints if c.__class__.__name__ == "CheckConstraint"}
    live = {
        row[0]
        for row in psycopg.connect(raw).execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid = 'organization_member_roles'::regclass"
        ).fetchall()
    }
    assert live == expected and all(len(name) <= 63 for name in live)


def test_the_database_enforces_the_role_vocabulary_and_one_active_row_per_role(database):
    raw = database["raw"]
    membership_id = _membership(raw)
    with psycopg.connect(raw, autocommit=True) as connection:
        for refused in ("admin", "owner"):
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert(connection, membership_id, refused)
        _insert(connection, membership_id, "submitter")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(connection, membership_id, "submitter")
        _insert(connection, membership_id, "submitter", active=False)  # a revoked history row is allowed
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO organization_member_roles (id, membership_id, role, is_active, assigned_at)"
                " VALUES (%s, %s, 'reviewer', false, now())",
                (uuid.uuid4(), membership_id),
            )  # inactive without revoked_at


def test_downgrade_drops_the_table_and_reupgrade_restores_it(database):
    raw = database["raw"]
    _alembic(database["url"], "downgrade", BEFORE)
    assert psycopg.connect(raw).execute("SELECT to_regclass('organization_member_roles') IS NULL").fetchone()[0]
    _alembic(database["url"], "upgrade", MIGRATION)
    assert psycopg.connect(raw).execute("SELECT count(*) FROM organization_member_roles").fetchone()[0] == 0
