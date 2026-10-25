"""Unified submission & review WP6: in-app notifications for the private symbol
workflow, through the real app on the in-memory SQLite client from
`test_wp55_organization_symbols_api.py`.

Submit and withdraw notify the organization's Org Admins and Org Reviewers
(never the submitter); a decision notifies the submitter unless they made it
themselves. Reads are scoped to the reader, their active organization and a
still-active membership. Everything is absent while the v2 flag is off.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    UserNotification,
)
from symgov_backend.settings import get_settings  # noqa: E402
from symgov_backend.user_notifications import list_notifications  # noqa: E402
from test_wp55_organization_symbols_api import _add_membership, _build_client  # noqa: E402

DRAFT = {"name": "Fire hydrant", "category": "Safety Devices", "discipline": "civil", "summary": "A fire hydrant symbol."}


def _join(Session, organization_id, email, name, *, base_role="user", capabilities=()):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        user = upsert_user(session, email=email, display_name=name, roles=[], pin="1234", must_change_pin=False)
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=organization_id, user_id=user.id, status="active",
            activated_at=now, created_at=now, updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(OrganizationRoleAssignment(
            id=uuid.uuid4(), membership_id=membership.id, base_role=base_role, is_active=True, assigned_at=now, revoked_at=None,
        ))
        for capability in capabilities:
            session.add(OrganizationMemberCapability(
                id=uuid.uuid4(), membership_id=membership.id, capability=capability, is_active=True, granted_at=now,
            ))
        session.commit()
        return user.id


def _world(*, workflow_v2=True):
    """ACME: Member (contributor, the submitter), Rex (Org Reviewer), Ada (Org
    Admin) and Pat (plain member). Olly belongs to another organization."""
    client, Session, member_id = _build_client()
    settings = client.app.dependency_overrides[get_settings]()
    client.app.dependency_overrides[get_settings] = lambda: replace(settings, organization_symbol_workflow_v2_enabled=workflow_v2)
    acme_id = _add_membership(Session, member_id, "acme", capabilities=("contributor",))
    ids = {
        "member": member_id,
        "rex": _join(Session, acme_id, "rex@example.test", "Rex", capabilities=("symbol_reviewer",)),
        "ada": _join(Session, acme_id, "ada@example.test", "Ada", base_role="admin"),
        "pat": _join(Session, acme_id, "pat@example.test", "Pat"),
    }
    other_id = _add_membership(Session, member_id, "other")  # a second org for Member; Member logs in to ACME below
    ids["olly"] = _join(Session, other_id, "olly@example.test", "Olly", capabilities=("symbol_reviewer",))
    with Session() as session:  # keep Member to one organization so login lands in ACME
        session.query(OrganizationMembership).filter_by(organization_id=other_id, user_id=member_id).delete()
        session.commit()

    def login(email):
        user_client = TestClient(client.app, headers={"origin": "http://testserver"})
        response = user_client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
        assert response.status_code == 200, response.text
        return user_client

    clients = {name: login(f"{name}@example.test") for name in ("rex", "ada", "pat", "olly")}
    clients["member"] = login("member@example.test")
    return clients, Session, ids, acme_id


def _kinds(user_client):
    response = user_client.get("/api/v1/notifications")
    assert response.status_code == 200, response.text
    return [item["kind"] for item in response.json()["items"]]


def _submit(member):
    draft = member.post("/api/v1/organization-symbols", json=DRAFT).json()
    base = f"/api/v1/organization-symbols/{draft['id']}"
    submitted = member.post(f"{base}/revisions/{draft['currentRevisionId']}/submit", json={"rationale": "Ready."})
    assert submitted.status_code == 200, submitted.text
    return base, submitted.json()["id"]


def test_a_submission_notifies_the_organizations_reviewers_and_admins_only():
    clients, _, _, _ = _world()
    _submit(clients["member"])
    assert _kinds(clients["rex"]) == ["org_symbol.submitted"]
    assert _kinds(clients["ada"]) == ["org_symbol.submitted"]
    assert _kinds(clients["member"]) == []
    assert _kinds(clients["pat"]) == []
    assert _kinds(clients["olly"]) == []

    [item] = clients["rex"].get("/api/v1/notifications").json()["items"]
    assert (item["symbolName"], item["actorName"], item["readAt"]) == ("Fire hydrant", "Member", None)


def test_a_decision_notifies_the_submitter_with_the_rationale_and_a_withdrawal_notifies_reviewers():
    clients, _, _, _ = _world()
    base, submission_id = _submit(clients["member"])
    decided = clients["rex"].post(
        f"{base}/review-submissions/{submission_id}/decision", json={"decision": "changes_requested", "rationale": "Add the flange."}
    )
    assert decided.status_code == 200, decided.text
    [notice] = clients["member"].get("/api/v1/notifications").json()["items"]
    assert (notice["kind"], notice["actorName"], notice["rationale"]) == ("org_symbol.changes_requested", "Rex", "Add the flange.")

    # A second symbol for the withdrawal: SQLite cannot model the partial
    # unique index that lets PostgreSQL resubmit a decided revision.
    other_base, other_submission = _submit(clients["member"])
    assert clients["member"].post(f"{other_base}/review-submissions/{other_submission}/withdraw").status_code == 200
    assert _kinds(clients["rex"]) == ["org_symbol.withdrawn", "org_symbol.submitted", "org_symbol.submitted"]
    assert _kinds(clients["member"]) == ["org_symbol.changes_requested"]


def test_self_approval_sends_the_submitter_nothing():
    clients, _, _, _ = _world()
    base, submission_id = _submit(clients["member"])
    approved = clients["member"].post(f"{base}/review-submissions/{submission_id}/decision", json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    assert _kinds(clients["member"]) == []


def test_reading_marks_only_the_readers_own_notices():
    clients, _, _, _ = _world()
    _submit(clients["member"])
    _submit(clients["member"])
    rex = clients["rex"]
    first, second = rex.get("/api/v1/notifications").json()["items"]
    assert rex.get("/api/v1/notifications").json()["unreadCount"] == 2

    assert clients["ada"].post(f"/api/v1/notifications/{first['id']}/read").status_code == 404
    assert rex.post("/api/v1/notifications/not-a-uuid/read").status_code == 404
    assert rex.post(f"/api/v1/notifications/{first['id']}/read").status_code == 204
    assert rex.get("/api/v1/notifications").json()["unreadCount"] == 1
    assert clients["ada"].get("/api/v1/notifications").json()["unreadCount"] == 2

    assert rex.post("/api/v1/notifications/read-all").status_code == 204
    body = rex.get("/api/v1/notifications").json()
    assert body["unreadCount"] == 0 and all(item["readAt"] for item in body["items"])


def test_a_member_who_leaves_stops_seeing_the_organizations_notices():
    clients, Session, ids, acme_id = _world()
    _submit(clients["member"])
    with Session() as session:
        assert list_notifications(session, ids["rex"], acme_id).unread_count == 1
        membership = session.query(OrganizationMembership).filter_by(organization_id=acme_id, user_id=ids["rex"]).one()
        membership.status = "deactivated"
        session.commit()
        page = list_notifications(session, ids["rex"], acme_id)
        assert (page.items, page.unread_count) == ([], 0)


def test_notifications_are_absent_and_never_written_while_the_flag_is_off():
    clients, Session, _, _ = _world(workflow_v2=False)
    _submit(clients["member"])
    assert clients["rex"].get("/api/v1/notifications").status_code == 404
    with Session() as session:
        assert session.query(UserNotification).count() == 0
