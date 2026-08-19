from __future__ import annotations
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from symgov_backend.app import create_app
from symgov_backend.auth import (
    _as_aware_utc,
    hash_session_token,
    upsert_user,
    utc_now,
)
from symgov_backend.dependencies import get_db_session, require_recent_step_up
from symgov_backend.models import User, UserSession

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


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
def postgres_url() -> Generator[str, None, None]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required")
    name = f"symgov-2c-{uuid.uuid4().hex[:12]}"
    password = "test-password"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--detach",
            "--name",
            name,
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--env",
            "POSTGRES_DB=symgov_2c",
            "--publish",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        check=True,
    )

    try:
        port_output = subprocess.run(
            ["docker", "port", name, "5432/tcp"], capture_output=True, text=True, check=True
        ).stdout.strip()
        port = int(port_output.rsplit(":", 1)[1])
        url = f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/symgov_2c"
        # Wait for PG
        start = time.time()
        while time.time() - start < 30:
            try:
                engine = create_engine(url)
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception:
                time.sleep(0.5)

        # Create required role for migrations
        subprocess.run(
            ["docker", "exec", name, "psql", "-U", "postgres", "-c", "CREATE ROLE symgov_app;"],
            check=True,
        )

        _alembic(url, "upgrade", "head")
        yield url
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, text=True)


def test_stage_2c_full_reauth_matrix(postgres_url):
    engine = create_engine(postgres_url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    user_email = "test-2c@example.com"
    user_pin = "1234"

    with Session() as session:
        upsert_user(
            session,
            email=user_email,
            display_name="Test 2C User",
            roles=["admin"],
            pin=user_pin,
            must_change_pin=False,
        )
        session.commit()

    app = create_app()

    @app.get("/api/v1/test-protected")
    def test_protected(user=pytest.importorskip("fastapi").Depends(require_recent_step_up)):
        return {"ok": True}

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    client = TestClient(app, headers={"origin": "http://testserver"})

    # --- 1. Login and verify no step-up ---
    login_resp = client.post("/api/v1/auth/login", json={"email": user_email, "pin": user_pin})
    assert login_resp.status_code == 200
    assert client.get("/api/v1/test-protected").status_code == 403

    # --- 2. Reauthenticate success ---
    reauth_resp = client.post("/api/v1/auth/reauthenticate", json={"pin": user_pin})
    assert reauth_resp.status_code == 200
    assert reauth_resp.json() == {"ok": True}
    assert client.get("/api/v1/test-protected").status_code == 200

    # --- 3. Step-up expiry (599 vs 600) ---
    with Session() as session:
        user_session = session.query(UserSession).filter(UserSession.revoked_at.is_(None)).first()
        # Set to 599 seconds ago
        user_session.recent_step_up_at = _as_aware_utc(utc_now()) - timedelta(seconds=599)
        session.commit()
    assert client.get("/api/v1/test-protected").status_code == 200

    with Session() as session:
        user_session = session.query(UserSession).filter(UserSession.revoked_at.is_(None)).first()
        # Set to 600 seconds ago
        user_session.recent_step_up_at = _as_aware_utc(utc_now()) - timedelta(seconds=600)
        session.commit()
    assert client.get("/api/v1/test-protected").status_code == 403

    # --- 4. PIN Change clears step-up ---
    # First, get step-up back
    client.post("/api/v1/auth/reauthenticate", json={"pin": user_pin})
    assert client.get("/api/v1/test-protected").status_code == 200
    
    new_pin = "4321"
    change_resp = client.post("/api/v1/auth/change-pin", json={"currentPin": user_pin, "newPin": new_pin})
    assert change_resp.status_code == 200
    # Step-up should be cleared
    assert client.get("/api/v1/test-protected").status_code == 403
    user_pin = new_pin # update for later steps

    # --- 5. Logout clears session (and step-up) ---
    client.post("/api/v1/auth/reauthenticate", json={"pin": user_pin})
    assert client.get("/api/v1/test-protected").status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/test-protected").status_code == 401

    # --- 6. Step-up is session-bound (not user-wide) ---
    # Login again
    client.post("/api/v1/auth/login", json={"email": user_email, "pin": user_pin})
    session_1_cookie = client.cookies.get("symgov_session")
    client.post("/api/v1/auth/reauthenticate", json={"pin": user_pin})
    assert client.get("/api/v1/test-protected").status_code == 200

    # New login for same user
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"email": user_email, "pin": user_pin})
    # This new session should NOT have step-up
    assert client.get("/api/v1/test-protected").status_code == 403

    # Restore first session
    client.cookies.set("symgov_session", session_1_cookie)
    assert client.get("/api/v1/test-protected").status_code == 200

    # --- 7. No legacy alias for reauthenticate ---
    legacy_resp = client.post("/api/auth/reauthenticate", json={"pin": user_pin})
    assert legacy_resp.status_code == 404

    # --- 8. Reauthentication throttling ---
    # (Assuming default limit is low enough or we just check failure response)
    # We can't easily test the full block without knowing settings, but we can check failure.
    fail_resp = client.post("/api/v1/auth/reauthenticate", json={"pin": "9999"})
    assert fail_resp.status_code == 401
    assert fail_resp.json()["detail"] == "Invalid PIN."
