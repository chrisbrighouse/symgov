"""Stage 9 WP9.4 regression: the `product_usage_daily_rollups` aggregate
table, its `refresh_product_usage_rollups`/`get_organization_usage_summary`
service functions, and the real Organization Admin/Platform Admin dashboard
HTTP endpoints, against a real disposable PostgreSQL container.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.4/§4 Q7) and the periodic-batch-refresh design Chris confirmed for this
package specifically (over a synchronous-incremental alternative):

- The rollup is a standalone, callable batch job (`refresh_product_usage_rollups`,
  mirroring WP9.1's own unscheduled `purge_expired_product_usage_events`),
  not wired to any scheduler here.
- Dashboard reads (`get_organization_usage_summary`, and the two HTTP routes
  wrapping it) query only the rollup table, never raw `product_usage_events`
  rows -- proven here by the retention-survival test, which purges the raw
  rows a rollup was built from and shows the rollup, and the dashboard
  response built from it, are completely unaffected.
- The confirmed 3-distinct-user minimum aggregation threshold is enforced
  by the read path: any day-cell with `distinct_user_count < 3` is omitted
  from the response entirely (not just its user count masked), counted only
  in that event type's own `suppressedDayCount`.
- `GET /org/me/usage-summary` (Organization Admin, self-scoped -- there is
  no `organizationId` path/query parameter to spoof) and
  `GET /platform/organizations/{organizationId}/usage-summary` (Platform
  Admin, any organization) both return the identical shape for the same
  organization and date range.

Each test uses its own freshly generated organization code(s) (never a
shared literal like `"acme"`) -- this file's own tests aggregate real usage
*by organization*, so two tests sharing one organization would leak each
other's rollup cells into the same dashboard query whenever their event
types/dates happened to fall in the same queried window.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles, _make_platform_admin  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import ProductUsageDailyRollup, ProductUsageEvent, User  # noqa: E402
from symgov_backend.product_usage_retention import purge_expired_product_usage_events  # noqa: E402
from symgov_backend.product_usage_rollups import refresh_product_usage_rollups  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0040"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp94_database():
    with _database("symgov-wp94") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


def _unique_code(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def _client(engine, *, pilot_codes):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        platform_admin_enabled=True,
        organization_pilot_codes=tuple(pilot_codes),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), TestingSessionLocal


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response.json()


def _email(Session, user_id) -> str:
    with Session() as session:
        return session.get(User, user_id).email


def _create_user(Session, label: str) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    return _create_user_with_global_roles(
        Session, email=f"wp94{label}-{suffix}@example.test", display_name=f"WP9.4 {label} {suffix}", roles=[]
    )


def _seed_raw_event(Session, *, organization_id, event_type, occurred_at, user_id=None, **extra):
    if user_id is None:
        user_id = _create_user(Session, "raw")
    with Session() as session:
        session.add(
            ProductUsageEvent(
                id=uuid.uuid4(),
                event_type=event_type,
                occurred_at=occurred_at,
                session_mode="organization",
                user_id=user_id,
                organization_id=organization_id,
                **extra,
            )
        )
        session.commit()
    return user_id


def test_summary_endpoint_suppresses_cells_below_the_distinct_user_threshold(wp94_database):
    engine, _, _ = wp94_database
    code = _unique_code("acme")
    admin_client, Session = _client(engine, pilot_codes=(code,))

    admin_id = _create_user(Session, "admin")
    organization_id = _add_membership(Session, admin_id, code=code, base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    day_ok = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    day_suppressed = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

    # Three distinct users preview a symbol on day_ok -- meets the threshold.
    for _ in range(3):
        _seed_raw_event(
            Session, organization_id=organization_id, event_type="symbol_previewed", occurred_at=day_ok,
            symbol_source="public",
        )
    # Two distinct users download on day_suppressed -- below the threshold.
    for _ in range(2):
        _seed_raw_event(
            Session, organization_id=organization_id, event_type="symbol_downloaded", occurred_at=day_suppressed,
            symbol_source="public", format="png",
        )

    with Session() as session:
        touched = refresh_product_usage_rollups(session)
        session.commit()
    assert touched == 2

    response = admin_client.get(
        "/api/v1/org/me/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organizationId"] == str(organization_id)

    by_type = {item["eventType"]: item for item in body["eventTypes"]}
    assert by_type["symbol_previewed"]["days"] == [
        {"date": "2026-08-10", "eventCount": 3, "distinctUserCount": 3}
    ]
    assert by_type["symbol_previewed"]["suppressedDayCount"] == 0
    assert by_type["symbol_previewed"]["totalEventCount"] == 3

    assert by_type["symbol_downloaded"]["days"] == []
    assert by_type["symbol_downloaded"]["suppressedDayCount"] == 1
    assert by_type["symbol_downloaded"]["totalEventCount"] == 0


def test_organization_admin_only_sees_their_own_organizations_rollups(wp94_database):
    engine, _, _ = wp94_database
    acme_code = _unique_code("acme")
    other_code = _unique_code("other")
    acme_client, Session = _client(engine, pilot_codes=(acme_code, other_code))
    other_client, _ = _client(engine, pilot_codes=(acme_code, other_code))

    acme_admin_id = _create_user(Session, "acmeadmin")
    acme_org_id = _add_membership(Session, acme_admin_id, code=acme_code, base_role="admin")
    _login(acme_client, _email(Session, acme_admin_id))

    other_admin_id = _create_user(Session, "otheradmin")
    _add_membership(Session, other_admin_id, code=other_code, base_role="admin")
    _login(other_client, _email(Session, other_admin_id))

    day = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    for _ in range(3):
        _seed_raw_event(Session, organization_id=acme_org_id, event_type="symbol_previewed", occurred_at=day, symbol_source="public")

    with Session() as session:
        refresh_product_usage_rollups(session)
        session.commit()

    acme_response = acme_client.get("/api/v1/org/me/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"})
    assert acme_response.status_code == 200, acme_response.text
    assert any(item["eventType"] == "symbol_previewed" and item["totalEventCount"] == 3 for item in acme_response.json()["eventTypes"])

    other_response = other_client.get("/api/v1/org/me/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"})
    assert other_response.status_code == 200, other_response.text
    assert other_response.json()["eventTypes"] == []


def test_platform_admin_can_read_any_organizations_usage_summary(wp94_database):
    engine, _, _ = wp94_database
    acme_code = _unique_code("acme")
    acme_client, Session = _client(engine, pilot_codes=(acme_code,))
    platform_client, _ = _client(engine, pilot_codes=("symgov",))

    acme_admin_id = _create_user(Session, "platformviewadmin")
    acme_org_id = _add_membership(Session, acme_admin_id, code=acme_code, base_role="admin")
    _login(acme_client, _email(Session, acme_admin_id))

    platform_admin_id = _create_user(Session, "platformadmin")
    _make_platform_admin(Session, platform_admin_id)
    _login(platform_client, _email(Session, platform_admin_id))

    day = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    for _ in range(4):
        _seed_raw_event(Session, organization_id=acme_org_id, event_type="favorite_changed", occurred_at=day, favourite_action="added")

    with Session() as session:
        refresh_product_usage_rollups(session)
        session.commit()

    own_view = acme_client.get("/api/v1/org/me/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"})
    platform_view = platform_client.get(
        f"/api/v1/platform/organizations/{acme_org_id}/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"}
    )
    assert own_view.status_code == 200, own_view.text
    assert platform_view.status_code == 200, platform_view.text
    assert own_view.json()["eventTypes"] == platform_view.json()["eventTypes"]

    unknown_org_response = platform_client.get(
        f"/api/v1/platform/organizations/{uuid.uuid4()}/usage-summary", params={"since": "2026-08-01", "until": "2026-08-31"}
    )
    assert unknown_org_response.status_code == 404


def test_rollups_survive_raw_event_retention_purge(wp94_database):
    engine, _, _ = wp94_database
    code = _unique_code("acme")
    admin_client, Session = _client(engine, pilot_codes=(code,))

    admin_id = _create_user(Session, "purgeadmin")
    organization_id = _add_membership(Session, admin_id, code=code, base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    old_day = datetime.now(timezone.utc) - timedelta(days=120)
    for _ in range(3):
        _seed_raw_event(Session, organization_id=organization_id, event_type="set_created", occurred_at=old_day)

    with Session() as session:
        refresh_product_usage_rollups(session)
        session.commit()

    with Session() as session:
        rollup_before = session.query(ProductUsageDailyRollup).filter(
            ProductUsageDailyRollup.organization_id == organization_id,
            ProductUsageDailyRollup.event_type == "set_created",
        ).one()
    assert rollup_before.event_count == 3
    assert rollup_before.distinct_user_count == 3

    with Session() as session:
        raw_count_before = session.query(ProductUsageEvent).filter(ProductUsageEvent.organization_id == organization_id).count()
    assert raw_count_before == 3

    with Session() as session:
        deleted = purge_expired_product_usage_events(session)
        session.commit()
    assert deleted == 3

    with Session() as session:
        raw_count_after = session.query(ProductUsageEvent).filter(ProductUsageEvent.organization_id == organization_id).count()
    assert raw_count_after == 0

    with Session() as session:
        rollup_after = session.query(ProductUsageDailyRollup).filter(
            ProductUsageDailyRollup.organization_id == organization_id,
            ProductUsageDailyRollup.event_type == "set_created",
        ).one()
    assert rollup_after.event_count == 3
    assert rollup_after.distinct_user_count == 3

    since = old_day.date() - timedelta(days=1)
    until = old_day.date() + timedelta(days=1)
    response = admin_client.get(
        "/api/v1/org/me/usage-summary", params={"since": since.isoformat(), "until": until.isoformat()}
    )
    assert response.status_code == 200, response.text
    by_type = {item["eventType"]: item for item in response.json()["eventTypes"]}
    assert by_type["set_created"]["totalEventCount"] == 3


def test_summary_date_range_filtering_excludes_days_outside_the_window(wp94_database):
    engine, _, _ = wp94_database
    code = _unique_code("acme")
    admin_client, Session = _client(engine, pilot_codes=(code,))

    admin_id = _create_user(Session, "rangeadmin")
    organization_id = _add_membership(Session, admin_id, code=code, base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    inside = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for occurred_at in (inside, outside):
        for _ in range(3):
            _seed_raw_event(Session, organization_id=organization_id, event_type="project_created", occurred_at=occurred_at)

    with Session() as session:
        refresh_product_usage_rollups(session)
        session.commit()

    response = admin_client.get(
        "/api/v1/org/me/usage-summary", params={"since": "2026-06-01", "until": "2026-06-30"}
    )
    assert response.status_code == 200, response.text
    by_type = {item["eventType"]: item for item in response.json()["eventTypes"]}
    assert by_type["project_created"]["days"] == [{"date": "2026-06-15", "eventCount": 3, "distinctUserCount": 3}]
    assert by_type["project_created"]["totalEventCount"] == 3


def test_purge_refuses_to_delete_organization_scoped_rows_never_rolled_up(wp94_database):
    """`refresh_product_usage_rollups` is not wired to any scheduler (this
    file's own module docstring), so a production deployment must run it
    before `purge_expired_product_usage_events` ever does -- otherwise the
    purge would delete raw rows no rollup cell ever captured, silently and
    unrecoverably losing that day's aggregate forever. Rather than rely
    purely on that documented operational ordering,
    `purge_expired_product_usage_events` itself refuses to delete an
    organization-scoped row whose own (organization, event_type, day)
    rollup cell does not yet exist. `personal`-mode rows have no rollup to
    protect (WP9.4 only rolls up organization-scoped activity) and purge
    unconditionally, as proven separately by
    `test_wp91_product_usage_events_postgresql.py`'s own purge test."""
    engine, _, _ = wp94_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    admin_id = _create_user(Session, "neverrolledup")
    organization_id = _add_membership(Session, admin_id, code=_unique_code("acme"), base_role="admin")

    old_day = datetime.now(timezone.utc) - timedelta(days=120)
    _seed_raw_event(Session, organization_id=organization_id, event_type="set_created", occurred_at=old_day)
    # Deliberately never call `refresh_product_usage_rollups` -- this
    # organization/event_type/day has no rollup cell at all.

    # `purge_expired_product_usage_events` is global (not scoped to one
    # organization), and this module-scoped database is shared with other
    # tests in this file that seed their own expired, already-rolled-up
    # rows and don't always purge them within their own test body -- so
    # this only asserts on *this test's own* organization's row, never on
    # the purge's global return count.
    with Session() as session:
        purge_expired_product_usage_events(session)
        session.commit()

    with Session() as session:
        remaining = session.query(ProductUsageEvent).filter(ProductUsageEvent.organization_id == organization_id).count()
    assert remaining == 1

    # Once refreshed, the now-rolled-up row becomes purgeable.
    with Session() as session:
        refresh_product_usage_rollups(session)
        session.commit()
    with Session() as session:
        purge_expired_product_usage_events(session)
        session.commit()
    with Session() as session:
        remaining_after_refresh = session.query(ProductUsageEvent).filter(ProductUsageEvent.organization_id == organization_id).count()
    assert remaining_after_refresh == 0
