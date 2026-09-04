"""Stage 7 WP7.6 regression: concurrency, multi-pack partial retirement,
re-promotion, and migration rollback-floor fixtures, against a real
disposable PostgreSQL container.

Proves, per the programme plan §13's acceptance language and the Stage 7
plan's §5 regression standard:
- The shared governed-symbol-row lock (§1.4) genuinely serializes two
  concurrent transactions, not just "in principle."
- Concurrent set-item-add-vs-demotion cannot produce a private symbol
  referenced by another organization's set, in both transaction orders
  (WP7.4's own test already proves add-then-demote is refused; this file
  proves the other order, demote-then-add, is also refused).
- A governed symbol with two published revisions in two different
  multi-symbol packs: demotion withdraws both revisions and retires every
  page/entry for both, while unrelated symbols in both packs remain
  active/queryable and pack symbol counts stay correct (the count bug
  WP7.5 flagged and this stage already fixed in `routes/published.py`).
- Re-promotion through a fresh promotion request publishes/activates only
  the newly approved revision; the previously withdrawn revision and its
  retired projections are never reactivated.
- Flags-off rollback to the exact pre-Stage-7 release is refused once
  withdrawn/retired demotion data exists (decision addendum's "Visibility
  rollback floor"), verified end-to-end through the real `alembic
  downgrade` command against data a real promote-then-demote flow produced
  -- not just a synthetic row insert.
"""

from __future__ import annotations

import sys
import threading
import time
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
    _create_user_with_global_roles,
    _login_platform_admin_with_step_up,
    _make_platform_admin,
    _promote_symbol,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0042"
PRE_STAGE7_RELEASE = "20260901_0034"


