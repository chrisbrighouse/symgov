from __future__ import annotations

from collections.abc import Generator, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from threading import Barrier, Event, Thread
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.models import OrganizationMembership, OrganizationRoleAssignment, User
from symgov_backend.organization_service import (
    add_organization_member,
    create_organization_with_initial_admin,
    deactivate_membership,
    reconcile_symgov_organization_bootstrap,
    replace_protected_membership_base_role,
    replace_membership_base_role,
)

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AUDIT_IMMUTABILITY_MIGRATION = (
    BACKEND / "alembic" / "versions" / "20260821_0029_audit_event_immutability.py"
)
LEGACY_SESSION_ID: uuid.UUID | None = None


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
def organization_database() -> Generator[Engine, None, None]:
    global LEGACY_SESSION_ID
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the disposable PostgreSQL migration rehearsal")
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon is required for the disposable PostgreSQL migration rehearsal")

    name = f"symgov-org-stage1-{uuid.uuid4().hex[:12]}"
    password = "disposable-org-stage1-password"
    _docker(
        "run",
        "--rm",
        "--detach",
        "--name",
        name,
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        "POSTGRES_DB=symgov_org_stage1",
        "--publish",
        "127.0.0.1::5432",
        "postgres:16-alpine",
    )
    engine = None
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        url = f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/symgov_org_stage1"
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
        _alembic(url, "upgrade", "20260808_0027")
        engine = create_engine(url)
        legacy_user_id = _insert_user(engine, "legacy-session@example.test")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        legacy_session_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_sessions "
                    "(id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose) "
                    "VALUES (:id,:user_id,:token_hash,:now,:expires_at,NULL,:now,'application')"
                ),
                {
                    "id": legacy_session_id,
                    "user_id": legacy_user_id,
                    "token_hash": uuid.uuid4().hex,
                    "now": now,
                    "expires_at": now + timedelta(hours=1),
                },
            )
        engine.dispose()

        _alembic(url, "upgrade", "20260822_0030")
        engine = create_engine(url)
        LEGACY_SESSION_ID = legacy_session_id
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "--force", name, check=False)


def _insert_user(engine: Engine, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": user_id, "email": email, "now": now},
        )
    return user_id


def test_audit_immutability_migration_source_is_append_only_and_reversible():
    source = AUDIT_IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260821_0029"' in source
    assert 'down_revision = "20260810_0028"' in source
    assert "BEFORE UPDATE OR DELETE ON audit_events" in source
    assert "BEFORE TRUNCATE ON audit_events" in source
    assert "GRANT SELECT, INSERT ON audit_events TO symgov_app" in source
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM symgov_app" in source
    assert "DROP TRIGGER IF EXISTS trg_audit_events_append_only" in source
    assert "DROP FUNCTION IF EXISTS protect_audit_events_append_only()" in source


def test_audit_events_are_append_only_for_owner_and_least_privilege_role(
    organization_database: Engine,
):
    owner_event_id = uuid.uuid4()
    app_event_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id,entity_type,entity_id,action,payload_json,created_at) "
                "VALUES (:id,'security_test',:entity_id,'created','{}'::jsonb,:now)"
            ),
            {"id": owner_event_id, "entity_id": uuid.uuid4(), "now": now},
        )
        connection.execute(text("SET LOCAL ROLE symgov_app"))
        assert connection.execute(text("SELECT current_user")).scalar_one() == "symgov_app"
        assert connection.execute(
            text("SELECT has_table_privilege(current_user, 'audit_events', 'SELECT, INSERT')")
        ).scalar_one() is True
        assert connection.execute(
            text(
                "SELECT has_table_privilege("
                "current_user, 'audit_events', 'UPDATE, DELETE, TRUNCATE')"
            )
        ).scalar_one() is False
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id,entity_type,entity_id,action,payload_json,created_at) "
                "VALUES (:id,'security_test',:entity_id,'app_insert','{}'::jsonb,:now)"
            ),
            {"id": app_event_id, "entity_id": uuid.uuid4(), "now": now},
        )
        assert connection.execute(
            text("SELECT count(*) FROM audit_events WHERE id=:id"), {"id": app_event_id}
        ).scalar_one() == 1

    for statement in (
        "UPDATE audit_events SET action='tampered' WHERE id=:id",
        "DELETE FROM audit_events WHERE id=:id",
        "TRUNCATE TABLE audit_events",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            with organization_database.begin() as connection:
                connection.execute(text("SET LOCAL ROLE symgov_app"))
                connection.execute(text(statement), {"id": app_event_id})

    for statement in (
        "UPDATE audit_events SET action='tampered' WHERE id=:id",
        "DELETE FROM audit_events WHERE id=:id",
        "TRUNCATE TABLE audit_events",
    ):
        with pytest.raises(DBAPIError, match="audit events are append-only"):
            with organization_database.begin() as connection:
                connection.execute(text(statement), {"id": owner_event_id})


def test_audit_immutability_migration_downgrades_safely_and_reupgrades(
    organization_database: Engine,
):
    url = organization_database.url.render_as_string(hide_password=False)
    _alembic(url, "downgrade", "20260810_0028")
    try:
        with organization_database.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid='audit_events'::regclass AND NOT tgisinternal"
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'symgov_app', 'audit_events', 'SELECT, INSERT')"
                )
            ).scalar_one() is False
    finally:
        _alembic(url, "upgrade", "20260822_0030")

    with organization_database.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgrelid='audit_events'::regclass AND NOT tgisinternal"
            )
        ).scalar_one() == 2


def _insert_organization(
    engine: Engine,
    *,
    code: str,
    normalized_code: str,
    is_protected: bool = False,
    is_active: bool = False,
) -> uuid.UUID:
    organization_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations "
                "(id,code,normalized_code,display_name,name_key,is_protected,is_active,fallback_icon_svg,created_at,updated_at) "
                "VALUES (:id,:code,:normalized_code,:display_name,:name_key,:is_protected,:is_active,'<svg/>',:now,:now)"
            ),
            {
                "id": organization_id,
                "code": code,
                "normalized_code": normalized_code,
                "display_name": code,
                "name_key": normalized_code,
                "is_protected": is_protected,
                "is_active": is_active,
                "now": now,
            },
        )
    return organization_id


def _insert_membership_with_role(
    engine: Engine,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "admin",
) -> uuid.UUID:
    membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {
                "id": membership_id,
                "organization_id": organization_id,
                "user_id": user_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,:role,true,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "membership_id": membership_id,
                "role": role,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE organizations SET is_active=true WHERE id=:organization_id"),
            {"organization_id": organization_id},
        )
    return membership_id


