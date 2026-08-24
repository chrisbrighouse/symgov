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
        first_default_count = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_default_changed"
        ).count()

    second = client.put(f"/api/v1/org/me/symbol-sets/{set_id}/projects", json=payload)
    assert second.status_code == 200
    with Session() as session:
        second_count = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_availability_replaced"
        ).count()
        second_default_count = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_default_changed"
        ).count()

    assert second_count == first_count
    assert second_default_count == first_default_count


def test_availability_replacement_audits_each_project_default_change_with_counts():
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)

    changed = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    )

    assert changed.status_code == 200
    with Session() as session:
        events = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_default_changed"
        ).order_by(AuditEvent.created_at, AuditEvent.id).all()
        latest = events[-1]
        assert latest.entity_type == "project"
        assert str(latest.entity_id) == project_id
        assert latest.payload_json["projectId"] == project_id
        assert latest.payload_json["symbolSetId"] == set_id
        assert latest.payload_json["oldDefaultSymbolSetId"] is None
        assert latest.payload_json["newDefaultSymbolSetId"] == set_id
        assert latest.payload_json["beforeAvailableSymbolSetCount"] == 0
        assert latest.payload_json["afterAvailableSymbolSetCount"] == 1

        availability = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_availability_replaced",
            AuditEvent.entity_id == uuid.UUID(set_id),
        ).one()
        assert availability.payload_json["affectedProjectIds"] == [project_id]
        assert availability.payload_json["beforeProjectCount"] == 0
        assert availability.payload_json["afterProjectCount"] == 1


def test_availability_removal_audits_default_clear_with_counts():
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    ).status_code == 200

    removed = client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": []},
    )

    assert removed.status_code == 200
    with Session() as session:
        events = session.query(AuditEvent).filter(
            AuditEvent.action == "symbol_set.project_default_changed"
        ).all()
        cleared = [
            event for event in events
            if event.payload_json["oldDefaultSymbolSetId"] == set_id
            and event.payload_json["newDefaultSymbolSetId"] is None
        ]
        assert len(cleared) == 1
        assert cleared[0].payload_json["projectId"] == project_id
        assert cleared[0].payload_json["beforeAvailableSymbolSetCount"] == 1
        assert cleared[0].payload_json["afterAvailableSymbolSetCount"] == 0


@pytest.mark.parametrize("terminal_status", ["superseded", "archived"])
def test_terminal_transition_audits_lifecycle_and_each_default_cleanup(terminal_status):
    client, Session = _stage4_client()
    set_id = _active_set(client)
    project_id = _project(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    ).status_code == 200
    assert client.put(
        "/api/v1/org/me/default-symbol-set", json={"setId": set_id}
    ).status_code == 200

    changed = client.patch(
        f"/api/v1/org/me/symbol-sets/{set_id}",
        json={"status": terminal_status},
    )

    assert changed.status_code == 200
    with Session() as session:
        lifecycle = session.query(AuditEvent).filter_by(
            entity_id=uuid.UUID(set_id), action=f"symbol_set.{terminal_status}"
        ).one()
        assert lifecycle.payload_json == {
            "source": "stage4",
            "organizationId": lifecycle.payload_json["organizationId"],
            "symbolSetId": set_id,
            "oldStatus": "active",
            "newStatus": terminal_status,
            "changedFields": ["status"],
            "affectedProjectIds": [project_id],
            "beforeAvailableProjectCount": 1,
            "afterAvailableProjectCount": 0,
        }

        project_defaults = session.query(AuditEvent).filter_by(
            entity_id=uuid.UUID(project_id),
            action="symbol_set.project_default_changed",
        ).all()
        project_default = next(
            event for event in project_defaults
            if event.payload_json["oldDefaultSymbolSetId"] == set_id
            and event.payload_json["newDefaultSymbolSetId"] is None
        )
        assert project_default.actor_id == lifecycle.actor_id
        assert project_default.payload_json["oldDefaultSymbolSetId"] == set_id
        assert project_default.payload_json["newDefaultSymbolSetId"] is None
        assert project_default.payload_json["affectedSymbolSetIds"] == [set_id]
        assert project_default.payload_json["beforeAvailableSymbolSetCount"] == 1
        assert project_default.payload_json["afterAvailableSymbolSetCount"] == 0

        organization_defaults = session.query(AuditEvent).filter_by(
            action="organization.symbol_set_default_changed"
        ).all()
        organization_default = next(
            event for event in organization_defaults
            if event.payload_json["oldDefaultSymbolSetId"] == set_id
            and event.payload_json["newDefaultSymbolSetId"] is None
        )
        assert organization_default.actor_id == lifecycle.actor_id
        assert organization_default.payload_json["oldDefaultSymbolSetId"] == set_id
        assert organization_default.payload_json["newDefaultSymbolSetId"] is None
        assert organization_default.payload_json["affectedProjectIds"] == [project_id]
        assert organization_default.payload_json["beforeAvailableProjectCount"] == 1
        assert organization_default.payload_json["afterAvailableProjectCount"] == 0


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
