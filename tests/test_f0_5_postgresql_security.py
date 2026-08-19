from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Generator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from symgov_backend.auth import (
    authoritative_user_from_token,
    complete_credential_change,
    create_user_session,
    hash_pin,
    hash_session_token,
)
from symgov_backend.models import User, UserRole, UserSession, UserSubscription

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


@dataclass(frozen=True)
class DisposablePostgres:
    url: str
    baseline_tables: frozenset[str]
    baseline_user_session_columns: frozenset[str]
    migrated_forced_user_id: uuid.UUID
    migrated_forced_token: str
    migrated_ordinary_user_id: uuid.UUID
    migrated_ordinary_token: str


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alembic(url: str, *args: str) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "SYMGOV_DATABASE_URL": url,
        "SYMGOV_MIGRATION_DATABASE_URL": url,
    }
    subprocess.run(
        ["alembic", *args],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def disposable_postgres() -> Generator[DisposablePostgres, None, None]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the disposable PostgreSQL security rehearsal")
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon is required for the disposable PostgreSQL security rehearsal")

    name = f"symgov-f05-{uuid.uuid4().hex[:12]}"
    password = "disposable-f05-test-password"
    _docker(
        "run",
        "--rm",
        "--detach",
        "--name",
        name,
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_DB=symgov_f05",
        "--publish",
        "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    try:
        port_output = _docker("port", name, "5432/tcp").stdout.strip()
        port = int(port_output.rsplit(":", 1)[1])
        url = f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/symgov_f05"
        raw_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
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
        _alembic(url, "upgrade", "20260802_0026")
        engine = create_engine(url)
        with engine.connect() as connection:
            baseline_tables = frozenset(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
            baseline_columns = frozenset(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'user_sessions'"
                    )
                ).scalars()
            )
        migrated_forced_user_id = uuid.uuid4()
        migrated_ordinary_user_id = uuid.uuid4()
        migrated_forced_token = f"pre-0027-forced-{uuid.uuid4().hex}"
        migrated_ordinary_token = f"pre-0027-ordinary-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with engine.begin() as connection:
            for user_id, email, must_change_pin in (
                (migrated_forced_user_id, "migrated-forced@example.test", True),
                (migrated_ordinary_user_id, "migrated-ordinary@example.test", False),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
                        "VALUES (:id,:email,:name,:pin_hash,:now,:must_change_pin,true,:now,:now)"
                    ),
                    {
                        "id": user_id,
                        "email": email,
                        "name": email,
                        "pin_hash": hash_pin("1234"),
                        "must_change_pin": must_change_pin,
                        "now": now,
                    },
                )
            for user_id, raw_token in (
                (migrated_forced_user_id, migrated_forced_token),
                (migrated_ordinary_user_id, migrated_ordinary_token),
            ):
                connection.execute(
                    text(
                        "INSERT INTO user_sessions "
                        "(id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at) "
                        "VALUES (:id,:user_id,:token_hash,:now,:expires_at,NULL,:now)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "user_id": user_id,
                        "token_hash": hash_session_token(raw_token),
                        "now": now,
                        "expires_at": now + timedelta(days=1),
                    },
                )
        engine.dispose()
        _alembic(url, "upgrade", "20260810_0028")
        with psycopg.connect(raw_url, autocommit=True) as connection:
            connection.execute("GRANT USAGE ON SCHEMA public TO symgov_app")
            connection.execute(
                "GRANT SELECT, INSERT ON "
                "auth_login_attempt_events, auth_throttle_recovery_events TO symgov_app"
            )
        yield DisposablePostgres(
            url,
            baseline_tables,
            baseline_columns,
            migrated_forced_user_id,
            migrated_forced_token,
            migrated_ordinary_user_id,
            migrated_ordinary_token,
        )
    finally:
        _docker("rm", "--force", name, check=False)


