"""The usage and audit routes, and the events the web app's own routes now record (WP-B)."""

from __future__ import annotations

from dataclasses import replace
import uuid

from symgov_backend.models import CatalogWorkbenchClipboard, CatalogWorkbenchState, GovernedSymbol, SymbolRevision, SymbolUsageEvent
from symgov_backend.settings import get_settings

from test_effective_palette import _eligibility, _make_default_set, _organization_id, _symbol
from test_projects_api import _stage4_client
from test_symbol_set_items import _ensure_symbol_tables, _replace_items
from usage_tables import USAGE_MODELS, create_tables

PREFIX = "/api/v1"


def _client(*, role="admin", enabled=True):
    client, Session = _stage4_client(role=role)
    settings = replace(client._stage4_settings, symbol_usage_analytics_enabled=enabled)
    client.app.dependency_overrides[get_settings] = lambda: settings
    create_tables(Session.kw["bind"], USAGE_MODELS + (CatalogWorkbenchState, CatalogWorkbenchClipboard))
    return client, Session


def _step_up(client):
    assert client.post(f"{PREFIX}/auth/reauthenticate", json={"pin": "1234"}).status_code == 200


def _events(Session, event_type=None):
    with Session() as session:
        query = session.query(SymbolUsageEvent)
        if event_type:
            query = query.filter(SymbolUsageEvent.event_type == event_type)
        return query.all()


def test_the_reading_routes_do_not_exist_while_the_flag_is_off():
    client, _ = _client(enabled=False)
    for path in ("/org/me/usage", "/org/me/audit", "/platform/usage/organizations", "/platform/usage/packs", "/platform/audit"):
        assert client.get(PREFIX + path).status_code == 404, path


def test_an_organization_admin_reads_usage_and_audit_but_not_the_platform_views():
    client, Session = _client()
    usage = client.get(f"{PREFIX}/org/me/usage")
    assert usage.status_code == 200 and usage.json()["totals"] == {} and usage.json()["days"] == 30
    assert client.get(f"{PREFIX}/org/me/usage", params={"days": 61}).status_code == 422
    audit = client.get(f"{PREFIX}/org/me/audit", params={"pageSize": 5})
    assert audit.status_code == 200 and "items" in audit.json() and audit.json()["pageSize"] == 5
    for path in ("/platform/usage/organizations", "/platform/usage/packs", "/platform/audit"):
        assert client.get(PREFIX + path).status_code == 403, path


def test_a_plain_member_cannot_read_the_organizations_usage_or_audit():
    client, _ = _client(role="user")
    assert client.get(f"{PREFIX}/org/me/usage").status_code == 403
    assert client.get(f"{PREFIX}/org/me/audit").status_code == 403


def test_the_audit_csv_downloads_safely_and_refuses_a_long_range():
    client, _ = _client()
    ok = client.get(f"{PREFIX}/org/me/audit.csv", params={"since": "2026-10-01", "until": "2026-10-16"})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/csv")
    assert ok.headers["content-disposition"].startswith("attachment") and ok.headers["x-content-type-options"] == "nosniff"
    assert ok.text.splitlines()[0].startswith("occurred_at,entity_type")
    assert client.get(f"{PREFIX}/org/me/audit.csv", params={"since": "2026-08-01", "until": "2026-10-16"}).status_code == 422
    assert client.get(f"{PREFIX}/org/me/audit.csv").status_code == 422


def test_putting_a_symbol_on_the_clipboard_is_counted_once_for_the_organization():
    client, Session = _client()
    _ensure_symbol_tables(Session)
    symbol = _symbol(Session, "clip-valve", catalog_symbol_id="S-31")
    organization = _organization_id(Session)

    body = {"items": [{"id": "S-31", "displayName": "S-31", "name": "Valve", "availableFormats": []}]}
    assert client.put(f"{PREFIX}/published/workbench/clipboard", json=body).status_code == 200
    assert client.put(f"{PREFIX}/published/workbench/clipboard", json=body).status_code == 200   # saved again: nothing new on it
    added = _events(Session, "symbol_added_to_clipboard")
    assert len(added) == 1
    assert (added[0].governed_symbol_id, added[0].organization_id, added[0].scope_kind, added[0].channel) == (symbol, organization, "organization", "web")

    assert client.put(f"{PREFIX}/published/workbench/clipboard", json={"items": []}).status_code == 200
    assert client.put(f"{PREFIX}/published/workbench/clipboard", json=body).status_code == 200   # put back: counts again
    assert len(_events(Session, "symbol_added_to_clipboard")) == 2


def test_a_clipboard_save_still_succeeds_when_counting_cannot_work():
    client, Session = _client()
    with Session() as session:
        session.execute(SymbolUsageEvent.__table__.delete())
        session.execute(__import__("sqlalchemy").text("DROP TABLE symbol_usage_events"))
        session.commit()
    body = {"items": [{"id": "S-1", "displayName": "S-1", "name": "Valve", "availableFormats": []}]}
    assert client.put(f"{PREFIX}/published/workbench/clipboard", json=body).status_code == 200


def test_opening_details_records_one_view_per_five_minutes(monkeypatch):
    client, Session = _client()
    _ensure_symbol_tables(Session)
    organization = _organization_id(Session)
    private = _symbol(
        Session, "org-view", owner_organization_id=organization, visibility="organization_private", organization_wide=True,
        revision_payload={"package_display_id": "ACME", "package_symbol_sequence": 1},
    )
    # Public lookup is PostgreSQL SQL; this stands in for a resolved organization-private symbol.
    import symgov_backend.routes.published as published

    def resolved(session, symbol_id, current_user, settings):
        if symbol_id != str(private):
            raise published.HTTPException(status_code=404, detail="Not found.")
        with Session() as inner:
            governed = inner.get(GovernedSymbol, private)
            return "organization_private", (governed, inner.get(SymbolRevision, governed.current_revision_id)), "id"

    monkeypatch.setattr(published, "_load_symbol_for_detail", resolved)
    for _ in range(3):
        assert client.post(f"{PREFIX}/published/symbols/{private}/view-events").status_code == 204
    views = _events(Session, "symbol_viewed")
    assert len(views) == 1 and views[0].governed_symbol_id == private and views[0].organization_id == organization
    assert client.post(f"{PREFIX}/published/symbols/{uuid.uuid4()}/view-events").status_code == 404


def test_adding_symbols_to_a_set_is_counted_and_resaving_the_same_members_is_not(monkeypatch):
    client, Session = _client()
    _ensure_symbol_tables(Session)
    _project_id, set_id = _make_default_set(client, Session)
    first, second = _symbol(Session, "set-a", catalog_symbol_id="S-41"), _symbol(Session, "set-b", catalog_symbol_id="S-42")
    _eligibility(monkeypatch, {first: uuid.uuid4(), second: uuid.uuid4()})
    assert _replace_items(client, set_id, {"items": [{"governedSymbolId": str(first), "sortOrder": 1}]}).status_code == 200
    assert {event.governed_symbol_id for event in _events(Session, "symbol_added_to_set")} == {first}
    assert _replace_items(client, set_id, {"items": [
        {"governedSymbolId": str(first), "sortOrder": 1, "groupName": "Valves"}, {"governedSymbolId": str(second), "sortOrder": 2},
    ]}).status_code == 200
    events = _events(Session, "symbol_added_to_set")
    assert sorted(str(event.governed_symbol_id) for event in events) == sorted(str(value) for value in (first, second))
    assert all(str(event.symbol_set_id) == set_id and event.scope_kind == "organization" for event in events)
