"""Stage 7 WP7.5 regression: complete reader-exclusion matrix after
demotion, against a real disposable PostgreSQL container.

This is the acceptance gate for WP7.4 (per the Stage 7 plan's own §2 note:
"this is the acceptance gate for WP7.4, not a separate feature"), not new
production behavior on its own -- it proves every reader the programme
plan §13 acceptance bar names actually excludes a demoted symbol:
"routes, aliases, assets, Favorites, and the Hannah/Whitney background
readers." Reuses the full promote-then-demote pipeline
`test_wp74_symbol_demotion_postgresql.py` already built (via its own
Postgres-backed HTTP fixtures) to get a realistic public symbol, then
demotes it and checks every reader before/after.

Real Postgres is required for the identical reasons the WP7.1-7.4 tests
already are: `active_public_symbol_projections`'s join semantics and the
`catalog_symbol_identifiers` resolver queries are not meaningfully
exercisable against SQLite.
"""

from __future__ import annotations

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
    _login_platform_admin_with_step_up,
    _make_platform_admin,
    _promote_symbol,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.catalog_symbol_resolution import resolve_catalog_symbol  # noqa: E402
from symgov_backend.models import HannahPhotoCandidate, WhitneyDemandSignal  # noqa: E402

NEW_MIGRATION_HEAD = "20260902_0037"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp75_database():
    with _database("symgov-wp75") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON catalog_symbol_identifiers TO symgov_app",
                "GRANT USAGE, SELECT ON SEQUENCE catalog_symbol_id_seq TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_cases TO symgov_app",
                "GRANT SELECT, INSERT ON human_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_case_actions TO symgov_app",
                "GRANT SELECT, INSERT ON publication_approval_targets TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT ON active_public_symbol_projections TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_sets TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_set_items TO symgov_app",
                "GRANT SELECT, INSERT, DELETE ON catalog_favourites TO symgov_app",
                "GRANT SELECT, INSERT ON hannah_photo_candidates TO symgov_app",
                "GRANT SELECT, INSERT ON whitney_demand_signals TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _setup_promoted_symbol(engine, Session, *, suffix):
    admin_client, _ = _client(engine)
    reviewer_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email=f"wp75admin{suffix}@example.test", display_name=f"Admin{suffix}", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": f"wp75admin{suffix}@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email=f"wp75reviewer{suffix}@example.test", display_name=f"Reviewer{suffix}", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": f"wp75reviewer{suffix}@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client, name=f"WP7.5 Symbol {suffix}")
    return symbol_id


def _demote(engine, Session, symbol_id, *, suffix, reason="WP7.5 reader-exclusion test."):
    platform_client, _ = _client(engine)
    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp75platform{suffix}@example.test", display_name=f"Platform{suffix}", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, f"wp75platform{suffix}@example.test")
    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": reason})
    assert demote.status_code == 200, demote.text
    return demote.json()


