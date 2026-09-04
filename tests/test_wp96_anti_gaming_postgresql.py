"""Stage 9 WP9.6 regression: anti-gaming enforcement (spec §12.4), against a
real disposable PostgreSQL container.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.6) and the design Chris confirmed for this package specifically:

- Re-reading spec §12.4 against WP9.5's own already-shipped design found
  two of the five rules already structurally satisfied (WP9.5's only real
  trigger is public-promotion acceptance, never a raw upload/like/
  self-download; no reviewer-facing surface exposes contribution/badge
  detail anywhere), and one genuinely real, previously undocumented gap:
  `promotion_requests._require_reviewer_authority`'s reviewer-authority
  check is deliberately global (not organization-scoped), which -- absent
  this package's own new check -- would let a reviewer who is also an
  active member of the submitting organization accept that organization's
  own promotion. `execute_organization_promotion_handoff` now blocks
  exactly that one case (Chris-confirmed), without reversing Stage 7's
  broader "reviewer authority is global" design.
- Dedupe-before-review (Chris-confirmed): comparing canonical_name/
  category/discipline against existing *public* symbols at submission
  time, informational only -- flags `PromotionRequest.
  possible_duplicate_governed_symbol_id`, never blocks the submission.
  Surfaced to the reviewer via `PromotionRequestResponse` (the one
  existing surface a reviewer sees a promotion request's own detail
  through today -- `open_organization_symbol_promotion_review`).
- Rate-limiting (Chris-confirmed number): no more than 10 promotion
  submissions per organization per rolling 7-day window, counting every
  real submission regardless of its later status.

Each test uses its own freshly generated organization code(s) (never a
shared literal), mirroring WP9.4/9.5's own lesson.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402
from test_wp95_contribution_reputation_postgresql import _create_org_symbol, _email, _login, _unique_code  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import ContributionEvent, GovernedSymbol, PromotionRequest, ReviewCaseAction  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0042"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp96_database():
    with _database("symgov-wp96") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            # Mirrors test_wp95_contribution_reputation_postgresql.py's own
            # fixture exactly -- this file drives the same full
            # draft -> organization-review -> promotion -> reviewer-decision
            # pipeline through real HTTP, so it needs the same pre-Stage-9
            # grants that were never baked into those tables' own migrations.
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


def _approve_org_symbol(admin_client, *, name):
    """Create an org-private draft and take it through organization review
    to an approved, closed decision -- the exact precondition
    `submit_promotion_request` requires. Does not itself submit for public
    promotion."""
    symbol_id, revision_id = _create_org_symbol(admin_client, name=name)
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit_review.status_code == 200, submit_review.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    return symbol_id, revision_id


def _submit_promotion(admin_client, symbol_id, *, reason="Broadly useful across organizations."):
    return admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": reason, "sharingAcknowledgment": True},
    )


def test_reviewer_who_is_active_member_of_submitting_organization_cannot_accept(wp96_database):
    engine, _, _ = wp96_database
    org_code = _unique_code("selfrev")
    admin_client, Session = _client(engine, pilot_codes=(org_code,))
    reviewer_client, _ = _client(engine, pilot_codes=(org_code,))

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp96admin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.6 Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code=org_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp96selfrev-{uuid.uuid4().hex[:8]}@example.test"
    reviewer_id = _create_user_with_global_roles(
        Session, email=reviewer_email, display_name="WP9.6 Self Reviewer", roles=["reviewer"]
    )
    # The gap this test proves is real: this reviewer holds the global
    # 'reviewer' role (sufficient for require_workspace_access) *and* is an
    # active member of the very organization submitting the promotion.
    _add_membership(Session, reviewer_id, code=org_code, base_role="user")
    _login(reviewer_client, reviewer_email)

    symbol_id, _revision_id = _approve_org_symbol(admin_client, name="WP9.6 Self Review Symbol")
    submit_promotion = _submit_promotion(admin_client, symbol_id)
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text
    # The decision record itself is written (this endpoint is unmodified),
    # but the handoff that would actually publish the symbol must fail.
    assert decision.json()["currentStage"] != "published"

    with Session() as session:
        symbol = session.get(GovernedSymbol, uuid.UUID(symbol_id))
        assert symbol.visibility == "organization_private"
        assert symbol.catalog_symbol_id is None

        request = session.get(PromotionRequest, uuid.UUID(promotion_request_id))
        assert request.status not in ("accepted",)

        awarded = (
            session.query(ContributionEvent)
            .filter(ContributionEvent.governed_symbol_id == uuid.UUID(symbol_id))
            .count()
        )
        assert awarded == 0

        failed_action = (
            session.query(ReviewCaseAction)
            .filter(ReviewCaseAction.review_case_id == uuid.UUID(review_case_id), ReviewCaseAction.action_status == "failed")
            .one()
        )
        assert "submitting organization" in (failed_action.action_payload_json or {}).get("error", "")


def test_reviewer_without_organization_membership_can_still_accept(wp96_database):
    """Positive control: WP9.6's new check must not regress the ordinary
    cross-organization reviewer acceptance path every other Stage 7/9 test
    already exercises."""
    engine, _, _ = wp96_database
    org_code = _unique_code("normalrev")
    admin_client, Session = _client(engine, pilot_codes=(org_code,))
    reviewer_client, _ = _client(engine, pilot_codes=(org_code,))

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp96admin2-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.6 Admin Two", roles=[]
    )
    _add_membership(Session, admin_id, code=org_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp96normalrev-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.6 Normal Reviewer", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    symbol_id, _revision_id = _approve_org_symbol(admin_client, name="WP9.6 Normal Review Symbol")
    submit_promotion = _submit_promotion(admin_client, symbol_id)
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review")
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision = reviewer_client.post(f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["currentStage"] == "published"

    with Session() as session:
        symbol = session.get(GovernedSymbol, uuid.UUID(symbol_id))
        assert symbol.visibility == "public"


def test_promotion_submission_flags_possible_duplicate_of_existing_public_symbol(wp96_database):
    engine, _, _ = wp96_database

    publisher_code = _unique_code("dupepub")
    publisher_client, PublisherSession = _client(engine, pilot_codes=(publisher_code,))
    publisher_reviewer_client, _ = _client(engine, pilot_codes=(publisher_code,))

    publisher_admin_id = _create_user_with_global_roles(
        PublisherSession, email=f"wp96dupepub-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.6 Dupe Publisher", roles=[]
    )
    _add_membership(PublisherSession, publisher_admin_id, code=publisher_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(publisher_client, _email(PublisherSession, publisher_admin_id))

    reviewer_email = f"wp96dupereviewer-{uuid.uuid4().hex[:8]}@example.test"
    _create_user_with_global_roles(PublisherSession, email=reviewer_email, display_name="WP9.6 Dupe Reviewer", roles=["reviewer"])
    _login(publisher_reviewer_client, reviewer_email)

    shared_name = f"WP9.6 Shared Fire Hydrant {uuid.uuid4().hex[:6]}"
    public_symbol_id, _rev = _approve_org_symbol(publisher_client, name=shared_name)
    first_promotion = _submit_promotion(publisher_client, public_symbol_id)
    assert first_promotion.status_code == 200, first_promotion.text
    # A brand-new public symbol has no existing public sibling yet.
    assert first_promotion.json()["possibleDuplicateOfGovernedSymbolId"] is None

    promotion_request_id = first_promotion.json()["id"]
    open_review = publisher_reviewer_client.post(
        f"/api/v1/organization-symbols/{public_symbol_id}/promotion-requests/{promotion_request_id}/open-review"
    )
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]
    decision = publisher_reviewer_client.post(
        f"/api/v1/workspace/review-cases/{review_case_id}/decisions", json={"decisionCode": "approve"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["currentStage"] == "published"

    with PublisherSession() as session:
        public_symbol = session.get(GovernedSymbol, uuid.UUID(public_symbol_id))
        assert public_symbol.visibility == "public"
        expected_slug = public_symbol.slug

    other_code = _unique_code("dupesub")
    other_client, OtherSession = _client(engine, pilot_codes=(other_code,))
    other_admin_id = _create_user_with_global_roles(
        OtherSession, email=f"wp96dupesub-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.6 Dupe Submitter", roles=[]
    )
    _add_membership(OtherSession, other_admin_id, code=other_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(other_client, _email(OtherSession, other_admin_id))

    duplicate_symbol_id, _rev2 = _approve_org_symbol(other_client, name=shared_name)
    duplicate_submission = _submit_promotion(other_client, duplicate_symbol_id)
    assert duplicate_submission.status_code == 200, duplicate_submission.text
    assert duplicate_submission.json()["possibleDuplicateOfGovernedSymbolId"] == public_symbol_id
    assert duplicate_submission.json()["possibleDuplicateOfSlug"] == expected_slug

    distinct_symbol_id, _rev3 = _approve_org_symbol(other_client, name=f"WP9.6 Distinct Symbol {uuid.uuid4().hex[:6]}")
    distinct_submission = _submit_promotion(other_client, distinct_symbol_id)
    assert distinct_submission.status_code == 200, distinct_submission.text
    assert distinct_submission.json()["possibleDuplicateOfGovernedSymbolId"] is None
    assert distinct_submission.json()["possibleDuplicateOfSlug"] is None


def test_organization_submission_rate_limit_blocks_the_eleventh_submission_in_seven_days(wp96_database):
    engine, _, _ = wp96_database
    org_code = _unique_code("ratelimit")
    admin_client, Session = _client(engine, pilot_codes=(org_code,))

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp96rate-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.6 Rate Limit Admin", roles=[]
    )
    _add_membership(Session, admin_id, code=org_code, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    for index in range(10):
        symbol_id, _revision_id = _approve_org_symbol(admin_client, name=f"WP9.6 Rate Limit Symbol {index}")
        response = _submit_promotion(admin_client, symbol_id)
        assert response.status_code == 200, response.text

    eleventh_symbol_id, _revision_id = _approve_org_symbol(admin_client, name="WP9.6 Rate Limit Symbol Eleventh")
    eleventh_response = _submit_promotion(admin_client, eleventh_symbol_id)
    assert eleventh_response.status_code == 409, eleventh_response.text
    assert "rolling 7-day" in eleventh_response.json()["detail"]

    with Session() as session:
        count = (
            session.query(PromotionRequest)
            .filter(PromotionRequest.governed_symbol_id == uuid.UUID(eleventh_symbol_id))
            .count()
        )
        assert count == 0
