"""`GET /published/symbols/search` against a real disposable PostgreSQL.

The search is PostgreSQL-only (JSONB operators, `PUBLISHED_SYMBOLS_SQL`), so
this is where it is proved. It covers:

- the Catalog scope returns the same rows, in the same shape, as
  `GET /published/symbols`, one page at a time, with the true total;
- search, facet filters (exact, OR within, AND across) and column filters
  follow the browser's rules, and each filter's counts ignore only itself;
- sorting, including natural ID order and preferred formats first;
- the favourites filter;
- visibility is decided by the live rules: a symbol taken out of its pack
  disappears at once although its facet row is still stored, and an
  organization's private symbols reach only that organization's sessions;
- facet rows follow the data: a payload, category or rules-version change is
  picked up on the next search, through the generation counters the
  migration's triggers maintain.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import (  # noqa: E402
    _add_membership,
    _client,
    _create_user_with_global_roles,
)
from test_wp81_catalog_organization_context_postgresql import (  # noqa: E402
    _login,
    _make_organization_wide_symbol,
)

MIGRATION_HEAD = "20260925_0064"
SEARCH = "/api/v1/published/symbols/search"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def search_database():
    with _database("symgov-catalog-search") as (engine, url, raw_url):
        _alembic(url, "upgrade", MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT, INSERT, DELETE ON catalog_favourites TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON catalog_symbol_facets TO symgov_app",
            ):
                connection.execute(statement)
        seeded = _seed_public_catalog(engine)
        yield engine, seeded


# name, category, discipline, pack, catalog ID, payload downloads
PUBLIC_SYMBOLS = [
    ("Gate valve", "valve", "piping", "alpha", "TST-2", ["gate.dxf", "gate.svg"]),
    ("Globe valve 100%", "valve", "piping", "alpha", "TST-10", ["globe.svg"]),
    ("Centrifugal pump", "pump", "mechanical", "beta", "TST-3", ["pump.dxf"]),
    ("Smoke detector", "detector", "fire", "alpha", "TST-1", []),
    ("Tag bubble", "tag", "general", "alpha", "TST-20", ["tag.png"]),
]


def _seed_public_catalog(engine) -> dict[str, dict]:
    """Published public symbols, seeded directly the way WP11.5 does it."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    seeded: dict[str, dict] = {}
    with engine.begin() as connection:
        owner_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ), {"id": owner_id, "email": f"search-owner-{owner_id}@example.test", "now": now})
        packs = {}
        for key, title in (("alpha", "Alpha Pack"), ("beta", "Beta Pack")):
            packs[key] = uuid.uuid4()
            connection.execute(text(
                "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
                "VALUES (:id,:code,:title,'public',DATE '2026-09-02','published',:now,:now)"
            ), {"id": packs[key], "code": f"{key.upper()}-PACK", "title": title, "now": now})
        for name, category, discipline, pack, catalog_id, downloads in PUBLIC_SYMBOLS:
            symbol_id, revision_id, page_id, entry_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            slug = name.lower().replace(" ", "-").replace("%", "pct")
            connection.execute(text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
                "VALUES (:id,:slug,:name,:category,:discipline,:owner,:now,:now)"
            ), {"id": symbol_id, "slug": slug, "name": name, "category": category, "discipline": discipline, "owner": owner_id, "now": now})
            connection.execute(text(
                "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
                "VALUES (:id,:symbol,'A','published',CAST(:payload AS jsonb),:owner,:now)"
            ), {"id": revision_id, "symbol": symbol_id, "owner": owner_id, "now": now,
                "payload": json.dumps({"name": name, "downloads": downloads})})
            connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision_id, "symbol": symbol_id})
            connection.execute(text(
                "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
                "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
            ), {"catalog": catalog_id, "symbol": symbol_id})
            connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"), {"catalog": catalog_id, "symbol": symbol_id})
            connection.execute(text(
                "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
                "VALUES (:id,:code,:title,:pack,:revision,DATE '2026-09-02',:now,:now)"
            ), {"id": page_id, "code": f"PAGE-{catalog_id}", "title": name, "pack": packs[pack], "revision": revision_id, "now": now})
            connection.execute(text(
                "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                "VALUES (:id,:pack,:revision,:page,1,:now)"
            ), {"id": entry_id, "pack": packs[pack], "revision": revision_id, "page": page_id, "now": now})
            seeded[name] = {"symbol_id": str(symbol_id), "revision_id": revision_id, "entry_id": entry_id, "catalog_id": catalog_id}
    return seeded


