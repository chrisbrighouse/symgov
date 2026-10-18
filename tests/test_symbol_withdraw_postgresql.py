"""Operator withdrawal of public symbols (`symbol_withdraw`) against a real PostgreSQL.

PostgreSQL-only: the CLI refreshes the published-symbol views, a PostgreSQL
function, and the lifecycle and publication-state check constraints are the
thing under test.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_review_routes_postgresql import _user  # noqa: E402

from symgov_backend import symbol_withdraw as cli  # noqa: E402

psycopg = pytest.importorskip("psycopg")

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    with _database("symgov-withdraw") as (engine, url, _raw_url):
        _alembic(url, "upgrade", "head")
        mp = pytest.MonkeyPatch()
        mp.setenv("SYMGOV_DATABASE_URL", url)
        actor = _user(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
                      email="withdraw-operator@example.test", roles=())
        yield {"engine": engine, "actor": actor, **_seed(engine, actor)}
        mp.undo()


def _pack(connection, code):
    pack_id = uuid.uuid4()
    connection.execute(text(
        "INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) "
        "VALUES (:id,:code,:code,'public',DATE '2026-06-11','published',:now,:now)"
    ), {"id": pack_id, "code": code, "now": NOW})
    return pack_id


def _symbol(connection, actor, slug, catalog_id, state, pack_id=None):
    symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(text(
        "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at) "
        "VALUES (:id,:slug,:slug,'valve_symbol','Instrumentation & Controls',:owner,'public',:now,:now)"
    ), {"id": symbol_id, "slug": slug, "owner": actor, "now": NOW})
    connection.execute(text(
        "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
        "VALUES (:id,:symbol,'r1',:state,CAST(:payload AS jsonb),:owner,:now)"
    ), {"id": revision_id, "symbol": symbol_id, "state": state, "owner": actor, "now": NOW,
        "payload": json.dumps({"name": slug, "summary": "A test symbol."})})
    connection.execute(text("UPDATE governed_symbols SET current_revision_id=:r WHERE id=:s"), {"r": revision_id, "s": symbol_id})
    if catalog_id:
        connection.execute(text(
            "INSERT INTO catalog_symbol_identifiers (identifier,role,governed_symbol_id,allocation_source,allocated_at) "
            "VALUES (:c,'canonical',:s,'global_sequence',now())"
        ), {"c": catalog_id, "s": symbol_id})
        connection.execute(text("UPDATE governed_symbols SET catalog_symbol_id=:c WHERE id=:s"), {"c": catalog_id, "s": symbol_id})
    page_id = None
    if pack_id is not None:
        page_id, entry_id = uuid.uuid4(), uuid.uuid4()
        connection.execute(text(
            "INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
            "VALUES (:id,:code,:code,:pack,:r,DATE '2026-06-11',:now,:now)"
        ), {"id": page_id, "code": f"PAGE-{slug}", "pack": pack_id, "r": revision_id, "now": NOW})
        connection.execute(text(
            "INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
            "VALUES (:id,:pack,:r,:page,1,:now)"
        ), {"id": entry_id, "pack": pack_id, "r": revision_id, "page": page_id, "now": NOW})
    return {"symbol_id": symbol_id, "revision_id": revision_id, "page_id": page_id}


def _seed(engine, actor):
    with engine.begin() as connection:
        own_pack = _pack(connection, "qet-solenoid-source-pack")
        shared_pack = _pack(connection, "shared-pack")
        return {
            "own_pack": own_pack,
            "shared_pack": shared_pack,
            # Published alone in its own pack: the pack retires with it.
            "valve": _symbol(connection, actor, "qet-solenoid-valve", "TST-71", "published", own_pack),
            # Still in review, never published.
            "switch": _symbol(connection, actor, "qet-pressure-switch", None, "review"),
            # Two symbols in one pack: withdrawing one leaves the pack published.
            "leaving": _symbol(connection, actor, "leaving-shared", "TST-80", "published", shared_pack),
            "staying": _symbol(connection, actor, "staying-shared", "TST-81", "published", shared_pack),
        }


def _scalar(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _states(engine):
    with engine.connect() as connection:
        return {
            "revisions": dict(connection.execute(text(
                "SELECT gs.slug, sr.lifecycle_state FROM symbol_revisions sr JOIN governed_symbols gs ON gs.id = sr.symbol_id"
            )).all()),
            "pages": dict(connection.execute(text("SELECT page_code, publication_state FROM published_pages")).all()),
            "packs": dict(connection.execute(text("SELECT pack_code, status FROM publication_packs")).all()),
        }


def _run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_dry_run_reports_and_changes_nothing(env, capsys):
    before = _states(env["engine"])
    code, out, err = _run(["--symbol", "TST-71", "--symbol", "qet-pressure-switch"], capsys)
    assert code == 0, err
    report = json.loads(out)
    assert report["mode"] == "dry-run"
    assert [(item["catalogSymbolId"], item["revisions"], item["activePages"], item["packs"]) for item in report["symbols"]] == [
        ("TST-71", {"r1": "published"}, 1, ["qet-solenoid-source-pack"]),
        (None, {"r1": "review"}, 0, []),
    ]
    assert _states(env["engine"]) == before


def test_an_unknown_symbol_refuses_the_whole_run(env, capsys):
    before = _states(env["engine"])
    code, _out, err = _run(["--symbol", "TST-71", "--symbol", "S-999999", "--apply",
                            "--actor-id", str(env["actor"]), "--reason", "test"], capsys)
    assert code == 1 and "symbol not found: S-999999" in err
    assert _states(env["engine"]) == before


def test_apply_needs_an_actor_and_a_reason(env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--symbol", "TST-71", "--apply", "--actor-id", str(env["actor"])])


def _in_catalog(engine, slug):
    """Rows for this symbol in the projection the Catalog reads.

    Not `published_symbol_views`: that legacy materialized view filters on no
    state at all and nothing in the application reads it.
    """
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT count(*) FROM active_public_symbol_projections p JOIN governed_symbols gs ON gs.id = p.governed_symbol_id "
            "WHERE gs.slug = :slug"
        ), {"slug": slug}).scalar_one()


def test_apply_withdraws_retires_and_records_the_restore_point(env, capsys):
    assert _in_catalog(env["engine"], "qet-solenoid-valve") == 1
    reason = "QElectroTech element published without the CC BY 3.0 attribution its licence requires"
    code, _out, err = _run(["--symbol", "TST-71", "--symbol", "qet-pressure-switch", "--symbol", "TST-80", "--apply",
                            "--actor-id", str(env["actor"]), "--reason", reason], capsys)
    assert code == 0, err
    states = _states(env["engine"])
    assert states["revisions"] == {
        "qet-solenoid-valve": "withdrawn", "qet-pressure-switch": "withdrawn",
        "leaving-shared": "withdrawn", "staying-shared": "published",
    }
    assert states["pages"] == {
        "PAGE-qet-solenoid-valve": "retired", "PAGE-leaving-shared": "retired", "PAGE-staying-shared": "active",
    }
    assert states["packs"] == {"qet-solenoid-source-pack": "retired", "shared-pack": "published"}
    engine = env["engine"]
    assert _scalar(engine, "SELECT retirement_reason FROM published_pages WHERE page_code = 'PAGE-qet-solenoid-valve'") == reason
    assert _scalar(engine, "SELECT retired_by FROM pack_entries WHERE symbol_revision_id = :r",
                   r=env["valve"]["revision_id"]) == env["actor"]
    # Identity and history stay.
    assert _scalar(engine, "SELECT catalog_symbol_id FROM governed_symbols WHERE slug = 'qet-solenoid-valve'") == "TST-71"
    assert _scalar(engine, "SELECT visibility FROM governed_symbols WHERE slug = 'qet-solenoid-valve'") == "public"
    with engine.connect() as connection:
        events = connection.execute(text(
            "SELECT payload_json FROM audit_events WHERE action = 'governed_symbol.withdrawn' ORDER BY payload_json->>'slug'"
        )).scalars().all()
    assert [event["slug"] for event in events] == ["leaving-shared", "qet-pressure-switch", "qet-solenoid-valve"]
    valve = events[2]
    assert valve["previous"]["revisions"] == {str(env["valve"]["revision_id"]): "published"}
    assert valve["previous"]["publishedPageIds"] == [str(env["valve"]["page_id"])]
    assert valve["retiredPacks"] == [{"id": str(env["own_pack"]), "previousStatus": "published"}]
    assert events[0]["retiredPacks"] == []
    assert _in_catalog(engine, "qet-solenoid-valve") == 0
    assert _in_catalog(engine, "staying-shared") == 1


def test_a_second_apply_finds_nothing_left_to_withdraw(env, capsys):
    code, out, err = _run(["--symbol", "TST-71", "--apply", "--actor-id", str(env["actor"]), "--reason", "again"], capsys)
    assert code == 0, err
    assert json.loads(out)["symbols"][0]["revisions"] == {}
