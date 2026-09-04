from __future__ import annotations

from symgov_backend.symbol_set_service import TRANSITIONS, labels
from types import SimpleNamespace
from collections import Counter
import uuid

import pytest

import symgov_backend.symbol_set_service as symbol_set_service
from symgov_backend.models import AuditEvent


def _client(**kwargs):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_projects_api import _stage4_client
    return _stage4_client(**kwargs)


def test_symbol_set_lifecycle_is_closed_world():
    assert TRANSITIONS == {
        "draft": {"active", "archived"},
        "active": {"superseded", "archived"},
        "superseded": {"archived"},
        "archived": set(),
    }


def test_symbol_set_labels_normalize_dedupe_and_preserve_first_spelling():
    assert labels([" Electrical ", "electrical", "Ｅｌｅｃｔｒｉｃａｌ", "P&ID"]) == ["Electrical", "P&ID"]


def test_symbol_set_patch_applies_other_fields_when_status_is_unchanged(monkeypatch):
    organization = SimpleNamespace(id="organization-id")
    row = SimpleNamespace(status="active", name="Old", id="set-id", owner_organization_id=organization.id, superseded_at=None, archived_at=None, updated_at="before")
    audits = []
    principal = SimpleNamespace(is_admin=True, organization=organization, user=SimpleNamespace(id="user-id"))
    monkeypatch.setattr(symbol_set_service, "get_set", lambda *args, **kwargs: (principal, row))
    monkeypatch.setattr(symbol_set_service, "audit", lambda *args, **kwargs: audits.append(args))
    monkeypatch.setattr(symbol_set_service, "stamp", lambda: "after")
    # This deliberately minimal principal double has no `.user` (unlike the
    # real Stage4Principal) -- record_governance_usage_event (Stage 9 WP9.2)
    # is stubbed here too, mirroring the `audit` stub above, since this test
    # only cares about the "other fields still apply" behavior.
    monkeypatch.setattr(symbol_set_service, "record_governance_usage_event", lambda *args, **kwargs: None)
    class Query:
        def filter(self, *args): return self
        def with_for_update(self): return self
        def one_or_none(self): return row
    session = SimpleNamespace(query=lambda *args: Query())
    data = SimpleNamespace(model_fields_set={"name", "status"}, name="New", description=None,
                           disciplines=None, useCases=None, status="active")
    symbol_set_service.patch_set(session, SimpleNamespace(), SimpleNamespace(), "set-id", data)
    assert row.name == "New"
    assert row.updated_at == "after"
    assert audits


def test_symbol_set_lifecycle_uses_real_fastapi_and_closed_world_transitions():
    client, _ = _client()
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": "SET-01", "name": "Electrical", "disciplines": [" Electrical ", "electrical"]},
    )
    assert created.status_code == 201
    symbol_set_id = created.json()["id"]
    assert created.json()["disciplines"] == ["Electrical"]
    assert client.get("/api/v1/org/me/symbol-sets").json()["items"][0]["status"] == "draft"
    assert client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"status": "active"}).status_code == 200
    assert client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"status": "superseded"}).status_code == 200
    assert client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"status": "archived"}).status_code == 200
    terminal = client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"name": "Nope"})
    assert terminal.status_code == 409
    assert terminal.json() == {"error": "request_error", "detail": "Symbol Set lifecycle transition is not permitted."}


def test_symbol_set_patch_audits_activation_only_for_the_real_transition():
    client, Session = _client()
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": "SET-AUDIT", "name": "Initial"},
    )
    assert created.status_code == 201
    symbol_set_id = created.json()["id"]

    activated = client.patch(
        f"/api/v1/org/me/symbol-sets/{symbol_set_id}",
        json={"status": "active"},
    )
    updated = client.patch(
        f"/api/v1/org/me/symbol-sets/{symbol_set_id}",
        json={"name": "Renamed"},
    )

    assert activated.status_code == updated.status_code == 200
    with Session() as session:
        action_counts = Counter(
            event.action
            for event in session.query(AuditEvent)
            .filter(AuditEvent.entity_id == uuid.UUID(symbol_set_id))
            .all()
        )
    assert action_counts == Counter({
        "symbol_set.created": 1,
        "symbol_set.activated": 1,
        "symbol_set.updated": 1,
    })
    with Session() as session:
        event = session.query(AuditEvent).filter_by(
            entity_id=uuid.UUID(symbol_set_id), action="symbol_set.activated"
        ).one()
        assert event.payload_json["oldStatus"] == "draft"
        assert event.payload_json["newStatus"] == "active"
        assert event.payload_json["changedFields"] == ["status"]
        assert event.payload_json["affectedProjectIds"] == []
        assert event.payload_json["beforeAvailableProjectCount"] == 0
        assert event.payload_json["afterAvailableProjectCount"] == 0


