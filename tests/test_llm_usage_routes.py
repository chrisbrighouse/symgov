from fastapi.testclient import TestClient
import pytest
from symgov_backend.app import create_app


def test_admin_llm_usage_unauthenticated():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/admin/llm/usage")
    assert response.status_code == 401


def test_legacy_admin_llm_usage_unauthenticated():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/admin/llm/usage")
    assert response.status_code == 401
