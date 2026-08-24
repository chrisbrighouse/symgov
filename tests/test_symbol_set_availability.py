from __future__ import annotations

import uuid

import pytest

from test_projects_api import _stage4_client
from symgov_backend.models import AuditEvent, Organization, ProjectSymbolSet


def _active_set(client):
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": "SET-01", "name": "Electrical"},
    )
    assert created.status_code == 201
    set_id = created.json()["id"]
    activated = client.patch(
        f"/api/v1/org/me/symbol-sets/{set_id}",
        json={"status": "active"},
    )
    assert activated.status_code == 200
    return set_id


def _project(client):
    created = client.post(
        "/api/v1/org/me/projects",
        json={"code": "P-01", "name": "Project One"},
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_organization_default_requires_project_availability():
    client, Session = _stage4_client()
    set_id = _active_set(client)

    response = client.put(
        "/api/v1/org/me/default-symbol-set",
        json={"setId": set_id},
    )

    assert response.status_code == 409
    with Session() as session:
        organization = session.query(Organization).one()
        assert organization.default_symbol_set_id is None


def test_identical_project_availability_replacement_is_a_no_op():
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)
    payload = {"projects": [{"projectId": project_id, "isDefault": True}]}

    first = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/projects", json=payload)
    assert first.status_code == 200
    with Session() as session:
        first_count = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_availability_replaced"
        ).count()

    second = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/projects", json=payload)
    assert second.status_code == 200
    with Session() as session:
        second_count = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_availability_replaced"
        ).count()

    assert second_count == first_count


def test_omitting_inactive_availability_preserves_history():
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)
    payload = {"projects": [{"projectId": project_id, "isDefault": False}]}
    assert client.put(f"/api/v1/org/me/symbol-sets/{set_id}/projects", json=payload).status_code == 200
    with Session() as session:
        link = session.query(ProjectSymbolSet).filter_by(
            symbol_set_id=uuid.UUID(set_id),
            project_id=uuid.UUID(project_id),
        ).one()
        link.status = "inactive"
        session.commit()

    response = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/projects", json={"projects": []})

    assert response.status_code == 200
    with Session() as session:
        link = session.query(ProjectSymbolSet).filter_by(
            symbol_set_id=uuid.UUID(set_id),
            project_id=uuid.UUID(project_id),
        ).one_or_none()
        assert link is not None
        assert link.status == "inactive"


def test_repeating_organization_default_assignment_is_a_no_op():
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": False}]},
    ).status_code == 200

    first = client.put("/api/v1/org/me/default-symbol-set", json={"setId": set_id})
    assert first.status_code == 200
    with Session() as session:
        first_count = session.query(AuditEvent).filter(
            AuditEvent.action == "organization.symbol_set_default_changed"
        ).count()
    second = client.put("/api/v1/org/me/default-symbol-set", json={"setId": set_id})
    assert second.status_code == 200
    with Session() as session:
        second_count = session.query(AuditEvent).filter(
            AuditEvent.action == "organization.symbol_set_default_changed"
        ).count()

    assert second_count == first_count


@pytest.mark.parametrize("status", ["superseded", "archived"])
def test_terminal_symbol_set_rejects_metadata_patch(status):
    client, Session = _stage4_client()
    set_id = _active_set(client)
    assert client.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": status}).status_code == 200
    response = client.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"name": "Changed"})
    assert response.status_code == 409
    with Session() as session:
        assert session.query(AuditEvent).filter(AuditEvent.action == "symbol_set.updated").count() == 0
