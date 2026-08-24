from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from symgov_backend.public_symbol_eligibility import current_public_symbols
from symgov_backend.auth import hash_session_token
from symgov_backend.models import AuditEvent, Project, ProjectSymbolSet, UserProjectSetSelection
from symgov_backend.project_service import patch_project
from symgov_backend.stage4_authorization import require_stage4_principal
from symgov_backend.symbol_set_service import clear_organization_default, patch_set, replace_projects, _lock_organization_default_anchors

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=120)


def _alembic(url: str, *args: str) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND), "SYMGOV_DATABASE_URL": url, "SYMGOV_MIGRATION_DATABASE_URL": url}
    subprocess.run(["alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="module")
def wp1_database():
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the disposable PostgreSQL migration rehearsal")
    name = f"symgov-wp1-{uuid.uuid4().hex[:12]}"
    password = "disposable-wp1-password"
    _docker("run", "--rm", "--detach", "--name", name, "--env", f"POSTGRES_PASSWORD={password}", "--env", "POSTGRES_DB=symgov_wp1", "--publish", "127.0.0.1::5432", "postgres:16-alpine")
    engine = None
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/symgov_wp1"
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
        _alembic(url, "upgrade", "20260821_0029")
        _alembic(url, "upgrade", "20260822_0030")
        engine = create_engine(url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "--force", name, check=False)


def _user(connection, email: str) -> uuid.UUID:
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(text("INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at) VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"), {"id": identifier, "email": email, "now": now})
    return identifier


def _organization(connection, code: str, user_id: uuid.UUID) -> uuid.UUID:
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(text("INSERT INTO organizations (id,code,normalized_code,display_name,name_key,is_active,fallback_icon_svg,created_at,updated_at) VALUES (:id,:code,:normalized,:code,:normalized,false,'<svg/>',:now,:now)"), {"id": identifier, "code": code, "normalized": code.lower(), "now": now})
    return identifier


def _admin(connection, organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    membership = uuid.uuid4()
    connection.execute(text("INSERT INTO organization_memberships (id,organization_id,user_id,status,activated_at,created_at,updated_at) VALUES (:id,:organization,:user,'active',:now,:now,:now)"), {"id": membership, "organization": organization_id, "user": user_id, "now": now})
    connection.execute(text("INSERT INTO organization_role_assignments (id,membership_id,base_role,is_active,assigned_at) VALUES (:id,:membership,'admin',true,:now)"), {"id": uuid.uuid4(), "membership": membership, "now": now})
    connection.execute(text("UPDATE organizations SET is_active=true WHERE id=:id"), {"id": organization_id})


def test_wp1_upgrade_is_real_0029_to_0030_rehearsal(wp1_database):
    names = set(inspect(wp1_database).get_table_names())
    assert {"projects", "symbol_sets", "project_symbol_sets", "symbol_set_items", "user_project_set_selections", "user_session_project_contexts"} <= names
    with wp1_database.connect() as connection:
        triggers = {row[0] for row in connection.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
        assert {
            "trg_symbol_set_copy_owner", "trg_project_symbol_sets_owner", "trg_symbol_sets_dependents_valid",
            "trg_projects_dependents_valid", "trg_user_project_set_selection_valid",
            "trg_user_session_project_context_valid", "trg_organization_symbol_set_default_valid",
            "trg_projects_identity", "trg_symbol_sets_identity", "trg_symbol_set_items_governed_symbol_lock",
            "trg_user_sessions_project_context_cleanup",
        } <= triggers
        functions = {row[0] for row in connection.execute(text("SELECT proname FROM pg_proc WHERE proname IN ('stage4_jsonb_max_depth','stage4_string_array_bounds','validate_symbol_set_copy_owner','validate_project_symbol_set_owner','validate_symbol_set_dependents','validate_project_dependents','validate_user_project_set_selection','validate_user_session_project_context','validate_organization_symbol_set_default','protect_project_identity','protect_symbol_set_identity','lock_governed_symbols_deterministically','lock_governed_symbol_boundary','cleanup_user_session_project_context')"))}
        assert functions == {"stage4_jsonb_max_depth", "stage4_string_array_bounds", "validate_symbol_set_copy_owner", "validate_project_symbol_set_owner", "validate_symbol_set_dependents", "validate_project_dependents", "validate_user_project_set_selection", "validate_user_session_project_context", "validate_organization_symbol_set_default", "protect_project_identity", "protect_symbol_set_identity", "lock_governed_symbols_deterministically", "lock_governed_symbol_boundary", "cleanup_user_session_project_context"}
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260822_0030"


def test_public_eligibility_rejects_revision_owned_by_a_different_symbol(wp1_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    requested, other, revision = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pack, page = uuid.uuid4(), uuid.uuid4()
    with wp1_database.begin() as connection:
        owner = _user(connection, f"eligibility-{uuid.uuid4()}@example.test")
        for symbol_id, slug in ((requested, "eligibility-requested"), (other, "eligibility-other")):
            connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now)"), {"id": symbol_id, "slug": slug, "owner": owner, "now": now})
        connection.execute(text("INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at) VALUES (:id,:symbol,'1','published','{}'::jsonb,:owner,:now)"), {"id": revision, "symbol": other, "owner": owner, "now": now})
        connection.execute(text("UPDATE governed_symbols SET current_revision_id=:revision WHERE id=:symbol"), {"revision": revision, "symbol": requested})
        connection.execute(text("INSERT INTO publication_packs (id,pack_code,title,audience,effective_date,status,created_at,updated_at) VALUES (:id,:code,'Eligibility','public',CURRENT_DATE,'published',:now,:now)"), {"id": pack, "code": f"ELIG-{uuid.uuid4().hex}", "now": now})
        connection.execute(text("INSERT INTO published_pages (id,page_code,title,pack_id,current_symbol_revision_id,effective_date,created_at,updated_at) VALUES (:id,:code,'Eligibility',:pack,:revision,CURRENT_DATE,:now,:now)"), {"id": page, "code": f"ELIG-PAGE-{uuid.uuid4().hex}", "pack": pack, "revision": revision, "now": now})
        connection.execute(text("INSERT INTO pack_entries (id,pack_id,symbol_revision_id,published_page_id,sort_order,created_at) VALUES (:id,:pack,:revision,:page,1,:now)"), {"id": uuid.uuid4(), "pack": pack, "revision": revision, "page": page, "now": now})
        assert current_public_symbols(connection, [requested]) == {}
        connection.execute(text("UPDATE symbol_revisions SET symbol_id=:symbol WHERE id=:revision"), {"symbol": requested, "revision": revision})
        assert current_public_symbols(connection, [requested]) == {requested: revision}


def test_wp1_copy_owner_and_self_checks_are_commit_time_safe(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"copy-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"A{uuid.uuid4().hex[:7].upper()}", user)
        other = _organization(connection, f"B{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        _admin(connection, other, user)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        parent = uuid.uuid4()
        connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,status,created_by_user_id,created_at,updated_at) VALUES (:id,:owner,'PARENT','parent','Parent','active',:user,:now,:now)"), {"id": parent, "owner": owner, "user": user, "now": now})
    self_id = uuid.uuid4()
    with pytest.raises(DBAPIError, match="copy_not_self"), wp1_database.begin() as connection:
        connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,copied_from_symbol_set_id,created_by_user_id,created_at,updated_at) VALUES (:id,:owner,'SELF','self', 'Self',:id,:user,:now,:now)"), {"id": self_id, "owner": owner, "user": user, "now": now})
    child = uuid.uuid4()
    with pytest.raises(DBAPIError, match="copied-from"), wp1_database.begin() as connection:
        connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,copied_from_symbol_set_id,created_by_user_id,created_at,updated_at) VALUES (:id,:owner,'CHILD','child','Child',:parent,:user,:now,:now)"), {"id": child, "owner": other, "parent": parent, "user": user, "now": now})
    with wp1_database.begin() as connection:
        valid_child = uuid.uuid4()
        connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,copied_from_symbol_set_id,created_by_user_id,created_at,updated_at) VALUES (:id,:owner,'VALIDCHILD','validchild','Valid Child',:parent,:user,:now,:now)"), {"id": valid_child, "owner": owner, "parent": parent, "user": user, "now": now})
        assert connection.execute(text("SELECT copied_from_symbol_set_id FROM symbol_sets WHERE id=:id"), {"id": valid_child}).scalar_one() == parent


def test_wp1_grants_and_guarded_downgrade_are_least_privilege(wp1_database):
    with wp1_database.connect() as connection:
        rows = connection.execute(text("SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='symgov_app' AND table_name IN ('projects','symbol_sets','project_symbol_sets','symbol_set_items','user_project_set_selections','user_session_project_contexts')")).all()
        privileges = {(row[0], row[1]) for row in rows}
        assert ("project_symbol_sets", "DELETE") in privileges
        assert ("symbol_set_items", "DELETE") in privileges
        assert ("projects", "DELETE") not in privileges
        assert ("symbol_sets", "DELETE") not in privileges
        connection.execute(text("SELECT lock_governed_symbols_deterministically(ARRAY[]::uuid[])"))


def test_wp1_security_definer_helpers_are_not_publicly_executable(wp1_database):
    role = f"wp1_probe_{uuid.uuid4().hex[:12]}"
    url = wp1_database.url.render_as_string(hide_password=False).replace("+psycopg", "")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f'CREATE ROLE "{role}"')
        try:
            connection.execute(f'SET ROLE "{role}"')
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
                connection.execute("SELECT lock_governed_symbols_deterministically(ARRAY[]::uuid[])")
            assert caught.value.sqlstate == "42501"
        finally:
            connection.execute("RESET ROLE")
            connection.execute(f'DROP ROLE "{role}"')


def _project(connection, organization_id, user_id, code="PRJ"):
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(text("""
        INSERT INTO projects
        (id, organization_id, code, normalized_code, name, metadata_json,
         created_by_user_id, created_at, updated_at)
        VALUES (:id, :organization, :code, :normalized, :code, '{}'::jsonb,
                :user, :now, :now)
    """), {"id": identifier, "organization": organization_id, "code": code,
           "normalized": code.lower(), "user": user_id, "now": now})
    return identifier


def _symbol_set(connection, organization_id, user_id, code="SET", status="active"):
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(text("""
        INSERT INTO symbol_sets
        (id, owner_organization_id, code, normalized_code, name, status,
         created_by_user_id, created_at, updated_at)
        VALUES (:id, :organization, :code, :normalized, :code, :status,
                :user, :now, :now)
    """), {"id": identifier, "organization": organization_id, "code": code,
           "normalized": code.lower(), "status": status, "user": user_id, "now": now})
    return identifier


def test_wp1_frozen_scalar_and_array_bounds_are_database_enforced(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"bounds-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"B{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for label, value in (("BLANK", "   "), ("LONG", "x" * 201)):
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO projects (id,organization_id,code,normalized_code,name,metadata_json,created_by_user_id,created_at,updated_at) VALUES (:id,:o,:code,:normalized,:name,'{}'::jsonb,:u,:n,:n)"), {"id": uuid.uuid4(), "o": owner, "code": f"P{label}", "normalized": f"p{label.lower()}", "name": value, "u": user, "n": now})
            assert getattr(caught.value.orig, "sqlstate", None) == "23514"
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(text("INSERT INTO projects (id,organization_id,code,normalized_code,name,external_reference,metadata_json,created_by_user_id,created_at,updated_at) VALUES (:id,:o,'PREF','pref','Project',:ref,'{}'::jsonb,:u,:n,:n)"), {"id": uuid.uuid4(), "o": owner, "ref": "x" * 201, "u": user, "n": now})
        cases = (
            ("BLANKSET", "   ", [], []),
            ("LONGSET", "x" * 201, [], []),
            ("TOOMANY", "Valid", ["x"] * 33, []),
            ("NONSTRING", "Valid", [1], []),
            ("EMPTYITEM", "Valid", ["   "], []),
            ("LONGITEM", "Valid", ["x" * 101], []),
            ("TOOMANYUSE", "Valid", [], ["x"] * 33),
            ("NONSTRINGUSE", "Valid", [], [1]),
            ("EMPTYUSE", "Valid", [], ["   "]),
            ("LONGUSE", "Valid", [], ["x" * 101]),
        )
        for code, name, disciplines, use_cases in cases:
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,disciplines_json,use_cases_json,created_by_user_id,created_at,updated_at) VALUES (:id,:o,:code,:normalized,:name,CAST(:disciplines AS jsonb),CAST(:use_cases AS jsonb),:u,:n,:n)"), {"id": uuid.uuid4(), "o": owner, "code": code, "normalized": code.lower(), "name": name, "disciplines": json.dumps(disciplines), "use_cases": json.dumps(use_cases), "u": user, "n": now})
            assert getattr(caught.value.orig, "sqlstate", None) == "23514"
        valid = uuid.uuid4()
        connection.execute(text("INSERT INTO symbol_sets (id,owner_organization_id,code,normalized_code,name,disciplines_json,use_cases_json,created_by_user_id,created_at,updated_at) VALUES (:id,:o,'VALIDBOUND','validbound','Valid',CAST(:disciplines AS jsonb),CAST(:use_cases AS jsonb),:u,:n,:n)"), {"id": valid, "o": owner, "disciplines": json.dumps(["x" * 100] * 32), "use_cases": json.dumps(["x" * 100] * 32), "u": user, "n": now})
        assert connection.execute(text("SELECT jsonb_array_length(disciplines_json) FROM symbol_sets WHERE id=:id"), {"id": valid}).scalar_one() == 32
        assert connection.execute(text("SELECT jsonb_array_length(use_cases_json) FROM symbol_sets WHERE id=:id"), {"id": valid}).scalar_one() == 32

        governed = uuid.uuid4()
        connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,'Bounds Symbol','test','test',:owner,:now,:now)"), {"id": governed, "slug": f"bounds-{uuid.uuid4()}", "owner": user, "now": now})
        item_bounds = (
            ("group_name", "x" * 201),
            ("display_label", "x" * 201),
            ("preferred_format", "x" * 201),
            ("notes", "x" * 2001),
            ("availability_reason", "x" * 501),
        )
        for column, value in item_bounds:
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text(f"INSERT INTO symbol_set_items (id,symbol_set_id,governed_symbol_id,sort_order,{column},created_at,updated_at) VALUES (:id,:set_id,:symbol,0,:value,:now,:now)"), {"id": uuid.uuid4(), "set_id": valid, "symbol": governed, "value": value, "now": now})
            assert getattr(caught.value.orig, "sqlstate", None) == "23514"
        connection.execute(text("INSERT INTO symbol_set_items (id,symbol_set_id,governed_symbol_id,sort_order,group_name,display_label,preferred_format,notes,availability_status,availability_reason,created_at,updated_at) VALUES (:id,:set_id,:symbol,0,:short,:short,:short,:notes,'unavailable',:reason,:now,:now)"), {"id": uuid.uuid4(), "set_id": valid, "symbol": governed, "short": "x" * 200, "notes": "x" * 2000, "reason": "x" * 500, "now": now})


