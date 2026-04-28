from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime import RuntimePersistenceBridge


SCOTT_WORKSPACE_ROOT = Path("/data/.openclaw/workspaces/scott")
SCOTT_RUNTIME_ROOT = SCOTT_WORKSPACE_ROOT / "runtime"
SCOTT_UPLOAD_ROOT = SCOTT_RUNTIME_ROOT / "external_uploads"
VLAD_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/vlad/runtime")
TRACY_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/tracy/runtime")
LIBBY_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/libby/runtime")
DAISY_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/daisy/runtime")
SCOTT_RUNNER_PATH = SCOTT_WORKSPACE_ROOT / "run_scott_intake.py"
SCOTT_DOWNSTREAM_PATH = SCOTT_WORKSPACE_ROOT / "enqueue_scott_downstream.py"
VLAD_RUNNER_PATH = Path("/data/.openclaw/workspaces/vlad/run_vlad_validation.py")
TRACY_RUNNER_PATH = Path("/data/.openclaw/workspaces/tracy/run_tracy_provenance.py")
LIBBY_RUNNER_PATH = Path("/data/.openclaw/workspaces/libby/run_libby_classification.py")
DAISY_RUNNER_PATH = Path("/data/.openclaw/workspaces/daisy/run_daisy_coordination.py")


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCOTT_RUNNER = _load_module("symgov_scott_runner", SCOTT_RUNNER_PATH)
SCOTT_DOWNSTREAM = _load_module("symgov_scott_downstream", SCOTT_DOWNSTREAM_PATH)
VLAD_RUNNER = _load_module("symgov_vlad_runner", VLAD_RUNNER_PATH)
TRACY_RUNNER = _load_module("symgov_tracy_runner", TRACY_RUNNER_PATH)
LIBBY_RUNNER = _load_module("symgov_libby_runner", LIBBY_RUNNER_PATH)
DAISY_RUNNER = _load_module("symgov_daisy_runner", DAISY_RUNNER_PATH)