def _session_client(engine, *, email, code=None, base_role="user", capabilities=()):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    client, _ = _client(engine)
    user_id = _create_user_with_global_roles(Session, email=email, display_name=email.split("@")[0], roles=[])
    if code is not None:
        _add_membership(Session, user_id, code=code, base_role=base_role, capabilities=capabilities)
    _login(client, email)
    return client


@pytest.fixture(scope="module")
def personal_client(search_database):
    engine, _ = search_database
    return _session_client(engine, email="search-personal@example.test")


def _search(client, **params):
    response = client.get(SEARCH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _names(body):
    return [item["name"] for item in body["items"]]


def _counts(body, facet):
    return {entry["value"]: entry["count"] for entry in body["facets"][facet]}


def test_catalog_scope_returns_the_list_routes_rows_one_page_at_a_time(search_database, personal_client):
    body = _search(personal_client, pageSize=2)
    assert body["scope"] == "catalog"
    assert body["total"] == len(PUBLIC_SYMBOLS)
    assert body["page"] == 1 and body["pageSize"] == 2
    # Default sort is the ID column, in natural order.
    assert [item["catalogSymbolId"] for item in body["items"]] == ["TST-1", "TST-2"]
    second = _search(personal_client, pageSize=2, page=2)
    assert [item["catalogSymbolId"] for item in second["items"]] == ["TST-3", "TST-10"]

    listed = personal_client.get("/api/v1/published/symbols").json()["items"]
    everything = _search(personal_client, pageSize=200)["items"]
    assert sorted(everything, key=lambda item: item["symbolId"]) == sorted(listed, key=lambda item: item["symbolId"])


def test_search_matches_the_browsers_search_text_and_the_live_pack_fields(personal_client):
    assert _names(_search(personal_client, q="GATE")) == ["Gate valve"]
    # A derived category label, not a stored column.
    assert sorted(_names(_search(personal_client, q="sensors / detectors"))) == ["Smoke detector"]
    # Pack title and code come from the live publication join.
    assert _names(_search(personal_client, q="beta pack")) == ["Centrifugal pump"]
    assert _names(_search(personal_client, q="BETA-PACK")) == ["Centrifugal pump"]
    # LIKE wildcards in the query are literal.
    assert _names(_search(personal_client, q="100%")) == ["Globe valve 100%"]
    assert _search(personal_client, q="va_ve")["total"] == 0


def test_facet_filters_are_exact_or_within_and_across(personal_client):
    valves = _search(personal_client, catalogCategories="Valves")
    assert sorted(_names(valves)) == ["Gate valve", "Globe valve 100%"]
    either = _search(personal_client, catalogCategories=["Valves", "Pumps"])
    assert either["total"] == 3
    both = _search(personal_client, catalogCategories=["Valves", "Pumps"], availableFormats="DXF")
    assert sorted(_names(both)) == ["Centrifugal pump", "Gate valve"]
    # Exact values only: a fragment of a value matches nothing.
    assert _search(personal_client, catalogCategories="Valve")["total"] == 0
    assert _names(_search(personal_client, pack="Beta Pack")) == ["Centrifugal pump"]
    assert _names(_search(personal_client, symbolFamily="pump")) == ["Centrifugal pump"]


def test_each_filters_counts_ignore_only_that_filter(personal_client):
    body = _search(personal_client, catalogCategories="Valves", availableFormats="SVG")
    assert body["total"] == 2
    # Category counts apply the format filter but not the category filter.
    categories = _counts(body, "catalogCategories")
    assert categories["Valves"] == 2
    assert categories["Pumps"] == 0  # listed, though the filters exclude it
    # Format counts apply the category filter but not the format filter.
    formats = _counts(body, "availableFormats")
    assert formats == {"DXF": 1, "SVG": 2, "PNG": 0}
    assert _counts(body, "pack") == {"Alpha Pack": 2, "Beta Pack": 0}


def test_column_filters_follow_the_shown_text(personal_client):
    assert _names(_search(personal_client, **{"column.name": "PUMP"})) == ["Centrifugal pump"]
    # The browser shows September as "Sept".
    assert _search(personal_client, **{"column.effectiveDate": "02 sept 2026"})["total"] == len(PUBLIC_SYMBOLS)
    assert _search(personal_client, **{"column.effectiveDate": "02 sep 2026"})["total"] == 0
    assert _search(personal_client, **{"column.scope": "public"})["total"] == len(PUBLIC_SYMBOLS)
    assert _names(_search(personal_client, **{"column.catalogDisciplines": "mechanical"})) == ["Centrifugal pump"]


def test_bad_parameters_get_the_standard_validation_envelope(personal_client):
    for params in ({"column.nope": "x"}, {"sort": "nope"}, {"pageSize": 201}, {"scope": "set"}):
        response = personal_client.get(SEARCH, params=params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"] == "validation_error"


def test_sorting(personal_client):
    by_name = _search(personal_client, sort="name", direction="desc")
    assert _names(by_name) == ["Tag bubble", "Smoke detector", "Globe valve 100%", "Gate valve", "Centrifugal pump"]
    # Preferred formats come first, whatever the column sort says.
    preferred = _search(personal_client, preferredFormats="PNG", sort="name")
    assert _names(preferred)[0] == "Tag bubble"
    preferred = _search(personal_client, preferredFormats=["DXF", "PNG"], sort="name")
    assert _names(preferred)[:3] == ["Centrifugal pump", "Gate valve", "Tag bubble"]


def test_favourites_filter(search_database):
    engine, seeded = search_database
    client = _session_client(engine, email="search-favourites@example.test")
    assert _search(client, favourites="true")["total"] == 0
    response = client.put(f"/api/v1/published/favourites/{seeded['Centrifugal pump']['symbol_id']}")
    assert response.status_code == 200, response.text
    body = _search(client, favourites="true")
    assert _names(body) == ["Centrifugal pump"]
    assert body["items"][0]["isFavourite"] is True


def test_a_symbol_leaves_the_results_as_soon_as_it_leaves_its_pack(search_database, personal_client):
    engine, seeded = search_database
    entry = seeded["Tag bubble"]
    assert "Tag bubble" in _names(_search(personal_client, pageSize=200))
    with engine.begin() as connection:
        stored = connection.execute(
            text("SELECT count(*) FROM catalog_symbol_facets WHERE symbol_revision_id = :id"),
            {"id": entry["revision_id"]},
        ).scalar_one()
        assert stored == 1
        row = connection.execute(
            text("SELECT * FROM pack_entries WHERE id = :id"), {"id": entry["entry_id"]}
        ).mappings().one()
        connection.execute(text("DELETE FROM pack_entries WHERE id = :id"), {"id": entry["entry_id"]})
    try:
        body = _search(personal_client, pageSize=200)
        assert "Tag bubble" not in _names(body)
        assert body["total"] == len(PUBLIC_SYMBOLS) - 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                    "VALUES (:id,:pack_id,:symbol_revision_id,:published_page_id,:sort_order,:created_at)"
                ),
                dict(row),
            )
    assert "Tag bubble" in _names(_search(personal_client, pageSize=200))