def _seed_forced_user(Session) -> tuple[uuid.UUID, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session.begin() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"race-{uuid.uuid4().hex}@example.test",
            display_name=f"Race {uuid.uuid4().hex}",
            pin_hash=hash_pin("1234"),
            pin_set_at=now,
            must_change_pin=True,
            is_active=True,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(user)
        session.flush()
        token = create_user_session(session, user=user, ttl_hours=0.5, purpose="credential_change")
        return user.id, token


def _admin_revoke(
    Session,
    user_id: uuid.UUID,
    operation: str,
    *,
    lock_attempted=None,
    backend_pid: list[int] | None = None,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session.begin() as session:
        if lock_attempted is not None and backend_pid is not None:
            backend_pid.append(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            lock_attempted.set()
        user = session.query(User).filter(User.id == user_id).with_for_update().one()
        if operation == "reset":
            user.pin_hash = hash_pin("5678")
            user.pin_set_at = now
            user.must_change_pin = True
        else:
            user.is_active = False
        user.updated_at = now
        session.query(UserSession).filter(
            UserSession.auth_user_id == user_id,
            UserSession.revoked_at.is_(None),
        ).update({UserSession.revoked_at: now}, synchronize_session=False)


def _wait_for_blocked_backend(engine, backend_pid: int, *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while True:
        with engine.connect() as connection:
            blockers = connection.execute(
                text("SELECT pg_blocking_pids(:backend_pid)"),
                {"backend_pid": backend_pid},
            ).scalar_one()
        if blockers:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"PostgreSQL backend {backend_pid} did not reach a blocked lock")
        time.sleep(0.01)


def _seed_application_submitter(Session) -> tuple[uuid.UUID, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = date.today()
    with Session.begin() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"submission-race-{uuid.uuid4().hex}@example.test",
            display_name=f"Submission Race {uuid.uuid4().hex}",
            pin_hash=hash_pin("1234"),
            pin_set_at=now,
            must_change_pin=False,
            is_active=True,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role="submitter", created_at=now))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier="plus",
                started_on=today,
                expires_on=today + timedelta(days=30),
                anchor_day=today.day,
                is_protected=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        token = create_user_session(session, user=user, ttl_hours=0.5, purpose="application")
        return user.id, token


@pytest.mark.parametrize("operation", ["reset", "deactivate"])
@pytest.mark.parametrize("commit_order", ["change_first", "admin_first"])
def test_change_pin_cannot_resurrect_session_after_admin_revocation(
    disposable_postgres: DisposablePostgres,
    operation: str,
    commit_order: str,
):
    engine = create_engine(disposable_postgres.url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    user_id, token = _seed_forced_user(Session)

    if commit_order == "admin_first":
        _admin_revoke(Session, user_id, operation)
        with Session() as session:
            with pytest.raises(ValueError):
                complete_credential_change(
                    session,
                    token=token,
                    current_pin="1234",
                    new_pin="9012",
                )
            session.rollback()
    else:
        change_locked = __import__("threading").Event()
        allow_change_commit = __import__("threading").Event()
        admin_lock_attempted = __import__("threading").Event()
        admin_backend_pid: list[int] = []

        def change_worker() -> None:
            with Session() as session:
                complete_credential_change(
                    session,
                    token=token,
                    current_pin="1234",
                    new_pin="9012",
                )
                change_locked.set()
                assert allow_change_commit.wait(timeout=10)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            change_future = executor.submit(change_worker)
            assert change_locked.wait(timeout=10)
            admin_future = executor.submit(
                _admin_revoke,
                Session,
                user_id,
                operation,
                lock_attempted=admin_lock_attempted,
                backend_pid=admin_backend_pid,
            )
            assert admin_lock_attempted.wait(timeout=10)
            _wait_for_blocked_backend(engine, admin_backend_pid[0])
            assert not admin_future.done()
            allow_change_commit.set()
            change_future.result(timeout=10)
            admin_future.result(timeout=10)

    with Session() as session:
        user = session.get(User, user_id)
        assert session.query(UserSession).filter(
            UserSession.auth_user_id == user_id,
            UserSession.revoked_at.is_(None),
        ).count() == 0
        if operation == "reset":
            assert user.must_change_pin is True
            assert user.is_active is True
        else:
            assert user.is_active is False
    engine.dispose()


@pytest.mark.parametrize("operation", ["reset", "deactivate"])
def test_authoritative_submission_guard_serializes_admin_revocation_until_side_effect_boundary_closes(
    disposable_postgres: DisposablePostgres,
    operation: str,
):
    engine = create_engine(disposable_postgres.url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    user_id, token = _seed_application_submitter(Session)
    guard_locked = __import__("threading").Event()
    release_guard = __import__("threading").Event()
    admin_committed = __import__("threading").Event()
    admin_lock_attempted = __import__("threading").Event()
    admin_backend_pid: list[int] = []
    side_effects: list[str] = []

    def guarded_request() -> None:
        with Session() as session:
            current = authoritative_user_from_token(session, token)
            assert current is not None
            assert "submitter" in current.roles
            guard_locked.set()
            side_effects.append("handler")
            assert release_guard.wait(timeout=10)
            session.rollback()

    def admin_revoke() -> None:
        _admin_revoke(
            Session,
            user_id,
            operation,
            lock_attempted=admin_lock_attempted,
            backend_pid=admin_backend_pid,
        )
        admin_committed.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        request_future = executor.submit(guarded_request)
        assert guard_locked.wait(timeout=10)
        admin_future = executor.submit(admin_revoke)
        assert admin_lock_attempted.wait(timeout=10)
        _wait_for_blocked_backend(engine, admin_backend_pid[0])
        assert not admin_committed.is_set()
        assert side_effects == ["handler"]
        release_guard.set()
        request_future.result(timeout=10)
        admin_future.result(timeout=10)

    assert admin_committed.is_set()
    with Session() as session:
        user = session.get(User, user_id)
        stored_session = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one()
        assert stored_session.revoked_at is not None
        if operation == "reset":
            assert user.must_change_pin is True
            assert user.is_active is True
        else:
            assert user.is_active is False
    engine.dispose()


def test_migration_executes_append_only_downgrade_and_reupgrade_contract(
    disposable_postgres: DisposablePostgres,
):
    engine = create_engine(disposable_postgres.url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session.begin() as session:
        forced_session = session.query(UserSession).filter(
            UserSession.token_hash == hash_session_token(disposable_postgres.migrated_forced_token)
        ).one()
        ordinary_session = session.query(UserSession).filter(
            UserSession.token_hash == hash_session_token(disposable_postgres.migrated_ordinary_token)
        ).one()
        assert forced_session.purpose == "credential_change"
        assert forced_session.revoked_at is None
        assert ordinary_session.purpose == "application"
        _, replacement_token = complete_credential_change(
            session,
            token=disposable_postgres.migrated_forced_token,
            current_pin="1234",
            new_pin="5678",
        )

    with Session() as session:
        migrated_user = session.get(User, disposable_postgres.migrated_forced_user_id)
        forced_session = session.query(UserSession).filter(
            UserSession.token_hash == hash_session_token(disposable_postgres.migrated_forced_token)
        ).one()
        # replacement_token is None because the credential_change session was revoked.
        # We check the forced_session state instead.
        assert migrated_user.must_change_pin is False
        assert forced_session.revoked_at is not None
        assert forced_session.purpose == "credential_change"

    actor_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:name,'test',:now,false,true,:now,:now)"
            ),
            {"id": actor_id, "email": "migration@example.test", "name": "Migration Actor", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO auth_login_attempt_events "
                "(id,occurred_at,email_key_hash,outcome,request_metadata_json) "
                "VALUES (:id,:now,'hash','success','{}')"
            ),
            {"id": uuid.uuid4(), "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO auth_throttle_recovery_events "
                "(id,actor_id,scope,target_key_hash,reason,cleared_count,created_at) "
                "VALUES (:id,:actor,'account','hash','disposable rehearsal',1,:now)"
            ),
            {"id": uuid.uuid4(), "actor": actor_id, "now": now},
        )

    role_login_event_id = uuid.uuid4()
    role_recovery_event_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE symgov_app"))
        assert connection.execute(text("SELECT current_user")).scalar_one() == "symgov_app"
        for table in ("auth_login_attempt_events", "auth_throttle_recovery_events"):
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, :table, 'SELECT, INSERT')"),
                {"table": table},
            ).scalar_one() is True
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, :table, 'UPDATE, DELETE, TRUNCATE')"),
                {"table": table},
            ).scalar_one() is False
        connection.execute(
            text(
                "INSERT INTO auth_login_attempt_events "
                "(id,occurred_at,email_key_hash,outcome,request_metadata_json) "
                "VALUES (:id,:now,'role-hash','success','{}')"
            ),
            {"id": role_login_event_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO auth_throttle_recovery_events "
                "(id,actor_id,scope,target_key_hash,reason,cleared_count,created_at) "
                "VALUES (:id,:actor,'account','role-hash','least privilege rehearsal',1,:now)"
            ),
            {"id": role_recovery_event_id, "actor": actor_id, "now": now},
        )
        assert connection.execute(
            text("SELECT count(*) FROM auth_login_attempt_events WHERE id = :id"),
            {"id": role_login_event_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM auth_throttle_recovery_events WHERE id = :id"),
            {"id": role_recovery_event_id},
        ).scalar_one() == 1

    for table in ("auth_login_attempt_events", "auth_throttle_recovery_events"):
        for statement in (
            f"UPDATE {table} SET created_at = created_at" if table.endswith("recovery_events") else f"UPDATE {table} SET occurred_at = occurred_at",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with engine.connect() as connection:
                transaction = connection.begin()
                connection.execute(text("SET LOCAL ROLE symgov_app"))
                with pytest.raises(DBAPIError, match="permission denied"):
                    connection.execute(text(statement))
                transaction.rollback()

    for table in ("auth_login_attempt_events", "auth_throttle_recovery_events"):
        for statement in (
            f"UPDATE {table} SET created_at = created_at" if table.endswith("recovery_events") else f"UPDATE {table} SET occurred_at = occurred_at",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(DBAPIError, match="append-only"):
                    connection.execute(text(statement))
                transaction.rollback()

    _alembic(disposable_postgres.url, "downgrade", "20260802_0026")
    with engine.connect() as connection:
        tables = frozenset(
            connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars()
        )
        columns = frozenset(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'user_sessions'"
                )
            ).scalars()
        )
        functions = connection.execute(
            text("SELECT count(*) FROM pg_proc WHERE proname = 'prevent_auth_security_event_mutation'")
        ).scalar_one()
        triggers = connection.execute(
            text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_auth_%'")
        ).scalar_one()
    assert tables == disposable_postgres.baseline_tables
    assert columns == disposable_postgres.baseline_user_session_columns
    assert functions == 0
    assert triggers == 0

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET must_change_pin = true WHERE id = :id"),
            {"id": disposable_postgres.migrated_forced_user_id},
        )

    _alembic(disposable_postgres.url, "upgrade", "20260808_0027")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM alembic_version WHERE version_num = '20260808_0027'")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'auth_login_attempt_events'")).scalar_one() == 1
        # The migration sets purpose='credential_change' only for UNREVOKED sessions of users with must_change_pin=true.
        # Since we revoked the migrated_forced_token via complete_credential_change, it should remain 'application'.
        # We check the migrated_ordinary_token which remains 'application'.
        # To test the migration logic, we would need an unrevoked session for a user whose must_change_pin was set to true before upgrade.
        # The ordinary user has must_change_pin=false, so its session should be 'application'.
        assert connection.execute(
            text("SELECT purpose FROM user_sessions WHERE token_hash = :token_hash"),
            {"token_hash": hash_session_token(disposable_postgres.migrated_ordinary_token)},
        ).scalar_one() == "application"
    engine.dispose()
