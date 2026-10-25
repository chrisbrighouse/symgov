"""Unified submission & review WP3: who may decide a private submission, by the
organization's review policy (spec draft 2 FR8), against real PostgreSQL.

With the workflow v2 flag on:
- self-approval is on by default, so a submitter decides their own submission
  even without the Org Reviewer capability (D3);
- with it off, the decider must be an Org Reviewer or Org Admin other than the
  submitter (A2);
- an Org Admin may decide without the Org Reviewer capability (A1);
- a plain member who did not submit can never decide.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import stage5_database  # noqa: E402,F401
from test_wp54_organization_symbol_review import wp54_fixtures  # noqa: E402,F401

from symgov_backend.models import AuditEvent, SymbolRevision  # noqa: E402
from symgov_backend.organization_review_policy import get_review_policy, set_review_policy  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import (  # noqa: E402
    OrganizationSymbolReviewError,
    OrganizationSymbolReviewNotVisible,
    decide_submission,
)


def _submitted(engine, actor, name="Pump"):
    with Session(engine) as session:
        symbol, revision = create_draft(session, actor, name=name, category="Pumps", discipline="Mechanical", summary="s")
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id
    with Session(engine) as session:
        submission = submit_for_review(session, actor, symbol_id=symbol_id, revision_id=revision_id)
        session.commit()
        return symbol_id, revision_id, submission.id


def _decide(engine, actor, submission_id, decision="approved"):
    with Session(engine) as session:
        row = decide_submission(session, actor, submission_id=submission_id, decision=decision, workflow_v2=True)
        session.commit()
        return row.decision


def _set_policy(engine, fx, allow):
    with Session(engine) as session:
        set_review_policy(
            session, fx["admin_reviewer"], fx["organization"], allow_self_approval=allow,
            effective_authority="organization_admin",
        )
        session.commit()


def test_by_default_a_submitter_approves_their_own_submission(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    with Session(engine) as session:
        assert get_review_policy(session, fx["organization"]).allow_self_approval is True
    symbol_id, revision_id, submission_id = _submitted(engine, fx["contributor"])
    _decide(engine, fx["contributor"], submission_id)
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"
        [event] = session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == symbol_id, AuditEvent.action == "org_symbol.decided")
        ).scalars().all()
        assert (event.payload_json["authority"], event.payload_json["self_approved"]) == ("self_approval", True)


def test_a_member_who_did_not_submit_cannot_decide(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    _, _, submission_id = _submitted(engine, fx["admin_reviewer"])
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError):
            decide_submission(session, fx["contributor"], submission_id=submission_id, decision="approved", workflow_v2=True)


def test_with_self_approval_off_the_submitter_is_refused_and_another_reviewer_decides(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    _set_policy(engine, fx, False)
    _, revision_id, submission_id = _submitted(engine, fx["admin_reviewer"])
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewError, match="another Org Reviewer"):
            decide_submission(session, fx["admin_reviewer"], submission_id=submission_id, decision="approved", workflow_v2=True)
    _decide(engine, fx["reviewer"], submission_id)
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "approved"


def test_an_org_admin_decides_without_the_org_reviewer_capability(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    _set_policy(engine, fx, False)
    _, revision_id, submission_id = _submitted(engine, fx["contributor"])
    assert _decide(engine, fx["admin_without_capability"], submission_id, "changes_requested") == "changes_requested"
    with Session(engine) as session:
        assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"


def test_another_organization_still_cannot_see_the_submission(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    _, _, submission_id = _submitted(engine, fx["contributor"])
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolReviewNotVisible):
            decide_submission(session, fx["cross_org_reviewer"], submission_id=submission_id, decision="approved", workflow_v2=True)


def test_changing_the_policy_is_audited_and_attributed(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    _set_policy(engine, fx, False)
    _set_policy(engine, fx, True)
    with Session(engine) as session:
        policy = get_review_policy(session, fx["organization"])
        assert policy.allow_self_approval is True
        assert policy.updated_by_user_id == uuid.UUID(fx["admin_reviewer"].id)
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "org_review_policy.updated", AuditEvent.entity_id == fx["organization"]
            )
        ).scalars().all()
        # Timestamps are whole seconds, so compare without relying on order.
        assert sorted((e.payload_json["before"]["allow_self_approval"], e.payload_json["after"]["allow_self_approval"]) for e in events) == [
            (False, True), (True, False),
        ]