def _availability(connection, project_id, symbol_set_id, user_id, *, status="active", default=False):
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    connection.execute(text("""
        INSERT INTO project_symbol_sets
        (id, project_id, symbol_set_id, status, is_default, created_by_user_id,
         created_at, updated_at)
        VALUES (:id, :project, :set_id, :status, :default, :user, :now, :now)
    """), {"id": identifier, "project": project_id, "set_id": symbol_set_id,
           "status": status, "default": default, "user": user_id, "now": now})
    return identifier




def test_wp1_availability_is_deferred_and_enforces_owner_and_activity(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"availability-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"A{uuid.uuid4().hex[:7].upper()}", user)
        other = _organization(connection, f"B{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        _admin(connection, other, user)
        project = _project(connection, owner, user)
        symbol_set = _symbol_set(connection, owner, user)
        _availability(connection, project, symbol_set, user)

    with pytest.raises(DBAPIError, match="owner"):
        with wp1_database.begin() as connection:
            foreign_set = _symbol_set(connection, other, user, code="FOREIGN")
            _availability(connection, project, foreign_set, user)
    with pytest.raises(DBAPIError, match="active"):
        with wp1_database.begin() as connection:
            closed_project = _project(connection, owner, user, "CLOSED")
            connection.execute(text("UPDATE projects SET status='closed', closed_at=now() WHERE id=:id"), {"id": closed_project})
            active_set = _symbol_set(connection, owner, user, code="CLOSEDSET")
            _availability(connection, closed_project, active_set, user)
    with pytest.raises(DBAPIError, match="active"):
        with wp1_database.begin() as connection:
            active_project = _project(connection, owner, user, "DRAFTSET")
            draft_set = _symbol_set(connection, owner, user, code="DRAFTSET2", status="draft")
            _availability(connection, active_project, draft_set, user)


def test_wp1_default_cardinality_is_enforced_by_unique_partial_index(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"default-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"D{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "DEF")
        first = _symbol_set(connection, owner, user, "FIRST")
        second = _symbol_set(connection, owner, user, "SECOND")
        _availability(connection, project, first, user, default=True)
        availability = _availability(connection, project, second, user)
        # The partial unique index is exercised in a savepoint so the outer
        # transaction remains usable for the durable cardinality assertion.
        with pytest.raises(DBAPIError) as caught:
            with connection.begin_nested():
                connection.execute(text("UPDATE project_symbol_sets SET is_default=true WHERE id=:id"), {"id": availability})
        error = caught
        assert "duplicate" in str(error.value).lower() or "unique" in str(error.value).lower()
        assert connection.execute(text("SELECT count(*) FROM project_symbol_sets WHERE project_id=:id AND status='active' AND is_default"), {"id": project}).scalar_one() == 1


def test_wp1_selection_and_organization_default_require_active_same_owner_rows(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"selection-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"S{uuid.uuid4().hex[:7].upper()}", user)
        other = _organization(connection, f"T{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        _admin(connection, other, user)
        project = _project(connection, owner, user, "SEL")
        symbol_set = _symbol_set(connection, owner, user, "VALID")
        _availability(connection, project, symbol_set, user)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:u,:p,:s,:n,:n)"), {"u": user, "p": project, "s": symbol_set, "n": now})
        connection.execute(text("UPDATE organizations SET default_symbol_set_id=:s WHERE id=:o"), {"s": symbol_set, "o": owner})
        assert connection.execute(text("SELECT active_symbol_set_id FROM user_project_set_selections WHERE user_id=:u AND project_id=:p"), {"u": user, "p": project}).scalar_one() == symbol_set
    with pytest.raises(DBAPIError, match="active"):
        with wp1_database.begin() as connection:
            draft = _symbol_set(connection, owner, user, "DRAFT", status="draft")
            connection.execute(text("UPDATE organizations SET default_symbol_set_id=:s WHERE id=:o"), {"s": draft, "o": owner})
    with pytest.raises(DBAPIError, match="same-owner|active"):
        with wp1_database.begin() as connection:
            foreign = _symbol_set(connection, other, user, "FOREIGN2")
            connection.execute(text("UPDATE organizations SET default_symbol_set_id=:s WHERE id=:o"), {"s": foreign, "o": owner})
    with pytest.raises(DBAPIError, match="active project availability"):
        with wp1_database.begin() as connection:
            invalid_user = _user(connection, f"selection-missing-{uuid.uuid4()}@example.test")
            missing = _symbol_set(connection, owner, user, "MISSING")
            connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:u,:p,:s,now(),now())"), {"u": invalid_user, "p": project, "s": missing})
    with pytest.raises(DBAPIError, match="active project availability"):
        with wp1_database.begin() as connection:
            invalid_user = _user(connection, f"selection-cross-{uuid.uuid4()}@example.test")
            other_project = _project(connection, other, user, "OTHERSEL")
            connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:u,:p,:s,now(),now())"), {"u": invalid_user, "p": other_project, "s": symbol_set})
    with pytest.raises(DBAPIError, match="active project availability"):
        with wp1_database.begin() as connection:
            invalid_user = _user(connection, f"selection-inactive-{uuid.uuid4()}@example.test")
            inactive = _symbol_set(connection, owner, user, "INACTIVESEL")
            _availability(connection, project, inactive, user, status="inactive")
            connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:u,:p,:s,now(),now())"), {"u": invalid_user, "p": project, "s": inactive})


def test_wp3_organization_default_requires_active_project_availability_at_commit(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"org-default-availability-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"U{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        symbol_set = _symbol_set(connection, owner, user, "NOAVAIL")

    with pytest.raises(DBAPIError, match="availability"):
        with wp1_database.begin() as connection:
            connection.execute(
                text("UPDATE organizations SET default_symbol_set_id=:symbol_set WHERE id=:organization"),
                {"symbol_set": symbol_set, "organization": owner},
            )


def test_wp1_session_context_and_old_writer_cleanup_are_real(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"context-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"C{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "CTX")
    session = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with wp1_database.begin() as connection:
        connection.execute(text("INSERT INTO user_sessions (id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose,session_mode,active_organization_id) VALUES (:id,:u,:token,:n,:expires,NULL,:n,'application','organization',:o)"), {"id": session, "u": user, "token": uuid.uuid4().hex, "n": now, "expires": now + timedelta(hours=1), "o": owner})
    with wp1_database.begin() as connection:
        connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
        assert connection.execute(text("SELECT count(*) FROM user_session_project_contexts")).scalar_one() == 1
    with wp1_database.begin() as connection:
        connection.execute(text("UPDATE user_sessions SET revoked_at=now() WHERE id=:id"), {"id": session})
        assert connection.execute(text("SELECT count(*) FROM user_session_project_contexts")).scalar_one() == 0
    with wp1_database.begin() as connection:
        session = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(text("INSERT INTO user_sessions (id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose,session_mode,active_organization_id) VALUES (:id,:u,:token,:n,:expires,NULL,:n,'application','organization',:o)"), {"id": session, "u": user, "token": uuid.uuid4().hex, "n": now, "expires": now + timedelta(hours=1), "o": owner})
    with wp1_database.begin() as connection:
        connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with wp1_database.begin() as connection:
        connection.execute(text("DELETE FROM user_sessions WHERE id=:id"), {"id": session})
        assert connection.execute(text("SELECT count(*) FROM user_session_project_contexts")).scalar_one() == 0


def test_wp1_project_and_symbol_set_identity_and_history_are_immutable(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"identity-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"I{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "IDENT")
        symbol_set = _symbol_set(connection, owner, user, "IDENTSET")
        for statement, params in (("UPDATE projects SET code='OTHER' WHERE id=:id", {"id": project}), ("UPDATE projects SET normalized_code='other' WHERE id=:id", {"id": project}), ("UPDATE projects SET organization_id=:id WHERE id=:project", {"id": uuid.uuid4(), "project": project}), ("UPDATE projects SET id=:new_id WHERE id=:id", {"new_id": uuid.uuid4(), "id": project}), ("DELETE FROM projects WHERE id=:id", {"id": project}), ("UPDATE symbol_sets SET code='OTHERSET' WHERE id=:id", {"id": symbol_set}), ("UPDATE symbol_sets SET normalized_code='otherset' WHERE id=:id", {"id": symbol_set}), ("UPDATE symbol_sets SET owner_organization_id=:owner WHERE id=:id", {"owner": uuid.uuid4(), "id": symbol_set}), ("UPDATE symbol_sets SET id=:new_id WHERE id=:id", {"new_id": uuid.uuid4(), "id": symbol_set}), ("DELETE FROM symbol_sets WHERE id=:id", {"id": symbol_set})):
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text(statement), params)
            error = caught
            assert "immutable" in str(error.value).lower() or "history" in str(error.value).lower()
        assert connection.execute(text("SELECT count(*) FROM projects WHERE id=:id"), {"id": project}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM symbol_sets WHERE id=:id"), {"id": symbol_set}).scalar_one() == 1


def test_wp1_least_privilege_role_executes_contract_deletes_but_not_history_delete(wp1_database):
    with wp1_database.begin() as connection:
        assert connection.execute(text("SELECT has_table_privilege('symgov_app','project_symbol_sets','DELETE')")).scalar_one()
        assert connection.execute(text("SELECT has_table_privilege('symgov_app','symbol_set_items','DELETE')")).scalar_one()
        assert connection.execute(text("SELECT has_table_privilege('symgov_app','user_project_set_selections','DELETE')")).scalar_one()
        assert connection.execute(text("SELECT has_table_privilege('symgov_app','user_session_project_contexts','DELETE')")).scalar_one()
        connection.execute(text("SET LOCAL ROLE symgov_app"))
        connection.execute(text("SELECT lock_governed_symbols_deterministically(ARRAY[]::uuid[])"))
        for statement in ("DELETE FROM projects", "DELETE FROM symbol_sets", "TRUNCATE projects", "TRUNCATE symbol_sets"):
            with pytest.raises(DBAPIError) as caught:
                with connection.begin_nested():
                    connection.execute(text(statement))
            error = caught
            assert "permission denied" in str(error.value).lower()


def test_wp1_governed_symbol_membership_and_helper_use_real_row_locks(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"locks-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"L{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        symbol_set = _symbol_set(connection, owner, user, "LOCKSET")
        symbols = [uuid.uuid4(), uuid.uuid4()]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for number, symbol in enumerate(symbols):
            connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,:name,'test','test',:owner,:now,:now)"), {"id": symbol, "slug": f"lock-{uuid.uuid4()}", "name": f"Lock {number}", "owner": user, "now": now})
    url = wp1_database.url.render_as_string(hide_password=False).replace("+psycopg", "")
    first = psycopg.connect(url)
    second = psycopg.connect(url)
    third = psycopg.connect(url)
    try:
        first.execute("BEGIN")
        first.execute("SELECT id FROM governed_symbols WHERE id=%s FOR UPDATE", (min(symbols),))
        second.execute("BEGIN")
        second.execute("SET LOCAL lock_timeout='200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute("SELECT lock_governed_symbols_deterministically(%s::uuid[])", ([max(symbols), min(symbols)],))
        third.execute("BEGIN")
        third.execute("SET LOCAL lock_timeout='200ms'")
        third.execute("SELECT id FROM governed_symbols WHERE id=%s FOR UPDATE", (max(symbols),))
        third.rollback()
        first.rollback()
        second.rollback()
        second.execute("BEGIN")
        second.execute("SELECT lock_governed_symbols_deterministically(%s::uuid[])", ([max(symbols), min(symbols)],))
        second.commit()
    finally:
        first.close()
        second.close()
        third.close()

    with wp1_database.begin() as connection:
        held = uuid.uuid4()
        connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,'Held','test','test',:owner,:now,:now)"), {"id": held, "slug": f"held-{uuid.uuid4()}", "owner": user, "now": now})
        item = uuid.uuid4()
        connection.execute(text("INSERT INTO symbol_set_items (id,symbol_set_id,governed_symbol_id,sort_order,created_at,updated_at) VALUES (:id,:set_id,:symbol,0,:now,:now)"), {"id": item, "set_id": symbol_set, "symbol": held, "now": now})
    lock = psycopg.connect(url)
    probe = psycopg.connect(url)
    try:
        lock.execute("BEGIN")
        lock.execute("SELECT id FROM governed_symbols WHERE id=%s FOR UPDATE", (held,))
        probe.execute("BEGIN")
        probe.execute("SET LOCAL lock_timeout='200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            probe.execute("DELETE FROM symbol_set_items WHERE id=%s", (item,))
        probe.rollback()
        lock.rollback()
    finally:
        lock.close()
        probe.close()


def test_wp1_populated_database_refuses_guarded_downgrade_and_keeps_revision(wp1_database):
    with pytest.raises(subprocess.CalledProcessError):
        _alembic(wp1_database.url.render_as_string(hide_password=False), "downgrade", "20260821_0029")
    with wp1_database.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260822_0030"


def test_wp1_default_race_has_one_durable_winner_and_one_loser(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"race-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"R{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "RACE")
        first = _symbol_set(connection, owner, user, "RACEA")
        second = _symbol_set(connection, owner, user, "RACEB")
        first_availability = _availability(connection, project, first, user)
        second_availability = _availability(connection, project, second, user)

    url = wp1_database.url.render_as_string(hide_password=False).replace("+psycopg", "")
    first_connection = psycopg.connect(url)
    second_connection = psycopg.connect(url)
    second_started = threading.Event()
    second_finished = threading.Event()
    result = {}
    try:
        first_connection.execute("BEGIN")
        first_connection.execute("UPDATE project_symbol_sets SET is_default=true WHERE id=%s", (first_availability,))

        def compete():
            try:
                second_connection.execute("BEGIN")
                second_connection.execute("SET LOCAL statement_timeout='5000ms'")
                second_started.set()
                second_connection.execute("UPDATE project_symbol_sets SET is_default=true WHERE id=%s", (second_availability,))
                second_connection.commit()
                result["outcome"] = "committed"
            except psycopg.errors.UniqueViolation as error:
                second_connection.rollback()
                result["outcome"] = error.sqlstate
            finally:
                second_finished.set()

        thread = threading.Thread(target=compete)
        thread.start()
        assert second_started.wait(timeout=2)
        first_connection.commit()
        assert second_finished.wait(timeout=5)
        thread.join(timeout=1)
        assert result["outcome"] == "23505"
    finally:
        first_connection.close()
        second_connection.close()
    with wp1_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM project_symbol_sets WHERE project_id=:project AND is_default"), {"project": project}).scalar_one() == 1
        assert connection.execute(text("SELECT is_default FROM project_symbol_sets WHERE id=:id"), {"id": first_availability}).scalar_one() is True


def test_wp3_availability_default_transfer_emits_complete_audit_evidence(wp1_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw_token = uuid.uuid4().hex
    with wp1_database.begin() as connection:
        user = _user(connection, f"default-audit-{uuid.uuid4()}@example.test")
        owner_code = f"J{uuid.uuid4().hex[:7].upper()}"
        owner = _organization(connection, owner_code, user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "DEFAULTAUDIT")
        old_default = _symbol_set(connection, owner, user, "DEFAULTOLD")
        new_default = _symbol_set(connection, owner, user, "DEFAULTNEW")
        _availability(connection, project, old_default, user, default=True)
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": uuid.uuid4(), "user": user, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": owner,
        })

    SessionLocal = sessionmaker(bind=wp1_database, autoflush=False, expire_on_commit=False)
    request = Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_pilot_codes=(owner_code.lower(),),
    )
    with SessionLocal.begin() as session:
        replace_projects(
            session,
            request,
            settings,
            new_default,
            SimpleNamespace(projects=[SimpleNamespace(projectId=project, isDefault=True)]),
        )

    with wp1_database.connect() as connection:
        default_event = connection.execute(text("""
            SELECT entity_type, entity_id, payload_json
            FROM audit_events
            WHERE action = 'symbol_set.project_default_changed'
              AND entity_id = :project
        """), {"project": project}).one()
        assert default_event.entity_type == "project"
        assert default_event.entity_id == project
        assert default_event.payload_json["projectId"] == str(project)
        assert default_event.payload_json["symbolSetId"] == str(new_default)
        assert default_event.payload_json["oldDefaultSymbolSetId"] == str(old_default)
        assert default_event.payload_json["newDefaultSymbolSetId"] == str(new_default)
        assert default_event.payload_json["beforeAvailableSymbolSetCount"] == 1
        assert default_event.payload_json["afterAvailableSymbolSetCount"] == 2

        availability_event = connection.execute(text("""
            SELECT payload_json
            FROM audit_events
            WHERE action = 'symbol_set.project_availability_replaced'
              AND entity_id = :symbol_set
        """), {"symbol_set": new_default}).scalar_one()
        assert availability_event["affectedProjectIds"] == [str(project)]
        assert availability_event["beforeProjectCount"] == 0
        assert availability_event["afterProjectCount"] == 1


def test_wp3_authority_shared_reads_coexist_and_writers_serialize(wp1_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw_token = uuid.uuid4().hex
    authority_session_id = uuid.uuid4()
    with wp1_database.begin() as connection:
        user = _user(connection, f"authority-share-{uuid.uuid4()}@example.test")
        owner_code = f"H{uuid.uuid4().hex[:7].upper()}"
        owner = _organization(connection, owner_code, user)
        _admin(connection, owner, user)
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": authority_session_id, "user": user, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": owner,
        })

    SessionLocal = sessionmaker(bind=wp1_database, autoflush=False, expire_on_commit=False)
    request = Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_pilot_codes=(owner_code.lower(),),
    )

    first_reader = SessionLocal()
    require_stage4_principal(first_reader, request, settings, admin=True)
    second_started = threading.Event()
    second_done = threading.Event()
    second_errors = []

    def read_authority() -> None:
        session = SessionLocal()
        try:
            second_started.set()
            require_stage4_principal(session, request, settings, admin=True)
            session.commit()
        except Exception as error:  # pragma: no cover - asserted below
            session.rollback()
            second_errors.append(type(error).__name__)
        finally:
            session.close()
            second_done.set()

    second_thread = threading.Thread(target=read_authority)
    second_thread.start()
    assert second_started.wait(timeout=2)
    shared_reads_coexisted = second_done.wait(timeout=1)
    first_reader.rollback()
    first_reader.close()
    assert second_done.wait(timeout=5)
    second_thread.join(timeout=1)
    assert shared_reads_coexisted
    assert second_errors == []

    authority_reader = SessionLocal()
    require_stage4_principal(authority_reader, request, settings, admin=True)
    writer_started = threading.Event()
    writer_done = threading.Event()
    writer_errors = []
    url = wp1_database.url.render_as_string(hide_password=False).replace("+psycopg", "")

    def change_authority() -> None:
        connection = psycopg.connect(url)
        try:
            connection.execute("BEGIN")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            writer_started.set()
            connection.execute(
                "UPDATE user_sessions SET revoked_at=now() WHERE id=%s",
                (authority_session_id,),
            )
            connection.commit()
        except Exception as error:  # pragma: no cover - asserted below
            connection.rollback()
            writer_errors.append(type(error).__name__)
        finally:
            connection.close()
            writer_done.set()

    writer_thread = threading.Thread(target=change_authority)
    writer_thread.start()
    assert writer_started.wait(timeout=2)
    assert not writer_done.wait(timeout=0.3)
    authority_reader.rollback()
    authority_reader.close()
    assert writer_done.wait(timeout=5)
    writer_thread.join(timeout=1)
    assert writer_errors == []
    with wp1_database.connect() as connection:
        assert connection.execute(text(
            "SELECT revoked_at FROM user_sessions WHERE id=:id"
        ), {"id": authority_session_id}).scalar_one() is not None


@pytest.mark.parametrize("authority_change", ["revoked", "expired"])
def test_wp3_authority_recheck_observes_commit_between_probe_and_lock(
    wp1_database, authority_change, monkeypatch
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw_token = uuid.uuid4().hex
    authority_session_id = uuid.uuid4()
    with wp1_database.begin() as connection:
        user = _user(connection, f"authority-fresh-{authority_change}-{uuid.uuid4()}@example.test")
        owner_code = f"F{uuid.uuid4().hex[:7].upper()}"
        owner = _organization(connection, owner_code, user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, f"FRESH{authority_change.upper()}")
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": authority_session_id, "user": user, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": owner,
        })

    SessionLocal = sessionmaker(bind=wp1_database, autoflush=False, expire_on_commit=False)
    request = Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_pilot_codes=(owner_code.lower(),),
    )
    original_query = SessionLocal.class_.query
    changed = False
    domain_accessed = False

    def query_after_probe(session, *entities, **kwargs):
        nonlocal changed, domain_accessed
        if entities and entities[0] in (Project, ProjectSymbolSet, AuditEvent):
            domain_accessed = True
        query = original_query(session, *entities, **kwargs)
        if not changed and entities and getattr(entities[0], "__name__", None) == "User":
            with wp1_database.begin() as connection:
                if authority_change == "revoked":
                    connection.execute(text(
                        "UPDATE user_sessions SET revoked_at=:now WHERE id=:id"
                    ), {"now": now, "id": authority_session_id})
                else:
                    connection.execute(text(
                        "UPDATE user_sessions SET expires_at=:expired WHERE id=:id"
                    ), {"expired": now - timedelta(minutes=1), "id": authority_session_id})
            changed = True
        return query

    monkeypatch.setattr(SessionLocal.class_, "query", query_after_probe)
    session = SessionLocal()
    try:
        with pytest.raises(HTTPException) as caught:
            patch_project(
                session,
                request,
                settings,
                project,
                SimpleNamespace(
                    status="closed",
                    model_fields_set={"status"},
                    only_status=lambda: True,
                ),
            )
        assert caught.value.status_code == 401
        assert changed
        assert not domain_accessed
        assert not session.new and not session.dirty and not session.deleted
    finally:
        session.rollback()
        session.close()
    with wp1_database.connect() as connection:
        assert connection.execute(text(
            "SELECT status FROM projects WHERE id=:id"
        ), {"id": project}).scalar_one() == "active"
        assert connection.execute(text(
            "SELECT count(*) FROM audit_events WHERE entity_id=:id"
        ), {"id": project}).scalar_one() == 0


def test_wp3_postgresql_lifecycle_and_default_cleanup_audits_are_complete(wp1_database):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw_token = uuid.uuid4().hex
    with wp1_database.begin() as connection:
        user = _user(connection, f"lifecycle-audit-{uuid.uuid4()}@example.test")
        owner_code = f"A{uuid.uuid4().hex[:7].upper()}"
        owner = _organization(connection, owner_code, user)
        _admin(connection, owner, user)
        cleanup_project = _project(connection, owner, user, "AUDITCLEAN")
        cleanup_set = _symbol_set(connection, owner, user, "AUDITCLEANSET")
        _availability(connection, cleanup_project, cleanup_set, user, default=True)
        connection.execute(text(
            "UPDATE organizations SET default_symbol_set_id=:set_id WHERE id=:owner"
        ), {"set_id": cleanup_set, "owner": owner})
        closing_project = _project(connection, owner, user, "AUDITCLOSE")
        closing_set = _symbol_set(connection, owner, user, "AUDITCLOSESET")
        _availability(connection, closing_project, closing_set, user, default=True)
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": uuid.uuid4(), "user": user, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": owner,
        })

    SessionLocal = sessionmaker(bind=wp1_database, autoflush=False, expire_on_commit=False)
    request = Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_pilot_codes=(owner_code.lower(),),
    )
    with SessionLocal.begin() as session:
        patch_set(
            session, request, settings, cleanup_set,
            SimpleNamespace(
                status="superseded", model_fields_set={"status"}, name=None,
                description=None, disciplines=None, useCases=None,
            ),
        )
    with SessionLocal.begin() as session:
        patch_project(
            session, request, settings, closing_project,
            SimpleNamespace(
                status="closed", model_fields_set={"status"}, only_status=lambda: True,
            ),
        )

    with wp1_database.connect() as connection:
        events = {
            row.action: row
            for row in connection.execute(text("""
                SELECT action, actor_id, payload_json
                FROM audit_events
                WHERE entity_id IN (:cleanup_set, :cleanup_project, :owner, :closing_project)
                  AND action IN (
                    'symbol_set.superseded', 'symbol_set.project_default_changed',
                    'organization.symbol_set_default_changed', 'project.closed'
                  )
            """), {
                "cleanup_set": cleanup_set, "cleanup_project": cleanup_project,
                "owner": owner, "closing_project": closing_project,
            })
        }
        assert set(events) == {
            "symbol_set.superseded", "symbol_set.project_default_changed",
            "organization.symbol_set_default_changed", "project.closed",
        }
        assert {row.actor_id for row in events.values()} == {user}
        lifecycle = events["symbol_set.superseded"].payload_json
        assert lifecycle["oldStatus"] == "active"
        assert lifecycle["newStatus"] == "superseded"
        assert lifecycle["affectedProjectIds"] == [str(cleanup_project)]
        assert lifecycle["beforeAvailableProjectCount"] == 1
        assert lifecycle["afterAvailableProjectCount"] == 0
        project_default = events["symbol_set.project_default_changed"].payload_json
        assert project_default["oldDefaultSymbolSetId"] == str(cleanup_set)
        assert project_default["newDefaultSymbolSetId"] is None
        organization_default = events["organization.symbol_set_default_changed"].payload_json
        assert organization_default["oldDefaultSymbolSetId"] == str(cleanup_set)
        assert organization_default["newDefaultSymbolSetId"] is None
        closed = events["project.closed"].payload_json
        assert closed["oldStatus"] == "active"
        assert closed["newStatus"] == "closed"
        assert closed["affectedSymbolSetIds"] == [str(closing_set)]
        assert closed["beforeAvailableSymbolSetCount"] == 1
        assert closed["afterAvailableSymbolSetCount"] == 0


