from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_queue_worker import AGENT_SPECS, DEFAULT_AGENT_ORDER
from .models import AgentDefinition, AgentQueueItem
from .runtime import RuntimePersistenceBridge, coerce_uuid

QUEUE_STATUS_GROUPS: dict[str, frozenset[str]] = {
    "active": frozenset({"queued", "running", "processing", "searching", "sensing", "in_progress"}),
    "waiting_operator": frozenset({"escalated", "candidate", "needs_review", "duplicate_pending"}),
    "terminal": frozenset(
        {
            "blocked",
            "cancelled",
            "completed",
            "deleted",
            "duplicate_resolved",
            "failed",
            "progress_saved",
            "published",
            "rejected",
            "signals_recorded",
        }
    ),
}
ACTIVE_STATUSES = set(QUEUE_STATUS_GROUPS["active"])
TERMINAL_STATUSES = set(QUEUE_STATUS_GROUPS["terminal"])
WAITING_OPERATOR_STATUSES = set(QUEUE_STATUS_GROUPS["waiting_operator"])
KNOWN_QUEUE_STATUSES = set().union(*QUEUE_STATUS_GROUPS.values())


def queue_status_group(status: str | None) -> str:
    """Return the explicit lifecycle group for an agent_queue_items status."""

    normalized = (status or "").strip()
    for group, statuses in QUEUE_STATUS_GROUPS.items():
        if normalized in statuses:
            return group
    return "unknown"


def is_active_queue_status(status: str | None) -> bool:
    return queue_status_group(status) == "active"


def is_terminal_queue_status(status: str | None) -> bool:
    return queue_status_group(status) == "terminal"


@dataclass(frozen=True)
class QueueRuntimeRecord:
    agent: str
    path: Path
    payload: dict[str, Any]

    @property
    def queue_item_id(self):
        return coerce_uuid(self.payload.get("id"))

    @property
    def source_id(self):
        return coerce_uuid(self.payload.get("source_id"))

    @property
    def status(self) -> str:
        return str(self.payload.get("status") or "")


def _coerce_optional_uuid(value: Any):
    parsed = coerce_uuid(value)
    return parsed if parsed is not None else coerce_uuid("00000000-0000-0000-0000-000000000000")


def _queue_payload_identity(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in {
            "candidate_symbol_id": payload.get("candidate_symbol_id"),
            "published_display_id": payload.get("published_display_id"),
            "symbol_display_id": payload.get("symbol_display_id"),
            "display_name": payload.get("display_name") or payload.get("displayName"),
            "workspace_display_name": payload.get("workspace_display_name"),
            "symbol_slug": payload.get("symbol_slug"),
            "package_display_id": payload.get("package_display_id") or payload.get("packageDisplayId"),
            "package_symbol_sequence": payload.get("package_symbol_sequence") or payload.get("packageSymbolSequence"),
        }.items()
        if value is not None
    }


def _queue_suggestion(
    *,
    rule_code: str,
    severity: str,
    queue_item_id: str | None,
    detail: str,
    suggested_remediation: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_type": "agent_queue_item",
        "source_id": _coerce_optional_uuid(queue_item_id),
        "severity": severity,
        "rule_code": rule_code,
        "detail": detail,
        "status": "open",
        "suggested_remediation": suggested_remediation,
        "observational_only": True,
        "evidence": evidence,
    }


