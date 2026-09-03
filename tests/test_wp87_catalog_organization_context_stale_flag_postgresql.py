"""Stage 8 WP8.7 regression: a demoted symbol's stale `organization_wide`
flag must not resurface it in its owning organization's own Catalog
listing/detail, against a real disposable PostgreSQL container.

Found during WP8.7's Contract Review, not guessed in advance: `symbol_demotion.py`
flips `visibility` back to `organization_private` on demotion but never touches
`organization_wide` -- that flag is only ever validated at the moment
`organization_symbol_review.set_organization_wide` toggles it on, and is only
cleared later, when `create_new_draft_revision` starts a fresh review cycle.
Between demotion and that next draft, a symbol that was `organization_wide=true`
before its (now-withdrawn) promotion still carries that flag, with a current
revision `lifecycle_state='withdrawn'`. `catalog_organization_context.py`'s
list/detail queries now require `lifecycle_state == 'approved'` on the current
revision specifically to close this window -- this is not a cross-tenant leak
(the symbol was always correctly scoped to its owning organization), but the
owning organization's own Catalog should not browse a withdrawn symbol.
"""

from __future__ import annotations

import sys
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
)

NEW_MIGRATION_HEAD = "20260902_0037"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp87_database():
    with _database("symgov-wp87") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT ON catalog_symbol_identifiers TO symgov_app",
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
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _create_organization_wide_symbol_then_promote_and_demote(admin_client, reviewer_client, platform_client, *, name):
    """Toggle organization-wide on *before* promoting, then promote and
    immediately demote -- the exact sequence that leaves `organization_wide`
    stale after `symbol_demotion.py` flips `visibility` back."""
    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]

    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    toggle = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/organization-wide", json={"enabled": True})
    assert toggle.status_code == 200, toggle.text
    assert toggle.json()["organizationWide"] is True

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text

    demote = platform_client.post(
        f"/api/v1/platform/governed-symbols/{symbol_id}/demote",
        json={"reason": "WP8.7 regression fixture: promote then demote a formerly organization-wide symbol."},
    )
    assert demote.status_code == 200, demote.text
    assert demote.json()["visibility"] == "organization_private"

    return symbol_id


def test_a_demoted_formerly_organization_wide_symbol_stays_out_of_its_own_organizations_catalog(wp87_database):
    engine, _, _ = wp87_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    admin_client, _ = _client(engine)
    reviewer_client, _ = _client(engine)
    platform_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(Session, email="wp87admin@example.test", display_name="WP8.7 Admin", roles=[])
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    admin_client.post("/api/v1/auth/login", json={"email": "wp87admin@example.test", "pin": "1234"})

    _create_user_with_global_roles(Session, email="wp87reviewer@example.test", display_name="WP8.7 Reviewer", roles=["reviewer"])
    reviewer_client.post("/api/v1/auth/login", json={"email": "wp87reviewer@example.test", "pin": "1234"})

    platform_admin_id = _create_user_with_global_roles(Session, email="wp87platform@example.test", display_name="WP8.7 Platform", roles=[])
    _make_platform_admin(Session, platform_admin_id)
    _login_platform_admin_with_step_up(platform_client, "wp87platform@example.test")

    symbol_id = _create_organization_wide_symbol_then_promote_and_demote(
        admin_client, reviewer_client, platform_client, name="WP8.7 Stale Flag Hydrant"
    )

    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT organization_wide, visibility FROM governed_symbols WHERE id=:id"),
            {"id": symbol_id},
        ).one()
        assert row.organization_wide is True, "fixture must reproduce the stale organization_wide=true precondition"
        assert row.visibility == "organization_private"

    listing = admin_client.get("/api/v1/published/symbols", params={"q": "WP8.7 Stale Flag Hydrant"})
    assert listing.status_code == 200, listing.text
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"]), (
        "a demoted symbol with a stale organization_wide=true flag must not "
        "resurface in its own owning organization's Catalog list"
    )

    detail = admin_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text
