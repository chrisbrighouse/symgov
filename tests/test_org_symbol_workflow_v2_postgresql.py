"""Unified submission & review WP2: the completed organization-private lifecycle,
against a real disposable PostgreSQL (the 0033/0076 triggers are Postgres-only).

With the workflow v2 flag on:
- a draft's details can be edited and an asset taken off while it is a draft;
- the submitter can withdraw a pending submission (closed with no decision);
- rejection is final for the revision; changes requested returns it to draft;
- approval publishes the symbol to the organization's library (organization-wide);
- a new revision of a published symbol is held as pending, so the published
  revision stays current and in the library until the new one is approved;
- every transition writes an `AuditEvent`.

With the flag off the WP5.4 behaviour is unchanged; `test_wp54_*` still covers it.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import stage5_database  # noqa: E402,F401
from test_wp54_organization_symbol_review import wp54_fixtures  # noqa: E402,F401

from symgov_backend.models import (  # noqa: E402
    AuditEvent,
    GovernedSymbol,
    OrganizationSymbolReviewSubmission,
    SymbolRevision,
)
from symgov_backend.organization_symbol_drafts import (  # noqa: E402
    OrganizationSymbolDraftError,
    create_draft,
    remove_asset,
    submission_history,
    submit_for_review,
    update_draft,
    withdraw_submission,
)
from symgov_backend.organization_symbol_review import (  # noqa: E402
    OrganizationSymbolReviewError,
    create_new_draft_revision,
    decide_submission,
    discard_pending_revision,
)
from symgov_backend.promotion_requests import PromotionRequestError, submit_promotion_request  # noqa: E402
from symgov_backend.symbol_eligibility import eligible_organization_private_symbols  # noqa: E402

V2 = {"workflow_v2": True}


def _draft(engine, fx, name="Pump"):
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, fx["contributor"], name=name, category="pumps", discipline="Mechanical", summary="A pump"
        )
        session.commit()
        return symbol.id, revision.id


def _submit(engine, actor, symbol_id, revision_id):
    with Session(engine) as session:
        submission = submit_for_review(session, actor, symbol_id=symbol_id, revision_id=revision_id)
        session.commit()
        return submission.id


def _decide(engine, actor, submission_id, decision, **kwargs):
    with Session(engine) as session:
        decide_submission(session, actor, submission_id=submission_id, decision=decision, rationale="Because.", **kwargs)
        session.commit()


def _published(engine, fx, name="Pump"):
    symbol_id, revision_id = _draft(engine, fx, name)
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, revision_id), "approved", **V2)
    return symbol_id, revision_id


def _actions(engine, symbol_id):
    with Session(engine) as session:
        return [
            row.action
            for row in session.execute(
                select(AuditEvent).where(AuditEvent.entity_id == symbol_id).order_by(AuditEvent.created_at, AuditEvent.action)
            ).scalars()
        ]


def test_a_draft_can_be_edited_until_it_is_submitted(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    with Session(engine) as session:
        update_draft(
            session, fx["contributor"], symbol_id=symbol_id, revision_id=revision_id,
            name="Centrifugal pump", discipline="Process", keywords=["pump", "centrifugal"],
        )
        session.commit()
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        revision = session.get(SymbolRevision, revision_id)
        assert (symbol.canonical_name, symbol.discipline) == ("Centrifugal pump", "Process")
        assert revision.payload_json["keywords"] == ["pump", "centrifugal"]

    _submit(engine, fx["contributor"], symbol_id, revision_id)
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftError):
            update_draft(session, fx["contributor"], symbol_id=symbol_id, revision_id=revision_id, name="Too late")
    assert {"org_symbol.draft_created", "org_symbol.draft_updated", "org_symbol.submitted"} <= set(_actions(engine, symbol_id))


def test_an_asset_can_be_taken_off_a_draft(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    attachment_id = uuid.uuid4()
    with Session(engine) as session:
        revision = session.get(SymbolRevision, revision_id)
        revision.payload_json = {**revision.payload_json, "assets": [{"attachment_id": str(attachment_id), "filename": "p.svg"}]}
        session.commit()
    with Session(engine) as session:
        remove_asset(session, fx["contributor"], symbol_id=symbol_id, revision_id=revision_id, attachment_id=attachment_id)
        session.commit()
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).payload_json["assets"] == []
    assert "org_symbol.asset_removed" in _actions(engine, symbol_id)


def test_the_submitter_can_withdraw_and_resubmit(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    submission_id = _submit(engine, fx["contributor"], symbol_id, revision_id)

    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftError):
            withdraw_submission(session, fx["reviewer"], symbol_id=symbol_id, submission_id=submission_id)
    with Session(engine) as session:
        withdraw_submission(session, fx["contributor"], symbol_id=symbol_id, submission_id=submission_id)
        session.commit()
    with Session(engine) as session:
        submission = session.get(OrganizationSymbolReviewSubmission, submission_id)
        assert (submission.status, submission.closure_reason) == ("closed", "withdrawn")
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"

    # A withdrawn submission can never be decided: the binding trigger refuses a decision on it.
    with Session(engine) as session:
        with pytest.raises(Exception):
            decide_submission(session, fx["reviewer"], submission_id=submission_id, decision="approved", **V2)

    resubmitted = _submit(engine, fx["contributor"], symbol_id, revision_id)
    with Session(engine) as session:
        outcomes = [entry.outcome for entry in submission_history(session, fx["contributor"], symbol_id)]
    assert resubmitted != submission_id
    assert sorted(outcomes) == ["active", "withdrawn"]
    assert "org_symbol.withdrawn" in _actions(engine, symbol_id)


def test_rejection_is_final_and_changes_requested_returns_to_draft(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, revision_id), "changes_requested", **V2)
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"

    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, revision_id), "rejected", **V2)
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "rejected"
        with pytest.raises(OrganizationSymbolDraftError):
            submit_for_review(session, fx["contributor"], symbol_id=symbol_id, revision_id=revision_id)

    # A new revision of a never-published symbol becomes its current revision.
    with Session(engine) as session:
        symbol, new_revision = create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
        assert (symbol.current_revision_id, symbol.pending_revision_id) == (new_revision.id, None)

    with Session(engine) as session:
        entries = submission_history(session, fx["contributor"], symbol_id)
        # Timestamps are whole seconds, so both submissions can share one; compare as a set.
        assert sorted(entry.outcome for entry in entries) == ["changes_requested", "rejected"]
        assert all(entry.decided_by_name and entry.decision.rationale == "Because." for entry in entries)


def test_approval_publishes_the_symbol_to_the_organization_library(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, _ = _published(engine, fx)
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert symbol.organization_wide is True
        assert symbol.visibility == "organization_private"
        assert [s.id for s in eligible_organization_private_symbols(session, fx["organization"], organization_wide=True)] == [symbol_id]


def test_a_new_revision_of_a_published_symbol_keeps_the_published_one_live(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, published_revision_id = _published(engine, fx)

    with Session(engine) as session:
        symbol, pending = create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
        pending_id = pending.id
    with Session(engine) as session:
        update_draft(session, fx["contributor"], symbol_id=symbol_id, revision_id=pending_id, name="Pump, mark 2")
        session.commit()
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert (symbol.current_revision_id, symbol.pending_revision_id) == (published_revision_id, pending_id)
        assert (symbol.canonical_name, symbol.organization_wide) == ("Pump", True)
        assert [s.id for s in eligible_organization_private_symbols(session, fx["organization"])] == [symbol_id]
        with pytest.raises(OrganizationSymbolReviewError):
            create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)

    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, pending_id), "approved", **V2)
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert (symbol.current_revision_id, symbol.pending_revision_id) == (pending_id, None)
        assert (symbol.canonical_name, symbol.organization_wide) == ("Pump, mark 2", True)
    assert "org_symbol.revision_created" in _actions(engine, symbol_id)


def test_rejecting_a_pending_revision_leaves_the_published_one_alone(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, published_revision_id = _published(engine, fx)
    with Session(engine) as session:
        _, pending = create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
        pending_id = pending.id
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, pending_id), "rejected", **V2)
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert (symbol.current_revision_id, symbol.pending_revision_id, symbol.organization_wide) == (
            published_revision_id, None, True,
        )
        assert session.get(SymbolRevision, pending_id).lifecycle_state == "rejected"


def test_a_pending_revision_can_be_discarded_while_it_is_a_draft(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, published_revision_id = _published(engine, fx)
    with Session(engine) as session:
        _, pending = create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
        pending_id = pending.id
    with Session(engine) as session:
        discard_pending_revision(session, fx["contributor"], symbol_id=symbol_id)
        session.commit()
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert (symbol.current_revision_id, symbol.pending_revision_id) == (published_revision_id, None)
        assert session.get(SymbolRevision, pending_id).lifecycle_state == "withdrawn"


def test_promotion_and_a_pending_revision_exclude_each_other(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, _ = _published(engine, fx)
    with Session(engine) as session:
        create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
    with Session(engine) as session:
        with pytest.raises(PromotionRequestError):
            submit_promotion_request(
                session, fx["admin_reviewer"], symbol_id=symbol_id, reason="Useful to everyone.", sharing_acknowledgment=True
            )

    other_id, _ = _published(engine, fx, name="Valve")
    with Session(engine) as session:
        submit_promotion_request(
            session, fx["admin_reviewer"], symbol_id=other_id, reason="Useful to everyone.", sharing_acknowledgment=True
        )
        session.commit()
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            create_new_draft_revision(session, fx["admin_reviewer"], symbol_id=other_id, **V2)


def test_a_pending_revision_can_never_reach_a_public_symbol(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, _ = _published(engine, fx)
    with Session(engine) as session:
        create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE governed_symbols SET visibility = 'public' WHERE id = :id"), {"id": symbol_id})


def test_code_that_closes_without_a_reason_is_recorded_as_decided(wp54_fixtures):
    """Rollback safety: release 63599b8 closes a submission naming only status and closed_at."""
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    submission_id = _submit(engine, fx["contributor"], symbol_id, revision_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization_symbol_review_decisions"
                " (id, submission_id, organization_id, governed_symbol_id, symbol_revision_id, decided_by_user_id, decision, decided_at)"
                " SELECT :id, s.id, s.organization_id, s.governed_symbol_id, s.symbol_revision_id, s.submitted_by_user_id, 'approved', now()"
                " FROM organization_symbol_review_submissions s WHERE s.id = :submission"
            ),
            {"id": uuid.uuid4(), "submission": submission_id},
        )
        connection.execute(
            text("UPDATE organization_symbol_review_submissions SET status = 'closed', closed_at = now() WHERE id = :id"),
            {"id": submission_id},
        )
    with Session(engine) as session:
        assert session.get(OrganizationSymbolReviewSubmission, submission_id).closure_reason == "decided"


def test_with_the_flag_off_rejection_still_returns_to_draft_and_nothing_is_published(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, revision_id = _draft(engine, fx)
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, revision_id), "rejected")
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, revision_id), "approved")
    with Session(engine) as session:
        assert session.get(GovernedSymbol, symbol_id).organization_wide is False


def test_approving_a_new_revision_keeps_the_library_setting_the_organization_chose(wp54_fixtures):
    from symgov_backend.organization_symbol_review import set_organization_wide

    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    symbol_id, _ = _published(engine, fx)
    with Session(engine) as session:
        set_organization_wide(session, fx["reviewer"], symbol_id=symbol_id, enabled=False)
        session.commit()
    with Session(engine) as session:
        _, pending = create_new_draft_revision(session, fx["contributor"], symbol_id=symbol_id, **V2)
        session.commit()
        pending_id = pending.id
    _decide(engine, fx["reviewer"], _submit(engine, fx["contributor"], symbol_id, pending_id), "approved", **V2)
    with Session(engine) as session:
        symbol = session.get(GovernedSymbol, symbol_id)
        assert (symbol.current_revision_id, symbol.organization_wide) == (pending_id, False)
    assert "org_symbol.library_changed" in _actions(engine, symbol_id)


def test_the_migration_downgrades_cleanly_and_refuses_while_its_data_exists():
    from test_organization_symbol_postgresql import _alembic, _database

    with _database("symgov-wp2-downgrade") as (engine, url, _raw):
        _alembic(url, "upgrade", "head")
        _alembic(url, "downgrade", "20261021_0075")
        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE column_name IN ('pending_revision_id', 'closure_reason')"
            )).scalar() == 0
        _alembic(url, "upgrade", "head")
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,must_change_pin,is_active,created_at,updated_at)"
                " VALUES (:id,'a@example.test','a','x',now(),false,true,now(),now())"
            ), {"id": uuid.uuid4()})
            author = connection.execute(text("SELECT id FROM users")).scalar()
            symbol = uuid.uuid4()
            connection.execute(text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,visibility,created_at,updated_at)"
                " VALUES (:id,:slug,'n','c','d',:author,'public',now(),now())"
            ), {"id": symbol, "slug": f"s-{symbol}", "author": author})
            connection.execute(text(
                "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,payload_json,author_id,created_at)"
                " VALUES (:id,:symbol,'r1','rejected','{}',:author,now())"
            ), {"id": uuid.uuid4(), "symbol": symbol, "author": author})
        refused = _alembic(url, "downgrade", "20261021_0075", check=False)
        assert refused.returncode != 0
        assert "cannot downgrade 20261024_0076" in refused.stdout + refused.stderr
