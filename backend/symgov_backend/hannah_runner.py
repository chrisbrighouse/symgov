"""Hannah's queue runner: one card per concept target (WP-H3, shared with WP-H4).

The agent worker loads `scripts/run_hannah_concept_curation.py`, which
re-exports this module: it calls `seed_hannah_concept_queue_cards` each tick
and `process_queue_item` for one queued card. The Workspace queues cards with
`create_concept_card` / the seeder and runs them itself with `run_cards` when
Hannah is off the worker. Each card runs one search for one concept
(`hannah_photos.curate_concept`), which writes its candidates and the target
state itself; the card, run and report are then mirrored into the database
like every other agent's.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from .db import create_session_factory
from .hannah_photos import (
    auto_approve_min_score,
    curate_concept,
    default_fetch_bytes,
    make_vision_completer,
    max_vision_calls_per_day,
    target_view_from_row,
    vision_calls_in_last_day,
)
from .hannah_review import HannahReviewError, auto_approve
from .hannah_targets import ConceptScope, default_http_get_json, readable_name
from .models.schema import HannahConceptTarget
from .runtime import RuntimePersistenceBridge, upload_object_bytes
from .services.llm import resolve_model_for_feature


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


CARD_QUERY = """
    SELECT t.id::text AS target_id, c.id::text AS concept_id, c.concept_code, c.concept_kind,
           r.preferred_name, r.definition, r.aliases_json
    FROM hannah_concept_targets t
    JOIN semantic_concepts c ON c.id = t.semantic_concept_id
    JOIN semantic_concept_revisions r ON r.id = c.current_revision_id
