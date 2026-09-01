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
        with psycopg.connect(raw_url, autocommit=True) as connection:
            # Production runs migrations as symgov_app, so it owns governed_symbols
            # and symbol_revisions outright; this disposable rehearsal runs migrations
            # as postgres, so the equivalent read access is granted explicitly here.
            connection.execute(
                "GRANT SELECT ON governed_symbols, symbol_revisions TO symgov_app"
            )
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


def _wait_for_blocker(observer, contender_pid: int, blocker_pid: int, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    observed = []
    while time.monotonic() < deadline:
        observed = observer.execute(
            "SELECT pg_blocking_pids(%s)", (contender_pid,)
        ).fetchone()[0]
        if blocker_pid in observed:
            return observed
        time.sleep(0.01)
    raise AssertionError(
        f"backend {contender_pid} was not blocked by {blocker_pid} within {timeout}s; "
        f"last blockers={observed}"
    )


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
        symbol = _symbol(
            connection,
            actor,
            organization_id=organization,
            visibility="organization_private",
        )
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

    for table in (
        "organization_symbol_review_submissions",
        "organization_symbol_review_decisions",
    ):
        connection = engine.connect()
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError, match="immutable|append-preserving"):
                connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        finally:
            transaction.rollback()
            connection.close()


def test_review_validators_ignore_temporary_shadow_relations(stage5_database):
    engine, _, raw_url = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, "temporary-shadow")
        organization = _organization(connection, "temporary-shadow")
        other = _organization(connection, "temporary-shadow-other")
        submission_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        submission_revision = _revision(connection, submission_symbol, actor)
        decision_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        decision_revision = _revision(connection, decision_symbol, actor)
        decision_submission = _submission(
            connection,
            organization,
            decision_symbol,
            decision_revision,
            actor,
        )
        wide_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        wide_revision = _revision(connection, wide_symbol, actor)
        connection.execute(
            text("GRANT UPDATE (organization_wide) ON public.governed_symbols TO symgov_app")
        )
        connection.execute(
            text(
                "GRANT SELECT ON public.published_pages, public.pack_entries, "
                "public.catalog_symbol_identifiers TO symgov_app"
            )
        )

    invalid_submission = uuid.uuid4()
    invalid_decision = uuid.uuid4()
    fabricated_wide_submission = uuid.uuid4()
    fabricated_wide_decision = uuid.uuid4()
    connection = psycopg.connect(raw_url)
    try:
        connection.execute("SET ROLE symgov_app")
        for table in (
            "governed_symbols",
            "symbol_revisions",
            "organization_symbol_review_submissions",
            "organization_symbol_review_decisions",
        ):
            connection.execute(
                f"CREATE TEMP TABLE {table} AS "
                f"SELECT * FROM public.{table} WITH NO DATA"
            )
        connection.execute(
            "INSERT INTO pg_temp.governed_symbols "
            "SELECT * FROM public.governed_symbols WHERE id IN (%s,%s)",
            (submission_symbol, wide_symbol),
        )
        connection.execute(
            "UPDATE pg_temp.governed_symbols SET organization_wide=true WHERE id=%s",
            (wide_symbol,),
        )
        connection.execute(
            "INSERT INTO pg_temp.symbol_revisions "
            "SELECT * FROM public.symbol_revisions WHERE id IN (%s,%s,%s)",
            (submission_revision, decision_revision, wide_revision),
        )
        connection.execute(
            "INSERT INTO pg_temp.organization_symbol_review_submissions "
            "(id,organization_id,governed_symbol_id,symbol_revision_id,"
            "submitted_by_user_id,submitted_at,status,closed_at) VALUES "
            "(%s,%s,%s,%s,%s,now(),'active',NULL),"
            "(%s,%s,%s,%s,%s,now(),'active',NULL),"
            "(%s,%s,%s,%s,%s,now(),'closed',now())",
            (
                invalid_submission,
                organization,
                submission_symbol,
                submission_revision,
                actor,
                decision_submission,
                other,
                decision_symbol,
                decision_revision,
                actor,
                fabricated_wide_submission,
                organization,
                wide_symbol,
                wide_revision,
                actor,
            ),
        )
        connection.execute(
            "INSERT INTO pg_temp.organization_symbol_review_decisions "
            "(id,submission_id,organization_id,governed_symbol_id,"
            "symbol_revision_id,decided_by_user_id,decision,decided_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'approved',now())",
            (
                fabricated_wide_decision,
                fabricated_wide_submission,
                organization,
                wide_symbol,
                wide_revision,
                actor,
            ),
        )
        connection.commit()

        cases = (
            (
                "submission",
                "INSERT INTO public.organization_symbol_review_submissions "
                "(id,organization_id,governed_symbol_id,symbol_revision_id,"
                "submitted_by_user_id,submitted_at) VALUES (%s,%s,%s,%s,%s,now())",
                (
                    invalid_submission,
                    other,
                    submission_symbol,
                    submission_revision,
                    actor,
                ),
                "binding",
            ),
            (
                "decision",
                "INSERT INTO public.organization_symbol_review_decisions "
                "(id,submission_id,organization_id,governed_symbol_id,"
                "symbol_revision_id,decided_by_user_id,decision,decided_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'approved',now())",
                (
                    invalid_decision,
                    decision_submission,
                    other,
                    decision_symbol,
                    decision_revision,
                    actor,
                ),
                "binding",
            ),
            (
                "organization-wide",
                "UPDATE public.governed_symbols SET organization_wide=true WHERE id=%s",
                (wide_symbol,),
                "current approved",
            ),
        )
        outcomes = {}
        for label, statement, parameters, expected_message in cases:
            try:
                connection.execute(statement, parameters)
                connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
                outcomes[label] = ("accepted", "")
            except psycopg.Error as error:
                outcomes[label] = (error.sqlstate, str(error))
            finally:
                connection.rollback()
        assert {label: outcome[0] for label, outcome in outcomes.items()} == {
            "submission": "23514",
            "decision": "23514",
            "organization-wide": "23514",
        }, outcomes
        for label, _, _, expected_message in cases:
            assert expected_message in outcomes[label][1]
    finally:
        connection.close()


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

    temporary_cases = (
        (
            "UPDATE governed_symbols SET owner_organization_id=:other WHERE id=:id",
            "UPDATE governed_symbols SET owner_organization_id=:original WHERE id=:id",
            {"other": other, "original": organization, "id": symbol},
        ),
        (
            "UPDATE symbol_revisions SET symbol_id=:other WHERE id=:id",
            "UPDATE symbol_revisions SET symbol_id=:original WHERE id=:id",
            {"other": other_symbol, "original": symbol, "id": revision},
        ),
    )
    for rebind, restore, parameters in temporary_cases:
        with engine.begin() as connection:
            connection.execute(text(rebind), parameters)
            connection.execute(text(restore), parameters)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

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


