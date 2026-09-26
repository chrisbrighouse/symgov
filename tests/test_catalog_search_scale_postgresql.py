"""Scale evidence for the Catalog's database search (Set/Catalog tab design, step 3).

Opt-in: set `SYMGOV_CATALOG_SCALE_TEST=1`. It seeds 50,000 synthetic public
symbols into a disposable PostgreSQL container, which the regular sweep
should not pay for. `SYMGOV_CATALOG_SCALE_EVIDENCE=<path>` also writes the
measurements as JSON.

Volumes are D8 as amended by Chris on 2026-09-25: 50,000 public symbols in
the Catalog, and a Symbol Set at the product's own ceiling of 1,000 items,
built through the real items API. The data never leaves the container.

Targets are WP11.5's P95 goals: Catalog search under 1.5 s for the Catalog
tab, and a representative effective-palette query under 1 s for the Set tab,
which is a palette query (chosen by Chris on 2026-09-26 over the 750 ms
Project/set switch goal).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

if os.environ.get("SYMGOV_CATALOG_SCALE_TEST") != "1":
    pytest.skip("Set SYMGOV_CATALOG_SCALE_TEST=1 to run the Catalog scale evidence.", allow_module_level=True)

from test_catalog_browse_search_postgresql import (  # noqa: E402
    SEARCH,
    _make_organization_wide_symbol,
    _replace_set_items,
    _sets_session,
)
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

MIGRATION_HEAD = "20260926_0066"
PUBLIC_SYMBOLS = 50_000
PRIVATE_ORGANIZATION_WIDE_SYMBOLS = 500
SET_ITEMS = 1_000
RUNS_PER_QUERY = 15
CATALOG_P95_TARGET_SECONDS = 1.5
SET_P95_TARGET_SECONDS = 1.0

psycopg = pytest.importorskip("psycopg")

# Short codes the product actually maps, so the facet rules do real work.
CATEGORIES = [
    "valve", "pump", "vessel", "pipe", "instrument", "motor", "lighting", "actuator",
    "control", "tag", "detector", "cylinder", "symbol", "fitting", "strainer",
]
DISCIPLINES = [
    "piping", "process", "mechanical", "electrical", "instrumentation", "hvac",
    "civil", "fire_alarm", "general", "safety",
]
DOWNLOAD_MIXES = [
    ["s.dxf", "s.svg"], ["s.svg"], ["s.dxf"], ["s.png"], ["s.dwg", "s.pdf"], [], ["s.dxf", "s.png", "s.svg"],
]

SEED_SQL = """
CREATE TEMP TABLE scale_seed ON COMMIT DROP AS
SELECT
    n,
    gen_random_uuid() AS symbol_id,
    gen_random_uuid() AS revision_id,
    gen_random_uuid() AS page_id,
    gen_random_uuid() AS entry_id,
    gen_random_uuid() AS extra_page_id,
    gen_random_uuid() AS extra_entry_id,
    'SCALE-' || lpad(n::text, 6, '0') AS catalog_id,
    (CAST(:categories AS text[]))[1 + n % :category_count] AS category,
    (CAST(:disciplines AS text[]))[1 + (n / 7) % :discipline_count] AS discipline,
    n % :download_mix_count AS mix
FROM generate_series(1, :public_symbols) AS n;

INSERT INTO governed_symbols (id, slug, canonical_name, category, discipline, owner_id, created_at, updated_at)
SELECT symbol_id, 'scale-' || n, 'Scale ' || category || ' ' || n, category, discipline, :owner, :now, :now
FROM scale_seed;

INSERT INTO symbol_revisions (id, symbol_id, revision_label, lifecycle_state, payload_json, author_id, created_at)
SELECT
    revision_id, symbol_id, 'A', 'published',
    jsonb_build_object(
        'name', 'Scale ' || category || ' ' || n,
        'summary', 'Synthetic ' || category || ' symbol for the scale test'
            || CASE WHEN n % 37 = 0 THEN ', smoke rated' ELSE '' END,
        'keywords', jsonb_build_array('scale', 'batch-' || (n % 50)),
        'downloads', (CAST(:download_mixes AS jsonb))->(mix)
    ),
    :owner, :now