def build_reggie_queue_control_suggestions(
    *,
    missing_runtime: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    runtime_orphans: list[dict[str, Any]],
    changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert reconciliation findings into Reggie suggestions, not auto-fixes.

    Reggie's first control-room role is observational: identify DB/runtime
    ambiguity and suggest a remediation path for an operator. This function does
    not mutate queue rows, runtime JSON, prompts, or control exception tables.
    """

    suggestions: list[dict[str, Any]] = []
    for row in missing_runtime:
        status_group = queue_status_group(row.get("db_status"))
        suggestions.append(
            _queue_suggestion(
                rule_code="agent_queue_active_db_missing_runtime",
                severity="warning" if status_group == "active" else "info",
                queue_item_id=row.get("queue_item_id"),
                detail=(
                    f"{row.get('agent')} queue item {row.get('queue_item_id')} is {row.get('db_status')} in DB "
                    "but has no matching runtime JSON record."
                ),
                suggested_remediation="Inspect the agent runtime directory and either restore/requeue the work item or mark the DB row with an explicit terminal status after operator review.",
                evidence={**row, "db_status_group": status_group},
            )
        )

    for row in changes or []:
        suggestions.append(
            _queue_suggestion(
                rule_code="agent_queue_db_runtime_terminal_mismatch",
                severity="info",
                queue_item_id=row.get("queue_item_id"),
                detail=(
                    f"{row.get('agent')} queue item {row.get('queue_item_id')} is {row.get('db_status')} in DB "
                    f"but runtime terminal status is {row.get('runtime_status')}."
                ),
                suggested_remediation="Review the runtime terminal status and, if valid, run the explicit reconciliation apply path to update the DB row.",
                evidence={**row, "runtime_status_group": queue_status_group(row.get("runtime_status"))},
            )
        )

    for row in skipped:
        suggestions.append(
            _queue_suggestion(
                rule_code="agent_queue_db_runtime_mismatch_skipped",
                severity="warning",
                queue_item_id=row.get("queue_item_id"),
                detail=f"Queue item {row.get('queue_item_id')} could not be reconciled automatically: {', '.join(row.get('reasons') or [])}.",
                suggested_remediation="Inspect DB row and runtime JSON manually before applying any state change.",
                evidence=row,
            )
        )

    for row in runtime_orphans:
        suggestions.append(
            _queue_suggestion(
                rule_code="agent_queue_runtime_without_db_mirror",
                severity="warning",
                queue_item_id=row.get("queue_item_id"),
                detail=(
                    f"Runtime queue record {row.get('queue_item_id')} for {row.get('agent')} has no DB mirror."
                ),
                suggested_remediation="Inspect the runtime JSON and decide whether to backfill a DB mirror row or archive the stale runtime record.",
                evidence={**row, "runtime_status_group": queue_status_group(row.get("runtime_status"))},
            )
        )
    return suggestions


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def iter_runtime_queue_records(agents: tuple[str, ...] = DEFAULT_AGENT_ORDER) -> list[QueueRuntimeRecord]:
    records: list[QueueRuntimeRecord] = []
    for agent in agents:
        spec = AGENT_SPECS.get(agent)
        if spec is None:
            continue
        queue_dir = Path(spec["runtime_root"]) / "agent_queue_items"
        if not queue_dir.exists():
            continue
        for path in sorted(queue_dir.glob("*.json")):
            payload = _load_json(path)
            if not payload:
                continue
            if payload.get("agent_id") != agent:
                continue
            records.append(QueueRuntimeRecord(agent=agent, path=path, payload=payload))
    return records


def reconcile_agent_queue_state(
    *,
    db_env_file: str | Path | None = None,
    agents: tuple[str, ...] = DEFAULT_AGENT_ORDER,
    apply: bool = False,
    active_only: bool = True,
) -> dict[str, Any]:
    """Compare DB agent_queue_items with runtime queue JSON files.

    Runtime JSON is allowed to repair a DB row only when:
    - the runtime queue id maps to the same DB UUID,
    - agent slug, source_type, and source_id match,
    - the runtime status is terminal,
    - and the DB row is active when active_only=True.

    The default is dry-run. Passing apply=True writes verified terminal runtime
    records back through RuntimePersistenceBridge.upsert_agent_queue_item.
    """

    bridge = RuntimePersistenceBridge(env_file=str(db_env_file) if db_env_file else None)
    runtime_records = iter_runtime_queue_records(agents)
    runtime_by_id = {record.queue_item_id: record for record in runtime_records if record.queue_item_id is not None}

    inspected = []
    changes = []
    skipped = []
    missing_runtime = []
    runtime_orphans = []
    db_queue_ids = set()

    with bridge.session_scope() as session:
        rows = (
            session.query(AgentQueueItem, AgentDefinition.slug)
            .join(AgentDefinition, AgentDefinition.id == AgentQueueItem.agent_id)
            .filter(AgentDefinition.slug.in_(agents))
            .order_by(AgentQueueItem.created_at.desc())
            .all()
        )
        for row, agent_slug in rows:
            db_queue_ids.add(row.id)
            db_status = row.status
            if active_only and db_status not in ACTIVE_STATUSES:
                continue
            runtime = runtime_by_id.get(row.id)
            if runtime is None:
                missing_runtime.append(
                    {
                        "queue_item_id": str(row.id),
                        "agent": agent_slug,
                        "db_status": db_status,
                        "source_type": row.source_type,
                        "source_id": str(row.source_id) if row.source_id else None,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        **_queue_payload_identity(row.payload_json),
                    }
                )
                continue

            inspected.append(str(row.id))
            reasons: list[str] = []
            runtime_payload = runtime.payload
            runtime_source_type = runtime_payload.get("source_type")
            runtime_source_id = runtime.source_id

            if runtime.agent != agent_slug:
                reasons.append("agent_mismatch")
            if runtime_source_type != row.source_type:
                reasons.append("source_type_mismatch")
            if runtime_source_id != row.source_id:
                reasons.append("source_id_mismatch")
            if runtime.status not in TERMINAL_STATUSES:
                reasons.append("runtime_status_not_terminal")
            if db_status == runtime.status:
                reasons.append("status_already_matches")

            record = {
                "queue_item_id": str(row.id),
                "agent": agent_slug,
                "runtime_path": str(runtime.path),
                "db_status": db_status,
                "runtime_status": runtime.status,
                "source_type": row.source_type,
                "source_id": str(row.source_id) if row.source_id else runtime_source_id,
                "created_at": row.created_at.isoformat() if row.created_at else runtime_payload.get("created_at"),
                "db_completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "runtime_completed_at": runtime_payload.get("completed_at"),
                **_queue_payload_identity(row.payload_json),
                **_queue_payload_identity(runtime_payload.get("payload") if isinstance(runtime_payload.get("payload"), dict) else runtime_payload),
            }
            if reasons:
                skipped.append({**record, "reasons": reasons})
                continue

            if apply:
                bridge.upsert_agent_queue_item(runtime_payload)
            changes.append({**record, "applied": apply})

    for runtime in runtime_records:
        runtime_id = runtime.queue_item_id
        if runtime_id is None or runtime_id in db_queue_ids:
            continue
        runtime_orphans.append(
            {
                "queue_item_id": str(runtime_id),
                "agent": runtime.agent,
                "runtime_path": str(runtime.path),
                "runtime_status": runtime.status,
                "source_type": runtime.payload.get("source_type"),
                "source_id": runtime.source_id,
                "created_at": runtime.payload.get("created_at") or runtime.payload.get("createdAt"),
                **_queue_payload_identity(runtime.payload.get("payload") if isinstance(runtime.payload.get("payload"), dict) else runtime.payload),
            }
        )

    control_suggestions = build_reggie_queue_control_suggestions(
        missing_runtime=missing_runtime,
        skipped=skipped,
        runtime_orphans=runtime_orphans,
        changes=changes,
    )

    return {
        "dry_run": not apply,
        "agents": list(agents),
        "active_only": active_only,
        "runtime_records_seen": len(runtime_records),
        "db_active_rows_inspected": len(inspected),
        "change_count": len(changes),
        "missing_runtime_count": len(missing_runtime),
        "runtime_orphan_count": len(runtime_orphans),
        "skipped_count": len(skipped),
        "control_suggestion_count": len(control_suggestions),
        "changes": changes,
        "missing_runtime": missing_runtime,
        "runtime_orphans": runtime_orphans,
        "skipped": skipped,
        "control_suggestions": control_suggestions,
    }
