from __future__ import annotations

import json
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func

from .models import AgentDefinition, AgentQueueItem, ProvenanceAssessment, ReviewCase, AuditEvent
from .runtime import RuntimePersistenceBridge, coerce_uuid

TERMINAL_QUEUE_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "superseded",
    "progress_saved",
    "signals_recorded",
    "candidate",
    "published",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def archive_agent_runtime_queue(
    *,
    agent: str,
    runtime_root: str | Path,
    archive_root: str | Path | None = None,
    terminal_statuses: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Move terminal runtime queue JSON files out of active agent_queue_items.

    Runtime queue workers only process status='queued'. Keeping completed JSON in the
    active queue directory is harmless but noisy; this helper archives terminal files
    after status inspection without touching DB state.
    """

    runtime_root = Path(runtime_root)
    queue_dir = runtime_root / "agent_queue_items"
    archive_dir = Path(archive_root) if archive_root else runtime_root / "agent_queue_items_archive" / utc_now().strftime("%Y%m%dT%H%M%SZ")
    terminal_statuses = terminal_statuses or TERMINAL_QUEUE_STATUSES
    inspected = 0
    archived: list[dict[str, str]] = []
    skipped: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    if not queue_dir.exists():
        return {
            "agent": agent,
            "runtimeRoot": str(runtime_root),
            "queueDir": str(queue_dir),
            "archiveDir": str(archive_dir),
            "dryRun": dry_run,
            "inspectedCount": 0,
            "archivedCount": 0,
            "skipped": {"queue_dir_missing": 1},
            "archived": [],
            "errors": [],
        }

    for path in sorted(queue_dir.glob("*.json")):
        inspected += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive operational path
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if payload.get("agent_id") != agent:
            skipped["wrong_agent"] += 1
            continue
        status = str(payload.get("status") or "")
        if status not in terminal_statuses:
            skipped[f"status:{status or 'missing'}"] += 1
            continue
        destination = archive_dir / path.name
        archived.append({"path": str(path), "archivePath": str(destination), "status": status})
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))

    return {
        "agent": agent,
        "runtimeRoot": str(runtime_root),
        "queueDir": str(queue_dir),
        "archiveDir": str(archive_dir),
        "dryRun": dry_run,
        "inspectedCount": inspected,
        "archivedCount": len(archived),
        "skipped": dict(skipped),
        "archived": archived,
        "errors": errors,
    }


def find_provenance_libby_items_missing_review_cases(
    *,
    db_env_file: str | Path | None = None,
    origin_batch_id: str | None = None,
) -> list[dict[str, Any]]:
    bridge = RuntimePersistenceBridge(env_file=db_env_file)
    with bridge.session_scope() as session:
        libby = session.query(AgentDefinition).filter_by(slug="libby").one()
        query = (
            session.query(AgentQueueItem, ProvenanceAssessment)
            .join(ProvenanceAssessment, ProvenanceAssessment.id == AgentQueueItem.source_id)
            .outerjoin(
                ReviewCase,
                and_(
                    ReviewCase.source_entity_type == "provenance_assessment",
                    ReviewCase.source_entity_id == ProvenanceAssessment.id,
                ),
            )
            .filter(AgentQueueItem.agent_id == libby.id)
            .filter(AgentQueueItem.source_type == "provenance_assessment")
            .filter(ReviewCase.id.is_(None))
        )
        if origin_batch_id:
            query = query.filter(AgentQueueItem.payload_json["origin_batch_id"].astext == origin_batch_id)
        rows = query.order_by(AgentQueueItem.created_at.asc()).all()
        return [
            {
                "queueItemId": str(queue_item.id),
                "provenanceAssessmentId": str(assessment.id),
                "originBatchId": (queue_item.payload_json or {}).get("origin_batch_id"),
                "status": queue_item.status,
                "createdAt": queue_item.created_at.isoformat(),
            }
            for queue_item, assessment in rows
        ]


def backfill_provenance_libby_review_cases(
    *,
    db_env_file: str | Path | None = None,
    origin_batch_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Create missing libby_disposition_review cases for Libby provenance handoffs."""

    bridge = RuntimePersistenceBridge(env_file=db_env_file)
    now = utc_now()
    created: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    with bridge.session_scope() as session:
        libby = session.query(AgentDefinition).filter_by(slug="libby").one()
        query = (
            session.query(AgentQueueItem, ProvenanceAssessment)
            .join(ProvenanceAssessment, ProvenanceAssessment.id == AgentQueueItem.source_id)
            .outerjoin(
                ReviewCase,
                and_(
                    ReviewCase.source_entity_type == "provenance_assessment",
                    ReviewCase.source_entity_id == ProvenanceAssessment.id,
                ),
            )
            .filter(AgentQueueItem.agent_id == libby.id)
            .filter(AgentQueueItem.source_type == "provenance_assessment")
            .filter(ReviewCase.id.is_(None))
        )
        if origin_batch_id:
            query = query.filter(AgentQueueItem.payload_json["origin_batch_id"].astext == origin_batch_id)
        rows = query.order_by(AgentQueueItem.created_at.asc()).all()
        for queue_item, assessment in rows:
            candidates.append({"queueItemId": str(queue_item.id), "provenanceAssessmentId": str(assessment.id)})
            if dry_run:
                continue
            review_case = ReviewCase(
                id=uuid.uuid4(),
                source_entity_type="provenance_assessment",
                source_entity_id=assessment.id,
                current_stage="libby_disposition_review",
                owner_id=None,
                escalation_level=assessment.risk_level or "medium",
                opened_at=now,
                closed_at=None,
            )
            session.add(review_case)
            payload = dict(queue_item.payload_json or {})
            payload["review_case_id"] = str(review_case.id)
            payload["current_stage"] = "libby_disposition_review"
            payload["tracy_backfill"] = {
                "reason": "missing_libby_provenance_review_case",
                "created_at": now.isoformat().replace("+00:00", "Z"),
            }
            queue_item.payload_json = payload
            session.add(
                AuditEvent(
                    entity_type="review_case",
                    entity_id=review_case.id,
                    action="tracy_libby_review_case_backfilled",
                    actor_id=None,
                    payload_json={
                        "queue_item_id": str(queue_item.id),
                        "provenance_assessment_id": str(assessment.id),
                        "origin_batch_id": payload.get("origin_batch_id"),
                    },
                    created_at=now,
                )
            )
            created.append({"reviewCaseId": str(review_case.id), "queueItemId": str(queue_item.id)})
    return {
        "dryRun": dry_run,
        "originBatchId": origin_batch_id,
        "candidateCount": len(candidates),
        "createdCount": len(created),
        "candidates": candidates,
        "created": created,
    }


def tracy_status_summary(*, db_env_file: str | Path | None = None, runtime_root: str | Path | None = None) -> dict[str, Any]:
    bridge = RuntimePersistenceBridge(env_file=db_env_file)
    runtime_root = Path(runtime_root) if runtime_root else Path("/data/.openclaw/workspaces/tracy/runtime")
    queue_dir = runtime_root / "agent_queue_items"
    runtime_status_counts: Counter[str] = Counter()
    runtime_files = 0
    if queue_dir.exists():
        for path in queue_dir.glob("*.json"):
            runtime_files += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                runtime_status_counts[str(payload.get("status") or "missing")] += 1
            except Exception:
                runtime_status_counts["unreadable"] += 1

    with bridge.session_scope() as session:
        tracy = session.query(AgentDefinition).filter_by(slug="tracy").one_or_none()
        queue_status_counts = {}
        oldest_active = None
        if tracy is not None:
            for status, count in (
                session.query(AgentQueueItem.status, func.count(AgentQueueItem.id))
                .filter(AgentQueueItem.agent_id == tracy.id)
                .group_by(AgentQueueItem.status)
                .all()
            ):
                queue_status_counts[str(status)] = int(count)
            oldest = (
                session.query(func.min(AgentQueueItem.created_at))
                .filter(AgentQueueItem.agent_id == tracy.id)
                .filter(AgentQueueItem.status.in_(["queued", "running", "searching", "in_progress"]))
                .scalar()
            )
            oldest_active = oldest.isoformat() if oldest else None

        rights_counts = {
            str(status): int(count)
            for status, count in session.query(ProvenanceAssessment.rights_disposition, func.count(ProvenanceAssessment.id))
            .group_by(ProvenanceAssessment.rights_disposition)
            .all()
        }
        outcome_counts = {
            str(status): int(count)
            for status, count in session.query(ProvenanceAssessment.processing_outcome, func.count(ProvenanceAssessment.id))
            .group_by(ProvenanceAssessment.processing_outcome)
            .all()
        }
        missing_review_cases = (
            session.query(func.count(ProvenanceAssessment.id))
            .outerjoin(
                ReviewCase,
                and_(
                    ReviewCase.source_entity_type == "provenance_assessment",
                    ReviewCase.source_entity_id == ProvenanceAssessment.id,
                ),
            )
            .filter(ReviewCase.id.is_(None))
            .scalar()
        )
        assessments_without_open_review_cases = (
            session.query(func.count(ProvenanceAssessment.id))
            .outerjoin(
                ReviewCase,
                and_(
                    ReviewCase.source_entity_type == "provenance_assessment",
                    ReviewCase.source_entity_id == ProvenanceAssessment.id,
                    ReviewCase.closed_at.is_(None),
                ),
            )
            .filter(ReviewCase.id.is_(None))
            .scalar()
        )
        rights_lane_open = (
            session.query(func.count(ReviewCase.id))
            .filter(ReviewCase.source_entity_type == "provenance_assessment")
            .filter(ReviewCase.current_stage == "provenance_rights_review")
            .filter(ReviewCase.closed_at.is_(None))
            .scalar()
        )

    return {
        "generatedAt": utc_now().isoformat().replace("+00:00", "Z"),
        "queueStatusCounts": queue_status_counts,
        "oldestActiveQueueItemAt": oldest_active,
        "rightsDispositionCounts": rights_counts,
        "processingOutcomeCounts": outcome_counts,
        "assessmentsMissingReviewCases": int(missing_review_cases or 0),
        "assessmentsWithoutOpenReviewCases": int(assessments_without_open_review_cases or 0),
        "rightsLaneOpenCount": int(rights_lane_open or 0),
        "runtimeQueueFiles": runtime_files,
        "runtimeStatusCounts": dict(runtime_status_counts),
    }