def test_facet_rows_follow_payload_category_and_rules_changes(search_database, personal_client):
    engine, seeded = search_database
    entry = seeded["Gate valve"]
    assert _search(personal_client, availableFormats="PDF")["total"] == 0

    def generations():
        with engine.begin() as connection:
            return connection.execute(
                text(
                    "SELECT sr.catalog_facet_generation, gs.catalog_facet_generation FROM symbol_revisions sr "
                    "JOIN governed_symbols gs ON gs.id = sr.symbol_id WHERE sr.id = :id"
                ),
                {"id": entry["revision_id"]},
            ).one()

    before = generations()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE symbol_revisions SET payload_json = payload_json || CAST(:extra AS jsonb) WHERE id = :id"
            ),
            {"id": entry["revision_id"], "extra": json.dumps({"downloads": ["gate.dxf", "gate.pdf"]})},
        )
    after_payload = generations()
    assert after_payload[0] == before[0] + 1 and after_payload[1] == before[1]
    assert _names(_search(personal_client, availableFormats="PDF")) == ["Gate valve"]

    # An update that leaves the payload alone does not bump the counter.
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE symbol_revisions SET rationale = 'reviewed' WHERE id = :id"), {"id": entry["revision_id"]}
        )
    assert generations() == after_payload

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE governed_symbols SET category = 'pump' WHERE id = CAST(:id AS uuid)"),
            {"id": entry["symbol_id"]},
        )
    assert generations()[1] == after_payload[1] + 1
    assert sorted(_names(_search(personal_client, catalogCategories="Pumps"))) == ["Centrifugal pump", "Gate valve"]

    # A row stored under another rules version is recomputed, not trusted.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE catalog_symbol_facets SET rules_version = 0, categories = '[]'::jsonb "
                "WHERE symbol_revision_id = :id"
            ),
            {"id": entry["revision_id"]},
        )
    assert "Gate valve" in _names(_search(personal_client, catalogCategories="Pumps"))

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE governed_symbols SET category = 'valve' WHERE id = CAST(:id AS uuid)"),
            {"id": entry["symbol_id"]},
        )
        connection.execute(
            text("UPDATE symbol_revisions SET payload_json = CAST(:payload AS jsonb) WHERE id = :id"),
            {"id": entry["revision_id"], "payload": json.dumps({"name": "Gate valve", "downloads": ["gate.dxf", "gate.svg"]})},
        )


