from __future__ import annotations

import asyncio
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import RuntimePersistenceBridge


AGENT_SPECS: dict[str, dict[str, Any]] = {
    "scott": {
        "runtime_root": Path("/data/.openclaw/workspaces/scott/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/scott/run_scott_intake.py"),
        "module": "symgov_scott_runner_worker",
        "persist_db": True,
        "downstream_path": Path("/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py"),
    },
    "vlad": {
        "runtime_root": Path("/data/.openclaw/workspaces/vlad/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/vlad/run_vlad_validation.py"),
        "module": "symgov_vlad_runner_worker",
        "persist_db": True,
        "storage": True,
    },
    "tracy": {
        "runtime_root": Path("/data/.openclaw/workspaces/tracy/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/tracy/run_tracy_provenance.py"),
        "module": "symgov_tracy_runner_worker",
        "persist_db": True,
    },
    "libby": {
        "runtime_root": Path("/data/.openclaw/workspaces/libby/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/libby/run_libby_classification.py"),
        "module": "symgov_libby_runner_worker",
        "persist_db": True,
    },
    "daisy": {
        "runtime_root": Path("/data/.openclaw/workspaces/daisy/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/daisy/run_daisy_coordination.py"),
        "module": "symgov_daisy_runner_worker",
        "persist_db": False,
    },
    "rupert": {
        "runtime_root": Path("/data/.openclaw/workspaces/rupert/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/rupert/run_rupert_publication.py"),
        "module": "symgov_rupert_runner_worker",
        "persist_db": True,
    },
    "ed": {
        "runtime_root": Path("/data/.openclaw/workspaces/ed/runtime"),
        "runner_path": Path("/data/.openclaw/workspaces/ed/run_ed_feedback.py"),
        "module": "symgov_ed_runner_worker",
        "persist_db": False,
    },
}

DEFAULT_AGENT_ORDER = ("scott", "vlad", "tracy", "libby", "daisy", "rupert", "ed")
LIBBY_RUNTIME_ROOT = AGENT_SPECS["libby"]["runtime_root"]


