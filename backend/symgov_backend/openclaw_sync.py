from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
OPENCLAW_ROOT = WORKSPACE_ROOT.parents[1]
OPENCLAW_CONFIG = OPENCLAW_ROOT / "openclaw.json"
MANIFEST_PATH = WORKSPACE_ROOT / "openclaw-agents.manifest.json"


@dataclass(frozen=True)
class OpenClawAgentSpec:
    id: str
    name: str
    workspace: Path
    agent_dir: Path
    model: str
    identity_name: str
    tools: dict[str, Any]
    agent_to_agent: bool
    required_workspace_files: tuple[str, ...]


@dataclass(frozen=True)
class OpenClawManifest:
    safe_plugins_allow: tuple[str, ...]
    safe_plugins_disable: tuple[str, ...]
    bindings: tuple[dict[str, Any], ...]
    agents: tuple[OpenClawAgentSpec, ...]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2) + "\n"


def load_manifest(path: str | Path | None = None) -> OpenClawManifest:
    manifest_path = Path(path) if path else MANIFEST_PATH
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    agents = tuple(
        OpenClawAgentSpec(
            id=entry["id"],
            name=entry["name"],
            workspace=Path(entry["workspace"]),
            agent_dir=Path(entry["agent_dir"]),
            model=entry["model"],
            identity_name=entry["identity_name"],
            tools=entry["tools"],
            agent_to_agent=bool(entry.get("agent_to_agent", False)),
            required_workspace_files=tuple(entry.get("required_workspace_files", [])),
        )
        for entry in data["agents"]
    )
    safe_plugins = data["safe_plugins"]
    return OpenClawManifest(
        safe_plugins_allow=tuple(safe_plugins.get("allow", [])),
        safe_plugins_disable=tuple(safe_plugins.get("disable", [])),
        bindings=tuple(data.get("bindings", [])),
        agents=agents,
    )


