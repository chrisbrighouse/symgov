#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if not BACKEND_ROOT.exists():
    BACKEND_ROOT = Path("/data/symgov/backend")
if not BACKEND_ROOT.exists():
    BACKEND_ROOT = Path("/data/.openclaw/workspace/symgov/backend")
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from symgov_backend.runtime import RuntimePersistenceBridge


SCHEMA_VERSION = "whitney-market-intelligence-v1"
PROMPT_VERSION = "whitney-market-intelligence-2026-05-22"
DEFAULT_DURATION_SECONDS = 120
MAX_DURATION_SECONDS = 300
LOOKBACK_DAYS = 180


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"symgov:whitney:{seed}"))


def stamp_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{seed}:{utc_now()}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def add_trace(trace: list[dict[str, str]], step: str, status: str, detail: str) -> None:
    trace.append({"step": step, "status": status, "detail": detail, "recorded_at": utc_now()})


def clamp_score(value: float) -> float:
    return round(max(0.0, min(0.99, value)), 4)


def segment_label(discipline: str | None, category: str | None) -> str:
    parts = [part for part in (discipline, category) if part]
    return " / ".join(parts) if parts else "Unclassified market"


def load_market_inputs(db_env_file: str | None, trace: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    bridge = RuntimePersistenceBridge(env_file=db_env_file)
    with bridge.session_scope() as session:
        published_segments = session.execute(
            text(
                """
                SELECT
                    gs.discipline,
                    gs.category,
                    count(*) AS published_count,
                    max(pp.updated_at) AS last_published_at
                FROM published_pages pp
                JOIN publication_packs pk ON pk.id = pp.pack_id
                JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
                JOIN governed_symbols gs ON gs.id = sr.symbol_id
                WHERE pk.status = 'published'
                    AND pk.audience = 'public'
                    AND sr.lifecycle_state = 'published'
                GROUP BY gs.discipline, gs.category
                ORDER BY published_count ASC, gs.discipline, gs.category
                LIMIT 50
                """
            )
        ).mappings().all()
        clarification_rows = session.execute(
            text(
                """
                SELECT
                    cr.symbol_id::text AS symbol_id,
                    cr.published_page_id::text AS published_page_id,
                    gs.slug AS symbol_slug,
                    gs.canonical_name AS symbol_name,
                    gs.discipline,
                    gs.category,
                    pp.title AS page_title,
                    count(*) AS clarification_count,
                    max(cr.created_at) AS last_clarification_at
                FROM clarification_records cr
                JOIN governed_symbols gs ON gs.id = cr.symbol_id
                JOIN published_pages pp ON pp.id = cr.published_page_id
                WHERE cr.created_at >= now() - (:lookback_days || ' days')::interval
                GROUP BY cr.symbol_id, cr.published_page_id, gs.slug, gs.canonical_name, gs.discipline, gs.category, pp.title
                ORDER BY clarification_count DESC, last_clarification_at DESC
                LIMIT 25
                """
            ),
            {"lookback_days": LOOKBACK_DAYS},
        ).mappings().all()
        intake_rows = session.execute(
            text(
                """
                SELECT
                    COALESCE(NULLIF(normalized_submission_json->>'candidate_title', ''), submission_kind, 'Unlabelled submission') AS candidate_title,
                    submission_kind,
                    count(*) AS submission_count,
                    max(created_at) AS last_submitted_at
                FROM intake_records
                WHERE created_at >= now() - (:lookback_days || ' days')::interval
                GROUP BY COALESCE(NULLIF(normalized_submission_json->>'candidate_title', ''), submission_kind, 'Unlabelled submission'), submission_kind
                ORDER BY submission_count DESC, last_submitted_at DESC
                LIMIT 25
                """
            ),
            {"lookback_days": LOOKBACK_DAYS},
        ).mappings().all()
        review_rows = session.execute(
            text(
                """
                SELECT
                    current_stage,
                    escalation_level,
                    count(*) AS open_count,
                    min(opened_at) AS oldest_opened_at,
                    max(opened_at) AS newest_opened_at
                FROM review_cases
                WHERE closed_at IS NULL
                GROUP BY current_stage, escalation_level
                ORDER BY open_count DESC, oldest_opened_at ASC
                LIMIT 25
                """
            )
        ).mappings().all()

    add_trace(
        trace,
        "load_internal_market_inputs",
        "passed",
        (
            f"{len(published_segments)} published segments, {len(clarification_rows)} clarification groups, "
            f"{len(intake_rows)} intake groups, {len(review_rows)} open review groups."
        ),
    )
    return {
        "published_segments": [dict(row) for row in published_segments],
        "clarifications": [dict(row) for row in clarification_rows],
        "intake": [dict(row) for row in intake_rows],
        "reviews": [dict(row) for row in review_rows],
    }


def build_signals(inputs: dict[str, list[dict[str, Any]]], focus: str | None, trace: list[dict[str, str]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    focus_normalized = str(focus or "").strip().lower()

    for row in inputs["clarifications"]:
        if focus_normalized and focus_normalized not in json.dumps(row, default=str).lower():
            continue
        count = int(row.get("clarification_count") or 0)
        source_ref = f"clarification:{row['symbol_id']}:{row['published_page_id']}"
        signals.append(
            {
                "id": stable_uuid(source_ref),
                "signal_type": "clarification_demand",
                "market_segment": segment_label(row.get("discipline"), row.get("category")),
                "discipline": row.get("discipline"),
                "category": row.get("category"),
                "source_type": "clarification_records",
                "source_ref": source_ref,
                "symbol_id": row.get("symbol_id"),
                "published_page_id": row.get("published_page_id"),
                "title": f"Clarification demand for {row.get('symbol_name')}",
                "summary": f"{count} clarification request(s) in the last {LOOKBACK_DAYS} days.",
                "demand_score": clamp_score(0.4 + count * 0.12),
                "confidence": clamp_score(0.72 + min(count, 3) * 0.06),
                "recommended_action": "Review Standards copy, examples, and lookup aliases for this symbol.",
                "status": "active",
                "evidence": {
                    "symbol_slug": row.get("symbol_slug"),
                    "page_title": row.get("page_title"),
                    "clarification_count": count,
                    "last_clarification_at": str(row.get("last_clarification_at") or ""),
                },
            }
        )

    for row in inputs["intake"]:
        if focus_normalized and focus_normalized not in json.dumps(row, default=str).lower():
            continue
        count = int(row.get("submission_count") or 0)
        title = str(row.get("candidate_title") or "Unlabelled submission")
        source_ref = f"intake:{title.lower()}:{row.get('submission_kind') or 'unknown'}"
        signals.append(
            {
                "id": stable_uuid(source_ref),
                "signal_type": "submission_interest",
                "market_segment": row.get("submission_kind") or "External submissions",
                "source_type": "intake_records",
                "source_ref": source_ref,
                "title": f"Submission interest: {title}",
                "summary": f"{count} accepted or attempted intake item(s) match this title/kind in the last {LOOKBACK_DAYS} days.",
                "demand_score": clamp_score(0.32 + count * 0.1),
                "confidence": clamp_score(0.62 + min(count, 4) * 0.06),
                "recommended_action": "Check whether this demand points to a missing Standards page, common alias, or source pack priority.",
                "status": "active",
                "evidence": {
                    "submission_kind": row.get("submission_kind"),
                    "submission_count": count,
                    "last_submitted_at": str(row.get("last_submitted_at") or ""),
                },
            }
        )

    for row in inputs["reviews"]:
        count = int(row.get("open_count") or 0)
        source_ref = f"review:{row.get('current_stage')}:{row.get('escalation_level')}"
        signals.append(
            {
                "id": stable_uuid(source_ref),
                "signal_type": "review_pressure",
                "market_segment": "Governance throughput",
                "source_type": "review_cases",
                "source_ref": source_ref,
                "title": f"Open review pressure: {row.get('current_stage')}",
                "summary": f"{count} open case(s) at {row.get('current_stage')} with {row.get('escalation_level')} escalation.",
                "demand_score": clamp_score(0.3 + count * 0.08),
                "confidence": 0.82,
                "recommended_action": "Prioritize queue reduction before promoting related symbols more broadly.",
                "status": "active",
                "evidence": {
                    "current_stage": row.get("current_stage"),
                    "escalation_level": row.get("escalation_level"),
                    "oldest_opened_at": str(row.get("oldest_opened_at") or ""),
                    "newest_opened_at": str(row.get("newest_opened_at") or ""),
                    "open_count": count,
                },
            }
        )

    for row in inputs["published_segments"]:
        count = int(row.get("published_count") or 0)
        if count >= 3:
            continue
        if focus_normalized and focus_normalized not in json.dumps(row, default=str).lower():
            continue
        source_ref = f"coverage:{row.get('discipline') or 'unknown'}:{row.get('category') or 'unknown'}"
        signals.append(
            {
                "id": stable_uuid(source_ref),
                "signal_type": "catalogue_coverage_gap",
                "market_segment": segment_label(row.get("discipline"), row.get("category")),
                "discipline": row.get("discipline"),
                "category": row.get("category"),
                "source_type": "published_catalogue",
                "source_ref": source_ref,
                "title": f"Thin published coverage: {segment_label(row.get('discipline'), row.get('category'))}",
                "summary": f"Only {count} published public symbol(s) currently cover this segment.",
                "demand_score": clamp_score(0.55 - count * 0.08),
                "confidence": 0.68,
                "recommended_action": "Compare incoming submissions and source packages against this segment before the next publication batch.",
                "status": "watch",
                "evidence": {
                    "published_count": count,
                    "last_published_at": str(row.get("last_published_at") or ""),
                },
            }
        )

    signals.sort(key=lambda item: (item.get("demand_score") or 0), reverse=True)
    add_trace(trace, "build_demand_signals", "passed", f"{len(signals)} demand signal(s) generated.")
    return signals[:100]


def build_recommendations(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top = signals[:5]
    recommendations = []
    for rank, signal in enumerate(top, start=1):
        recommendations.append(
            {
                "rank": rank,
                "signal_id": signal["id"],
                "title": signal["title"],
                "recommended_action": signal.get("recommended_action"),
                "demand_score": signal.get("demand_score"),
            }
        )
    return recommendations


def run_market_intelligence_task(task: dict[str, Any], db_env_file: str | None = None) -> dict[str, Any]:
    started_monotonic = time.monotonic()
    duration_seconds = max(30, min(int(task.get("duration_seconds") or DEFAULT_DURATION_SECONDS), MAX_DURATION_SECONDS))
    trace: list[dict[str, str]] = []
    inputs = load_market_inputs(db_env_file, trace)
    signals = build_signals(inputs, task.get("focus"), trace)
    recommendations = build_recommendations(signals)
    elapsed_seconds = int(time.monotonic() - started_monotonic)
    summary = (
        f"Whitney found {len(signals)} demand signal(s) from internal telemetry; "
        f"{len(recommendations)} recommendation(s) are ready for operator review."
    )
    return {
        "queue_item_id": task.get("queue_item_id") or "untracked",
        "agent": "whitney",
        "schema_version": SCHEMA_VERSION,
        "task_type": "market_demand_scan",
        "decision": "signals_recorded",
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "summary": summary,
        "signals": signals,
        "recommendations": recommendations,
        "evidence": {
            "lookback_days": LOOKBACK_DAYS,
            "focus": task.get("focus"),
            "input_counts": {key: len(value) for key, value in inputs.items()},
        },
        "evidence_trace": trace,
    }


def queue_item_payload_to_task(queue_item: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def process_queue_item(queue_item_path: str | Path, runtime_root: str | Path, persist_db: bool = False, db_env_file: str | None = None) -> dict[str, Any]:
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)
    queue_item = load_json(queue_item_path)
    if queue_item.get("agent_id") != "whitney":
        raise ValueError("Queue item agent_id must be 'whitney'.")

    started_at = utc_now()
    queue_item["status"] = "sensing"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)

    artifact = run_market_intelligence_task(queue_item_payload_to_task(queue_item), db_env_file=db_env_file)
    completed_at = utc_now()
    queue_item["status"] = "signals_recorded"
    queue_item["confidence"] = 0.78 if artifact["signals"] else 0.5
    queue_item["escalation_reason"] = None
    queue_item["payload_json"] = {
        **(queue_item.get("payload_json") or {}),
        "signal_count": len(artifact["signals"]),
        "recommendation_count": len(artifact["recommendations"]),
        "elapsed_seconds": artifact["elapsed_seconds"],
    }
    queue_item["completed_at"] = completed_at
    write_json(queue_item_path, queue_item)

    run_id = stamp_id("arun", queue_item["id"])
    run_record = {
        "id": run_id,
        "queue_item_id": queue_item["id"],
        "model": "ollama/gemma4:e4b",
        "prompt_version": PROMPT_VERSION,
        "tool_trace_json": artifact["evidence_trace"],
        "result_status": queue_item["status"],
        "started_at": started_at,
        "completed_at": completed_at,
    }

    artifact_id = stamp_id("aout", queue_item["id"])
    output_artifact_record = {
        "id": artifact_id,
        "queue_item_id": queue_item["id"],
        "artifact_type": "whitney_market_intelligence_report",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    record_id = stamp_id("wmir", queue_item["id"])
    durable_record = {
        "id": record_id,
        "queue_item_id": queue_item["id"],
        "report_type": "demand_sensing",
        "status": "signals_recorded",
        "summary": artifact["summary"],
        "signals": artifact["signals"],
        "recommendations": artifact["recommendations"],
        "evidence": artifact["evidence"],
        "created_at": started_at,
        "completed_at": completed_at,
    }

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    durable_record_path = runtime_root / "market_intelligence_reports" / f"{record_id}.json"
    write_json(durable_record_path, durable_record)

    db_persistence = None
    if persist_db:
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=durable_record,
            durable_kind="whitney_market_intelligence_report",
        )

    return {
        "queue_item_id": queue_item["id"],
        "queue_item_status": queue_item["status"],
        "market_intelligence_report_path": str(durable_record_path),
        "db_persistence": db_persistence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Whitney market intelligence and demand sensing.")
    parser.add_argument("--queue-item", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--persist-db", action="store_true")
    parser.add_argument("--db-env-file")
    args = parser.parse_args()
    result = process_queue_item(
        args.queue_item,
        args.runtime_root,
        persist_db=args.persist_db,
        db_env_file=args.db_env_file,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
