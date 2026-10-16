"""What an organization key can read that a personal key cannot (WP-A).

Every route here answers 404 to a personal key and to another organization's
key; a private symbol never appears outside its own organization. The SQLite
harness proves the shape and the 404s; tenant isolation of the underlying
eligibility is the palette's own, covered in its PostgreSQL suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from symgov_backend.catalog_org_api_keys import claim_organization_catalog_api_key, issue_organization_catalog_api_key
from symgov_backend.models import Organization, OrganizationMembership, User

from test_catalog_api_key_policies import _key
from test_catalog_org_api_keys import _client, _member_id
from test_effective_palette import _eligibility, _make_default_set, _organization_id, _symbol
from test_symbol_set_items import _ensure_symbol_tables, _replace_items

NOW = datetime.now(timezone.utc).replace(microsecond=0)
BASE = "/api/v1/catalog/organization"


def _org_key(Session, organization_id, holder_id, name="Plant add-in"):
    with Session() as session:
        issued = issue_organization_catalog_api_key(
            session, organization_id=organization_id, issuer_user_id=holder_id, holder_user_id=holder_id,
            integration_name=name, scopes=["catalog.read"],
        )
        _, raw = claim_organization_catalog_api_key(session, api_key_id=uuid.UUID(issued["keyId"]), user_id=holder_id)
        session.commit()
        return raw


def _personal_key(Session, holder_id):
    with Session() as session:
        user = session.get(User, holder_id)
        value = "-".join(["personal", "reader", "value"])
        _, raw = _key(session, holder=user, token=value)
        session.commit()
        return value


def _auth(raw):
    return {"Authorization": f"Bearer {raw}"}


def _world():
    client, Session = _client()
    _ensure_symbol_tables(Session)
    holder = _member_id(Session)
    organization = _organization_id(Session)
    return client, Session, holder, organization


def _private(Session, organization, slug, **kwargs):
    return _symbol(
        Session, slug, owner_organization_id=organization, visibility="organization_private", organization_wide=True,
        revision_payload={"package_display_id": "ACME", "package_symbol_sequence": kwargs.pop("sequence", 1), **kwargs},
    )


def test_a_personal_key_and_a_missing_key_cannot_see_these_routes():
    client, Session, holder, organization = _world()
    personal = _personal_key(Session, holder)
    paths = ["/symbols", f"/symbols/{uuid.uuid4()}", f"/symbols/{uuid.uuid4()}/preview", "/symbol-sets", "/symbol-sets/x/items", "/projects", f"/projects/{uuid.uuid4()}/palette"]
    for path in paths:
        assert client.get(BASE + path, headers=_auth(personal)).status_code == 404, path
        assert client.get(BASE + path).status_code == 401, path


def test_the_routes_do_not_exist_when_the_feature_is_off():
    client, _ = _client(enabled=False)
    assert client.get(BASE + "/symbols", headers=_auth("anything")).status_code == 404


def test_an_organization_key_lists_its_own_private_symbols_only():
    client, Session, holder, organization = _world()
    mine = _private(Session, organization, "pump-skid", sequence=1)
    with Session() as session:
        now = NOW
        other = Organization(
            id=uuid.uuid4(), code="OTHER", normalized_code="other", display_name="Other", name_key="other",
            entitlement_status="active", is_active=True, is_protected=False, fallback_icon_svg="<svg/>", created_at=now, updated_at=now,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    theirs = _private(Session, other_id, "their-secret-valve", sequence=2)
    raw = _org_key(Session, organization, holder)

    listed = client.get(BASE + "/symbols", headers=_auth(raw))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["id"] for item in body["items"]] == [str(mine)] and body["total"] == 1
    assert body["items"][0]["source"] == "organization_private"
    assert "payload" not in body["items"][0]
    assert str(theirs) not in listed.text and "their-secret-valve" not in listed.text

    assert client.get(f"{BASE}/symbols/{mine}", headers=_auth(raw)).json()["slug"] == "pump-skid"
    assert client.get(f"{BASE}/symbols/{theirs}", headers=_auth(raw)).status_code == 404
    assert client.get(f"{BASE}/symbols/not-a-uuid", headers=_auth(raw)).status_code == 404
    assert client.get(f"{BASE}/symbols/{mine}/preview", headers=_auth(raw)).status_code == 404  # no preview asset stored


def test_search_filters_and_cursor_paginates():
    client, Session, holder, organization = _world()
    for index in range(3):
        _private(Session, organization, f"valve-{index}", sequence=index + 1)
    _private(Session, organization, "pump-1", sequence=9)
    raw = _org_key(Session, organization, holder)
    first = client.get(BASE + "/symbols", params={"q": "valve", "limit": 2}, headers=_auth(raw)).json()
    assert len(first["items"]) == 2 and first["nextCursor"] == "2" and first["total"] == 3
    second = client.get(BASE + "/symbols", params={"q": "valve", "limit": 2, "cursor": first["nextCursor"]}, headers=_auth(raw)).json()
    assert len(second["items"]) == 1 and second["nextCursor"] is None
    assert client.get(BASE + "/symbols", params={"cursor": "-1"}, headers=_auth(raw)).status_code == 400
    assert client.get(BASE + "/symbols", params={"cursor": "abc"}, headers=_auth(raw)).status_code == 400


def test_sets_projects_and_the_palette(monkeypatch):
    client, Session, holder, organization = _world()
    project_id, set_id = _make_default_set(client, Session)
    public = _symbol(Session, "set-symbol", catalog_symbol_id="S-1")
    _eligibility(monkeypatch, {public: uuid.uuid4()})
    assert _replace_items(client, set_id, {"items": [{"governedSymbolId": str(public), "sortOrder": 1, "groupName": "Valves"}]}).status_code == 200
    private = _private(Session, organization, "org-wide-thing", sequence=7)
    raw = _org_key(Session, organization, holder)

    sets = client.get(BASE + "/symbol-sets", headers=_auth(raw)).json()
    assert [item["id"] for item in sets["items"]] == [set_id]
    code = sets["items"][0]["code"]
    items = client.get(f"{BASE}/symbol-sets/{code}/items", headers=_auth(raw)).json()
    assert [item["displayId"] for item in items["items"]] == ["S-1"] and items["items"][0]["group"] == "Valves"
    assert client.get(f"{BASE}/symbol-sets/nope/items", headers=_auth(raw)).status_code == 404

    projects = client.get(BASE + "/projects", headers=_auth(raw)).json()
    assert [item["id"] for item in projects["items"]] == [project_id]

    palette = client.get(f"{BASE}/projects/{project_id}/palette", headers=_auth(raw)).json()
    assert palette["reason"] == "project_default" and palette["activeSet"]["id"] == set_id
    assert [item["id"] for item in palette["items"]] == [str(public), str(private)]
    assert [item["source"] for item in palette["items"]] == ["set", "organization_wide"]
    explicit = client.get(f"{BASE}/projects/{project_id}/palette", params={"setCode": code}, headers=_auth(raw)).json()
    assert explicit["reason"] == "explicit"
    assert client.get(f"{BASE}/projects/{project_id}/palette", params={"setCode": "nope"}, headers=_auth(raw)).status_code == 404
    assert client.get(f"{BASE}/projects/{uuid.uuid4()}/palette", headers=_auth(raw)).status_code == 404


def test_a_revoked_organization_key_reads_nothing():
    client, Session, holder, organization = _world()
    raw = _org_key(Session, organization, holder)
    assert client.get(BASE + "/symbols", headers=_auth(raw)).status_code == 200
    with Session() as session:
        session.query(OrganizationMembership).update({"status": "inactive"})
        session.commit()
    assert client.get(BASE + "/symbols", headers=_auth(raw)).status_code == 401


def test_reads_by_keys_are_counted_to_the_right_owner():
    from symgov_backend.models import SymbolUsageEvent

    client, Session, holder, organization = _world()
    mine = _private(Session, organization, "counted-valve", sequence=1)
    org_raw, personal_raw = _org_key(Session, organization, holder), _personal_key(Session, holder)

    assert client.get(BASE + "/symbols", params={"q": "valve"}, headers=_auth(org_raw)).status_code == 200
    assert client.get(f"{BASE}/symbols/{mine}", headers=_auth(org_raw)).status_code == 200
    assert client.get(BASE + "/symbols", params={"q": "valve"}, headers=_auth(personal_raw)).status_code == 404

    with Session() as session:
        rows = session.query(SymbolUsageEvent).order_by(SymbolUsageEvent.occurred_at).all()
    by_type = {row.event_type: row for row in rows if row.key_kind == "organization"}
    assert set(by_type) == {"catalog_searched", "symbol_api_fetched"}
    for row in by_type.values():
        assert row.channel == "api" and row.scope_kind == "organization" and row.organization_id == organization and row.user_id == holder
    assert by_type["catalog_searched"].query_text == "valve" and by_type["catalog_searched"].result_count == 1
    assert by_type["symbol_api_fetched"].governed_symbol_id == mine
    assert not [row for row in rows if row.key_kind == "personal"]   # a personal key's refused request is not counted
