"""Stage 8 WP8.3 regression: Favourites tenant/session-context behavior for
organization-private symbols, against a real disposable PostgreSQL container.

Per the Stage 8 plan
(`docs/plans/2026-09-03-symbol-set-management-stage8-implementation-plan.md`,
WP8.3/SS1.3), the actual gap found on inspection was narrower than the plan's
own working assumption: every *enrichment* call site of
`load_favourite_symbol_ids` in `routes/published.py` (the WP8.1 list, the
WP8.2 detail, and the pre-existing `get_published_page`) already only ever
marks `isFavourite` for symbols already present in that endpoint's own
tenant-scoped result set -- there was never a leak to close there, since a
different organization's private symbol can never appear in that set to
begin with. The real, fixed gap was `PUT /favourites/{symbol_ref}` (add):
before WP8.3 it only resolved via the public-only
`_load_published_symbol_row`, so an organization-private symbol could never
be favourited at all. This file proves, empirically, both halves: the fix
(an organization-private symbol can now be favourited by its own
organization's session, and not by any other session), and the pre-existing
"no leak" claim about the enrichment call sites (never proven before, only
reasoned about in the WP8.1/WP8.2 implementation notes).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
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

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

NEW_MIGRATION_HEAD = "20260904_0039"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp83_database():
    with _database("symgov-wp83") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT, INSERT, DELETE ON catalog_favourites TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def test_owning_organization_can_favourite_its_own_organization_wide_symbol(wp83_database):
    engine, _, _ = wp83_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp83acme1@example.test", display_name="AcmeAdmin1", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp83acme1@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.3 Favouritable Hydrant")

    add = acme_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 200, add.text
    assert add.json() == {"symbolId": symbol_id, "isFavourite": True}

    bare_list = acme_client.get("/api/v1/published/favourites")
    assert bare_list.status_code == 200
    assert any(item["symbolId"] == symbol_id for item in bare_list.json()["items"])

    listing = acme_client.get("/api/v1/published/symbols", params={"q": "WP8.3 Favouritable Hydrant"})
    items = [item for item in listing.json()["items"] if item["symbolId"] == symbol_id]
    assert len(items) == 1
    assert items[0]["isFavourite"] is True

    detail = acme_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 200
    assert detail.json()["item"]["isFavourite"] is True


def test_a_different_organizations_session_cannot_favourite_it(wp83_database):
    engine, _, _ = wp83_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp83acme2@example.test", display_name="AcmeAdmin2", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp83acme2@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.3 Cross-Tenant Favourite Hydrant")

    other_admin_id = _create_user_with_global_roles(Session, email="wp83other@example.test", display_name="OtherAdmin", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(other_client, "wp83other@example.test")

    add = other_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 404, add.text


def test_favourite_added_in_one_organization_never_leaks_into_another_organizations_list_or_detail(wp83_database):
    """The proof the WP8.1/WP8.2 implementation notes only reasoned about:
    a favourite recorded while org A was active must not surface -- as a
    full item, or as an `isFavourite` flag on some other item -- when a
    *different* organization's session later calls the same endpoints."""
    engine, _, _ = wp83_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp83acme3@example.test", display_name="AcmeAdmin3", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp83acme3@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.3 No Cross-Org Leak Hydrant")
    add = acme_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 200, add.text

    other_admin_id = _create_user_with_global_roles(Session, email="wp83other2@example.test", display_name="OtherAdmin2", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(other_client, "wp83other2@example.test")

    listing = other_client.get("/api/v1/published/symbols", params={"q": "WP8.3 No Cross-Org Leak Hydrant"})
    assert listing.status_code == 200
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"])

    detail = other_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text

    other_bare_list = other_client.get("/api/v1/published/favourites")
    assert other_bare_list.status_code == 200
    assert not any(item["symbolId"] == symbol_id for item in other_bare_list.json()["items"]), (
        "the bare favourites list is per-user (not per-organization) -- a different "
        "user in a different organization must never see another user's favourite at all"
    )


def _login_choosing_organization(client, email, *, organization_code):
    """A genuinely multi-membership login: `POST /auth/login` returns a
    `selectionChallenge` (not a session) once a user has more than one
    eligible organization membership (`issue_application_context`,
    `routes/auth.py:180-227`) -- completed via `POST /auth/select-organization`
    with the token and the chosen organization's id. This is the real
    mechanism for the *same user* to end up in a different organization's
    session across logins, which plan SS1.3's "later queried while a
    different organization's session is active" scenario actually requires
    -- not something a single-membership login can produce."""
    login = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert login.status_code == 200, login.text
    body = login.json()
    challenge = body["selectionChallenge"]
    assert challenge is not None, "expected a multi-membership selection challenge"
    choice = next(item for item in challenge["choices"] if item["code"] == organization_code)
    selected = client.post(
        "/api/v1/auth/select-organization",
        json={"token": challenge["token"], "organizationId": choice["organizationId"]},
    )
    assert selected.status_code == 200, selected.text
    return selected.json()


def test_the_owning_user_sees_the_stale_favourite_id_from_any_session_but_no_detail_leaks(wp83_database):
    """Per plan SS1.3 (FR-CTX-008): the *same user's* bare favourites list
    deliberately keeps listing a stale/hidden favourite ID regardless of
    which session they're currently in -- this is existing, intentional
    behavior (already proved for demotion in Stage 7's WP7.5), and WP8.3
    generalizes the same claim to organization-context specifically. No
    enriched detail leaks alongside that bare ID from the non-owning session."""
    engine, _, _ = wp83_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_org_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp83acme4@example.test", display_name="AcmeAdmin4", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp83acme4@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.3 Stale ID Hydrant")
    add = acme_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 200, add.text

    _add_membership(Session, acme_admin_id, code="other", base_role="user")
    session_response = _login_choosing_organization(other_org_client, "wp83acme4@example.test", organization_code="OTHER")
    assert session_response["user"]["session"]["mode"] == "organization"
    assert session_response["user"]["organization"]["code"] == "OTHER"

    bare_list = other_org_client.get("/api/v1/published/favourites")
    assert bare_list.status_code == 200
    assert any(item["symbolId"] == symbol_id for item in bare_list.json()["items"]), (
        "the bare favourites list must keep listing the stale ID even from a "
        "different active organization -- deliberate existing behavior, per plan SS1.3"
    )
    assert bare_list.json()["items"] == [{"symbolId": symbol_id}], "no other field may leak alongside the bare ID"

    detail = other_org_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 404, detail.text

    listing = other_org_client.get("/api/v1/published/symbols", params={"q": "WP8.3 Stale ID Hydrant"})
    assert listing.status_code == 200
    assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"])


def test_removal_works_regardless_of_which_session_is_currently_active(wp83_database):
    """Per plan SS1.3: the owning user may safely remove a stale/hidden
    favourite from any session. Proves this for an organization-private
    symbol specifically, using the existing remove-by-UUID fast path, from
    the *same user's* genuinely different (`OTHER`) organization session."""
    engine, _, _ = wp83_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_org_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp83acme5@example.test", display_name="AcmeAdmin5", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp83acme5@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.3 Removable Hydrant")
    add = acme_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 200, add.text

    _add_membership(Session, acme_admin_id, code="other", base_role="user")
    session_response = _login_choosing_organization(other_org_client, "wp83acme5@example.test", organization_code="OTHER")
    assert session_response["user"]["organization"]["code"] == "OTHER"

    remove = other_org_client.delete(f"/api/v1/published/favourites/{symbol_id}")
    assert remove.status_code == 200, remove.text
    assert remove.json() == {"symbolId": symbol_id, "isFavourite": False}

    bare_list = other_org_client.get("/api/v1/published/favourites")
    assert bare_list.status_code == 200
    assert not any(item["symbolId"] == symbol_id for item in bare_list.json()["items"])
