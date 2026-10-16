"""Recording symbol usage (WP-B): whose activity a row is, and what it may keep."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest

from symgov_backend.models import SymbolUsageEvent
from symgov_backend.symbol_usage_events import (
    build_symbol_usage_event,
    key_activity,
    record_symbol_usage,
    record_symbol_usage_best_effort,
    resolve_symbol_refs,
    user_activity,
)

from usage_tables import NOW, seed_organization, seed_symbol, seed_user, usage_session_factory


@pytest.fixture()
def session():
    with usage_session_factory()() as value:
        yield value


def _web(**overrides):
    values = dict(event_type="symbol_viewed", channel="web", user_id=uuid.uuid4(), organization_id=uuid.uuid4(), occurred_at=NOW)
    values.update(overrides)
    return values


def test_an_organization_row_is_the_organizations_and_keeps_its_search_text():
    event = build_symbol_usage_event(**_web(event_type="catalog_searched", query_text="  gate   valve  ", result_count=0))
    assert event.scope_kind == "organization" and event.query_text == "gate valve" and event.result_count == 0


def test_a_row_with_no_organization_is_personal_and_keeps_no_search_text():
    event = build_symbol_usage_event(**_web(organization_id=None, event_type="catalog_searched", query_text="secret project name"))
    assert event.scope_kind == "personal" and event.query_text is None


def test_a_personal_key_never_counts_for_an_organization_even_if_one_is_passed():
    event = build_symbol_usage_event(
        channel="api", event_type="symbol_api_fetched", user_id=uuid.uuid4(), organization_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(), key_kind="personal", query_text="valve",
    )
    assert event.organization_id is None and event.scope_kind == "personal" and event.query_text is None


def test_credentials_in_search_text_are_redacted_and_long_text_is_cut():
    event = build_symbol_usage_event(**_web(event_type="catalog_searched", query_text="find token=abcd1234 " + "x" * 400))
    assert "abcd1234" not in event.query_text and len(event.query_text) <= 200


def test_unknown_event_types_are_refused():
    with pytest.raises(ValueError):
        build_symbol_usage_event(**_web(event_type="symbol_deleted"))


def test_activity_helpers_follow_the_session_and_the_key():
    org = str(uuid.uuid4())
    inside = SimpleNamespace(id=str(uuid.uuid4()), session_mode="organization", active_organization_id=org)
    outside = SimpleNamespace(id=str(uuid.uuid4()), session_mode="personal", active_organization_id=org)
    assert user_activity(inside)["organization_id"] == org
    assert user_activity(outside)["organization_id"] is None and user_activity(outside)["channel"] == "web"
    key = SimpleNamespace(api_key_id="k", holder_user_id="u", organization_id=org, key_kind="organization")
    assert key_activity(key) == {"channel": "api", "user_id": "u", "organization_id": org, "api_key_id": "k", "key_kind": "organization"}


def test_recording_adds_a_row_and_repeats_within_the_window_are_dropped(session):
    user, organization = seed_user(session), seed_organization(session)
    symbol = seed_symbol(session, user)
    fields = dict(
        event_type="symbol_viewed", channel="web", user_id=user.id, organization_id=organization.id,
        governed_symbol_id=symbol.id, dedupe_seconds=300,
    )
    assert record_symbol_usage(session, **fields, occurred_at=NOW) is True
    assert record_symbol_usage(session, **fields, occurred_at=NOW + timedelta(seconds=120)) is False
    assert record_symbol_usage(session, **fields, occurred_at=NOW + timedelta(seconds=400)) is True
    other = seed_symbol(session, user, "pump")
    assert record_symbol_usage(session, **{**fields, "governed_symbol_id": other.id}, occurred_at=NOW) is True
    assert session.query(SymbolUsageEvent).count() == 3


def test_a_failed_write_never_raises_and_leaves_the_callers_work_alone(session):
    user = seed_user(session)
    session.commit()
    # A key on a web row violates the table's channel/key rule at flush.
    assert record_symbol_usage(session, event_type="symbol_viewed", channel="web", user_id=user.id, api_key_id=uuid.uuid4()) is False
    assert session.query(SymbolUsageEvent).count() == 0
    assert session.get(type(user), user.id) is not None
    assert record_symbol_usage(session, event_type="symbol_viewed", channel="web", user_id=user.id) is True


def test_best_effort_commits_and_swallows_everything(session):
    user = seed_user(session)
    assert record_symbol_usage_best_effort(session, event_type="symbol_viewed", channel="web", user_id=user.id) is True

    class Broken:
        def begin_nested(self):
            raise RuntimeError("database gone")

        def commit(self):
            raise RuntimeError("database gone")

        def rollback(self):
            raise RuntimeError("database gone")

    assert record_symbol_usage_best_effort(Broken(), event_type="symbol_viewed", channel="web") is False


def test_refs_resolve_by_catalog_id_slug_or_uuid(session):
    owner = seed_user(session)
    by_id = seed_symbol(session, owner, catalog_id="S-77")
    by_slug = seed_symbol(session, owner, "pump")
    raw = uuid.uuid4()
    found = resolve_symbol_refs(session, ["S-77", by_slug.slug, str(raw), "nothing-here", ""])
    assert found == {"S-77": by_id.id, by_slug.slug: by_slug.id, str(raw): raw}