def test_upgrade_preserves_legacy_session_insert_as_personal(
    organization_database: Engine,
):
    assert LEGACY_SESSION_ID is not None
    with organization_database.connect() as connection:
        row = connection.execute(
            text(
                "SELECT session_mode, active_organization_id, recent_step_up_at "
                "FROM user_sessions WHERE id = :id"
            ),
            {"id": LEGACY_SESSION_ID},
        ).one()
    assert row == ("personal", None, None)


def test_reserved_symgov_identity_and_codes_are_database_enforced(
    organization_database: Engine,
):
    with pytest.raises(IntegrityError, match="ck_organizations_reserved_identity"):
        _insert_organization(
            organization_database,
            code="SYMGOV",
            normalized_code="symgov",
        )

    symgov_id = _insert_organization(
        organization_database,
        code="symgov",
        normalized_code="symgov",
        is_protected=True,
    )
    bootstrap_user_id = _insert_user(organization_database, "symgov-bootstrap-admin@example.test")
    bootstrap_membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {"id": bootstrap_membership_id, "organization_id": symgov_id, "user_id": bootstrap_user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'admin',true,:now)"
            ),
            {"id": uuid.uuid4(), "membership_id": bootstrap_membership_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO platform_role_assignments "
                "(id,user_id,role,is_active,assigned_at) "
                "VALUES (:id,:user_id,'platform_admin',true,:now)"
            ),
            {"id": uuid.uuid4(), "user_id": bootstrap_user_id, "now": now},
        )
        connection.execute(
            text("UPDATE organizations SET is_active=true WHERE id=:organization_id"),
            {"organization_id": symgov_id},
        )
    with pytest.raises(DBAPIError, match="organization code is immutable"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE organizations SET code = 'CHANGED', normalized_code = 'changed' WHERE id = :id"),
                {"id": symgov_id},
            )
    with pytest.raises(DBAPIError, match="organization UUID is immutable"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE organizations SET id = :new_id WHERE id = :id"),
                {"id": symgov_id, "new_id": uuid.uuid4()},
            )
    with pytest.raises(DBAPIError, match="protected symgov organization cannot be deactivated"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE organizations SET is_active = false WHERE id = :id"),
                {"id": symgov_id},
            )
    with pytest.raises(DBAPIError, match="protected symgov organization cannot be deleted"):
        with organization_database.begin() as connection:
            connection.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": symgov_id})

    _insert_organization(organization_database, code="IDOX", normalized_code="idox")
    with pytest.raises(IntegrityError, match="uq_organizations_normalized_code"):
        _insert_organization(organization_database, code="IDOX", normalized_code="idox")


def test_active_organization_insert_requires_admin_at_commit(
    organization_database: Engine,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(DBAPIError, match="active organization must retain at least one active Organization Administrator"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations "
                    "(id,code,normalized_code,display_name,name_key,is_protected,fallback_icon_svg,created_at,updated_at) "
                    "VALUES (:id,'EMPTY-ACTIVE','empty-active','Empty Active','empty-active',false,'<svg/>',:now,:now)"
                ),
                {"id": uuid.uuid4(), "now": now},
            )


def test_session_organization_context_is_immutable_but_step_up_is_mutable(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "session-context@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="SESSION-ORG",
        normalized_code="session-org",
    )
    session_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {
                "id": membership_id,
                "organization_id": organization_id,
                "user_id": user_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'admin',true,:now)"
            ),
            {"id": uuid.uuid4(), "membership_id": membership_id, "now": now},
        )
        connection.execute(
            text("UPDATE organizations SET is_active=true WHERE id=:organization_id"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO user_sessions "
                "(id,auth_user_id,token_hash,created_at,expires_at,session_mode,active_organization_id) "
                "VALUES (:id,:user_id,:token_hash,:now,:expires_at,'organization',:organization_id)"
            ),
            {
                "id": session_id,
                "user_id": user_id,
                "token_hash": uuid.uuid4().hex,
                "now": now,
                "expires_at": now + timedelta(hours=1),
                "organization_id": organization_id,
            },
        )
    with organization_database.begin() as connection:
        connection.execute(
            text("UPDATE user_sessions SET recent_step_up_at = :now WHERE id = :id"),
            {"id": session_id, "now": now},
        )
    with pytest.raises(DBAPIError, match="organization context is immutable"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE user_sessions SET session_mode = 'personal', active_organization_id = NULL WHERE id = :id"),
                {"id": session_id},
            )


def test_organization_session_requires_active_membership_on_insert(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "unentitled-session@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="MEMBER-SESSION",
        normalized_code="member-session",
    )
    anchor_user_id = _insert_user(organization_database, "member-session-anchor@example.test")
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=anchor_user_id,
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(DBAPIError, match="active organization membership"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_sessions "
                    "(id,auth_user_id,token_hash,created_at,expires_at,session_mode,active_organization_id) "
                    "VALUES (:id,:user_id,:token_hash,:now,:expires_at,'organization',:organization_id)"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "token_hash": uuid.uuid4().hex,
                    "now": now,
                    "expires_at": now + timedelta(hours=1),
                    "organization_id": organization_id,
                },
            )


def test_platform_admin_assignment_requires_active_symgov_admin_at_commit(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "ineligible-platform@example.test")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(DBAPIError, match="active Symgov organization admin"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO platform_role_assignments "
                    "(id,user_id,role,is_active,assigned_at) "
                    "VALUES (:id,:user_id,'platform_admin',true,:now)"
                ),
                {"id": uuid.uuid4(), "user_id": user_id, "now": now},
            )


def test_final_active_platform_admin_cannot_be_revoked_by_direct_sql(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "final-platform-admin@example.test")
    membership_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    platform_assignment_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        symgov_id = connection.execute(
            text(
                "SELECT id FROM organizations "
                "WHERE normalized_code='symgov' AND is_protected=true"
            )
        ).scalar_one_or_none()
        if symgov_id is None:
            symgov_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO organizations "
                    "(id,code,normalized_code,display_name,name_key,is_protected,fallback_icon_svg,created_at,updated_at) "
                    "VALUES (:id,'symgov','symgov','Symgov','symgov',true,'<svg/>',:now,:now)"
                ),
                {"id": symgov_id, "now": now},
            )
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {"id": membership_id, "organization_id": symgov_id, "user_id": user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'admin',true,:now)"
            ),
            {"id": assignment_id, "membership_id": membership_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO platform_role_assignments "
                "(id,user_id,role,is_active,assigned_at) "
                "VALUES (:id,:user_id,'platform_admin',true,:now)"
            ),
            {"id": platform_assignment_id, "user_id": user_id, "now": now},
        )

    with pytest.raises(DBAPIError, match="active Symgov organization must retain at least one active Platform Administrator"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE platform_role_assignments "
                    "SET is_active=false, revoked_at=:now "
                    "WHERE role='platform_admin' AND is_active=true"
                ),
                {"now": now},
            )


