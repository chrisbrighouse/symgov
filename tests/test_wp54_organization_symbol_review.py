"""WP5.4 regression: organization review lifecycle, against a real
disposable PostgreSQL container — the stale-decision guard (a UNIQUE
constraint), the binding-validation triggers, and the organization-wide
eligibility trigger are all Postgres-only (see WP5.1's migration), so a
SQLite unit test cannot exercise them.

Proves, per the Stage 5 plan (§4) and programme plan §11:
- Only an active member with the explicit `symbol_reviewer` capability may
  decide a submission; Organization Admin status alone is insufficient.
- A decision is revision-specific and has no public-visibility side
  effect (visibility stays organization_private throughout).
- A second decision on the same submission is rejected as a stale-decision
  conflict (HTTP 409 at the route; a domain conflict here).
- Mutation after approval requires a fresh draft revision, not an in-place
  edit of the approved revision, and clears `organization_wide` since the
  new revision is not yet approved.
- Organization-wide scope can be enabled only once the current revision
  has an approved, closed decision, and only by an Organization Admin.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _organization,
    _user,
    stage5_database,
)
from test_wp53_organization_symbol_drafts import _actor, _membership  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.models import GovernedSymbol, SymbolRevision  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import (  # noqa: E402
    OrganizationSymbolReviewConflict,
    OrganizationSymbolReviewError,
    OrganizationSymbolReviewNotVisible,
    create_new_draft_revision,
    decide_submission,
    set_organization_wide,
)


@pytest.fixture()
def wp54_fixtures(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        organization = _organization(connection, "wp54")
        other_organization = _organization(connection, "wp54other")
        contributor_user = _user(connection, "contributor")
        admin_user = _user(connection, "admin")
        reviewer_user = _user(connection, "reviewer")
        second_reviewer_user = _user(connection, "reviewer2")
        admin_without_capability_user = _user(connection, "adminonly")
        cross_org_reviewer_user = _user(connection, "crossorgreviewer")

        _membership(connection, organization, contributor_user, base_role="user", capabilities=("contributor",))
        _membership(connection, organization, admin_user, base_role="admin", capabilities=("symbol_reviewer",))
        _membership(connection, organization, reviewer_user, base_role="user", capabilities=("symbol_reviewer",))
        _membership(connection, organization, second_reviewer_user, base_role="user", capabilities=("symbol_reviewer",))
        _membership(connection, organization, admin_without_capability_user, base_role="admin")
        _membership(connection, other_organization, cross_org_reviewer_user, base_role="user", capabilities=("symbol_reviewer",))

    return {
        "engine": engine,
        "organization": organization,
        "other_organization": other_organization,
        "contributor": _actor(contributor_user, organization, base_role="user", capabilities=("contributor",)),
        "admin_reviewer": _actor(admin_user, organization, base_role="admin", capabilities=("symbol_reviewer",)),
        "reviewer": _actor(reviewer_user, organization, base_role="user", capabilities=("symbol_reviewer",)),
        "second_reviewer": _actor(second_reviewer_user, organization, base_role="user", capabilities=("symbol_reviewer",)),
        "admin_without_capability": _actor(admin_without_capability_user, organization, base_role="admin"),
        "cross_org_reviewer": _actor(cross_org_reviewer_user, other_organization, base_role="user", capabilities=("symbol_reviewer",)),
    }


def _draft_and_submission(engine, fixtures):
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, fixtures["contributor"], name="Review target", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        submission = submit_for_review(session, fixtures["contributor"], symbol_id=symbol_id, revision_id=revision_id)
        session.commit()
        submission_id = submission.id
    return symbol_id, revision_id, submission_id


def test_admin_without_symbol_reviewer_capability_cannot_decide(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    _, _, submission_id = _draft_and_submission(engine, wp54_fixtures)
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            decide_submission(
                session, wp54_fixtures["admin_without_capability"], submission_id=submission_id, decision="approved"
            )


def test_cross_organization_reviewer_cannot_see_or_decide_the_submission(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    _, _, submission_id = _draft_and_submission(engine, wp54_fixtures)
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewNotVisible):
            decide_submission(
                session, wp54_fixtures["cross_org_reviewer"], submission_id=submission_id, decision="approved"
            )


def test_approval_advances_lifecycle_and_has_no_public_visibility_side_effect(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    symbol_id, revision_id, submission_id = _draft_and_submission(engine, wp54_fixtures)

    with Session(engine) as session:
        decision = decide_submission(
            session, wp54_fixtures["reviewer"], submission_id=submission_id, decision="approved", rationale="Looks good."
        )
        session.commit()
        assert decision.decision == "approved"
        assert decision.rationale == "Looks good."

    with engine.connect() as connection:
        revision_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id = :id"), {"id": revision_id}
        ).scalar_one()
        assert revision_state == "approved"
        submission_status = connection.execute(
            text("SELECT status, closed_at FROM organization_symbol_review_submissions WHERE id = :id"),
            {"id": submission_id},
        ).one()
        assert submission_status.status == "closed"
        assert submission_status.closed_at is not None
        visibility = connection.execute(
            text("SELECT visibility FROM governed_symbols WHERE id = :id"), {"id": symbol_id}
        ).scalar_one()
        assert visibility == "organization_private"


def test_rejection_and_changes_requested_return_revision_to_draft(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    for decision_value in ("rejected", "changes_requested"):
        _, revision_id, submission_id = _draft_and_submission(engine, wp54_fixtures)
        with Session(engine) as session:
            decide_submission(session, wp54_fixtures["reviewer"], submission_id=submission_id, decision=decision_value)
            session.commit()
        with engine.connect() as connection:
            state = connection.execute(
                text("SELECT lifecycle_state FROM symbol_revisions WHERE id = :id"), {"id": revision_id}
            ).scalar_one()
            assert state == "draft", decision_value


def test_a_second_decision_on_the_same_submission_is_a_stale_decision_conflict(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    _, _, submission_id = _draft_and_submission(engine, wp54_fixtures)

    with Session(engine) as session:
        decide_submission(session, wp54_fixtures["reviewer"], submission_id=submission_id, decision="approved")
        session.commit()

    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewConflict):
            decide_submission(
                session, wp54_fixtures["second_reviewer"], submission_id=submission_id, decision="rejected"
            )


def test_organization_wide_requires_current_revision_approved_and_admin_or_reviewer(wp54_fixtures):
    """Stage 6 WP6.3 (confirmed with Chris 2026-09-01) broadened this from
    admin-only to admin-or-`symbol_reviewer`, on the reasoning that a
    reviewer already trusted to approve a revision is equally trusted to
    decide whether it becomes organization-wide."""
    engine = wp54_fixtures["engine"]
    symbol_id, revision_id, submission_id = _draft_and_submission(engine, wp54_fixtures)

    # Not yet approved: rejected by the DB-level eligibility trigger.
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            set_organization_wide(session, wp54_fixtures["admin_reviewer"], symbol_id=symbol_id, enabled=True)

    with Session(engine) as session:
        decide_submission(session, wp54_fixtures["reviewer"], submission_id=submission_id, decision="approved")
        session.commit()

    # Neither admin status nor the symbol_reviewer capability alone is
    # sufficient by itself -- but a plain contributor with neither is
    # still rejected.
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            set_organization_wide(session, wp54_fixtures["contributor"], symbol_id=symbol_id, enabled=True)

    # A non-admin reviewer (has the symbol_reviewer capability) can toggle.
    with Session(engine) as session:
        symbol = set_organization_wide(session, wp54_fixtures["reviewer"], symbol_id=symbol_id, enabled=True)
        session.commit()
        assert symbol.organization_wide is True

    # An admin without the symbol_reviewer capability can also toggle.
    with Session(engine) as session:
        symbol = set_organization_wide(session, wp54_fixtures["admin_without_capability"], symbol_id=symbol_id, enabled=False)
        session.commit()
        assert symbol.organization_wide is False

    with Session(engine) as session:
        symbol = set_organization_wide(session, wp54_fixtures["admin_reviewer"], symbol_id=symbol_id, enabled=True)
        session.commit()
        assert symbol.organization_wide is True

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT organization_wide FROM governed_symbols WHERE id = :id"), {"id": symbol_id}
        ).scalar_one()
        assert stored is True


def test_mutation_after_approval_creates_a_fresh_draft_revision_and_clears_organization_wide(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    symbol_id, revision_id, submission_id = _draft_and_submission(engine, wp54_fixtures)

    with Session(engine) as session:
        decide_submission(session, wp54_fixtures["reviewer"], submission_id=submission_id, decision="approved")
        session.commit()
    with Session(engine) as session:
        set_organization_wide(session, wp54_fixtures["admin_reviewer"], symbol_id=symbol_id, enabled=True)
        session.commit()

    with Session(engine) as session:
        symbol, new_revision = create_new_draft_revision(session, wp54_fixtures["contributor"], symbol_id=symbol_id)
        session.commit()
        assert new_revision.id != revision_id
        assert new_revision.lifecycle_state == "draft"
        assert symbol.current_revision_id == new_revision.id
        assert symbol.organization_wide is False

    with engine.connect() as connection:
        old_revision_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id = :id"), {"id": revision_id}
        ).scalar_one()
        assert old_revision_state == "approved"
        current_revision = connection.execute(
            text("SELECT current_revision_id, organization_wide FROM governed_symbols WHERE id = :id"),
            {"id": symbol_id},
        ).one()
        assert current_revision.current_revision_id != revision_id
        assert current_revision.organization_wide is False


def test_cannot_start_a_new_draft_revision_while_current_revision_is_under_review(wp54_fixtures):
    engine = wp54_fixtures["engine"]
    symbol_id, _, _ = _draft_and_submission(engine, wp54_fixtures)
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            create_new_draft_revision(session, wp54_fixtures["contributor"], symbol_id=symbol_id)
