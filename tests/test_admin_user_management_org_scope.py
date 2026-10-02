"""User management from a customer organization session.

The global `admin` role is not scoped to an organization. Inside a session
bound to a customer organization, user management narrows to that
organization's members, and platform administration (Workspace, LLM, account
creation, subscriptions, removal) is refused even to holders of that role.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import authenticate_user, upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import OrganizationMembership, OrganizationRoleAssignment
from symgov_backend.settings import SymgovAPISettings, get_settings
from symgov_backend.subscriptions import upgrade_to_plus

from test_organization_admin_api import _add_org_with_members, _build_engine_and_session, _login_and_select_org, _step_up


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    with patch("symgov_backend.organization_service._emit_audit"):
        yield


def _user(session, email, name, *, roles=()):
    user = upsert_user(session, email=email, display_name=name, roles=[], pin="1234", must_change_pin=False)
    if roles:
        upgrade_to_plus(session, user, months=12)
        upsert_user(session, email=email, display_name=name, roles=list(roles), pin="1234", must_change_pin=False)
    return user.id


def _add_membership(Session, organization_id, user_id, *, base_role="user"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=organization_id, user_id=user_id, status="active",
            activated_at=now, created_at=now, updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(OrganizationRoleAssignment(
            id=uuid.uuid4(), membership_id=membership.id, base_role=base_role, is_active=True, assigned_at=now,
        ))
        session.commit()


@pytest.fixture
def world():
    Session = _build_engine_and_session()
    with Session() as session:
        ids = {
            "admin": _user(session, "admin@acme.test", "Ada Admin", roles=["admin"]),
            "member": _user(session, "member@acme.test", "Mia Member"),
            "shared": _user(session, "shared@acme.test", "Sam Shared"),
            "outsider": _user(session, "outsider@globex.test", "Otto Outsider"),
        }
        session.commit()
    acme_id, _, _ = _add_org_with_members(Session, ids["admin"], ids["member"], code="ACME")
    globex_id, _, _ = _add_org_with_members(Session, ids["outsider"], code="GLOBEX")
    _add_membership(Session, acme_id, ids["shared"])
    _add_membership(Session, globex_id, ids["shared"])

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        symbol_sets_enabled=False,
        organization_symbols_enabled=False,
        organization_agents_enabled=False,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    return client, Session, ids, acme_id, globex_id


def _listed_emails(client):
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 200, response.text
    return sorted(item["email"] for item in response.json()["items"]), response.json()["total"]


def test_org_admin_lists_only_their_own_organization_members(world):
    client, _, _, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)

    emails, total = _listed_emails(client)

    assert emails == ["admin@acme.test", "member@acme.test", "shared@acme.test"]
    assert total == 3


def test_org_admin_without_the_global_admin_role_can_still_list_members(world):
    client, Session, _, acme_id, globex_id = world
    _login_and_select_org(client, "outsider@globex.test", globex_id)

    emails, _ = _listed_emails(client)

    assert emails == ["outsider@globex.test", "shared@acme.test"]


def test_org_member_without_org_admin_cannot_manage_users(world):
    client, _, _, acme_id, _ = world
    _login_and_select_org(client, "member@acme.test", acme_id)

    assert client.get("/api/v1/admin/users").status_code == 403


def test_platform_administration_is_refused_in_a_customer_organization_session(world):
    client, _, ids, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)
    _step_up(client)

    assert client.get("/api/v1/admin/llm/settings").status_code == 403
    assert client.get("/api/v1/admin/llm/usage").status_code == 403
    assert client.get("/api/v1/workspace/agent-worker-health").status_code == 403
    created = client.post(
        "/api/v1/admin/users",
        json={"email": "new@acme.test", "displayName": "New", "roles": [], "pin": "4590", "isActive": True},
    )
    assert created.status_code == 403
    member = ids["member"]
    assert client.post(f"/api/v1/admin/users/{member}/subscription/upgrade", json={"months": 1}).status_code == 403
    assert client.post(f"/api/v1/admin/users/{member}/subscription/cancel").status_code == 403
    assert client.delete(f"/api/v1/admin/users/{member}").status_code == 403


def test_org_admin_can_deactivate_and_reset_pin_for_their_own_members(world):
    client, Session, ids, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)
    _step_up(client)
    member = ids["member"]

    deactivated = client.patch(f"/api/v1/admin/users/{member}", json={"isActive": False})
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["user"]["isActive"] is False
    assert client.patch(f"/api/v1/admin/users/{member}", json={"isActive": True}).status_code == 200

    reset = client.post(f"/api/v1/admin/users/{member}/reset-pin", json={"pin": "6781"})
    assert reset.status_code == 200, reset.text
    with Session() as session:
        assert authenticate_user(session, email="member@acme.test", pin="6781") is not None


def test_org_admin_cannot_change_roles_or_display_names(world):
    client, _, ids, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)
    _step_up(client)
    member = ids["member"]

    assert client.patch(f"/api/v1/admin/users/{member}", json={"roles": ["admin"]}).status_code == 403
    assert client.patch(f"/api/v1/admin/users/{member}", json={"displayName": "Renamed"}).status_code == 403


def test_org_admin_cannot_reach_accounts_outside_or_shared_with_another_organization(world):
    client, _, ids, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)
    _step_up(client)

    assert client.patch(f"/api/v1/admin/users/{ids['outsider']}", json={"isActive": False}).status_code == 404
    assert client.post(f"/api/v1/admin/users/{ids['outsider']}/reset-pin", json={"pin": "6781"}).status_code == 404
    shared = client.patch(f"/api/v1/admin/users/{ids['shared']}", json={"isActive": False})
    assert shared.status_code == 403
    assert "another organization" in shared.json()["detail"]
    assert client.post(f"/api/v1/admin/users/{ids['shared']}/reset-pin", json={"pin": "6781"}).status_code == 403


def test_org_admin_cannot_deactivate_themselves(world):
    client, _, ids, acme_id, _ = world
    _login_and_select_org(client, "admin@acme.test", acme_id)
    _step_up(client)

    assert client.patch(f"/api/v1/admin/users/{ids['admin']}", json={"isActive": False}).status_code == 400
