from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError


psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alembic(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": url,
        "SYMGOV_MIGRATION_DATABASE_URL": url,
    }
    return subprocess.run(
        ["alembic", *args],
        cwd=BACKEND,
        env=env,
        check=check,
        capture_output=True,
        text=True,
        timeout=240,
    )


@contextmanager
def _database(name_prefix: str):
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the disposable PostgreSQL migration rehearsal")
    name = f"{name_prefix}-{uuid.uuid4().hex[:12]}"
    password = f"disposable-{name_prefix}-password"
    _docker(
        "run",
        "--rm",
        "--detach",
        "--name",
        name,
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_DB=symgov_stage5",
        "--publish",
        "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    engine = None
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/symgov_stage5"
        url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(raw_url, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute("CREATE ROLE symgov_app")
        engine = create_engine(url)
        yield engine, url, raw_url
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "--force", name, check=False)


@pytest.fixture(scope="module")
def stage5_database():
    with _database("symgov-stage5") as (engine, url, raw_url):
        _alembic(url, "upgrade", "20260826_0032")
        _alembic(url, "upgrade", "20260829_0033")
        yield engine, url, raw_url


def _user(connection, label: str) -> uuid.UUID:
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(
        text(
            "INSERT INTO users "
            "(id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ),
        {"id": identifier, "email": f"{label}-{identifier}@example.test", "now": now},
    )
    return identifier


def _organization(connection, label: str) -> uuid.UUID:
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    code = f"{label[:1].upper()}{uuid.uuid4().hex[:7].upper()}"
    connection.execute(
        text(
            "INSERT INTO organizations "
            "(id,code,normalized_code,display_name,name_key,is_active,fallback_icon_svg,created_at,updated_at) "
            "VALUES (:id,:code,:normalized,:code,:normalized,false,'<svg/>',:now,:now)"
        ),
        {"id": identifier, "code": code, "normalized": code.lower(), "now": now},
    )
    return identifier


def _symbol(connection, owner_id, *, organization_id=None, visibility=None):
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    values = {
        "id": identifier,
        "slug": f"stage5-{identifier}",
        "owner": owner_id,
        "organization": organization_id,
        "visibility": visibility,
        "now": now,
    }
    columns = "id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at"
    placeholders = ":id,:slug,:slug,'test','test',:owner,:now,:now"
    if organization_id is not None:
        columns += ",owner_organization_id"
        placeholders += ",:organization"
    if visibility is not None:
        columns += ",visibility"
        placeholders += ",:visibility"
    connection.execute(
        text(f"INSERT INTO governed_symbols ({columns}) VALUES ({placeholders})"),
        values,
    )
    return identifier


def _revision(connection, symbol_id, owner_id, *, lifecycle="draft"):
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(
        text(
            "INSERT INTO symbol_revisions "
            "(id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,:label,:lifecycle,'{}'::jsonb,:owner,:now)"
        ),
        {
            "id": identifier,
            "symbol": symbol_id,
            "label": uuid.uuid4().hex,
            "lifecycle": lifecycle,
            "owner": owner_id,
            "now": now,
        },
    )
    connection.execute(
        text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"),
        {"revision": identifier, "symbol": symbol_id},
    )
    return identifier


def _submission(connection, organization_id, symbol_id, revision_id, actor_id):
    identifier = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO organization_symbol_review_submissions "
            "(id,organization_id,governed_symbol_id,symbol_revision_id,submitted_by_user_id,submitted_at) "
            "VALUES (:id,:organization,:symbol,:revision,:actor,now())"
        ),
        {
            "id": identifier,
            "organization": organization_id,
            "symbol": symbol_id,
            "revision": revision_id,
            "actor": actor_id,
        },
    )
    return identifier


def _approve(connection, submission_id, organization_id, symbol_id, revision_id, actor_id):
    decision = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO organization_symbol_review_decisions "
            "(id,submission_id,organization_id,governed_symbol_id,symbol_revision_id,decided_by_user_id,decision,decided_at) "
            "VALUES (:id,:submission,:organization,:symbol,:revision,:actor,'approved',now())"
        ),
        {
            "id": decision,
            "submission": submission_id,
            "organization": organization_id,
            "symbol": symbol_id,
            "revision": revision_id,
            "actor": actor_id,
        },
    )
    connection.execute(
        text(
            "UPDATE organization_symbol_review_submissions "
            "SET status='closed', closed_at=now() WHERE id=:id"
        ),
        {"id": submission_id},
    )
    return decision


def test_0032_to_0033_upgrade_defaults_checks_indexes_and_view_are_real(stage5_database):
    engine, _, _ = stage5_database
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("governed_symbols")}
    assert columns["owner_organization_id"]["nullable"] is True
    assert columns["visibility"]["nullable"] is False
    assert columns["organization_wide"]["nullable"] is False
    assert "active_public_symbol_projections" in inspector.get_view_names()
    with engine.begin() as connection:
        actor = _user(connection, "legacy")
        symbol = _symbol(connection, actor)
        assert connection.execute(
            text(
                "SELECT owner_organization_id, visibility, organization_wide "
                "FROM governed_symbols WHERE id=:id"
            ),
            {"id": symbol},
        ).one() == (None, "public", False)
        for statement in (
            "UPDATE governed_symbols SET visibility='secret' WHERE id=:id",
            "UPDATE governed_symbols SET organization_wide=true WHERE id=:id",
        ):
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text(statement), {"id": symbol})
            assert getattr(caught.value.orig, "sqlstate", None) == "23514"