@pytest.mark.parametrize(
    ("parent_kind", "first_operation"),
    (
        ("governed_symbol_owner", "submission"),
        ("governed_symbol_owner", "parent_rebind"),
        ("symbol_revision_parent", "submission"),
        ("symbol_revision_parent", "parent_rebind"),
    ),
)
def test_review_insertion_and_parent_rebind_serialize_across_transactions(
    stage5_database, parent_kind, first_operation
):
    engine, _, raw_url = stage5_database
    with engine.begin() as connection:
        actor = _user(connection, f"binding-race-{parent_kind}-{first_operation}")
        organization = _organization(connection, "binding-race")
        other_organization = _organization(connection, "binding-race-other")
        symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        revision = _revision(connection, symbol, actor)
        other_symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )

    submission_id = uuid.uuid4()
    if parent_kind == "governed_symbol_owner":
        parent_statement = (
            "UPDATE governed_symbols SET owner_organization_id=%s WHERE id=%s"
        )
        parent_parameters = (other_organization, symbol)
    else:
        parent_statement = "UPDATE symbol_revisions SET symbol_id=%s WHERE id=%s"
        parent_parameters = (other_symbol, revision)

    def insert_submission(connection):
        connection.execute(
            "INSERT INTO organization_symbol_review_submissions "
            "(id,organization_id,governed_symbol_id,symbol_revision_id,"
            "submitted_by_user_id,submitted_at) VALUES (%s,%s,%s,%s,%s,now())",
            (submission_id, organization, symbol, revision, actor),
        )

    holder = psycopg.connect(raw_url)
    contender = psycopg.connect(raw_url)
    observer = psycopg.connect(raw_url, autocommit=True)
    boundary = threading.Barrier(2)
    finished = threading.Event()
    outcome = {}
    try:
        holder.execute("BEGIN")
        holder_pid = holder.execute("SELECT pg_backend_pid()").fetchone()[0]
        if first_operation == "submission":
            insert_submission(holder)
        else:
            holder.execute(parent_statement, parent_parameters)
        holder.execute("SET CONSTRAINTS ALL IMMEDIATE")

        contender_pid = contender.execute("SELECT pg_backend_pid()").fetchone()[0]

        def contend():
            try:
                contender.execute("BEGIN")
                contender.execute("SET LOCAL statement_timeout='5000ms'")
                boundary.wait(timeout=5)
                if first_operation == "submission":
                    contender.execute(parent_statement, parent_parameters)
                else:
                    insert_submission(contender)
                contender.execute("SET CONSTRAINTS ALL IMMEDIATE")
                contender.commit()
                outcome["value"] = ("committed", None)
            except (DBAPIError, psycopg.Error) as error:
                contender.rollback()
                database_error = getattr(error, "orig", error)
                outcome["value"] = (
                    getattr(database_error, "sqlstate", None),
                    str(database_error),
                )
            finally:
                finished.set()

        thread = threading.Thread(target=contend)
        thread.start()
        boundary.wait(timeout=5)
        observed_blockers = _wait_for_blocker(
            observer, contender_pid, holder_pid, timeout=5.0
        )
        assert holder_pid in observed_blockers
        assert not finished.is_set()

        holder.commit()
        assert finished.wait(timeout=5)
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert outcome["value"][0] == "23514"
        assert "binding" in outcome["value"][1]
        print(
            "binding race",
            parent_kind,
            first_operation,
            f"contender_pid={contender_pid}",
            f"blockers={observed_blockers}",
            f"loser={outcome['value'][0]}",
        )
    finally:
        holder.rollback()
        contender.rollback()
        holder.close()
        contender.close()
        observer.close()

    with engine.connect() as connection:
        submission_rows = connection.execute(
            text(
                "SELECT organization_id, governed_symbol_id, symbol_revision_id "
                "FROM organization_symbol_review_submissions WHERE id=:id"
            ),
            {"id": submission_id},
        ).all()
        owner = connection.execute(
            text("SELECT owner_organization_id FROM governed_symbols WHERE id=:id"),
            {"id": symbol},
        ).scalar_one()
        revision_parent = connection.execute(
            text("SELECT symbol_id FROM symbol_revisions WHERE id=:id"),
            {"id": revision},
        ).scalar_one()

    if first_operation == "submission":
        assert submission_rows == [(organization, symbol, revision)]
        assert owner == organization
        assert revision_parent == symbol
    else:
        assert submission_rows == []
        if parent_kind == "governed_symbol_owner":
            assert owner == other_organization
            assert revision_parent == symbol
        else:
            assert owner == organization
            assert revision_parent == other_symbol


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
        organization = _organization(connection, "projection")
        symbol = _symbol(
            connection, actor, organization_id=organization, visibility="public"
        )
        revision = _revision(connection, symbol, actor, lifecycle="published")
        submission = _submission(connection, organization, symbol, revision, actor)
        _approve(connection, submission, organization, symbol, revision, actor)
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
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE governed_symbols "
                "SET visibility='organization_private', organization_wide=true WHERE id=:id"
            ),
            {"id": symbol},
        )
    assert projected() == 0
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE governed_symbols "
                "SET visibility='public', organization_wide=false WHERE id=:id"
            ),
            {"id": symbol},
        )
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


