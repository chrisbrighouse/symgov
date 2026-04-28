from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path("/data/.openclaw/workspace/symgov")
DEFAULT_CONFIG_PATH = WORKSPACE_ROOT / "symgov-notifications.json"


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {"available": False, "reason": "missing_config", "config_path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "reason": "invalid_config_json",
            "config_path": str(path),
            "detail": str(exc),
        }
    data["available"] = True
    data["config_path"] = str(path)
    return data


def _resolve_target(agent_id: str, config: dict[str, Any]) -> dict[str, Any]:
    targets = config.get("targets") or {}
    agents = config.get("agents") or {}
    agent_entry = agents.get(agent_id) or {}
    if not agent_entry.get("enabled", True):
        return {"enabled": False, "reason": "agent_disabled"}
    target_name = agent_entry.get("target") or config.get("default_target")
    target = targets.get(target_name) if target_name else None
    if not target:
        return {"enabled": False, "reason": "missing_target", "target_name": target_name}
    if not target.get("enabled", True):
        return {"enabled": False, "reason": "target_disabled", "target_name": target_name}
    channel = _normalize_text(target.get("channel"))
    destination = _normalize_text(target.get("target"))
    if not channel or not destination:
        return {"enabled": False, "reason": "target_incomplete", "target_name": target_name}
    return {
        "enabled": True,
        "name": target_name,
        "channel": channel,
        "target": destination,
        "label": _normalize_text(target.get("label")) or target_name,
        "agent_config": agent_entry,
    }


def _message_lines(*values: Any) -> str:
    return "\n".join(str(value) for value in values if _normalize_text(value))


def _verbosity(agent_config: dict[str, Any], phase: str) -> str:
    verbosity = agent_config.get("verbosity")
    if isinstance(verbosity, dict):
        value = _normalize_text(verbosity.get(phase)) or _normalize_text(verbosity.get("default"))
    else:
        value = _normalize_text(verbosity)
    if value not in {"terse", "normal", "detailed"}:
        return "normal"
    return value


def _phase_enabled(agent_config: dict[str, Any], phase: str) -> bool:
    phases = agent_config.get("phases")
    if not isinstance(phases, dict):
        return True
    value = phases.get(phase)
    if value is None:
        return True
    return bool(value)


def _source_label(queue_item: dict[str, Any], artifact: dict[str, Any] | None = None) -> str | None:
    payload = queue_item.get("payload_json") or {}
    normalized = (artifact or {}).get("normalized_technical_metadata") or {}
    submission = (artifact or {}).get("normalized_submission") or {}
    payload_file_name = _normalize_text(payload.get("file_name"))
    asset_file_name = _normalize_text(Path(payload["asset_path"]).name) if payload.get("asset_path") else None
    return (
        _normalize_text(normalized.get("file_name"))
        or _normalize_text(submission.get("file_name"))
        or payload_file_name
        or asset_file_name
    )


def _build_start_message(agent_id: str, queue_item: dict[str, Any], verbosity: str) -> str:
    payload = queue_item.get("payload_json") or {}
    if verbosity == "terse":
        return _message_lines(
            f"SymGov {agent_id.capitalize()} started",
            f"file: {_source_label(queue_item) or 'n/a'}",
            f"queue: {queue_item.get('id')}",
        )
    if verbosity == "detailed":
        return _message_lines(
            f"SymGov {agent_id.capitalize()} started",
            f"queue: {queue_item.get('id')}",
            f"source_type: {_normalize_text(queue_item.get('source_type')) or 'unknown'}",
            f"source_id: {_normalize_text(queue_item.get('source_id')) or 'unknown'}",
            f"file: {_source_label(queue_item) or 'n/a'}",
            f"priority: {_normalize_text(queue_item.get('priority')) or 'n/a'}",
            f"submitted_by: {_normalize_text(payload.get('submitted_by')) or 'n/a'}",
            f"batch: {_normalize_text(payload.get('submission_batch_id')) or 'n/a'}",
        )
    return _message_lines(
        f"SymGov {agent_id.capitalize()} started",
        f"queue: {queue_item.get('id')}",
        f"file: {_source_label(queue_item) or 'n/a'}",
        f"priority: {_normalize_text(queue_item.get('priority')) or 'n/a'}",
    )