FROM scale_seed;

INSERT INTO catalog_symbol_identifiers (identifier, role, governed_symbol_id, allocation_source, allocated_at)
SELECT catalog_id, 'canonical', symbol_id, 'global_sequence', now() FROM scale_seed;

UPDATE governed_symbols gs
SET current_revision_id = s.revision_id, catalog_symbol_id = s.catalog_id
FROM scale_seed s
WHERE gs.id = s.symbol_id;

INSERT INTO publication_packs (id, pack_code, title, audience, effective_date, status, created_at, updated_at)
SELECT
    (SELECT md5('scale-pack-' || p)::uuid), 'SCALE-PACK-' || p, 'Scale Pack ' || p,
    'public', DATE '2026-09-01' + p, 'published', :now, :now
FROM generate_series(0, 9) AS p;

INSERT INTO published_pages (id, page_code, title, pack_id, current_symbol_revision_id, effective_date, created_at, updated_at)
SELECT page_id, 'SCALE-PAGE-' || n, 'Scale ' || n, md5('scale-pack-' || (n % 10))::uuid, revision_id, DATE '2026-09-01', :now, :now
FROM scale_seed;

INSERT INTO pack_entries (id, pack_id, symbol_revision_id, published_page_id, sort_order, created_at)
SELECT entry_id, md5('scale-pack-' || (n % 10))::uuid, revision_id, page_id, n, :now
FROM scale_seed;

-- One symbol in twenty is also published in a second pack.
INSERT INTO published_pages (id, page_code, title, pack_id, current_symbol_revision_id, effective_date, created_at, updated_at)
SELECT extra_page_id, 'SCALE-PAGE-X-' || n, 'Scale ' || n, md5('scale-pack-' || ((n + 5) % 10))::uuid, revision_id, DATE '2026-09-01', :now, :now
FROM scale_seed WHERE n % 20 = 0;

INSERT INTO pack_entries (id, pack_id, symbol_revision_id, published_page_id, sort_order, created_at)
SELECT extra_entry_id, md5('scale-pack-' || ((n + 5) % 10))::uuid, revision_id, extra_page_id, n, :now
FROM scale_seed WHERE n % 20 = 0;
"""

PRIVATE_SEED_SQL = """
CREATE TEMP TABLE scale_private ON COMMIT DROP AS
SELECT n, gen_random_uuid() AS symbol_id, gen_random_uuid() AS revision_id,
       gen_random_uuid() AS submission_id, gen_random_uuid() AS decision_id
FROM generate_series(1, :count) AS n;

INSERT INTO governed_symbols (id, slug, canonical_name, category, discipline, owner_id, owner_organization_id,
                              visibility, organization_wide, created_at, updated_at)
SELECT symbol_id, 'scale-private-' || n, 'Scale private ' || n, 'valve', 'piping', :owner, :org,
       'organization_private', false, :now, :now
FROM scale_private;

INSERT INTO symbol_revisions (id, symbol_id, revision_label, lifecycle_state, payload_json, author_id, created_at)
SELECT revision_id, symbol_id, '1', 'approved',
       jsonb_build_object('name', 'Scale private ' || n, 'downloads', jsonb_build_array('p.svg')), :owner, :now
FROM scale_private;

UPDATE governed_symbols gs SET current_revision_id = p.revision_id
FROM scale_private p WHERE gs.id = p.symbol_id;

INSERT INTO organization_symbol_review_submissions (id, organization_id, governed_symbol_id, symbol_revision_id,
                                                    submitted_by_user_id, submitted_at, status, closed_at)
SELECT submission_id, :org, symbol_id, revision_id, :owner, :now, 'closed', :now FROM scale_private;

INSERT INTO organization_symbol_review_decisions (id, submission_id, organization_id, governed_symbol_id,
                                                  symbol_revision_id, decided_by_user_id, decision, decided_at)
SELECT decision_id, submission_id, :org, symbol_id, revision_id, :owner, 'approved', :now FROM scale_private;

