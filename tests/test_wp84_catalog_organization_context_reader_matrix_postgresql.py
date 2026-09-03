"""Stage 8 WP8.4 regression: the reader-exclusion / cross-tenant matrix for
the organization-aware Catalog surface (WP8.1-8.3), against a real
disposable PostgreSQL container.

Mirrors Stage 7's WP7.5 methodology exactly (per the Stage 8 plan
`docs/plans/2026-09-03-symbol-set-management-stage8-implementation-plan.md`,
WP8.4): this is the acceptance gate for WP8.1-8.3, not a separate feature.
WP8.1/8.2/8.3 already each proved their own endpoint's cross-tenant
isolation individually, against their own dedicated fixture symbols. This
file adds two things those did not cover:

1. Readers WP8.1-8.3 never touched at all: `GET /symbols/{id}/comments`,
   `POST /symbols/commands`, and -- the most important one, since it is
   this repository's *other* Catalog read surface -- the API-key-authenticated
   `routes/catalog.py` (`GET /catalog/symbols/{ref}`), which SS14 explicitly
   requires stay public-only. Confirmed by grep already (§1.1 of the plan)
   to have zero `session_mode`/`active_organization_id` references at all;
   this file proves it empirically instead of trusting the grep.
2. A single consolidated matrix -- one organization-private symbol, hit
   across every relevant reader from three sessions (owning organization,
   a different organization, personal mode) -- exactly mirroring WP7.5's
   "one symbol, every reader, before/after" structure, rather than the
   scattered per-endpoint proofs WP8.1-8.3 each did with their own symbols.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
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

from symgov_backend.catalog_api_auth import hash_api_key  # noqa: E402
from symgov_backend.models import CatalogApiKey  # noqa: E402

NEW_MIGRATION_HEAD = "20260902_0037"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp84_database():
    with _database("symgov-wp84") as (engine, url, raw_url):
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
                "GRANT SELECT, UPDATE ON catalog_api_keys TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _make_catalog_api_key(engine, *, token="wp84-catalog-key"):
    with engine.begin() as connection:
        from sqlalchemy import text as sa_text
        key_id = uuid.uuid4()
        connection.execute(
            sa_text(
                "INSERT INTO catalog_api_keys "
                "(id, customer_name, integration_name, key_prefix, key_hash, scopes_json, status, "
                " allowed_origins_json, created_at, updated_at) "
                "VALUES (:id, 'WP8.4 Customer', 'WP8.4 Integration', 'wp84-', :key_hash, "
                " '[\"catalog.read\"]'::jsonb, 'active', '[]'::jsonb, now(), now())"
            ),
            {"id": key_id, "key_hash": hash_api_key(token)},
        )
    return token


def test_comments_endpoint_excludes_organization_private_symbols(wp84_database):
    engine, _, _ = wp84_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp84acme1@example.test", display_name="AcmeAdmin1", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp84acme1@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.4 Comments Hydrant")

    comments = acme_client.get(f"/api/v1/published/symbols/{symbol_id}/comments")
    assert comments.status_code == 404, comments.text


def test_bulk_command_endpoint_excludes_organization_private_symbols(wp84_database):
    engine, _, _ = wp84_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp84acme2@example.test", display_name="AcmeAdmin2", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp84acme2@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.4 Command Hydrant")

    command = acme_client.post(
        "/api/v1/published/symbols/commands",
        json={
            "command": "comment",
            "symbolIds": [symbol_id],
            "comment": "should never reach an organization-private symbol",
            "requestId": str(uuid.uuid4()),
        },
    )
    assert command.status_code == 404, command.text


def test_catalog_api_key_surface_never_reaches_organization_private_symbols(wp84_database):
    """SS14's explicit requirement ("Keep Catalog API-key routes public-only")
    proved end-to-end, not just via the grep already recorded in plan SS1.1:
    a real API key, hitting the real route, against a real organization-wide
    private symbol's raw UUID."""
    engine, _, _ = wp84_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp84acme3@example.test", display_name="AcmeAdmin3", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp84acme3@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.4 API Key Hydrant")

    token = _make_catalog_api_key(engine)
    api_client, _ = _client(engine)
    headers = {"Authorization": f"Bearer {token}"}

    detail = api_client.get(f"/api/v1/catalog/symbols/{symbol_id}", headers=headers)
    assert detail.status_code == 404, detail.text

    search = api_client.get("/api/v1/catalog/symbols", params={"q": "WP8.4 API Key Hydrant"}, headers=headers)
    assert search.status_code == 200, search.text
    assert not any(
        item.get("id") == symbol_id or item.get("symbolId") == symbol_id
        for item in search.json().get("items", [])
    )


