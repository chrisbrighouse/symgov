from __future__ import annotations

import uuid

from test_projects_api import _stage4_client


CONTEXT = "/api/v1/org/me/symbol-context"


def test_context_routes_use_real_fastapi_and_exact_request_shapes():
    client, _ = _stage4_client()
    project = client.post("/api/v1/org/me/projects", json={"code": "P-API", "name": "API"}).json()

    assert client.get(CONTEXT).json() == {"selectedProject": None, "activeSet": None, "reason": "none"}
    assert client.put(f"{CONTEXT}/project", json={"projectId": project["id"], "extra": True}).status_code == 422
    assert client.put(f"{CONTEXT}/active-set", json={"setCode": "SET", "actorId": str(uuid.uuid4())}).status_code == 422
    assert client.request("DELETE", f"{CONTEXT}/project", json={"projectId": project["id"]}).status_code == 422
    assert client.request("DELETE", f"{CONTEXT}/active-set", json={"setCode": "SET"}).status_code == 422


def test_context_routes_reject_personal_session():
    client, _ = _stage4_client(bind=False)
    response = client.get(CONTEXT)
    assert response.status_code == 403
    assert response.json() == {"error": "request_error", "detail": "An organization-bound session is required."}
