"""Unified submission & review WP7: promotion review usability (D1 kept).

- `GET /organization-symbols/promotion-review-queue` lists every open
  promotion request, across organizations, for a Public Reviewer. A request
  from the reviewer's own organization is listed but marked undecidable
  (FR15). Organization roles alone do not open it.
- `GET /organization-symbols/promoted` lists the organization's symbols that
  promotion made public (they leave the private drafts list under D1).
- Both are absent with the workflow v2 flag off.

Portable (SQLite), on `test_wp72_promotion_requests_api.py`'s fixtures.
Acceptance itself runs the publication handoff and is proven on PostgreSQL by
`test_wp73_promotion_publication_handoff_postgresql.py`.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    GovernedSymbol,
    OrganizationMembership,
    PromotionRequest,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402
from test_wp72_promotion_requests_api import (  # noqa: E402
    _add_membership,
    _build_client,
    _create_approved_symbol,
    _login,
)

QUEUE = "/api/v1/organization-symbols/promotion-review-queue"
PROMOTED = "/api/v1/organization-symbols/promoted"


def _world(*, workflow_v2=True):
    client, Session, user_id = _build_client()
    client.app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        organization_symbol_workflow_v2_enabled=workflow_v2,
    )
    acme_id = _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    client.post("/api/v1/auth/organizations/select", json={"organizationId": str(acme_id)})
    return client, Session, user_id, acme_id


def _submit_promotion(client):
    symbol_id = _create_approved_symbol(client)
    response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful across the industry.", "sharingAcknowledgment": True},
    )
    assert response.status_code == 200, response.text
    return symbol_id, response.json()["id"]


def _public_reviewer(client, Session, email="reviewer@example.test"):
    with Session() as session:
        user = upsert_user(session, email=email, display_name="Rita Reviewer", roles=[], pin="1234", must_change_pin=False)
        session.commit()
        user_id = user.id
    with Session() as session:
        subscription = session.get(UserSubscription, user_id)
        subscription.tier = "plus"
        subscription.expires_on = datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 1)
        session.commit()
    with Session() as session:
        upsert_user(session, email=email, display_name="Rita Reviewer", roles=["reviewer"], pin="1234", must_change_pin=False)
        session.commit()
    reviewer = TestClient(client.app, headers={"origin": "http://testserver"})
    assert _login(reviewer, email).status_code == 200
    return reviewer, user_id


def test_a_public_reviewer_sees_another_organizations_promotion_request_with_its_symbol():
    client, Session, _, _ = _world()
    symbol_id, request_id = _submit_promotion(client)
    reviewer, _ = _public_reviewer(client, Session)

    response = reviewer.get(QUEUE)
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["id"] == request_id
    assert item["governedSymbolId"] == symbol_id
    assert item["organizationName"] == "ACME Organization"
    assert item["symbolName"] == "Fire hydrant"
    assert item["submittedByName"] == "Member"
    assert item["status"] == "submitted"
    assert item["revision"]["summary"] == "A fire hydrant symbol."
    assert item["canDecide"] is True
    assert item["blockedReason"] is None


def test_opening_from_the_queue_moves_the_request_to_triage_with_a_review_case():
    client, Session, _, _ = _world()
    symbol_id, request_id = _submit_promotion(client)
    reviewer, _ = _public_reviewer(client, Session)

    opened = reviewer.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/open-review")
    assert opened.status_code == 200, opened.text

    [item] = reviewer.get(QUEUE).json()["items"]
    assert item["status"] == "triage"
    assert item["reviewCaseId"] == opened.json()["reviewCaseId"]


def test_a_reviewer_from_the_submitting_organization_sees_the_request_but_cannot_decide_it():
    client, Session, _, acme_id = _world()
    _submit_promotion(client)
    reviewer, reviewer_id = _public_reviewer(client, Session)
    with Session() as session:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        session.add(OrganizationMembership(
            id=uuid.uuid4(), organization_id=acme_id, user_id=reviewer_id, status="active",
            activated_at=now, created_at=now, updated_at=now,
        ))
        session.commit()

    [item] = reviewer.get(QUEUE).json()["items"]
    assert item["canDecide"] is False
    assert "member of the organization that submitted" in item["blockedReason"]


def test_closed_requests_leave_the_queue():
    client, Session, _, _ = _world()
    symbol_id, request_id = _submit_promotion(client)
    client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw", json={})
    reviewer, _ = _public_reviewer(client, Session)

    assert reviewer.get(QUEUE).json()["items"] == []


def test_org_roles_alone_do_not_open_the_promotion_queue():
    client, _, _, _ = _world()
    _submit_promotion(client)

    # An Org Admin and Org Reviewer with no public role.
    response = client.get(QUEUE)
    assert response.status_code == 403


def test_both_routes_are_absent_with_the_flag_off():
    client, Session, _, _ = _world(workflow_v2=False)
    reviewer, _ = _public_reviewer(client, Session)

    assert reviewer.get(QUEUE).status_code == 404
    assert client.get(PROMOTED).status_code == 404


def test_a_promoted_symbol_leaves_the_drafts_list_and_appears_as_promoted():
    client, Session, _, _ = _world()
    symbol_id, request_id = _submit_promotion(client)
    assert client.get(PROMOTED).json()["items"] == []

    # Stand in for the PostgreSQL-only handoff: what acceptance leaves behind.
    closed_at = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
    with Session() as session:
        symbol = session.get(GovernedSymbol, uuid.UUID(symbol_id))
        symbol.visibility = "public"
        request = session.get(PromotionRequest, uuid.UUID(request_id))
        request.status = "accepted"
        request.closed_at = closed_at
        session.commit()

    drafts = client.get("/api/v1/organization-symbols").json()["items"]
    assert symbol_id not in [draft["id"] for draft in drafts]

    [item] = client.get(PROMOTED).json()["items"]
    assert item["id"] == symbol_id
    assert item["canonicalName"] == "Fire hydrant"
    assert item["promotionRequestId"] == request_id
    assert item["promotedFromOrganizationName"] == "ACME Organization"
    assert item["promotedAt"].startswith("2026-10-25T09:30")


def test_a_demoted_symbol_is_no_longer_listed_as_promoted():
    client, Session, _, _ = _world()
    symbol_id, request_id = _submit_promotion(client)
    with Session() as session:
        request = session.get(PromotionRequest, uuid.UUID(request_id))
        request.status = "accepted"
        request.closed_at = datetime.now(timezone.utc)
        # Demotion returns the same symbol to organization_private.
        session.commit()

    assert client.get(PROMOTED).json()["items"] == []