def test_active_membership_requires_exactly_one_active_base_role_at_commit(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "membership-role@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="ROLE-ORG",
        normalized_code="role-org",
    )
    membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    anchor_admin_user_id = _insert_user(organization_database, "membership-role-anchor-admin@example.test")
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=anchor_admin_user_id,
    )

    with pytest.raises(DBAPIError, match="must have exactly one active base role"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization_memberships "
                    "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                    "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
                ),
                {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
            )

    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'admin',true,:now)"
            ),
            {"id": uuid.uuid4(), "membership_id": membership_id, "now": now},
        )

    with pytest.raises(DBAPIError, match="inactive organization membership cannot retain an active base role"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE organization_memberships SET status = 'inactive', deactivated_at = :now WHERE id = :id"),
                {"id": membership_id, "now": now},
            )

    with organization_database.begin() as connection:
        connection.execute(
            text(
                "UPDATE organization_role_assignments SET is_active=false, revoked_at=:now "
                "WHERE membership_id=:membership_id AND is_active=true"
            ),
            {"membership_id": membership_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'user',true,:now)"
            ),
            {"id": uuid.uuid4(), "membership_id": membership_id, "now": now},
        )


def test_active_organization_cannot_lose_final_admin_by_direct_sql(
    organization_database: Engine,
):
    admin_user_id = _insert_user(organization_database, "final-org-admin@example.test")
    ordinary_user_id = _insert_user(organization_database, "remaining-org-user@example.test")
    organization_id = uuid.uuid4()
    admin_membership_id = uuid.uuid4()
    ordinary_membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations "
                "(id,code,normalized_code,display_name,name_key,is_protected,fallback_icon_svg,created_at,updated_at) "
                "VALUES (:id,'MINIMUM-ADMIN','minimum-admin','Minimum Admin','minimum-admin',false,'<svg/>',:now,:now)"
            ),
            {"id": organization_id, "now": now},
        )
        for membership_id, user_id, role in (
            (admin_membership_id, admin_user_id, "admin"),
            (ordinary_membership_id, ordinary_user_id, "user"),
        ):
            connection.execute(
                text(
                    "INSERT INTO organization_memberships "
                    "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                    "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
                ),
                {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO organization_role_assignments "
                    "(id,membership_id,base_role,is_active,assigned_at) "
                    "VALUES (:id,:membership_id,:role,true,:now)"
                ),
                {"id": uuid.uuid4(), "membership_id": membership_id, "role": role, "now": now},
            )

    with pytest.raises(DBAPIError, match="active organization must retain at least one active Organization Administrator"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE organization_role_assignments "
                    "SET is_active=false, revoked_at=:now "
                    "WHERE membership_id=:membership_id AND is_active=true"
                ),
                {"membership_id": admin_membership_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO organization_role_assignments "
                    "(id,membership_id,base_role,is_active,assigned_at) "
                    "VALUES (:id,:membership_id,'user',true,:now)"
                ),
                {"id": uuid.uuid4(), "membership_id": admin_membership_id, "now": now},
            )


@pytest.mark.parametrize(
    ("code", "normalized_code", "admin_email", "eligibility_update"),
    (
        ("INACTIVE-ADMIN", "inactive-admin", "inactive-final-admin@example.test", "is_active=false"),
        ("DELETED-ADMIN", "deleted-admin", "deleted-final-admin@example.test", "deleted_at=:now"),
    ),
)
def test_active_organization_cannot_lose_final_admin_user_by_direct_sql(
    organization_database: Engine,
    code: str,
    normalized_code: str,
    admin_email: str,
    eligibility_update: str,
):
    admin_user_id = _insert_user(organization_database, admin_email)
    ordinary_user_id = _insert_user(organization_database, f"remaining-{normalized_code}@example.test")
    organization_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations "
                "(id,code,normalized_code,display_name,name_key,is_protected,fallback_icon_svg,created_at,updated_at) "
                "VALUES (:id,:code,:normalized_code,:code,:normalized_code,false,'<svg/>',:now,:now)"
            ),
            {
                "id": organization_id,
                "code": code,
                "normalized_code": normalized_code,
                "now": now,
            },
        )
        for user_id, role in ((admin_user_id, "admin"), (ordinary_user_id, "user")):
            membership_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO organization_memberships "
                    "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                    "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
                ),
                {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO organization_role_assignments "
                    "(id,membership_id,base_role,is_active,assigned_at) "
                    "VALUES (:id,:membership_id,:role,true,:now)"
                ),
                {"id": uuid.uuid4(), "membership_id": membership_id, "role": role, "now": now},
            )

    with pytest.raises(DBAPIError, match="active organization must retain at least one active Organization Administrator"):
        with organization_database.begin() as connection:
            connection.execute(
                text(f"UPDATE users SET {eligibility_update}, updated_at=:now WHERE id=:user_id"),
                {"user_id": admin_user_id, "now": now},
            )


def test_duplicate_membership_is_rejected_for_same_organization_and_user(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "duplicate-member@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="DUPLICATE-MEMBER",
        normalized_code="duplicate-member",
    )
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=user_id,
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(IntegrityError, match="uq_organization_memberships_org_user"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization_memberships "
                    "(id,organization_id,user_id,status,created_at,updated_at) "
                    "VALUES (:id,:organization_id,:user_id,'inactive',:now,:now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "now": now,
                },
            )