def _normalize_bindings(bindings: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return sorted(
        (deepcopy(binding) for binding in bindings),
        key=lambda item: _json_dumps(item),
    )


def _expected_agent_record(spec: OpenClawAgentSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "workspace": str(spec.workspace),
        "agentDir": str(spec.agent_dir),
        "model": spec.model,
        "identity": {"name": spec.identity_name},
        "tools": deepcopy(spec.tools),
    }


def _expected_agent_file(spec: OpenClawAgentSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "identityName": spec.identity_name,
        "model": spec.model,
        "workspace": str(spec.workspace),
    }


def _workspace_state_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "setupCompletedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _write_json_if_changed(path: Path, payload: Any) -> bool:
    expected = _json_dumps(payload)
    if path.exists() and path.read_text(encoding="utf-8") == expected:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8")
    return True


def audit_openclaw_registration(
    manifest_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    resolved_config = Path(config_path) if config_path else OPENCLAW_CONFIG
    config = json.loads(resolved_config.read_text(encoding="utf-8"))

    plugins = config.get("plugins", {})
    allow = plugins.get("allow", [])
    entries = plugins.get("entries", {})
    live_bindings = _normalize_bindings(config.get("bindings", []))
    expected_bindings = _normalize_bindings(list(manifest.bindings))

    plugin_status = {
        "allow_missing": [name for name in manifest.safe_plugins_allow if name not in allow],
        "allow_unexpected_enabled": [name for name in manifest.safe_plugins_disable if name in allow],
        "disabled_plugins_still_enabled": [
            name for name in manifest.safe_plugins_disable if entries.get(name, {}).get("enabled") is True
        ],
    }

    agent_list = config.get("agents", {}).get("list", [])
    agents_by_id = {entry.get("id"): entry for entry in agent_list if entry.get("id")}
    agent_to_agent_allow = set(config.get("tools", {}).get("agentToAgent", {}).get("allow", []))

    agent_status: list[dict[str, Any]] = []
    for spec in manifest.agents:
        entry = agents_by_id.get(spec.id)
        expected = _expected_agent_record(spec)
        workspace_checks = []
        for relative_path in spec.required_workspace_files:
            path = spec.workspace / relative_path
            workspace_checks.append(
                {
                    "path": str(path),
                    "exists": path.exists(),
                }
            )

        agent_json_path = spec.agent_dir / "agent.json"
        workspace_state_path = spec.workspace / ".openclaw" / "workspace-state.json"
        agent_json_ok = False
        if agent_json_path.exists():
            try:
                agent_json_ok = json.loads(agent_json_path.read_text(encoding="utf-8")) == _expected_agent_file(spec)
            except json.JSONDecodeError:
                agent_json_ok = False

        if entry is None:
            config_ok = False
            mismatches = ["missing from openclaw config"]
        else:
            mismatches = [
                field
                for field, expected_value in expected.items()
                if entry.get(field) != expected_value
            ]
            config_ok = not mismatches

        agent_status.append(
            {
                "id": spec.id,
                "config_present": entry is not None,
                "config_ok": config_ok,
                "config_mismatches": mismatches,
                "agent_json_path": str(agent_json_path),
                "agent_json_ok": agent_json_ok,
                "workspace_state_path": str(workspace_state_path),
                "workspace_state_exists": workspace_state_path.exists(),
                "agent_to_agent_expected": spec.agent_to_agent,
                "agent_to_agent_present": spec.id in agent_to_agent_allow,
                "required_workspace_files": workspace_checks,
            }
        )

    return {
        "manifest_path": str(Path(manifest_path) if manifest_path else MANIFEST_PATH),
        "config_path": str(resolved_config),
        "plugin_status": plugin_status,
        "bindings": {
            "expected_count": len(expected_bindings),
            "actual_count": len(live_bindings),
            "ok": live_bindings == expected_bindings,
            "expected": expected_bindings,
            "actual": live_bindings,
        },
        "agents": agent_status,
        "healthy": (
            not plugin_status["allow_missing"]
            and not plugin_status["allow_unexpected_enabled"]
            and not plugin_status["disabled_plugins_still_enabled"]
            and live_bindings == expected_bindings
            and all(
                agent["config_ok"]
                and agent["agent_json_ok"]
                and agent["workspace_state_exists"]
                and (agent["agent_to_agent_present"] or not agent["agent_to_agent_expected"])
                and all(item["exists"] for item in agent["required_workspace_files"])
                for agent in agent_status
            )
        ),
    }


def reconcile_openclaw_registration(
    manifest_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    resolved_config = Path(config_path) if config_path else OPENCLAW_CONFIG
    config = json.loads(resolved_config.read_text(encoding="utf-8"))
    operations: list[dict[str, Any]] = []

    plugins = config.setdefault("plugins", {})
    allow = plugins.setdefault("allow", [])
    entries = plugins.setdefault("entries", {})

    for plugin_name in manifest.safe_plugins_disable:
        if plugin_name in allow:
            allow.remove(plugin_name)
            operations.append({"kind": "config", "target": "plugins.allow", "action": "removed", "plugin": plugin_name})
        plugin_entry = entries.setdefault(plugin_name, {})
        if plugin_entry.get("enabled") is not False:
            plugin_entry["enabled"] = False
            operations.append(
                {"kind": "config", "target": f"plugins.entries.{plugin_name}.enabled", "action": "set", "value": False}
            )

    for plugin_name in manifest.safe_plugins_allow:
        if plugin_name not in allow:
            allow.append(plugin_name)
            operations.append({"kind": "config", "target": "plugins.allow", "action": "added", "plugin": plugin_name})

    current_bindings = _normalize_bindings(config.get("bindings", []))
    expected_bindings = _normalize_bindings(list(manifest.bindings))
    if current_bindings != expected_bindings:
        config["bindings"] = expected_bindings
        operations.append(
            {
                "kind": "config",
                "target": "bindings",
                "action": "replaced",
                "expected_count": len(expected_bindings),
            }
        )

    agents = config.setdefault("agents", {}).setdefault("list", [])
    agents_by_id = {entry.get("id"): entry for entry in agents if entry.get("id")}
    agent_to_agent_allow = config.setdefault("tools", {}).setdefault("agentToAgent", {}).setdefault("allow", [])

    for spec in manifest.agents:
        expected = _expected_agent_record(spec)
        current = agents_by_id.get(spec.id)
        if current is None:
            agents.append(expected)
            operations.append({"kind": "config", "target": f"agents.list[{spec.id}]", "action": "inserted"})
        elif current != expected:
            current.clear()
            current.update(expected)
            operations.append({"kind": "config", "target": f"agents.list[{spec.id}]", "action": "updated"})

        if spec.agent_to_agent and spec.id not in agent_to_agent_allow:
            agent_to_agent_allow.append(spec.id)
            operations.append({"kind": "config", "target": "tools.agentToAgent.allow", "action": "added", "agent": spec.id})

        agent_file_path = spec.agent_dir / "agent.json"
        if _write_json_if_changed(agent_file_path, _expected_agent_file(spec)):
            operations.append({"kind": "file", "target": str(agent_file_path), "action": "wrote"})

        sessions_dir = spec.agent_dir.parent / "sessions"
        if not sessions_dir.exists():
            sessions_dir.mkdir(parents=True, exist_ok=True)
            operations.append({"kind": "file", "target": str(sessions_dir), "action": "created_dir"})

        workspace_state_path = spec.workspace / ".openclaw" / "workspace-state.json"
        if not workspace_state_path.exists():
            _write_json_if_changed(workspace_state_path, _workspace_state_payload())
            operations.append({"kind": "file", "target": str(workspace_state_path), "action": "wrote"})

    if _write_json_if_changed(resolved_config, config):
        operations.append({"kind": "file", "target": str(resolved_config), "action": "wrote"})

    return {
        "manifest_path": str(Path(manifest_path) if manifest_path else MANIFEST_PATH),
        "config_path": str(resolved_config),
        "operations": operations,
        "postcheck": audit_openclaw_registration(manifest_path=manifest_path, config_path=resolved_config),
    }
