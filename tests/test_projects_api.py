from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
from sqlalchemy import CheckConstraint


import symgov_backend.project_service as project_service
from symgov_backend.auth import hash_session_token
from symgov_backend.models import Organization, OrganizationMembership, OrganizationRoleAssignment, UserSession
from symgov_backend.settings import get_settings

from symgov_backend.project_service import normalize_code, normalize_text, validate_json


def _stage4_client(*, enabled=True, role="admin", pilots=("acme",), bind=True):
    from sqlalchemy import JSON
    from symgov_backend.models import AuditEvent, Project, ProjectSymbolSet, SymbolSet, UserProjectSetSelection, UserSessionProjectContext
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_organization_auth_context import _add_membership, _build_client, _login

    client, Session, user_id, settings = _build_client(enabled=enabled, pilots=pilots)
    for model in (SymbolSet, Project, ProjectSymbolSet, UserProjectSetSelection, UserSessionProjectContext, AuditEvent):
        table = model.__table__
        original = table.constraints
        original_types = {column.name: column.type for column in table.columns}
        original_defaults = {column.name: column.server_default for column in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item for item in original
                if not (isinstance(item, CheckConstraint) and ("~" in str(item.sqltext) or "jsonb" in str(item.sqltext) or "char_length" in str(item.sqltext) or "::" in str(item.sqltext) or "convert_to" in str(item.sqltext)))
            }
            table.create(Session.kw["bind"] if "bind" in Session.kw else Session.get_bind())
        finally:
            table.constraints = original
            for column in table.columns:
                column.type = original_types[column.name]
                column.server_default = original_defaults[column.name]
    if bind:
        _add_membership(Session, user_id, "acme", base_role=role)
    _login(client)
    client._stage4_settings = settings
    return client, Session


def _bound_session(client, Session):
    token = client.cookies.get("symgov_session")
    with Session() as session:
        return session.query(UserSession).filter(UserSession.token_hash == hash_session_token(token)).one().id


def _login(client):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_organization_auth_context import _login as login
    return login(client)


def _set_bound_session(client, Session, **changes):
    session_id = _bound_session(client, Session)
    with Session() as session:
        row = session.get(UserSession, session_id)
        for name, value in changes.items():
            setattr(row, name, value)
        session.commit()


def test_project_code_is_immutable_grammar_and_lowercase_key():
    assert normalize_code("P-01") == ("P-01", "p-01")
    with pytest.raises(ValueError):
        normalize_code("bad code")


def test_project_short_description_uses_unicode_code_points():
    assert len("😀" * 50) == 50
    assert len("😀" * 51) == 51
    assert normalize_text("  NFKC name  ", "Name", 200) == "NFKC name"


def test_project_metadata_is_bounded_and_finite():
    assert validate_json({"nested": [True, None, 1.5]})["nested"][0] is True
    with pytest.raises(ValueError):
        validate_json({"value": float("nan")})
    with pytest.raises(ValueError):
        validate_json({"a": {"b": {"c": {"d": {"e": 1}}}}})


def test_project_patch_applies_other_fields_when_status_is_unchanged(monkeypatch):
    row = SimpleNamespace(status="active", name="Old", id="project-id", closed_at=None, updated_at="before")
    audits = []
    monkeypatch.setattr(project_service, "get_project", lambda *args, **kwargs: (SimpleNamespace(is_admin=True), row))
    monkeypatch.setattr(project_service, "audit", lambda *args, **kwargs: audits.append(args))
    monkeypatch.setattr(project_service, "now", lambda: "after")
    data = SimpleNamespace(model_fields_set={"name", "status"}, name="New", shortDescription=None,
                           externalReference=None, metadata=None, status="active")
    project_service.patch_project(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), "project-id", data)
    assert row.name == "New"
    assert row.updated_at == "after"
    assert audits