def test_concurrent_admin_demotions_serialize_and_preserve_one_active_admin(
    organization_database: Engine,
):
    user_one_id = _insert_user(organization_database, "role-one@example.test")
    user_two_id = _insert_user(organization_database, "role-two@example.test")
    actor_id = user_one_id
    organization_id = _insert_organization(
        organization_database,
        code="CONCURRENT-ROLE",
        normalized_code="concurrent-role",
    )
    membership_ids = (
        _insert_membership_with_role(
            organization_database,
            organization_id=organization_id,
            user_id=user_one_id,
        ),
        _insert_membership_with_role(
            organization_database,
            organization_id=organization_id,
            user_id=user_two_id,
        ),
    )
    Session = sessionmaker(bind=organization_database, autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)
    outcomes: list[str] = []

    def demote(membership_id: uuid.UUID) -> None:
        with Session() as session:
            barrier.wait(timeout=10)
            try:
                replace_membership_base_role(
                    session,
                    membership_id=membership_id,
                    new_base_role="user",
                    actor_user_id=actor_id,
                )
                session.commit()
                outcomes.append("committed")
            except ValueError as exc:
                session.rollback()
                outcomes.append(str(exc))

    threads = [Thread(target=demote, args=(membership_id,)) for membership_id in membership_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    assert outcomes.count("committed") == 1
    assert sum("last active organization admin" in outcome.lower() for outcome in outcomes) == 1
    with Session() as session:
        active_admin_count = (
            session.query(OrganizationRoleAssignment)
            .join(
                OrganizationMembership,
                OrganizationMembership.id == OrganizationRoleAssignment.membership_id,
            )
            .filter(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == "active",
                OrganizationRoleAssignment.base_role == "admin",
                OrganizationRoleAssignment.is_active.is_(True),
            )
            .count()
        )
    assert active_admin_count == 1


ADMINISTRATION_LOCK_KEY = (0x53594D47, 0x4F524731)


def _ensure_protected_owner_platform_admin(engine: Engine) -> uuid.UUID:
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        owner = session.execute(
            select(User).where(User.email == "chris.brighouse@hotmail.co.uk")
        ).scalar_one_or_none()
        if owner is None:
            owner_id = _insert_user(engine, "chris.brighouse@hotmail.co.uk")
        else:
            owner_id = owner.id
    with Session() as session:
        reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()
    return owner_id


def test_bootstrap_reactivates_protected_owner_without_rewriting_membership_history(
    organization_database: Engine,
):
    owner_id = _ensure_protected_owner_platform_admin(organization_database)
    safety_admin_id, _ = _insert_symgov_admin(
        organization_database,
        email=f"bootstrap-safety-{uuid.uuid4().hex[:8]}@example.test",
    )

    with organization_database.begin() as connection:
        membership_id, seeded_activated_at = connection.execute(
            text(
                "SELECT membership.id, membership.activated_at "
                "FROM organization_memberships membership "
                "JOIN organizations organization ON organization.id=membership.organization_id "
                "WHERE membership.user_id=:user_id AND organization.normalized_code='symgov'"
            ),
            {"user_id": owner_id},
        ).one()
        seeded_invited_at = seeded_activated_at - timedelta(days=1)
        seeded_deactivated_at = seeded_activated_at + timedelta(days=1)
        connection.execute(
            text(
                "INSERT INTO platform_role_assignments "
                "(id,user_id,role,is_active,assigned_at) "
                "VALUES (:id,:user_id,'platform_admin',true,:assigned_at)"
            ),
            {
                "id": uuid.uuid4(),
                "user_id": safety_admin_id,
                "assigned_at": seeded_invited_at,
            },
        )
        connection.execute(
            text(
                "UPDATE organization_role_assignments "
                "SET is_active=false, revoked_at=:deactivated_at, revoke_reason='test_seed' "
                "WHERE membership_id=:membership_id AND is_active=true"
            ),
            {
                "membership_id": membership_id,
                "deactivated_at": seeded_deactivated_at,
            },
        )
        connection.execute(
            text(
                "UPDATE platform_role_assignments "
                "SET is_active=false, revoked_at=:deactivated_at, revoke_reason='test_seed' "
                "WHERE user_id=:user_id AND role='platform_admin' AND is_active=true"
            ),
            {"user_id": owner_id, "deactivated_at": seeded_deactivated_at},
        )
        connection.execute(
            text(
                "UPDATE organization_memberships "
                "SET status='inactive', invited_at=:invited_at, "
                "deactivated_at=:deactivated_at, updated_at=:deactivated_at "
                "WHERE id=:membership_id"
            ),
            {
                "membership_id": membership_id,
                "invited_at": seeded_invited_at,
                "deactivated_at": seeded_deactivated_at,
            },
        )

    Session = sessionmaker(bind=organization_database, autoflush=False, expire_on_commit=False)
    with Session() as session:
        reconcile_symgov_organization_bootstrap(session, apply=True)
        session.commit()

    with organization_database.connect() as connection:
        membership = connection.execute(
            text(
                "SELECT status, invited_at, activated_at, deactivated_at "
                "FROM organization_memberships WHERE id=:membership_id"
            ),
            {"membership_id": membership_id},
        ).one()
        active_admin_count = connection.execute(
            text(
                "SELECT count(*) FROM organization_role_assignments "
                "WHERE membership_id=:membership_id AND base_role='admin' AND is_active=true"
            ),
            {"membership_id": membership_id},
        ).scalar_one()
        active_platform_admin_count = connection.execute(
            text(
                "SELECT count(*) FROM platform_role_assignments "
                "WHERE user_id=:user_id AND role='platform_admin' AND is_active=true"
            ),
            {"user_id": owner_id},
        ).scalar_one()

    assert membership == (
        "active",
        seeded_invited_at,
        seeded_activated_at,
        seeded_deactivated_at,
    )
    assert active_admin_count == 1
    assert active_platform_admin_count == 1


@pytest.mark.parametrize(
    ("table_name", "expected_error"),
    (
        ("organization_memberships", "membership history cannot be deleted"),
        ("organization_role_assignments", "assignment history cannot be deleted"),
        ("organization_member_capabilities", "capability history cannot be deleted"),
        ("platform_role_assignments", "platform-role history cannot be deleted"),
    ),
)
def test_history_tables_reject_truncate(
    organization_database: Engine,
    table_name: str,
    expected_error: str,
):
    with organization_database.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError, match=expected_error):
                connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
        finally:
            transaction.rollback()


