"""Stage 7 WP7.1 public-projection migration regressions.

Proves the `20260902_0035` migration (programme plan §13's public-projection
migration): `symbol_revisions.lifecycle_state` gains `withdrawn`;
`published_pages`/`pack_entries` gain checked `publication_state = active |
retired` plus nullable retirement actor/time/reason, backfilled/defaulted to
`active` so the pre-Stage-7 publication writer (`execute_publication_handoff`/
`runtime.py`, which does not set this column) remains compatible; and
`active_public_symbol_projections` additionally requires both the page and
the pack entry to still be `active`.

Follows the disposable-PostgreSQL harness and fixture-construction patterns
established in `test_organization_symbol_postgresql.py` and
`test_wp52a_public_reader_visibility_floor.py` — cross-table/lock/trigger
behavior like this cannot be trusted to SQLite (per the Stage 5/6/7 plans'
own regression standard).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _alembic,
    _approve,
    _database,
    _organization,
    _revision,
    _submission,
    _user,
)

THIS_MIGRATION = "20260902_0035"
PRE_STAGE7_RELEASE = "20260901_0034"
CURRENT_GLOBAL_HEAD = "20260904_0038"  # bump alongside every later migration; see the note below.

psycopg = pytest.importorskip("psycopg")


def test_this_migration_is_present_and_correctly_chained():
    """Proves `THIS_MIGRATION` (the migration this file's fixtures actually
    exercise) exists and chains from the pre-Stage-7 release -- independent
    of whatever the *current* global head is, so this assertion does not
    need updating every time a later migration lands on top of it (unlike
    `test_new_migration_is_the_sole_alembic_head` below, which does)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision(THIS_MIGRATION)
    assert revision is not None
    assert revision.down_revision == PRE_STAGE7_RELEASE


def test_new_migration_is_the_sole_alembic_head():
    """Update `CURRENT_GLOBAL_HEAD` (the one-line stale-head correction
    this repository's migration tests each carry) whenever a later
    migration is added on top of this one."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("script_location", "backend/alembic")
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [CURRENT_GLOBAL_HEAD]


@pytest.fixture(scope="module")
def wp71_database():
    with _database("symgov-wp71") as (engine, url, raw_url):
        _alembic(url, "upgrade", CURRENT_GLOBAL_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute(
                "GRANT SELECT ON governed_symbols, symbol_revisions, "
                "published_pages, pack_entries TO symgov_app"
            )
        yield engine, url, raw_url


def _publish_symbol(connection, actor, organization, *, page_state="active", entry_state="active"):
    """Build a fully valid public/published symbol (catalog identifier,
    published pack/page/entry) mirroring
    `test_wp52a_public_reader_visibility_floor._publish_symbol`'s
    construction, with the new `publication_state` columns left at their
    default ('active') unless overridden -- proving the pre-Stage-7 writer
    shape (no explicit `publication_state` in the INSERT) still works.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbol = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO governed_symbols "
            "(id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
            "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now,:organization,'public')"
        ),
        {"id": symbol, "slug": f"wp71-{symbol}", "owner": actor, "now": now, "organization": organization},
    )
    revision = _revision(connection, symbol, actor, lifecycle="published")
    submission = _submission(connection, organization, symbol, revision, actor)
    _approve(connection, submission, organization, symbol, revision, actor)

    catalog_id = f"WP71-{uuid.uuid4().hex[:16].upper()}"
    connection.execute(
        text(
            "INSERT INTO catalog_symbol_identifiers "
            "(identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
        ),
        {"catalog": catalog_id, "symbol": symbol},
    )
    connection.execute(
        text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"),
        {"catalog": catalog_id, "symbol": symbol},
    )

    pack, page, entry = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO publication_packs "
            "(id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'WP7.1','public',CURRENT_DATE,'published',now(),now())"
        ),
        {"id": pack, "code": f"WP71-{uuid.uuid4().hex}"},
    )
    page_columns = "id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at"
    page_values = ":id,:code,'WP7.1',:pack,:revision,CURRENT_DATE,now(),now()"
    page_params = {"id": page, "code": f"WP71-PAGE-{uuid.uuid4().hex}", "pack": pack, "revision": revision}
    if page_state != "active":
        page_columns += ",publication_state,retired_at,retired_by,retirement_reason"
        page_values += ",:state,now(),:actor,'test retirement'"
        page_params.update({"state": page_state, "actor": actor})
    connection.execute(text(f"INSERT INTO published_pages ({page_columns}) VALUES ({page_values})"), page_params)

    entry_columns = "id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at"
    entry_values = ":id,:pack,:revision,:page,1,now()"
    entry_params = {"id": entry, "pack": pack, "revision": revision, "page": page}
    if entry_state != "active":
        entry_columns += ",publication_state,retired_at,retired_by,retirement_reason"
        entry_values += ",:state,now(),:actor,'test retirement'"
        entry_params.update({"state": entry_state, "actor": actor})
    connection.execute(text(f"INSERT INTO pack_entries ({entry_columns}) VALUES ({entry_values})"), entry_params)

    return symbol, revision, page, entry, pack