def test_review_bindings_history_and_organization_wide_eligibility_are_real(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "review")
        organization = _organization(connection, "review")
        other = _organization(connection, "other")
        symbol = _symbol(connection, actor, organization_id=organization, visibility="public")
        revision = _revision(connection, symbol, actor)
        other_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        other_revision = _revision(connection, other_symbol, actor)

    with pytest.raises(DBAPIError, match="binding"):
        with engine.begin() as connection:
            _submission(connection, other, symbol, revision, actor)

    with pytest.raises(DBAPIError, match="binding"):
        with engine.begin() as connection:
            _submission(connection, organization, symbol, other_revision, actor)

    with pytest.raises(DBAPIError, match="current approved"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE governed_symbols SET organization_wide=true WHERE id=:id"),
                {"id": symbol},
            )

    with engine.begin() as connection:
        submission = _submission(connection, organization, symbol, revision, actor)

    with pytest.raises(DBAPIError, match="binding"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization_symbol_review_decisions "
                    "(id,submission_id,organization_id,governed_symbol_id,symbol_revision_id,decided_by_user_id,decision,decided_at) "
                    "VALUES (:id,:submission,:organization,:symbol,:revision,:actor,'approved',now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "submission": submission,
                    "organization": other,
                    "symbol": symbol,
                    "revision": revision,
                    "actor": actor,
                },
            )

    with engine.begin() as connection:
        decision = _approve(connection, submission, organization, symbol, revision, actor)
        connection.execute(
            text("UPDATE governed_symbols SET organization_wide=true WHERE id=:id"),
            {"id": symbol},
        )

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT organization_wide FROM governed_symbols WHERE id=:id"),
            {"id": symbol},
        ).scalar_one() is True

    for statement, identifier in (
        ("UPDATE organization_symbol_review_decisions SET rationale='tampered' WHERE id=:id", decision),
        ("DELETE FROM organization_symbol_review_decisions WHERE id=:id", decision),
        ("UPDATE organization_symbol_review_submissions SET rationale='tampered' WHERE id=:id", submission),
        ("DELETE FROM organization_symbol_review_submissions WHERE id=:id", submission),
    ):
        with pytest.raises(DBAPIError, match="immutable|append-preserving"):
            with engine.begin() as connection:
                connection.execute(text(statement), {"id": identifier})


def test_one_active_review_per_revision_has_one_real_concurrent_winner(stage5_database):
    engine, _, raw_url = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "race")
        organization = _organization(connection, "race")
        symbol = _symbol(connection, actor, organization_id=organization, visibility="public")
        revision = _revision(connection, symbol, actor)

    first = psycopg.connect(raw_url)
    second = psycopg.connect(raw_url)
    finished = threading.Event()
    outcome = {}
    try:
        first.execute("BEGIN")
        first.execute(
            "INSERT INTO organization_symbol_review_submissions "
            "(id,organization_id,governed_symbol_id,symbol_revision_id,submitted_by_user_id,submitted_at) "
            "VALUES (%s,%s,%s,%s,%s,now())",
            (uuid.uuid4(), organization, symbol, revision, actor),
        )

        def compete():
            try:
                second.execute("BEGIN")
                second.execute("SET LOCAL statement_timeout='5000ms'")
                second.execute(
                    "INSERT INTO organization_symbol_review_submissions "
                    "(id,organization_id,governed_symbol_id,symbol_revision_id,submitted_by_user_id,submitted_at) "
                    "VALUES (%s,%s,%s,%s,%s,now())",
                    (uuid.uuid4(), organization, symbol, revision, actor),
                )
                second.commit()
                outcome["value"] = "committed"
            except psycopg.errors.UniqueViolation as error:
                second.rollback()
                outcome["value"] = error.sqlstate
            finally:
                finished.set()

        thread = threading.Thread(target=compete)
        thread.start()
        time.sleep(0.1)
        first.commit()
        assert finished.wait(timeout=5)
        thread.join(timeout=1)
        assert outcome["value"] == "23505"
    finally:
        first.close()
        second.close()
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM organization_symbol_review_submissions "
                "WHERE symbol_revision_id=:revision AND status='active'"
            ),
            {"revision": revision},
        ).scalar_one() == 1