def test_symgov_app_history_privileges_are_least_privilege(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, f"runtime-history-{uuid.uuid4().hex[:8]}@example.test")
    organization_slug = f"runtime-{uuid.uuid4().hex[:8]}"
    organization_id = _insert_organization(
        organization_database,
        code=organization_slug.upper(),
        normalized_code=organization_slug,
    )
    membership_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'inactive',:now,:now)"
            ),
            {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
        )

    inserts = {
        "organization_memberships": (
            "(id,organization_id,user_id,status,created_at,updated_at) "
            "VALUES (:id,:organization_id,:user_id,'inactive',:now,:now)",
            {"organization_id": organization_id, "user_id": _insert_user(organization_database, f"runtime-member-{uuid.uuid4().hex[:8]}@example.test")},
        ),
        "organization_role_assignments": (
            "(id,membership_id,base_role,is_active,assigned_at,revoked_at) "
            "VALUES (:id,:membership_id,'user',false,:now,:now)",
            {"membership_id": membership_id},
        ),
        "organization_member_capabilities": (
            "(id,membership_id,capability,is_active,granted_at,revoked_at) "
            "VALUES (:id,:membership_id,'contributor',false,:now,:now)",
            {"membership_id": membership_id},
        ),
        "platform_role_assignments": (
            "(id,user_id,role,is_active,assigned_at,revoked_at) "
            "VALUES (:id,:user_id,'platform_admin',false,:now,:now)",
            {"user_id": user_id},
        ),
    }
    row_ids = {table_name: uuid.uuid4() for table_name in inserts}

    with organization_database.begin() as connection:
        connection.execute(text("SET LOCAL ROLE symgov_app"))
        assert connection.execute(text("SELECT current_user")).scalar_one() == "symgov_app"
        for table_name, (values_sql, parameters) in inserts.items():
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, :table, 'SELECT, INSERT')"),
                {"table": table_name},
            ).scalar_one() is True
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, :table, 'UPDATE, DELETE, TRUNCATE')"),
                {"table": table_name},
            ).scalar_one() is False
            connection.execute(
                text(f"INSERT INTO {table_name} {values_sql}"),
                {"id": row_ids[table_name], "now": now, **parameters},
            )
            assert connection.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE id=:id"),
                {"id": row_ids[table_name]},
            ).scalar_one() == 1

    for table_name, row_id in row_ids.items():
        for statement in (
            f"UPDATE {table_name} SET id=id WHERE id=:id",
            f"DELETE FROM {table_name} WHERE id=:id",
            f"TRUNCATE TABLE {table_name}",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with organization_database.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE symgov_app"))
                    connection.execute(text(statement), {"id": row_id})


def _insert_symgov_admin(engine: Engine, *, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = _insert_user(engine, email)
    with engine.connect() as connection:
        organization_id = connection.execute(
            text("SELECT id FROM organizations WHERE normalized_code='symgov'")
        ).scalar_one()
    membership_id = _insert_membership_with_role(
        engine,
        organization_id=organization_id,
        user_id=user_id,
    )
    return user_id, membership_id


def _wait_for_administration_lock_waiter(engine: Engine, pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND pid=:pid AND classid=:classid AND objid=:objid AND granted=false)"
                ),
                {
                    "pid": pid,
                    "classid": ADMINISTRATION_LOCK_KEY[0],
                    "objid": ADMINISTRATION_LOCK_KEY[1],
                },
            ).scalar_one()
        if waiting:
            return True
        time.sleep(0.02)
    return False


def _run_mixed_administration_pair(
    engine: Engine,
    first_operation,
    second_operation,
) -> list[BaseException]:
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    operation_complete = [Event(), Event()]
    release_commit = [Event(), Event()]
    backend_ready = [Event(), Event()]
    backend_pids = [0, 0]
    errors: list[BaseException] = []

    def worker(index: int, operation) -> None:
        with Session() as session:
            backend_pids[index] = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            backend_ready[index].set()
            try:
                operation(session)
                operation_complete[index].set()
                if not release_commit[index].wait(timeout=15):
                    raise TimeoutError("timed out waiting for deterministic commit release")
                session.commit()
            except BaseException as exc:
                session.rollback()
                errors.append(exc)
                operation_complete[index].set()

    first = Thread(target=worker, args=(0, first_operation), daemon=True)
    second = Thread(target=worker, args=(1, second_operation), daemon=True)
    first.start()
    assert operation_complete[0].wait(timeout=10)
    second.start()
    assert backend_ready[1].wait(timeout=10)
    waiter_observed = _wait_for_administration_lock_waiter(engine, backend_pids[1])
    release_commit[0].set()
    first.join(timeout=15)
    assert not first.is_alive()
    assert operation_complete[1].wait(timeout=15)
    release_commit[1].set()
    second.join(timeout=15)
    assert not second.is_alive()
    assert waiter_observed, "second mixed administration path did not wait on the canonical database lock"
    return errors


def _assert_platform_administration_continuity(engine: Engine, *, code: str) -> None:
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM organizations WHERE code=:code AND is_active=true"),
            {"code": code},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM platform_role_assignments platform_role "
                "JOIN users account ON account.id=platform_role.user_id "
                "JOIN organization_memberships membership ON membership.user_id=account.id "
                "JOIN organizations organization ON organization.id=membership.organization_id "
                "JOIN organization_role_assignments role ON role.membership_id=membership.id "
                "WHERE platform_role.role='platform_admin' AND platform_role.is_active=true "
                "AND account.is_active=true AND account.deleted_at IS NULL "
                "AND membership.status='active' AND organization.normalized_code='symgov' "
                "AND role.base_role='admin' AND role.is_active=true"
            )
        ).scalar_one() >= 1


@pytest.mark.parametrize("first_path", ("user_update", "platform_service"))
def test_user_update_and_platform_admin_service_use_one_canonical_lock(
    organization_database: Engine,
    first_path: str,
):
    actor_id = _ensure_protected_owner_platform_admin(organization_database)
    initial_admin_id = _insert_user(
        organization_database,
        f"mixed-user-platform-{first_path}@example.test",
    )
    code = f"MIXED-UP-{uuid.uuid4().hex[:8].upper()}"
    lock_target_id = _insert_user(
        organization_database,
        f"mixed-user-lock-target-{first_path}@example.test",
    )

    def user_update(session) -> None:
        session.execute(
            text("UPDATE users SET is_active=is_active WHERE id=:user_id"),
            {"user_id": lock_target_id},
        )

    def platform_service(session) -> None:
        create_organization_with_initial_admin(
            session,
            code=code,
            display_name=code,
            initial_admin_user_id=initial_admin_id,
            actor_user_id=actor_id,
        )

    operations = {"user_update": user_update, "platform_service": platform_service}
    second_path = "platform_service" if first_path == "user_update" else "user_update"
    errors = _run_mixed_administration_pair(
        organization_database,
        operations[first_path],
        operations[second_path],
    )
    assert not [error for error in errors if isinstance(error, DBAPIError)]
    assert errors == []
    _assert_platform_administration_continuity(organization_database, code=code)


def test_unrelated_user_update_does_not_take_or_wait_for_administration_lock(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, f"unrelated-lock-{uuid.uuid4().hex[:8]}@example.test")
    completed = Event()
    backend_ready = Event()
    backend_pid = [0]
    errors: list[BaseException] = []

    def update_unrelated_column() -> None:
        try:
            with organization_database.begin() as connection:
                backend_pid[0] = connection.execute(text("SELECT pg_backend_pid()" )).scalar_one()
                backend_ready.set()
                connection.execute(
                    text("UPDATE users SET updated_at=:now WHERE id=:user_id"),
                    {"now": datetime.now(timezone.utc), "user_id": user_id},
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    with organization_database.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:classid, :objid)"),
            {"classid": ADMINISTRATION_LOCK_KEY[0], "objid": ADMINISTRATION_LOCK_KEY[1]},
        )
        worker = Thread(target=update_unrelated_column, daemon=True)
        worker.start()
        assert backend_ready.wait(timeout=5)
        assert completed.wait(timeout=2), "updated_at-only write waited for the administration lock"
        assert holder.execute(
            text(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND pid=:pid AND classid=:classid AND objid=:objid) "
                "AND cardinality(pg_blocking_pids(:pid))=0"
            ),
            {"pid": backend_pid[0], "classid": ADMINISTRATION_LOCK_KEY[0], "objid": ADMINISTRATION_LOCK_KEY[1]},
        ).scalar_one() is True
        transaction.rollback()
    worker.join(timeout=5)
    assert errors == []