"""


def create_concept_card(
    session: Any,
    *,
    hannah_id: str,
    row: Any,
    runtime_root: str | Path,
    created_at: str,
    status: str = "queued",
    started_by: str = "hannah_seeder",
) -> dict[str, Any]:
    """Write one concept card to the database and the runtime queue folder.

    `status="running"` keeps the worker's queued-card scan away from a card the
    caller is about to run itself.
    """
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
        "started_by": started_by,
    }
    session.execute(
        text(
            "INSERT INTO agent_queue_items (id, agent_id, source_type, source_id, status, priority, payload_json, created_at) "
            "VALUES (:id, CAST(:agent_id AS uuid), :source_type, CAST(:source_id AS uuid), :status, 'medium', "
            "CAST(:payload AS jsonb), :created_at)"
        ),
        {
            "id": card_id, "agent_id": hannah_id, "source_type": CARD_SOURCE_TYPE, "source_id": row["concept_id"],
            "status": status, "payload": json.dumps(payload), "created_at": created_at,
        },
    )
    path = Path(runtime_root) / "agent_queue_items" / f"{card_id}.json"
    write_json(
        path,
        {
            "id": card_id, "agent_id": "hannah", "agent_definition_id": hannah_id, "source_type": CARD_SOURCE_TYPE,
            "source_id": row["concept_id"], "status": status, "priority": "medium", "payload_json": payload,
            "created_at": created_at, "started_at": None, "completed_at": None,
        },
    )
    return {"id": card_id, "path": str(path)}


def hannah_agent_id(session: Any) -> str:
    hannah_id = session.execute(text("SELECT id::text FROM agent_definitions WHERE slug = 'hannah'")).scalar()
    if hannah_id is None:
        raise RuntimeError("Hannah agent definition is missing.")
    return hannah_id


def open_card_exists(session: Any, *, hannah_id: str, concept_id: str) -> bool:
    return bool(
        session.execute(
            text(
                "SELECT 1 FROM agent_queue_items WHERE agent_id = CAST(:agent_id AS uuid) AND source_type = :source_type "
                "AND source_id = CAST(:concept_id AS uuid) AND status IN :open LIMIT 1"
            ).bindparams(bindparam("open", expanding=True)),
            {"agent_id": hannah_id, "source_type": CARD_SOURCE_TYPE, "concept_id": concept_id, "open": list(OPEN_CARD_STATUSES)},
        ).scalar()
    )


def seed_hannah_concept_queue_cards(
    *,
    db_env_file: str | None,
    runtime_root: str | Path,
    limit: int = 10,
    session_factory: Any = None,
    status: str = "queued",
    started_by: str = "hannah_seeder",
) -> dict[str, Any]:
    """Queue one card per target that is due a search and has no open card.

    The worker's queue is topped up to `limit` open cards rather than grown by
    `limit` each tick; a Workspace "search now" (`status="running"`) is not
    capped that way. Nothing is queued while the daily vision budget is used up.
    """
    queue_dir = Path(runtime_root) / "agent_queue_items"
    queue_dir.mkdir(parents=True, exist_ok=True)
    created_at = utc_now()
    created: list[dict[str, str]] = []
    session_factory = session_factory or create_session_factory(env_file=db_env_file, nopool=True)
    with session_factory() as session:
        hannah_id = hannah_agent_id(session)
        if max_vision_calls_per_day() - vision_calls_in_last_day(session, datetime.now(timezone.utc)) <= 0:
            return {"createdCount": 0, "created": [], "skipped": "vision_budget_used_up"}
        if status == "queued":
            open_cards = session.execute(
                text(
                    "SELECT count(*) FROM agent_queue_items WHERE agent_id = CAST(:agent_id AS uuid) "
                    "AND source_type = :source_type AND status IN :open"
                ).bindparams(bindparam("open", expanding=True)),
                {"agent_id": hannah_id, "source_type": CARD_SOURCE_TYPE, "open": list(OPEN_CARD_STATUSES)},
            ).scalar()
            limit = max(0, limit - int(open_cards or 0))
            if limit == 0:
                return {"createdCount": 0, "created": [], "skipped": "queue_full"}
        rows = session.execute(
            text(
                CARD_QUERY
                + """
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
            card = create_concept_card(
                session,
                hannah_id=hannah_id,
                row=row,
                runtime_root=runtime_root,
                created_at=created_at,
                status=status,
                started_by=started_by,
            )
            created.append({"queue_item_id": card["id"], "concept_code": row["concept_code"], "path": card["path"]})
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
        if budget == 0:
            # Searching now would gather candidates no model can check and park
            # the concept for hours; end the card and leave the target due.
            queue_item["status"], queue_item["completed_at"] = "retry_later", utc_now()
            queue_item["escalation_reason"] = "Hannah's daily vision budget is used up; the concept stays due."
            write_json(queue_item_path, queue_item)
            if persist_db:
                RuntimePersistenceBridge(env_file=db_env_file).upsert_agent_queue_item(queue_item)
            return {"queue_item_id": queue_item["id"], "queue_item_status": "retry_later", "db_persistence": None}
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
        min_score = auto_approve_min_score()
        report["auto_approve_min_score"] = min_score
        if persist_db and min_score is not None and report["proposed"]:
            try:
                report["auto_approved"] = auto_approve(
                    session,
                    target.semantic_concept_id,
                    min_score=min_score,
                    fetch_bytes=default_fetch_bytes,
                    store_object=lambda key, payload, content_type: upload_object_bytes(
                        object_key=key, payload=payload, content_type=content_type, env_file=storage_env_file
                    ),
                )
            except HannahReviewError as exc:
                report["auto_approved"] = {"approved": [], "failed": [{"error": exc.detail}]}
            session.refresh(target)
            report["target_status"] = target.status
            report["proposed"] = session.execute(
                text("SELECT count(*) FROM hannah_concept_photos WHERE semantic_concept_id = :id AND status = 'proposed'"),
                {"id": target.semantic_concept_id},
            ).scalar()
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


def run_cards(card_paths: list[str], *, runtime_root: str | Path, db_env_file: str | None) -> list[dict[str, Any]]:
    """Run cards one after another, as the Workspace does when Hannah is off the worker.

    A card stopped from the Workspace before its turn is skipped; one already
    running finishes its concept.
    """
    results = []
    for card_path in card_paths:
        try:
            card = json.loads(Path(card_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if card.get("status") not in ("queued", "running"):
            continue
        try:
            results.append(process_queue_item(card_path, runtime_root, persist_db=True, db_env_file=db_env_file))
        except Exception as exc:  # one failed concept must not stop the rest
            results.append({"queue_item_id": card.get("id"), "error": f"{type(exc).__name__}: {exc}"})
    return results
