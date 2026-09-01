"""WP5.2b regression: the background agent readers (Hannah curation,
Whitney market intelligence) must not surface an organization_private
symbol, even though before this fix each restated its own independent
copy of the legacy publication predicate with no visibility check.

Unlike the HTTP routes covered by WP5.2a, these are not driven through
FastAPI's dependency-injected session; each script builds its own
RuntimePersistenceBridge from `SYMGOV_DATABASE_URL`/`SYMGOV_MIGRATION_DATABASE_URL`.
Point those at the disposable Postgres container and call the actual
production functions directly, rather than re-typing the SQL, so this
regression fails if the scripts drift again.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _organization,
    _user,
    stage5_database,
)
from test_wp52a_public_reader_visibility_floor import _publish_symbol  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_hannah_curation  # noqa: E402
import run_whitney_market_intelligence  # noqa: E402


@pytest.fixture()
def wp52b_fixtures(stage5_database, monkeypatch):
    engine, url, raw_url = stage5_database
    # RuntimePersistenceBridge builds its own engine from these env vars
    # (see backend/symgov_backend/db.py:get_database_url); point them at
    # this test's disposable container so the scripts' actual production
    # code paths run against it.
    monkeypatch.setenv("SYMGOV_DATABASE_URL", url)
    monkeypatch.setenv("SYMGOV_MIGRATION_DATABASE_URL", url)

    # stage5_database is module-scoped and its data persists across tests
    # in this run, so give each fixture invocation its own
    # category/discipline segment to keep Whitney's aggregate counts
    # test-isolated.
    segment = uuid.uuid4().hex[:8]
    with engine.begin() as connection:
        actor = _user(connection, "wp52b")
        organization = _organization(connection, "wp52b")
        public_symbol, public_page_code = _publish_symbol(
            connection, actor, organization, visibility="public",
            category=segment, discipline=segment,
        )
        private_symbol, private_page_code = _publish_symbol(
            connection, actor, organization, visibility="organization_private",
            category=segment, discipline=segment,
        )

    return {
        "actor": actor,
        "public_symbol": public_symbol,
        "public_page_code": public_page_code,
        "private_symbol": private_symbol,
        "private_page_code": private_page_code,
        "engine": engine,
        "segment": segment,
    }


def test_hannah_load_eligible_symbols_excludes_organization_private(wp52b_fixtures):
    rows = run_hannah_curation.load_eligible_symbols(db_env_file=None, limit=200)
    returned_ids = {row["symbol_id"] for row in rows}
    assert str(wp52b_fixtures["public_symbol"]) in returned_ids
    assert str(wp52b_fixtures["private_symbol"]) not in returned_ids


def test_whitney_published_segments_excludes_organization_private_count(wp52b_fixtures):
    """published_segments is a discipline/category aggregate with no
    per-symbol identity, so the regression proves the private symbol's
    single published entry is not counted at all: with both a public and
    an organization_private symbol sharing this fixture's unique
    category/discipline segment, published_count for that segment must be
    1, not 2."""
    segment_key = wp52b_fixtures["segment"]
    inputs = run_whitney_market_intelligence.load_market_inputs(db_env_file=None, trace=[])
    segment = next(
        (
            row
            for row in inputs["published_segments"]
            if row["discipline"] == segment_key and row["category"] == segment_key
        ),
        None,
    )
    assert segment is not None, inputs["published_segments"]
    assert segment["published_count"] == 1


def test_whitney_clarification_rows_excludes_organization_private(wp52b_fixtures):
    engine = wp52b_fixtures["engine"]
    actor = wp52b_fixtures["actor"]
    with engine.begin() as connection:
        for label, symbol_id, page_code in (
            ("public", wp52b_fixtures["public_symbol"], wp52b_fixtures["public_page_code"]),
            ("private", wp52b_fixtures["private_symbol"], wp52b_fixtures["private_page_code"]),
        ):
            page_id = connection.execute(
                text("SELECT id FROM published_pages WHERE page_code = :code"),
                {"code": page_code},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO clarification_records "
                    "(id,symbol_id,published_page_id,source,kind,status,submitted_by,"
                    "context_json,detail,created_at,updated_at) "
                    "VALUES (:id,:symbol,:page,'workspace','question','open',:actor,"
                    "'{}'::jsonb,:detail,now(),now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "symbol": symbol_id,
                    "page": page_id,
                    "actor": actor,
                    "detail": f"wp52b-{label}-clarification",
                },
            )

    inputs = run_whitney_market_intelligence.load_market_inputs(db_env_file=None, trace=[])
    returned_ids = {row["symbol_id"] for row in inputs["clarifications"]}
    assert str(wp52b_fixtures["public_symbol"]) in returned_ids
    assert str(wp52b_fixtures["private_symbol"]) not in returned_ids