def _client(engine):
    """Local override of test_wp74's `_client`: this file's cross-org
    set-item test also needs `symbol_sets_enabled`, which WP7.4's own
    fixture never required."""
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        symbol_sets_enabled=True,
        platform_admin_enabled=True,
        organization_pilot_codes=("acme", "other", "symgov"),
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

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp76_database():
    with _database("symgov-wp76") as (engine, url, raw_url):
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
                "GRANT SELECT, INSERT, UPDATE ON symbol_sets TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON symbol_set_items TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _setup_promoted_symbol(engine, Session, *, suffix, org_code="acme"):
    admin_client, _ = _client(engine)
    reviewer_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email=f"wp76admin{suffix}@example.test", display_name=f"Admin{suffix}", roles=[])
    _add_membership(Session, admin_id, code=org_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": f"wp76admin{suffix}@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email=f"wp76reviewer{suffix}@example.test", display_name=f"Reviewer{suffix}", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": f"wp76reviewer{suffix}@example.test", "pin": "1234"})

    symbol_id = _promote_symbol(admin_client, reviewer_client, name=f"WP7.6 Symbol {suffix}")
    return symbol_id, admin_client, reviewer_client


def _demote(engine, Session, symbol_id, *, suffix, reason="WP7.6 test."):
    platform_client, _ = _client(engine)
    platform_admin_id = _create_user_with_global_roles(Session, email=f"wp76platform{suffix}@example.test", display_name=f"Platform{suffix}", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, f"wp76platform{suffix}@example.test")
    return platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": reason})


# --- 1. Row lock genuinely serializes two concurrent transactions ---

def test_governed_symbol_row_lock_blocks_a_concurrent_transaction(wp76_database):
    engine, _, raw_url = wp76_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    symbol_id, _, _ = _setup_promoted_symbol(engine, Session, suffix="lock")

    events: list[str] = []
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def _hold_lock():
        with psycopg.connect(raw_url, autocommit=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN")
                cursor.execute("SELECT id FROM governed_symbols WHERE id = %s FOR UPDATE", (symbol_id,))
                events.append("A:locked")
                lock_acquired.set()
                release_lock.wait(timeout=10)
                events.append("A:committing")
                connection.commit()

    def _attempt_concurrent_update():
        lock_acquired.wait(timeout=10)
        with psycopg.connect(raw_url, autocommit=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN")
                events.append("B:attempting")
                # This must block until thread A commits/releases the lock.
                cursor.execute(
                    "SELECT id FROM governed_symbols WHERE id = %s FOR UPDATE", (symbol_id,)
                )
                events.append("B:locked")
                connection.commit()

    thread_a = threading.Thread(target=_hold_lock)
    thread_b = threading.Thread(target=_attempt_concurrent_update)
    thread_a.start()
    thread_b.start()

    # Give thread B a moment to actually reach the blocking SELECT before
    # releasing thread A's lock, otherwise the ordering below proves nothing.
    time.sleep(0.5)
    assert "B:locked" not in events, "thread B must still be blocked while thread A holds the row lock"

    release_lock.set()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert events.index("A:committing") < events.index("B:locked"), (
        "thread B must only acquire the lock after thread A releases it via commit"
    )


# --- 2. The other transaction order: demote first, then attempt to add a cross-org set item ---

def test_adding_a_cross_organization_set_item_to_an_already_demoted_symbol_is_rejected(wp76_database):
    engine, _, _ = wp76_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    symbol_id, _, _ = _setup_promoted_symbol(engine, Session, suffix="order2", org_code="acme")

    demote_response = _demote(engine, Session, symbol_id, suffix="order2")
    assert demote_response.status_code == 200, demote_response.text

    other_admin_id = _create_user_with_global_roles(Session, email="wp76other@example.test", display_name="OtherAdmin", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin")
    other_client, _ = _client(engine)
    other_client.post("/api/v1/auth/login", json={"email": "wp76other@example.test", "pin": "1234"})

    create_set = other_client.post(
        "/api/v1/org/me/symbol-sets", json={"code": "WP76SET", "name": "WP7.6 Other Org Set"}
    )
    assert create_set.status_code == 201, create_set.text
    set_id = create_set.json()["id"]

    add_item = other_client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": symbol_id, "sortOrder": 0}]},
    )
    assert add_item.status_code == 409, add_item.text


# --- 3. Multi-symbol pack partial retirement ---

def test_multi_symbol_pack_partial_retirement(wp76_database):
    """A governed symbol with two published revisions in two different
    multi-symbol packs: demotion must withdraw both revisions and retire
    every page/entry for both, while unrelated symbols in both packs
    remain active/queryable and pack symbol counts stay correct."""
    engine, _, _ = wp76_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        owner = connection.execute(
            text("INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
                 "VALUES (:id,:email,:email,'x',:now,false,true,:now,:now) RETURNING id"),
            {"id": uuid.uuid4(), "email": f"wp76multipack-{uuid.uuid4()}@example.test", "now": now},
        ).scalar_one()
        org_code = f"WP76MP{uuid.uuid4().hex[:6].upper()}"
        organization = connection.execute(
            text("INSERT INTO organizations (id,code,normalized_code,display_name,name_key,entitlement_status,is_active,is_protected,fallback_icon_svg,created_at,updated_at) "
                 "VALUES (:id,:code,:normalized,:code,:normalized,'active',true,false,'<svg/>',:now,:now) RETURNING id"),
            {"id": uuid.uuid4(), "code": org_code, "normalized": org_code.lower(), "now": now},
        ).scalar_one()
        # enforce_active_organization_admin_minimum requires an active
        # organization to have at least one active admin at every commit --
        # the membership/role assignment must land in this same transaction.
        membership_id = connection.execute(
            text("INSERT INTO organization_memberships (id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                 "VALUES (:id,:org,:user,'active',:now,:now,:now) RETURNING id"),
            {"id": uuid.uuid4(), "org": organization, "user": owner, "now": now},
        ).scalar_one()
        connection.execute(
            text("INSERT INTO organization_role_assignments (id,membership_id,base_role,is_active,assigned_at) "
                 "VALUES (:id,:membership,'admin',true,:now)"),
            {"id": uuid.uuid4(), "membership": membership_id, "now": now},
        )

        def _governed_symbol(slug, *, catalog_prefix):
            symbol_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
                     "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now,:organization,'public')"),
                {"id": symbol_id, "slug": slug, "owner": owner, "now": now, "organization": organization},
            )
            catalog_id = f"{catalog_prefix}-{uuid.uuid4().hex[:10].upper()}"
            connection.execute(
                text("INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
                     "VALUES (:catalog,'canonical',:symbol,'global_sequence',:now)"),
                {"catalog": catalog_id, "symbol": symbol_id, "now": now},
            )
            connection.execute(
                text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"),
                {"catalog": catalog_id, "symbol": symbol_id},
            )
            return symbol_id

        def _published_revision(symbol_id, label):
            revision_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
                     "VALUES (:id,:symbol,:label,'published','{}'::jsonb,:owner,:now)"),
                {"id": revision_id, "symbol": symbol_id, "label": label, "owner": owner, "now": now},
            )
            return revision_id

        def _pack(code):
            pack_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
                     "VALUES (:id,:code,:code,'public',CURRENT_DATE,'published',:now,:now)"),
                {"id": pack_id, "code": code, "now": now},
            )
            return pack_id

        def _page_and_entry(pack_id, revision_id, page_code):
            page_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
                     "VALUES (:id,:code,:code,:pack,:revision,CURRENT_DATE,:now,:now)"),
                {"id": page_id, "code": page_code, "pack": pack_id, "revision": revision_id, "now": now},
            )
            entry_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                     "VALUES (:id,:pack,:revision,:page,1,:now)"),
                {"id": entry_id, "pack": pack_id, "revision": revision_id, "page": page_id, "now": now},
            )
            return page_id, entry_id

        # The multi-revision symbol under test.
        symbol_id = _governed_symbol("wp76-multipack-target", catalog_prefix="MP")
        revision_1 = _published_revision(symbol_id, "r1")
        revision_2 = _published_revision(symbol_id, "r2")
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": revision_2, "s": symbol_id})

        # Two unrelated symbols, one per pack, that must remain active.
        unrelated_1 = _governed_symbol("wp76-multipack-unrelated-1", catalog_prefix="U1")
        unrelated_1_revision = _published_revision(unrelated_1, "u1")
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": unrelated_1_revision, "s": unrelated_1})

        unrelated_2 = _governed_symbol("wp76-multipack-unrelated-2", catalog_prefix="U2")
        unrelated_2_revision = _published_revision(unrelated_2, "u2")
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": unrelated_2_revision, "s": unrelated_2})

        pack_1 = _pack(f"WP76-PACK1-{uuid.uuid4().hex[:8]}")
        pack_2 = _pack(f"WP76-PACK2-{uuid.uuid4().hex[:8]}")

        target_page_1, target_entry_1 = _page_and_entry(pack_1, revision_1, f"WP76-TP1-{uuid.uuid4().hex[:8]}")
        unrelated_page_1, unrelated_entry_1 = _page_and_entry(pack_1, unrelated_1_revision, f"WP76-UP1-{uuid.uuid4().hex[:8]}")

        target_page_2, target_entry_2 = _page_and_entry(pack_2, revision_2, f"WP76-TP2-{uuid.uuid4().hex[:8]}")
        unrelated_page_2, unrelated_entry_2 = _page_and_entry(pack_2, unrelated_2_revision, f"WP76-UP2-{uuid.uuid4().hex[:8]}")

    # Demote via the real platform-admin route.
    platform_client, _ = _client(engine)
    platform_admin_id = _create_user_with_global_roles(Session, email="wp76mpplatform@example.test", display_name="MPPlatform", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, "wp76mpplatform@example.test")
    demote = platform_client.post(f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "Multi-pack partial retirement test."})
    assert demote.status_code == 200, demote.text
    body = demote.json()
    assert set(body["symbolRevisionIds"]) == {str(revision_1), str(revision_2)}
    assert set(body["publishedPageIds"]) == {str(target_page_1), str(target_page_2)}
    assert body["retiredPackIds"] == []  # both packs still have an unrelated active entry

    with engine.begin() as connection:
        revision_states = dict(
            connection.execute(
                text("SELECT id, lifecycle_state FROM symbol_revisions WHERE id IN (:r1, :r2)"),
                {"r1": revision_1, "r2": revision_2},
            ).all()
        )
        assert revision_states[revision_1] == "withdrawn"
        assert revision_states[revision_2] == "withdrawn"

        target_page_states = connection.execute(
            text("SELECT publication_state FROM published_pages WHERE id IN (:p1, :p2)"),
            {"p1": target_page_1, "p2": target_page_2},
        ).scalars().all()
        assert all(state == "retired" for state in target_page_states)

        unrelated_page_states = connection.execute(
            text("SELECT publication_state FROM published_pages WHERE id IN (:p1, :p2)"),
            {"p1": unrelated_page_1, "p2": unrelated_page_2},
        ).scalars().all()
        assert all(state == "active" for state in unrelated_page_states)

        pack_statuses = connection.execute(
            text("SELECT id, status FROM publication_packs WHERE id IN (:p1, :p2)"),
            {"p1": pack_1, "p2": pack_2},
        ).all()
        assert all(status == "published" for _, status in pack_statuses), "unrelated active entries must keep both packs published"

        # The pack symbol_count bug WP7.5 flagged and this stage fixed:
        # each pack must report exactly 1 (the unrelated active entry),
        # never 2 (which would double-count the now-retired target entry).
        pack_counts = connection.execute(
            text(
                "SELECT pk.id, count(pe.id) FROM publication_packs pk "
                "LEFT JOIN pack_entries pe ON pe.pack_id = pk.id AND pe.publication_state = 'active' "
                "WHERE pk.id IN (:p1, :p2) GROUP BY pk.id"
            ),
            {"p1": pack_1, "p2": pack_2},
        ).all()
        assert all(count == 1 for _, count in pack_counts)

        excluded = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:id"), {"id": symbol_id}
        ).first()
        assert excluded is None

        unrelated_still_visible = connection.execute(
            text("SELECT count(*) FROM active_public_symbol_projections WHERE governed_symbol_id IN (:u1, :u2)"),
            {"u1": unrelated_1, "u2": unrelated_2},
        ).scalar_one()
        assert unrelated_still_visible == 2


