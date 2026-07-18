from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import authenticate_user, upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import User, UserRole, UserSession


def build_client_with_users():
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
            email="admin@symgov.local",
            display_name="Alfi",
            roles=["admin", "submitter", "reviewer"],
            pin="4590",
        )
        upsert_user(
            session,
            email="reviewer@symgov.local",
            display_name="Rupert",
            roles=["reviewer"],
            pin="4590",
        )
        session.commit()

    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), Session


def test_admin_users_list_requires_admin_role():
    client, _ = build_client_with_users()

    login = client.post("/api/v1/auth/login", json={"email": "reviewer@symgov.local", "pin": "4590"})
    assert login.status_code == 200

    response = client.get("/api/v1/admin/users")

    assert response.status_code == 403


def test_admin_can_list_create_update_and_reset_users():
    client, Session = build_client_with_users()

    login = client.post("/api/v1/auth/login", json={"email": "admin@symgov.local", "pin": "4590"})
    assert login.status_code == 200

    listed = client.get("/api/v1/admin/users")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2

    created = client.post(
        "/api/v1/admin/users",
        json={
            "email": "new.submitter@symgov.local",
            "displayName": "New Submitter",
            "roles": ["submitter"],
            "pin": "4590",
            "isActive": True,
        },
    )
    assert created.status_code == 201
    created_user = created.json()["user"]
    assert created_user["email"] == "new.submitter@symgov.local"
    assert created_user["roles"] == ["submitter"]
    assert created_user["mustChangePin"] is True

    updated = client.patch(
        f"/api/v1/admin/users/{created_user['id']}",
        json={"roles": ["reviewer", "submitter"], "isActive": False},
    )
    assert updated.status_code == 200
    assert updated.json()["user"]["roles"] == ["reviewer", "submitter"]
    assert updated.json()["user"]["isActive"] is False

    reset = client.post(f"/api/v1/admin/users/{created_user['id']}/reset-pin", json={"pin": "6781"})
    assert reset.status_code == 200
    assert reset.json()["user"]["mustChangePin"] is True

    with Session() as session:
        assert authenticate_user(session, email="new.submitter@symgov.local", pin="4590") is None
        assert authenticate_user(session, email="new.submitter@symgov.local", pin="6781") is None

    reactivated = client.patch(
        f"/api/v1/admin/users/{created_user['id']}",
        json={"isActive": True},
    )
    assert reactivated.status_code == 200

    with Session() as session:
        assert authenticate_user(session, email="new.submitter@symgov.local", pin="6781") is not None


def test_admin_can_create_catalog_only_user_without_roles():
    client, _ = build_client_with_users()

    login = client.post("/api/v1/auth/login", json={"email": "admin@symgov.local", "pin": "4590"})
    assert login.status_code == 200

    created = client.post(
        "/api/v1/admin/users",
        json={
            "email": "catalog.reader@symgov.local",
            "displayName": "Catalog Reader",
            "roles": [],
            "pin": "4590",
        },
    )

    assert created.status_code == 201
    assert created.json()["user"]["roles"] == []


def test_admin_can_assign_integrator_role():
    client, _ = build_client_with_users()
    assert client.post("/api/v1/auth/login", json={"email": "admin@symgov.local", "pin": "4590"}).status_code == 200

    created = client.post(
        "/api/v1/admin/users",
        json={
            "email": "integrator@symgov.local",
            "displayName": "Catalog Integrator",
            "roles": ["integrator"],
            "pin": "4590",
        },
    )

    assert created.status_code == 201
    assert created.json()["user"]["roles"] == ["integrator"]


def test_admin_can_remove_last_role_from_existing_user():
    client, _ = build_client_with_users()

    login = client.post("/api/v1/auth/login", json={"email": "admin@symgov.local", "pin": "4590"})
    assert login.status_code == 200

    listed = client.get("/api/v1/admin/users")
    reviewer = next(user for user in listed.json()["items"] if user["email"] == "reviewer@symgov.local")

    updated = client.patch(f"/api/v1/admin/users/{reviewer['id']}", json={"roles": []})

    assert updated.status_code == 200
    assert updated.json()["user"]["roles"] == []


def test_admin_create_user_rejects_duplicate_email_or_name():
    client, _ = build_client_with_users()

    login = client.post("/api/v1/auth/login", json={"email": "admin@symgov.local", "pin": "4590"})
    assert login.status_code == 200

    duplicate_email = client.post(
        "/api/v1/admin/users",
        json={
            "email": "ADMIN@symgov.local",
            "displayName": "Another Name",
            "roles": ["submitter"],
            "pin": "4590",
        },
    )
    assert duplicate_email.status_code == 409

    duplicate_name = client.post(
        "/api/v1/admin/users",
        json={
            "email": "fresh@symgov.local",
            "displayName": "alfi",
            "roles": ["submitter"],
            "pin": "4590",
        },
    )
    assert duplicate_name.status_code == 409
