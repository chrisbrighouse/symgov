"""`20261004_0068` creates the organization subscription tables and seeds the
existing non-protected organizations with the default plan.

Rehearsed in a disposable container: organizations are seeded at the previous
head, so the backfill runs against real rows.

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
from symgov_backend.models import OrganizationSubscription, OrganizationSubscriptionEvent  # noqa: E402
from symgov_backend.organization_subscriptions import DEFAULT_SEAT_LIMIT, DEFAULT_TERM_MONTHS  # noqa: E402
from test_two_role_privilege_model_postgresql import _alembic, _docker  # noqa: E402

BEFORE = "20260930_0067"
MIGRATION = "20261004_0068"
DATABASE = "symgov_org_subscriptions_probe"
EXISTING = ("ACME", "BIRSCO", "CROSSWELL")


def _scalar(raw: str, sql: str, *params):
    with psycopg.connect(raw) as connection:
        return connection.execute(sql, params).fetchone()[0]


def _seed_organizations(raw: str) -> None:
    """Each organization gets an admin in the same transaction: a deferred trigger
    refuses to commit an active organization without one."""
    with psycopg.connect(raw) as connection:
        has_symgov = connection.execute("SELECT count(*) FROM organizations WHERE code = 'symgov'").fetchone()[0]
        rows = [(code, code.lower(), code.title(), False) for code in EXISTING]
        if not has_symgov:
            rows.append(("symgov", "symgov", "Symgov", True))
        for code, normalized, display, protected in rows:
            user_id, org_id, membership_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(
                "INSERT INTO users (id, email, display_name, pin_hash, pin_set_at, created_at, updated_at)"
                " VALUES (%s, %s, %s, 'x', now(), now(), now())",
                (user_id, f"admin@{normalized}.example", f"{display} Admin"),
            )
            connection.execute(
                "INSERT INTO organizations (id, code, normalized_code, display_name, name_key,"
                " is_protected, fallback_icon_svg, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, '<svg/>', now(), now())",
                (org_id, code, normalized, display, normalized, protected),
            )
            connection.execute(
                "INSERT INTO organization_memberships (id, organization_id, user_id, status, activated_at,"
                " created_at, updated_at) VALUES (%s, %s, %s, 'active', now(), now(), now())",
                (membership_id, org_id, user_id),
            )
            connection.execute(
                "INSERT INTO organization_role_assignments (id, membership_id, base_role, is_active, assigned_at)"
                " VALUES (%s, %s, 'admin', true, now())",
                (uuid.uuid4(), membership_id),
            )
            if protected:
                connection.execute(
                    "INSERT INTO platform_role_assignments (id, user_id, role, is_active, assigned_at)"
                    " VALUES (%s, %s, 'platform_admin', true, now())",
                    (uuid.uuid4(), user_id),
                )
        connection.commit()


@pytest.fixture(scope="module")
def database():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the organization subscriptions migration rehearsal")

    name = f"symgov-org-subs-probe-{uuid.uuid4().hex[:12]}"
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


def test_backfill_seeds_existing_organizations_and_skips_the_protected_one(database):
    raw = database["raw"]
    _alembic(database["url"], "upgrade", MIGRATION)

    seeded = psycopg.connect(raw).execute(
        "SELECT o.code, s.seat_limit, s.started_on = current_date,"
        " s.expires_on = (s.started_on + interval '12 months')::date, s.version"
        " FROM organization_subscriptions s JOIN organizations o ON o.id = s.organization_id"
        " ORDER BY o.code"
    ).fetchall()
    assert seeded == [(code, 25, True, True, 1) for code in EXISTING]
    assert (DEFAULT_SEAT_LIMIT, DEFAULT_TERM_MONTHS) == (25, 12)  # SQL literals mirror the constants

    events = psycopg.connect(raw).execute(
        "SELECT action, actor_id IS NULL, new_seat_limit FROM organization_subscription_events"
    ).fetchall()
    assert events == [("created", True, 25)] * len(EXISTING)
    assert _scalar(
        raw,
        "SELECT count(*) FROM organization_subscriptions s JOIN organizations o ON o.id = s.organization_id"
        " WHERE o.is_protected",
    ) == 0


def test_constraint_names_match_the_orm_and_are_not_truncated(database):
    raw = database["raw"]
    expected = {
        c.name
        for table in (OrganizationSubscription.__table__, OrganizationSubscriptionEvent.__table__)
        for c in table.constraints
        if c.name and c.__class__.__name__ == "CheckConstraint"
    }
    live = {
        row[0]
        for row in psycopg.connect(raw).execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid IN"
            " ('organization_subscriptions'::regclass, 'organization_subscription_events'::regclass)"
        ).fetchall()
    }
    assert live == expected
    assert all(len(name) <= 63 for name in live)


def test_database_rejects_bad_rows(database):
    raw = database["raw"]
    org_id = _scalar(raw, "SELECT id FROM organizations WHERE code = 'ACME'")
    with psycopg.connect(raw, autocommit=True) as connection:
        for seat_limit, expires in ((0, "2099-01-01"), (5, "1999-01-01")):
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "UPDATE organization_subscriptions SET seat_limit = %s, expires_on = %s::date"
                    " WHERE organization_id = %s",
                    (seat_limit, expires, org_id),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO organization_subscription_events (id, organization_id, action, new_seat_limit,"
                " new_expires_on, created_at) VALUES (%s, %s, 'bogus', 1, '2099-01-01', now())",
                (uuid.uuid4(), org_id),
            )


def test_downgrade_drops_the_tables_and_a_reupgrade_reseeds(database):
    raw = database["raw"]
    _alembic(database["url"], "downgrade", BEFORE)
    assert _scalar(raw, "SELECT to_regclass('organization_subscriptions') IS NULL")
    assert _scalar(raw, "SELECT to_regclass('organization_subscription_events') IS NULL")

    _alembic(database["url"], "upgrade", MIGRATION)
    assert _scalar(raw, "SELECT count(*) FROM organization_subscriptions") == len(EXISTING)
    # Running the seed again must not duplicate or fail.
    with psycopg.connect(raw, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO organization_subscriptions (organization_id, seat_limit, started_on, expires_on,"
            " version, created_at, updated_at)"
            " SELECT id, 25, current_date, (current_date + interval '12 months')::date, 1, now(), now()"
            " FROM organizations WHERE is_protected = false ON CONFLICT (organization_id) DO NOTHING"
        )
    assert _scalar(raw, "SELECT count(*) FROM organization_subscriptions") == len(EXISTING)