UPDATE governed_symbols gs SET organization_wide = true
FROM scale_private p WHERE gs.id = p.symbol_id;
"""


def _timed(fn):
    started = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - started


def _p95(samples):
    ordered = sorted(samples)
    return ordered[max(0, int(round(0.95 * len(ordered))) - 1)]


def _measure(client, params, runs=RUNS_PER_QUERY):
    """Steady-state timings: one discarded warm-up call, then `runs` timed ones.

    The warm-up's own time is kept as `firstCall`, so a cold cache is
    reported rather than hidden.
    """
    warm_up, first_call = _timed(lambda: client.get(SEARCH, params=params))
    assert warm_up.status_code == 200, warm_up.text
    samples = []
    body = None
    for _ in range(runs):
        response, seconds = _timed(lambda: client.get(SEARCH, params=params))
        assert response.status_code == 200, response.text
        body = response.json()
        samples.append(seconds)
    return {
        "p95": round(_p95(samples), 4),
        "median": round(statistics.median(samples), 4),
        "max": round(max(samples), 4),
        "firstCall": round(first_call, 4),
        "total": body["total"],
    }


@pytest.fixture(scope="module")
def scale_catalog():
    from datetime import datetime, timezone

    with _database("symgov-catalog-scale") as (engine, url, raw_url):
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

        admin = _sets_session(
            engine, email="scale-admin@example.test", code="scaleorg", base_role="admin",
            capabilities=("contributor", "symbol_reviewer"),
        )
        # One real organization-wide symbol first, so the organization exists
        # with a genuine approval trail alongside the seeded ones.
        _make_organization_wide_symbol(admin, name="Scale real hydrant")
        member = _sets_session(engine, email="scale-member@example.test", code="scaleorg")

        now = datetime.now(timezone.utc).replace(microsecond=0)
        with engine.begin() as connection:
            owner = connection.execute(text("SELECT id FROM users WHERE email = 'scale-admin@example.test'")).scalar_one()
            organization = connection.execute(
                text("SELECT id FROM organizations WHERE normalized_code = 'scaleorg'")
            ).scalar_one()
        seed_params = {
            "categories": CATEGORIES,
            "category_count": len(CATEGORIES),
            "disciplines": DISCIPLINES,
            "discipline_count": len(DISCIPLINES),
            "download_mixes": json.dumps(DOWNLOAD_MIXES),
            "download_mix_count": len(DOWNLOAD_MIXES),
            "public_symbols": PUBLIC_SYMBOLS,
            "owner": owner,
            "now": now,
        }

        def seed():
            with engine.begin() as connection:
                for statement in SEED_SQL.split(";\n"):
                    if statement.strip():
                        connection.execute(text(statement), seed_params)
                for statement in PRIVATE_SEED_SQL.split(";\n"):
                    if statement.strip():
                        connection.execute(
                            text(statement),
                            {"count": PRIVATE_ORGANIZATION_WIDE_SYMBOLS, "owner": owner, "org": organization, "now": now},
                        )

        _, seed_seconds = _timed(seed)
        # Production tables are autovacuumed; freshly bulk-loaded ones are
        # not, and would make every first scan pay for it.
        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute("VACUUM ANALYZE")

        with engine.begin() as connection:
            set_symbol_ids = [
                str(row[0])
                for row in connection.execute(
                    text("SELECT id FROM governed_symbols WHERE slug LIKE 'scale-%' AND visibility = 'public' ORDER BY slug LIMIT :n"),
                    {"n": SET_ITEMS},
                )
            ]
        project = admin.post("/api/v1/org/me/projects", json={"code": "SCALE", "name": "Scale project"})
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        created = admin.post("/api/v1/org/me/symbol-sets", json={"code": "SCALE-SET", "name": "Scale set"})
        assert created.status_code == 201, created.text
        set_id = created.json()["id"]
        assert admin.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": "active"}).status_code == 200
        groups = ["Valves", "Rotating", "Instruments", "Electrical"]
        _replace_set_items(
            admin,
            set_id,
            [
                {"governedSymbolId": symbol_id, "sortOrder": index, "groupName": groups[index % len(groups)]}
                for index, symbol_id in enumerate(set_symbol_ids)
            ],
        )
        attached = admin.put(
            f"/api/v1/org/me/symbol-sets/{set_id}/projects",
            json={"projects": [{"projectId": project_id, "isDefault": True}]},
        )
        assert attached.status_code == 200, attached.text

        yield {
            "engine": engine,
            "raw_url": raw_url,
            "member": member,
            "project_id": project_id,
            "seed_seconds": round(seed_seconds, 2),
        }


def test_catalog_and_set_search_meet_the_wp11_5_targets_at_scale(scale_catalog):
    from symgov_backend.catalog_facets import backfill_catalog_facets

    engine, member, project_id = scale_catalog["engine"], scale_catalog["member"], scale_catalog["project_id"]
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    evidence: dict = {
        "publicSymbols": PUBLIC_SYMBOLS,
        "organizationWidePrivateSymbols": PRIVATE_ORGANIZATION_WIDE_SYMBOLS + 1,
        "setItems": SET_ITEMS,
        "runsPerQuery": RUNS_PER_QUERY,
        "seedSeconds": scale_catalog["seed_seconds"],
    }

    with Session() as session:
        backfill, backfill_seconds = _timed(lambda: backfill_catalog_facets(session, apply=True))
    evidence["backfill"] = {**backfill, "seconds": round(backfill_seconds, 2)}
    assert backfill["written"] >= PUBLIC_SYMBOLS
    with psycopg.connect(scale_catalog["raw_url"], autocommit=True) as connection:
        connection.execute("VACUUM ANALYZE catalog_symbol_facets")

    # A favourite, so the favourites filter has something to find.
    first = member.get(SEARCH, params={"pageSize": 1}).json()["items"][0]
    assert member.put(f"/api/v1/published/favourites/{first['symbolId']}").status_code == 200

    catalog_queries = {
        "first page": {},
        "text search": {"q": "valve"},
        "rare text search": {"q": "scale pump 4243"},
        "one discipline": {"catalogDisciplines": "Piping / P&ID"},
        "two categories and a format": {"catalogCategories": ["Valves", "Pumps"], "availableFormats": "DXF"},
        "sort by name, descending": {"sort": "name", "direction": "desc"},
        "deep page": {"page": 800},
        "favourites": {"favourites": "true"},
        "column filter": {"column.name": "4242"},
        "preferred formats": {"preferredFormats": ["SVG", "DXF"]},
    }
    set_queries = {
        "set, first page": {"scope": "set", "projectId": project_id},
        "set, one group": {"scope": "set", "projectId": project_id, "setGroup": "Valves"},
        "set, text search": {"scope": "set", "projectId": project_id, "q": "valve"},
        "set, sort by name": {"scope": "set", "projectId": project_id, "sort": "name"},
    }
    evidence["catalog"] = {name: _measure(member, params) for name, params in catalog_queries.items()}
    evidence["set"] = {name: _measure(member, params) for name, params in set_queries.items()}

    # One revision changes, so the next search recomputes exactly one row.
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE symbol_revisions SET payload_json = payload_json || '{\"summary\": \"changed\"}'::jsonb "
            "WHERE id = (SELECT current_revision_id FROM governed_symbols WHERE slug = 'scale-1')"
        ))
    _, refill_seconds = _timed(lambda: member.get(SEARCH))
    evidence["searchAfterOneRevisionChanged"] = round(refill_seconds, 4)

    output = os.environ.get("SYMGOV_CATALOG_SCALE_EVIDENCE")
    if output:
        Path(output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))

    assert evidence["catalog"]["first page"]["total"] == PUBLIC_SYMBOLS + PUBLIC_SYMBOLS // 20 + PRIVATE_ORGANIZATION_WIDE_SYMBOLS + 1
    assert evidence["set"]["set, first page"]["total"] == SET_ITEMS + PRIVATE_ORGANIZATION_WIDE_SYMBOLS + 1
    slow_catalog = {name: m["p95"] for name, m in evidence["catalog"].items() if m["p95"] >= CATALOG_P95_TARGET_SECONDS}
    slow_set = {name: m["p95"] for name, m in evidence["set"].items() if m["p95"] >= SET_P95_TARGET_SECONDS}
    assert not slow_catalog, f"Catalog queries over the {CATALOG_P95_TARGET_SECONDS}s P95 target: {slow_catalog}"
    assert not slow_set, f"Set queries over the {SET_P95_TARGET_SECONDS}s P95 target: {slow_set}"