class SubmissionError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def timestamp_token(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.strftime("%Y%m%dT%H%M%SZ")


def guess_declared_format(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".svg":
        return "svg"
    if suffix == ".png":
        return "png"
    if suffix in {".jpg", ".jpeg"}:
        return "jpeg"
    if suffix == ".json":
        return "json"
    return "unknown"


def safe_filename(filename: str) -> str:
    candidate = Path(filename).name.strip()
    candidate = candidate.replace("/", "-").replace("\\", "-")
    return candidate or "upload.bin"


def candidate_symbol_id(filename: str) -> str:
    stem = Path(filename).stem
    normalized = "".join(char if char.isalnum() else "-" for char in stem).strip("-")
    return (normalized or "UNSPECIFIED").upper()


def candidate_title(filename: str) -> str:
    stem = Path(filename).stem
    normalized = " ".join(part for part in stem.replace("_", " ").replace("-", " ").split() if part)
    return normalized.title() if normalized else ""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_processing_result(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    sanitized: dict[str, Any] = {}
    for key in (
        "queue_item_path",
        "queue_item_status",
        "run_record_path",
        "artifact_record_path",
        "intake_record_path",
        "validation_report_path",
        "provenance_assessment_path",
        "classification_record_path",
        "review_coordination_report_path",
    ):
        entry = value.get(key)
        if entry is not None:
            sanitized[key] = str(entry)

    artifact = value.get("artifact") or {}
    if isinstance(artifact, dict):
        artifact_summary: dict[str, Any] = {}
        for key in (
            "decision",
            "confidence",
            "eligibility_status",
            "rights_status",
            "risk_level",
            "classification_status",
            "source_classification",
            "libby_approved",
            "coordination_status",
            "coordination_summary",
            "escalation_target",
        ):
            if artifact.get(key) is not None:
                artifact_summary[key] = artifact.get(key)
        if artifact_summary:
            sanitized["artifact"] = artifact_summary

    additional_db_records = value.get("additional_db_records") or {}
    if isinstance(additional_db_records, dict):
        db_summary: dict[str, Any] = {}
        review_case = additional_db_records.get("review_case")
        if isinstance(review_case, dict):
            db_summary["review_case"] = {
                "id": review_case.get("id"),
                "current_stage": review_case.get("current_stage"),
                "escalation_level": review_case.get("escalation_level"),
            }
        if db_summary:
            sanitized["additional_db_records"] = db_summary

    notifications = value.get("notifications") or {}
    if isinstance(notifications, dict):
        notification_summary: dict[str, Any] = {}
        for phase, entry in notifications.items():
            if not isinstance(entry, dict):
                continue
            target = entry.get("target") or {}
            notification_summary[phase] = {
                "ok": entry.get("ok"),
                "skipped": entry.get("skipped"),
                "reason": entry.get("reason"),
                "phase": entry.get("phase"),
                "returncode": entry.get("returncode"),
                "targetLabel": target.get("label"),
                "targetChannel": target.get("channel"),
                "targetName": target.get("name"),
            }
        if notification_summary:
            sanitized["notifications"] = notification_summary

    return sanitized


def _sanitize_downstream_created(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    sanitized: dict[str, Any] = {}
    for key, entry in value.items():
        if key == "daisy" and isinstance(entry, list):
            sanitized[key] = [
                {
                    "source_agent": item.get("source_agent"),
                    "daisy_queue_item_path": str(item.get("daisy_queue_item_path")) if item.get("daisy_queue_item_path") else None,
                    "daisy_processing": _sanitize_processing_result(item.get("daisy_processing") or {}),
                }
                for item in entry
                if isinstance(item, dict)
            ]
            continue

        if key.endswith("_processing"):
            sanitized[key] = _sanitize_processing_result(entry)
            continue

        if key.endswith("_queue_item_path") and entry is not None:
            sanitized[key] = str(entry)
            continue

        if key == "libby" and isinstance(entry, dict):
            sanitized[key] = {
                "libby_queue_item_path": str(entry.get("libby_queue_item_path")) if entry.get("libby_queue_item_path") else None,
                "libby_processing": _sanitize_processing_result(entry.get("libby_processing") or {}),
            }
            continue

        sanitized[key] = entry

    return sanitized


@dataclass
class ExternalSubmissionService:
    bridge: RuntimePersistenceBridge
    pin: str
    db_env_file: Path
    storage_env_file: Path | None = None
    scott_runtime_root: Path = SCOTT_RUNTIME_ROOT
    upload_root: Path = SCOTT_UPLOAD_ROOT
    vlad_runtime_root: Path = VLAD_RUNTIME_ROOT
    tracy_runtime_root: Path = TRACY_RUNTIME_ROOT
    libby_runtime_root: Path = LIBBY_RUNTIME_ROOT
    daisy_runtime_root: Path = DAISY_RUNTIME_ROOT

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        pin = str(payload.get("pin") or "").strip()
        if len(pin) != 4 or pin != self.pin:
            raise SubmissionError("Invalid submission PIN.")

        submitter_name = str(payload.get("submitter_name") or "").strip()
        submitter_email = str(payload.get("submitter_email") or "").strip().lower()
        overall_description = str(payload.get("overall_description") or "").strip()
        files = payload.get("files")

        if not submitter_name:
            raise SubmissionError("submitter_name is required.")
        if not submitter_email or "@" not in submitter_email:
            raise SubmissionError("A valid submitter_email is required.")
        if not overall_description:
            raise SubmissionError("overall_description is required.")
        if not isinstance(files, list) or not files:
            raise SubmissionError("At least one file is required.")

        self.bridge.seed_agent_definitions()
        submitter = self.bridge.upsert_external_identity(
            display_name=submitter_name,
            email=submitter_email,
            identity_type="submitter",
            status="active",
        )

        started_at = utc_now()
        batch_token = timestamp_token(started_at)
        batch_id = f"subext-{batch_token}"
        source_ref = f"external-submission-{batch_token}"
        batch_root = self.upload_root / batch_id
        batch_root.mkdir(parents=True, exist_ok=True)

        queue_items: list[dict[str, Any]] = []
        attachment_ids: list[str] = []

        for index, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                raise SubmissionError("Each file entry must be an object.")

            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                raise SubmissionError(f"files[{index - 1}].name is required.")
            file_name = safe_filename(raw_name)
            file_note = str(item.get("note") or "").strip()
            content_type = str(item.get("content_type") or "application/octet-stream").strip() or "application/octet-stream"
            encoded_content = str(item.get("content_base64") or "").strip()
            if not encoded_content:
                raise SubmissionError(f"files[{index - 1}].content_base64 is required.")

            try:
                file_bytes = base64.b64decode(encoded_content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SubmissionError(f"files[{index - 1}] is not valid base64.") from exc
            if not file_bytes:
                raise SubmissionError(f"files[{index - 1}] is empty.")

            digest = hashlib.sha256(file_bytes).hexdigest()
            object_key = f"external-submissions/{batch_id}/{index:02d}-{file_name}"
            stored_path = batch_root / f"{index:02d}-{file_name}"
            stored_path.write_bytes(file_bytes)

            attachment = self.bridge.create_attachment(
                parent_type="external_submission_batch",
                parent_id=batch_id,
                filename=file_name,
                object_key=object_key,
                content_type=content_type,
                size_bytes=len(file_bytes),
                sha256=digest,
            )
            storage_result = self.bridge.upload_object_bytes(
                object_key=object_key,
                payload=file_bytes,
                content_type=content_type,
                env_file=self.storage_env_file,
            )
            attachment_ids.append(attachment["id"])

            source_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{batch_id}:{index}:{file_name}"))
            queue_item_id = f"aqi-scott-ext-{batch_token}-{index:02d}"
            queue_item_path = self.scott_runtime_root / "agent_queue_items" / f"{queue_item_id}.json"

            payload_json = {
                "submission_kind": "contributor_submission",
                "source_ref": source_ref,
                "submitted_by": f"{submitter_name} <{submitter_email}>",
                "submitter_name": submitter_name,
                "submitter_email": submitter_email,
                "raw_input_path": str(stored_path),
                "original_filename": file_name,
                "declared_format": guess_declared_format(file_name),
                "candidate_symbol_id": candidate_symbol_id(file_name),
                "candidate_title": candidate_title(file_name),
                "contributor_name": submitter_name,
                "contributor_org": "",
                "contributor_declaration": overall_description,
                "source_notes": file_note or overall_description,
                "submission_batch_id": batch_id,
                "submission_batch_summary": overall_description,
                "file_note": file_note,
                "external_submitter_id": submitter["id"],
                "attachment_id": attachment["id"],
                "attachment_ids": [attachment["id"]],
                "raw_object_key": object_key,
                "rights_documents": [],
                "evidence_links": [],
                "standards_source_refs": [],
            }
            queue_item = {
                "id": queue_item_id,
                "agent_id": "scott",
                "source_type": "raw_submission",
                "source_id": source_uuid,
                "status": "queued",
                "priority": "medium",
                "payload_json": payload_json,
                "confidence": None,
                "escalation_reason": None,
                "created_at": iso_now(),
                "started_at": None,
                "completed_at": None,
            }
            write_json(queue_item_path, queue_item)

            process_result = SCOTT_RUNNER.process_queue_item(
                queue_item_path=queue_item_path,
                runtime_root=self.scott_runtime_root,
                persist_db=True,
                db_env_file=str(self.db_env_file),
            )
            intake_record_path = Path(process_result["intake_record_path"])
            intake_record = load_json(intake_record_path)
            downstream = self._create_downstream_queue_items(intake_record_path, intake_record)

            queue_items.append(
                {
                    "id": queue_item_id,
                    "fileName": file_name,
                    "fileNote": file_note,
                    "batchSummary": overall_description,
                    "routes": (intake_record.get("routing_recommendation_json") or {}).get("route_to_agents") or [],
                    "payload": payload_json,
                    "attachmentId": attachment["id"],
                    "attachmentObjectKey": attachment["object_key"],
                    "attachmentStorage": storage_result,
                    "intakeRecordId": intake_record["id"],
                    "intakeStatus": intake_record["intake_status"],
                    "eligibilityStatus": intake_record["eligibility_status"],
                    "dbPersistence": process_result.get("db_persistence"),
                    "downstreamCreated": _sanitize_downstream_created(downstream),
                }
            )

        self.bridge.create_audit_event(
            entity_type="external_submission_batch",
            entity_id=batch_id,
            action="external_submission_received",
            payload_json={
                "batch_id": batch_id,
                "source_ref": source_ref,
                "submitter_name": submitter_name,
                "submitter_email": submitter_email,
                "file_count": len(queue_items),
                "attachment_ids": attachment_ids,
            },
        )

        return {
            "batchId": batch_id,
            "createdAt": started_at.isoformat().replace("+00:00", "Z"),
            "submitterName": submitter_name,
            "submitterEmail": submitter_email,
            "sharedSummary": overall_description,
            "queueItems": queue_items,
        }

    def _create_downstream_queue_items(self, intake_record_path: Path, intake_record: dict[str, Any]) -> dict[str, Any]:
        if intake_record.get("intake_status") != "accepted":
            return {}
        if intake_record.get("eligibility_status") != "eligible":
            return {}

        route_to_agents = (intake_record.get("routing_recommendation_json") or {}).get("route_to_agents") or []
        if not route_to_agents:
            return {}

        created: dict[str, Any] = {}
        timestamp = SCOTT_DOWNSTREAM.utc_stamp()

        if "vlad" in route_to_agents:
            vlad_item = SCOTT_DOWNSTREAM.build_vlad_queue_item(intake_record, timestamp)
            vlad_path = self.vlad_runtime_root / "agent_queue_items" / f"{vlad_item['id']}.json"
            SCOTT_DOWNSTREAM.write_json(vlad_path, vlad_item)
            created["vlad_queue_item_path"] = str(vlad_path)
            created["vlad_processing"] = VLAD_RUNNER.process_queue_item(
                queue_item_path=vlad_path,
                runtime_root=self.vlad_runtime_root,
                persist_db=True,
                db_env_file=str(self.db_env_file),
                storage_env_file=str(self.storage_env_file) if self.storage_env_file else None,
            )
            daisy_created = self._create_daisy_from_processing(
                source_agent="vlad",
                processing_result=created["vlad_processing"],
                timestamp=timestamp,
            )
            if daisy_created:
                created.setdefault("daisy", []).extend(daisy_created)

        if "tracy" in route_to_agents:
            tracy_item = SCOTT_DOWNSTREAM.build_tracy_queue_item(intake_record, timestamp)
            tracy_path = self.tracy_runtime_root / "agent_queue_items" / f"{tracy_item['id']}.json"
            SCOTT_DOWNSTREAM.write_json(tracy_path, tracy_item)
            created["tracy_queue_item_path"] = str(tracy_path)
            created["tracy_processing"] = TRACY_RUNNER.process_queue_item(
                queue_item_path=tracy_path,
                runtime_root=self.tracy_runtime_root,
                persist_db=True,
                db_env_file=str(self.db_env_file),
            )
            libby_created = self._create_libby_from_processing(
                processing_result=created["tracy_processing"],
            )
            if libby_created:
                created["libby"] = libby_created
                daisy_created = self._create_daisy_from_processing(
                    source_agent="libby",
                    processing_result=libby_created["libby_processing"],
                    timestamp=timestamp,
                )
                if daisy_created:
                    created.setdefault("daisy", []).extend(daisy_created)

        return created

    def _create_libby_from_processing(
        self,
        *,
        processing_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        downstream_created = processing_result.get("downstream_created") or {}
        libby_queue_item_path = downstream_created.get("libby_queue_item_path")
        if not libby_queue_item_path:
            return None

        libby_path = Path(libby_queue_item_path)
        libby_processing = LIBBY_RUNNER.process_queue_item(
            queue_item_path=libby_path,
            runtime_root=self.libby_runtime_root,
            persist_db=True,
            db_env_file=str(self.db_env_file),
        )
        return {
            "libby_queue_item_path": str(libby_path),
            "libby_processing": libby_processing,
        }

    def _create_daisy_from_processing(
        self,
        *,
        source_agent: str,
        processing_result: dict[str, Any],
        timestamp: str,
    ) -> list[dict[str, Any]]:
        additional_db_records = processing_result.get("additional_db_records") or {}
        review_case = additional_db_records.get("review_case")
        artifact = processing_result.get("artifact") or {}
        if not review_case:
            return []

        daisy_item = DAISY_RUNNER.build_daisy_queue_item(
            review_case=review_case,
            timestamp=timestamp,
            validation_status=artifact.get("decision") if source_agent == "vlad" else None,
            rights_status=artifact.get("rights_status") if source_agent in {"tracy", "libby"} else None,
        )
        daisy_path = self.daisy_runtime_root / "agent_queue_items" / f"{daisy_item['id']}.json"
        write_json(daisy_path, daisy_item)
        daisy_processing = DAISY_RUNNER.process_queue_item(
            queue_item_path=daisy_path,
            runtime_root=self.daisy_runtime_root,
        )
        return [
            {
                "source_agent": source_agent,
                "daisy_queue_item_path": str(daisy_path),
                "daisy_processing": daisy_processing,
            }
        ]
