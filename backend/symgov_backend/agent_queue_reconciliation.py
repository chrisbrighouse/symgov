from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_queue_worker import AGENT_SPECS, DEFAULT_AGENT_ORDER
from .models import AgentDefinition, AgentQueueItem
from .runtime import RuntimePersistenceBridge, coerce_uuid

ACTIVE_STATUSES = {"queued", "running", "processing", "searching", "sensing"}
TERMINAL_STATUSES = {
    "cancelled",
    "completed",
    "escalated",
    "failed",
    "progress_saved",
    "published",
    "signals_recorded",
}


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

    with bridge.session_scope() as session:
        rows = (
            session.query(AgentQueueItem, AgentDefinition.slug)
            .join(AgentDefinition, AgentDefinition.id == AgentQueueItem.agent_id)
            .filter(AgentDefinition.slug.in_(agents))
            .order_by(AgentQueueItem.created_at.desc())
            .all()
        )
        for row, agent_slug in rows:
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
                "db_completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "runtime_completed_at": runtime_payload.get("completed_at"),
            }
            if reasons:
                skipped.append({**record, "reasons": reasons})
                continue

            if apply:
                bridge.upsert_agent_queue_item(runtime_payload)
            changes.append({**record, "applied": apply})

    return {
        "dry_run": not apply,
        "agents": list(agents),
        "active_only": active_only,
        "runtime_records_seen": len(runtime_records),
        "db_active_rows_inspected": len(inspected),
        "change_count": len(changes),
        "missing_runtime_count": len(missing_runtime),
        "skipped_count": len(skipped),
        "changes": changes,
        "missing_runtime": missing_runtime,
        "skipped": skipped,
    }
