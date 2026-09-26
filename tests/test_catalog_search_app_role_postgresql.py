"""The Catalog and Set searches need `TEMPORARY` on the database for `symgov_app`.

`catalog_browse_search._build_candidates` builds a per-transaction temp table
on every search. Production revokes `TEMPORARY` from `PUBLIC` (PostgreSQL
grants it to `PUBLIC` by default), and `symgov_app` had no grant of its own,
so after `stage11-1536b05` shipped on 2026-09-26 every Catalog and Set search
failed with `permission denied to create temporary tables in database
"symgov"`: the Catalog tab showed 0 symbols and the Set tab "Request failed".
No other test caught it, because they all connect as a superuser.

The grant was made by hand (`GRANT TEMPORARY ON DATABASE symgov TO
symgov_app;`, as the owner). A migration cannot make it, because
`symgov_migrator` does not own the database, so the requirement is recorded in
`/docker/symgov-postgres/README.md`, `symgov-governance-architecture.md`, and
checked by `scripts/deploy-release.sh` before every deploy. This file
reproduces the production privilege model in a disposable container and pins
both halves: the search fails without the grant and works with it.

Redaction: this file never prints the disposable container's connection
string or role passwords.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.catalog_browse_search import (  # noqa: E402
    CatalogSearchRequest,
    CatalogSearchScope,
    search_catalog,
)

from postgres_image import POSTGRES_IMAGE, POSTGRES_READY_TIMEOUT  # noqa: E402
from test_two_role_privilege_model_postgresql import _alembic, _docker  # noqa: E402

MIGRATION_HEAD = "20260925_0064"
DATABASE = "symgov_temp_probe"


@pytest.fixture(scope="module")
def production_privilege_model():
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required for the app-role search rehearsal")

    name = f"symgov-temp-probe-{uuid.uuid4().hex[:12]}"
    superuser_password = f"disposable-{uuid.uuid4().hex}"
    app_password = f"disposable-app-{uuid.uuid4().hex}"
    _docker(
        "run", "--rm", "--detach", "--name", name,
        "--env", f"POSTGRES_PASSWORD={superuser_password}",
        "--env", f"POSTGRES_DB={DATABASE}",
        "--publish", "127.0.0.1::5432",
        POSTGRES_IMAGE,
    )
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        privileged_raw = f"postgresql://postgres:{superuser_password}@127.0.0.1:{port}/{DATABASE}"
        app_raw = f"postgresql://symgov_app:{app_password}@127.0.0.1:{port}/{DATABASE}"

        deadline = time.monotonic() + POSTGRES_READY_TIMEOUT
        while True:
            try:
                with psycopg.connect(privileged_raw, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

        # The production model, as /docker/symgov-postgres/README.md records it.
        with psycopg.connect(privileged_raw, autocommit=True) as connection:
            connection.execute(f"REVOKE TEMPORARY ON DATABASE {DATABASE} FROM PUBLIC")
            connection.execute(f"CREATE ROLE symgov_app LOGIN PASSWORD '{app_password}'")
            connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
                " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO symgov_app"
            )
            connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
                " GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO symgov_app"
            )

        _alembic(privileged_raw.replace("postgresql://", "postgresql+psycopg://", 1),
                 "upgrade", MIGRATION_HEAD)
        yield {"privileged_raw": privileged_raw,
               "app_url": app_raw.replace("postgresql://", "postgresql+psycopg://", 1)}
    finally:
        _docker("rm", "--force", "--volumes", name, check=False)


def _search_as_app_role(app_url: str):
    engine = create_engine(app_url)
    try:
        with Session(engine) as session:
            page = search_catalog(session, CatalogSearchScope(user_id=uuid.uuid4()), CatalogSearchRequest())
            session.commit()
            return page
    finally:
        engine.dispose()


def test_the_catalog_search_needs_temporary_on_the_database(production_privilege_model):
    """The 2026-09-26 outage, reproduced, and then the grant that fixed it."""
    with pytest.raises(ProgrammingError, match="permission denied to create temporary tables"):
        _search_as_app_role(production_privilege_model["app_url"])

    with psycopg.connect(production_privilege_model["privileged_raw"], autocommit=True) as connection:
        connection.execute(f"GRANT TEMPORARY ON DATABASE {DATABASE} TO symgov_app")

    page = _search_as_app_role(production_privilege_model["app_url"])
    assert page.total == 0
    assert page.entries == []
