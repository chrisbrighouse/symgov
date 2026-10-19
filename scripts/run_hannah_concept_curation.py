#!/usr/bin/env python3
"""Hannah's queue runner: one card per concept target (WP-H3).

The agent worker loads this module, calls `seed_hannah_concept_queue_cards`
each tick and `process_queue_item` for one queued card. Each card runs one
search for one concept (`symgov_backend.hannah_photos.curate_concept`), which
writes its candidates and the target state itself; the card, run and report
are then mirrored into the database like every other agent's.

It replaces `run_hannah_curation.py`'s per-symbol search, which stays in place
until WP-H7 retires it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import bindparam, text  # noqa: E402

from symgov_backend.db import create_session_factory  # noqa: E402
from symgov_backend.hannah_photos import (  # noqa: E402
    curate_concept,
    default_fetch_bytes,
    make_vision_completer,
    max_vision_calls_per_day,
    target_view_from_row,
    vision_calls_in_last_day,
)
from symgov_backend.hannah_targets import ConceptScope, default_http_get_json, readable_name  # noqa: E402
from symgov_backend.models.schema import HannahConceptTarget  # noqa: E402
from symgov_backend.runtime import RuntimePersistenceBridge  # noqa: E402
from symgov_backend.services.llm import resolve_model_for_feature  # noqa: E402


SCHEMA_VERSION = "hannah-curation-v2"
PROMPT_VERSION = "hannah-concept-curation-2026-10-20"
CARD_SOURCE_TYPE = "concept_photo_curation"
OPEN_CARD_STATUSES = ("queued", "running", "searching")
QUEUE_STATUS_FOR_TARGET = {
    "has_photos": "success",
    "has_proposals": "candidate",
    "no_candidates": "completed",
    "searched": "retry_later",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_id(prefix: str, seed: str) -> str:
    return f"{prefix}-{hashlib.sha1(f'{prefix}:{seed}:{utc_now()}'.encode()).hexdigest()[:12]}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def seed_hannah_concept_queue_cards(
    *, db_env_file: str | None, runtime_root: str | Path, limit: int = 10, session_factory: Any = None
) -> dict[str, Any]:
    """Queue one card per target that is due a search and has no open card."""
    queue_dir = Path(runtime_root) / "agent_queue_items"
    queue_dir.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()
    created: list[dict[str, str]] = []
    session_factory = session_factory or create_session_factory(env_file=db_env_file, nopool=True)
    with session_factory() as session:
        hannah_id = session.execute(text("SELECT id::text FROM agent_definitions WHERE slug = 'hannah'")).scalar()
        if hannah_id is None:
            raise RuntimeError("Hannah agent definition is missing.")
        rows = session.execute(
            text(
                """
                SELECT t.id::text AS target_id, c.id::text AS concept_id, c.concept_code, c.concept_kind,
                       r.preferred_name, r.definition, r.aliases_json
                FROM hannah_concept_targets t
                JOIN semantic_concepts c ON c.id = t.semantic_concept_id
                JOIN semantic_concept_revisions r ON r.id = c.current_revision_id
                WHERE t.photographable IN ('yes', 'unsure')
                  AND (t.status = 'pending'
                       OR (t.status IN ('searched', 'no_candidates', 'has_photos') AND t.next_eligible_at <= now()))
                  -- The background-reader visibility floor: only concepts still on a public Catalog symbol.
                  AND EXISTS (
                      SELECT 1 FROM active_public_symbol_projections app
                      JOIN symbol_semantic_assignments a
                        ON a.symbol_revision_id = app.symbol_revision_id
                       AND a.assignment_role = 'primary' AND a.status = 'verified'
                      WHERE a.semantic_concept_id = c.id
                        AND app.visibility = 'public' AND app.owner_organization_id IS NULL
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_queue_items q
                      WHERE q.agent_id = CAST(:agent_id AS uuid) AND q.source_type = :source_type
                        AND q.source_id = c.id AND q.status IN :open
                  )
                ORDER BY (t.status = 'pending') DESC, t.last_attempt_at NULLS FIRST, c.concept_code
                LIMIT :limit
                """
            ).bindparams(bindparam("open", expanding=True)),
            {"agent_id": hannah_id, "source_type": CARD_SOURCE_TYPE, "open": list(OPEN_CARD_STATUSES), "limit": limit},
        ).mappings().all()
        for row in rows:
            name = readable_name(
                ConceptScope(
                    concept_id=row["concept_id"], concept_code=row["concept_code"], concept_kind=row["concept_kind"],
                    preferred_name=row["preferred_name"], definition=row["definition"],
                    aliases=[str(alias) for alias in row["aliases_json"] or []], symbol_count=0,
                )
            )
            card_id = str(uuid.uuid4())
            payload = {
                "task_type": CARD_SOURCE_TYPE,
                "display_name": f"{row['concept_code']} {name}",
                "concept_code": row["concept_code"],
                "concept_name": name,
                "target_id": row["target_id"],
                "stage": "Catalogue curation",
                "started_by": "hannah_seeder",
            }
            session.execute(
                text(
                    "INSERT INTO agent_queue_items (id, agent_id, source_type, source_id, status, priority, payload_json, created_at) "
                    "VALUES (:id, CAST(:agent_id AS uuid), :source_type, CAST(:source_id AS uuid), 'queued', 'medium', "
                    "CAST(:payload AS jsonb), :created_at)"
                ),
                {
                    "id": card_id, "agent_id": hannah_id, "source_type": CARD_SOURCE_TYPE, "source_id": row["concept_id"],
                    "payload": json.dumps(payload), "created_at": created_at,
                },
            )
            write_json(
                queue_dir / f"{card_id}.json",
                {
                    "id": card_id, "agent_id": "hannah", "agent_definition_id": hannah_id, "source_type": CARD_SOURCE_TYPE,
                    "source_id": row["concept_id"], "status": "queued", "priority": "medium", "payload_json": payload,
                    "created_at": created_at, "started_at": None, "completed_at": None,
                },
            )
            created.append({"queue_item_id": card_id, "concept_code": row["concept_code"]})
        session.commit()
    return {"createdCount": len(created), "created": created}


def process_queue_item(
    queue_item_path: str | Path,
    runtime_root: str | Path,
    persist_db: bool = False,
    db_env_file: str | None = None,
    storage_env_file: str | None = None,
) -> dict[str, Any]:
    queue_item_path, runtime_root = Path(queue_item_path), Path(runtime_root)
    queue_item = json.loads(queue_item_path.read_text(encoding="utf-8"))
    if queue_item.get("agent_id") != "hannah":
        raise ValueError("Queue item agent_id must be 'hannah'.")
    if queue_item.get("source_type") != CARD_SOURCE_TYPE:
        # A card from the retired per-symbol search: cancel it once rather than
        # failing on it every worker tick.
        queue_item["status"], queue_item["completed_at"] = "cancelled", utc_now()
        queue_item["escalation_reason"] = "Retired per-symbol Hannah card; concept curation replaces it."
        write_json(queue_item_path, queue_item)
        if persist_db:
            RuntimePersistenceBridge(env_file=db_env_file).upsert_agent_queue_item(queue_item)
        return {"queue_item_id": queue_item.get("id"), "queue_item_status": "cancelled", "db_persistence": None}

    started_at = utc_now()
    queue_item["status"], queue_item["started_at"] = "searching", started_at
    write_json(queue_item_path, queue_item)

    session_factory = create_session_factory(env_file=db_env_file, nopool=True)
    model = resolve_model_for_feature("hannah_vision")
    with session_factory() as session:
        target = session.get(HannahConceptTarget, uuid.UUID(queue_item["payload_json"]["target_id"]))
        if target is None:
            raise RuntimeError("The card's Hannah concept target no longer exists.")
        budget = max(0, max_vision_calls_per_day() - vision_calls_in_last_day(session, datetime.now(timezone.utc)))
        report = curate_concept(
            session,
            target_view_from_row(session, target),
            apply=persist_db,
            http_get_json=default_http_get_json,
            fetch_bytes=default_fetch_bytes,
            complete_vision=make_vision_completer(model, initiator_kind="scheduled_worker", session_factory=session_factory),
            vision_model=model,
            max_vision_calls=budget,
            queue_item_id=queue_item["id"],
        )
    report.update({"queue_item_id": queue_item["id"], "agent": "hannah", "schema_version": SCHEMA_VERSION, "vision_budget": budget})

    completed_at = utc_now()
    queue_item["status"] = QUEUE_STATUS_FOR_TARGET.get(report["target_status"], "completed")
    queue_item["completed_at"] = completed_at
    queue_item["payload_json"] = {
        **queue_item["payload_json"],
        "gathered": report["gathered"],
        "vision_calls": report["vision_calls"],
        "proposed": report["proposed"],
        "target_status": report["target_status"],
    }
    write_json(queue_item_path, queue_item)

    run_record = {
        "id": stamp_id("arun", queue_item["id"]),
        "queue_item_id": queue_item["id"],
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "tool_trace_json": {"source_errors": report["source_errors"], "vision_calls": report["vision_calls"]},
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at,
    }
    artifact_record = {
        "id": stamp_id("aout", queue_item["id"]),
        "queue_item_id": queue_item["id"],
        "artifact_type": "hannah_concept_curation_report",
        "schema_version": SCHEMA_VERSION,
        "payload_json": report,
        "created_at": completed_at,
    }
    write_json(runtime_root / "agent_runs" / f"{run_record['id']}.json", run_record)
    report_path = runtime_root / "agent_output_artifacts" / f"{artifact_record['id']}.json"
    write_json(report_path, artifact_record)

    db_persistence = None
    if persist_db:
        db_persistence = RuntimePersistenceBridge(env_file=db_env_file).persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=artifact_record,
            durable_record={},
            durable_kind="hannah_concept_curation_report",
        )
    return {
        "queue_item_id": queue_item["id"],
        "queue_item_status": queue_item["status"],
        "curation_report_path": str(report_path),
        "db_persistence": db_persistence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Hannah concept curation card.")
    parser.add_argument("--queue-item", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--persist-db", action="store_true")
    parser.add_argument("--db-env-file")
    parser.add_argument("--storage-env-file")
    args = parser.parse_args()
    print(json.dumps(process_queue_item(args.queue_item, args.runtime_root, args.persist_db, args.db_env_file, args.storage_env_file), indent=2))


if __name__ == "__main__":
    main()