def _build_finish_message(
    agent_id: str,
    queue_item: dict[str, Any],
    artifact: dict[str, Any],
    queue_status: str,
    verbosity: str,
) -> str:
    decision = _normalize_text(artifact.get("decision")) or queue_status
    confidence = artifact.get("confidence")
    header = f"SymGov {agent_id.capitalize()} {queue_status}"
    common = [
        header,
        f"queue: {queue_item.get('id')}",
        f"file: {_source_label(queue_item, artifact) or 'n/a'}",
        f"decision: {decision}",
        f"confidence: {confidence if confidence is not None else 'n/a'}",
    ]
    if verbosity == "terse":
        defects = artifact.get("defects") or []
        if defects:
            common.append(f"top_defect: {defects[0].get('code')}")
        elif artifact.get("escalation_target") not in {None, "none"}:
            common.append(f"escalation_target: {artifact.get('escalation_target')}")
        return _message_lines(*common)

    if agent_id == "scott":
        common.extend(
            [
                f"eligibility: {_normalize_text(artifact.get('eligibility_status')) or 'n/a'}",
                f"routes: {', '.join((artifact.get('routing_recommendation') or {}).get('route_to_agents') or ['none'])}",
            ]
        )
        if verbosity == "detailed":
            common.extend(
                [
                    f"submission_kind: {_normalize_text((artifact.get('normalized_submission') or {}).get('submission_kind')) or 'n/a'}",
                    f"format: {_normalize_text((artifact.get('normalized_submission') or {}).get('file_format')) or 'n/a'}",
                    f"reason_codes: {', '.join((artifact.get('routing_recommendation') or {}).get('reason_codes') or ['none'])}",
                ]
            )
    elif agent_id == "tracy":
        common.extend(
            [
                f"rights_status: {_normalize_text(artifact.get('rights_status')) or 'n/a'}",
                f"risk_level: {_normalize_text(artifact.get('risk_level')) or 'n/a'}",
            ]
        )
        if verbosity in {"normal", "detailed"}:
            common.append(f"summary: {_normalize_text(artifact.get('reviewer_summary')) or 'n/a'}")
        if verbosity == "detailed":
            common.append(
                f"recommended_actions: {', '.join(artifact.get('recommended_actions') or ['none'])}"
            )
    elif agent_id == "vlad":
        technical = artifact.get("normalized_technical_metadata") or {}
        common.extend(
            [
                f"sheet_type: {_normalize_text(technical.get('sheet_type')) or 'n/a'}",
                f"estimated_symbols: {technical.get('estimated_symbol_count', 'n/a')}",
                f"proposed_children: {technical.get('proposed_child_count', 'n/a')}",
            ]
        )
        if verbosity == "detailed":
            common.extend(
                [
                    f"split_status: {_normalize_text(technical.get('split_status')) or 'n/a'}",
                    f"ocr_assigned: {(technical.get('ocr_label_summary') or {}).get('assigned_count', 'n/a')}",
                    f"defect_count: {len(artifact.get('defects') or [])}",
                ]
            )
    elif agent_id == "daisy":
        common.extend(
            [
                f"assignment_proposals: {len(artifact.get('assignment_proposals') or [])}",
                f"stage_proposals: {len(artifact.get('stage_transition_proposals') or [])}",
            ]
        )
        if verbosity in {"normal", "detailed"}:
            common.append(f"summary: {_normalize_text(artifact.get('coordination_summary')) or 'n/a'}")
        if verbosity == "detailed":
            common.append(
                f"evidence_requests: {len(artifact.get('contributor_evidence_requests') or [])}"
            )
    elif agent_id == "libby":
        common.extend(
            [
                f"classification_status: {_normalize_text(artifact.get('classification_status')) or 'n/a'}",
                f"source_classification: {_normalize_text(artifact.get('source_classification')) or 'n/a'}",
                f"libby_approved: {artifact.get('libby_approved')}",
            ]
        )
        if verbosity in {"normal", "detailed"}:
            common.append(f"summary: {_normalize_text(artifact.get('classification_summary')) or 'n/a'}")
        if verbosity == "detailed":
            common.append(
                f"aliases: {', '.join(artifact.get('aliases') or ['none'])}"
            )
    elif agent_id == "rupert":
        standards = artifact.get("standards_availability_summary") or {}
        common.extend(
            [
                f"release_target: {_normalize_text(artifact.get('release_target')) or 'n/a'}",
                f"publication_state: {_normalize_text(standards.get('publication_state')) or 'n/a'}",
                f"symbol_count: {standards.get('symbol_count', 'n/a')}",
            ]
        )
        if verbosity in {"normal", "detailed"}:
            common.append(f"summary: {_normalize_text(artifact.get('publication_summary')) or 'n/a'}")
        if verbosity == "detailed":
            common.extend(
                [
                    f"pack_code: {_normalize_text(standards.get('pack_code')) or 'n/a'}",
                    f"release_manifest: {_normalize_text(artifact.get('release_manifest_path')) or 'n/a'}",
                ]
            )
    elif agent_id == "ed":
        findings = artifact.get("interface_findings") or []
        recommendations = artifact.get("recommendations") or []
        common.extend(
            [
                f"findings: {len(findings)}",
                f"recommendations: {len(recommendations)}",
            ]
        )
        if verbosity in {"normal", "detailed"}:
            common.append(f"summary: {_normalize_text(artifact.get('feedback_summary')) or 'n/a'}")
        if verbosity == "detailed" and findings:
            top = findings[0]
            common.append(
                f"top_finding: {top.get('interface', 'general')} / {top.get('severity', 'unknown')}"
            )

    defects = artifact.get("defects") or []
    if defects:
        top = defects[0]
        common.append(f"top_defect: {top.get('code')}: {top.get('detail')}")
    elif artifact.get("escalation_target") not in {None, "none"}:
        common.append(f"escalation_target: {artifact.get('escalation_target')}")

    return _message_lines(*common)


