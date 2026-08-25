from __future__ import annotations

import uuid

from symgov_backend.models import AuditEvent, OrganizationRoleAssignment, Project, ProjectSymbolSet, UserProjectSetSelection, UserSession, UserSessionProjectContext
from symgov_backend.auth import hash_session_token
from test_projects_api import _stage4_client
from test_symbol_set_availability import _active_set, _project


def _session_id(client, Session):
    token = client.cookies.get("symgov_session")
    with Session() as session:
        return session.query(UserSession).filter_by(token_hash=hash_session_token(token)).one().id


def _available_set(client, *, code="SET-01", default=False):
    created = client.post("/api/v1/org/me/symbol-sets", json={"code": code, "name": code})
    assert created.status_code == 201
    set_id = created.json()["id"]
    assert client.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": "active"}).status_code == 200
    return set_id


def _make_available(client, set_id, project_id, *, default=False):
    response = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": default}]},
    )
    assert response.status_code == 200


def test_project_context_selection_is_session_scoped_and_zero_set_safe():
    client, Session = _stage4_client()
    project_id = _project(client)
    session_id = _session_id(client, Session)
    with Session() as session:
        active_organization_id = session.get(UserSession, session_id).active_organization_id

    response = client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id})

    assert response.status_code == 200
    assert response.json() == {
        "selectedProject": {"id": project_id, "code": "P-01", "name": "Project One", "shortDescription": None, "status": "active"},
        "activeSet": None,
        "reason": "none",
    }
    with Session() as session:
        assert session.get(UserSessionProjectContext, session_id).project_id == uuid.UUID(project_id)
        assert session.get(UserSession, session_id).active_organization_id == active_organization_id
        event = session.query(AuditEvent).filter_by(action="project.selected").one()
        assert event.payload_json["projectId"] == project_id
        assert "description" not in str(event.payload_json).lower()


def test_context_resolution_precedence_and_explicit_is_response_local():
    client, Session = _stage4_client()
    project_id = _project(client)
    preferred = _available_set(client, code="PREFERRED")
    _make_available(client, preferred, project_id, default=True)
    assert client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id}).json()["reason"] == "project_default"

    selected = client.put("/api/v1/org/me/symbol-context/active-set", json={"setCode": "ＰＲＥＦＥＲＲＥＤ"})
    assert selected.status_code == 200
    assert selected.json()["activeSet"]["id"] == preferred
    assert selected.json()["reason"] == "explicit"
    assert client.get("/api/v1/org/me/symbol-context").json()["reason"] == "user_preference"

    cleared = client.delete("/api/v1/org/me/symbol-context/active-set")
    assert cleared.status_code == 200
    assert cleared.json()["activeSet"]["id"] == preferred
    assert cleared.json()["reason"] == "project_default"
    with Session() as session:
        assert session.query(UserProjectSetSelection).count() == 0
        actions = [row.action for row in session.query(AuditEvent).filter(AuditEvent.action.like("%selected%") | AuditEvent.action.like("%cleared%"))]
        assert "symbol_set.selected" in actions
        assert "symbol_set.selection_cleared" in actions


def test_project_clear_retains_preference_and_returns_empty_204():
    client, Session = _stage4_client()
    project_id = _project(client)
    set_id = _active_set(client)
    _make_available(client, set_id, project_id)
    assert client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id}).status_code == 200
    assert client.put("/api/v1/org/me/symbol-context/active-set", json={"setCode": "SET-01"}).status_code == 200

    response = client.delete("/api/v1/org/me/symbol-context/project")

    assert response.status_code == 204
    assert response.content == b""
    with Session() as session:
        assert session.query(UserSessionProjectContext).count() == 0
        assert session.query(UserProjectSetSelection).count() == 1
        assert session.query(AuditEvent).filter_by(action="project.selection_cleared").count() == 1


def test_context_rejects_missing_selected_project_and_cross_tenant_resources_generically():
    client, _ = _stage4_client()
    missing = client.put("/api/v1/org/me/symbol-context/project", json={"projectId": str(uuid.uuid4())})
    no_project = client.put("/api/v1/org/me/symbol-context/active-set", json={"setCode": "UNKNOWN"})

    assert missing.status_code == 404
    assert missing.json() == {"error": "not_found", "detail": "Not found."}
    assert no_project.status_code == 409
    assert set(no_project.json()) == {"error", "detail"}


def test_active_member_can_select_project_created_by_admin():
    client, Session = _stage4_client()
    project_id = _project(client)
    with Session() as session:
        session.query(OrganizationRoleAssignment).update({"base_role": "user"})
        session.commit()

    response = client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id})

    assert response.status_code == 200
    assert response.json()["selectedProject"]["id"] == project_id


def test_organization_default_fallback_requires_project_availability():
    client, _ = _stage4_client()
    project_id = _project(client)
    set_id = _active_set(client)
    _make_available(client, set_id, project_id)
    assert client.put("/api/v1/org/me/default-symbol-set", json={"setId": set_id}).status_code == 200

    response = client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id})

    assert response.json()["activeSet"]["id"] == set_id
    assert response.json()["reason"] == "organization_default"


def test_get_persists_recovery_from_removed_availability_and_closed_project():
    client, Session = _stage4_client()
    project_id = _project(client)
    set_id = _active_set(client)
    _make_available(client, set_id, project_id)
    assert client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id}).status_code == 200
    assert client.put("/api/v1/org/me/symbol-context/active-set", json={"setCode": "SET-01"}).status_code == 200
    with Session() as session:
        session.query(ProjectSymbolSet).filter_by(project_id=uuid.UUID(project_id)).update({"status": "inactive"})
        session.commit()

    recovered = client.get("/api/v1/org/me/symbol-context")
    assert recovered.status_code == 200
    assert recovered.json()["activeSet"] is None
    with Session() as session:
        assert session.query(UserProjectSetSelection).count() == 0
        session.query(Project).filter_by(id=uuid.UUID(project_id)).update({"status": "closed"})
        session.commit()

    closed = client.get("/api/v1/org/me/symbol-context")
    assert closed.json() == {"selectedProject": None, "activeSet": None, "reason": "none"}
    with Session() as session:
        assert session.query(UserSessionProjectContext).count() == 0


def test_project_put_mutates_only_session_context_and_retains_stale_durable_preference():
    client, Session = _stage4_client()
    project_id = _project(client)
    set_id = _active_set(client)
    _make_available(client, set_id, project_id)
    assert client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id}).status_code == 200
    assert client.put("/api/v1/org/me/symbol-context/active-set", json={"setCode": "SET-01"}).status_code == 200
    assert client.delete("/api/v1/org/me/symbol-context/project").status_code == 204
    with Session() as session:
        session.query(ProjectSymbolSet).filter_by(project_id=uuid.UUID(project_id)).update({"status": "inactive"})
        session.commit()

    selected = client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id})

    assert selected.status_code == 200
    assert selected.json()["activeSet"] is None
    with Session() as session:
        assert session.query(UserProjectSetSelection).count() == 1