def test_organization_wide_private_symbols_reach_only_their_own_organization(search_database, personal_client):
    engine, _ = search_database
    acme = _session_client(
        engine, email="search-acme@example.test", code="acme", base_role="admin",
        capabilities=("contributor", "symbol_reviewer"),
    )
    symbol_id = _make_organization_wide_symbol(acme, name="Acme search strainer")

    body = _search(acme, q="acme search strainer")
    assert [item["symbolId"] for item in body["items"]] == [symbol_id]
    assert body["items"][0]["source"] == "organization_private"
    assert _counts(_search(acme), "catalogDisciplines").get("Civil / Structural") == 1
    assert _search(acme, **{"column.scope": "organization private"})["total"] == 1
    # Private symbols have no pack, so a pack filter excludes them.
    assert _search(acme, pack="Alpha Pack", q="strainer")["total"] == 0

    # An active organization must keep an admin, so the other member is one.
    other = _session_client(engine, email="search-other@example.test", code="other", base_role="admin")
    assert _search(other, q="acme search strainer")["total"] == 0
    assert _search(personal_client, q="acme search strainer")["total"] == 0
    assert "Civil / Structural" not in _counts(_search(other), "catalogDisciplines")


def test_backfill_fills_what_the_search_would_otherwise_compute(search_database, personal_client):
    from symgov_backend.catalog_facets import backfill_catalog_facets

    engine, seeded = search_database
    _search(personal_client)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        assert backfill_catalog_facets(session, apply=False)["missingOrOutdated"] == 0
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM catalog_symbol_facets WHERE symbol_revision_id = :id"),
            {"id": seeded["Smoke detector"]["revision_id"]},
        )
    with Session() as session:
        dry_run = backfill_catalog_facets(session, apply=False)
        assert dry_run["missingOrOutdated"] == 1 and dry_run["written"] == 0
    with Session() as session:
        applied = backfill_catalog_facets(session, apply=True)
        assert applied["written"] == 1
        # Every public row, plus the organization-wide symbol the organization test made.
        assert applied["catalogRevisions"] >= len(PUBLIC_SYMBOLS)
    with Session() as session:
        assert backfill_catalog_facets(session, apply=False)["missingOrOutdated"] == 0


