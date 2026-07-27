from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from symgov_backend import publication_handoff
from symgov_backend.agent_queue_worker import AGENT_SPECS


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_RUNNER = REPOSITORY_ROOT / "scripts" / "run_rupert_publication.py"
REPOSITORY_RUNTIME = REPOSITORY_ROOT / "backend" / "symgov_backend" / "runtime.py"


def test_publication_handoff_uses_repository_owned_rupert_runner():
    assert publication_handoff.RUPERT_RUNNER.resolve() == REPOSITORY_RUNNER.resolve()


def test_agent_queue_worker_uses_repository_owned_rupert_runner():
    assert AGENT_SPECS["rupert"]["runner_path"].resolve() == REPOSITORY_RUNNER.resolve()


def test_repository_rupert_runner_imports_repository_runtime_module():
    probe = """
import json
import runpy
import sys
from pathlib import Path

namespace = runpy.run_path(sys.argv[1])
bridge = namespace["RuntimePersistenceBridge"]
runtime_module = sys.modules[bridge.__module__]
print(json.dumps({
    "backend_root": str(Path(namespace["BACKEND_ROOT"]).resolve()),
    "runtime_file": str(Path(runtime_module.__file__).resolve()),
}))
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(REPOSITORY_RUNNER)],
        cwd="/",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    resolved = json.loads(completed.stdout)
    assert Path(resolved["backend_root"]) == (REPOSITORY_ROOT / "backend").resolve()
    assert Path(resolved["runtime_file"]) == REPOSITORY_RUNTIME.resolve()
