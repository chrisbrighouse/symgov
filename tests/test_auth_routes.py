from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import authenticate_user, upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import User, UserRole, UserSession


def build_client_with_user():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSession.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        upsert_user(
            session,
            email="chris.brighouse@hotmail.co.uk",
            display_name="Alfi",
            roles=["admin", "submitter", "reviewer"],
            pin="4590",
        )
        session.commit()

    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), Session


def test_login_sets_http_only_session_cookie_and_returns_user_roles():
    client, _ = build_client_with_user()

    response = client.post("/api/v1/auth/login", json={"email": "CHRIS.BRIGHOUSE@HOTMAIL.CO.UK", "pin": "4590"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "chris.brighouse@hotmail.co.uk"
    assert payload["user"]["displayName"] == "Alfi"
    assert payload["user"]["roles"] == ["admin", "reviewer", "submitter"]
    assert payload["user"]["mustChangePin"] is True
    assert "symgov_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_login_rejects_wrong_pin():
    client, _ = build_client_with_user()

    response = client.post("/api/v1/auth/login", json={"email": "chris.brighouse@hotmail.co.uk", "pin": "1234"})

    assert response.status_code == 401


def test_me_returns_current_user_after_login():
    client, _ = build_client_with_user()
    login = client.post("/api/v1/auth/login", json={"email": "chris.brighouse@hotmail.co.uk", "pin": "4590"})
    assert login.status_code == 200

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["displayName"] == "Alfi"


def test_logout_revokes_current_session():
    client, _ = build_client_with_user()
    login = client.post("/api/v1/auth/login", json={"email": "chris.brighouse@hotmail.co.uk", "pin": "4590"})
    assert login.status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_change_pin_requires_current_pin_and_clears_default_pin_flag():
    client, Session = build_client_with_user()
    login = client.post("/api/v1/auth/login", json={"email": "chris.brighouse@hotmail.co.uk", "pin": "4590"})
    assert login.status_code == 200

    response = client.post("/api/v1/auth/change-pin", json={"currentPin": "4590", "newPin": "6781"})

    assert response.status_code == 200
    assert response.json()["user"]["mustChangePin"] is False
    with Session() as session:
        assert authenticate_user(session, email="chris.brighouse@hotmail.co.uk", pin="4590") is None
        assert authenticate_user(session, email="chris.brighouse@hotmail.co.uk", pin="6781") is not None


def test_change_pin_rejects_wrong_current_pin():
    client, _ = build_client_with_user()
    login = client.post("/api/v1/auth/login", json={"email": "chris.brighouse@hotmail.co.uk", "pin": "4590"})
    assert login.status_code == 200

    response = client.post("/api/v1/auth/change-pin", json={"currentPin": "1234", "newPin": "6781"})

    assert response.status_code == 400
