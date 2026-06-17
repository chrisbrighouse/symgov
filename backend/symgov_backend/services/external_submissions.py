from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..filename_inference import infer_filename_metadata, inferred_candidate_title
from ..runtime import RuntimePersistenceBridge


SCOTT_WORKSPACE_ROOT = Path("/data/.openclaw/workspaces/scott")
SCOTT_RUNTIME_ROOT = SCOTT_WORKSPACE_ROOT / "runtime"
SCOTT_UPLOAD_ROOT = SCOTT_RUNTIME_ROOT / "external_uploads"


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
    if suffix == ".dxf":
        return "dxf"
    if suffix == ".zip":
        return "zip"
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
    return inferred_candidate_title(filename)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass
class ExternalSubmissionService:
    bridge: RuntimePersistenceBridge
    pin: str
    db_env_file: Path
    storage_env_file: Path | None = None
    scott_runtime_root: Path = SCOTT_RUNTIME_ROOT
    upload_root: Path = SCOTT_UPLOAD_ROOT

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
        uploaded_files: list[dict[str, Any]] = []

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
            uploaded_files.append(
                {
                    "index": index,
                    "file_name": file_name,
                    "file_note": file_note,
                    "content_type": content_type,
                    "format": guess_declared_format(file_name),
                    "object_key": object_key,
                    "stored_path": str(stored_path),
                    "attachment": attachment,
                    "storage_result": storage_result,
                }
            )

        grouped_files: dict[str, list[dict[str, Any]]] = {}
        group_order: list[str] = []
        for uploaded in uploaded_files:
            group_key = Path(uploaded["file_name"]).stem.strip().lower() or uploaded["file_name"].lower()
            if group_key not in grouped_files:
                grouped_files[group_key] = []
                group_order.append(group_key)
            grouped_files[group_key].append(uploaded)

        def asset_entry(uploaded: dict[str, Any], role: str, downloadable: bool = True) -> dict[str, Any]:
            return {
                "object_key": uploaded["object_key"],
                "filename": uploaded["file_name"],
                "content_type": uploaded["content_type"],
                "format": uploaded["format"],
                "role": role,
                "downloadable": downloadable,
                "attachment_id": uploaded["attachment"]["id"],
                "attachment_object_key": uploaded["attachment"]["object_key"],
            }

        for symbol_index, group_key in enumerate(group_order, start=1):
            group = grouped_files[group_key]
            primary = next((item for item in group if item["format"] == "dxf"), group[0])
            companion_preview = next(
                (item for item in group if item is not primary and item["format"] in {"png", "jpeg"}),
                None,
            )
            source_assets = [asset_entry(item, "source") for item in group]
            visual_assets: dict[str, Any] = {"source_assets": source_assets}
            if companion_preview is not None:
                visual_assets["preview"] = asset_entry(companion_preview, "companion_preview")

            group_attachment_ids = [item["attachment"]["id"] for item in group]
            source_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{batch_id}:{symbol_index}:{primary['file_name']}"))
            queue_item_id = f"aqi-scott-ext-{batch_token}-{symbol_index:02d}"
            queue_item_path = self.scott_runtime_root / "agent_queue_items" / f"{queue_item_id}.json"
            combined_note = primary["file_note"] or overall_description
            if len(group) > 1:
                grouped_names = ", ".join(item["file_name"] for item in group)
                combined_note = f"{combined_note}\nCompanion files in this symbol submission: {grouped_names}".strip()

            payload_json = {
                "submission_kind": "contributor_submission",
                "source_ref": source_ref,
                "submitted_by": f"{submitter_name} <{submitter_email}>",
                "submitter_name": submitter_name,
                "submitter_email": submitter_email,
                "raw_input_path": primary["stored_path"],
                "original_filename": primary["file_name"],
                "declared_format": primary["format"],
                "candidate_symbol_id": candidate_symbol_id(primary["file_name"]),
                "candidate_title": candidate_title(primary["file_name"]),
                "filename_inference": infer_filename_metadata(primary["file_name"]),
                "contributor_name": submitter_name,
                "contributor_org": "",
                "contributor_declaration": overall_description,
                "source_notes": combined_note,
                "submission_batch_id": batch_id,
                "submission_batch_summary": overall_description,
                "package_symbol_sequence": symbol_index,
                "file_note": primary["file_note"],
                "external_submitter_id": submitter["id"],
                "attachment_id": primary["attachment"]["id"],
                "attachment_ids": group_attachment_ids,
                "raw_object_key": primary["object_key"],
                "visual_assets": visual_assets,
                "companion_files": [
                    {
                        "file_name": item["file_name"],
                        "object_key": item["object_key"],
                        "content_type": item["content_type"],
                        "format": item["format"],
                        "attachment_id": item["attachment"]["id"],
                    }
                    for item in group
                    if item is not primary
                ],
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
            db_persistence = self.bridge.upsert_agent_queue_item(queue_item)

            queue_items.append(
                {
                    "id": queue_item_id,
                    "fileName": primary["file_name"],
                    "fileNote": primary["file_note"],
                    "batchSummary": overall_description,
                    "status": "queued",
                    "routes": [],
                    "payload": payload_json,
                    "attachmentId": primary["attachment"]["id"],
                    "attachmentObjectKey": primary["attachment"]["object_key"],
                    "attachmentStorage": primary["storage_result"],
                    "scottQueueItemPath": str(queue_item_path),
                    "intakeRecordId": None,
                    "intakeStatus": "pending",
                    "eligibilityStatus": "pending",
                    "dbPersistence": db_persistence,
                    "downstreamCreated": {},
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
