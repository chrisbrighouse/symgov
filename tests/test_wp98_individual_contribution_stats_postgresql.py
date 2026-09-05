"""Stage 9 WP9.8 regression: `user_contribution_totals` (the per-user
mirror of `organization_contribution_totals`) and the real self-service
`GET /profile/contributions` HTTP endpoint, against a real disposable
PostgreSQL container.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.8) and the design Chris confirmed for this package specifically:

- Spec §12.2's "individual users may see private contribution/activity
  statistics in their profile" is a distinct requirement from the already-
  deferred opt-in leaderboard (Q9) -- it is built here as a small additive
  backend read endpoint, not scoped out.
- Individual stats are accepted/reversed counts only, no badges -- §12.2
  lists badges under "Organization badges", a separate, already-shipped
  organization-level concept (`GET /org/me/contributions`).
- Counts are all-time across every organization the user has ever
  contributed through, not scoped to their current active organization.
- `user_contribution_totals` exists specifically so these counts survive
  the raw `contribution_events` ledger's own 90-day retention purge
  unchanged, mirroring `organization_contribution_totals`' own design.
- `GET /profile/contributions` is self-service: any authenticated user may
  read only their own row, with no admin capability required and no
  organizationId/userId parameter to spoof.

This file reuses WP9.5's own `_accept_promotion` helper (drives the real
org-private-draft -> organization-review-approval -> public-promotion-
submission -> reviewer-acceptance flow over real HTTP) rather than
duplicating it, since WP9.8's only real trigger is the same one WP9.5
already wired -- this file proves the individual/user-scoped read model on
top of it, not a new trigger.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_wp74_symbol_demotion_postgresql import _create_user_with_global_roles, _make_platform_admin  # noqa: E402
from test_wp95_contribution_reputation_postgresql import (  # noqa: E402
    _accept_promotion,
    _client,
    _email,
    _login,
    _step_up,
    _unique_code,
    wp95_database,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.contribution_events import get_user_contributions  # noqa: E402
from symgov_backend.contribution_retention import purge_expired_contribution_events  # noqa: E402
from symgov_backend.models import ContributionEvent  # noqa: E402

psycopg = pytest.importorskip("psycopg")

# This file reuses WP9.5's own disposable-Postgres fixture directly
# (identical grants/migration head this package needs too) rather than
# standing up a second, near-identical one -- both drive the same real
# promotion/demotion flow.


def test_promotion_acceptance_increments_the_submitting_users_own_totals(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, _organization_id, admin_id, _symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.8 First Contribution Symbol",
        reviewer_email=f"wp98rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    with Session() as session:
        summary = get_user_contributions(session, admin_id)
    assert summary == {"acceptedContributionCount": 1, "reversedContributionCount": 0}

    admin_client, _ = _client(engine, pilot_codes=(org_code,))
    _login(admin_client, _email(Session, admin_id))
    response = admin_client.get("/api/v1/profile/contributions")
    assert response.status_code == 200, response.text
    assert response.json() == {"acceptedContributionCount": 1, "reversedContributionCount": 0}


def test_second_accepted_contribution_by_same_user_increments_further(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, _organization_id, admin_id, _symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.8 Second Contribution Symbol A",
        reviewer_email=f"wp98rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    admin_client, _ = _client(engine, pilot_codes=(org_code,))
    reviewer_client, _ = _client(engine, pilot_codes=(org_code,))
    _login(admin_client, _email(Session, admin_id))
    reviewer_email = f"wp98rev2-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.8 Reviewer Two", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": "WP9.8 Second Contribution Symbol B", "category": "fire", "discipline": "civil", "summary": "A WP9.8 test symbol."},
    )
    assert response.status_code == 200, response.text
    symbol_id, revision_id = response.json()["id"], response.json()["currentRevisionId"]
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "A second broadly useful symbol.", "sharingAcknowledgment": True},
    )
    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{submit_promotion.json()['id']}/open-review")
    review_case_id = open_review.json()["reviewCaseId"]
    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text

    with Session() as session:
        summary = get_user_contributions(session, admin_id)
    assert summary == {"acceptedContributionCount": 2, "reversedContributionCount": 0}


def test_demotion_increments_the_original_submitters_reversed_count(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, _organization_id, admin_id, symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.8 Demotion Symbol",
        reviewer_email=f"wp98rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    platform_client, _ = _client(engine, pilot_codes=("symgov",))
    platform_admin_id = _create_user_with_global_roles(
        Session, email=f"wp98platform-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.8 Platform Admin", roles=[]
    )
    _make_platform_admin(Session, platform_admin_id)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)

    demote = platform_client.post(
        f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "WP9.8 regression test demotion."}
    )
    assert demote.status_code == 200, demote.text

    with Session() as session:
        # The demoting platform admin never submitted anything -- the
        # reversal is attributed to the *original submitter*, not the
        # demoting actor.
        platform_admin_summary = get_user_contributions(session, platform_admin_id)
        submitter_summary = get_user_contributions(session, admin_id)
    assert platform_admin_summary == {"acceptedContributionCount": 0, "reversedContributionCount": 0}
    assert submitter_summary == {"acceptedContributionCount": 1, "reversedContributionCount": 1}


def test_profile_contributions_endpoint_is_self_service_not_admin_gated(wp95_database):
    engine, _, _ = wp95_database
    client, Session = _client(engine, pilot_codes=())
    # A plain user with zero global roles and zero organization membership
    # -- no admin/reviewer/organization-admin capability of any kind.
    plain_user_id = _create_user_with_global_roles(
        Session, email=f"wp98plain-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.8 Plain User", roles=[]
    )
    _login(client, _email(Session, plain_user_id))

    response = client.get("/api/v1/profile/contributions")
    assert response.status_code == 200, response.text
    assert response.json() == {"acceptedContributionCount": 0, "reversedContributionCount": 0}


def test_user_totals_are_scoped_to_the_individual_not_leaked_to_other_users(wp95_database):
    engine, _, _ = wp95_database
    acme_code = _unique_code("acme")
    other_code = _unique_code("other")
    Session, _organization_id, acme_admin_id, _symbol_id, _promo_id = _accept_promotion(
        engine, org_code=acme_code, symbol_name="WP9.8 Isolation Symbol",
        reviewer_email=f"wp98rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    other_client, _ = _client(engine, pilot_codes=(acme_code, other_code))
    other_admin_id = _create_user_with_global_roles(
        Session, email=f"wp98other-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.8 Other Admin", roles=[]
    )
    _login(other_client, _email(Session, other_admin_id))

    other_response = other_client.get("/api/v1/profile/contributions")
    assert other_response.status_code == 200, other_response.text
    assert other_response.json() == {"acceptedContributionCount": 0, "reversedContributionCount": 0}

    acme_client, _ = _client(engine, pilot_codes=(acme_code, other_code))
    _login(acme_client, _email(Session, acme_admin_id))
    acme_response = acme_client.get("/api/v1/profile/contributions")
    assert acme_response.status_code == 200, acme_response.text
    assert acme_response.json() == {"acceptedContributionCount": 1, "reversedContributionCount": 0}


def test_user_totals_survive_ledger_retention_purge(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, _organization_id, admin_id, symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.8 Purge Survival Symbol",
        reviewer_email=f"wp98rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    old_day = datetime.now(timezone.utc) - timedelta(days=120)
    with Session() as session:
        event = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).one()
        session.execute(text("SET session_replication_role = replica"))
        session.execute(
            text("UPDATE contribution_events SET occurred_at = :occurred_at WHERE id = :id"),
            {"occurred_at": old_day, "id": event.id},
        )
        session.execute(text("SET session_replication_role = DEFAULT"))
        session.commit()

    with Session() as session:
        deleted = purge_expired_contribution_events(session)
        session.commit()
    assert deleted == 1

    with Session() as session:
        remaining = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).count()
        summary = get_user_contributions(session, admin_id)
    assert remaining == 0
    assert summary == {"acceptedContributionCount": 1, "reversedContributionCount": 0}