def test_full_reader_matrix_cross_organization_isolation(wp84_database):
    """One organization-private symbol, hit across every relevant reader
    from three sessions -- the WP7.5-style consolidated matrix. Proves the
    SS14 acceptance bar verbatim: "the same URL under different authorized
    sessions never leaks another organization's private result."""
    engine, _, _ = wp84_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    acme_client, _ = _client(engine)
    other_client, _ = _client(engine)
    personal_client, _ = _client(engine)

    acme_admin_id = _create_user_with_global_roles(Session, email="wp84acmematrix@example.test", display_name="AcmeMatrix", roles=[])
    _add_membership(Session, acme_admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(acme_client, "wp84acmematrix@example.test")
    symbol_id = _make_organization_wide_symbol(acme_client, name="WP8.4 Matrix Hydrant")
    add_favourite = acme_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add_favourite.status_code == 200, add_favourite.text

    other_admin_id = _create_user_with_global_roles(Session, email="wp84othermatrix@example.test", display_name="OtherMatrix", roles=[])
    _add_membership(Session, other_admin_id, code="other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(other_client, "wp84othermatrix@example.test")

    _create_user_with_global_roles(Session, email="wp84personalmatrix@example.test", display_name="PersonalMatrix", roles=[])
    personal_login = _login(personal_client, "wp84personalmatrix@example.test")
    assert personal_login["user"]["session"]["mode"] == "personal"

    # --- Owning organization: visible everywhere it should be. ---
    listing = acme_client.get("/api/v1/published/symbols", params={"q": "WP8.4 Matrix Hydrant"})
    assert any(item["symbolId"] == symbol_id for item in listing.json()["items"])
    detail = acme_client.get(f"/api/v1/published/symbols/{symbol_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["isFavourite"] is True

    # --- Every other session: excluded from every reader, every route shape. ---
    for label, client in (("different organization", other_client), ("personal mode", personal_client)):
        listing = client.get("/api/v1/published/symbols", params={"q": "WP8.4 Matrix Hydrant"})
        assert listing.status_code == 200
        assert not any(item["symbolId"] == symbol_id for item in listing.json()["items"]), label

        detail = client.get(f"/api/v1/published/symbols/{symbol_id}")
        assert detail.status_code == 404, f"{label}: {detail.text}"

        preview = client.get(f"/api/v1/published/symbols/{symbol_id}/preview")
        assert preview.status_code == 404, f"{label}: {preview.text}"

        comments = client.get(f"/api/v1/published/symbols/{symbol_id}/comments")
        assert comments.status_code == 404, f"{label}: {comments.text}"

        command = client.post(
            "/api/v1/published/symbols/commands",
            json={
                "command": "comment",
                "symbolIds": [symbol_id],
                "comment": f"must not reach it from {label}",
                "requestId": str(uuid.uuid4()),
            },
        )
        assert command.status_code == 404, f"{label}: {command.text}"

        add_attempt = client.put(f"/api/v1/published/favourites/{symbol_id}")
        assert add_attempt.status_code == 404, f"{label}: {add_attempt.text}"

    # --- Packs/pages listings never surface it (it has neither). ---
    packs = acme_client.get("/api/v1/published/packs")
    assert packs.status_code == 200
    assert not any(item.get("id") == symbol_id for item in packs.json().get("items", []))
