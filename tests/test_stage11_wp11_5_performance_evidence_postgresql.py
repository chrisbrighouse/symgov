"""Stage 11 WP11.5 — representative indexed query/load evidence, against a
real disposable PostgreSQL container.

Per the Stage 11 implementation plan §4 (decided 2026-09-06): assume a
roughly production-scale dataset (a few thousand symbols across a handful
of organizations), label the resulting numbers clearly as provisional/
illustrative -- never a capacity guarantee -- per CLAUDE.md's rule against
presenting illustrative values as real production metrics. This file's
wall-clock numbers depend entirely on the disposable container's host
hardware and are recorded for reference only; the assertions that gate
this test are structural (real index usage, no sequential scan on the
large tables), not the timing numbers themselves.

Every eligibility path WP11.1 introduced (`symbol_eligibility.
eligible_organization_private_symbols`, reused by `symbol_set_builder`,
`effective_palette`, and `symbol_set_service`) reuses the exact join shape
and indexes Stage 5/6 already built
(`ix_governed_symbols_owner_visibility_organization_wide`,
`ix_org_symbol_review_decisions_tenant_symbol_revision`,
`ix_org_symbol_review_submissions_tenant_symbol_revision`) -- it does not
introduce a new query shape, only new call sites for the same one. This
test proves that indexing still holds at a representative dataset size
now that more call sites depend on it.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_stage11_wp11_3_adversarial_fixture import _client, _login, _new_user, stage11_database  # noqa: E402,F401

DATASET_ORGANIZATION_PRIVATE_SYMBOLS = 1500
DATASET_PUBLIC_SYMBOLS = 1500


def _seed_dataset(engine, *, code):
    """Seeds a roughly production-scale dataset directly via SQL (an ORM
    insert loop of this size would dominate the timing being measured) --
    `DATASET_ORGANIZATION_PRIVATE_SYMBOLS` approved organization-private
    symbols in one organization plus `DATASET_PUBLIC_SYMBOLS` published
    public symbols, so both eligibility halves are populated at scale."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        organization_id = connection.execute(
            text("SELECT id FROM organizations WHERE normalized_code = :code"), {"code": code},
        ).scalar_one()
        owner_id = connection.execute(
            text("SELECT owner_id FROM governed_symbols LIMIT 1"),
        ).scalar_one_or_none()
        if owner_id is None:
            owner_id = uuid.uuid4()
            connection.execute(text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ), {"id": owner_id, "email": f"wp115-{owner_id}@example.test", "now": now})

        for index in range(DATASET_ORGANIZATION_PRIVATE_SYMBOLS):
            symbol_id, revision_id, submission_id, decision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,owner_organization_id,"
                "visibility,organization_wide,created_at,updated_at) "
                "VALUES (:id,:slug,:name,'fire','fire-safety',:owner,:org,'organization_private',false,:now,:now)"
            ), {"id": symbol_id, "slug": f"wp115-private-{index}-{symbol_id.hex[:8]}", "name": f"WP11.5 Private Symbol {index}", "owner": owner_id, "org": organization_id, "now": now})
            connection.execute(text(
                "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
                "VALUES (:id,:symbol,'1','approved','{}'::jsonb,:owner,:now)"
            ), {"id": revision_id, "symbol": symbol_id, "owner": owner_id, "now": now})
            connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision_id, "symbol": symbol_id})
            connection.execute(text(
                "INSERT INTO organization_symbol_review_submissions (id,organization_id,governed_symbol_id,symbol_revision_id,"
                "submitted_by_user_id,submitted_at,status,closed_at) "
                "VALUES (:id,:org,:symbol,:revision,:owner,:now,'closed',:now)"
            ), {"id": submission_id, "org": organization_id, "symbol": symbol_id, "revision": revision_id, "owner": owner_id, "now": now})
            connection.execute(text(
                "INSERT INTO organization_symbol_review_decisions (id,submission_id,organization_id,governed_symbol_id,"
                "symbol_revision_id,decided_by_user_id,decision,decided_at) "
                "VALUES (:id,:submission,:org,:symbol,:revision,:owner,'approved',:now)"
            ), {"id": decision_id, "submission": submission_id, "org": organization_id, "symbol": symbol_id, "revision": revision_id, "owner": owner_id, "now": now})

        pack_id, page_id = uuid.uuid4(), uuid.uuid4()
        connection.execute(text(
            "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
            "VALUES (:id,:code,'WP11.5 Load','public',CURRENT_DATE,'published',:now,:now)"
        ), {"id": pack_id, "code": f"WP115-{uuid.uuid4().hex}", "now": now})
        for index in range(DATASET_PUBLIC_SYMBOLS):
            symbol_id, revision_id, page_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            catalog_id = f"WP115-{uuid.uuid4().hex[:12].upper()}"
            connection.execute(text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) "
                "VALUES (:id,:slug,:name,'fire','fire-safety',:owner,:now,:now)"
            ), {"id": symbol_id, "slug": f"wp115-public-{index}-{symbol_id.hex[:8]}", "name": f"WP11.5 Public Symbol {index}", "owner": owner_id, "now": now})
            connection.execute(text(
                "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
                "VALUES (:id,:symbol,'1','published','{}'::jsonb,:owner,:now)"
            ), {"id": revision_id, "symbol": symbol_id, "owner": owner_id, "now": now})
            connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision_id, "symbol": symbol_id})
            connection.execute(text(
                "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
                "VALUES (:catalog,'canonical',:symbol,'global_sequence',now())"
            ), {"catalog": catalog_id, "symbol": symbol_id})
            connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:catalog WHERE id=:symbol"), {"catalog": catalog_id, "symbol": symbol_id})
            connection.execute(text(
                "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
                "VALUES (:id,:code,'WP11.5 Load',:pack,:revision,CURRENT_DATE,:now,:now)"
            ), {"id": page_id, "code": f"WP115-PAGE-{uuid.uuid4().hex}", "pack": pack_id, "revision": revision_id, "now": now})
            connection.execute(text(
                "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                "VALUES (:id,:pack,:revision,:page,1,:now)"
            ), {"id": uuid.uuid4(), "pack": pack_id, "revision": revision_id, "page": page_id, "now": now})


