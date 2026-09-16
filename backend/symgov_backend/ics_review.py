"""Operator review of ICS domain crosswalk proposals. Never runs migrations.

`ics_import` proposes; this dispositions. The two are deliberately separate
commands: an import reaches the network and writes an archive, while a review
decision touches one already-governed row and reaches nothing outside the
database. Listing is read-only.

Like the importer, this is a trusted operator interface rather than a public
API endpoint, and it owns its transaction so a failed decision leaves the row
exactly as it was.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .ics_taxonomy import (
    CROSSWALK_REVIEW_STATUSES,
    apply_decision_register,
    describe_domain_crosswalks,
    disposition_crosswalk,
    load_decision_register,
    plan_register_application,
)


class ConfigurationError(ValueError):
    """A missing setting, safe to name. Never carries a credential value."""


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    # hide_parameters keeps bound values out of any driver exception text.
    return create_engine(url, hide_parameters=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="Read-only crosswalk review queue")
    listing.add_argument(
        "--review-status",
        choices=sorted(CROSSWALK_REVIEW_STATUSES),
        help="Only rows in this state; omit for every state",
    )
    listing.add_argument("--import-id", type=uuid.UUID, help="Only rows from this import")

    for decision in ("approve", "reject"):
        command = commands.add_parser(
            decision, help=f"Record a reviewed {decision} on one proposal"
        )
        command.add_argument("--crosswalk-id", type=uuid.UUID, required=True)
        command.add_argument(
            "--reviewer-id",
            type=uuid.UUID,
            required=True,
            help="Existing authorized reviewer UUID; a disposition is never anonymous",
        )
        command.add_argument(
            "--note", help="The reviewer's own rationale, kept apart from the import's reason"
        )

    register = commands.add_parser(
        "apply-register",
        help="Record every decision in the reviewed register (dry-run unless --apply)",
    )
    register.add_argument(
        "--reviewer-id",
        type=uuid.UUID,
        required=True,
        help="Existing authorized reviewer UUID; the register records decisions, not authority",
    )
    register.add_argument(
        "--apply",
        action="store_true",
        help="Write the decisions; without it the plan is printed and nothing is recorded",
    )
    register.add_argument("--import-id", type=uuid.UUID, help="Only rows from this import")
    register.add_argument(
        "--allow-correction",
        action="store_true",
        help="Permit changing a decision already recorded against a row",
    )

    args = parser.parse_args(argv)
    try:
        engine = _engine()
        try:
            if args.command == "list":
                with Session(engine) as session:
                    report = describe_domain_crosswalks(
                        session,
                        import_id=args.import_id,
                        review_status=args.review_status,
                    )
                print(json.dumps(report, indent=2))
                return 0

            if args.command == "apply-register":
                loaded = load_decision_register()
                if not args.apply:
                    # Resolve the plan in a read-only session and roll it back,
                    # so a dry run cannot leave a decision behind.
                    with Session(engine) as session:
                        plan = plan_register_application(
                            loaded,
                            describe_domain_crosswalks(session, import_id=args.import_id),
                            allow_correction=args.allow_correction,
                        )
                        session.rollback()
                    print(json.dumps({"mode": "dry-run", **_summary(loaded, plan)}, indent=2))
                    return 1 if plan["problems"] else 0
                with Session(engine) as session, session.begin():
                    plan = apply_decision_register(
                        session,
                        reviewed_by_user_id=args.reviewer_id,
                        occurred_at=datetime.now(timezone.utc),
                        register=loaded,
                        import_id=args.import_id,
                        allow_correction=args.allow_correction,
                    )
                print(json.dumps({"mode": "apply", **_summary(loaded, plan)}, indent=2))
                return 0

            # A decision commands its own transaction, so an invalid
            # transition or an unknown reviewer rolls back untouched.
            with Session(engine) as session, session.begin():
                row = disposition_crosswalk(
                    session,
                    args.crosswalk_id,
                    target_status="approved" if args.command == "approve" else "rejected",
                    occurred_at=datetime.now(timezone.utc),
                    reviewed_by_user_id=args.reviewer_id,
                    review_note=args.note,
                )
                recorded = {
                    "crosswalk_id": str(row.id),
                    "review_status": row.review_status,
                    "reviewed_by_user_id": str(row.reviewed_by_user_id),
                    "reviewed_at": row.reviewed_at.isoformat(),
                    "review_note": row.review_note,
                }
            print(json.dumps(recorded, indent=2))
            return 0
        finally:
            engine.dispose()
    # ConfigurationError subclasses ValueError, so it is caught first;
    # a sibling handler cannot pick up a re-raise from this same `try`.
    except ConfigurationError as exc:
        # Naming an absent setting leaks nothing; naming its value would.
        print(f"ICS crosswalk review failed: {exc}.", file=sys.stderr)
        return 1
    except ValueError as exc:
        # A register/queue mismatch names only domains and ICS codes, so it is
        # safe to report in full and is the whole point of the dry run.
        if args.command == "apply-register":
            print(f"ICS crosswalk register not applied: {exc}", file=sys.stderr)
            return 1
        print(
            "ICS crosswalk review failed; no decision is recorded. Check the "
            "crosswalk id, the reviewer id and the legality of the transition.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print(
            "ICS crosswalk review could not list the queue; check database readiness."
            if args.command == "list"
            else "ICS crosswalk register not applied; check database readiness."
            if args.command == "apply-register"
            else (
                "ICS crosswalk review failed; no decision is recorded. Check the "
                "crosswalk id, the reviewer id, the legality of the transition "
                "and database readiness."
            ),
            file=sys.stderr,
        )
        return 1


def _summary(register: dict, plan: dict) -> dict:
    return {
        "decided_on": register.get("decided_on"),
        "registered": len(register["decisions"]),
        "recorded": len(plan["planned"]),
        "already_recorded": len(plan["unchanged"]),
        "problems": plan["problems"],
        "actions": plan["planned"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