def test_symbol_revisions_lifecycle_state_accepts_withdrawn(wp71_database):
    engine, _, _ = wp71_database
    with engine.begin() as connection:
        actor = _user(connection, f"wp71-lifecycle-{uuid.uuid4()}")
        symbol = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(
            text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
                "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now)"
            ),
            {"id": symbol, "slug": f"wp71-lifecycle-{symbol}", "owner": actor, "now": now},
        )
        revision = _revision(connection, symbol, actor, lifecycle="published")
        connection.execute(
            text("UPDATE symbol_revisions SET lifecycle_state='withdrawn' WHERE id=:id"),
            {"id": revision},
        )
        state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id=:id"), {"id": revision}
        ).scalar_one()
        assert state == "withdrawn"

    with pytest.raises(DBAPIError, match="lifecycle_state"):
        with engine.begin() as connection:
            actor = _user(connection, f"wp71-lifecycle-bad-{uuid.uuid4()}")
            symbol = uuid.uuid4()
            now = datetime.now(timezone.utc).replace(microsecond=0)
            connection.execute(
                text(
                    "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
                    "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now)"
                ),
                {"id": symbol, "slug": f"wp71-lifecycle-bad-{symbol}", "owner": actor, "now": now},
            )
            _revision(connection, symbol, actor, lifecycle="not-a-real-state")


def test_pre_stage7_writer_still_inserts_active_rows_via_default(wp71_database):
    """An INSERT shaped exactly like the existing `runtime.py` publication
    writer (no `publication_state` column at all) must still succeed and
    default to 'active' -- proving rolling-deploy compatibility."""
    engine, _, _ = wp71_database
    with engine.begin() as connection:
        actor = _user(connection, f"wp71-compat-{uuid.uuid4()}")
        organization = _organization(connection, "wp71-compat")
        symbol, revision, page, entry, pack = _publish_symbol(connection, actor, organization)
        page_state, entry_state = connection.execute(
            text(
                "SELECT pp.publication_state, pe.publication_state "
                "FROM published_pages pp, pack_entries pe "
                "WHERE pp.id=:page AND pe.id=:entry"
            ),
            {"page": page, "entry": entry},
        ).one()
        assert page_state == "active"
        assert entry_state == "active"


def test_new_writer_can_set_explicit_retired_state(wp71_database):
    engine, _, _ = wp71_database
    with engine.begin() as connection:
        actor = _user(connection, f"wp71-retire-{uuid.uuid4()}")
        organization = _organization(connection, "wp71-retire")
        symbol, revision, page, entry, pack = _publish_symbol(
            connection, actor, organization, page_state="retired", entry_state="retired"
        )
        page_row = connection.execute(
            text("SELECT publication_state, retired_by, retired_at, retirement_reason FROM published_pages WHERE id=:id"),
            {"id": page},
        ).one()
        assert page_row.publication_state == "retired"
        assert page_row.retired_by == actor
        assert page_row.retired_at is not None
        assert page_row.retirement_reason == "test retirement"