def test_least_privilege_role_enforces_allowed_and_forbidden_operations(stage5_database):
    engine, url, _ = stage5_database
    with engine.connect() as connection:
        actor = _user(connection, "least-privilege")
        organization = _organization(connection, "least-privilege")
        symbol = _symbol(
            connection, actor, organization_id=organization, visibility="organization_private"
        )
        revision = _revision(connection, symbol, actor)
        connection.commit()

    submission_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    connection = engine.connect()
    try:
        connection.execute(text("SET ROLE symgov_app"))

        connection.execute(text("SELECT count(*) FROM active_public_symbol_projections"))

        connection.execute(
            text(
                "INSERT INTO organization_symbol_review_submissions "
                "(id,organization_id,governed_symbol_id,symbol_revision_id,submitted_by_user_id,submitted_at) "
                "VALUES (:id,:organization,:symbol,:revision,:actor,now())"
            ),
            {
                "id": submission_id,
                "organization": organization,
                "symbol": symbol,
                "revision": revision,
                "actor": actor,
            },
        )
        assert connection.execute(
            text("SELECT status FROM organization_symbol_review_submissions WHERE id=:id"),
            {"id": submission_id},
        ).scalar_one() == "active"

        connection.execute(
            text(
                "INSERT INTO organization_symbol_review_decisions "
                "(id,submission_id,organization_id,governed_symbol_id,symbol_revision_id,"
                "decided_by_user_id,decision,decided_at) "
                "VALUES (:id,:submission,:organization,:symbol,:revision,:actor,'approved',now())"
            ),
            {
                "id": decision_id,
                "submission": submission_id,
                "organization": organization,
                "symbol": symbol,
                "revision": revision,
                "actor": actor,
            },
        )
        connection.execute(
            text(
                "UPDATE organization_symbol_review_submissions "
                "SET status='closed', closed_at=now() WHERE id=:id"
            ),
            {"id": submission_id},
        )
        closed = connection.execute(
            text(
                "SELECT status, closed_at FROM organization_symbol_review_submissions WHERE id=:id"
            ),
            {"id": submission_id},
        ).one()
        assert closed.status == "closed"
        assert closed.closed_at is not None
        connection.commit()

        forbidden = (
            (
                "DELETE FROM organization_symbol_review_submissions WHERE id=:id",
                {"id": submission_id},
            ),
            ("TRUNCATE TABLE organization_symbol_review_submissions", {}),
            (
                "UPDATE organization_symbol_review_decisions SET rationale='x' WHERE id=:id",
                {"id": decision_id},
            ),
            (
                "DELETE FROM organization_symbol_review_decisions WHERE id=:id",
                {"id": decision_id},
            ),
            ("TRUNCATE TABLE organization_symbol_review_decisions", {}),
        )
        for statement, parameters in forbidden:
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(text(statement), parameters)
            connection.rollback()
    finally:
        connection.execute(text("RESET ROLE"))
        connection.commit()
        connection.close()

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM organization_symbol_review_submissions WHERE id=:id"),
            {"id": submission_id},
        ).scalar_one() == "closed"

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