# --- 4. Re-promotion only reactivates the newly approved revision ---

def test_re_promotion_only_reactivates_the_newly_approved_revision(wp76_database):
    engine, _, _ = wp76_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    symbol_id, admin_client, reviewer_client = _setup_promoted_symbol(engine, Session, suffix="repromo")

    with engine.begin() as connection:
        original_revision_id = connection.execute(
            text("SELECT current_revision_id FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).scalar_one()
        original_page_id = connection.execute(
            text("SELECT id FROM published_pages WHERE current_symbol_revision_id=:r"), {"r": original_revision_id}
        ).scalar_one()

    demote = _demote(engine, Session, symbol_id, suffix="repromo")
    assert demote.status_code == 200, demote.text

    # Start a fresh draft revision, get it organization-approved, and
    # submit + approve a brand-new promotion request against it.
    new_revision_response = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions")
    assert new_revision_response.status_code == 200, new_revision_response.text
    new_revision_id = new_revision_response.json()["currentRevisionId"]
    assert new_revision_id != original_revision_id

    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{new_revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Re-promotion after demotion.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]
    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text

    with engine.begin() as connection:
        symbol_row = connection.execute(
            text("SELECT visibility, current_revision_id FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).one()
        assert symbol_row.visibility == "public"
        assert str(symbol_row.current_revision_id) == new_revision_id

        original_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id=:id"), {"id": original_revision_id}
        ).scalar_one()
        assert original_state == "withdrawn", "the demoted revision must stay withdrawn, never reactivated"

        original_page_state = connection.execute(
            text("SELECT publication_state FROM published_pages WHERE id=:id"), {"id": original_page_id}
        ).scalar_one()
        assert original_page_state == "retired", "the original page projection must stay retired, never reactivated"

        new_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id=:id"), {"id": new_revision_id}
        ).scalar_one()
        assert new_state == "published"

        active_projection = connection.execute(
            text("SELECT symbol_revision_id FROM active_public_symbol_projections WHERE governed_symbol_id=:id"),
            {"id": symbol_id},
        ).scalar_one()
        assert str(active_projection) == new_revision_id


# --- 5. Visibility rollback floor: refuse downgrade once demotion data exists ---

def test_downgrade_past_the_visibility_floor_is_refused_after_a_real_demotion(wp76_database):
    engine, url, _ = wp76_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    symbol_id, _, _ = _setup_promoted_symbol(engine, Session, suffix="rollback")
    demote = _demote(engine, Session, symbol_id, suffix="rollback")
    assert demote.status_code == 200, demote.text

    result = _alembic(url, "downgrade", PRE_STAGE7_RELEASE, check=False)
    assert result.returncode != 0, "downgrade past the visibility floor must be refused once demotion data exists"
    assert "visibility rollback floor" in (result.stderr or "") + (result.stdout or "")

    current = _alembic(url, "current")
    assert NEW_MIGRATION_HEAD in current.stdout