def test_symbol_set_patch_identical_normalized_values_is_a_true_no_op():
    client, Session = _client()
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={
            "code": "SET-NOOP",
            "name": "Electrical Set",
            "description": "Primary symbols",
            "disciplines": ["Electrical", "P&ID"],
            "useCases": ["Design", "Review"],
        },
    )
    assert created.status_code == 201
    symbol_set = created.json()
    symbol_set_id = symbol_set["id"]
    with Session() as session:
        before_updated_at = session.query(symbol_set_service.SymbolSet).filter_by(
            id=uuid.UUID(symbol_set_id)
        ).one().updated_at
        before_audits = session.query(AuditEvent).filter_by(entity_id=uuid.UUID(symbol_set_id)).count()

    response = client.patch(
        f"/api/v1/org/me/symbol-sets/{symbol_set_id}",
        json={
            "name": "  Ｅｌｅｃｔｒｉｃａｌ Ｓｅｔ  ",
            "description": "  Primary symbols  ",
            "disciplines": [" Electrical ", "electrical", "P&ID"],
            "useCases": [" Design ", "design", "Review"],
            "status": "draft",
        },
    )

    assert response.status_code == 200
    with Session() as session:
        after_updated_at = session.query(symbol_set_service.SymbolSet).filter_by(
            id=uuid.UUID(symbol_set_id)
        ).one().updated_at
        after_audits = session.query(AuditEvent).filter_by(entity_id=uuid.UUID(symbol_set_id)).count()
    assert after_updated_at == before_updated_at
    assert after_audits == before_audits


@pytest.mark.parametrize("field", ("disciplines", "useCases", "status"))
def test_symbol_set_patch_rejects_explicit_null_for_non_nullable_fields(field):
    client, _ = _client()
    created = client.post("/api/v1/org/me/symbol-sets", json={"code": "SET-NULL", "name": "Electrical"})
    response = client.patch(
        f"/api/v1/org/me/symbol-sets/{created.json()['id']}",
        json={field: None},
    )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error", "detail", "issues"}
    assert body["error"] == "validation_error"
    assert any(issue["loc"][-1] == field for issue in body["issues"])


def test_symbol_set_patch_explicit_null_clears_nullable_description():
    client, _ = _client()
    created = client.post(
        "/api/v1/org/me/symbol-sets",
        json={"code": "SET-CLEAR", "name": "Electrical", "description": "Initial"},
    )
    response = client.patch(
        f"/api/v1/org/me/symbol-sets/{created.json()['id']}",
        json={"description": None},
    )

    assert response.status_code == 200
    assert response.json()["description"] is None


def test_symbol_set_real_fastapi_member_can_read_active_but_not_mutate():
    client, Session = _client()
    created = client.post("/api/v1/org/me/symbol-sets", json={"code": "SET-01", "name": "Electrical"})
    symbol_set_id = created.json()["id"]
    activated = client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"status": "active"})
    assert activated.status_code == 200
    from symgov_backend.models import OrganizationMembership, OrganizationRoleAssignment, UserSession
    from symgov_backend.auth import hash_session_token
    token = client.cookies.get("symgov_session")
    with Session() as session:
        bound = session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one()
        membership = session.query(OrganizationMembership).filter_by(
            organization_id=bound.active_organization_id, user_id=bound.auth_user_id
        ).one()
        role = session.query(OrganizationRoleAssignment).filter_by(membership_id=membership.id).one()
        role.base_role = "user"
        session.commit()
    assert client.get(f"/api/v1/org/me/symbol-sets/{symbol_set_id}").status_code == 200
    denied = client.patch(f"/api/v1/org/me/symbol-sets/{symbol_set_id}", json={"name": "Nope"})
    assert denied.status_code == 403
    assert denied.json() == {"error": "request_error", "detail": "Organization Admin privileges are required."}


def test_symbol_set_real_fastapi_validation_and_duplicate_conflict_envelopes():
    client, _ = _client()
    invalid = client.post("/api/v1/org/me/symbol-sets", json={"code": "bad code", "name": ""})
    assert invalid.status_code == 422
    assert set(invalid.json()) == {"error", "detail", "issues"}
    created = client.post("/api/v1/org/me/symbol-sets", json={"code": "SET-01", "name": "Electrical"})
    duplicate = client.post("/api/v1/org/me/symbol-sets", json={"code": "SET-01", "name": "Other"})
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert set(duplicate.json()) == {"error", "detail"}
