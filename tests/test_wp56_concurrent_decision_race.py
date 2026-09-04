"""WP5.6 Security Review addendum: a genuine concurrent-transaction race
regression for `decide_submission` (WP5.4).

The existing `tests/test_wp54_organization_symbol_review.py::
test_a_second_decision_on_the_same_submission_is_a_stale_decision_conflict`
only proves a *sequential* double-decision is rejected (the second call
commits after the first has already committed, so it observes
`submission.status == "closed"` at the application layer before ever
reaching the database). It does not prove the actual concurrency
property the Stage 5 plan's SEC-WP51-002/003 note asks for: two
reviewers racing to decide the *same* submission with genuinely
overlapping transactions, where the app-level `status != "active"`
check cannot have observed the other transaction's write yet, and the
database-level `unique(submission_id)` constraint on
`organization_symbol_review_decisions` is what actually has to
adjudicate the race.

This test forces that overlap with a `threading.Barrier` so both
transactions pass the `submission.status == "active"` read before either
commits, then asserts exactly one decision row survives and the loser
gets `OrganizationSymbolReviewConflict` (not a corrupted/duplicated
decision, not a silent overwrite).
"""

from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _organization, _user, stage5_database  # noqa: E402
from test_wp53_organization_symbol_drafts import _actor, _membership  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import (  # noqa: E402
    OrganizationSymbolReviewConflict,
    decide_submission,
)


@pytest.fixture()
def race_fixtures(stage5_database):
    engine, url, _ = stage5_database
    # Stage 9 WP9.2 added ProductUsageEvent emission inside decide_submission
    # (a shared, widely-tested function this file exercises directly) -- that
    # call now unconditionally needs `product_usage_events` to exist. Applied
    # locally here, not by bumping the shared `stage5_database` fixture
    # itself, since that fixture is also used by several other Stage 5 test
    # files that must stay pinned to their own original schema snapshot.
    _alembic(url, "upgrade", "20260904_0039")
    with engine.begin() as connection:
        organization = _organization(connection, "wp56race")
        contributor_user = _user(connection, "wp56race-contributor")
        reviewer_a_user = _user(connection, "wp56race-reviewer-a")
        reviewer_b_user = _user(connection, "wp56race-reviewer-b")
        _membership(connection, organization, contributor_user, base_role="user", capabilities=("contributor",))
        _membership(connection, organization, reviewer_a_user, base_role="user", capabilities=("symbol_reviewer",))
        _membership(connection, organization, reviewer_b_user, base_role="user", capabilities=("symbol_reviewer",))

    contributor = _actor(contributor_user, organization, base_role="user", capabilities=("contributor",))
    reviewer_a = _actor(reviewer_a_user, organization, base_role="user", capabilities=("symbol_reviewer",))
    reviewer_b = _actor(reviewer_b_user, organization, base_role="user", capabilities=("symbol_reviewer",))

    with Session(engine) as session:
        symbol, revision = create_draft(
            session, contributor, name="Race target", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        submission = submit_for_review(session, contributor, symbol_id=symbol_id, revision_id=revision_id)
        session.commit()
        submission_id = submission.id

    return {
        "engine": engine,
        "reviewer_a": reviewer_a,
        "reviewer_b": reviewer_b,
        "submission_id": submission_id,
        "revision_id": revision_id,
    }


def test_two_reviewers_racing_the_same_submission_only_one_decision_survives(race_fixtures):
    engine = race_fixtures["engine"]
    submission_id = race_fixtures["submission_id"]
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _attempt(name: str, actor, decision: str) -> None:
        try:
            with Session(engine) as session:
                # Force both transactions to observe status == "active"
                # before either has a chance to commit its closing update.
                submission = session.get(
                    __import__("symgov_backend.models", fromlist=["OrganizationSymbolReviewSubmission"]).OrganizationSymbolReviewSubmission,
                    submission_id,
                )
                assert submission.status == "active"
                barrier.wait(timeout=10)
                result = decide_submission(session, actor, submission_id=submission_id, decision=decision)
                session.commit()
                results[name] = ("ok", result.decision)
        except OrganizationSymbolReviewConflict as exc:
            results[name] = ("conflict", str(exc))
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            results[name] = ("error", repr(exc))

    thread_a = threading.Thread(target=_attempt, args=("a", race_fixtures["reviewer_a"], "approved"))
    thread_b = threading.Thread(target=_attempt, args=("b", race_fixtures["reviewer_b"], "rejected"))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)

    outcomes = {results["a"][0], results["b"][0]}
    assert outcomes == {"ok", "conflict"}, results

    with engine.connect() as connection:
        decision_count = connection.execute(
            text(
                "SELECT count(*) FROM organization_symbol_review_decisions WHERE submission_id = :id"
            ),
            {"id": submission_id},
        ).scalar_one()
        assert decision_count == 1

        submission_row = connection.execute(
            text("SELECT status, closed_at FROM organization_symbol_review_submissions WHERE id = :id"),
            {"id": submission_id},
        ).one()
        assert submission_row.status == "closed"
        assert submission_row.closed_at is not None
