"""Stage 9 WP9.5 regression: the `contribution_events` append-only ledger,
`organization_badges`/`organization_contribution_totals` persisted read
models, and the real `GET /org/me/contributions` /
`GET /platform/organizations/{id}/contributions` HTTP endpoints, against a
real disposable PostgreSQL container.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.5) and the design Chris confirmed for this package specifically:

- The only wired trigger for a `contribution_awarded` row is a symbol's
  public promotion being accepted (`organization_promotion_handoff.
  execute_organization_promotion_handoff`, via the real reviewer
  decision endpoint) -- driven end to end here, not via direct ORM
  inserts, so the thing actually proven is that the real request path
  produces the row/badges/totals in the same transaction as the
  acceptance itself.
- First Contribution and Contributor Organization share one trigger (an
  organization's first-ever accepted contribution) and are always awarded
  together; a second accepted contribution by the same organization must
  not duplicate either badge.
- Demotion (`symbol_demotion.execute_demotion`) reverses the symbol's
  still-active accepted contribution via an append-only
  `contribution_reversed` row referencing the original by
  `reversed_event_id` -- and does *not* revoke either already-awarded
  badge (explicitly left to WP9.6's own anti-gaming scope).
- `organization_badges`/`organization_contribution_totals` are this
  package's read model and must survive this ledger's own 90-day
  retention purge unchanged -- proven the same way WP9.4's own rollup
  survived `product_usage_events`' purge.
- `contribution_events` rows are immutable once inserted (an `UPDATE`
  trigger enforces this), and the frozen `event_type`/`reason` vocabulary
  is enforced by `CheckConstraint`s.

Each test uses its own freshly generated organization code(s) (never a
shared literal like `"acme"`) -- this file's own tests aggregate real
contribution counts/badges *by organization*, so two tests sharing one
organization would leak each other's totals into the same read.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles, _make_platform_admin  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.contribution_events import get_organization_contributions  # noqa: E402
from symgov_backend.contribution_retention import purge_expired_contribution_events  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import ContributionEvent, User  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0043"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp95_database():
    with _database("symgov-wp95") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            # This disposable rehearsal runs migrations as `postgres`, not
            # `symgov_app`, so pre-Stage-9 tables (created before the
            # "grant inside the migration" convention started with WP9.1)
            # need the same equivalent access explicitly granted here --
            # mirrors test_wp92_governance_usage_events_postgresql.py's own
            # fixture exactly, since this file drives the identical
            # promotion/demotion flow through real HTTP.
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT ON catalog_symbol_identifiers TO symgov_app",
                "GRANT USAGE, SELECT ON SEQUENCE catalog_symbol_id_seq TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_cases TO symgov_app",
                "GRANT SELECT, INSERT ON human_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_case_actions TO symgov_app",
                "GRANT SELECT, INSERT ON publication_approval_targets TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT ON active_public_symbol_projections TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_sets TO symgov_app",
                "GRANT SELECT, INSERT ON symbol_set_items TO symgov_app",
                "GRANT SELECT ON catalog_favourites TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _unique_code(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def _client(engine, *, pilot_codes):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        platform_admin_enabled=True,
        symbol_sets_enabled=True,
        organization_admin_enabled=True,
        organization_pilot_codes=tuple(pilot_codes),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), TestingSessionLocal


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response.json()


def _step_up(client):
    response = client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})
    assert response.status_code == 200, response.text


def _email(Session, user_id) -> str:
    with Session() as session:
        return session.get(User, user_id).email


def _create_org_symbol(admin_client, *, name):
    response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A WP9.5 test symbol."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["id"], body["currentRevisionId"]


def _accept_promotion(engine, *, org_code, symbol_name, reviewer_email):
    """Drives a whole org-private-draft -> organization-review-approval ->
    public-promotion-submission -> reviewer-acceptance flow over real HTTP,
    returning (Session, organization_id, admin_id, symbol_id, promotion_request_id)."""
    admin_client, Session = _client(engine, pilot_codes=(org_code,))
    reviewer_client, _ = _client(engine, pilot_codes=(org_code,))

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp95admin-{uuid.uuid4().hex[:8]}@example.test", display_name=f"WP9.5 Admin {uuid.uuid4().hex[:6]}", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code=org_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    _create_user_with_global_roles(Session, email=reviewer_email, display_name=f"WP9.5 Reviewer {uuid.uuid4().hex[:6]}", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    symbol_id, revision_id = _create_org_symbol(admin_client, name=symbol_name)
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful across organizations.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["currentStage"] == "published"

    return Session, organization_id, admin_id, uuid.UUID(symbol_id), uuid.UUID(promotion_request_id)


def test_promotion_acceptance_awards_contribution_and_first_two_badges(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, organization_id, admin_id, symbol_id, promotion_request_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 First Badge Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )

    with Session() as session:
        event = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).one()
    assert event.event_type == "contribution_awarded"
    assert event.organization_id == organization_id
    assert event.submission_id == promotion_request_id
    assert event.user_id == admin_id
    assert event.reason is None
    assert event.reversed_event_id is None

    with Session() as session:
        summary = get_organization_contributions(session, organization_id)
    assert summary["acceptedContributionCount"] == 1
    assert summary["reversedContributionCount"] == 0
    badge_types = {badge["badgeType"] for badge in summary["badges"]}
    assert badge_types == {"first_contribution", "contributor_organization"}


def test_second_accepted_contribution_does_not_duplicate_badges(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, organization_id, _admin_id, _symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 Second Contribution Symbol A",
        reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test",
    )

    admin_client, _ = _client(engine, pilot_codes=(org_code,))
    reviewer_client, _ = _client(engine, pilot_codes=(org_code,))
    with Session() as session:
        existing_admin = session.query(User).filter(User.id == _admin_id).one()
        admin_email = existing_admin.email
    _login(admin_client, admin_email)
    reviewer_email = f"wp95rev2-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.5 Reviewer Two", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    symbol_id, revision_id = _create_org_symbol(admin_client, name="WP9.5 Second Contribution Symbol B")
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
        summary = get_organization_contributions(session, organization_id)
    assert summary["acceptedContributionCount"] == 2
    assert len(summary["badges"]) == 2  # still exactly the two badges, not duplicated


def test_demotion_reverses_contribution_without_revoking_badges(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, organization_id, _admin_id, symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 Demotion Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )

    with Session() as session:
        award = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).one()

    platform_client, _ = _client(engine, pilot_codes=("symgov",))
    platform_admin_id = _create_user_with_global_roles(
        Session, email=f"wp95platform-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.5 Platform Admin", roles=[]
    )
    _make_platform_admin(Session, platform_admin_id)
    _login(platform_client, _email(Session, platform_admin_id))
    _step_up(platform_client)

    demote = platform_client.post(
        f"/api/v1/platform/governed-symbols/{symbol_id}/demote", json={"reason": "WP9.5 regression test demotion."}
    )
    assert demote.status_code == 200, demote.text

    with Session() as session:
        reversal = (
            session.query(ContributionEvent)
            .filter(ContributionEvent.governed_symbol_id == symbol_id, ContributionEvent.event_type == "contribution_reversed")
            .one()
        )
    assert reversal.reversed_event_id == award.id
    assert reversal.reason == "WP9.5 regression test demotion."
    assert reversal.organization_id == organization_id

    with Session() as session:
        summary = get_organization_contributions(session, organization_id)
    assert summary["acceptedContributionCount"] == 1
    assert summary["reversedContributionCount"] == 1
    # Badges are never revoked by a reversal -- explicitly left to WP9.6.
    badge_types = {badge["badgeType"] for badge in summary["badges"]}
    assert badge_types == {"first_contribution", "contributor_organization"}


def test_organization_and_platform_routes_are_tenant_isolated(wp95_database):
    engine, _, _ = wp95_database
    acme_code = _unique_code("acme")
    other_code = _unique_code("other")
    Session, acme_org_id, acme_admin_id, _symbol_id, _promo_id = _accept_promotion(
        engine, org_code=acme_code, symbol_name="WP9.5 Tenant Isolation Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )

    acme_client, _ = _client(engine, pilot_codes=(acme_code, other_code))
    other_client, _ = _client(engine, pilot_codes=(acme_code, other_code))
    platform_client, _ = _client(engine, pilot_codes=(acme_code, other_code, "symgov"))

    _login(acme_client, _email(Session, acme_admin_id))

    other_admin_id = _create_user_with_global_roles(
        Session, email=f"wp95other-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.5 Other Admin", roles=[]
    )
    _add_membership(Session, other_admin_id, code=other_code, base_role="admin")
    _login(other_client, _email(Session, other_admin_id))

    platform_admin_id = _create_user_with_global_roles(
        Session, email=f"wp95platview-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.5 Platform Viewer", roles=[]
    )
    _make_platform_admin(Session, platform_admin_id)
    _login(platform_client, _email(Session, platform_admin_id))

    acme_response = acme_client.get("/api/v1/org/me/contributions")
    assert acme_response.status_code == 200, acme_response.text
    assert acme_response.json()["acceptedContributionCount"] == 1
    assert len(acme_response.json()["badges"]) == 2

    other_response = other_client.get("/api/v1/org/me/contributions")
    assert other_response.status_code == 200, other_response.text
    assert other_response.json()["acceptedContributionCount"] == 0
    assert other_response.json()["badges"] == []

    platform_view = platform_client.get(f"/api/v1/platform/organizations/{acme_org_id}/contributions")
    assert platform_view.status_code == 200, platform_view.text
    assert platform_view.json() == acme_response.json()

    unknown = platform_client.get(f"/api/v1/platform/organizations/{uuid.uuid4()}/contributions")
    assert unknown.status_code == 404


def test_contribution_events_are_immutable(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, _organization_id, _admin_id, symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 Immutability Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )
    with Session() as session:
        event = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).one()
        row_id = event.id

    with Session() as session:
        with pytest.raises(ProgrammingError) as excinfo:
            session.execute(
                text("UPDATE contribution_events SET reason = 'attempted mutation' WHERE id = :id"), {"id": row_id}
            )
            session.commit()
        assert "immutable" in str(excinfo.value).lower()


def test_unknown_event_type_and_missing_reversal_reason_are_rejected(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, organization_id, admin_id, symbol_id, promotion_request_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 Constraint Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )
    now = datetime.now(timezone.utc)

    with Session() as session:
        session.add(
            ContributionEvent(
                id=uuid.uuid4(),
                organization_id=organization_id,
                user_id=admin_id,
                submission_id=promotion_request_id,
                event_type="not_a_real_event_type",
                occurred_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session() as session:
        session.add(
            ContributionEvent(
                id=uuid.uuid4(),
                organization_id=organization_id,
                user_id=admin_id,
                submission_id=promotion_request_id,
                event_type="contribution_reversed",
                reversed_event_id=uuid.uuid4(),
                reason=None,
                occurred_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_badges_and_totals_survive_ledger_retention_purge(wp95_database):
    engine, _, _ = wp95_database
    org_code = _unique_code("acme")
    Session, organization_id, _admin_id, symbol_id, _promo_id = _accept_promotion(
        engine, org_code=org_code, symbol_name="WP9.5 Purge Survival Symbol", reviewer_email=f"wp95rev-{uuid.uuid4().hex[:8]}@example.test"
    )

    old_day = datetime.now(timezone.utc) - timedelta(days=120)
    with Session() as session:
        event = session.query(ContributionEvent).filter(ContributionEvent.governed_symbol_id == symbol_id).one()
        # The row is immutable via trigger (proven separately by
        # test_contribution_events_are_immutable) -- bypass it here only to
        # simulate "this real award row has aged past retention", the same
        # backdating this test needs to prove badges/totals survive its
        # purge. Never done outside a test.
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
    assert remaining == 0

    with Session() as session:
        summary = get_organization_contributions(session, organization_id)
    assert summary["acceptedContributionCount"] == 1
    assert len(summary["badges"]) == 2
