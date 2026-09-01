"""Stage 6 WP6.1 — effective palette union/ordering/pagination/dedup logic.

SQLite-backed route-level tests (mirrors `test_symbol_set_items.py`'s
pattern): eligibility is mocked via `current_public_symbols`, since the
Postgres-only visibility floor (`active_public_symbol_projections`) is
exercised separately by `tests/test_symbol_set_tenant_isolation.py`
against a real disposable PostgreSQL container. These tests prove the
union/ordering/dedup/pagination/resolution-order *shape* is correct; they
do not attempt to prove tenant isolation, which is a Postgres-only
guarantee — see `test_symbol_set_tenant_isolation.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import uuid

from symgov_backend.settings import get_settings

from test_projects_api import _stage4_client
from test_symbol_set_availability import _active_set, _project
from test_symbol_set_items import _ensure_symbol_tables
from symgov_backend.models import GovernedSymbol, Organization, User
import symgov_backend.effective_palette as effective_palette_module
import symgov_backend.symbol_set_service as symbol_set_service


def _eligibility(monkeypatch, values):
    def fake(session, ids):
        return {symbol_id: values[symbol_id] for symbol_id in ids if symbol_id in values}

    monkeypatch.setattr(symbol_set_service, "current_public_symbols", fake)
    monkeypatch.setattr(effective_palette_module, "current_public_symbols", fake)


def _organization_id(Session) -> uuid.UUID:
    with Session() as session:
        return session.query(Organization).one().id


def _symbol(Session, slug, *, owner_organization_id=None, visibility="public", organization_wide=False, identifier=None):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        owner = session.query(User).first()
        row = GovernedSymbol(
            id=identifier or uuid.uuid4(),
            slug=slug,
            canonical_name=slug,
            category="test",
            discipline="test",
            owner_id=owner.id,
            owner_organization_id=owner_organization_id,
            visibility=visibility,
            organization_wide=organization_wide,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _make_default_set(client, Session):
    project_id = _project(client)
    set_id = _active_set(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    ).status_code == 200
    return project_id, set_id


def test_palette_unions_set_items_and_organization_wide_symbols_deduplicated_and_ordered(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, set_id = _make_default_set(client, Session)
    organization_id = _organization_id(Session)

    set_symbol_a = _symbol(Session, "set-symbol-a")
    set_symbol_b = _symbol(Session, "set-symbol-b")
    _eligibility(monkeypatch, {set_symbol_a: uuid.uuid4(), set_symbol_b: uuid.uuid4()})
    put_items = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [
            {"governedSymbolId": str(set_symbol_a), "sortOrder": 5},
            {"governedSymbolId": str(set_symbol_b), "sortOrder": 1},
        ]},
    )
    assert put_items.status_code == 200

    org_wide_z = _symbol(Session, "org-wide-zeta", owner_organization_id=organization_id, visibility="organization_private", organization_wide=True)
    org_wide_a = _symbol(Session, "org-wide-alpha", owner_organization_id=organization_id, visibility="organization_private", organization_wide=True)

    response = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette")
    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "project_default"
    assert body["activeSet"]["id"] == set_id

    ids_in_order = [item["governedSymbolId"] for item in body["items"]]
    assert ids_in_order == [str(set_symbol_b), str(set_symbol_a), str(org_wide_a), str(org_wide_z)]

    by_id = {item["governedSymbolId"]: item for item in body["items"]}
    assert by_id[str(set_symbol_a)]["source"] == "set"
    assert by_id[str(set_symbol_b)]["source"] == "set"
    assert by_id[str(org_wide_a)]["source"] == "organization_wide"
    assert by_id[str(org_wide_a)]["groupName"] == "Organization-wide"
    assert by_id[str(org_wide_a)]["sortOrder"] > by_id[str(set_symbol_a)]["sortOrder"]
    assert by_id[str(org_wide_z)]["sortOrder"] > by_id[str(org_wide_a)]["sortOrder"]
    assert body["total"] == 4


def test_palette_excludes_ineligible_set_items_but_they_remain_visible_to_builder(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, set_id = _make_default_set(client, Session)

    eligible_id = _symbol(Session, "eligible")
    stale_id = _symbol(Session, "stale")
    _eligibility(monkeypatch, {eligible_id: uuid.uuid4(), stale_id: uuid.uuid4()})
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [
            {"governedSymbolId": str(eligible_id), "sortOrder": 1},
            {"governedSymbolId": str(stale_id), "sortOrder": 2},
        ]},
    ).status_code == 200

    # stale_id drops out of Public Catalog eligibility after being added.
    _eligibility(monkeypatch, {eligible_id: uuid.uuid4()})

    palette = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette").json()
    assert [item["governedSymbolId"] for item in palette["items"]] == [str(eligible_id)]

    builder_items = client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items").json()
    statuses = {item["governedSymbolId"]: item["availabilityStatus"] for item in builder_items["items"]}
    assert statuses[str(stale_id)] == "unavailable"


def test_palette_excludes_organization_private_symbols_not_marked_organization_wide():
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, _ = _make_default_set(client, Session)
    organization_id = _organization_id(Session)

    _symbol(Session, "not-organization-wide", owner_organization_id=organization_id, visibility="organization_private", organization_wide=False)

    palette = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette").json()
    assert palette["items"] == []
    assert palette["total"] == 0


def test_palette_respects_organization_symbols_enabled_flag():
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, _ = _make_default_set(client, Session)
    organization_id = _organization_id(Session)
    _symbol(Session, "org-wide", owner_organization_id=organization_id, visibility="organization_private", organization_wide=True)

    with_flag_on = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette").json()
    assert with_flag_on["total"] == 1

    flag_off_settings = replace(client._stage4_settings, organization_symbols_enabled=False)
    client.app.dependency_overrides[get_settings] = lambda: flag_off_settings
    with_flag_off = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette").json()
    assert with_flag_off["total"] == 0


def test_palette_explicit_set_code_overrides_resolution_without_persisting():
    # Only one Symbol Set is made available to this project: the ad hoc
    # SQLite schema used by this test module renders
    # `ProjectSymbolSet`'s partial unique index
    # (`uq_project_symbol_sets_active_default`, PostgreSQL-only
    # `postgresql_where`) as a plain unique index on `project_id` alone,
    # so a second available-but-not-default set for the same project is
    # not representable here. The two-sets-per-project explicit-override
    # case is covered against real PostgreSQL in
    # `test_symbol_set_tenant_isolation.py`.
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id = _project(client)
    set_id = _active_set(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": False}]},
    ).status_code == 200

    no_default = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette")
    assert no_default.json()["reason"] == "none"
    assert no_default.json()["activeSet"] is None

    explicit = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette", params={"setCode": "SET-01"})
    assert explicit.status_code == 200
    assert explicit.json()["reason"] == "explicit"
    assert explicit.json()["activeSet"]["id"] == set_id

    default_again = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette")
    assert default_again.json()["reason"] == "none"
    assert default_again.json()["activeSet"] is None


def test_palette_rejects_ineligible_explicit_set_code():
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, _ = _make_default_set(client, Session)

    response = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette", params={"setCode": "NOT-REAL"})
    assert response.status_code == 404


def test_palette_pagination_is_bounded_and_deterministic(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, set_id = _make_default_set(client, Session)

    symbol_ids = [_symbol(Session, f"symbol-{index:02d}") for index in range(5)]
    _eligibility(monkeypatch, {symbol_id: uuid.uuid4() for symbol_id in symbol_ids})
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [
            {"governedSymbolId": str(symbol_id), "sortOrder": index}
            for index, symbol_id in enumerate(symbol_ids)
        ]},
    ).status_code == 200

    first_page = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette", params={"pageSize": 2}).json()
    assert first_page["total"] == 5
    assert len(first_page["items"]) == 2
    second_page = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette", params={"pageSize": 2, "page": 2}).json()
    assert [item["governedSymbolId"] for item in first_page["items"] + second_page["items"]] == [str(symbol_id) for symbol_id in symbol_ids[:4]]


def test_palette_requires_an_existing_active_project():
    client, _ = _stage4_client()
    response = client.get(f"/api/v1/org/me/projects/{uuid.uuid4()}/effective-palette")
    assert response.status_code == 404


def test_palette_with_no_active_set_still_includes_organization_wide_symbols():
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id = _project(client)
    organization_id = _organization_id(Session)
    _symbol(Session, "org-wide-only", owner_organization_id=organization_id, visibility="organization_private", organization_wide=True)

    palette = client.get(f"/api/v1/org/me/projects/{project_id}/effective-palette").json()
    assert palette["reason"] == "none"
    assert palette["activeSet"] is None
    assert palette["total"] == 1
    assert palette["items"][0]["source"] == "organization_wide"
