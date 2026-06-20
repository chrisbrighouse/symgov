from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = Path(__file__).resolve().parents[1]
SCOTT_RUNNER_PATH = REPO_ROOT / "scripts" / "run_scott_intake.py"
SCOTT_DOWNSTREAM_PATH = Path("/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scott_runner = load_module("scott_runner_zip_phase2", SCOTT_RUNNER_PATH)
scott_downstream = load_module("scott_downstream_zip_phase2", SCOTT_DOWNSTREAM_PATH)


def build_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def minimal_svg(label: str) -> bytes:
    return f'<svg xmlns="http://www.w3.org/2000/svg"><title>{label}</title><path d="M0 0h10v10z"/></svg>'.encode("utf-8")


def minimal_dxf() -> bytes:
    return b"0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n11\n10\n21\n10\n0\nENDSEC\n0\nEOF\n"


class FakeExternalSubmissionBridge:
    def __init__(self):
        self.queue_items = []
        self.attachments = []
        self.uploads = []
        self.audit_events = []

    def seed_agent_definitions(self):
        return None

    def upsert_external_identity(self, **kwargs):
        return {"id": "submitter-test", **kwargs}

    def create_attachment(self, **kwargs):
        attachment = {"id": f"att-{len(self.attachments) + 1}", **kwargs}
        self.attachments.append(attachment)
        return attachment

    def upload_object_bytes(self, **kwargs):
        self.uploads.append(kwargs)
        return {"status": "uploaded", "object_key": kwargs["object_key"]}

    def upsert_agent_queue_item(self, queue_item):
        self.queue_items.append(queue_item)
        return {"status": "upserted", "id": queue_item["id"]}

    def create_audit_event(self, **kwargs):
        self.audit_events.append(kwargs)
        return {"id": "audit-test"}


def test_external_submission_guesses_and_queues_zip_package(tmp_path):
    from symgov_backend.services.external_submissions import ExternalSubmissionService, guess_declared_format

    assert guess_declared_format("symbols.ZIP") == "zip"

    zip_bytes_io = io.BytesIO()
    with zipfile.ZipFile(zip_bytes_io, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pump.svg", minimal_svg("pump"))

    bridge = FakeExternalSubmissionBridge()
    service = ExternalSubmissionService(
        bridge=bridge,
        pin="1234",
        db_env_file=tmp_path / "db.env",
        storage_env_file=tmp_path / "storage.env",
        scott_runtime_root=tmp_path / "scott-runtime",
        upload_root=tmp_path / "uploads",
    )

    service.submit(
        {
            "pin": "1234",
            "submitter_name": "Tester",
            "submitter_email": "tester@example.test",
            "overall_description": "Package of engineering symbols.",
            "files": [
                {
                    "name": "plant-symbols.zip",
                    "content_type": "application/zip",
                    "content_base64": base64.b64encode(zip_bytes_io.getvalue()).decode("ascii"),
                }
            ],
        }
    )

    payload = bridge.queue_items[0]["payload_json"]
    assert payload["declared_format"] == "zip"
    assert payload["original_filename"] == "plant-symbols.zip"


def test_scott_zip_expands_supported_members_preserving_duplicate_filenames(tmp_path):
    zip_path = build_zip(
        tmp_path / "symbols.zip",
        {
            "area-a/pump.svg": minimal_svg("pump a"),
            "area-b/pump.svg": minimal_svg("pump b"),
            "cad/valve.dxf": minimal_dxf(),
            "metadata/manifest.json": json.dumps({"source": "test"}).encode("utf-8"),
            "ignored/readme.txt": b"operator notes",
        },
    )

    artifact = scott_runner.run_intake_task(
        {
            "queue_item_id": "aqi-scott-zip-test",
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-zip-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "PLANT-SYMBOLS",
            "contributor_declaration": "I can submit this test ZIP.",
            "attachment_id": "att-zip",
            "raw_object_key": "external-submissions/batch/plant-symbols.zip",
            "package_workspace_root": str(tmp_path / "packages"),
        }
    )

    assert artifact["decision"] == "accepted"
    assert artifact["eligibility_status"] == "eligible"
    assert artifact["routing_recommendation"]["route_to_agents"] == []
    assert "zip_package_expanded" in artifact["eligibility_flags"]
    manifest = artifact["package_manifest"]
    accepted = [member for member in manifest["members"] if member["status"] == "accepted"]
    skipped = [member for member in manifest["members"] if member["status"] == "skipped"]
    assert [member["original_path"] for member in accepted] == [
        "area-a/pump.svg",
        "area-b/pump.svg",
        "cad/valve.dxf",
        "metadata/manifest.json",
    ]
    assert len({member["member_id"] for member in accepted}) == 4
    assert len({member["safe_stored_path"] for member in accepted}) == 4
    assert accepted[0]["filename"] == accepted[1]["filename"] == "pump.svg"
    assert accepted[0]["safe_stored_path"] != accepted[1]["safe_stored_path"]
    assert skipped[0]["original_path"] == "ignored/readme.txt"
    assert skipped[0]["reason_codes"] == ["unsupported_member_format"]

    child_tasks = artifact["package_child_tasks"]
    assert len(child_tasks) == 4
    assert child_tasks[0]["declared_format"] == "svg"
    assert child_tasks[1]["declared_format"] == "svg"
    assert child_tasks[2]["declared_format"] == "dxf"
    assert child_tasks[3]["declared_format"] == "json"
    for index, task in enumerate(child_tasks, start=1):
        assert task["source_package_id"] == manifest["source_package_id"]
        assert task["source_package_attachment_id"] == "att-zip"
        assert task["source_package_object_key"] == "external-submissions/batch/plant-symbols.zip"
        assert task["package_member"]["member_index"] == index
        assert Path(task["raw_input_path"]).exists()
        assert task["attachment_ids"] == ["att-zip"]
        assert task["visual_assets"]["source_assets"][0]["role"] == "package_member_source"


def test_scott_zip_keeps_same_named_dxf_raster_pairs_as_one_symbol_candidate(tmp_path):
    zip_path = build_zip(
        tmp_path / "fire-alarms.zip",
        {
            "symbols/Elec_FireAlarms_Sounder.dxf": minimal_dxf(),
            "symbols/Elec_FireAlarms_Sounder.jpg": b"fake jpeg preview",
            "sheets/FireAlarm_Legend_Sheet.jpg": b"fake multi-symbol sheet",
        },
    )

    artifact = scott_runner.run_intake_task(
        {
            "queue_item_id": "aqi-scott-fire-zip-test",
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-fire-zip-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "FIRE-ALARMS",
            "contributor_declaration": "I can submit this test ZIP.",
            "attachment_id": "att-fire-zip",
            "raw_object_key": "external-submissions/batch/fire-alarms.zip",
            "package_workspace_root": str(tmp_path / "packages"),
        }
    )

    assert artifact["decision"] == "accepted"
    child_tasks = artifact["package_child_tasks"]
    assert [task["original_filename"] for task in child_tasks] == [
        "Elec_FireAlarms_Sounder.dxf",
        "FireAlarm_Legend_Sheet.jpg",
    ]

    paired_task = child_tasks[0]
    assert paired_task["declared_format"] == "dxf"
    assert paired_task["candidate_title"] == "Electrical FireAlarms Sounder"
    assert paired_task["filename_inference"]["inferred_name"] == "Electrical FireAlarms Sounder"
    assert paired_task["filename_inference"]["discipline_hint"] == "Electrical"
    assert paired_task["package_symbol_grouping"] == "paired_dxf_raster_symbol"
    assert paired_task["package_member_relationship"] == "primary_with_companion"
    assert paired_task["package_member"]["relationship"] == "primary"
    assert paired_task["companion_files"][0]["file_name"] == "Elec_FireAlarms_Sounder.jpg"
    assert paired_task["companion_files"][0]["role"] == "package_member_companion"
    assert [asset["role"] for asset in paired_task["visual_assets"]["source_assets"]] == [
        "package_member_source",
        "package_member_companion",
    ]

    companion_member = next(
        member for member in artifact["package_manifest"]["members"] if member["filename"] == "Elec_FireAlarms_Sounder.jpg"
    )
    assert companion_member["relationship"] == "companion"
    assert companion_member["downstream_role"] == "companion_to_primary_symbol"

    sheet_task = child_tasks[1]
    assert sheet_task["declared_format"] == "jpeg"
    assert sheet_task["companion_files"] == []
    assert sheet_task["package_symbol_grouping"] == "standalone_package_symbol_file"
    assert sheet_task["package_member_relationship"] == "standalone_symbol_file"
    assert sheet_task["package_member"]["relationship"] == "standalone_symbol_file"


def test_scott_marks_fire_alarm_raster_package_members_as_standalone_symbols(tmp_path):
    zip_path = build_zip(
        tmp_path / "FireAlarms.zip",
        {
            "Elec_FireAlarms_Detector_Heat_RateOfRise.jpg": b"fake heat rate-of-rise jpeg",
            "Elec_FireAlarms_Sounder_Beacon-alt.jpg": b"fake sounder beacon jpeg",
        },
    )

    artifact = scott_runner.run_intake_task(
        {
            "queue_item_id": "aqi-scott-fire-raster-zip-test",
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-fire-raster-zip-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "FIRE-ALARMS",
            "contributor_declaration": "I can submit this test ZIP.",
            "attachment_id": "att-fire-zip",
            "raw_object_key": "external-submissions/batch/FireAlarms.zip",
            "package_workspace_root": str(tmp_path / "packages"),
        }
    )

    assert artifact["decision"] == "accepted"
    child_tasks = artifact["package_child_tasks"]
    assert [task["original_filename"] for task in child_tasks] == [
        "Elec_FireAlarms_Detector_Heat_RateOfRise.jpg",
        "Elec_FireAlarms_Sounder_Beacon-alt.jpg",
    ]
    heat_task = child_tasks[0]
    assert heat_task["declared_format"] == "jpeg"
    assert heat_task["candidate_title"] == "Electrical FireAlarms Detector Heat RateOfRise"
    assert heat_task["package_symbol_grouping"] == "standalone_package_symbol_file"
    assert heat_task["package_member_relationship"] == "standalone_symbol_file"
    assert heat_task["package_member"]["relationship"] == "standalone_symbol_file"
    assert heat_task["companion_files"] == []


def test_scott_zip_rejects_path_traversal_without_extracting(tmp_path):
    zip_path = build_zip(tmp_path / "evil.zip", {"../escape.svg": minimal_svg("evil")})

    artifact = scott_runner.run_intake_task(
        {
            "queue_item_id": "aqi-scott-zip-evil-test",
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-zip-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "EVIL-ZIP",
            "contributor_declaration": "I can submit this test ZIP.",
            "package_workspace_root": str(tmp_path / "packages"),
        }
    )

    assert artifact["decision"] == "rejected"
    assert artifact["eligibility_status"] == "ineligible"
    assert "unsafe_zip_member" in artifact["eligibility_flags"]
    assert artifact["package_child_tasks"] == []
    assert artifact["package_manifest"]["members"][0]["status"] == "rejected"
    assert "path_traversal" in artifact["package_manifest"]["members"][0]["reason_codes"]


def test_scott_process_queue_uploads_extracted_zip_members_to_declared_object_keys(tmp_path, monkeypatch):
    zip_path = build_zip(
        tmp_path / "symbols.zip",
        {"a/pump.svg": minimal_svg("pump"), "b/valve.dxf": minimal_dxf()},
    )
    runtime_root = tmp_path / "runtime"
    queue_item_path = runtime_root / "agent_queue_items" / "aqi-scott-zip-upload-test.json"
    queue_item_path.parent.mkdir(parents=True)
    queue_item = {
        "id": "aqi-scott-zip-upload-test",
        "agent_id": "scott",
        "source_type": "raw_submission",
        "source_id": "src-zip-upload-test",
        "status": "queued",
        "priority": "medium",
        "payload_json": {
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-zip-upload-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "PLANT-SYMBOLS",
            "contributor_declaration": "I can submit this test ZIP.",
            "raw_object_key": "external-submissions/batch/symbols.zip",
            "package_workspace_root": str(tmp_path / "packages"),
        },
    }
    queue_item_path.write_text(json.dumps(queue_item), encoding="utf-8")
    monkeypatch.setattr(scott_runner, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    class FakeBridge:
        instances = []

        def __init__(self, env_file=None):
            self.env_file = env_file
            self.uploads = []
            FakeBridge.instances.append(self)

        def upload_file(self, **kwargs):
            self.uploads.append(kwargs)
            return {"status": "uploaded", "object_key": kwargs["object_key"]}

        def persist_agent_execution(self, **kwargs):
            return {"status": "persisted"}

    monkeypatch.setattr(scott_runner, "RuntimePersistenceBridge", FakeBridge)

    result = scott_runner.process_queue_item(
        queue_item_path,
        runtime_root,
        persist_db=True,
        db_env_file=tmp_path / "db.env",
        storage_env_file=tmp_path / "storage.env",
    )

    bridge = FakeBridge.instances[0]
    uploaded_keys = [upload["object_key"] for upload in bridge.uploads]
    accepted_members = [member for member in result["artifact"]["package_manifest"]["members"] if member["status"] == "accepted"]
    assert uploaded_keys == [
        f"external-submissions/batch/symbols.zip/members/{accepted_members[0]['member_id']}/pump.svg",
        f"external-submissions/batch/symbols.zip/members/{accepted_members[1]['member_id']}/valve.dxf",
    ]
    assert all(upload["env_file"] == tmp_path / "storage.env" for upload in bridge.uploads)
    assert all(Path(upload["path"]).exists() for upload in bridge.uploads)
    assert result["zip_member_uploads"]["uploaded_count"] == 2


def test_scott_process_queue_writes_child_scott_queue_items_for_zip(tmp_path, monkeypatch):
    zip_path = build_zip(tmp_path / "symbols.zip", {"a/pump.svg": minimal_svg("pump"), "b/valve.dxf": minimal_dxf()})
    runtime_root = tmp_path / "runtime"
    queue_item_path = runtime_root / "agent_queue_items" / "aqi-scott-zip-test.json"
    queue_item_path.parent.mkdir(parents=True)
    queue_item = {
        "id": "aqi-scott-zip-test",
        "agent_id": "scott",
        "source_type": "raw_submission",
        "source_id": "src-zip-test",
        "status": "queued",
        "priority": "medium",
        "payload_json": {
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-zip-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(zip_path),
            "declared_format": "zip",
            "candidate_symbol_id": "PLANT-SYMBOLS",
            "contributor_declaration": "I can submit this test ZIP.",
            "package_workspace_root": str(tmp_path / "packages"),
        },
    }
    queue_item_path.write_text(json.dumps(queue_item), encoding="utf-8")
    monkeypatch.setattr(scott_runner, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    result = scott_runner.process_queue_item(queue_item_path, runtime_root, persist_db=False)

    child_paths = [Path(path) for path in result["package_child_queue_item_paths"]]
    assert len(child_paths) == 2
    for child_path in child_paths:
        assert child_path.exists()
        child = json.loads(child_path.read_text(encoding="utf-8"))
        assert child["agent_id"] == "scott"
        assert child["source_type"] == "zip_package_member"
        assert child["source_id"].startswith("pkg-aqi-scott-zip-test-")
        assert child["payload_json"]["source_package_id"] == result["artifact"]["package_manifest"]["source_package_id"]
    assert result["queue_item_status"] == "completed"


def test_downstream_queue_items_preserve_zip_package_member_provenance(tmp_path):
    member_path = tmp_path / "pump.svg"
    member_path.write_text("<svg/>", encoding="utf-8")
    intake_record = {
        "id": "intake-zip-member-test",
        "intake_status": "accepted",
        "eligibility_status": "eligible",
        "source_ref": "external-submission-zip-test",
        "submitter": "Tester <tester@example.test>",
        "routing_recommendation_json": {"route_to_agents": ["vlad", "tracy"], "priority": "medium"},
        "normalized_submission_json": {
            "raw_input_path": str(member_path),
            "declared_format": "svg",
            "candidate_symbol_id": "PUMP-001",
            "candidate_title": "Pump",
            "original_filename": "pump.svg",
            "source_package_id": "pkg-aqi-scott-zip-test-abcdef123456",
            "source_package_attachment_id": "att-zip",
            "source_package_object_key": "external-submissions/batch/symbols.zip",
            "source_package_sha256": "abc123",
            "source_package_queue_item_id": "aqi-scott-zip-test",
            "package_member": {
                "member_id": "0001-a-pump-svg",
                "member_index": 1,
                "original_path": "a/pump.svg",
                "filename": "pump.svg",
                "declared_format": "svg",
            },
            "package_member_relationship": "primary_with_companion",
            "package_symbol_grouping": "paired_dxf_raster_symbol",
            "companion_files": [
                {
                    "file_name": "pump.jpg",
                    "format": "jpeg",
                    "role": "package_member_companion",
                    "package_member_id": "0002-a-pump-jpg",
                    "original_path": "a/pump.jpg",
                }
            ],
            "visual_assets": {"source_assets": [{"filename": "pump.svg"}, {"filename": "pump.jpg"}]},
        },
    }

    vlad_item = scott_downstream.build_vlad_queue_item(intake_record, "20260613T000000Z")
    tracy_item = scott_downstream.build_tracy_queue_item(intake_record, "20260613T000000Z")

    for item in (vlad_item, tracy_item):
        payload = item["payload_json"]
        assert payload["source_package_id"] == "pkg-aqi-scott-zip-test-abcdef123456"
        assert payload["source_package_attachment_id"] == "att-zip"
        assert payload["source_package_object_key"] == "external-submissions/batch/symbols.zip"
        assert payload["source_package_sha256"] == "abc123"
        assert payload["source_package_queue_item_id"] == "aqi-scott-zip-test"
        assert payload["package_member"]["original_path"] == "a/pump.svg"
        assert payload["package_member_relationship"] == "primary_with_companion"
        assert payload["package_symbol_grouping"] == "paired_dxf_raster_symbol"
        assert payload["companion_files"][0]["original_path"] == "a/pump.jpg"
        assert payload["visual_assets"]["source_assets"][1]["filename"] == "pump.jpg"


def test_downstream_does_not_request_raster_sheet_splitting_for_standalone_zip_symbol_jpeg(tmp_path):
    member_path = tmp_path / "Elec_FireAlarms_Detector_Heat_RateOfRise.jpg"
    member_path.write_bytes(b"fake jpeg")
    intake_record = {
        "id": "intake-fire-raster-member-test",
        "intake_status": "accepted",
        "eligibility_status": "eligible",
        "source_ref": "external-submission-fire-zip-test",
        "submitter": "Tester <tester@example.test>",
        "routing_recommendation_json": {"route_to_agents": ["vlad", "tracy"], "priority": "medium"},
        "normalized_submission_json": {
            "raw_input_path": str(member_path),
            "declared_format": "jpeg",
            "candidate_symbol_id": "ELEC-FIREALARMS-DETECTOR-HEAT-RATEOFRISE-001",
            "candidate_title": "Electrical FireAlarms Detector Heat Rate Of Rise",
            "original_filename": member_path.name,
            "source_package_id": "pkg-aqi-scott-fire-zip-test-abcdef123456",
            "package_member": {
                "member_id": "0001-elec-firealarms-detector-heat-rateofrise-jpg",
                "member_index": 1,
                "original_path": member_path.name,
                "filename": member_path.name,
                "declared_format": "jpeg",
                "relationship": "standalone_symbol_file",
            },
            "package_member_relationship": "standalone_symbol_file",
            "package_symbol_grouping": "standalone_package_symbol_file",
            "companion_files": [],
        },
    }

    vlad_item = scott_downstream.build_vlad_queue_item(intake_record, "20260613T000000Z")

    assert vlad_item["payload_json"]["asset_format"] == "jpeg"
    assert vlad_item["payload_json"]["expected_checks"] == ["integrity"]
    assert vlad_item["payload_json"]["package_symbol_grouping"] == "standalone_package_symbol_file"
