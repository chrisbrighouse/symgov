"""Operator retirement of open Coordinated Reviews cases. Never runs migrations.

The Coordinated Reviews queue is every `review_cases` row with no `closed_at`
whose source is a validation report or a provenance assessment. This closes
those rows (and their still-open split children) instead of deleting them, so
decisions, actions and the provenance behind published symbols stay intact.

Dry-run is the default: it reports what would change and writes nothing. Pass
`--apply` to commit. Cases from other sources (for example organization
promotion requests) are never touched. Reversible by clearing `closed_at` and
restoring `current_stage` from the per-case audit event.

Like the other operator CLIs this is a trusted interface, not an API endpoint,
and it owns its transaction so a failure leaves every row as it was.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .models import AuditEvent, ReviewCase, ReviewSplitItem

QUEUE_SOURCE_TYPES = ("validation_report", "provenance_assessment")
RETIRED_STAGE = "retired_historical_cleanup"
# Matches the queue's own definition of an open child (routes/workspace.py
# REVIEW_SPLIT_STATUS_GROUPS["active_review"]). Downstream-queued children are
# already in another agent's hands and are left alone.
OPEN_CHILD_STATUSES = ("awaiting_decision", "returned_for_review", "duplicate_exception")
RETIRED_CHILD_STATUS = "deleted"
AUDIT_ACTION = "review_case_retired_historical_cleanup"


class ConfigurationError(ValueError):
    """A missing setting, safe to name. Never carries a credential value."""


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    return create_engine(url, hide_parameters=True)


def plan_retirement(session: Session) -> dict:
    cases = session.execute(
        select(ReviewCase)
        .where(ReviewCase.closed_at.is_(None))
        .where(ReviewCase.source_entity_type.in_(QUEUE_SOURCE_TYPES))
        .order_by(ReviewCase.opened_at)
    ).scalars().all()
    case_ids = [case.id for case in cases]
    children = []
    if case_ids:
        children = session.execute(
            select(ReviewSplitItem)
            .where(ReviewSplitItem.review_case_id.in_(case_ids))
            .where(ReviewSplitItem.status.in_(OPEN_CHILD_STATUSES))
        ).scalars().all()
    other_open = session.execute(
        select(ReviewCase.source_entity_type)
        .where(ReviewCase.closed_at.is_(None))
        .where(ReviewCase.source_entity_type.notin_(QUEUE_SOURCE_TYPES))
    ).scalars().all()
    return {
        "cases": cases,
        "children": children,
        "untouched_other_sources": dict(Counter(other_open)),
    }


def apply_retirement(session: Session, plan: dict, *, actor_id: uuid.UUID, reason: str, now: datetime) -> None:
    for case in plan["cases"]:
        session.add(
            AuditEvent(
                entity_type="review_case",
                entity_id=case.id,
                action=AUDIT_ACTION,
                actor_id=actor_id,
                payload_json={
                    "reason": reason,
                    "previous_stage": case.current_stage,
                    "source_entity_type": case.source_entity_type,
                    "opened_at": case.opened_at.isoformat() if case.opened_at else None,
                },
                created_at=now,
            )
        )
        case.current_stage = RETIRED_STAGE
        case.closed_at = now
    for child in plan["children"]:
        child.status = RETIRED_CHILD_STATUS
        child.updated_at = now
        child.processed_at = now


def _summary(plan: dict, mode: str) -> dict:
    cases = plan["cases"]
    return {
        "mode": mode,
        "cases": len(cases),
        "split_children": len(plan["children"]),
        "by_source_and_stage": dict(
            Counter(f"{case.source_entity_type}/{case.current_stage}" for case in cases)
        ),
        "oldest_opened_at": cases[0].opened_at.isoformat() if cases else None,
        "newest_opened_at": cases[-1].opened_at.isoformat() if cases else None,
        "untouched_other_sources": plan["untouched_other_sources"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit; omit for a dry run")
    parser.add_argument("--actor-id", type=uuid.UUID, help="User id recorded in the audit events (required with --apply)")
    parser.add_argument("--reason", help="Why these cases are retired (required with --apply)")
    args = parser.parse_args(argv)
    if args.apply and not (args.actor_id and args.reason):
        parser.error("--apply requires --actor-id and --reason")

    try:
        engine = _engine()
        try:
            with Session(engine) as session, session.begin():
                plan = plan_retirement(session)
                # Summarise before applying: apply moves current_stage.
                summary = _summary(plan, "apply" if args.apply else "dry-run")
                if not args.apply:
                    print(json.dumps(summary, indent=2))
                    return 0
                apply_retirement(
                    session,
                    plan,
                    actor_id=args.actor_id,
                    reason=args.reason,
                    now=datetime.now(timezone.utc).replace(microsecond=0),
                )
            print(json.dumps(summary, indent=2))
            return 0
        finally:
            engine.dispose()
    except ConfigurationError as exc:
        print(f"Review queue retirement failed: {exc}.", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print(
            "Review queue retirement failed; nothing was changed. Check the actor id and database readiness.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