def test_wp3_service_cleanup_and_availability_paths_share_project_before_set_lock_order(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"lock-order-{uuid.uuid4()}@example.test")
        owner_code = f"O{uuid.uuid4().hex[:7].upper()}"
        owner = _organization(connection, owner_code, user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "LOCKORDER")
        symbol_set = _symbol_set(connection, owner, user, "LOCKORDERSET")
        availability = _availability(connection, project, symbol_set, user)
        connection.execute(text("UPDATE organizations SET default_symbol_set_id=:symbol_set WHERE id=:organization"), {"symbol_set": symbol_set, "organization": owner})
        connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:user,:project,:symbol_set,now(),now())"), {"user": user, "project": project, "symbol_set": symbol_set})
        now = datetime.now(timezone.utc).replace(microsecond=0)
        raw_token = uuid.uuid4().hex
        connection.execute(text("INSERT INTO user_sessions (id,auth_user_id,token_hash,created_at,expires_at,last_seen_at,purpose,session_mode,active_organization_id) VALUES (:id,:user,:token,:now,:expires,:now,'application','organization',:organization)"), {"id": uuid.uuid4(), "user": user, "token": hash_session_token(raw_token), "now": now, "expires": now + timedelta(hours=1), "organization": owner})

    SessionLocal = sessionmaker(bind=wp1_database)
    barrier = threading.Barrier(2)
    outcomes = []
    request = Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})
    settings = SimpleNamespace(organizations_enabled=True, symbol_sets_enabled=True, organization_pilot_codes=(owner_code.lower(),))

    def run_service_path(mode: str) -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=2)
            if mode == "cleanup":
                clear_organization_default(session, request, settings)
                patch_project(
                    session,
                    request,
                    settings,
                    project,
                    SimpleNamespace(
                        status="closed",
                        model_fields_set={"status"},
                        only_status=lambda: True,
                    ),
                )
            else:
                replace_projects(
                    session,
                    request,
                    settings,
                    symbol_set,
                    SimpleNamespace(projects=[SimpleNamespace(projectId=project, isDefault=True)]),
                )
            session.commit()
            outcomes.append("committed")
        except Exception as error:  # pragma: no cover - failure details are asserted below
            session.rollback()
            outcomes.append(type(error).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=run_service_path, args=(mode,)) for mode in ("cleanup", "availability")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=8)
    assert sorted(outcomes) in (["HTTPException", "committed"], ["committed", "committed"])
    assert all(not thread.is_alive() for thread in threads)

    with SessionLocal.begin() as session:
        _, organization = _lock_organization_default_anchors(session, owner)
        assert organization.default_symbol_set_id is None
        link = session.query(ProjectSymbolSet).filter_by(id=availability).one_or_none()
        assert link is None or (link.status == "inactive" and link.is_default is False)
        assert session.query(UserProjectSetSelection).filter_by(project_id=project, active_symbol_set_id=symbol_set).count() == 0


