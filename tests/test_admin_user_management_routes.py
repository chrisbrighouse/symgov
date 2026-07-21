import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import authenticate_user, upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.subscriptions import upgrade_to_plus

OWNER_EMAIL = "chris.brighouse@hotmail.co.uk"


def build_client_with_users():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        upsert_user(session, email=OWNER_EMAIL, display_name="Chris", roles=["admin", "submitter", "reviewer"], pin="4590")
        reviewer = upsert_user(session, email="reviewer@symgov.local", display_name="Rupert", roles=["reviewer"], pin="4590")
        upgrade_to_plus(session, reviewer, months=12)
        session.commit()
    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), Session


def login_admin(client):
    response = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "pin": "4590"})
    assert response.status_code == 200


def create_free_user(client, *, email="new@example.com", display_name="New User"):
    response = client.post(
        "/api/v1/admin/users",
        json={"email": email, "displayName": display_name, "roles": [], "pin": "4590", "isActive": True},
    )
    assert response.status_code == 201
    return response.json()["user"]


def test_admin_users_list_requires_admin_role():
    client, _ = build_client_with_users()
    assert client.post("/api/v1/auth/login", json={"email": "reviewer@symgov.local", "pin": "4590"}).status_code == 200
    assert client.get("/api/v1/admin/users").status_code == 403


def test_new_users_default_to_free_and_admin_can_upgrade_assign_roles_reset_and_deactivate():
    client, Session = build_client_with_users()
    login_admin(client)
    listed = client.get("/api/v1/admin/users")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    user = create_free_user(client, email="new.submitter@symgov.local", display_name="New Submitter")
    assert user["roles"] == []
    assert user["subscription"]["tier"] == "free"
    assert user["subscription"]["expiresOn"] is None

    upgraded = client.post(f"/api/v1/admin/users/{user['id']}/subscription/upgrade", json={"months": 3})
    assert upgraded.status_code == 200
    assert upgraded.json()["user"]["subscription"]["tier"] == "plus"
    assert upgraded.json()["user"]["subscription"]["expiresOn"]

    updated = client.patch(f"/api/v1/admin/users/{user['id']}", json={"roles": ["reviewer", "submitter"], "isActive": False})
    assert updated.status_code == 200
    assert updated.json()["user"]["roles"] == ["reviewer", "submitter"]
    assert updated.json()["user"]["isActive"] is False

    reset = client.post(f"/api/v1/admin/users/{user['id']}/reset-pin", json={"pin": "6781"})
    assert reset.status_code == 200
    with Session() as session:
        assert authenticate_user(session, email=user["email"], pin="6781") is None

    assert client.patch(f"/api/v1/admin/users/{user['id']}", json={"isActive": True}).status_code == 200
    with Session() as session:
        assert authenticate_user(session, email=user["email"], pin="6781") is not None


def test_free_user_cannot_receive_roles_and_create_rejects_roles():
    client, _ = build_client_with_users()
    login_admin(client)
    rejected_create = client.post(
        "/api/v1/admin/users",
        json={"email": "bad@example.com", "displayName": "Bad", "roles": ["admin"], "pin": "4590"},
    )
    assert rejected_create.status_code == 400
    user = create_free_user(client)
    assert client.patch(f"/api/v1/admin/users/{user['id']}", json={"roles": ["reviewer"]}).status_code == 400


def test_upgrade_adjust_cancel_removes_roles_permanently():
    client, _ = build_client_with_users()
    login_admin(client)
    user = create_free_user(client, email="tiered@example.com", display_name="Tiered")
    upgraded = client.post(f"/api/v1/admin/users/{user['id']}/subscription/upgrade", json={"months": 3})
    original_expiry = upgraded.json()["user"]["subscription"]["expiresOn"]
    assert client.patch(f"/api/v1/admin/users/{user['id']}", json={"roles": ["integrator"]}).status_code == 200

    adjusted = client.post(f"/api/v1/admin/users/{user['id']}/subscription/adjust", json={"months": 1})
    assert adjusted.status_code == 200
    assert adjusted.json()["user"]["subscription"]["expiresOn"] != original_expiry

    cancelled = client.post(f"/api/v1/admin/users/{user['id']}/subscription/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["user"]["subscription"]["tier"] == "free"
    assert cancelled.json()["user"]["roles"] == []


def test_admin_can_soft_delete_user_and_revokes_login():
    client, Session = build_client_with_users()
    login_admin(client)
    user = create_free_user(client, email="delete@example.com", display_name="Delete Me")
    removed = client.delete(f"/api/v1/admin/users/{user['id']}")
    assert removed.status_code == 200
    assert removed.json()["user"]["isDeleted"] is True
    assert removed.json()["user"]["subscription"]["tier"] == "free"
    with Session() as session:
        stored = session.get(User, uuid.UUID(user["id"]))
        assert stored.deleted_at is not None
        assert authenticate_user(session, email=user["email"], pin="4590") is None
    assert client.post(f"/api/v1/admin/users/{user['id']}/subscription/upgrade", json={"months": 1}).status_code == 410


def test_protected_owner_cannot_be_cancelled_deactivated_deleted_or_lose_admin():
    client, _ = build_client_with_users()
    login_admin(client)
    owner = next(item for item in client.get("/api/v1/admin/users").json()["items"] if item["email"] == OWNER_EMAIL)
    assert owner["subscription"]["isProtected"] is True
    assert client.post(f"/api/v1/admin/users/{owner['id']}/subscription/cancel").status_code == 400
    assert client.patch(f"/api/v1/admin/users/{owner['id']}", json={"isActive": False}).status_code == 400
    assert client.patch(f"/api/v1/admin/users/{owner['id']}", json={"roles": []}).status_code == 400
    assert client.delete(f"/api/v1/admin/users/{owner['id']}").status_code == 400


def test_user_list_is_paginated_searchable_and_filterable():
    client, _ = build_client_with_users()
    login_admin(client)
    result = client.get("/api/v1/admin/users?page=1&pageSize=1&q=reviewer&tier=plus")
    assert result.status_code == 200
    assert result.json()["page"] == 1
    assert result.json()["pageSize"] == 1
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["email"] == "reviewer@symgov.local"


def test_user_list_reconciles_expiry_before_tier_filter_and_total():
    client, Session = build_client_with_users()
    with Session() as session:
        user = upsert_user(session, email="stale@example.com", display_name="Stale Plus", roles=[], pin="4590")
        upgrade_to_plus(session, user, months=1, as_of=date(2025, 1, 1))
        session.add(UserRole(user_id=user.id, role="reviewer", created_at=user.created_at))
        session.commit()
    login_admin(client)

    plus = client.get("/api/v1/admin/users?q=stale&tier=plus")
    free = client.get("/api/v1/admin/users?q=stale&tier=free")

    assert plus.status_code == 200
    assert plus.json()["total"] == 0
    assert free.json()["total"] == 1
    assert free.json()["items"][0]["roles"] == []


def test_duplicate_email_or_name_is_rejected():
    client, _ = build_client_with_users()
    login_admin(client)
    assert client.post("/api/v1/admin/users", json={"email": OWNER_EMAIL.upper(), "displayName": "Other", "roles": [], "pin": "4590"}).status_code == 409
    assert client.post("/api/v1/admin/users", json={"email": "fresh@example.com", "displayName": "chris", "roles": [], "pin": "4590"}).status_code == 409
