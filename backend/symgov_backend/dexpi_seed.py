"""Seed the DEXPI pilot's semantic concepts. Never runs migrations.

`dexpi_concepts` decides what the concepts are; this records them. Like
`ics_import` and `ics_review` it is a trusted operator interface rather than an
API endpoint, it owns its transaction, and `plan` reaches the database only to
read.

**Why this is a command and not a migration.** The pilot plan put the concept
seed in Alembic, following `20260909_0052`, which seeded the CFIHOS
classification schemes. Classification nodes have no author.
`semantic_concepts.created_by_user_id` and
`semantic_concept_revisions.author_id` are both NOT NULL foreign keys to
`users`, so 83 concepts cannot be seeded without naming who created them -- and
a migration has no one to name. It would also fail outright in the disposable
PostgreSQL rehearsal, where Alembic runs against a database that has no users
at all. Decision D8 (2026-09-23) settles it here instead, where `--actor-id`
makes the author an argument the operator has to supply.

**Seeding is not idempotent by luck, so it is idempotent by check.** A concept
is identified by its `SGC-` code, allocated from a sequence, so re-running
cannot recognise its own earlier work by primary key. The existing concepts are
indexed by preferred name before anything is written, and a name already
present is left exactly as it is: reported as unchanged, or as drift when the
stored content no longer matches the plan. This command never rewrites a
concept -- changing a published concept is a governed revision, and that
belongs to a reviewer, not to a seed script.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .models import SemanticConcept, SemanticConceptRevision
from .semantic_concepts import (
    create_semantic_concept,
    transition_semantic_concept_revision,
)
from .services.dexpi_concepts import plan_concepts

DEFAULT_SELECTION_PATH = "integrations/dexpi/selection.json"

# draft -> published, one legal step at a time. `create_semantic_concept` opens
# the revision in `draft`, and only `published` makes the concept active and
# therefore assignable, so the seed has to walk the whole lifecycle rather than
# stop at `approved`.
_PUBLISH_SEQUENCE = ("review", "approved", "published")

_REVISION_LABEL = "r1"


class ConfigurationError(ValueError):
    """A missing setting, safe to name. Never carries a credential value."""


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    # hide_parameters keeps bound values out of any driver exception text.
    return create_engine(url, hide_parameters=True)


def load_plan(selection_path: str | Path) -> dict:
    path = Path(selection_path)
    if not path.is_file():
        raise ConfigurationError(f"selection manifest not found at {path}")
    return plan_concepts(json.loads(path.read_text(encoding="utf-8")))


def index_existing(session: Session) -> dict[str, dict]:
    """Every concept already recorded, keyed by folded preferred name.

    Revisions are read rather than `current_revision_id`, because a run that
    created concepts but failed before publishing them leaves that column null
    and those concepts still must not be created a second time. The newest
    revision wins where a concept has several.
    """
    rows = session.execute(
        select(SemanticConcept, SemanticConceptRevision)
        .join(SemanticConceptRevision, SemanticConceptRevision.concept_id == SemanticConcept.id)
        .order_by(SemanticConceptRevision.created_at, SemanticConceptRevision.revision_label)
    ).all()
    index: dict[str, dict] = {}
    for concept, revision in rows:
        index[revision.preferred_name.casefold()] = {
            "concept_id": concept.id,
            "concept_code": concept.concept_code,
            "status": concept.status,
            "concept_kind": concept.concept_kind,
            "lifecycle_state": revision.lifecycle_state,
            "definition": revision.definition,
            "aliases": list(revision.aliases_json or []),
        }
    return index


def _drift(planned: dict, existing: dict) -> list[str]:
    differences = []
    if existing["concept_kind"] != planned["concept_kind"]:
        differences.append(
            f"kind is {existing['concept_kind']}, plan says {planned['concept_kind']}"
        )
    if existing["definition"] != planned["definition"]:
        differences.append("definition differs from the plan")
    if sorted(existing["aliases"]) != sorted(planned["aliases"]):
        differences.append(
            f"aliases differ ({len(existing['aliases'])} stored, {len(planned['aliases'])} planned)"
        )
    if existing["status"] != "active":
        differences.append(f"concept status is {existing['status']}, not active")
    return differences


def resolve(plan: dict, existing: dict[str, dict]) -> dict:
    """Split the planned concepts into what to create, keep, and report."""
    to_create, unchanged, drifted = [], [], []
    for concept in plan["concepts"]:
        match = existing.get(concept["preferred_name"].casefold())
        if match is None:
            to_create.append(concept)
            continue
        differences = _drift(concept, match)
        record = {
            "concept_key": concept["concept_key"],
            "concept_code": match["concept_code"],
        }
        if differences:
            drifted.append({**record, "differences": differences})
        else:
            unchanged.append(record)
    return {"create": to_create, "unchanged": unchanged, "drift": drifted}


def seed(
    session: Session,
    *,
    plan: dict,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict:
    """Create and publish every planned concept that is not already recorded."""
    resolution = resolve(plan, index_existing(session))
    created = []
    for planned in resolution["create"]:
        concept, revision = create_semantic_concept(
            session,
            concept_kind=planned["concept_kind"],
            preferred_name=planned["preferred_name"],
            definition=planned["definition"],
            created_by_user_id=actor_id,
            created_at=occurred_at,
            revision_label=_REVISION_LABEL,
            aliases=planned["aliases"],
            notes=planned["notes"],
            rationale=planned["rationale"],
        )
        for target_state in _PUBLISH_SEQUENCE:
            transition_semantic_concept_revision(
                session,
                revision.id,
                target_state=target_state,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
        created.append(
            {
                "concept_key": planned["concept_key"],
                "concept_code": concept.concept_code,
                "concept_id": str(concept.id),
                "concept_kind": planned["concept_kind"],
                "aliases": len(planned["aliases"]),
            }
        )
    return {
        "created": created,
        "unchanged": resolution["unchanged"],
        "drift": resolution["drift"],
    }


def concept_map(session: Session, plan: dict) -> dict:
    """`concept_key` -> the recorded code and id, for the ingestion driver.

    WP4 assigns a concept per symbol and needs the codes this run allocated;
    resolving them here once, from storage, keeps the driver from having to
    repeat the name match.
    """
    existing = index_existing(session)
    mapping, missing = {}, []
    for concept in plan["concepts"]:
        match = existing.get(concept["preferred_name"].casefold())
        if match is None:
            missing.append(concept["concept_key"])
            continue
        mapping[concept["concept_key"]] = {
            "concept_code": match["concept_code"],
            "concept_id": str(match["concept_id"]),
        }
    return {
        "schema_version": "1.0",
        "concepts": mapping,
        "missing": missing,
        "assignments": plan["assignments"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("plan", "Read-only: what the seed would create, keep and flag"),
        ("apply", "Create and publish every planned concept not already recorded"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--selection",
            default=DEFAULT_SELECTION_PATH,
            help=f"WP2 selection manifest (default {DEFAULT_SELECTION_PATH})",
        )
        command.add_argument(
            "--output",
            help="Write the concept_key -> concept_code map here for the ingestion driver",
        )
        if name == "apply":
            command.add_argument(
                "--actor-id",
                type=uuid.UUID,
                required=True,
                help="Existing user UUID; a governed concept is never authored anonymously",
            )
    return parser


def _report(plan: dict, *, mode: str, concepts: list, unchanged: list, drift: list) -> dict:
    """One report shape for both commands: a dry run says `would_create` of the
    same concepts an apply reports as `created`, so the two can be diffed."""
    return {
        "mode": mode,
        "planned_concepts": plan["summary"]["concept_count"],
        "assigned_symbols": plan["summary"]["assigned_symbols"],
        "kind_counts": plan["summary"]["kind_counts"],
        "created" if mode == "apply" else "would_create": len(concepts),
        "unchanged": len(unchanged),
        "drift": drift,
        "concepts": concepts,
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = load_plan(args.selection)
        engine = _engine()
        try:
            if args.command == "plan":
                with Session(engine) as session:
                    resolution = resolve(plan, index_existing(session))
                    mapping = concept_map(session, plan) if args.output else None
                    # Nothing was written, but roll back explicitly so the
                    # read transaction cannot outlive the report.
                    session.rollback()
                report = _report(
                    plan,
                    mode="dry-run",
                    concepts=[
                        {
                            "concept_key": item["concept_key"],
                            "concept_kind": item["concept_kind"],
                            "aliases": len(item["aliases"]),
                        }
                        for item in resolution["create"]
                    ],
                    unchanged=resolution["unchanged"],
                    drift=resolution["drift"],
                )
            else:
                with Session(engine) as session, session.begin():
                    result = seed(
                        session,
                        plan=plan,
                        actor_id=args.actor_id,
                        occurred_at=datetime.now(timezone.utc),
                    )
                    mapping = concept_map(session, plan) if args.output else None
                report = _report(
                    plan,
                    mode="apply",
                    concepts=result["created"],
                    unchanged=result["unchanged"],
                    drift=result["drift"],
                )

            if args.output:
                Path(args.output).write_text(
                    json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                report["output"] = args.output
                report["mapped_concepts"] = len(mapping["concepts"])
                report["unmapped_concepts"] = mapping["missing"]
            print(json.dumps(report, indent=2))
            return 1 if report["drift"] else 0
        finally:
            engine.dispose()
    except ConfigurationError as exc:
        # Naming an absent setting leaks nothing; naming its value would.
        print(f"DEXPI concept seed failed: {exc}.", file=sys.stderr)
        return 1
    except ValueError as exc:
        # Plan and lifecycle errors name concepts and states only.
        print(f"DEXPI concept seed failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print(
            "DEXPI concept seed failed; nothing was recorded. Check the actor id, "
            "the selection manifest and database readiness.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