# -- Set tab (scope=set) -----------------------------------------------------


def _sets_client(engine):
    """`_client` with Symbol Sets switched on, as the Set tab needs."""
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from symgov_backend.app import create_app
    from symgov_backend.dependencies import get_db_session
    from symgov_backend.settings import get_settings

    client, TestingSessionLocal = _client(engine)
    base_settings = client.app.dependency_overrides[get_settings]()
    settings = replace(base_settings, symbol_sets_enabled=True)
    app = create_app()

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"})


def _sets_session(engine, *, email, code=None, base_role="user", capabilities=()):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    client = _sets_client(engine)
    user_id = _create_user_with_global_roles(Session, email=email, display_name=email.split("@")[0], roles=[])
    if code is not None:
        _add_membership(Session, user_id, code=code, base_role=base_role, capabilities=capabilities)
    _login(client, email)
    return client


def _approved_private_symbol(client, *, name):
    """Approved within the organization, but not organization-wide."""
    created = client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "valve", "discipline": "piping", "summary": "A private relief valve."},
    )
    assert created.status_code == 200, created.text
    symbol_id, revision_id = created.json()["id"], created.json()["currentRevisionId"]
    submitted = client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    decided = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submitted.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decided.status_code == 200, decided.text
    return symbol_id


def _replace_set_items(client, set_id, items):
    etag = client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items").json()["etag"]
    response = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/items", json={"items": items, "etag": etag})
    assert response.status_code == 200, response.text