@pytest.mark.parametrize("first_path", ("bootstrap", "platform_service"))
def test_bootstrap_and_platform_administration_use_one_canonical_lock(
    organization_database: Engine,
    first_path: str,
):
    actor_id = _ensure_protected_owner_platform_admin(organization_database)
    initial_admin_id = _insert_user(
        organization_database,
        f"mixed-bootstrap-platform-{first_path}@example.test",
    )
    code = f"MIXED-BP-{uuid.uuid4().hex[:8].upper()}"

    def bootstrap(session) -> None:
        reconcile_symgov_organization_bootstrap(session, apply=True)

    def platform_service(session) -> None:
        create_organization_with_initial_admin(
            session,
            code=code,
            display_name=code,
            initial_admin_user_id=initial_admin_id,
            actor_user_id=actor_id,
        )

    operations = {"bootstrap": bootstrap, "platform_service": platform_service}
    second_path = "platform_service" if first_path == "bootstrap" else "bootstrap"
    errors = _run_mixed_administration_pair(
        organization_database,
        operations[first_path],
        operations[second_path],
    )
    assert not [error for error in errors if isinstance(error, DBAPIError)]
    assert errors == []
    _assert_platform_administration_continuity(organization_database, code=code)


@pytest.mark.parametrize("first_path", ("symgov_membership", "platform_service"))
def test_symgov_membership_and_platform_admin_service_use_one_canonical_lock(
    organization_database: Engine,
    first_path: str,
):
    actor_id = _ensure_protected_owner_platform_admin(organization_database)
    member_id, membership_id = _insert_symgov_admin(
        organization_database,
        email=f"mixed-membership-platform-{first_path}@example.test",
    )
    initial_admin_id = _insert_user(
        organization_database,
        f"mixed-membership-target-{first_path}@example.test",
    )
    code = f"MIXED-MP-{uuid.uuid4().hex[:8].upper()}"

    def symgov_membership(session) -> None:
        replace_protected_membership_base_role(
            session,
            membership_id=membership_id,
            new_base_role="user",
            actor_user_id=actor_id,
            reason="Verify canonical administration locking",
        )

    def platform_service(session) -> None:
        create_organization_with_initial_admin(
            session,
            code=code,
            display_name=code,
            initial_admin_user_id=initial_admin_id,
            actor_user_id=actor_id,
        )

    operations = {"symgov_membership": symgov_membership, "platform_service": platform_service}
    second_path = "platform_service" if first_path == "symgov_membership" else "symgov_membership"
    errors = _run_mixed_administration_pair(
        organization_database,
        operations[first_path],
        operations[second_path],
    )
    assert not [error for error in errors if isinstance(error, DBAPIError)]
    assert errors == []
    _assert_platform_administration_continuity(organization_database, code=code)
    with organization_database.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM organization_memberships membership "
                "JOIN organization_role_assignments role ON role.membership_id=membership.id "
                "WHERE membership.user_id=:user_id AND membership.status='active' "
                "AND role.base_role='user' AND role.is_active=true"
            ),
            {"user_id": member_id},
        ).scalar_one() == 1


@pytest.mark.parametrize("authority_mutation", ("demote", "deactivate"))
@pytest.mark.parametrize("first_path", ("authority", "privileged"))
def test_privileged_mutation_revalidates_concurrently_changed_admin_authority(
    organization_database: Engine,
    authority_mutation: str,
    first_path: str,
):
    suffix = uuid.uuid4().hex[:8]
    organization_id = _insert_organization(
        organization_database,
        code=f"RACE-{suffix.upper()}",
        normalized_code=f"race-{suffix}",
    )
    actor_id = _insert_user(organization_database, f"race-actor-{suffix}@example.test")
    authority_id = _insert_user(organization_database, f"race-authority-{suffix}@example.test")
    target_id = _insert_user(organization_database, f"race-target-{suffix}@example.test")
    actor_membership_id = _insert_membership_with_role(
        organization_database, organization_id=organization_id, user_id=actor_id,
    )
    _insert_membership_with_role(
        organization_database, organization_id=organization_id, user_id=authority_id,
    )

    def change_authority(session) -> None:
        if authority_mutation == "demote":
            replace_membership_base_role(
                session,
                membership_id=actor_membership_id,
                new_base_role="user",
                actor_user_id=authority_id,
            )
        else:
            deactivate_membership(
                session,
                membership_id=actor_membership_id,
                actor_user_id=authority_id,
                reason="Concurrent administrator deactivation",
            )

    def privileged_mutation(session) -> None:
        add_organization_member(
            session,
            organization_id,
            user_id=target_id,
            base_role="user",
            actor_user_id=actor_id,
        )

    operations = {"authority": change_authority, "privileged": privileged_mutation}
    second_path = "privileged" if first_path == "authority" else "authority"
    errors = _run_mixed_administration_pair(
        organization_database,
        operations[first_path],
        operations[second_path],
    )

    with organization_database.connect() as connection:
        target_memberships = connection.execute(
            text(
                "SELECT count(*) FROM organization_memberships "
                "WHERE organization_id=:organization_id AND user_id=:target_id AND status='active'"
            ),
            {"organization_id": organization_id, "target_id": target_id},
        ).scalar_one()
    if first_path == "authority":
        assert target_memberships == 0
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert "active administrator" in str(errors[0]).lower()
    else:
        assert target_memberships == 1
        assert errors == []


def test_challenge_hashes_and_attempt_bounds_are_database_enforced(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "challenge@example.test")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    values = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_hash": "not-a-hash",
        "eligible_hash": "0" * 64,
        "eligible_json": "[]",
        "now": now,
        "expires_at": now + timedelta(minutes=10),
    }
    with pytest.raises(IntegrityError, match="ck_auth_organization_selection_challenges_token_hash"):
        with organization_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO auth_organization_selection_challenges "
                    "(id,user_id,token_hash,eligible_organizations_hash,eligible_organizations_json,expires_at,created_at,updated_at) "
                    "VALUES (:id,:user_id,:token_hash,:eligible_hash,:eligible_json,:expires_at,:now,:now)"
                ),
                values,
            )


