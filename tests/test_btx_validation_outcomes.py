"""BTX validation must report conversion failures without publishing candidates."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_vlad():
    spec = importlib.util.spec_from_file_location("vlad_btx_outcomes", REPO_ROOT / "scripts/run_vlad_validation.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_queue(tmp_path, monkeypatch, symbols):
    vlad = load_vlad()
    source = tmp_path / "HVAC.btx"
    source.write_text("converter is mocked; outcomes are under test", encoding="utf-8")
    task = {
        "asset_path": str(source), "asset_format": "btx",
        "raw_object_key": "external-submissions/test/HVAC.btx",
        "original_filename": "HVAC.btx", "source_package_id": "package-test",
        "package_member": "library/HVAC.btx", "contributor_declaration": "Test permission",
        "source_notes": "Test provenance",
    }
    manifest = {
        "source_filename": source.name, "source_sha256": vlad.sha256_file(source),
        "btx_version": "1", "tool_set_title": "HVAC",
        "successful_symbol_count": sum(item["status"] != "failed" for item in symbols),
        "failed_symbol_count": sum(item["status"] == "failed" for item in symbols),
        "symbols": symbols,
    }

    def convert(_source, output):
        output.mkdir(parents=True)
        for symbol in symbols:
            for fmt in ("svg", "dxf", "png"):
                if symbol.get(fmt):
                    (output / symbol[fmt]).write_bytes(b"mock derivative")
        return copy.deepcopy(manifest)

    monkeypatch.setattr(vlad, "convert_btx", convert)
    monkeypatch.setattr(vlad, "send_agent_status_update", lambda *a, **k: {"status": "skipped"})
    calls = {name: [] for name in ("attachments", "uploads", "queues", "reviews", "reports", "artifacts")}

    class Bridge:
        def __init__(self, env_file=None):
            pass

        def create_attachment(self, **kwargs):
            calls["attachments"].append(kwargs)
            return {"id": "attachment-test", **kwargs}

        def upload_file(self, **kwargs):
            calls["uploads"].append(kwargs)
            return {"status": "mocked"}

        def persist_agent_execution(self, **kwargs):
            calls["reports"].append(kwargs)
            return {"status": "mocked"}

        def upsert_agent_queue_item(self, item):
            calls["queues"].append(item)
            return item

        def create_agent_output_artifact(self, **kwargs):
            calls["artifacts"].append(kwargs)
            return kwargs

        def create_review_case(self, **kwargs):
            calls["reviews"].append(kwargs)
            return {"id": "review-test", **kwargs}

    monkeypatch.setattr(vlad, "RuntimePersistenceBridge", Bridge)
    # Production's fixed Libby runtime must never be written by these tests.
    write_json = vlad.write_json
    downstream_writes = []

    def write_scratch(path, payload):
        path = Path(path)
        if path.is_relative_to(Path("/data/.openclaw/workspaces/libby/runtime")):
            downstream_writes.append(payload)
            path = tmp_path / "libby" / path.name
        assert path.resolve().is_relative_to(tmp_path.resolve())
        write_json(path, payload)

    monkeypatch.setattr(vlad, "write_json", write_scratch)
    runtime = tmp_path / "runtime"
    queue_path = runtime / "agent_queue_items" / "aqi-btx-outcomes.json"
    write_scratch(queue_path, {
        "id": "aqi-btx-outcomes", "agent_id": "vlad", "source_type": "intake_record",
        "source_id": "intake-test", "status": "queued", "priority": "medium", "payload_json": task,
    })
    result = vlad.process_queue_item(queue_path, runtime, persist_db=True)
    return result, calls, downstream_writes, manifest


def failed_symbol(ordinal, warnings=True):
    return {
        "ordinal": ordinal, "subject": f"HVAC {ordinal}", "status": "failed",
        "warnings": [{"code": "BTX-TEST-FAILURE", "detail": "Unsupported test appearance"}] if warnings else [],
    }


@pytest.mark.parametrize("symbols", [
    [failed_symbol(i) for i in range(30)],
    [],
    [failed_symbol(0, warnings=False)],
], ids=["zero-success-thirty-failed", "empty-manifest", "failed-without-warning"])
def test_unusable_btx_library_fails_without_downstream_children(tmp_path, monkeypatch, symbols):
    result, calls, downstream, manifest = run_queue(tmp_path, monkeypatch, symbols)
    artifact = result["artifact"]
    assert artifact["decision"] == "fail"
    assert artifact["defects"]
    assert all(defect["code"].startswith("VLAD-BTX-") for defect in artifact["defects"])
    assert any("0 usable" in defect["detail"] for defect in artifact["defects"])
    metadata = artifact["normalized_technical_metadata"]
    trace = metadata["btx_conversion_trace"]
    assert trace["successful_symbol_count"] == 0
    assert trace["failed_symbol_count"] == len(symbols)
    assert trace["failed_symbols"] == symbols
    assert metadata["btx_library"]["symbols"] == manifest["symbols"]
    assert metadata["btx_library"]["children"] == []
    assert metadata["derivative_manifest"]["children"] == []
    assert metadata["visual_assets"]["derivatives"] == []
    assert metadata["visual_assets"]["preview"] is None
    for check in ("btx_conversion", "btx_library_expansion"):
        assert [entry["status"] for entry in artifact["evidence_trace"] if entry["check"] == check] == ["failed"]
    for symbol in symbols:
        assert any(f"symbol {symbol['ordinal']}" in defect["detail"] for defect in artifact["defects"])
    assert [entry["status"] for entry in artifact["evidence_trace"]
            if entry["check"] == "btx_symbol_conversion"] == ["failed"] * len(symbols)
    assert artifact["review_recommendation"] is None
    assert calls["attachments"] == calls["uploads"] == calls["queues"] == calls["reviews"] == []
    assert downstream == result["downstream_queue_item_paths"] == []
    assert result["additional_db_records"]["persistence_boundary"]["expected_child_count"] == 0
    report = json.loads(Path(result["validation_report_path"]).read_text())
    assert report["validation_status"] == "fail"
    assert report["defect_count"] == len(artifact["defects"])


def test_partial_btx_escalates_only_successful_children_with_failure_evidence(tmp_path, monkeypatch):
    success = {
        "ordinal": 0, "subject": "Single Door", "internal_name": "door-test", "status": "success",
        "warnings": [], "svg": "door.svg", "dxf": "door.dxf", "png": "door.png",
    }
    failed = failed_symbol(1)
    result, calls, downstream, manifest = run_queue(tmp_path, monkeypatch, [success, failed])
    artifact = result["artifact"]
    assert artifact["decision"] == "escalate"
    assert artifact["escalation_target"] == "human_reviewer"
    assert artifact["review_recommendation"]["current_stage"] == "raster_split_review"
    assert calls["reviews"][0]["current_stage"] == "raster_split_review"
    metadata = artifact["normalized_technical_metadata"]
    trace = metadata["btx_conversion_trace"]
    assert trace["successful_symbol_count"] == trace["failed_symbol_count"] == 1
    assert trace["failed_symbols"] == [failed]
    for check in ("btx_conversion", "btx_library_expansion"):
        assert [entry["status"] for entry in artifact["evidence_trace"] if entry["check"] == check] == ["warning"]
    assert any(entry["check"] == "btx_symbol_conversion" and entry["status"] == "warning"
               and "symbol 1" in entry["detail"] and "Unsupported test appearance" in entry["detail"]
               for entry in artifact["evidence_trace"])
    assert len(metadata["btx_library"]["children"]) == len(metadata["derivative_manifest"]["children"]) == 1
    child = metadata["btx_library"]["children"][0]
    assert child["child_symbol_id"] == f"btx:{manifest['source_sha256']}:0"
    assert child["source_package_id"] == "package-test"
    assert child["package_member"] == "library/HVAC.btx"
    assert {asset["format"] for asset in child["assets"]} == {"svg", "dxf", "png"}
    assert all(asset["sha256"] and asset["size_bytes"] > 0 for asset in child["assets"])
    assert metadata["visual_assets"]["source_assets"][0]["object_key"] == "external-submissions/test/HVAC.btx"
    assert len(downstream) == len(calls["queues"]) == 1
    queued = calls["queues"][0]
    assert queued["status"] == "queued"
    assert queued["source_type"] == "btx_extracted_symbol"
    assert queued["payload_json"]["source_btx_sha256"] == manifest["source_sha256"]
    assert queued["payload_json"]["contributor_declaration"] == "Test permission"
    assert queued["payload_json"]["classification_hints"] == {"category": "Doors", "discipline": "Architectural"}
    assert artifact["control_exceptions"] == []
