"""WP5.2a regression: the shared public-reader predicates must exclude
`organization_private` rows, even when every legacy (pre-Stage-5) condition
they used to check is otherwise satisfied.

Every HTTP route call site (`catalog_search.py`, `routes/published.py`,
`routes/catalog.py`) imports `PUBLISHED_SYMBOLS_SQL` from
`published_catalog.py` and only ever *appends* additional `AND ...` clauses
to it; none of them restate the base predicate. `symbol_set_service.py`
similarly only calls `current_public_symbols`, backed by
`PUBLIC_SYMBOL_ELIGIBILITY_SQL` in `public_symbol_eligibility.py`. Proving
both shared constants exclude an `organization_private` row therefore
proves every one of their call sites does too, since none of them can
loosen (only narrow) the base query.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _approve,
    _organization,
    _revision,
    _submission,
    _user,
    stage5_database,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.public_symbol_eligibility import (  # noqa: E402
    PUBLIC_SYMBOL_ELIGIBILITY_SQL,
    current_public_symbols,
)
from symgov_backend.published_catalog import PUBLISHED_SYMBOLS_SQL  # noqa: E402


def _publish_symbol(
    connection, actor, organization, *, visibility: str, category: str = "test", discipline: str = "test"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Build a symbol that satisfies every legacy publication predicate
    (published pack/page/entry, published revision) with the given
    `visibility`, mirroring
    test_active_public_projection_enforces_every_publication_predicate's
    fixture construction in test_organization_symbol_postgresql.py.

    `category`/`discipline` default to 'test' but should be given a
    per-test-unique value by callers that aggregate on them (e.g.
    Whitney's discipline/category segment counts), since `stage5_database`
    is module-scoped and its data persists across tests in the same run.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbol = uuid.uuid4()
    columns = "id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility"
    placeholders = ":id,:slug,:slug,:category,:discipline,:owner,:now,:now,:organization,:visibility"
    connection.execute(
        text(f"INSERT INTO governed_symbols ({columns}) VALUES ({placeholders})"),
        {
            "id": symbol,
            "slug": f"wp52a-{symbol}",
            "category": category,
            "discipline": discipline,
            "owner": actor,
            "now": now,
            "organization": organization,
            "visibility": visibility,
        },
    )
    revision = _revision(connection, symbol, actor, lifecycle="published")

    if visibility == "public":
        submission = _submission(connection, organization, symbol, revision, actor)
        _approve(connection, submission, organization, symbol, revision, actor)

    catalog_id = f"WP52A-{uuid.uuid4().hex[:16].upper()}"
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
    page_code = f"WP52A-PAGE-{uuid.uuid4().hex}"
    connection.execute(
        text(
            "INSERT INTO publication_packs "
            "(id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'WP5.2a','public',CURRENT_DATE,'published',now(),now())"
        ),
        {"id": pack, "code": f"WP52A-{uuid.uuid4().hex}"},
    )
    connection.execute(
        text(
            "INSERT INTO published_pages "
            "(id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,'WP5.2a',:pack,:revision,CURRENT_DATE,now(),now())"
        ),
        {"id": page, "code": page_code, "pack": pack, "revision": revision},
    )
    connection.execute(
        text(
            "INSERT INTO pack_entries "
            "(id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:revision,:page,1,now())"
        ),
        {"id": entry, "pack": pack, "revision": revision, "page": page},
    )
    return symbol, page_code


@pytest.fixture()
def wp52a_fixtures(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "wp52a")
        organization = _organization(connection, "wp52a")
        public_symbol, public_page = _publish_symbol(
            connection, actor, organization, visibility="public"
        )
        private_symbol, private_page = _publish_symbol(
            connection, actor, organization, visibility="organization_private"
        )
    return engine, public_symbol, public_page, private_symbol, private_page


def test_current_public_symbols_excludes_organization_private(wp52a_fixtures):
    """symbol_set_service.py's only visibility-eligibility check must not
    surface an organization_private symbol even though it satisfies every
    other legacy publication condition."""
    engine, public_symbol, _, private_symbol, _ = wp52a_fixtures
    with Session(engine) as session:
        result = current_public_symbols(session, [public_symbol, private_symbol])
    assert public_symbol in result
    assert private_symbol not in result


def test_public_symbol_eligibility_sql_directly_excludes_organization_private(wp52a_fixtures):
    engine, public_symbol, _, private_symbol, _ = wp52a_fixtures
    with engine.connect() as connection:
        rows = connection.execute(
            PUBLIC_SYMBOL_ELIGIBILITY_SQL,
            {"symbol_ids": [public_symbol, private_symbol]},
        ).all()
    returned_ids = {row[0] for row in rows}
    assert public_symbol in returned_ids
    assert private_symbol not in returned_ids


def test_published_symbols_sql_base_query_excludes_organization_private(wp52a_fixtures):
    """The shared PUBLISHED_SYMBOLS_SQL constant every route imports and
    appends onto (catalog_search.py, routes/published.py, routes/catalog.py)
    must not return an organization_private row."""
    engine, public_symbol, _, private_symbol, _ = wp52a_fixtures
    with engine.connect() as connection:
        rows = connection.execute(
            text(PUBLISHED_SYMBOLS_SQL + " AND gs.id = ANY(:symbol_ids)"),
            {"symbol_ids": [public_symbol, private_symbol]},
        ).all()
    returned_ids = {row.symbol_id for row in rows}
    assert str(public_symbol) in returned_ids
    assert str(private_symbol) not in returned_ids


def test_published_symbols_sql_detail_lookup_append_pattern_excludes_private(wp52a_fixtures):
    """Mirrors routes/published.py's/routes/catalog.py's single-symbol
    detail lookup, which appends "AND gs.id = :symbol_id ... LIMIT 1" onto
    the shared base query."""
    engine, public_symbol, _, private_symbol, _ = wp52a_fixtures
    with engine.connect() as connection:
        for symbol_id, should_be_visible in ((public_symbol, True), (private_symbol, False)):
            rows = connection.execute(
                text(
                    PUBLISHED_SYMBOLS_SQL
                    + """
                    AND gs.id = :symbol_id
                    ORDER BY pp.effective_date DESC, pk.effective_date DESC
                    LIMIT 1
                    """
                ),
                {"symbol_id": symbol_id},
            ).all()
            assert bool(rows) == should_be_visible, (symbol_id, should_be_visible, rows)


def test_published_symbols_sql_page_code_lookup_append_pattern_excludes_private(wp52a_fixtures):
    """Mirrors routes/published.py's page-code lookup at
    `text(PUBLISHED_SYMBOLS_SQL + " AND pp.page_code = :page_code LIMIT 1")`."""
    engine, _, public_page, _, private_page = wp52a_fixtures
    with engine.connect() as connection:
        public_rows = connection.execute(
            text(PUBLISHED_SYMBOLS_SQL + " AND pp.page_code = :page_code LIMIT 1"),
            {"page_code": public_page},
        ).all()
        private_rows = connection.execute(
            text(PUBLISHED_SYMBOLS_SQL + " AND pp.page_code = :page_code LIMIT 1"),
            {"page_code": private_page},
        ).all()
    assert public_rows
    assert not private_rows


def test_published_symbols_sql_expanding_bindparam_append_pattern_excludes_private(wp52a_fixtures):
    """Mirrors routes/published.py's IN-list lookup at
    `PUBLISHED_SYMBOLS_SQL + "AND gs.id::text IN :symbol_ids ..."` with
    `.bindparams(bindparam("symbol_ids", expanding=True))`."""
    engine, public_symbol, _, private_symbol, _ = wp52a_fixtures
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                PUBLISHED_SYMBOLS_SQL
                + """
                AND gs.id::text IN :symbol_ids
                ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name
                """
            ).bindparams(bindparam("symbol_ids", expanding=True)),
            {"symbol_ids": [str(public_symbol), str(private_symbol)]},
        ).all()
    returned_ids = {row.symbol_id for row in rows}
    assert str(public_symbol) in returned_ids
    assert str(private_symbol) not in returned_ids
