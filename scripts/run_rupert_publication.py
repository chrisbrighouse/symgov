#!/usr/bin/env python3
import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "0.1.0"
PROMPT_VERSION = "rupert-local-contract-0.1.0"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.notifications import send_agent_status_update
from symgov_backend.runtime import RuntimePersistenceBridge, env_flag


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_date():
    return datetime.now(timezone.utc).date().isoformat()


def stamp_id(prefix, base_id):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{base_id}-{timestamp}"


def slugify(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "release"


def add_trace(trace, check, status, detail):
    trace.append({"check": check, "status": status, "detail": detail})


def add_defect(defects, code, severity, detail):
    defects.append({"code": code, "severity": severity, "detail": detail})


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def cleanup_queue_item(queue_item_path, runtime_root):
    queue_path = Path(queue_item_path).resolve()
    queue_dir = (Path(runtime_root).resolve() / "agent_queue_items").resolve()

    if queue_dir not in queue_path.parents:
        raise SystemExit(f"Refusing to remove queue item outside {queue_dir}.")
    if queue_path.suffix != ".json":
        raise SystemExit("Refusing to remove a non-JSON queue item.")
    if not queue_path.exists():
        return {
            "queue_item_path": str(queue_path),
            "removed": False,
            "message": "Queue item was already absent.",
        }

    queue_path.unlink()
    return {
        "queue_item_path": str(queue_path),
        "removed": True,
        "message": "Queue item removed from Rupert runtime queue.",
    }


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def queue_status_for_decision(decision):
    if decision == "escalate":
        return "escalated"
    return "completed"


def queue_item_payload_to_task(queue_item):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def build_rupert_queue_item(
    *,
    source_id: str,
    timestamp: str,
    review_case_id: str | None = None,
    review_decision_id: str | None = None,
    human_decision: str = "approve",
    human_approved: bool = True,
    symbol_revision_ids: list[str] | None = None,
    release_target: str | None = None,
    publication_pack_code: str | None = None,
    publication_pack_title: str | None = None,
    effective_date: str | None = None,
    standards_visibility: str = "public",
    release_area: str | None = None,
):
    queue_id = f"aqi-rupert-{source_id}-{timestamp}"
    return {
        "id": queue_id,
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": source_id,
        "status": "queued",
        "priority": "medium",
        "payload_json": {
            "review_case_id": review_case_id,
            "review_decision_id": review_decision_id,
            "human_decision": human_decision,
            "human_approved": human_approved,
            "symbol_revision_ids": symbol_revision_ids or [],
            "release_target": release_target,
            "publication_pack_code": publication_pack_code,
            "publication_pack_title": publication_pack_title,
            "effective_date": effective_date or today_date(),
            "standards_visibility": standards_visibility,
            "release_area": release_area,
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def default_pack(task, release_target, effective_date):
    pack_code = task.get("publication_pack_code") or f"pack-{slugify(release_target)}"
    return {
        "pack_code": pack_code,
        "title": task.get("publication_pack_title") or f"Publication pack {release_target}",
        "audience": task.get("standards_visibility") or "public",
        "effective_date": effective_date,
        "status": "staged",
    }


def page_code_for_revision(revision_id, release_target):
    return f"{slugify(release_target)}-{slugify(revision_id)}"


def build_page_proposals(symbol_revision_ids, release_target, effective_date):
    proposals = []
    for index, revision_id in enumerate(symbol_revision_ids, start=1):
        proposals.append(
            {
                "symbol_revision_id": revision_id,
                "page_code": page_code_for_revision(revision_id, release_target),
                "title": f"Published symbol {revision_id}",
                "effective_date": effective_date,
                "sort_order": index,
            }
        )
    return proposals


def build_pack_entries(page_proposals, pack_code):
    return [
        {
            "pack_code": pack_code,
            "symbol_revision_id": page["symbol_revision_id"],
            "published_page_code": page["page_code"],
            "sort_order": page["sort_order"],
        }
        for page in page_proposals
    ]


def run_publication_task(task, release_area_root=None):
    queue_item_id = task.get("queue_item_id") or "untracked"
    human_decision = task.get("human_decision")
    human_approved = bool(task.get("human_approved"))
    symbol_revision_ids = [str(item) for item in ensure_list(task.get("symbol_revision_ids")) if str(item).strip()]
    release_target = task.get("release_target") or "standards-current"
    effective_date = task.get("effective_date") or today_date()
    standards_visibility = task.get("standards_visibility") or "public"
    release_area = task.get("release_area") or str(release_area_root or "runtime/release_area")

    defects = []
    evidence_trace = []
    decision = "stage"
    confidence = 0.86
    escalation_target = "none"

    if not human_approved or human_decision != "approve":
        add_defect(
            defects,
            "RUPERT-APPROVAL-001",
            "high",
            "Rupert requires an explicit human approval decision before publication staging.",
        )
        add_trace(evidence_trace, "human_approval", "failed", "Approval handoff was missing or was not an approve decision.")
        decision = "escalate"
        confidence = 0.24
        escalation_target = "release_manager"
    else:
        add_trace(evidence_trace, "human_approval", "passed", "Explicit human approval handoff was present.")

    if not symbol_revision_ids:
        add_defect(
            defects,
            "RUPERT-REVISION-001",
            "high",
            "No symbol revisions were provided for publication staging.",
        )
        add_trace(evidence_trace, "symbol_revisions", "failed", "Rupert could not identify any symbol revisions to stage.")
        decision = "escalate"
        confidence = min(confidence, 0.32)
        escalation_target = "release_manager"
    else:
        add_trace(
            evidence_trace,
            "symbol_revisions",
            "passed",
            f"Prepared {len(symbol_revision_ids)} symbol revision(s) for publication staging.",
        )

    if not release_target:
        add_defect(defects, "RUPERT-RELEASE-001", "medium", "No release target was provided.")
        add_trace(evidence_trace, "release_target", "failed", "Release target was missing.")
        decision = "escalate"
        confidence = min(confidence, 0.5)
        escalation_target = "release_manager"
    else:
        add_trace(evidence_trace, "release_target", "passed", f"Release target is {release_target}.")

    publication_pack = default_pack(task, release_target, effective_date)
    published_page_proposals = build_page_proposals(symbol_revision_ids, release_target, effective_date)
    pack_entry_proposals = build_pack_entries(published_page_proposals, publication_pack["pack_code"])

    standards_availability_summary = {
        "visibility": standards_visibility,
        "publication_state": "staged_for_standards" if decision != "escalate" else "blocked",
        "symbol_count": len(symbol_revision_ids),
        "pack_code": publication_pack["pack_code"],
        "release_target": release_target,
    }

    release_manifest = {
        "queue_item_id": queue_item_id,
        "release_target": release_target,
        "release_area": release_area,
        "publication_pack": publication_pack,
        "symbol_revision_ids": symbol_revision_ids,
        "published_page_proposals": published_page_proposals,
        "pack_entry_proposals": pack_entry_proposals,
        "standards_availability_summary": standards_availability_summary,
    }

    release_manifest_path = None
    if decision != "escalate":
        release_dir = Path(release_area) / slugify(release_target)
        release_manifest_path = release_dir / f"{queue_item_id}.json"
        write_json(release_manifest_path, release_manifest)
        add_trace(
            evidence_trace,
            "release_area_manifest",
            "passed",
            f"Wrote release-area manifest to {release_manifest_path}.",
        )

    publication_summary = (
        f"Rupert staged {len(symbol_revision_ids)} symbol revision(s) for {release_target}."
        if decision != "escalate"
        else "Rupert could not stage the publication handoff without release-manager review."
    )

    return {
        "queue_item_id": queue_item_id,
        "agent": "rupert",
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "confidence": round(confidence, 2),
        "escalation_target": escalation_target,
        "publication_summary": publication_summary,
        "release_target": release_target,
        "release_area": release_area,
        "release_manifest_path": str(release_manifest_path) if release_manifest_path else None,
        "publication_pack": publication_pack,
        "staged_symbol_revisions": symbol_revision_ids,
        "published_page_proposals": published_page_proposals,
        "pack_entry_proposals": pack_entry_proposals,
        "standards_availability_summary": standards_availability_summary,
        "defects": defects,
        "evidence_trace": evidence_trace,
    }


def process_queue_item(queue_item_path, runtime_root, persist_db=False, db_env_file=None):
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)

    with queue_item_path.open("r", encoding="utf-8") as handle:
        queue_item = json.load(handle)

    if queue_item.get("agent_id") != "rupert":
        raise ValueError("Queue item agent_id must be 'rupert'.")

    started_at = utc_now()
    queue_item["status"] = "running"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)
    notification_status = {
        "started": send_agent_status_update("rupert", "started", queue_item),
        "completed": None,
    }

    task = queue_item_payload_to_task(queue_item)
    artifact = run_publication_task(task, release_area_root=runtime_root / "release_area")
    completed_at = utc_now()

    queue_item["status"] = queue_status_for_decision(artifact["decision"])
    queue_item["confidence"] = artifact["confidence"]
    queue_item["escalation_reason"] = (
        "publication_handoff_requires_release_manager" if artifact["decision"] == "escalate" else None
    )
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
        "artifact_type": "publication_plan",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    report_id = stamp_id("rpr", queue_item["id"])
    publication_report = {
        "id": report_id,
        "queue_item_id": queue_item["id"],
        "source_type": queue_item.get("source_type"),
        "source_id": queue_item.get("source_id"),
        "publication_status": queue_item["status"],
        "publication_summary": artifact["publication_summary"],
        "release_target": artifact["release_target"],
        "release_area": artifact["release_area"],
        "release_manifest_path": artifact["release_manifest_path"],
        "publication_pack": artifact["publication_pack"],
        "staged_symbol_revisions": artifact["staged_symbol_revisions"],
        "published_page_proposals": artifact["published_page_proposals"],
        "pack_entry_proposals": artifact["pack_entry_proposals"],
        "standards_availability_summary": artifact["standards_availability_summary"],
        "report_json": {
            "decision": artifact["decision"],
            "confidence": artifact["confidence"],
            "escalation_target": artifact["escalation_target"],
            "defects": artifact["defects"],
            "evidence_trace": artifact["evidence_trace"],
        },
        "created_at": completed_at,
    }

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    write_json(runtime_root / "publication_reports" / f"{report_id}.json", publication_report)

    db_persistence = None
    if persist_db:
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_publication_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            publication_report=publication_report,
        )

    notification_status["completed"] = send_agent_status_update(
        "rupert",
        "completed",
        queue_item,
        artifact=artifact,
        queue_status=queue_item["status"],
    )

    return {
        "queue_item_path": str(queue_item_path),
        "queue_item_status": queue_item["status"],
        "run_record_path": str(runtime_root / "agent_runs" / f"{run_id}.json"),
        "artifact_record_path": str(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json"),
        "publication_report_path": str(runtime_root / "publication_reports" / f"{report_id}.json"),
        "release_manifest_path": artifact["release_manifest_path"],
        "db_persistence": db_persistence,
        "notifications": notification_status,
        "artifact": artifact,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run local Rupert publication staging in task or queue mode.")
    parser.add_argument("--input", help="Path to a JSON task file.")
    parser.add_argument("--output", help="Path to write the JSON publication artifact.")
    parser.add_argument("--queue-item", help="Path to an agent_queue_item JSON record.")
    parser.add_argument("--runtime-root", help="Root directory for local file-backed queue records.")
    parser.add_argument(
        "--cleanup-queue-item",
        action="store_true",
        help="Remove the specified queue item from this agent's runtime/agent_queue_items directory.",
    )
    parser.add_argument(
        "--persist-db",
        action="store_true",
        default=env_flag("SYMGOV_RUPERT_PERSIST_DB", False),
        help="Persist Rupert queue, run, artifact, publication job, pack, pages, and entries to the Symgov database.",
    )
    parser.add_argument("--db-env-file", help="Path to the Symgov database env file used with --persist-db.")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.cleanup_queue_item:
        if not args.queue_item or not args.runtime_root:
            raise SystemExit("--queue-item and --runtime-root are required with --cleanup-queue-item.")
        print(json.dumps(cleanup_queue_item(args.queue_item, args.runtime_root), indent=2))
        return

    if args.queue_item:
        if not args.runtime_root:
            raise SystemExit("--runtime-root is required with --queue-item.")
        result = process_queue_item(
            args.queue_item,
            args.runtime_root,
            persist_db=args.persist_db,
            db_env_file=args.db_env_file,
        )
        print(json.dumps(result, indent=2))
        return

    if not args.input or not args.output:
        raise SystemExit("--input and --output are required when not using --queue-item.")

    input_path = Path(args.input)
    output_path = Path(args.output)
    with input_path.open("r", encoding="utf-8") as handle:
        task = json.load(handle)

    artifact = run_publication_task(task)
    write_json(output_path, artifact)


if __name__ == "__main__":
    main()
