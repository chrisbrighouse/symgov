"""What the organization usage page and the platform roll-up say (WP-B)."""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest

from symgov_backend.models import SymbolUsageEvent
from symgov_backend.symbol_usage_maintenance import refresh_symbol_usage_rollups
from symgov_backend.symbol_usage_reports import organization_usage, platform_usage_by_organization, platform_usage_by_pack

from usage_tables import NOW, seed_organization, seed_symbol, seed_user, usage_session_factory


@pytest.fixture()
def session():
    with usage_session_factory()() as value:
        yield value


def _event(session, event_type, *, organization=None, user=None, symbol=None, channel="web", api_key_id=None, key_kind=None, at=NOW, **extra):
    from symgov_backend.symbol_usage_events import build_symbol_usage_event

    session.add(build_symbol_usage_event(
        event_type=event_type, channel=channel, user_id=user.id if user else None, organization_id=organization.id if organization else None,
        governed_symbol_id=symbol.id if symbol else None, api_key_id=api_key_id, key_kind=key_kind, occurred_at=at, **extra,
    ))


def _world(session):
    acme, other = seed_organization(session, "ACME"), seed_organization(session, "OTHER")
    ada, bob, eve = seed_user(session, "Ada"), seed_user(session, "Bob"), seed_user(session, "Eve")
    valve, pump = seed_symbol(session, ada, "valve", catalog_id="S-1"), seed_symbol(session, ada, "pump", catalog_id="S-2")
    return acme, other, ada, bob, eve, valve, pump


def test_the_page_counts_only_that_organizations_own_activity(session):
    acme, other, ada, bob, eve, valve, pump = _world(session)
    key = uuid.uuid4()
    for _ in range(3):
        _event(session, "symbol_viewed", organization=acme, user=ada, symbol=valve)
    _event(session, "symbol_downloaded", organization=acme, user=bob, symbol=valve, format="svg")
    _event(session, "symbol_viewed", organization=acme, user=bob, symbol=pump)
    _event(session, "symbol_viewed", organization=other, user=eve, symbol=valve)           # another organization
    _event(session, "symbol_viewed", user=ada, symbol=valve)                               # Ada, in a personal session
    _event(session, "symbol_api_fetched", user=ada, symbol=valve, channel="api", api_key_id=key, key_kind="personal", organization=acme)  # personal key
    _event(session, "symbol_api_fetched", organization=acme, user=bob, symbol=pump, channel="api", api_key_id=uuid.uuid4(), key_kind="organization")
    session.commit()

    page = organization_usage(session, acme.id, days=30, now=NOW + timedelta(hours=1))
    assert page["totals"] == {"symbol_viewed": 4, "symbol_downloaded": 1, "symbol_api_fetched": 1}
    assert page["channels"] == {"web": 5, "api": 1}
    assert [(item["displayId"], item["total"]) for item in page["topSymbols"]] == [("S-1", 4), ("S-2", 2)]
    users = {item["displayName"]: item["total"] for item in page["users"]}
    assert users == {ada.display_name: 3, bob.display_name: 3}
    assert {item["email"] for item in page["users"]} == {ada.email, bob.email}
    assert eve.display_name not in repr(page)


def test_zero_result_searches_are_listed_and_ones_with_results_are_not(session):
    acme, other, ada, *_ = _world(session)
    for _ in range(2):
        _event(session, "catalog_searched", organization=acme, user=ada, query_text="hydrogen vessel", result_count=0)
    _event(session, "catalog_searched", organization=acme, user=ada, query_text="gate valve", result_count=14)
    _event(session, "catalog_searched", organization=other, user=ada, query_text="their secret", result_count=0)
    _event(session, "catalog_searched", user=ada, query_text="personal search", result_count=0)
    session.commit()
    page = organization_usage(session, acme.id, now=NOW)
    assert page["zeroResultSearches"] == [{"query": "hydrogen vessel", "searches": 2}]
    assert "personal search" not in repr(page) and "their secret" not in repr(page)


def test_the_window_is_bounded_and_old_events_fall_outside_it(session):
    acme, other, ada, bob, eve, valve, pump = _world(session)
    _event(session, "symbol_viewed", organization=acme, user=ada, symbol=valve, at=NOW - timedelta(days=10))
    _event(session, "symbol_viewed", organization=acme, user=ada, symbol=valve, at=NOW - timedelta(days=45))
    session.commit()
    assert organization_usage(session, acme.id, days=30, now=NOW)["totals"] == {"symbol_viewed": 1}
    assert organization_usage(session, acme.id, days=60, now=NOW)["totals"] == {"symbol_viewed": 2}
    assert organization_usage(session, acme.id, days=999, now=NOW)["days"] == 60


def test_the_trend_comes_from_the_rollups_and_outlives_the_raw_rows(session):
    acme, other, ada, bob, eve, valve, pump = _world(session)
    _event(session, "symbol_viewed", organization=acme, user=ada, symbol=valve, at=NOW - timedelta(days=2))
    _event(session, "symbol_viewed", organization=acme, user=bob, symbol=valve, at=NOW - timedelta(days=2))
    session.commit()
    refresh_symbol_usage_rollups(session, since_day=(NOW - timedelta(days=5)).date(), now=NOW)
    session.query(SymbolUsageEvent).delete()
    session.commit()
    page = organization_usage(session, acme.id, now=NOW)
    assert page["totals"] == {} and page["users"] == []
    assert page["trend"] == [{"date": (NOW - timedelta(days=2)).date().isoformat(), "counts": {"symbol_viewed": 2}}]


def test_the_platform_rollup_has_totals_only_and_labels_personal_activity(session):
    acme, other, ada, bob, eve, valve, pump = _world(session)
    for user, org in ((ada, acme), (bob, acme), (eve, other), (eve, None)):
        _event(session, "symbol_viewed", organization=org, user=user, symbol=valve, pack_code="0001")
    _event(session, "catalog_searched", organization=acme, user=ada, query_text="private words", result_count=3)
    session.commit()
    refresh_symbol_usage_rollups(session, since_day=(NOW - timedelta(days=1)).date(), now=NOW)
    session.commit()

    by_org = platform_usage_by_organization(session, now=NOW)
    names = {item["displayName"]: item["total"] for item in by_org["items"]}
    assert names[acme.display_name] == 3 and names[other.display_name] == 1 and names["Personal (no organization)"] == 1
    text = repr(by_org) + repr(platform_usage_by_pack(session, now=NOW))
    for forbidden in (ada.email, bob.display_name, "private words", str(ada.id)):
        assert forbidden not in text
    packs = platform_usage_by_pack(session, now=NOW)
    assert packs["items"] == [{"packCode": "0001", "counts": {"symbol_viewed": 4}, "total": 4}]