def _explain_last_statement_uses_index(engine, Session, organization_id) -> tuple[bool, str]:
    """Captures the *actual* SQL `eligible_organization_private_symbols`
    compiles and executes (via a `before_cursor_execute` event), rather
    than a hand-duplicated SQL string that could drift from the real
    query shape if the function is edited later without updating a
    duplicated string in lockstep (WP11.6 independent-review finding)."""
    from symgov_backend.symbol_eligibility import eligible_organization_private_symbols

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "governed_symbols" in statement and "organization_symbol_review_decisions" in statement:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with Session() as session:
            eligible_organization_private_symbols(session, organization_id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert captured, "eligible_organization_private_symbols did not execute the expected query shape."
    statement, parameters = captured[-1]
    # The captured statement/parameters are already in the DBAPI driver's
    # native paramstyle (psycopg's `%(name)s`), not SQLAlchemy's `:name`
    # textual style, so this must go through `exec_driver_sql` (bypasses
    # the text()/compiler layer) rather than `text()`.
    with engine.begin() as connection:
        plan_rows = connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).all()
    plan_text = "\n".join(row[0] for row in plan_rows)
    return ("Seq Scan on governed_symbols" not in plan_text and "Seq Scan on organization_symbol_review_decisions" not in plan_text), plan_text


def test_widened_eligibility_query_uses_indexes_and_is_representatively_fast(stage11_database):
    engine, _, _ = stage11_database
    client, Session = _client(engine, pilot_codes=("wp115org",))

    from test_wp74_symbol_demotion_postgresql import _add_membership
    admin_id, admin_email = _new_user(Session, "wp115admin")
    _add_membership(Session, admin_id, code="wp115org", base_role="admin")

    seed_start = time.monotonic()
    _seed_dataset(engine, code="wp115org")
    seed_seconds = time.monotonic() - seed_start

    with engine.begin() as connection:
        connection.execute(text("ANALYZE governed_symbols"))
        connection.execute(text("ANALYZE organization_symbol_review_decisions"))
        connection.execute(text("ANALYZE organization_symbol_review_submissions"))
        organization_id = connection.execute(
            text("SELECT id FROM organizations WHERE normalized_code = 'wp115org'"),
        ).scalar_one()

    uses_index, plan_text = _explain_last_statement_uses_index(engine, Session, organization_id)
    assert uses_index, f"Expected an index-backed plan, got:\n{plan_text}"

    _login(client, admin_email)

    timings = {}
    for label, request in (
        ("builder-search", lambda: client.get("/api/v1/org/me/symbol-sets/builder-search", params={"pageSize": 50})),
        ("published-symbols-list", lambda: client.get("/api/v1/published/symbols")),
    ):
        started = time.monotonic()
        response = request()
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        timings[label] = elapsed

    print(
        f"\nWP11.5 illustrative timing evidence (dataset: {DATASET_ORGANIZATION_PRIVATE_SYMBOLS} organization-private + "
        f"{DATASET_PUBLIC_SYMBOLS} public symbols; seed took {seed_seconds:.2f}s on this host):"
    )
    for label, elapsed in timings.items():
        print(f"  {label}: {elapsed * 1000:.1f} ms")

    # Loose, illustrative-only sanity bound -- not a capacity guarantee, and
    # deliberately generous so it never gates a real release on this
    # session's own disposable-container hardware.
    assert all(elapsed < 10.0 for elapsed in timings.values()), timings
