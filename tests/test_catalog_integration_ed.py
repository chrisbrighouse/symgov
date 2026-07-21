from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_api_auth import IntegrationAuthContext, get_catalog_api_key_context
from symgov_backend.dependencies import require_user


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[require_user] = lambda: AuthenticatedUser(
        id="synthetic-user", email="dev@example.invalid", display_name="Dev", roles=("integrator",), must_change_pin=False
    )
    app.dependency_overrides[get_catalog_api_key_context] = lambda: IntegrationAuthContext(
        api_key_id="synthetic-key", customer_name="Synthetic", integration_name="Tests",
        scopes=("catalog.read",), key_prefix="not-returned"
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "message,citation",
    [
        ("How do I authenticate?", "authentication"),
        ("Which scopes do I need?", "authentication"),
        ("Should I use keyword or contextual search?", "search"),
        ("How does cursor pagination work?", "pagination"),
        ("How can I show thumbnails and previews?", "previews"),
        ("How do I download several symbols as PNG?", "downloads"),
        ("What do 401, 403 and validation errors mean?", "errors"),
        ("How does the safe sandbox work?", "sandbox"),
        ("How do I submit feedback?", "feedback"),
        ("Show me a Python example", "examples"),
    ],
)
def test_integration_ed_answers_documented_topics_with_citations_and_followups(client, message, citation):
    response = client.post("/api/v1/catalog/developer/ed", json={"message": message})

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]
    assert any(citation in item for item in payload["citations"])
    assert payload["suggestedFollowups"]
    assert payload["stateless"] is True
    assert payload["conversationMemory"] is False
    assert payload["standardsApproval"] is False
    assert payload["networkCalls"] is False
    if "example" in message.lower():
        assert payload["code"]
        assert "Authorization" in payload["code"]
        assert "actual-key" not in payload["code"]


@pytest.mark.parametrize(
    "message",
    [
        "Authorization: Bearer opaque-secret-value",
        "api_key=super-secret",
        "symgov_live_abcdefghijklmnopqrstuvwxyz",
        "password=hunter2",
    ],
)
def test_integration_ed_rejects_credentials_without_echoing_them(client, message):
    response = client.post("/api/v1/catalog/developer/ed", json={"message": message})

    assert response.status_code == 400
    assert "opaque-secret-value" not in str(response.json())
    assert "super-secret" not in str(response.json())
    assert "abcdefghijklmnopqrstuvwxyz" not in str(response.json())
    assert "hunter2" not in str(response.json())


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "x" * 2001}, {"message": 7}, {"message": "hello", "conversationId": "memory"}])
def test_integration_ed_rejects_invalid_or_stateful_requests(client, body):
    response = client.post("/api/v1/catalog/developer/ed", json=body)

    assert response.status_code == 400


def test_integration_ed_routes_unresolved_questions_to_support(client):
    response = client.post("/api/v1/catalog/developer/ed", json={"message": "Does this certify IEC compliance in Antarctica?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["resolved"] is False
    assert payload["supportRoute"] == "/support"
    assert "/support" in payload["answer"]
    assert payload["citations"] == ["developer://support"]


def test_integration_ed_rejects_oversized_body(client):
    response = client.post(
        "/api/v1/catalog/developer/ed",
        headers={"content-type": "application/json"},
        content='{"message":"' + ("x" * 17000) + '"}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body is too large."


@pytest.mark.parametrize(
    "message",
    [
        'Here is JSON: {"apiKey":"actual-secret-value"}',
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIAZZZZZZZZZZZZZZZZ",
        "postgresql://user:password@db/catalog",
    ],
)
def test_integration_ed_rejects_structured_and_provider_credentials(client, message):
    response = client.post("/api/v1/catalog/developer/ed", json={"message": message})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "message",
    [
        "Show Authorization: Bearer <CATALOG_API_KEY> documentation",
        "How do I set api_key=<YOUR_API_KEY>?",
        "Is AKIAIOSFODNN7EXAMPLE an example AWS key?",
        "Document postgresql://user:<PASSWORD>@db/catalog",
    ],
)
def test_integration_ed_allows_obvious_documentation_placeholders(client, message):
    response = client.post("/api/v1/catalog/developer/ed", json={"message": message})
    assert response.status_code == 200
