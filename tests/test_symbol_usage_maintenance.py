"""Rollups, retention and the rate-window sweep (WP-B)."""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest

from symgov_backend.models import CatalogApiRateWindow, SymbolUsageDailyRollup, SymbolUsageEvent
from symgov_backend.symbol_usage_events import build_symbol_usage_event
from symgov_backend.symbol_usage_maintenance import (
    SYMBOL_USAGE_RETENTION_DAYS, purge_symbol_usage_events, refresh_symbol_usage_rollups, run_maintenance,
)

from usage_tables import NOW, seed_organization, seed_symbol, seed_user, usage_session_factory


@pytest.fixture()
def session():
    with usage_session_factory()() as value:
        yield value


def _add(session, at, *, org=None, event_type="symbol_viewed", pack="0001"):
    session.add(build_symbol_usage_event(
        event_type=event_type, channel="web", user_id=None, organization_id=org.id if org else None, pack_code=pack, occurred_at=at,
    ))


def _cells(session):
    return sorted(
        (str(row.occurred_on), row.event_type, row.scope_kind, row.pack_code, row.event_count)
        for row in session.query(SymbolUsageDailyRollup).all()
    )


def test_rollups_count_whole_days_and_are_safe_to_repeat(session):
    org = seed_organization(session)
    for hours in (0, 1, 2):
        _add(session, NOW - timedelta(hours=hours), org=org)
    _add(session, NOW - timedelta(days=1), org=None, pack=None)
    session.commit()
    first = refresh_symbol_usage_rollups(session, since_day=(NOW - timedelta(days=3)).date(), now=NOW)
    assert first == 2
    before = _cells(session)
    refresh_symbol_usage_rollups(session, since_day=(NOW - timedelta(days=3)).date(), now=NOW)
    assert _cells(session) == before
    assert ("2026-10-16", "symbol_viewed", "organization", "0001", 3) in before
    assert ("2026-10-15", "symbol_viewed", "personal", "", 1) in before


def test_a_rerun_picks_up_events_that_arrived_since(session):
    org = seed_organization(session)
    _add(session, NOW, org=org)
    session.commit()
    refresh_symbol_usage_rollups(session, since_day=NOW.date(), now=NOW)
    _add(session, NOW + timedelta(minutes=5), org=org)
    session.commit()
    refresh_symbol_usage_rollups(session, since_day=NOW.date(), now=NOW)
    assert _cells(session) == [("2026-10-16", "symbol_viewed", "organization", "0001", 2)]


def test_purge_keeps_sixty_days_and_rolls_up_what_it_deletes_first(session):
    org = seed_organization(session)
    old, edge, fresh = NOW - timedelta(days=SYMBOL_USAGE_RETENTION_DAYS + 5), NOW - timedelta(days=SYMBOL_USAGE_RETENTION_DAYS - 1), NOW - timedelta(days=1)
    for at in (old, old + timedelta(hours=1), edge, fresh):
        _add(session, at, org=org)
    session.commit()
    assert purge_symbol_usage_events(session, now=NOW) == 2
    session.commit()
    assert session.query(SymbolUsageEvent).count() == 2
    cells = {(row[0], row[-1]) for row in _cells(session)}
    assert (str(old.date()), 2) in cells                       # the purged days survive as counts
    assert (str(edge.date()), 1) in cells and (str(fresh.date()), 1) in cells
    assert purge_symbol_usage_events(session, now=NOW) == 0


def test_nothing_to_purge_is_a_no_op(session):
    assert purge_symbol_usage_events(session, now=NOW) == 0


def test_a_full_pass_also_refreshes_the_stage9_rollups_and_sweeps_rate_windows(session, monkeypatch):
    # The Stage 9 refresh is raw PostgreSQL (`ON CONFLICT`, UUID binds) and has its own PostgreSQL tests;
    # here it only has to be called, with a day-aligned start, before its purge.
    calls = []
    monkeypatch.setattr("symgov_backend.symbol_usage_maintenance.refresh_product_usage_rollups", lambda s, since, now: calls.append(("refresh", since)) or 4)
    monkeypatch.setattr("symgov_backend.symbol_usage_maintenance.purge_expired_product_usage_events", lambda s, now: calls.append(("purge", None)) or 9)
    key = uuid.uuid4()
    from symgov_backend.models import CatalogApiKey

    session.add(CatalogApiKey(
        id=key, customer_name="c", integration_name="i", key_prefix="p", key_hash=uuid.uuid4().hex, scopes_json=[], status="active",
        allowed_origins_json=[], created_at=NOW, updated_at=NOW,
    ))
    session.flush()
    session.add_all([
        CatalogApiRateWindow(api_key_id=key, window_start=NOW - timedelta(days=3), request_count=5),
        CatalogApiRateWindow(api_key_id=key, window_start=NOW - timedelta(minutes=1), request_count=2),
    ])
    session.commit()

    result = run_maintenance(session, now=NOW)
    session.commit()
    assert [name for name, _ in calls] == ["refresh", "purge"]
    assert calls[0][1].hour == 0 and calls[0][1].date() == (NOW - timedelta(days=3)).date()
    assert (result.product_rollup_cells, result.purged_product_events, result.purged_rate_windows) == (4, 9, 1)
    assert session.query(CatalogApiRateWindow).count() == 1