@pytest.fixture(scope="module")
def set_tab(search_database):
    engine, seeded = search_database
    admin = _sets_session(
        engine, email="set-admin@example.test", code="setorg", base_role="admin",
        capabilities=("contributor", "symbol_reviewer"),
    )
    private_id = _approved_private_symbol(admin, name="Setorg relief valve")
    wide_id = _make_organization_wide_symbol(admin, name="Setorg hydrant")

    project = admin.post("/api/v1/org/me/projects", json={"code": "NORTH", "name": "North Terminal"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    created = admin.post("/api/v1/org/me/symbol-sets", json={"code": "SET-PID", "name": "P&ID core"})
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    assert admin.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": "active"}).status_code == 200
    items = [
        {"governedSymbolId": seeded["Gate valve"]["symbol_id"], "sortOrder": 1, "groupName": "Valves",
         "displayLabel": "Isolation valve", "preferredFormat": "DXF", "notes": "Manual isolation only."},
        {"governedSymbolId": seeded["Centrifugal pump"]["symbol_id"], "sortOrder": 2, "groupName": "Rotating"},
        {"governedSymbolId": private_id, "sortOrder": 3, "groupName": "Valves"},
    ]
    _replace_set_items(admin, set_id, items)
    attached = admin.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    )
    assert attached.status_code == 200, attached.text
    # A plain member, as a set's everyday reader is.
    member = _sets_session(engine, email="set-member@example.test", code="setorg")
    return {
        "admin": admin, "member": member, "project_id": project_id, "set_id": set_id, "items": items,
        "private_id": private_id, "wide_id": wide_id, "seeded": seeded,
    }


def _set_search(client, project_id, **params):
    return _search(client, scope="set", projectId=project_id, **params)


def test_set_scope_lists_the_palette_in_set_order(set_tab):
    body = _set_search(set_tab["member"], set_tab["project_id"])
    assert body["scope"] == "set"
    assert body["reason"] == "project_default"
    assert body["activeSet"]["code"] == "SET-PID"
    assert body["project"]["id"] == set_tab["project_id"]
    assert body["total"] == 4
    assert [item["symbolId"] for item in body["items"]] == [
        set_tab["seeded"]["Gate valve"]["symbol_id"],
        set_tab["seeded"]["Centrifugal pump"]["symbol_id"],
        set_tab["private_id"],
        set_tab["wide_id"],
    ]
    gate, _, private, wide = body["items"]
    assert gate["source"] == "public"
    assert gate["paletteEntry"] == {
        "source": "set", "sortOrder": 1, "groupName": "Valves", "displayLabel": "Isolation valve",
        "preferredFormat": "DXF", "notes": "Manual isolation only.",
    }
    assert private["source"] == "organization_private"
    assert private["paletteEntry"]["source"] == "set"
    assert wide["paletteEntry"]["source"] == "organization_wide"
    assert wide["paletteEntry"]["groupName"] == "Organization-wide"

    assert _counts(body, "setGroup") == {"Valves": 2, "Rotating": 1, "Organization-wide": 1}
    assert _counts(body, "paletteSource") == {"set": 3, "organization_wide": 1}


def test_set_scope_filters_search_and_sorts(set_tab):
    member, project_id = set_tab["member"], set_tab["project_id"]
    valves = _set_search(member, project_id, setGroup="Valves")
    assert [item["name"] for item in valves["items"]] == ["Gate valve", "Setorg relief valve"]
    # Counts for the set group ignore the set-group filter itself.
    assert _counts(valves, "setGroup")["Rotating"] == 1
    # The set's own label is searchable.
    assert _names(_set_search(member, project_id, q="isolation valve")) == ["Gate valve"]
    # Catalog filters work on the set, including on its private items.
    assert sorted(_names(_set_search(member, project_id, catalogCategories="Valves"))) == ["Gate valve", "Setorg relief valve"]
    by_name = _set_search(member, project_id, sort="name")
    assert _names(by_name) == ["Centrifugal pump", "Gate valve", "Setorg hydrant", "Setorg relief valve"]
    assert _set_search(member, project_id, paletteSource="organization_wide")["total"] == 1


def test_set_scope_parameters_are_checked(set_tab):
    member, project_id = set_tab["member"], set_tab["project_id"]
    for params in (
        {"scope": "set"},
        {"scope": "catalog", "projectId": project_id},
        {"scope": "catalog", "setGroup": "Valves"},
        {"scope": "catalog", "sort": "setOrder"},
    ):
        response = member.get(SEARCH, params=params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"] == "validation_error"


def test_the_set_tab_is_the_projects_organization_only(search_database, set_tab, personal_client):
    engine, _ = search_database
    project_id = set_tab["project_id"]
    outsider = _sets_session(engine, email="set-outsider@example.test", code="otherset", base_role="admin")
    assert outsider.get(SEARCH, params={"scope": "set", "projectId": project_id}).status_code == 404
    assert personal_client.get(SEARCH, params={"scope": "set", "projectId": project_id}).status_code == 404
    # With Symbol Sets off, the Set tab does not exist, as the palette route.
    flags_off = _session_client(engine, email="set-flags-off@example.test", code="setorg")
    assert flags_off.get(SEARCH, params={"scope": "set", "projectId": project_id}).status_code == 404
    # The Catalog tab of the set's own organization still lists only its
    # organization-wide private symbols, not the set-only one.
    catalog = _search(set_tab["member"], q="setorg", pageSize=200)
    assert [item["symbolId"] for item in catalog["items"]] == [set_tab["wide_id"]]


def test_a_set_only_private_symbol_opens_while_an_active_set_holds_it(search_database, set_tab):
    engine, _ = search_database
    private_id, member = set_tab["private_id"], set_tab["member"]
    detail = member.get(f"/api/v1/published/symbols/{private_id}")
    assert detail.status_code == 200, detail.text
    outsider = _sets_session(engine, email="set-outsider-detail@example.test", code="otherdetail", base_role="admin")
    assert outsider.get(f"/api/v1/published/symbols/{private_id}").status_code == 404

    remaining = [item for item in set_tab["items"] if item["governedSymbolId"] != private_id]
    _replace_set_items(set_tab["admin"], set_tab["set_id"], remaining)
    try:
        assert member.get(f"/api/v1/published/symbols/{private_id}").status_code == 404
        assert private_id not in [item["symbolId"] for item in _set_search(member, set_tab["project_id"])["items"]]
    finally:
        _replace_set_items(set_tab["admin"], set_tab["set_id"], set_tab["items"])
    assert member.get(f"/api/v1/published/symbols/{private_id}").status_code == 200
