#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "0.1.0"
PROMPT_VERSION = "tracy-local-contract-0.1.0"
DEFAULT_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
BACKEND_ROOT = Path(os.environ.get("SYMGOV_BACKEND_ROOT", str(DEFAULT_BACKEND_ROOT))).resolve()

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.runtime import RuntimePersistenceBridge, env_flag
from symgov_backend.notifications import send_agent_status_update


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_id(prefix, base_id):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{base_id}-{timestamp}"


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
        "message": "Queue item removed from Tracy runtime queue.",
    }


def queue_status_for_outcome(outcome):
    if outcome == "failed":
        return "failed"
    # Both 'pass' and 'review_required' (canonical escalation) result in completed
    # queue items because the agent's work is done; routing logic then decides
    # the next step (Libby or Daisy).
    return "completed"


def queue_status_for_decision(decision):
    if decision == "escalate":
        return "escalated"
    return "completed"


def build_libby_queue_item(
    *,
    queue_item: dict,
    task: dict,
    artifact: dict,
    provenance_assessment_id: str,
    created_review_case: dict | None,
    timestamp: str,
):
    queue_id = f"aqi-libby-{provenance_assessment_id}-{timestamp}"
    payload = queue_item.get("payload_json") or {}
    attachment_ids = ensure_list(payload.get("attachment_ids"))
    attachment_id = payload.get("attachment_id") or (attachment_ids[0] if attachment_ids else None)
    origin_file_name = payload.get("origin_file_name")
    candidate_symbol_id = payload.get("candidate_symbol_id")
    if not origin_file_name and candidate_symbol_id:
        origin_file_name = f"{candidate_symbol_id}.source"

    return {
        "id": queue_id,
        "agent_id": "libby",
        "source_type": "provenance_assessment",
        "source_id": provenance_assessment_id,
        "status": "queued",
        "priority": queue_item.get("priority") or "medium",
        "payload_json": {
            "intake_record_id": task.get("intake_record_id"),
            "provenance_assessment_id": provenance_assessment_id,
            "review_case_id": created_review_case.get("id") if created_review_case else None,
            "current_stage": created_review_case.get("current_stage") if created_review_case else None,
            "escalation_level": created_review_case.get("escalation_level") if created_review_case else None,
            "origin_attachment_id": attachment_id,
            "origin_object_key": payload.get("raw_object_key"),
            "origin_file_name": origin_file_name or candidate_symbol_id or "submitted-symbol",
            "origin_batch_id": payload.get("submission_batch_id"),
            "symbol_key": candidate_symbol_id or provenance_assessment_id,
            "symbol_region_index": None,
            "candidate_symbol_id": candidate_symbol_id,
            "candidate_symbol_name": payload.get("candidate_title") or candidate_symbol_id,
            "asset_format": payload.get("declared_format"),
            "rights_status": artifact.get("rights_status"),
            "rights_disposition": artifact.get("rights_disposition"),
            "processing_outcome": artifact.get("processing_outcome"),
            "source_refs": ensure_list(artifact.get("evidence", {}).get("standards_source_refs")),
            "contributor_declaration": payload.get("contributor_declaration"),
            "source_notes": payload.get("source_notes"),
            "file_note": payload.get("file_note"),
            "submission_batch_summary": payload.get("submission_batch_summary"),
            "ocr_labels": [],
            "current_classification_id": None,
            "allow_web_research": True,
        },
        "confidence": None,
        "escalation_reason": None,
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def build_daisy_rights_coordination_queue_item(
    *,
    queue_item: dict,
    task: dict,
    artifact: dict,
    provenance_assessment_id: str,
    created_review_case: dict,
    timestamp: str,
):
    queue_id = f"aqi-daisy-rights-coord-{provenance_assessment_id}-{timestamp}"
    payload = queue_item.get("payload_json") or {}
    return {
        "id": queue_id,
        "agent_id": "daisy",
        "source_type": "provenance_rights_coordination",
        "source_id": created_review_case["id"],
        "status": "queued",
        "priority": queue_item.get("priority") or "high",
        "payload_json": {
            "review_case_id": created_review_case["id"],
            "current_stage": created_review_case.get("current_stage"),
            "source_entity_type": created_review_case.get("source_entity_type"),
            "source_entity_id": created_review_case.get("source_entity_id"),
            "escalation_level": created_review_case.get("escalation_level"),
            "validation_status": None,
            "rights_status": artifact.get("rights_status"),
            "rights_disposition": artifact.get("rights_disposition"),
            "processing_outcome": artifact.get("processing_outcome"),
            "risk_level": artifact.get("risk_level"),
            "review_queue_family": "review_coordination",
            "review_queue_label": "Daisy Rights Review Coordination",
            "coordination_step": "daisy_rights_review_coordination",
            "target_review_queue_family": "rights_review",
            "target_review_queue_label": "Provenance/Rights Review",
            "reviewer_pool": ["rights_reviewer", "qa_admin"],
            "candidate_symbol_id": payload.get("candidate_symbol_id"),
            "candidate_title": payload.get("candidate_title"),
            "source_ref": task.get("source_ref"),
            "source_notes": payload.get("source_notes"),
            "tracy_provenance_assessment_id": provenance_assessment_id,
            "tracy_rights_summary": artifact.get("reviewer_summary"),
            "tracy_recommended_actions": ensure_list(artifact.get("recommended_actions")),
            "tracy_defects": ensure_list(artifact.get("defects")),
        },
        "confidence": None,
        "escalation_reason": "rights_review_required",
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
    }


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def local_evidence_status(ref):
    if not isinstance(ref, str):
        return "invalid", None
    if ref.startswith("/"):
        path = Path(ref)
        return ("present" if path.exists() else "missing"), ref
    return "external", ref


def summarize_keywords(text):
    lowered = text.lower()
    positive = any(token in lowered for token in ["original", "authored", "owned", "internal", "company-authored"])
    negative = any(
        token in lowered
        for token in ["licensed", "restricted", "third-party", "third party", "no redistribution", "may not be redistributed"]
    )
    return positive, negative


def run_provenance_task(task):
    queue_item_id = task.get("queue_item_id") or "untracked"
    intake_record_id = task.get("intake_record_id")
    source_ref = task.get("source_ref")
    submitted_by = task.get("submitted_by")
    contributor_declaration = (task.get("contributor_declaration") or "").strip()
    rights_documents = ensure_list(task.get("rights_documents"))
    standards_source_refs = ensure_list(task.get("standards_source_refs"))
    evidence_links = ensure_list(task.get("evidence_links"))

    defects = []
    evidence_trace = []
    evidence = {
        "declaration_excerpt": contributor_declaration[:240] if contributor_declaration else None,
        "rights_documents": [],
        "standards_source_refs": standards_source_refs,
        "evidence_links": [],
    }

    decision = "pass"
    confidence = 0.9
    escalation_target = "none"
    rights_status = "unknown"
    risk_level = "medium"
    
    # Canonical state separation
    rights_disposition = "unknown_warning"
    processing_outcome = "pass"

    recommended_actions = []
    review_recommendation = None

    required_fields = {
        "intake_record_id": intake_record_id,
        "source_ref": source_ref,
        "submitted_by": submitted_by,
        "contributor_declaration": contributor_declaration,
    }
    missing_fields = [name for name, value in required_fields.items() if not value]
    if missing_fields:
        add_defect(defects, "TRACY-TASK-001", "high", f"Missing required fields: {', '.join(missing_fields)}.")
        add_trace(evidence_trace, "task_fields", "failed", "Task payload is missing required provenance fields.")
        decision = "escalate"
        confidence = 0.25
        escalation_target = "human_reviewer"
        rights_status = "unknown"
        risk_level = "high"
        rights_disposition = "failed"
        processing_outcome = "failed"

    if contributor_declaration:
        positive, negative = summarize_keywords(contributor_declaration)
        if positive and negative:
            rights_status = "conflict"
            rights_disposition = "conflict"
            risk_level = "critical"
            decision = "escalate"
            processing_outcome = "failed"
            confidence = min(confidence, 0.72)
            escalation_target = "human_reviewer"
            add_defect(defects, "TRACY-RIGHTS-001", "critical", "Declaration contains both ownership and restriction signals.")
            add_trace(evidence_trace, "declaration_analysis", "failed", "Detected conflicting ownership and restriction language.")
        elif negative:
            rights_status = "restricted"
            rights_disposition = "restricted"
            risk_level = "high"
            decision = "fail"
            processing_outcome = "failed"
            confidence = min(confidence, 0.91)
            add_defect(defects, "TRACY-RIGHTS-002", "high", "Declaration indicates restricted or third-party licensing.")
            add_trace(evidence_trace, "declaration_analysis", "failed", "Detected restrictive or third-party rights language.")
            recommended_actions.append("Route to human reviewer for licensing resolution before publication.")
        elif positive:
            rights_status = "cleared"
            rights_disposition = "cleared"
            risk_level = "low"
            processing_outcome = "pass"
            add_trace(evidence_trace, "declaration_analysis", "passed", "Detected positive ownership language with no restriction keywords.")
        else:
            rights_status = "unknown"
            rights_disposition = "unknown_warning"
            risk_level = "medium"
            decision = "escalate"
            processing_outcome = "review_required"
            confidence = min(confidence, 0.55)
            escalation_target = "human_reviewer"
            add_defect(defects, "TRACY-DECL-001", "medium", "Declaration does not clearly state ownership or rights status.")
            add_trace(evidence_trace, "declaration_analysis", "failed", "Declaration language was ambiguous.")

    if not standards_source_refs:
        add_defect(defects, "TRACY-SOURCE-001", "medium", "No standards source references were provided.")
        add_trace(evidence_trace, "source_refs", "failed", "Standards source references are missing.")
        confidence = min(confidence, 0.7)
        if decision == "pass":
            decision = "escalate"
            escalation_target = "human_reviewer"
            rights_status = "unknown"
            rights_disposition = "unknown_warning"
            processing_outcome = "review_required"
            risk_level = "medium"
    else:
        add_trace(evidence_trace, "source_refs", "passed", f"Captured {len(standards_source_refs)} standards source reference(s).")

    all_evidence_refs = rights_documents + evidence_links
    missing_local_refs = 0
    for ref in all_evidence_refs:
        status, value = local_evidence_status(ref)
        evidence["evidence_links"].append({"ref": value, "status": status})
        if status == "missing":
            missing_local_refs += 1

    for ref in rights_documents:
        status, value = local_evidence_status(ref)
        evidence["rights_documents"].append({"ref": value, "status": status})

    if missing_local_refs:
        add_defect(defects, "TRACY-EVID-001", "high", f"{missing_local_refs} local evidence file(s) could not be resolved.")
        add_trace(evidence_trace, "evidence_links", "failed", "One or more local evidence files are missing.")
        decision = "escalate"
        confidence = min(confidence, 0.45)
        escalation_target = "human_reviewer"
        if rights_status == "cleared":
            rights_status = "unknown"
            rights_disposition = "unknown_warning"
            processing_outcome = "review_required"
        risk_level = "high"
    else:
        add_trace(evidence_trace, "evidence_links", "passed", f"Recorded {len(all_evidence_refs)} evidence reference(s).")

    if rights_status == "restricted":
        recommended_actions.append("Block publication routing until licensing terms are resolved.")
    elif rights_status == "conflict":
        recommended_actions.append("Escalate to human reviewer for ownership conflict resolution.")
    elif rights_status == "unknown":
        recommended_actions.append("Request clearer contributor declaration and supporting rights evidence.")
    else:
        recommended_actions.append("Allow downstream review to proceed while preserving provenance evidence.")

    if rights_disposition in {"restricted", "conflict"} or processing_outcome == "failed":
        review_recommendation = {
            "current_stage": "provenance_rights_review",
            "escalation_level": "high" if rights_disposition in {"restricted", "conflict"} else "medium",
            "detail": f"Tracy flagged rights disposition {rights_disposition} with {risk_level} risk for human review.",
            "coordination_step": "daisy_rights_review_coordination",
            "review_queue_family": "review_coordination",
            "review_queue_label": "Daisy Rights Review Coordination",
        }

    reviewer_summary = (
        f"Rights status is {rights_status} with {risk_level} risk based on declaration analysis and recorded evidence."
    )

    return {
        "queue_item_id": queue_item_id,
        "agent": "tracy",
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        "confidence": round(confidence, 2),
        "escalation_target": escalation_target,
        "rights_status": rights_status,
        "rights_disposition": rights_disposition,
        "processing_outcome": processing_outcome,
        "risk_level": risk_level,
        "reviewer_summary": reviewer_summary,
        "evidence": evidence,
        "recommended_actions": recommended_actions,
        "defects": defects,
        "evidence_trace": evidence_trace,
        "review_recommendation": review_recommendation,
    }


def queue_item_payload_to_task(queue_item):
    payload = copy.deepcopy(queue_item.get("payload_json") or {})
    payload["queue_item_id"] = queue_item.get("id")
    payload["source_type"] = queue_item.get("source_type")
    payload["source_id"] = queue_item.get("source_id")
    payload["priority"] = queue_item.get("priority")
    return payload


def process_queue_item(queue_item_path, runtime_root, persist_db=False, db_env_file=None):
    queue_item_path = Path(queue_item_path)
    runtime_root = Path(runtime_root)

    with queue_item_path.open("r", encoding="utf-8") as handle:
        queue_item = json.load(handle)

    if queue_item.get("agent_id") != "tracy":
        raise ValueError("Queue item agent_id must be 'tracy'.")

    started_at = utc_now()
    queue_item["status"] = "running"
    queue_item["started_at"] = started_at
    write_json(queue_item_path, queue_item)
    notification_status = {
        "started": send_agent_status_update("tracy", "started", queue_item),
        "completed": None,
    }

    task = queue_item_payload_to_task(queue_item)
    artifact = run_provenance_task(task)
    completed_at = utc_now()

    queue_item["status"] = queue_status_for_outcome(artifact["processing_outcome"])
    queue_item["confidence"] = artifact["confidence"]
    queue_item["escalation_reason"] = (
        "provenance_requires_escalation" if artifact["decision"] == "escalate" else None
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
        "artifact_type": "provenance_assessment",
        "schema_version": artifact["schema_version"],
        "payload_json": artifact,
        "created_at": completed_at,
    }

    report_id = stamp_id("pa", queue_item["id"])
    provenance_assessment = {
        "id": report_id,
        "queue_item_id": queue_item["id"],
        "intake_record_id": artifact["intake_record_id"],
        "rights_status": artifact["rights_status"],
        "rights_disposition": artifact["rights_disposition"],
        "processing_outcome": artifact["processing_outcome"],
        "risk_level": artifact["risk_level"],
        "confidence": artifact["confidence"],
        "summary": artifact["reviewer_summary"],
        "evidence_json": artifact["evidence"],
        "report_json": {
            "decision": artifact["decision"],
            "confidence": artifact["confidence"],
            "escalation_target": artifact["escalation_target"],
            "recommended_actions": artifact["recommended_actions"],
            "defects": artifact["defects"],
            "evidence_trace": artifact["evidence_trace"],
        },
        "assessed_at": completed_at,
    }

    write_json(runtime_root / "agent_runs" / f"{run_id}.json", run_record)
    write_json(runtime_root / "agent_output_artifacts" / f"{artifact_id}.json", output_artifact_record)
    write_json(runtime_root / "provenance_assessments" / f"{report_id}.json", provenance_assessment)

    db_persistence = None
    additional_db_records = {"review_case": None, "daisy_rights_coordination_queue_item": None}
    libby_queue_item_path = None
    daisy_rights_coordination_queue_item_path = None
    if persist_db or env_flag("SYMGOV_PERSIST_TO_DB"):
        bridge = RuntimePersistenceBridge(env_file=db_env_file)
        db_persistence = bridge.persist_agent_execution(
            queue_item=queue_item,
            run_record=run_record,
            output_artifact_record=output_artifact_record,
            durable_record=provenance_assessment,
            durable_kind="provenance_assessment",
        )
        review_recommendation = artifact.get("review_recommendation")
        if review_recommendation:
            additional_db_records["review_case"] = bridge.create_review_case(
                source_entity_type="provenance_assessment",
                source_entity_id=report_id,
                current_stage=review_recommendation["current_stage"],
                escalation_level=review_recommendation["escalation_level"],
                opened_at=completed_at,
            )
            daisy_rights_coordination_queue_item = build_daisy_rights_coordination_queue_item(
                queue_item=queue_item,
                task=task,
                artifact=artifact,
                provenance_assessment_id=report_id,
                created_review_case=additional_db_records["review_case"],
                timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            )
            daisy_runtime_root = Path("/data/.openclaw/workspaces/daisy/runtime")
            daisy_rights_coordination_queue_item_path = daisy_runtime_root / "agent_queue_items" / f"{daisy_rights_coordination_queue_item['id']}.json"
            write_json(daisy_rights_coordination_queue_item_path, daisy_rights_coordination_queue_item)
            additional_db_records["daisy_rights_coordination_queue_item"] = bridge.upsert_agent_queue_item(daisy_rights_coordination_queue_item)

    libby_runtime_root = Path("/data/.openclaw/workspaces/libby/runtime")
    libby_queue_item = build_libby_queue_item(
        queue_item=queue_item,
        task=task,
        artifact=artifact,
        provenance_assessment_id=report_id,
        created_review_case=additional_db_records["review_case"],
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    )
    libby_queue_item_path = libby_runtime_root / "agent_queue_items" / f"{libby_queue_item['id']}.json"
    write_json(libby_queue_item_path, libby_queue_item)

    notification_status["completed"] = send_agent_status_update(
        "tracy",
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
        "provenance_assessment_path": str(runtime_root / "provenance_assessments" / f"{report_id}.json"),
        "db_persistence": db_persistence,
        "additional_db_records": additional_db_records,
        "downstream_created": {
            "libby_queue_item_path": str(libby_queue_item_path) if libby_queue_item_path else None,
            "daisy_rights_coordination_queue_item_path": str(daisy_rights_coordination_queue_item_path) if daisy_rights_coordination_queue_item_path else None,
        },
        "notifications": notification_status,
        "artifact": artifact,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run local Tracy provenance processing in task or queue mode.")
    parser.add_argument("--input", help="Path to a JSON task file.")
    parser.add_argument("--output", help="Path to write the JSON provenance artifact.")
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
        help="Also mirror queue, run, artifact, and provenance records into the Symgov database.",
    )
    parser.add_argument(
        "--db-env-file",
        help="Path to the Symgov database env file used with --persist-db.",
    )
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

    artifact = run_provenance_task(task)
    write_json(output_path, artifact)


if __name__ == "__main__":
    main()