def test_review_parent_rows_cannot_rebind_immutable_history(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "parent-binding")
        organization = _organization(connection, "parent-binding")
        other = _organization(connection, "parent-other")
        symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        revision = _revision(connection, symbol, actor)
        other_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        _submission(connection, organization, symbol, revision, actor)

    cases = (
        (
            "UPDATE governed_symbols SET owner_organization_id=:other WHERE id=:id",
            {"other": other, "id": symbol},
        ),
        (
            "UPDATE symbol_revisions SET symbol_id=:other WHERE id=:id",
            {"other": other_symbol, "id": revision},
        ),
    )
    for statement, parameters in cases:
        connection = engine.connect()
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError, match="binding"):
                connection.execute(text(statement), parameters)
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            transaction.rollback()
            connection.close()


def test_deferred_organization_wide_validation_uses_final_row_state(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "final-wide-state")
        organization = _organization(connection, "final-wide-state")
        symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        _revision(connection, symbol, actor)
        connection.execute(
            text("UPDATE governed_symbols SET organization_wide=true WHERE id=:id"),
            {"id": symbol},
        )
        connection.execute(
            text("UPDATE governed_symbols SET organization_wide=false WHERE id=:id"),
            {"id": symbol},
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT organization_wide FROM governed_symbols WHERE id=:id"),
            {"id": symbol},
        ).scalar_one() is False


def test_active_public_projection_enforces_every_publication_predicate(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "projection")
        symbol = _symbol(connection, actor)
        revision = _revision(connection, symbol, actor, lifecycle="published")
        catalog_id = f"S5-{uuid.uuid4().hex[:16].upper()}"
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
                "VALUES (:id,:code,'Stage 5','public',CURRENT_DATE,'published',now(),now())"
            ),
            {"id": pack, "code": f"S5-{uuid.uuid4().hex}"},
        )
        connection.execute(
            text(
                "INSERT INTO published_pages "
                "(id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) "
                "VALUES (:id,:code,'Stage 5',:pack,:revision,CURRENT_DATE,now(),now())"
            ),
            {"id": page, "code": f"S5-PAGE-{uuid.uuid4().hex}", "pack": pack, "revision": revision},
        )
        connection.execute(
            text(
                "INSERT INTO pack_entries "
                "(id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) "
                "VALUES (:id,:pack,:revision,:page,1,now())"
            ),
            {"id": entry, "pack": pack, "revision": revision, "page": page},
        )

    def projected() -> int:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT count(*) FROM active_public_symbol_projections "
                    "WHERE governed_symbol_id=:symbol"
                ),
                {"symbol": symbol},
            ).scalar_one()

    assert projected() == 1
    mutations = (
        ("UPDATE governed_symbols SET visibility='organization_private' WHERE id=:id", symbol),
        ("UPDATE governed_symbols SET visibility='public', current_revision_id=NULL WHERE id=:id", symbol),
        ("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:id", symbol),
        ("UPDATE symbol_revisions SET lifecycle_state='draft' WHERE id=:id", revision),
        ("UPDATE symbol_revisions SET lifecycle_state='published' WHERE id=:id", revision),
        ("UPDATE publication_packs SET audience='organization' WHERE id=:id", pack),
        ("UPDATE publication_packs SET audience='public', status='draft' WHERE id=:id", pack),
        ("UPDATE publication_packs SET status='published' WHERE id=:id", pack),
    )
    expected = (0, 0, 1, 0, 1, 0, 0, 1)
    for (statement, identifier), count in zip(mutations, expected, strict=True):
        with engine.begin() as connection:
            connection.execute(text(statement), {"id": identifier, "revision": revision})
        assert projected() == count


def test_least_privilege_grants_and_populated_downgrade_guard_are_real(stage5_database):
    engine, url, _ = stage5_database
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT has_table_privilege('symgov_app','active_public_symbol_projections','SELECT')")
        ).scalar_one() is True
        assert connection.execute(
            text("SELECT has_table_privilege('symgov_app','organization_symbol_review_submissions','SELECT,INSERT')")
        ).scalar_one() is True
        assert connection.execute(
            text("SELECT has_column_privilege('symgov_app','organization_symbol_review_submissions','status','UPDATE')")
        ).scalar_one() is True
        assert connection.execute(
            text("SELECT has_table_privilege('symgov_app','organization_symbol_review_submissions','DELETE,TRUNCATE')")
        ).scalar_one() is False
        assert connection.execute(
            text("SELECT has_table_privilege('symgov_app','organization_symbol_review_decisions','UPDATE,DELETE,TRUNCATE')")
        ).scalar_one() is False
    result = _alembic(url, "downgrade", "20260826_0032", check=False)
    assert result.returncode != 0
    assert "cannot downgrade organization symbol visibility" in (result.stdout + result.stderr)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260829_0033"


def test_clean_0033_downgrade_and_reupgrade_remain_linear():
    with _database("symgov-stage5-clean") as (engine, url, _):
        _alembic(url, "upgrade", "20260826_0032")
        _alembic(url, "upgrade", "20260829_0033")
        _alembic(url, "downgrade", "20260826_0032")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260826_0032"
            assert "organization_symbol_review_submissions" not in inspect(connection).get_table_names()
        _alembic(url, "upgrade", "20260829_0033")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260829_0033"