def test_symbol_list_and_detail_routes_exclude_the_demoted_symbol(wp75_database):
    engine, _, _ = wp75_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    reader_client, _ = _client(engine)
    _create_user_with_global_roles(Session, email="wp75reader1@example.test", display_name="Reader1", roles=[])
    reader_client.post("/api/v1/auth/login", json={"email": "wp75reader1@example.test", "pin": "1234"})

    symbol_id = _setup_promoted_symbol(engine, Session, suffix="1")

    with engine.begin() as connection:
        catalog_symbol_id = connection.execute(
            text("SELECT catalog_symbol_id FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).scalar_one()
        slug = connection.execute(text("SELECT slug FROM governed_symbols WHERE id=:id"), {"id": symbol_id}).scalar_one()
        page_code = connection.execute(
            text("SELECT page_code FROM published_pages WHERE current_symbol_revision_id="
                 "(SELECT current_revision_id FROM governed_symbols WHERE id=:id)"),
            {"id": symbol_id},
        ).scalar_one()
        pack_code = connection.execute(
            text("SELECT pk.pack_code FROM publication_packs pk JOIN published_pages pp ON pp.pack_id=pk.id WHERE pp.page_code=:page_code"),
            {"page_code": page_code},
        ).scalar_one()

    # --- Before demotion: visible everywhere. ---
    list_before = reader_client.get("/api/v1/published/symbols", params={"q": "WP7.5 Symbol 1"})
    assert list_before.status_code == 200
    assert any(item.get("catalogSymbolId") == catalog_symbol_id or item.get("symbolId") == symbol_id for item in list_before.json().get("items", []))

    detail_before = reader_client.get(f"/api/v1/published/symbols/{catalog_symbol_id}")
    assert detail_before.status_code == 200, detail_before.text

    page_before = reader_client.get(f"/api/v1/published/pages/{page_code}")
    assert page_before.status_code == 200, page_before.text

    packs_before = reader_client.get("/api/v1/published/packs")
    assert packs_before.status_code == 200
    assert any(item["packCode"] == pack_code for item in packs_before.json()["items"])

    assert resolve_catalog_symbol(Session(), catalog_symbol_id) is not None

    # --- Demote. ---
    _demote(engine, Session, symbol_id, suffix="1")

    # --- After demotion: excluded from every route above. ---
    detail_after = reader_client.get(f"/api/v1/published/symbols/{catalog_symbol_id}")
    assert detail_after.status_code == 404, detail_after.text

    detail_after_by_uuid = reader_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail_after_by_uuid.status_code == 404

    detail_after_by_slug = reader_client.get(f"/api/v1/published/symbols/{slug}")
    assert detail_after_by_slug.status_code == 404

    list_after = reader_client.get("/api/v1/published/symbols", params={"q": "WP7.5 Symbol 1"})
    assert list_after.status_code == 200
    assert not any(item.get("symbolId") == symbol_id for item in list_after.json().get("items", []))

    page_after = reader_client.get(f"/api/v1/published/pages/{page_code}")
    assert page_after.status_code == 404, page_after.text

    packs_after = reader_client.get("/api/v1/published/packs")
    assert packs_after.status_code == 200
    assert not any(item["packCode"] == pack_code for item in packs_after.json()["items"]), (
        "the org-promotion pack has exactly one entry, so it must be fully retired and excluded"
    )

    # The canonical resolver alias path must not bypass visibility either --
    # confirmed empirically, not just read: every resolution branch in
    # resolve_catalog_symbol requires governed_symbols.catalog_symbol_id IS
    # NOT NULL, and execute_demotion clears that column, so canonical,
    # uuid, slug, and historical_alias lookups all fail closed together.
    with Session() as session:
        assert resolve_catalog_symbol(session, catalog_symbol_id) is None
        assert resolve_catalog_symbol(session, symbol_id) is None
        assert resolve_catalog_symbol(session, slug) is None
        assert resolve_catalog_symbol(session, page_code) is None


def test_favourites_hide_details_but_do_not_leak_or_crash(wp75_database):
    engine, _, _ = wp75_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    reader_client, _ = _client(engine)
    _create_user_with_global_roles(Session, email="wp75reader2@example.test", display_name="Reader2", roles=[])
    reader_client.post("/api/v1/auth/login", json={"email": "wp75reader2@example.test", "pin": "1234"})

    symbol_id = _setup_promoted_symbol(engine, Session, suffix="2")

    favourite_response = reader_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert favourite_response.status_code == 200, favourite_response.text

    list_before = reader_client.get("/api/v1/published/favourites")
    assert list_before.status_code == 200
    assert any(item["symbolId"] == symbol_id for item in list_before.json()["items"])

    _demote(engine, Session, symbol_id, suffix="2")

    # Favorites list still returns the bare (stale) ID -- existing,
    # deliberate behavior (per the spec's Stage 8 language: historical rows
    # remain for restoration/audit) -- but carries no symbol details, and
    # the detail route itself must 404.
    list_after = reader_client.get("/api/v1/published/favourites")
    assert list_after.status_code == 200
    favourite_ids_after = [item["symbolId"] for item in list_after.json()["items"]]
    assert symbol_id in favourite_ids_after
    for item in list_after.json()["items"]:
        assert set(item.keys()) == {"symbolId"}, "favourites list must never carry hidden-symbol details"

    detail_after = reader_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail_after.status_code == 404


def test_hannah_and_whitney_background_readers_exclude_the_demoted_symbol(wp75_database):
    engine, _, _ = wp75_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    symbol_id = _setup_promoted_symbol(engine, Session, suffix="3")

    reviewer_client, _ = _client(engine)
    # Hannah/Whitney read routes require the global "admin" role specifically
    # (WORKSPACE_POLICY_BY_OPERATION classifies them "admin", not
    # "reviewer_admin", unlike the review-case decision endpoint).
    _create_user_with_global_roles(Session, email="wp75hannah@example.test", display_name="HannahReviewer", roles=["admin"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp75hannah@example.test", "pin": "1234"})

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        page_id = connection.execute(
            text("SELECT id FROM published_pages WHERE current_symbol_revision_id="
                 "(SELECT current_revision_id FROM governed_symbols WHERE id=:id)"),
            {"id": symbol_id},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO hannah_photo_candidates "
                "(id,symbol_id,published_page_id,source_url,image_url,source_domain,rights_status,status,evidence_json,first_seen_at,last_seen_at) "
                "VALUES (:id,:symbol,:page,'https://example.test/src','https://example.test/img.jpg','example.test','cleared','new','{}'::jsonb,:now,:now)"
            ),
            {"id": uuid.uuid4(), "symbol": symbol_id, "page": page_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO whitney_demand_signals "
                "(id,symbol_id,published_page_id,signal_type,source_type,title,summary,status,evidence_json,first_seen_at,last_seen_at) "
                "VALUES (:id,:symbol,:page,'search_gap','manual','WP7.5 signal','test','new','{}'::jsonb,:now,:now)"
            ),
            {"id": uuid.uuid4(), "symbol": symbol_id, "page": page_id, "now": now},
        )

    hannah_before = reviewer_client.get("/api/v1/workspace/hannah/photo-candidates")
    assert hannah_before.status_code == 200, hannah_before.text
    assert any(item["symbolId"] == symbol_id for item in hannah_before.json()["items"])

    whitney_before = reviewer_client.get("/api/v1/workspace/whitney/demand-signals")
    assert whitney_before.status_code == 200, whitney_before.text
    assert any(item.get("symbolId") == symbol_id for item in whitney_before.json()["items"])

    _demote(engine, Session, symbol_id, suffix="3")

    hannah_after = reviewer_client.get("/api/v1/workspace/hannah/photo-candidates")
    assert hannah_after.status_code == 200, hannah_after.text
    assert not any(item["symbolId"] == symbol_id for item in hannah_after.json()["items"]), (
        "Hannah must exclude a demoted symbol's photo candidates even though the row itself still exists"
    )

    whitney_after = reviewer_client.get("/api/v1/workspace/whitney/demand-signals")
    assert whitney_after.status_code == 200, whitney_after.text
    assert not any(item.get("symbolId") == symbol_id for item in whitney_after.json()["items"]), (
        "Whitney must exclude a demoted symbol's demand signal even though the row itself still exists"
    )

    # The underlying tracking rows are never deleted by demotion.
    with engine.begin() as connection:
        hannah_count = connection.execute(
            text("SELECT count(*) FROM hannah_photo_candidates WHERE symbol_id=:id"), {"id": symbol_id}
        ).scalar_one()
        whitney_count = connection.execute(
            text("SELECT count(*) FROM whitney_demand_signals WHERE symbol_id=:id"), {"id": symbol_id}
        ).scalar_one()
        assert hannah_count == 1
        assert whitney_count == 1
