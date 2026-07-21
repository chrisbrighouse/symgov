from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.subscriptions import upgrade_to_plus


def build_client_with_users():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with Session() as session:
        admin = upsert_user(
            session,
            email="admin@symgov.local",
            display_name="Alfi",
            roles=[],
            pin="4590",
        )
        reviewer = upsert_user(
            session,
            email="reviewer@symgov.local",
            display_name="Rupert",
            roles=[],
            pin="4590",
        )
        upgrade_to_plus(session, admin, months=12)
        upgrade_to_plus(session, reviewer, months=12)
        for role in ("admin", "submitter", "reviewer"):
            session.add(UserRole(user_id=admin.id, role=role, created_at=admin.created_at))
        session.add(UserRole(user_id=reviewer.id, role="reviewer", created_at=reviewer.created_at))
        session.commit()

    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def login(client: TestClient, email: str):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "4590"})
    assert response.status_code == 200


def test_admin_llm_settings_require_admin_role(tmp_path, monkeypatch):
    client = build_client_with_users()
    monkeypatch.setenv("SYMGOV_LLM_SETTINGS_PATH", str(tmp_path / "llm-settings.json"))

    login(client, "reviewer@symgov.local")
    response = client.get("/api/v1/admin/llm/settings")
    assert response.status_code == 403


def test_admin_can_get_and_update_llm_settings(tmp_path, monkeypatch):
    client = build_client_with_users()
    settings_path = tmp_path / "llm-settings.json"
    monkeypatch.setenv("SYMGOV_LLM_SETTINGS_PATH", str(settings_path))

    login(client, "admin@symgov.local")

    get_result = client.get("/api/v1/admin/llm/settings")
    assert get_result.status_code == 200
    assert get_result.json()["provider"] == "openrouter"

    patch_result = client.patch(
        "/api/v1/admin/llm/settings",
        json={
            "provider": "openrouter",
            "defaultModel": "openai/gpt-4o-mini",
            "featureModels": {"edConcierge": "openai/gpt-4.1-mini"},
        },
    )
    assert patch_result.status_code == 200
    assert patch_result.json()["defaultModel"] == "openai/gpt-4o-mini"
    assert patch_result.json()["featureModels"]["edConcierge"] == "openai/gpt-4.1-mini"
    assert settings_path.exists()


def test_admin_openrouter_model_list_and_test_endpoint(tmp_path, monkeypatch):
    client = build_client_with_users()
    monkeypatch.setenv("SYMGOV_LLM_SETTINGS_PATH", str(tmp_path / "llm-settings.json"))

    from symgov_backend.routes import llm as llm_routes

    monkeypatch.setattr(
        llm_routes,
        "fetch_openrouter_models",
        lambda: [{"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "contextLength": 128000}],
    )
    monkeypatch.setattr(
        llm_routes,
        "request_openrouter_completion",
        lambda **_: {
            "provider": "openrouter",
            "model": "openai/gpt-4o-mini",
            "outputText": "ok",
            "latencyMs": 12,
            "usage": {"total_tokens": 7},
        },
    )

    login(client, "admin@symgov.local")

    model_list = client.get("/api/v1/admin/llm/openrouter-models")
    assert model_list.status_code == 200
    assert model_list.json()["items"][0]["id"] == "openai/gpt-4o-mini"

    test_result = client.post(
        "/api/v1/admin/llm/test",
        json={"prompt": "hello", "model": "openai/gpt-4o-mini"},
    )
    assert test_result.status_code == 200
    assert test_result.json()["outputText"] == "ok"


def test_authenticated_users_can_call_llm_chat(tmp_path, monkeypatch):
    client = build_client_with_users()
    monkeypatch.setenv("SYMGOV_LLM_SETTINGS_PATH", str(tmp_path / "llm-settings.json"))

    from symgov_backend.routes import llm as llm_routes

    monkeypatch.setattr(
        llm_routes,
        "request_openrouter_completion",
        lambda **_: {
            "provider": "openrouter",
            "model": "openai/gpt-4o-mini",
            "outputText": "assistant output",
            "latencyMs": 15,
            "usage": {"total_tokens": 10},
        },
    )

    login(client, "reviewer@symgov.local")

    response = client.post("/api/v1/llm/chat", json={"prompt": "ping", "feature": "edConcierge"})
    assert response.status_code == 200
    assert response.json()["outputText"] == "assistant output"
