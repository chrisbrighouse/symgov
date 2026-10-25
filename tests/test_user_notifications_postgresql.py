"""Unified submission & review WP6: notification recipients and the
`user_notifications` table (migration 20261024_0078), against real PostgreSQL.

Recipients come from live membership, role and capability rows, which the
SQLite route tests only approximate; the kind check and the cascades are
PostgreSQL-only.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import stage5_database  # noqa: E402,F401
from test_wp54_organization_symbol_review import wp54_fixtures  # noqa: E402,F401

from symgov_backend.models import OrganizationSymbolReviewSubmission, UserNotification  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import decide_submission  # noqa: E402
from symgov_backend.user_notifications import notify_decided, notify_submitted, organization_reviewer_ids  # noqa: E402


def _uid(actor):
    return uuid.UUID(actor.id)


def _submitted(engine, actor):
    with Session(engine) as session:
        symbol, revision = create_draft(session, actor, name="Pump", category="Pumps", discipline="Mechanical", summary="s")
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id
    with Session(engine) as session:
        submission = submit_for_review(session, actor, symbol_id=symbol_id, revision_id=revision_id)
        notify_submitted(session, submission)
        session.commit()
        return submission.id


def test_a_submission_reaches_every_org_admin_and_reviewer_but_not_the_submitter_or_another_org(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    submission_id = _submitted(engine, fx["admin_reviewer"])
    with Session(engine) as session:
        recipients = set(session.execute(
            select(UserNotification.user_id).where(UserNotification.submission_id == submission_id)
        ).scalars())
        assert recipients == {_uid(fx[name]) for name in ("reviewer", "second_reviewer", "admin_without_capability")}
        assert organization_reviewer_ids(session, fx["other_organization"]) == {_uid(fx["cross_org_reviewer"])}


def test_a_decision_reaches_the_submitter_and_the_kind_is_checked(wp54_fixtures):
    fx, engine = wp54_fixtures, wp54_fixtures["engine"]
    submission_id = _submitted(engine, fx["contributor"])
    with Session(engine) as session:
        decision = decide_submission(session, fx["reviewer"], submission_id=submission_id, decision="rejected", workflow_v2=True)
        [notice] = notify_decided(session, decision)
        session.commit()
        assert (notice.user_id, notice.kind) == (_uid(fx["contributor"]), "org_symbol.rejected")
        assert notice.payload_json["symbolName"] == "Pump"

    with Session(engine) as session:
        submission = session.get(OrganizationSymbolReviewSubmission, submission_id)
        session.add(UserNotification(
            id=uuid.uuid4(), user_id=_uid(fx["contributor"]), organization_id=submission.organization_id,
            kind="org_symbol.assigned", created_at=datetime.now(timezone.utc),
        ))
        # The bare name in the migration gains the convention prefix exactly once.
        with pytest.raises(IntegrityError, match='"ck_user_notifications_kind"'):
            session.flush()
