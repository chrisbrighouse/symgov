from __future__ import annotations

import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_tracy_status_endpoint_and_schema_are_available():
    schema_source = (BACKEND_ROOT / "symgov_backend" / "schemas.py").read_text(encoding="utf-8")
    workspace_source = (BACKEND_ROOT / "symgov_backend" / "routes" / "workspace.py").read_text(encoding="utf-8")

    assert "class WorkspaceTracyStatusResponse" in schema_source
    assert '"/tracy/status"' in workspace_source
    assert "rightsDispositionCounts" in schema_source
    assert "assessmentsMissingReviewCases" in schema_source
    assert "assessmentsWithoutOpenReviewCases" in schema_source
    assert "runtimeQueueFiles" in schema_source
    assert "Tracy status could not be loaded." in workspace_source


def test_manage_symgov_exposes_runtime_archive_command():
    manage_source = (BACKEND_ROOT / "manage_symgov.py").read_text(encoding="utf-8")

    assert 'archive-agent-runtime-queue' in manage_source
    assert '--terminal-status' in manage_source
    assert 'archive_agent_runtime_queue' in manage_source


def test_runtime_ops_module_exposes_tracy_archive_and_backfill_helpers():
    import symgov_backend.tracy_operations as ops

    assert hasattr(ops, "archive_agent_runtime_queue")
    assert hasattr(ops, "find_provenance_libby_items_missing_review_cases")
    assert hasattr(ops, "backfill_provenance_libby_review_cases")
