"""Regression cover for the catalog identifier backfill (20260823_0030a),
against a real disposable PostgreSQL container.

Why this file exists: `20260802_0026` introduces
`catalog_symbol_identifiers` empty and adds a nullable
`governed_symbols.catalog_symbol_id`, populating neither, while
`20260826_0031` enforces that every *published* symbol already carries a
matching canonical identifier. Nothing bridged the two, so the release
could not migrate any database holding published symbols -- which is
exactly what production held (95 governed symbols, 84 published
revisions, 84 published pages, 84 pack entries). The 2026-09-07
deployment attempt hit this at step 5; see
`docs/plans/2026-09-07-stage11-catalog-backfill-and-resume-plan.md`.

Why 2356 passing tests missed it: the WP11.7 rehearsal
(`test_stage11_wp11_7_migration_rehearsal_postgresql.py`) seeds
`governed_symbols` rows via `_insert_legacy_public_symbol` but never a
`symbol_revisions` row, so nothing in it is ever published and
`20260826_0031`'s preflight passes trivially. **The publication seeding
below is the whole point of this file** -- without it, this test proves
nothing that the rehearsal did not already prove.

Redaction: this file never prints the disposable container's connection
string, and every seeded identity uses synthetic `@example.test` emails.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

# The revision production actually sat at when this defect was found: the
# registry does not exist yet, so the backfill must also tolerate
# `20260802_0026` creating it against already-populated tables.
PRODUCTION_REVISION = "20260801_0026"
BACKFILL_REVISION = "20260823_0030a"
CURRENT_HEAD = "20260905_0044"

PUBLISHED_SYMBOL_COUNT = 3


def _seed_published_catalog(engine) -> dict[str, object]:
    """Seed governed symbols that are genuinely published: a
    `symbol_revisions` row with `lifecycle_state='published'`, plus the
    `published_pages` and `pack_entries` rows that `20260826_0031` also
    treats as publication evidence."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    author_id = uuid.uuid4()
    pack_id = uuid.uuid4()
    symbol_ids: list[uuid.UUID] = []

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": author_id, "email": "backfill-author@example.test", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO publication_packs (id,pack_code,title,audience,"
                "effective_date,status,created_at,updated_at) "
                "VALUES (:id,'PACK-BACKFILL','Backfill pack','public',"
                ":effective,'published',:now,:now)"
            ),
            {"id": pack_id, "effective": date(2026, 1, 1), "now": now},
        )

        for index in range(PUBLISHED_SYMBOL_COUNT):
            symbol_id = uuid.uuid4()
            revision_id = uuid.uuid4()
            page_id = uuid.uuid4()
            symbol_ids.append(symbol_id)

            connection.execute(
                text(
                    "INSERT INTO governed_symbols (id,slug,canonical_name,category,"
                    "discipline,owner_id,created_at,updated_at) "
                    "VALUES (:id,:slug,:slug,'fire','fire-safety',:owner,:now,:now)"
                ),
                {"id": symbol_id, "slug": f"backfill-symbol-{index}", "owner": author_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO symbol_revisions (id,symbol_id,revision_label,"
                    "lifecycle_state,payload_json,author_id,created_at) "
                    "VALUES (:id,:symbol,'r1','published','{}'::jsonb,:author,:now)"
                ),
                {"id": revision_id, "symbol": symbol_id, "author": author_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO published_pages (id,page_code,title,pack_id,"
                    "current_symbol_revision_id,effective_date,created_at,updated_at) "
                    "VALUES (:id,:code,:code,:pack,:revision,:effective,:now,:now)"
                ),
                {
                    "id": page_id,
                    "code": f"PAGE-BACKFILL-{index}",
                    "pack": pack_id,
                    "revision": revision_id,
                    "effective": date(2026, 1, 1),
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,"
                    "published_page_id,sort_order,created_at) "
                    "VALUES (:id,:pack,:revision,:page,:sort,:now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "pack": pack_id,
                    "revision": revision_id,
                    "page": page_id,
                    "sort": index,
                    "now": now,
                },
            )

    return {"symbol_ids": symbol_ids, "author_id": author_id}


@pytest.fixture(scope="module")
def backfill_database():
    with _database("symgov-catalog-backfill") as (engine, url, raw_url):
        yield engine, url, raw_url


def test_published_symbols_seeded_before_the_registry_migrate_through_to_head(backfill_database):
    """The exact production shape: published symbols already exist when the
    catalog identifier registry is introduced. Before 20260823_0030a this
    upgrade aborted at 20260826_0031."""
    engine, url, _ = backfill_database

    _alembic(url, "upgrade", PRODUCTION_REVISION)
    seeded = _seed_published_catalog(engine)

    with engine.connect() as connection:
        symbols_before = connection.execute(
            text("SELECT count(*) FROM governed_symbols")
        ).scalar_one()
        pages_before = connection.execute(
            text("SELECT count(*) FROM published_pages")
        ).scalar_one()
        entries_before = connection.execute(
            text("SELECT count(*) FROM pack_entries")
        ).scalar_one()
    assert symbols_before == PUBLISHED_SYMBOL_COUNT

    # The upgrade under test: one continuous motion across the backfill and
    # the invariant that depends on it.
    _alembic(url, "upgrade", CURRENT_HEAD)

    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == CURRENT_HEAD

        # No publication data was destroyed to satisfy the invariant.
        assert connection.execute(
            text("SELECT count(*) FROM governed_symbols")
        ).scalar_one() == symbols_before
        assert connection.execute(
            text("SELECT count(*) FROM published_pages")
        ).scalar_one() == pages_before
        assert connection.execute(
            text("SELECT count(*) FROM pack_entries")
        ).scalar_one() == entries_before

        # Every seeded symbol carries a canonical identifier attributed to
        # the backfill, in the S-%06d form the application allocates.
        rows = connection.execute(
            text(
                "SELECT gs.id, gs.catalog_symbol_id, csi.role, csi.allocation_source "
                "FROM governed_symbols gs "
                "JOIN catalog_symbol_identifiers csi ON csi.identifier = gs.catalog_symbol_id "
                "ORDER BY gs.slug"
            )
        ).all()
    assert len(rows) == PUBLISHED_SYMBOL_COUNT
    assert {row.id for row in rows} == set(seeded["symbol_ids"])
    for row in rows:
        assert row.role == "canonical"
        assert row.allocation_source == "legacy_backfill"
        assert row.catalog_symbol_id.startswith("S-")
        assert len(row.catalog_symbol_id) == 8
        assert row.catalog_symbol_id[2:].isdigit()

    # Identifiers are unique per symbol.
    assert len({row.catalog_symbol_id for row in rows}) == PUBLISHED_SYMBOL_COUNT


def test_the_0031_publication_invariant_finds_nothing_to_complain_about(backfill_database):
    """Run 20260826_0031's own preflight predicate against the migrated
    database, so this test fails for the original reason if the backfill
    ever stops covering published symbols."""
    engine, _, _ = backfill_database

    with engine.connect() as connection:
        offending = connection.execute(
            text(
                """
                SELECT count(*)
                FROM symbol_revisions sr
                JOIN governed_symbols gs ON gs.id = sr.symbol_id
                LEFT JOIN catalog_symbol_identifiers csi
                  ON csi.identifier = gs.catalog_symbol_id
                 AND csi.role = 'canonical'
                 AND csi.governed_symbol_id = gs.id
                WHERE (
                    sr.lifecycle_state = 'published'
                    OR EXISTS (SELECT 1 FROM published_pages pp
                                WHERE pp.current_symbol_revision_id = sr.id)
                    OR EXISTS (SELECT 1 FROM pack_entries pe
                                WHERE pe.symbol_revision_id = sr.id)
                )
                  AND (gs.catalog_symbol_id IS NULL OR csi.identifier IS NULL)
                """
            )
        ).scalar_one()
    assert offending == 0


def test_backfill_is_idempotent_and_allocates_nothing_on_a_second_pass(backfill_database):
    """The backfill selects only `catalog_symbol_id IS NULL`, so re-running
    its allocation block must not mint duplicate identifiers."""
    engine, _, _ = backfill_database

    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT count(*) FROM catalog_symbol_identifiers")
        ).scalar_one()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DO $$
                DECLARE
                    target RECORD;
                    allocated TEXT;
                BEGIN
                    FOR target IN
                        SELECT id FROM governed_symbols
                        WHERE catalog_symbol_id IS NULL
                        ORDER BY created_at, slug
                    LOOP
                        allocated := 'S-' || lpad(nextval('catalog_symbol_id_seq')::text, 6, '0');
                        INSERT INTO catalog_symbol_identifiers
                            (identifier, role, governed_symbol_id, allocation_source, allocated_at)
                        VALUES
                            (allocated, 'canonical', target.id, 'legacy_backfill', now());
                        UPDATE governed_symbols
                           SET catalog_symbol_id = allocated
                         WHERE id = target.id;
                    END LOOP;
                END;
                $$
                """
            )
        )

    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT count(*) FROM catalog_symbol_identifiers")
        ).scalar_one()
    assert after == before