@dataclass(frozen=True)
class AgentQueueWorkerConfig:
    agents: tuple[str, ...] = ("libby",)
    db_env_file: Path | None = None
    storage_env_file: Path | None = None
    interval_seconds: float = 10.0
    limit: int = 10
    drain: bool = False
    runtime_roots: dict[str, Path] = field(default_factory=dict)


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_queue_item(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def runtime_root_for(agent: str, config: AgentQueueWorkerConfig) -> Path:
    if agent in config.runtime_roots:
        return config.runtime_roots[agent]
    try:
        return AGENT_SPECS[agent]["runtime_root"]
    except KeyError as exc:
        raise ValueError(f"Unsupported agent queue: {agent}") from exc


def queued_item_paths(runtime_root: Path, agent: str, limit: int) -> list[Path]:
    queue_dir = runtime_root / "agent_queue_items"
    if not queue_dir.exists():
        return []

    candidates = []
    for path in queue_dir.glob("*.json"):
        queue_item = load_queue_item(path)
        if not queue_item:
            continue
        if queue_item.get("agent_id") != agent:
            continue
        if queue_item.get("status") != "queued":
            continue
        candidates.append((str(queue_item.get("created_at") or ""), path))

    candidates.sort(key=lambda item: (item[0], item[1].name))
    return [path for _, path in candidates[: max(0, limit)]]


def process_scott_downstream(result: dict[str, Any], config: AgentQueueWorkerConfig) -> dict[str, Any] | None:
    intake_record_path = result.get("intake_record_path")
    if not intake_record_path:
        return None

    scott_spec = AGENT_SPECS["scott"]
    downstream = _load_module("symgov_scott_downstream_worker", scott_spec["downstream_path"])
    intake_record = downstream.load_json(intake_record_path)
    if intake_record.get("intake_status") != "accepted" or intake_record.get("eligibility_status") != "eligible":
        return {"created": {}, "reason": "intake_not_accepted_or_eligible"}

    route_to_agents = (intake_record.get("routing_recommendation_json") or {}).get("route_to_agents") or []
    timestamp = downstream.utc_stamp()
    created: dict[str, str] = {}
    db_created: dict[str, Any] = {}

    if "vlad" in route_to_agents:
        item = downstream.build_vlad_queue_item(intake_record, timestamp)
        path = runtime_root_for("vlad", config) / "agent_queue_items" / f"{item['id']}.json"
        downstream.write_json(path, item)
        created["vlad_queue_item_path"] = str(path)
        if config.db_env_file:
            db_created["vlad"] = RuntimePersistenceBridge(env_file=config.db_env_file).upsert_agent_queue_item(item)

    if "tracy" in route_to_agents:
        item = downstream.build_tracy_queue_item(intake_record, timestamp)
        path = runtime_root_for("tracy", config) / "agent_queue_items" / f"{item['id']}.json"
        downstream.write_json(path, item)
        created["tracy_queue_item_path"] = str(path)
        if config.db_env_file:
            db_created["tracy"] = RuntimePersistenceBridge(env_file=config.db_env_file).upsert_agent_queue_item(item)

    return {"route_to_agents": route_to_agents, "created": created, "dbCreated": db_created}


def process_agent_queue_once(agent: str, config: AgentQueueWorkerConfig) -> dict[str, Any]:
    if agent not in AGENT_SPECS:
        raise ValueError(f"Unsupported agent queue: {agent}")

    spec = AGENT_SPECS[agent]
    runtime_root = runtime_root_for(agent, config)
    runner = _load_module(spec["module"], spec["runner_path"])
    processed = []
    errors = []

    for queue_path in queued_item_paths(runtime_root, agent, config.limit):
        try:
            kwargs: dict[str, Any] = {"queue_item_path": queue_path, "runtime_root": runtime_root}
            if spec.get("persist_db"):
                kwargs["persist_db"] = True
                kwargs["db_env_file"] = str(config.db_env_file) if config.db_env_file else None
            if spec.get("storage"):
                kwargs["storage_env_file"] = str(config.storage_env_file) if config.storage_env_file else None
            result = runner.process_queue_item(**kwargs)
            if agent == "daisy" and config.db_env_file:
                bridge = RuntimePersistenceBridge(env_file=config.db_env_file)
                related_paths = result.get("completed_related_queue_item_paths") or [str(queue_path)]
                db_updates = []
                for related_path in related_paths:
                    related_queue_item = load_queue_item(Path(related_path))
                    if related_queue_item:
                        db_updates.append(bridge.upsert_agent_queue_item(related_queue_item))
                result["dbQueueStatusUpdates"] = db_updates
            downstream = process_scott_downstream(result, config) if agent == "scott" else None
            processed.append(
                {
                    "queueItemPath": str(queue_path),
                    "queueItemStatus": result.get("queue_item_status") or result.get("status"),
                    "downstreamAgent": result.get("downstream_agent"),
                    "downstreamQueueItemPath": result.get("downstream_queue_item_path"),
                    "downstream": downstream,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            errors.append({"queueItemPath": str(queue_path), "error": str(exc)})

    return {
        "agent": agent,
        "processedCount": len(processed),
        "errorCount": len(errors),
        "processed": processed,
        "errors": errors,
    }


def process_agent_queues_once(config: AgentQueueWorkerConfig) -> dict[str, Any]:
    results = [process_agent_queue_once(agent, config) for agent in config.agents]
    return {
        "agents": list(config.agents),
        "processedCount": sum(result["processedCount"] for result in results),
        "errorCount": sum(result["errorCount"] for result in results),
        "results": results,
    }


def drain_agent_queues(config: AgentQueueWorkerConfig, max_cycles: int = 50) -> dict[str, Any]:
    cycles = []
    for cycle in range(1, max_cycles + 1):
        result = process_agent_queues_once(config)
        cycles.append({"cycle": cycle, **result})
        if result["processedCount"] == 0:
            break
    return {
        "agents": list(config.agents),
        "cycleCount": len(cycles),
        "processedCount": sum(cycle["processedCount"] for cycle in cycles),
        "errorCount": sum(cycle["errorCount"] for cycle in cycles),
        "cycles": cycles,
    }


def process_libby_queue_once(config: AgentQueueWorkerConfig) -> dict[str, Any]:
    return process_agent_queue_once("libby", AgentQueueWorkerConfig(**{**config.__dict__, "agents": ("libby",)}))


async def run_agent_queue_worker(config: AgentQueueWorkerConfig, stop_event: asyncio.Event | None = None) -> None:
    while stop_event is None or not stop_event.is_set():
        if config.drain:
            await asyncio.to_thread(drain_agent_queues, config)
        else:
            await asyncio.to_thread(process_agent_queues_once, config)
        try:
            if stop_event is None:
                await asyncio.sleep(config.interval_seconds)
            else:
                await asyncio.wait_for(stop_event.wait(), timeout=config.interval_seconds)
        except asyncio.TimeoutError:
            continue


async def run_libby_queue_worker(config: AgentQueueWorkerConfig, stop_event: asyncio.Event | None = None) -> None:
    libby_config = AgentQueueWorkerConfig(**{**config.__dict__, "agents": ("libby",)})
    await run_agent_queue_worker(libby_config, stop_event)
