"""Org roles and public roles are separate grants (unified submission spec, draft 2, §6).

- Org Submitter / Org Reviewer are the per-membership capabilities `contributor`
  and `symbol_reviewer`. They unlock the organization's private workflow only.
- Public Submitter / Public Reviewer are the `submitter` and `reviewer` roles
  (personal Plus or organization plan roles). They unlock public catalogue work
  in every session, including a customer organization session.
- The global `admin` role is not scoped to an organization, so inside a
  customer organization session it unlocks nothing on the public side.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import (
    public_workflow_roles,
    require_public_submitter,
    require_workspace_access,
)
from symgov_backend.organization_symbol_review import OrganizationSymbolReviewError, _require_reviewer
from symgov_backend.promotion_requests import PromotionRequestError, _require_reviewer_authority

ACME_ID = "6f1c2a4e-0000-4000-8000-00000000ac3e"


def _user(*roles, mode="customer", capabilities=()):
    sessions = {
        "personal": dict(session_mode="personal"),
        "customer": dict(session_mode="organization", active_organization_id=ACME_ID, organization_code="ACME"),
        "platform": dict(session_mode="organization", active_organization_id=ACME_ID, organization_code="symgov"),
    }
    return AuthenticatedUser(
        id="00000000-0000-0000-0000-000000000001",
        email="member@example.test",
        display_name="Member",
        roles=tuple(roles),
        must_change_pin=False,
        organization_capabilities=tuple(capabilities),
        **sessions[mode],
    )


def _workspace_request(method, route_path):
    return Request(
        {"type": "http", "method": method, "path": route_path, "headers": [], "route": SimpleNamespace(path=route_path)}
    )


REVIEW_QUEUE = ("GET", "/api/v1/workspace/review-cases")
REVIEW_DECISION = ("POST", "/api/v1/workspace/review-cases/{review_case_id}/decisions")
RIGHTS_QUEUE = ("GET", "/api/v1/workspace/rights-review-cases")
AGENT_CONTROL = ("POST", "/api/v1/workspace/scott/source-searches")


def test_public_workflow_roles_drop_admin_only_in_a_customer_organization_session():
    assert public_workflow_roles(_user("admin", "reviewer", mode="customer")) == {"reviewer"}
    assert public_workflow_roles(_user("admin", "reviewer", mode="platform")) == {"admin", "reviewer"}
    assert public_workflow_roles(_user("admin", "reviewer", mode="personal")) == {"admin", "reviewer"}
    # Capabilities are a separate grant and never become public roles.
    assert public_workflow_roles(_user(mode="customer", capabilities=("contributor", "symbol_reviewer"))) == frozenset()


@pytest.mark.parametrize(
    ("roles", "mode", "capabilities", "operation", "allowed"),
    [
        # A Public Reviewer keeps public review inside their organization session.
        (("reviewer",), "customer", (), REVIEW_QUEUE, True),
        (("reviewer",), "customer", (), REVIEW_DECISION, True),
        (("reviewer",), "customer", (), RIGHTS_QUEUE, True),
        (("reviewer",), "customer", (), AGENT_CONTROL, False),
        # An Org Reviewer alone has no public review.
        ((), "customer", ("symbol_reviewer",), REVIEW_QUEUE, False),
        ((), "customer", ("symbol_reviewer",), REVIEW_DECISION, False),
        # Global admin is inert in a customer organization session...
        (("admin",), "customer", (), REVIEW_QUEUE, False),
        (("admin",), "customer", (), REVIEW_DECISION, False),
        (("admin",), "customer", (), AGENT_CONTROL, False),
        # ...but holding Public Reviewer as well still allows public review.
        (("admin", "reviewer"), "customer", (), REVIEW_DECISION, True),
        # Outside customer organizations nothing changes.
        (("admin",), "platform", (), REVIEW_DECISION, True),
        (("admin",), "personal", (), AGENT_CONTROL, True),
        (("reviewer",), "personal", (), REVIEW_QUEUE, True),
    ],
)
def test_workspace_review_access_follows_public_roles_only(roles, mode, capabilities, operation, allowed):
    user = _user(*roles, mode=mode, capabilities=capabilities)
    request = _workspace_request(*operation)
    if allowed:
        assert require_workspace_access(request, user) is user
        return
    with pytest.raises(HTTPException) as exc:
        require_workspace_access(request, user)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    ("roles", "mode", "capabilities", "allowed"),
    [
        (("submitter",), "customer", (), True),
        (("admin",), "customer", (), False),
        ((), "customer", ("contributor",), False),
        (("admin",), "personal", (), True),
        (("submitter",), "personal", (), True),
    ],
)
def test_public_submission_needs_public_submitter_in_a_customer_organization_session(roles, mode, capabilities, allowed):
    dependency = require_public_submitter
    user = _user(*roles, mode=mode, capabilities=capabilities)
    if allowed:
        assert dependency(user) is user
        return
    with pytest.raises(HTTPException) as exc:
        dependency(user)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    ("roles", "mode", "allowed"),
    [
        (("reviewer",), "customer", True),
        (("admin",), "customer", False),
        (("admin",), "platform", True),
        ((), "customer", False),
    ],
)
def test_opening_a_promotion_review_needs_public_reviewer_authority(roles, mode, allowed):
    user = _user(*roles, mode=mode, capabilities=("symbol_reviewer",))
    if allowed:
        _require_reviewer_authority(user)
        return
    with pytest.raises(PromotionRequestError):
        _require_reviewer_authority(user)


def test_a_public_reviewer_without_org_reviewer_cannot_decide_private_submissions():
    with pytest.raises(OrganizationSymbolReviewError):
        _require_reviewer(_user("reviewer", mode="customer"))


def test_a_user_with_both_roles_does_both_kinds_of_review_in_one_organization_session():
    user = _user("reviewer", mode="customer", capabilities=("symbol_reviewer",))
    assert require_workspace_access(_workspace_request(*REVIEW_DECISION), user) is user
    _require_reviewer(user)
    _require_reviewer_authority(user)


def _http_world():
    """A subscribed ACME with one Public Reviewer (org plan role) and one Org Reviewer (capability)."""
    import sys
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from symgov_backend.models import OrganizationMemberCapability
    from test_organization_member_roles import REASON, _seed_member, _setup, _url

    client, Session, _, acme_id, public_membership, _ = _setup()
    assert client.put(_url(acme_id, public_membership, "reviewer"), json={"reason": REASON}).status_code == 200
    org_membership, _ = _seed_member(Session, acme_id, email="org-reviewer@example.test")
    with Session() as session:
        session.add(OrganizationMemberCapability(
            id=uuid.uuid4(), membership_id=org_membership, capability="symbol_reviewer", is_active=True,
            granted_at=datetime.now(timezone.utc),
        ))
        session.commit()
    return client, acme_id


def _org_session(client, org_id, email):
    from fastapi.testclient import TestClient

    member = TestClient(client.app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    assert member.post("/api/v1/auth/login", json={"email": email, "pin": "1234"}).status_code == 200
    member.post("/api/v1/auth/organizations/select", json={"organizationId": str(org_id)})
    session = member.get("/api/v1/auth/me").json()["user"]["session"]
    assert (session["mode"], session["activeOrganizationId"]) == ("organization", str(org_id)), session
    return member


def test_over_http_an_org_reviewer_is_refused_public_review_and_a_public_reviewer_is_not():
    client, acme_id = _http_world()

    org_reviewer = _org_session(client, acme_id, "org-reviewer@example.test")
    me = org_reviewer.get("/api/v1/auth/me").json()["user"]
    assert me["roles"] == []
    assert org_reviewer.get("/api/v1/workspace/review-cases").status_code == 403

    public_reviewer = _org_session(client, acme_id, "member@example.test")
    assert public_reviewer.get("/api/v1/auth/me").json()["user"]["roles"] == ["reviewer"]
    # Past the role gate: whatever the handler then does, it is not a 403.
    assert public_reviewer.get("/api/v1/workspace/review-cases").status_code != 403