def _insert_selection_challenge(
    engine: Engine,
    *,
    user_id: uuid.UUID,
    organization_ids: list[uuid.UUID],
    attempt_count: int = 0,
) -> tuple[uuid.UUID, str]:
    challenge_id = uuid.uuid4()
    token_hash = hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = created_at + timedelta(minutes=10)
    snapshot = [
        {
            "organizationId": str(organization_id),
            "code": f"ORG-{index}",
            "displayName": f"ORG-{index} Organization",
        }
        for index, organization_id in enumerate(organization_ids)
    ]
    serialized = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO auth_organization_selection_challenges "
                "(id,user_id,token_hash,eligible_organizations_hash,eligible_organizations_json,"
                "expires_at,max_attempts,attempt_count,created_at,updated_at) "
                "VALUES (:id,:user_id,:token_hash,:eligible_hash,:eligible_json,:expires_at,5,:attempt_count,:created_at,:created_at)"
            ),
            {
                "id": challenge_id,
                "user_id": user_id,
                "token_hash": token_hash,
                "eligible_hash": hashlib.sha256(serialized.encode()).hexdigest(),
                "eligible_json": serialized,
                "expires_at": expires_at,
                "attempt_count": attempt_count,
                "created_at": created_at,
            },
        )
    return challenge_id, token_hash


def test_selection_challenge_concurrent_consume_has_exactly_one_winner(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, f"selection-consume-{uuid.uuid4().hex[:8]}@example.test")
    code_suffix = uuid.uuid4().hex[:8]
    organization_id = _insert_organization(
        organization_database,
        code=f"SEL-{code_suffix.upper()}",
        normalized_code=f"sel-{code_suffix}",
    )
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=user_id,
    )
    challenge_id, token_hash = _insert_selection_challenge(
        organization_database,
        user_id=user_id,
        organization_ids=[organization_id],
    )

    Session = sessionmaker(bind=organization_database, autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def consume_once() -> None:
        try:
            with Session() as session:
                barrier.wait(timeout=5)
                now = datetime.now(timezone.utc).replace(microsecond=0)
                user_row = session.execute(
                    text(
                        "SELECT id FROM users WHERE id=:user_id "
                        "AND is_active=true AND deleted_at IS NULL FOR UPDATE"
                    ),
                    {"user_id": user_id},
                ).one_or_none()
                challenge_row = session.execute(
                    text(
                        "SELECT id,attempt_count,max_attempts,consumed_at,revoked_at,expires_at "
                        "FROM auth_organization_selection_challenges "
                        "WHERE id=:challenge_id AND token_hash=:token_hash FOR UPDATE"
                    ),
                    {"challenge_id": challenge_id, "token_hash": token_hash},
                ).one_or_none()
                if user_row is None or challenge_row is None:
                    session.rollback()
                    results.append("rejected")
                    return
                if (
                    challenge_row.consumed_at is not None
                    or challenge_row.revoked_at is not None
                    or challenge_row.attempt_count >= challenge_row.max_attempts
                    or challenge_row.expires_at <= now
                ):
                    session.rollback()
                    results.append("rejected")
                    return
                session.execute(
                    text(
                        "UPDATE auth_organization_selection_challenges "
                        "SET consumed_at=:now,updated_at=:now WHERE id=:challenge_id"
                    ),
                    {"now": now, "challenge_id": challenge_id},
                )
                session.execute(
                    text(
                        "INSERT INTO user_sessions "
                        "(id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose,session_mode,active_organization_id) "
                        "VALUES (:id,:user_id,:token_hash,:now,:expires_at,NULL,:now,'application','organization',:organization_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "user_id": user_id,
                        "token_hash": hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                        "now": now,
                        "expires_at": now + timedelta(hours=1),
                        "organization_id": organization_id,
                    },
                )
                session.commit()
                results.append("success")
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=consume_once, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(results) == ["rejected", "success"]
    with organization_database.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM user_sessions WHERE auth_user_id=:user_id"),
            {"user_id": user_id},
        ).scalar_one() == 1
        consumed_at = connection.execute(
            text("SELECT consumed_at FROM auth_organization_selection_challenges WHERE id=:id"),
            {"id": challenge_id},
        ).scalar_one()
        assert consumed_at is not None


def test_selection_challenge_fourth_and_fifth_invalid_attempts_are_atomic(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, f"selection-attempts-{uuid.uuid4().hex[:8]}@example.test")
    code_suffix = uuid.uuid4().hex[:8]
    organization_id = _insert_organization(
        organization_database,
        code=f"ATT-{code_suffix.upper()}",
        normalized_code=f"att-{code_suffix}",
    )
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=user_id,
    )
    challenge_id, token_hash = _insert_selection_challenge(
        organization_database,
        user_id=user_id,
        organization_ids=[organization_id],
        attempt_count=3,
    )

    Session = sessionmaker(bind=organization_database, autoflush=False, expire_on_commit=False)
    barrier = Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def invalid_attempt_once() -> None:
        try:
            with Session() as session:
                barrier.wait(timeout=5)
                now = datetime.now(timezone.utc).replace(microsecond=0)
                session.execute(
                    text("SELECT id FROM users WHERE id=:user_id FOR UPDATE"),
                    {"user_id": user_id},
                ).one()
                challenge_row = session.execute(
                    text(
                        "SELECT id,attempt_count,max_attempts,consumed_at,revoked_at,expires_at "
                        "FROM auth_organization_selection_challenges "
                        "WHERE id=:challenge_id AND token_hash=:token_hash FOR UPDATE"
                    ),
                    {"challenge_id": challenge_id, "token_hash": token_hash},
                ).one()
                if (
                    challenge_row.consumed_at is not None
                    or challenge_row.revoked_at is not None
                    or challenge_row.attempt_count >= challenge_row.max_attempts
                    or challenge_row.expires_at <= now
                ):
                    session.rollback()
                    outcomes.append("denied")
                    return
                new_attempt_count = challenge_row.attempt_count + 1
                revoked_at = now if new_attempt_count >= challenge_row.max_attempts else None
                session.execute(
                    text(
                        "UPDATE auth_organization_selection_challenges "
                        "SET attempt_count=:attempt_count,revoked_at=:revoked_at,updated_at=:now "
                        "WHERE id=:challenge_id"
                    ),
                    {
                        "attempt_count": new_attempt_count,
                        "revoked_at": revoked_at,
                        "now": now,
                        "challenge_id": challenge_id,
                    },
                )
                session.commit()
                outcomes.append("exhausted" if new_attempt_count >= challenge_row.max_attempts else "retryable")
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=invalid_attempt_once, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(outcomes) == ["exhausted", "retryable"]
    with organization_database.connect() as connection:
        row = connection.execute(
            text(
                "SELECT attempt_count,revoked_at,consumed_at "
                "FROM auth_organization_selection_challenges WHERE id=:id"
            ),
            {"id": challenge_id},
        ).one()
        assert row.attempt_count == 5
        assert row.revoked_at is not None
        assert row.consumed_at is None
        assert connection.execute(
            text("SELECT count(*) FROM user_sessions WHERE auth_user_id=:user_id"),
            {"user_id": user_id},
        ).scalar_one() == 0