def test_retirement_metadata_constraint_rejects_retired_without_retired_at(wp71_database):
    engine, _, _ = wp71_database
    with pytest.raises(DBAPIError, match="retirement_metadata"):
        with engine.begin() as connection:
            actor = _user(connection, f"wp71-bad-retire-{uuid.uuid4()}")
            organization = _organization(connection, "wp71-bad-retire")
            symbol = uuid.uuid4()
            now = datetime.now(timezone.utc).replace(microsecond=0)
            connection.execute(
                text(
                    "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
                    "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now,:organization,'public')"
                ),
                {"id": symbol, "slug": f"wp71-bad-{symbol}", "owner": actor, "now": now, "organization": organization},
            )
            revision = _revision(connection, symbol, actor, lifecycle="published")
            catalog_id = f"WP71-BAD-{uuid.uuid4().hex[:16].upper()}"
            connection.execute(
                text(
                    "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
                    "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
                ),
                {"catalog": catalog_id, "symbol": symbol},
            )
            connection.execute(
                text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"),
                {"catalog": catalog_id, "symbol": symbol},
            )
            pack, page = uuid.uuid4(), uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
                    "VALUES (:id,:code,'WP7.1','public',CURRENT_DATE,'published',now(),now())"
                ),
                {"id": pack, "code": f"WP71-BAD-{uuid.uuid4().hex}"},
            )
            # publication_state='retired' with no retired_at must be rejected.
            connection.execute(
                text(
                    "INSERT INTO published_pages "
                    "(id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at,publication_state) "
                    "VALUES (:id,:code,'WP7.1',:pack,:revision,CURRENT_DATE,now(),now(),'retired')"
                ),
                {"id": page, "code": f"WP71-BAD-PAGE-{uuid.uuid4().hex}", "pack": pack, "revision": revision},
            )


def test_active_public_symbol_projections_excludes_retired_page(wp71_database):
    engine, _, _ = wp71_database
    with engine.begin() as connection:
        actor = _user(connection, f"wp71-view-page-{uuid.uuid4()}")
        organization = _organization(connection, "wp71-view-page")
        symbol, revision, page, entry, pack = _publish_symbol(connection, actor, organization)
        visible = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:symbol"),
            {"symbol": symbol},
        ).first()
        assert visible is not None

        connection.execute(
            text(
                "UPDATE published_pages SET publication_state='retired', retired_at=now(), retired_by=:actor "
                "WHERE id=:page"
            ),
            {"actor": actor, "page": page},
        )
        excluded = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:symbol"),
            {"symbol": symbol},
        ).first()
        assert excluded is None


def test_active_public_symbol_projections_excludes_retired_pack_entry(wp71_database):
    engine, _, _ = wp71_database
    with engine.begin() as connection:
        actor = _user(connection, f"wp71-view-entry-{uuid.uuid4()}")
        organization = _organization(connection, "wp71-view-entry")
        symbol, revision, page, entry, pack = _publish_symbol(connection, actor, organization)
        visible = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:symbol"),
            {"symbol": symbol},
        ).first()
        assert visible is not None

        connection.execute(
            text(
                "UPDATE pack_entries SET publication_state='retired', retired_at=now(), retired_by=:actor "
                "WHERE id=:entry"
            ),
            {"actor": actor, "entry": entry},
        )
        excluded = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:symbol"),
            {"symbol": symbol},
        ).first()
        assert excluded is None


def test_downgrade_to_pre_stage7_release_restores_old_schema_and_view():
    """Flags-off rollback to the exact pre-Stage-7 release must work against
    this additive schema, per the Stage 7 plan's §5 regression standard --
    exercised on its own disposable instance rather than the shared
    module-scoped `wp71_database` fixture, since it mutates schema state
    other tests in this module depend on."""
    with _database("symgov-wp71-downgrade") as (engine, url, raw_url):
        _alembic(url, "upgrade", THIS_MIGRATION)
        assert "publication_state" in {c["name"] for c in inspect(engine).get_columns("published_pages")}

        _alembic(url, "downgrade", PRE_STAGE7_RELEASE)
        columns_after_downgrade = {c["name"] for c in inspect(engine).get_columns("published_pages")}
        assert "publication_state" not in columns_after_downgrade
        assert "retired_by" not in columns_after_downgrade

        with engine.connect() as connection:
            constraint_names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'symbol_revisions'::regclass AND contype = 'c'"
                    )
                )
            }
            assert constraint_names == {"ck_symbol_revisions_ck_symbol_revisions_lifecycle_state"}

        _alembic(url, "upgrade", THIS_MIGRATION)
        columns_after_reupgrade = {c["name"] for c in inspect(engine).get_columns("published_pages")}
        assert "publication_state" in columns_after_reupgrade