def test_project_lifecycle_uses_real_fastapi_and_bound_organization_session():
    client, Session = _stage4_client()

    created = client.post("/api/v1/org/me/projects", json={"code": "P-01", "name": "First"})
    assert created.status_code == 201
    project = created.json()
    assert project["code"] == "P-01"
    project_id = project["id"]

    listed = client.get("/api/v1/org/me/projects?page=1&pageSize=10")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [project_id]
    detail = client.get(f"/api/v1/org/me/projects/{project_id}")
    assert detail.status_code == 200
    updated = client.patch(f"/api/v1/org/me/projects/{project_id}", json={"name": "Renamed"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    closed = client.patch(f"/api/v1/org/me/projects/{project_id}", json={"status": "closed"})
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert client.get(f"/api/v1/org/me/projects/{project_id}").json()["status"] == "closed"
    with Session() as session:
        assert session.query(project_service.AuditEvent).count() == 3


@pytest.mark.parametrize(
    ("enabled", "bind", "expected"),
    ((False, True, 404), (True, False, 403)),
    ids=("feature-off", "personal-session"),
)
def test_project_route_rejects_real_non_organization_contexts(enabled, bind, expected):
    client, _ = _stage4_client(enabled=enabled, bind=bind)
    response = client.get("/api/v1/org/me/projects")
    assert response.status_code == expected
    assert set(response.json()) == {"error", "detail"}


def test_project_route_rejects_missing_session_with_real_fastapi():
    client, _ = _stage4_client()
    client.cookies.clear()
    response = client.get("/api/v1/org/me/projects")
    assert response.status_code == 401
    assert response.json() == {"error": "request_error", "detail": "Authentication required."}


def test_project_member_cannot_mutate_but_can_list_through_real_fastapi():
    client, _ = _stage4_client(role="user")
    assert client.get("/api/v1/org/me/projects").status_code == 200
    response = client.post("/api/v1/org/me/projects", json={"code": "P-01", "name": "First"})
    assert response.status_code == 403


def test_project_validation_uses_real_fastapi_error_envelope():
    client, _ = _stage4_client()
    response = client.post("/api/v1/org/me/projects", json={"code": "bad code", "name": ""})
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error", "detail", "issues"}
    assert body["error"] == "validation_error"


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("expired", 401),
        ("revoked", 401),
        ("non_pilot", 404),
        ("ineligible_entitlement", 404),
        ("inactive_organization", 404),
        ("inactive_membership", 404),
        ("inactive_role", 404),
    ),
)
def test_project_real_fastapi_revalidates_each_bound_authority_state(case, expected):
    client, Session = _stage4_client()
    _login(client)
    with Session() as session:
        bound = session.get(UserSession, _bound_session(client, Session))
        organization = session.get(Organization, bound.active_organization_id)
        membership = session.query(OrganizationMembership).filter_by(
            organization_id=organization.id, user_id=bound.auth_user_id
        ).one()
        role = session.query(OrganizationRoleAssignment).filter_by(membership_id=membership.id).one()
        if case == "expired":
            bound.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        elif case == "revoked":
            bound.revoked_at = datetime.now(timezone.utc)
        elif case == "ineligible_entitlement":
            organization.entitlement_status = "suspended"
        elif case == "inactive_organization":
            organization.is_active = False
        elif case == "inactive_membership":
            membership.status = "inactive"
        elif case == "inactive_role":
            role.is_active = False
            role.revoked_at = datetime.now(timezone.utc)
        session.commit()
    if case == "non_pilot":
        client.app.dependency_overrides[get_settings] = lambda: replace(
            client._stage4_settings, organization_pilot_codes=("other",)
        )
    response = client.get("/api/v1/org/me/projects")
    assert response.status_code == expected
    assert set(response.json()) == {"error", "detail"}


def test_project_real_fastapi_hides_other_organization_detail_and_mutation():
    client, Session = _stage4_client(pilots=("acme", "other"))
    _login(client)
    created = client.post("/api/v1/org/me/projects", json={"code": "P-01", "name": "First"})
    project_id = created.json()["id"]
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_organization_auth_context import _add_membership
    other_id = _add_membership(Session, _bound_session_user(Session, client), "other")
    _set_bound_session(client, Session, active_organization_id=other_id)
    detail = client.get(f"/api/v1/org/me/projects/{project_id}")
    update = client.patch(f"/api/v1/org/me/projects/{project_id}", json={"name": "Hidden"})
    assert detail.status_code == update.status_code == 404
    assert detail.json() == {"error": "not_found", "detail": "Not found."}
    assert update.json() == {"error": "not_found", "detail": "Not found."}


def _bound_session_user(Session, client):
    with Session() as session:
        return session.get(UserSession, _bound_session(client, Session)).auth_user_id
