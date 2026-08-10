import json
from pathlib import Path

from symgov_backend import openclaw_sync


def test_load_manifest_repository_policy_has_empty_managed_bindings():
    manifest_path = Path(__file__).resolve().parents[1] / "openclaw-agents.manifest.json"

    manifest = openclaw_sync.load_manifest(manifest_path)

    assert manifest.bindings == ()


def test_default_openclaw_config_path_points_to_retained_data_openclaw_tree():
    assert openclaw_sync.OPENCLAW_CONFIG == openclaw_sync.WORKSPACE_ROOT.parent / ".openclaw" / "openclaw.json"


def test_reconcile_replaces_accidental_route_with_empty_bindings_and_preserves_unrelated_state(tmp_path):
    workspace = tmp_path / "workspaces" / "libby"
    agent_dir = tmp_path / "agents" / "libby" / "agent"
    config_path = tmp_path / "openclaw.json"
    manifest_path = tmp_path / "openclaw-agents.manifest.json"
    secret_value = "SYNTHETIC_SECRET_TOKEN"

    (workspace / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("ok\n", encoding="utf-8")
    (workspace / ".openclaw").mkdir(parents=True, exist_ok=True)

    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir.parent / "sessions").mkdir(parents=True, exist_ok=True)

    manifest_payload = {
        "version": 1,
        "safe_plugins": {"allow": ["telegram"], "disable": ["nostr"]},
        "model_profiles": {"classification": {"model": "anthropic/claude-sonnet-4-6"}},
        "bindings": [],
        "agents": [
            {
                "id": "libby",
                "name": "Libby",
                "workspace": str(workspace),
                "agent_dir": str(agent_dir),
                "model_profile": "classification",
                "identity_name": "Libby",
                "tools": {"profile": "full", "allow": ["group:runtime", "group:sessions"]},
                "agent_to_agent": False,
                "required_workspace_files": ["AGENTS.md"],
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")

    expected_agent_record = {
        "id": "libby",
        "name": "Libby",
        "workspace": str(workspace),
        "agentDir": str(agent_dir),
        "model": "anthropic/claude-sonnet-4-6",
        "identity": {"name": "Libby"},
        "tools": {"profile": "full", "allow": ["group:runtime", "group:sessions"]},
    }
    (agent_dir / "agent.json").write_text(
        json.dumps(
            {
                "id": "libby",
                "name": "Libby",
                "identityName": "Libby",
                "model": "anthropic/claude-sonnet-4-6",
                "workspace": str(workspace),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / ".openclaw" / "workspace-state.json").write_text(
        json.dumps({"version": 1, "setupCompletedAt": "2026-08-10T00:00:00Z"}, indent=2) + "\n",
        encoding="utf-8",
    )

    config_payload = {
        "plugins": {
            "allow": ["telegram"],
            "entries": {
                "nostr": {"enabled": False},
            },
        },
        "bindings": [
            {
                "type": "route",
                "agentId": "libby",
                "match": {"channel": "telegram", "accountId": "7643191699"},
            }
        ],
        "agents": {"list": [expected_agent_record]},
        "tools": {"agentToAgent": {"allow": []}},
        "telegram": {"token": secret_value},
        "unrelated": {"keep": {"depth": [1, 2, 3]}},
    }
    config_path.write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")

    audit_before = openclaw_sync.audit_openclaw_registration(manifest_path=manifest_path, config_path=config_path)
    assert audit_before["bindings"] == {
        "expected_count": 0,
        "actual_count": 1,
        "ok": False,
        "expected": [],
        "actual": [
            {
                "type": "route",
                "agentId": "libby",
                "match": {"channel": "telegram", "accountId": "7643191699"},
            }
        ],
    }

    reconcile = openclaw_sync.reconcile_openclaw_registration(manifest_path=manifest_path, config_path=config_path)
    assert any(
        op["target"] == "bindings" and op["action"] == "replaced" and op["expected_count"] == 0
        for op in reconcile["operations"]
    )
    assert reconcile["postcheck"]["bindings"]["ok"] is True
    assert reconcile["postcheck"]["bindings"]["actual_count"] == 0

    reconciled_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert reconciled_config["bindings"] == []
    assert reconciled_config["unrelated"] == config_payload["unrelated"]
    assert reconciled_config["telegram"]["token"] == secret_value

    assert secret_value not in json.dumps(audit_before)
    assert secret_value not in json.dumps(reconcile)