def send_agent_status_update(
    agent_id: str,
    phase: str,
    queue_item: dict[str, Any],
    artifact: dict[str, Any] | None = None,
    queue_status: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    config = _load_config(config_path)
    if not config.get("available"):
        return {"ok": False, "skipped": True, **config}

    target = _resolve_target(agent_id, config)
    if not target.get("enabled"):
        return {"ok": False, "skipped": True, "config_path": config["config_path"], **target}
    agent_config = target.get("agent_config") or {}
    if not _phase_enabled(agent_config, phase):
        return {
            "ok": False,
            "skipped": True,
            "config_path": config["config_path"],
            "reason": "phase_disabled",
            "phase": phase,
            "target": target,
        }

    if shutil.which("openclaw") is None:
        return {
            "ok": False,
            "skipped": True,
            "config_path": config["config_path"],
            "reason": "missing_openclaw_cli",
        }

    verbosity = _verbosity(agent_config, phase)
    if phase == "started":
        message = _build_start_message(agent_id, queue_item, verbosity)
    else:
        message = _build_finish_message(agent_id, queue_item, artifact or {}, queue_status or "completed", verbosity)

    command = [
        "openclaw",
        "message",
        "send",
        "--channel",
        target["channel"],
        "--target",
        target["target"],
        "--message",
        message,
        "--json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False,
            "skipped": False,
            "config_path": config["config_path"],
            "target": target,
            "reason": "send_failed",
            "detail": str(exc),
        }

    response = {
        "ok": result.returncode == 0,
        "skipped": False,
        "config_path": config["config_path"],
        "target": target,
        "phase": phase,
        "verbosity": verbosity,
        "message": message,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if result.returncode != 0:
        response["reason"] = "openclaw_send_failed"
    return response