def test_role_assignment_history_cannot_be_rewritten_or_deleted(
    organization_database: Engine,
):
    user_id = _insert_user(organization_database, "role-history@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="HISTORY-ORG",
        normalized_code="history-org",
    )
    membership_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,activated_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'active',:now,:now,:now)"
            ),
            {"id": membership_id, "organization_id": organization_id, "user_id": user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'admin',true,:now)"
            ),
            {"id": assignment_id, "membership_id": membership_id, "now": now},
        )

    with pytest.raises(DBAPIError, match="assignment identity is immutable"):
        with organization_database.begin() as connection:
            connection.execute(
                text("UPDATE organization_role_assignments SET base_role='user' WHERE id=:id"),
                {"id": assignment_id},
            )
    with pytest.raises(DBAPIError, match="assignment history cannot be deleted"):
        with organization_database.begin() as connection:
            connection.execute(
                text("DELETE FROM organization_role_assignments WHERE id=:id"),
                {"id": assignment_id},
            )


def test_membership_identity_and_lifecycle_history_are_preserved(
    organization_database: Engine,
):
    member_id = _insert_user(organization_database, "membership-history@example.test")
    alternate_user_id = _insert_user(organization_database, "membership-history-alternate@example.test")
    organization_id = _insert_organization(
        organization_database,
        code="MEMBERSHIP-HISTORY",
        normalized_code="membership-history",
    )
    alternate_organization_id = _insert_organization(
        organization_database,
        code="MEMBERSHIP-HISTORY-ALT",
        normalized_code="membership-history-alt",
    )
    _insert_membership_with_role(
        organization_database,
        organization_id=organization_id,
        user_id=alternate_user_id,
    )
    membership_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    invited_at = created_at - timedelta(minutes=1)

    with organization_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_memberships "
                "(id,organization_id,user_id,status,invited_at,created_at,updated_at) "
                "VALUES (:id,:organization_id,:user_id,'invited',:invited_at,:created_at,:created_at)"
            ),
            {
                "id": membership_id,
                "organization_id": organization_id,
                "user_id": member_id,
                "invited_at": invited_at,
                "created_at": created_at,
            },
        )

    def membership_row():
        with organization_database.connect() as connection:
            return connection.execute(
                text(
                    "SELECT id,organization_id,user_id,status,invited_at,activated_at,"
                    "deactivated_at,created_at,updated_at "
                    "FROM organization_memberships WHERE id=:id"
                ),
                {"id": membership_id},
            ).one()

    def assert_update_rejected(set_clause: str, parameters: Mapping[str, object], message: str) -> None:
        before = membership_row()
        with organization_database.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(DBAPIError, match=message):
                    connection.execute(
                        text(f"UPDATE organization_memberships SET {set_clause} WHERE id=:membership_id"),
                        {"membership_id": membership_id, **parameters},
                    )
            finally:
                transaction.rollback()
        assert membership_row() == before

    for set_clause, parameters in (
        ("id=:value", {"value": uuid.uuid4()}),
        ("organization_id=:value", {"value": alternate_organization_id}),
        ("user_id=:value", {"value": alternate_user_id}),
        ("created_at=:value", {"value": created_at - timedelta(days=1)}),
    ):
        assert_update_rejected(set_clause, parameters, "membership identity is immutable")

    activated_at = created_at + timedelta(minutes=1)
    with organization_database.begin() as connection:
        connection.execute(
            text(
                "UPDATE organization_memberships "
                "SET status='active',activated_at=:activated_at,updated_at=:activated_at "
                "WHERE id=:membership_id"
            ),
            {"membership_id": membership_id, "activated_at": activated_at},
        )
        connection.execute(
            text(
                "INSERT INTO organization_role_assignments "
                "(id,membership_id,base_role,is_active,assigned_at) "
                "VALUES (:id,:membership_id,'user',true,:activated_at)"
            ),
            {"id": uuid.uuid4(), "membership_id": membership_id, "activated_at": activated_at},
        )

    Session = sessionmaker(bind=organization_database, autoflush=False, expire_on_commit=False)
    with Session() as session:
        deactivate_membership(
            session,
            membership_id=membership_id,
            actor_user_id=alternate_user_id,
        )
        session.commit()

    inactive_row = membership_row()
    assert inactive_row.status == "inactive"
    assert inactive_row.invited_at == invited_at
    assert inactive_row.activated_at == activated_at
    assert inactive_row.deactivated_at is not None

    for column_name in ("invited_at", "activated_at", "deactivated_at"):
        assert_update_rejected(
            f"{column_name}=:value",
            {"value": inactive_row.deactivated_at + timedelta(minutes=1)},
            "membership lifecycle timestamps are append-only",
        )

    before_delete = membership_row()
    with organization_database.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError, match="membership history cannot be deleted"):
                connection.execute(
                    text("DELETE FROM organization_memberships WHERE id=:id"),
                    {"id": membership_id},
                )
        finally:
            transaction.rollback()
    assert membership_row() == before_delete

    with organization_database.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": member_id})
        finally:
            transaction.rollback()
    assert membership_row() == before_delete


def test_zz_downgrade_and_reupgrade_preserve_legacy_session_data(
    organization_database: Engine,
):
    assert LEGACY_SESSION_ID is not None
    url = organization_database.url.render_as_string(hide_password=False)
    organization_database.dispose()

    _alembic(url, "downgrade", "20260808_0027")
    downgraded = create_engine(url)
    with downgraded.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='user_sessions'"
                )
            ).scalars()
        )
        assert "session_mode" not in columns
        assert connection.execute(
            text("SELECT to_regprocedure('validate_user_session_organization_membership()')")
        ).scalar_one() is None
        assert connection.execute(
            text("SELECT to_regprocedure('enforce_platform_admin_eligibility()')")
        ).scalar_one() is None
        assert connection.execute(text("SELECT to_regclass('organizations')")).scalar_one() is None
        assert connection.execute(
            text("SELECT count(*) FROM user_sessions WHERE id=:id"),
            {"id": LEGACY_SESSION_ID},
        ).scalar_one() == 1
    downgraded.dispose()

    _alembic(url, "upgrade", "20260810_0028")
    reupgraded = create_engine(url)
    with reupgraded.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260810_0028"
        assert connection.execute(text("SELECT to_regclass('organizations')")).scalar_one() == "organizations"
        assert connection.execute(
            text("SELECT to_regprocedure('enforce_platform_admin_eligibility()')")
        ).scalar_one() == "enforce_platform_admin_eligibility()"
        assert connection.execute(
            text("SELECT session_mode FROM user_sessions WHERE id=:id"),
            {"id": LEGACY_SESSION_ID},
        ).scalar_one() == "personal"
    reupgraded.dispose()
