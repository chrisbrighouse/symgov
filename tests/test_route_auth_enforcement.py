from fastapi.testclient import TestClient

from symgov_backend.app import create_app


def test_published_catalog_requires_authenticated_user():
    client = TestClient(create_app())

    response = client.get("/api/v1/published/packs")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_external_submission_requires_submitter_or_admin_user():
    client = TestClient(create_app())

    response = client.post("/api/v1/public/external-submissions", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_workspace_routes_require_authenticated_worker_user():
    client = TestClient(create_app())

    response = client.get("/api/v1/workspace/review-cases")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."
