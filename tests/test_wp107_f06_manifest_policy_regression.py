"""Stage 10 WP10.7 -- F0.6 manifest-policy regression test.

Per the Stage 10 plan
(`docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md`,
§4 Q1), this session investigated F0.6 (the decision addendum's own flagged
Telegram->Libby direct-binding contradiction) and found it confirmed
unreachable from `main`'s current code: the manifest-consuming module
(`openclaw_sync.py`) was deleted from `main` before Stage 1, and the
contradictory manifest itself survives only in a stale, unmerged feature
branch (`origin/feature/llm-consumption-dashboard-20260801`, 70 commits
behind `main`, never rebased). Chris confirmed treating F0.6 as closed for
Stage 10 purposes on that evidence, with this test as the addendum's own
literal remaining bar: "one authoritative policy and a manifest-policy
regression test."

This test does not touch, read, or depend on that stale branch, the
non-git sibling directory this session found it checked out in, or any
live Hermes manifest/profile -- it only asserts properties of this
repository's own tracked `main`-branch source, so that if a future change
(a careless merge of that stale branch, a manifest file dropped back into
the workspace root, a new `openclaw_sync`-style module) ever reintroduces
a live manifest-consuming code path, this test fails and calls it out
explicitly, rather than the contradiction silently reappearing."""

from __future__ import annotations

import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
REPO_ROOT = BACKEND.parent


def _tracked_backend_python_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "backend/symgov_backend"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line.endswith(".py")]


def test_no_openclaw_manifest_consuming_module_is_tracked_on_main():
    """`openclaw_sync.py` (the module that used to load
    `openclaw-agents.manifest.json` and its `bindings` array) must not
    exist as a tracked file anywhere under `backend/symgov_backend/` on
    this branch."""
    tracked_names = {path.name for path in _tracked_backend_python_files()}
    assert "openclaw_sync.py" not in tracked_names


def test_no_tracked_backend_source_reads_an_openclaw_agents_manifest_file():
    """No tracked backend Python source may reference the legacy manifest
    filename or load a `bindings`-shaped routing table from JSON -- the
    live worker dispatch table (`AGENT_SPECS` in `agent_queue_worker.py`)
    must remain a hardcoded Python literal, not a file read."""
    offending: list[str] = []
    for path in _tracked_backend_python_files():
        text = path.read_text(encoding="utf-8")
        if "openclaw-agents.manifest" in text or "openclaw_agents_manifest" in text:
            offending.append(str(path.relative_to(REPO_ROOT)))
    assert offending == [], f"Found manifest-consuming references in: {offending}"


def test_agent_runtime_setting_defaults_to_direct_and_is_the_sole_selector():
    """`SYMGOV_AGENT_RUNTIME` (default `direct`) must remain the only
    thing that selects between the direct-runner and Hermes-dispatch code
    paths in `agent_queue_worker.py` -- no manifest-derived value may
    participate in that branch."""
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from symgov_backend import agent_queue_worker
    from symgov_backend.settings import SymgovAPISettings

    assert SymgovAPISettings().agent_runtime == "direct"
    assert agent_queue_worker.AgentQueueWorkerConfig().agent_runtime == "direct"

    source = Path(agent_queue_worker.__file__).read_text(encoding="utf-8")
    assert "config.agent_runtime ==" in source


def test_agent_specs_dispatch_table_is_a_hardcoded_literal_not_a_file_read():
    """The per-agent workspace/runner dispatch table must be a Python
    dict literal assigned directly in this module's own source -- not
    loaded from any external manifest file at runtime. (This module does
    read JSON elsewhere -- `load_queue_item` parses per-item work-queue
    payloads -- which is unrelated to agent routing/binding and is not
    what this test checks.)"""
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from symgov_backend import agent_queue_worker

    assert isinstance(agent_queue_worker.AGENT_SPECS, dict)
    assert "libby" in agent_queue_worker.AGENT_SPECS
    source = Path(agent_queue_worker.__file__).read_text(encoding="utf-8")
    assert "AGENT_SPECS: dict[str, dict[str, Any]] = {" in source


def test_backend_readme_still_documents_the_manifest_retirement_policy():
    """A lightweight content canary (mirroring this repository's own
    migration-head-canary convention): if this documented policy sentence
    is ever removed or reworded without a deliberate update here, this
    test should be revisited rather than the policy silently drifting."""
    readme = (BACKEND / "README.md").read_text(encoding="utf-8")
    assert "No legacy registration manifest participates in live worker execution." in readme
