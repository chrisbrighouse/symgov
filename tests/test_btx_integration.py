import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FIXTURE_ROOT = REPO_ROOT / "integrations" / "btx" / "SymGov_BTX_Integration_Handoff" / "fixtures"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.services.btx_converter import BtxConversionError, convert_btx
from symgov_backend.agent_queue_worker import AgentQueueWorkerConfig, process_scott_downstream
from symgov_backend.catalog_taxonomy import CATALOG_CATEGORY_ORDER, normalize_catalog_category


def load_runner(name):
    path = REPO_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_converter_extracts_fixture_into_three_portable_assets_per_symbol(tmp_path):
    manifest = convert_btx(FIXTURE_ROOT / "Doors.btx", tmp_path)

    expected = json.loads((FIXTURE_ROOT / "expected_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tool_set_title"] == expected["tool_set_title"]
    assert manifest["successful_symbol_count"] == 5
    assert manifest["failed_symbol_count"] == 0
    assert [symbol["subject"] for symbol in manifest["symbols"]] == [symbol["subject"] for symbol in expected["symbols"]]
    for symbol in manifest["symbols"]:
        assert symbol["width_points"] > 0
        assert symbol["height_points"] > 0
        for format_name in ("svg", "dxf", "png"):
            derivative = tmp_path / symbol[format_name]
            assert derivative.exists() and derivative.stat().st_size > 0
    assert (tmp_path / "Single_Door.svg").read_text(encoding="utf-8").startswith("<svg")
    assert "AC1015" in (tmp_path / "Single_Door.dxf").read_text(encoding="ascii")


def test_converter_accepts_zip_with_exactly_one_btx(tmp_path):
    archive = tmp_path / "doors.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(FIXTURE_ROOT / "Doors.btx", "library/Doors.btx")
    manifest = convert_btx(archive, tmp_path / "out")
    assert manifest["source_filename"] == "Doors.btx"
    assert manifest["successful_symbol_count"] == 5


def test_converter_keeps_each_duplicate_subject_derivative_distinct(tmp_path):
    import re
    import xml.etree.ElementTree as ET
    import zlib

    root = ET.parse(FIXTURE_ROOT / "Doors.btx").getroot()
    for item in root.findall("ToolChestItem")[:2]:
        raw = zlib.decompress(bytes.fromhex(item.findtext("Raw"))).decode("latin-1")
        raw = re.sub(r"/Subj\([^)]*\)", "/Subj(Duplicate Door)", raw, count=1)
        item.find("Raw").text = zlib.compress(raw.encode("latin-1")).hex()
    duplicate_fixture = tmp_path / "duplicate-subjects.btx"
    ET.ElementTree(root).write(duplicate_fixture, encoding="utf-8", xml_declaration=True)

    manifest = convert_btx(duplicate_fixture, tmp_path / "out")

    for format_name in ("svg", "dxf", "png"):
        names = [symbol[format_name] for symbol in manifest["symbols"] if symbol.get(format_name)]
        assert len(names) == len(set(names))
        assert all((tmp_path / "out" / name).is_file() for name in names)


def test_converter_rejects_entity_declarations(tmp_path):
    unsafe = tmp_path / "unsafe.btx"
    unsafe.write_text('<!DOCTYPE x [<!ENTITY value "bad">]><BluebeamRevuToolSet/>', encoding="utf-8")
    with pytest.raises(BtxConversionError, match="DTD"):
        convert_btx(unsafe, tmp_path / "out")


def test_scott_routes_btx_to_vlad_and_tracy(tmp_path):
    scott = load_runner("run_scott_intake.py")
    btx = tmp_path / "Doors.btx"
    shutil.copy(FIXTURE_ROOT / "Doors.btx", btx)
    artifact = scott.run_intake_task({
        "queue_item_id": "aqi-scott-btx-test",
        "submission_kind": "contributor_submission",
        "source_ref": "external-submission-btx-test",
        "submitted_by": "Tester <tester@example.test>",
        "raw_input_path": str(btx),
        "declared_format": "btx",
        "candidate_symbol_id": "DOORS",
        "contributor_declaration": "I can submit this test library.",
    })
    assert artifact["decision"] == "accepted"
    assert "btx_library_expansion_candidate" in artifact["eligibility_flags"]
    assert artifact["routing_recommendation"]["route_to_agents"] == ["vlad", "tracy"]


def test_vlad_expands_btx_library_into_isolated_children(tmp_path):
    vlad = load_runner("run_vlad_validation.py")
    btx = tmp_path / "Doors.btx"
    shutil.copy(FIXTURE_ROOT / "Doors.btx", btx)
    artifact = vlad.run_validation_task({
        "queue_item_id": "aqi-vlad-btx-test",
        "source_type": "intake_record",
        "source_id": "intake-btx-test",
        "asset_path": str(btx),
        "asset_format": "btx",
        "raw_object_key": "external-submissions/test/Doors.btx",
        "runtime_root": str(tmp_path / "runtime"),
    })
    library = artifact["normalized_technical_metadata"]["btx_library"]
    assert artifact["decision"] == "escalate"
    assert artifact["review_recommendation"]["current_stage"] == "raster_split_review"
    assert len(library["children"]) == 5
    first = library["children"][0]
    assert first["child_symbol_id"].startswith("btx:")
    assert {asset["format"] for asset in first["assets"]} == {"svg", "dxf", "png"}
    assert any(item["artifact_type"] == "btx_library_manifest" for item in artifact["additional_artifacts"])


def test_vlad_btx_conversion_creates_reviewable_preview_children(tmp_path):
    vlad = load_runner("run_vlad_validation.py")
    btx = tmp_path / "Doors.btx"
    shutil.copy(FIXTURE_ROOT / "Doors.btx", btx)

    artifact = vlad.run_validation_task({
        "queue_item_id": "aqi-vlad-btx-review-test",
        "source_type": "intake_record",
        "source_id": "intake-btx-review-test",
        "asset_path": str(btx),
        "asset_format": "btx",
        "raw_object_key": "external-submissions/test/Doors.btx",
        "runtime_root": str(tmp_path / "runtime"),
    })

    metadata = artifact["normalized_technical_metadata"]
    assert artifact["decision"] == "escalate"
    assert artifact["review_recommendation"]["current_stage"] == "raster_split_review"
    assert len(metadata["derivative_manifest"]["children"]) == 5
    first = metadata["derivative_manifest"]["children"][0]
    assert first["proposed_symbol_name"] == "Single Door"
    assert first["file_name"].endswith(".png")
    assert first["attachment_object_key"].endswith("Single_Door.png")
    assert first["visual_assets"]["preview"]["format"] == "png"
    assert {asset["format"] for asset in first["visual_assets"]["derivatives"]} == {"svg", "dxf", "png"}
    assert first["source_btx_sha256"]
    assert isinstance(first["btx_ordinal"], int)
    assert first["btx_subject"] == "Single Door"
    assert metadata["visual_assets"]["preview"]["format"] == "png"
    assert {asset["format"] for asset in metadata["visual_assets"]["derivatives"]} == {"svg", "dxf", "png"}


def test_vlad_btx_conversion_emits_structured_auditable_trace(tmp_path):
    vlad = load_runner("run_vlad_validation.py")
    btx = tmp_path / "Doors.btx"
    shutil.copy(FIXTURE_ROOT / "Doors.btx", btx)

    artifact = vlad.run_validation_task({
        "queue_item_id": "aqi-vlad-btx-trace-test",
        "source_type": "intake_record",
        "source_id": "intake-btx-trace-test",
        "asset_path": str(btx),
        "asset_format": "btx",
        "original_filename": "01-Doors.btx",
        "raw_object_key": "external-submissions/test/01-Doors.btx",
        "runtime_root": str(tmp_path / "runtime"),
    })

    trace = artifact["normalized_technical_metadata"]["btx_conversion_trace"]
    assert trace["queue_item_id"] == "aqi-vlad-btx-trace-test"
    assert trace["source_sha256"]
    assert trace["source_filename"] == "01-Doors.btx"
    assert trace["source_object_key"] == "external-submissions/test/01-Doors.btx"
    assert trace["btx_version"] == "1"
    assert trace["tool_set_title"] == "Door Symbols"
    assert trace["total_symbol_count"] == 5
    assert trace["successful_symbol_count"] == 5
    assert trace["failed_symbol_count"] == 0
    assert Path(trace["output_directory"]).is_dir()
    assert len(trace["symbols"]) == 5
    first = trace["symbols"][0]
    assert {asset["format"] for asset in first["assets"]} == {"svg", "dxf", "png"}
    assert all(asset["file_name"] and asset["sha256"] and asset["size_bytes"] > 0 and asset["object_key"] for asset in first["assets"])
    assert any(entry["check"] == "btx_conversion" and entry["status"] == "passed" for entry in artifact["evidence_trace"])



def test_vlad_btx_persistence_boundary_diagnostics_identify_upload_failure(tmp_path, monkeypatch):
    vlad = load_runner("run_vlad_validation.py")
    btx = tmp_path / "Doors.btx"
    shutil.copy(FIXTURE_ROOT / "Doors.btx", btx)

    runtime_root = tmp_path / "runtime"
    queue_item_path = runtime_root / "agent_queue_items" / "aqi-vlad-btx-persistence-diag.json"
    queue_item_path.parent.mkdir(parents=True)
    queue_item = {
        "id": "aqi-vlad-btx-persistence-diag",
        "agent_id": "vlad",
        "source_type": "intake_record",
        "source_id": "intake-btx-persistence-diag",
        "status": "queued",
        "priority": "medium",
        "payload_json": {
            "asset_path": str(btx),
            "asset_format": "btx",
            "original_filename": "01-Doors.btx",
            "raw_object_key": "external-submissions/test/01-Doors.btx",
            "candidate_symbol_id": "DOORS",
            "candidate_title": "Doors",
        },
    }
    queue_item_path.write_text(json.dumps(queue_item), encoding="utf-8")

    class FakeBridge:
        def __init__(self, env_file=None):
            self.env_file = env_file
            self.created = []
            self.uploads = []

        def create_attachment(self, **kwargs):
            self.created.append(kwargs)
            return {
                "id": f"att-{len(self.created)}",
                "object_key": kwargs["object_key"],
                "filename": kwargs["filename"],
            }

        def upload_file(self, **kwargs):
            self.uploads.append(kwargs)
            if kwargs["object_key"].endswith(".svg"):
                raise RuntimeError("synthetic upload outage")
            return {"status": "uploaded", "object_key": kwargs["object_key"]}

        def persist_agent_execution(self, **kwargs):
            raise AssertionError("persistence should abort before durable report write")

    monkeypatch.setattr(vlad, "RuntimePersistenceBridge", FakeBridge)
    monkeypatch.setattr(vlad, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    with pytest.raises(RuntimeError, match=r"persistence boundary failure \[upload_completed\]") as excinfo:
        vlad.process_queue_item(
            queue_item_path,
            runtime_root,
            persist_db=True,
            db_env_file="/tmp/db.env",
            storage_env_file="/tmp/storage.env",
        )

    message = str(excinfo.value)
    assert "correlation=" in message
    assert "queue=aqi-vlad-btx-persistence-diag" in message
    assert "report=vr-aqi-vlad-btx-persistence-diag" in message
    assert "expected_derivatives=" in message and "actual_derivatives=" in message
    assert "expected_children=" in message and "actual_children=" in message


def test_tracked_worker_builds_btx_vlad_and_tracy_contracts(tmp_path):
    intake = {
        "id": "ir-btx-test", "intake_status": "accepted", "eligibility_status": "eligible",
        "source_ref": "external-submission-btx-test", "raw_object_key": "external/Doors.btx",
        "normalized_submission_json": {"declared_format": "btx", "raw_input_path": "/tmp/Doors.btx", "original_filename": "Doors.btx"},
        "routing_recommendation_json": {"route_to_agents": ["vlad", "tracy"]},
    }
    intake_path = tmp_path / "intake.json"
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    result = process_scott_downstream(
        {"intake_record_path": str(intake_path)},
        AgentQueueWorkerConfig(runtime_roots={"vlad": tmp_path / "vlad", "tracy": tmp_path / "tracy"}),
    )
    vlad = json.loads(Path(result["created"]["vlad_queue_item_path"]).read_text(encoding="utf-8"))
    tracy = json.loads(Path(result["created"]["tracy_queue_item_path"]).read_text(encoding="utf-8"))
    assert vlad["payload_json"]["expected_checks"] == ["integrity", "btx_library_expansion"]
    assert tracy["payload_json"]["provenance_scope"] == "library_and_extracted_symbols"


def test_btx_door_child_has_authoritative_architectural_classification_hints(tmp_path):
    vlad = load_runner("run_vlad_validation.py")
    libby = load_runner("run_libby_classification.py")
    child = {
        "child_symbol_id": "btx:test:0", "candidate_title": "Single Door", "ordinal": 0,
        "source_btx_sha256": "test-sha", "assets": [{"format": "png", "path": str(tmp_path / "door.png")}],
    }
    queue_item = vlad.build_btx_libby_queue_item({"id": "vlad-btx", "priority": "medium"}, {"asset_path": "Doors.btx"}, child, "20260710T140000Z")

    payload = queue_item["payload_json"]
    artifact = libby.infer_classification(payload)

    assert payload["btx_subject"] == "Single Door"
    assert payload["classification_hints"] == {"category": "Doors", "discipline": "Architectural"}
    assert artifact["category"] == "Doors"
    assert artifact["discipline"] == "Architectural"
    assert any(entry["check"] == "btx_subject_architectural_match" for entry in artifact["evidence_trace"])


def test_doors_is_a_canonical_catalog_category():
    assert "Doors" in CATALOG_CATEGORY_ORDER
    assert normalize_catalog_category("door") == ["Doors"]
    assert normalize_catalog_category("Doors") == ["Doors"]
