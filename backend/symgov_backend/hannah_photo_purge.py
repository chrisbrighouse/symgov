"""Remove every Hannah supplemental photo and reset her curation history.

Hannah's keyword-scored photo search is being replaced (concept-level search
with a vision check), and Chris decided on 2026-10-19 that nothing she found
under the old process should survive. This module clears:

- the object-storage files under ``hannah/supplemental-photos/``;
- the ``attachments`` rows that point at them;
- every ``hannah_photo_candidates`` row (candidates, rejections, no-result rows);
- every ``hannah_symbol_curation_states`` row, so each symbol is searchable again;
- any Hannah queue card that is not yet finished, which is cancelled rather
  than deleted so its run history stays readable.

Queue history, agent runs and output artifacts are kept. Database changes are
committed first and the storage files deleted afterwards: a storage failure
then leaves an unreferenced, non-public file, never a row pointing at nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


HANNAH_OBJECT_PREFIX = "hannah/supplemental-photos/"
# Mirrors TERMINAL_CARD_STATUSES in scripts/run_hannah_curation.py.
TERMINAL_QUEUE_STATUSES = ("success", "completed", "candidate", "blocked", "retry_later", "cancelled", "stopped")
# Other tables with a foreign key to attachments. A Hannah attachment referenced
# from either of them is not Hannah's alone, so the purge refuses to touch it.
FOREIGN_ATTACHMENT_REFERENCES = (
    ("classification_records", "origin_attachment_id"),
    ("published_preview_authorizations", "attachment_id"),
)


def _hannah_agent_id(session: Session) -> str | None:
    value = session.execute(text("SELECT id FROM agent_definitions WHERE slug = 'hannah'")).scalar()
    return str(value) if value is not None else None


def plan_hannah_photo_purge(session: Session) -> dict[str, Any]:
    """Read what a purge would remove, without changing anything."""
    attachments = session.execute(
        text(
            "SELECT id, object_key FROM attachments WHERE object_key LIKE :prefix ORDER BY object_key"
        ),
        {"prefix": f"{HANNAH_OBJECT_PREFIX}%"},
    ).all()
    attachment_ids = [str(row[0]) for row in attachments]
    object_keys = sorted({str(row[1]) for row in attachments})
    # Candidates can carry an object key whose attachment row is already gone.
    object_keys = sorted(
        set(object_keys)
        | {
            str(key)
            for key in session.execute(
                text("SELECT DISTINCT object_key FROM hannah_photo_candidates WHERE object_key IS NOT NULL")
            ).scalars()
        }
    )

    blocking_references: list[dict[str, Any]] = []
    if attachment_ids:
        for table, column in FOREIGN_ATTACHMENT_REFERENCES:
            count = session.execute(
                text(f"SELECT count(*) FROM {table} WHERE CAST({column} AS TEXT) IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": attachment_ids},
            ).scalar()
            if count:
                blocking_references.append({"table": table, "column": column, "rows": int(count)})

    candidate_counts = {
        str(status): int(count)
        for status, count in session.execute(
            text("SELECT status, count(*) FROM hannah_photo_candidates GROUP BY status ORDER BY status")
        ).all()
    }
    curation_state_count = int(session.execute(text("SELECT count(*) FROM hannah_symbol_curation_states")).scalar() or 0)

    agent_id = _hannah_agent_id(session)
    open_queue_items: list[dict[str, str]] = []
    if agent_id is not None:
        open_queue_items = [
            {"id": str(row[0]), "status": str(row[1]), "source_type": str(row[2])}
            for row in session.execute(
                text(
                    "SELECT id, status, source_type FROM agent_queue_items "
                    "WHERE CAST(agent_id AS TEXT) = :agent_id AND status NOT IN :terminal ORDER BY created_at"
                ).bindparams(bindparam("terminal", expanding=True)),
                {"agent_id": agent_id, "terminal": list(TERMINAL_QUEUE_STATUSES)},
            ).all()
        ]

    return {
        "hannah_agent_id": agent_id,
        "attachment_ids": attachment_ids,
        "object_keys": object_keys,
        "blocking_references": blocking_references,
        "candidate_counts": candidate_counts,
        "candidate_total": sum(candidate_counts.values()),
        "curation_state_count": curation_state_count,
        "open_queue_items": open_queue_items,
    }


def purge_hannah_photos(
    session: Session,
    *,
    apply: bool,
    delete_object: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dry run unless ``apply``; with ``apply``, ``delete_object`` removes one storage key."""
    plan = plan_hannah_photo_purge(session)
    summary: dict[str, Any] = {
        "applied": False,
        "attachments": len(plan["attachment_ids"]),
        "object_keys": plan["object_keys"],
        "candidates": plan["candidate_counts"],
        "candidate_total": plan["candidate_total"],
        "curation_states": plan["curation_state_count"],
        "open_queue_items_to_cancel": plan["open_queue_items"],
        "blocking_references": plan["blocking_references"],
    }
    if not apply:
        return summary
    if plan["blocking_references"]:
        raise RuntimeError(
            "Hannah attachments are referenced from other tables; nothing was changed: "
            + json.dumps(plan["blocking_references"])
        )
    if delete_object is None:
        raise ValueError("delete_object is required when apply is true.")

    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    session.execute(text("DELETE FROM hannah_photo_candidates"))
    session.execute(text("DELETE FROM hannah_symbol_curation_states"))
    if plan["attachment_ids"]:
        session.execute(
            text("DELETE FROM attachments WHERE CAST(id AS TEXT) IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": plan["attachment_ids"]},
        )
    open_ids = [item["id"] for item in plan["open_queue_items"]]
    if open_ids:
        session.execute(
            text(
                "UPDATE agent_queue_items SET status = 'cancelled', completed_at = :now "
                "WHERE CAST(id AS TEXT) IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"now": now, "ids": open_ids},
        )
    if plan["hannah_agent_id"] is not None:
        payload_value = "CAST(:payload AS jsonb)" if session.get_bind().dialect.name == "postgresql" else ":payload"
        session.execute(
            text(
                "INSERT INTO audit_events (id, entity_type, entity_id, action, actor_id, payload_json, created_at) "
                f"VALUES (:id, 'agent_definition', :entity_id, 'hannah_photos_purged', NULL, {payload_value}, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "entity_id": plan["hannah_agent_id"],
                "payload": json.dumps(
                    {
                        "attachments": len(plan["attachment_ids"]),
                        "object_keys": plan["object_keys"],
                        "candidates": plan["candidate_counts"],
                        "curation_states": plan["curation_state_count"],
                        "cancelled_queue_items": open_ids,
                    }
                ),
                "now": now,
            },
        )
    session.commit()

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for object_key in plan["object_keys"]:
        try:
            delete_object(object_key)
            deleted.append(object_key)
        except Exception as exc:  # keep going: each file is independent
            failed.append({"object_key": object_key, "error": str(exc)})

    summary.update({"applied": True, "objects_deleted": deleted, "object_delete_failures": failed})
    return summary
