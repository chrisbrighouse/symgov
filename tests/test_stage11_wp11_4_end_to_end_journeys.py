"""Stage 11 WP11.4 — full end-to-end journey exercises (programme plan §17
"Integrated verification" item 3), against a real disposable PostgreSQL
container.

Per the Stage 11 implementation plan §1.2, this package re-exercises
existing functionality end-to-end; it does not rebuild it. See
`docs/plans/2026-09-06-stage11-wp11.4-end-to-end-journeys.md` for the full
per-journey audit citing the Stage 1-10 test(s) that already prove each of
the ten named journeys as one connected, real-HTTP-endpoint flow. This
file adds the one journey no prior stage's test chained together as a
single flow: a personal (zero-membership) session logging in, browsing
the public Catalog, and favouriting a symbol.

Reuses WP11.3's disposable-Postgres fixture infrastructure
(`stage11_database`, `_client`, `_login`) rather than building a fourth
parallel harness.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_stage11_wp11_3_adversarial_fixture import _client, _login, _new_user, stage11_database  # noqa: E402,F401


def _published_public_symbol(engine, canonical_name: str) -> str:
    """Builds a genuinely published, public-eligible governed symbol via
    raw SQL (mirrors `test_symbol_set_tenant_isolation.py`'s own helper of
    the same shape) and returns its human-readable catalog identifier, the
    same reference a real Catalog client would use."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbol_id, revision_id, pack_id, page_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    catalog_id = f"WP114-{uuid.uuid4().hex[:12].upper()}"
    with engine.begin() as connection:
        owner = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ), {"id": owner, "email": f"wp114owner-{owner}@example.test", "now": now})
        connection.execute(text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
            "VALUES (:id,:slug,:name,'fire','fire-safety',:owner,:now,:now)"
        ), {"id": symbol_id, "slug": canonical_name.lower().replace(" ", "-"), "name": canonical_name, "owner": owner, "now": now})
        connection.execute(text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'1','published','{}'::jsonb,:owner,:now)"
        ), {"id": revision_id, "symbol": symbol_id, "owner": owner, "now": now})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision_id, "symbol": symbol_id})
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
        ), {"catalog": catalog_id, "symbol": symbol_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"), {"catalog": catalog_id, "symbol": symbol_id})
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'WP11.4 Journey','public',CURRENT_DATE,'published',:now,:now)"
        ), {"id": pack_id, "code": f"WP114-{uuid.uuid4().hex}", "now": now})
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,'WP11.4 Journey',:pack,:revision,CURRENT_DATE,:now,:now)"
        ), {"id": page_id, "code": f"WP114-PAGE-{uuid.uuid4().hex}", "pack": pack_id, "revision": revision_id, "now": now})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:revision,:page,1,:now)"
        ), {"id": uuid.uuid4(), "pack": pack_id, "revision": revision_id, "page": page_id, "now": now})
    return catalog_id


def test_personal_session_can_browse_the_public_catalog_and_favourite_a_symbol(stage11_database):
    """The "personal login and public Catalog" journey: a zero-membership
    personal session logs in, lists the public Catalog, fetches a
    symbol's detail, favourites it by reference, and sees it in their own
    favourites list -- one continuous flow through the real routes."""
    engine, _, _ = stage11_database
    catalog_id = _published_public_symbol(engine, "WP11.4 Journey Fire Alarm")

    client, Session = _client(engine, pilot_codes=("symgov",))
    _, personal_email = _new_user(Session, "journeypersonal")
    login = _login(client, personal_email)
    assert login.json()["user"]["session"]["mode"] == "personal"
    assert login.json()["user"]["session"]["activeOrganizationId"] is None

    listing = client.get("/api/v1/published/symbols")
    assert listing.status_code == 200, listing.text
    assert any(item.get("catalogSymbolId") == catalog_id for item in listing.json()["items"])

    detail = client.get(f"/api/v1/published/symbols/{catalog_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["catalogSymbolId"] == catalog_id

    favourited = client.put(f"/api/v1/published/favourites/{catalog_id}")
    assert favourited.status_code == 200, favourited.text
    assert favourited.json()["isFavourite"] is True

    favourites = client.get("/api/v1/published/favourites")
    assert favourites.status_code == 200, favourites.text
    assert str(favourited.json()["symbolId"]) in {item["symbolId"] for item in favourites.json()["items"]}