def test_wp1_session_context_rejects_wrong_org_revoked_personal_and_closed_project(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"context-reject-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"E{uuid.uuid4().hex[:7].upper()}", user)
        other = _organization(connection, f"F{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        _admin(connection, other, user)
        project = _project(connection, owner, user, "REJECT")
        other_project = _project(connection, other, user, "OTHERREJ")
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def insert_session(connection, *, organization=None, mode="organization", purpose="application", revoked=False):
        session = uuid.uuid4()
        connection.execute(text("INSERT INTO user_sessions (id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose,session_mode,active_organization_id) VALUES (:id,:u,:token,:n,:expires,:revoked,:n,:purpose,:mode,:o)"), {"id": session, "u": user, "token": uuid.uuid4().hex, "n": now, "expires": now + timedelta(hours=1), "revoked": now if revoked else None, "purpose": purpose, "mode": mode, "o": organization})
        return session

    with pytest.raises(DBAPIError, match="session project context"):
        with wp1_database.begin() as connection:
            session = insert_session(connection, organization=other)
            connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with pytest.raises(DBAPIError, match="session project context"):
        with wp1_database.begin() as connection:
            session = insert_session(connection, organization=owner, revoked=True)
            connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with pytest.raises(DBAPIError, match="session project context"):
        with wp1_database.begin() as connection:
            session = insert_session(connection, mode="personal")
            connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with pytest.raises(DBAPIError, match="session project context"):
        with wp1_database.begin() as connection:
            session = insert_session(connection, organization=owner, purpose="credential_change")
            connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with wp1_database.begin() as connection:
        session = insert_session(connection, organization=owner)
        connection.execute(text("UPDATE projects SET status='closed', closed_at=:n WHERE id=:p"), {"n": now, "p": project})
    with pytest.raises(DBAPIError, match="session project context"):
        with wp1_database.begin() as connection:
            connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with wp1_database.begin() as connection:
        connection.execute(text("UPDATE projects SET status='active', closed_at=NULL WHERE id=:p"), {"p": project})
        connection.execute(text("DELETE FROM user_sessions WHERE id=:s"), {"s": session})


def test_wp1_membership_insert_waits_and_helper_completes_after_release(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"insert-lock-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"G{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        symbol_set = _symbol_set(connection, owner, user, "INSERTLOCK")
        governed = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,'Insert Lock','test','test',:owner,:now,:now)"), {"id": governed, "slug": f"insert-{uuid.uuid4()}", "owner": user, "now": now})
    url = wp1_database.url.render_as_string(hide_password=False).replace("+psycopg", "")
    holder = psycopg.connect(url)
    probe = psycopg.connect(url)
    try:
        holder.execute("BEGIN")
        holder.execute("SELECT id FROM governed_symbols WHERE id=%s FOR UPDATE", (governed,))
        probe.execute("BEGIN")
        probe.execute("SET LOCAL lock_timeout='200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            probe.execute("INSERT INTO symbol_set_items (id,symbol_set_id,governed_symbol_id,sort_order,created_at,updated_at) VALUES (%s,%s,%s,0,%s,%s)", (uuid.uuid4(), symbol_set, governed, now, now))
        probe.rollback()
        holder.rollback()
        probe.execute("BEGIN")
        probe.execute("SELECT lock_governed_symbols_deterministically(%s::uuid[])", ([governed],))
        probe.commit()
    finally:
        holder.close()
        probe.close()


def test_wp1_role_executes_all_contract_deletes(wp1_database):
    with wp1_database.begin() as connection:
        user = _user(connection, f"deletes-{uuid.uuid4()}@example.test")
        owner = _organization(connection, f"H{uuid.uuid4().hex[:7].upper()}", user)
        _admin(connection, owner, user)
        project = _project(connection, owner, user, "DELETES")
        symbol_set = _symbol_set(connection, owner, user, "DELETES")
        availability = _availability(connection, project, symbol_set, user)
        governed = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(text("INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at) VALUES (:id,:slug,'Delete Symbol','test','test',:owner,:now,:now)"), {"id": governed, "slug": f"delete-{uuid.uuid4()}", "owner": user, "now": now})
        item = uuid.uuid4()
        connection.execute(text("INSERT INTO symbol_set_items (id,symbol_set_id,governed_symbol_id,sort_order,created_at,updated_at) VALUES (:id,:set_id,:symbol,0,:now,:now)"), {"id": item, "set_id": symbol_set, "symbol": governed, "now": now})
        connection.execute(text("INSERT INTO user_project_set_selections (user_id,project_id,active_symbol_set_id,selected_at,updated_at) VALUES (:u,:p,:s,:n,:n)"), {"u": user, "p": project, "s": symbol_set, "n": now})
        session = uuid.uuid4()
        connection.execute(text("INSERT INTO user_sessions (id,auth_user_id,token_hash,created_at,expires_at,revoked_at,last_seen_at,purpose,session_mode,active_organization_id) VALUES (:id,:u,:token,:n,:expires,NULL,:n,'application','organization',:o)"), {"id": session, "u": user, "token": uuid.uuid4().hex, "n": now, "expires": now + timedelta(hours=1), "o": owner})
        connection.execute(text("INSERT INTO user_session_project_contexts (user_session_id,project_id,selected_at,updated_at) VALUES (:s,:p,:n,:n)"), {"s": session, "p": project, "n": now})
    with wp1_database.begin() as connection:
        connection.execute(text("SET LOCAL ROLE symgov_app"))
        for statement, identifier in (("DELETE FROM project_symbol_sets WHERE id=:id", availability), ("DELETE FROM symbol_set_items WHERE id=:id", item), ("DELETE FROM user_project_set_selections WHERE user_id=:user AND project_id=:project", None), ("DELETE FROM user_session_project_contexts WHERE user_session_id=:session", None)):
            params = {"id": identifier, "user": user, "project": project, "session": session}
            connection.execute(text(statement), params)
        connection.execute(text("SELECT lock_governed_symbols_deterministically(ARRAY[:id]::uuid[])"), {"id": governed})


@pytest.fixture
def empty_wp1_database():
    name = f"symgov-wp1-empty-{uuid.uuid4().hex[:12]}"
    password = "disposable-wp1-password"
    _docker("run", "--rm", "--detach", "--name", name, "--env", f"POSTGRES_PASSWORD={password}", "--env", "POSTGRES_DB=symgov_wp1", "--publish", "127.0.0.1::5432", "postgres:16-alpine")
    engine = None
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        raw_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/symgov_wp1"
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
        _alembic(url, "upgrade", "20260821_0029")
        _alembic(url, "upgrade", "20260822_0030")
        engine = create_engine(url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "--force", name, check=False)


def test_wp1_empty_database_downgrades_and_reupgrades_without_stage4_objects(empty_wp1_database):
    url = empty_wp1_database.url.render_as_string(hide_password=False)
    _alembic(url, "downgrade", "20260821_0029")
    with empty_wp1_database.connect() as connection:
        names = set(inspect(empty_wp1_database).get_table_names())
        assert not {"projects", "symbol_sets", "project_symbol_sets", "symbol_set_items", "user_project_set_selections", "user_session_project_contexts"} & names
        assert "default_symbol_set_id" not in {column["name"] for column in inspect(empty_wp1_database).get_columns("organizations")}
        assert connection.execute(text("SELECT count(*) FROM pg_proc WHERE proname IN ('stage4_jsonb_max_depth', 'lock_governed_symbols_deterministically', 'lock_governed_symbol_boundary')")).scalar_one() == 0
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260821_0029"
    _alembic(url, "upgrade", "20260822_0030")
    with empty_wp1_database.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260822_0030"
        assert inspect(empty_wp1_database).has_table("projects")