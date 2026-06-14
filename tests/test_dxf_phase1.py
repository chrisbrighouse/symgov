from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = Path(__file__).resolve().parents[1]
SCOTT_RUNNER_PATH = REPO_ROOT / "scripts" / "run_scott_intake.py"
SCOTT_DOWNSTREAM_PATH = Path("/data/.openclaw/workspaces/scott/enqueue_scott_downstream.py")
VLAD_RUNNER_PATH = REPO_ROOT / "scripts" / "run_vlad_validation.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scott_runner = load_module("scott_runner_dxf_phase1", SCOTT_RUNNER_PATH)
# Scott's direct-runner module prepends the legacy compatibility backend path while
# importing. Keep the test process pointed back at the repo backend so later tests
# do not accidentally import stale compatibility modules.
LEGACY_BACKEND_ROOT = str(Path("/data/.openclaw/workspace/symgov/backend"))
LEGACY_BACKEND_PREFIX = f"{LEGACY_BACKEND_ROOT}/"
for path_entry in list(sys.path):
    if path_entry == LEGACY_BACKEND_ROOT or path_entry.startswith(LEGACY_BACKEND_PREFIX):
        sys.path.remove(path_entry)
for module_name in list(sys.modules):
    if module_name == "symgov_backend" or module_name.startswith("symgov_backend."):
        del sys.modules[module_name]
if str(BACKEND_ROOT) in sys.path:
    sys.path.remove(str(BACKEND_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

scott_downstream = load_module("scott_downstream_dxf_phase1", SCOTT_DOWNSTREAM_PATH)
vlad_runner = load_module("vlad_runner_dxf_phase1", VLAD_RUNNER_PATH)
for path_entry in list(sys.path):
    if path_entry == LEGACY_BACKEND_ROOT or path_entry.startswith(LEGACY_BACKEND_PREFIX):
        sys.path.remove(path_entry)
for module_name in list(sys.modules):
    if module_name == "symgov_backend" or module_name.startswith("symgov_backend."):
        del sys.modules[module_name]
if str(BACKEND_ROOT) in sys.path:
    sys.path.remove(str(BACKEND_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))


def minimal_dxf() -> str:
    return "\n".join(
        [
            "0",
            "SECTION",
            "2",
            "HEADER",
            "9",
            "$ACADVER",
            "1",
            "AC1027",
            "0",
            "ENDSEC",
            "0",
            "SECTION",
            "2",
            "ENTITIES",
            "0",
            "LINE",
            "8",
            "0",
            "10",
            "0",
            "20",
            "0",
            "11",
            "10",
            "21",
            "10",
            "0",
            "CIRCLE",
            "8",
            "symbols",
            "10",
            "5",
            "20",
            "5",
            "40",
            "2",
            "0",
            "ENDSEC",
            "0",
            "EOF",
            "",
        ]
    )


def test_external_submission_guesses_dxf_format():
    from symgov_backend.services.external_submissions import guess_declared_format

    assert guess_declared_format("pump-symbol.dxf") == "dxf"
    assert guess_declared_format("PUMP-SYMBOL.DXF") == "dxf"


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


def test_external_submission_groups_same_stem_dxf_and_jpg_as_one_symbol(tmp_path):
    from symgov_backend.services.external_submissions import ExternalSubmissionService

    bridge = FakeExternalSubmissionBridge()
    service = ExternalSubmissionService(
        bridge=bridge,
        pin="1234",
        db_env_file=tmp_path / "db.env",
        storage_env_file=tmp_path / "storage.env",
        scott_runtime_root=tmp_path / "scott-runtime",
        upload_root=tmp_path / "uploads",
    )

    result = service.submit(
        {
            "pin": "1234",
            "submitter_name": "Tester",
            "submitter_email": "tester@example.test",
            "overall_description": "Two representations of one boiler flue symbol.",
            "files": [
                {
                    "name": "Boiler_Flue_Top.dxf",
                    "content_type": "application/dxf",
                    "content_base64": base64.b64encode(minimal_dxf().encode("utf-8")).decode("ascii"),
                },
                {
                    "name": "Boiler_Flue_Top.jpg",
                    "content_type": "image/jpeg",
                    "content_base64": base64.b64encode(b"fake-jpeg").decode("ascii"),
                },
            ],
        }
    )

    assert len(result["queueItems"]) == 1
    assert len(bridge.queue_items) == 1
    payload = bridge.queue_items[0]["payload_json"]
    assert payload["original_filename"] == "Boiler_Flue_Top.dxf"
    assert payload["declared_format"] == "dxf"
    assert payload["raw_object_key"].endswith("01-Boiler_Flue_Top.dxf")
    assert len(payload["attachment_ids"]) == 2
    assert payload["visual_assets"]["preview"]["object_key"].endswith("02-Boiler_Flue_Top.jpg")
    assert payload["visual_assets"]["preview"]["content_type"] == "image/jpeg"
    assert payload["visual_assets"]["source_assets"][0]["object_key"].endswith("01-Boiler_Flue_Top.dxf")


def test_scott_accepts_dxf_and_routes_to_vlad_and_tracy(tmp_path):
    dxf_path = tmp_path / "pump-symbol.dxf"
    dxf_path.write_text(minimal_dxf(), encoding="utf-8")

    artifact = scott_runner.run_intake_task(
        {
            "queue_item_id": "aqi-scott-dxf-test",
            "submission_kind": "contributor_submission",
            "source_ref": "external-submission-test",
            "submitted_by": "Tester <tester@example.test>",
            "raw_input_path": str(dxf_path),
            "declared_format": "dxf",
            "candidate_symbol_id": "PUMP-SYMBOL",
            "contributor_declaration": "I can submit this test DXF.",
            "attachment_ids": ["att-dxf", "att-jpg"],
            "visual_assets": {
                "preview": {
                    "object_key": "raw-submissions/batch/02-pump-symbol.jpg",
                    "filename": "pump-symbol.jpg",
                    "content_type": "image/jpeg",
                    "format": "jpeg",
                    "role": "companion_preview",
                },
                "source_assets": [
                    {
                        "object_key": "raw-submissions/batch/01-pump-symbol.dxf",
                        "filename": "pump-symbol.dxf",
                        "content_type": "application/dxf",
                        "format": "dxf",
                        "role": "source",
                    },
                    {
                        "object_key": "raw-submissions/batch/02-pump-symbol.jpg",
                        "filename": "pump-symbol.jpg",
                        "content_type": "image/jpeg",
                        "format": "jpeg",
                        "role": "source",
                    },
                ],
            },
            "companion_files": [
                {
                    "file_name": "pump-symbol.jpg",
                    "object_key": "raw-submissions/batch/02-pump-symbol.jpg",
                    "content_type": "image/jpeg",
                    "format": "jpeg",
                    "attachment_id": "att-jpg",
                }
            ],
        }
    )

    assert artifact["decision"] == "accepted"
    assert artifact["eligibility_status"] == "eligible"
    assert artifact["extracted_metadata"]["guessed_format"] == "dxf"
    assert artifact["normalized_submission"]["visual_assets"]["preview"]["object_key"].endswith(
        "02-pump-symbol.jpg"
    )
    assert len(artifact["normalized_submission"]["visual_assets"]["source_assets"]) == 2
    assert artifact["normalized_submission"]["companion_files"][0]["file_name"] == "pump-symbol.jpg"
    assert artifact["normalized_submission"]["attachment_ids"] == ["att-dxf", "att-jpg"]
    assert "dxf_validation_candidate" in artifact["eligibility_flags"]
    assert artifact["routing_recommendation"]["route_to_agents"] == ["vlad", "tracy"]
    assert "SCOTT-ROUTE-DXF" in artifact["routing_recommendation"]["reason_codes"]


def test_downstream_enqueue_builds_dxf_vlad_queue_item(tmp_path):
    dxf_path = tmp_path / "pump-symbol.dxf"
    dxf_path.write_text(minimal_dxf(), encoding="utf-8")
    intake_record = {
        "id": "intake-dxf-test",
        "intake_status": "accepted",
        "eligibility_status": "eligible",
        "routing_recommendation_json": {"route_to_agents": ["vlad", "tracy"], "priority": "medium"},
        "normalized_submission_json": {
            "raw_input_path": str(dxf_path),
            "declared_format": "dxf",
            "candidate_symbol_id": "PUMP-SYMBOL",
            "candidate_title": "Pump Symbol",
            "original_filename": "pump-symbol.dxf",
        },
    }

    item = scott_downstream.build_vlad_queue_item(intake_record, "20260613T000000Z")

    assert item["payload_json"]["asset_format"] == "dxf"
    assert item["payload_json"]["expected_checks"] == [
        "integrity",
        "dxf_parse",
        "dxf_metadata",
        "dxf_derivative",
    ]


def test_vlad_dxf_validation_generates_accessible_svg_derivative(tmp_path):
    dxf_path = tmp_path / "pump-symbol.dxf"
    dxf_path.write_text(minimal_dxf(), encoding="utf-8")
    runtime_root = tmp_path / "runtime"

    artifact = vlad_runner.run_validation_task(
        {
            "queue_item_id": "aqi-vlad-dxf-test",
            "source_type": "intake_record",
            "source_id": "intake-dxf-test",
            "asset_path": str(dxf_path),
            "asset_format": "dxf",
            "expected_checks": ["integrity", "dxf_parse", "dxf_metadata", "dxf_derivative"],
            "runtime_root": str(runtime_root),
            "raw_object_key": "raw-submissions/pump-symbol.dxf",
            "candidate_symbol_id": "PUMP-SYMBOL",
            "candidate_title": "Pump Symbol",
        }
    )

    assert artifact["decision"] == "pass"
    metadata = artifact["normalized_technical_metadata"]
    assert metadata["asset_format"] == "dxf"
    assert metadata["dxf_metadata"]["entity_counts"]["LINE"] == 1
    assert metadata["dxf_metadata"]["entity_counts"]["CIRCLE"] == 1
    derivative = metadata["dxf_derivative"]
    svg_path = Path(derivative["svg_path"])
    assert svg_path.exists()
    svg_text = svg_path.read_text(encoding="utf-8")
    assert "<title" in svg_text
    assert "<desc" in svg_text
    assert 'role="img"' in svg_text
    assert "aria-labelledby" in svg_text
    assert derivative["object_key"] == "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg"
    assert derivative["content_type"] == "image/svg+xml"
    assert derivative["role"] == "generated_preview"
    assert derivative["downloadable"] is False
    assert metadata["visual_assets"] == {
        "preview": {
            "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
            "filename": "pump-symbol.svg",
            "content_type": "image/svg+xml",
            "format": "svg",
            "role": "generated_preview",
            "downloadable": False,
        },
        "source_assets": [
            {
                "object_key": "raw-submissions/pump-symbol.dxf",
                "filename": "pump-symbol.dxf",
                "content_type": "application/dxf",
                "format": "dxf",
                "role": "source",
                "downloadable": True,
            }
        ],
        "derivatives": [
            {
                "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
                "filename": "pump-symbol.svg",
                "content_type": "image/svg+xml",
                "format": "svg",
                "role": "generated_preview",
                "downloadable": False,
            }
        ],
    }
    assert any(
        item["artifact_type"] == "dxf_derivative_manifest"
        for item in artifact["additional_artifacts"]
    )


class FakeDerivativeBridge:
    def __init__(self):
        self.attachments = []
        self.uploads = []

    def create_attachment(self, **kwargs):
        self.attachments.append(kwargs)
        return {
            "id": "att-dxf-preview",
            "object_key": kwargs["object_key"],
            "filename": kwargs["filename"],
        }

    def upload_file(self, **kwargs):
        self.uploads.append(kwargs)
        return {"status": "uploaded", "object_key": kwargs["object_key"]}


def test_vlad_persists_dxf_derivative_preview_to_storage(tmp_path):
    svg_path = tmp_path / "pump-symbol.svg"
    svg_path.write_text("<svg/>", encoding="utf-8")
    manifest = {
        "svg_path": str(svg_path),
        "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
        "filename": "pump-symbol.svg",
        "content_type": "image/svg+xml",
        "format": "svg",
        "role": "generated_preview",
        "downloadable": False,
        "visual_assets": {"derivatives": []},
    }
    bridge = FakeDerivativeBridge()

    attachment = vlad_runner.persist_dxf_derivative_assets(
        bridge,
        report_id="vr-dxf-test",
        derivative_manifest=manifest,
        storage_env_file="/tmp/storage.env",
    )

    assert bridge.attachments == [
        {
            "parent_type": "validation_report",
            "parent_id": "vr-dxf-test",
            "filename": "pump-symbol.svg",
            "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
            "content_type": "image/svg+xml",
            "size_bytes": svg_path.stat().st_size,
            "sha256": vlad_runner.sha256_file(svg_path),
        }
    ]
    assert bridge.uploads == [
        {
            "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
            "path": str(svg_path),
            "content_type": "image/svg+xml",
            "env_file": "/tmp/storage.env",
        }
    ]
    assert attachment == {
        "id": "att-dxf-preview",
        "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
        "filename": "pump-symbol.svg",
    }
    assert manifest["attachment_id"] == "att-dxf-preview"
    assert manifest["attachment_object_key"] == "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg"
    assert manifest["attachment_storage"] == {
        "status": "uploaded",
        "object_key": "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg",
    }


def test_vlad_process_queue_persists_dxf_derivative_before_report_payload(tmp_path, monkeypatch):
    dxf_path = tmp_path / "pump-symbol.dxf"
    dxf_path.write_text(minimal_dxf(), encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    queue_item_path = runtime_root / "agent_queue_items" / "aqi-vlad-dxf-test.json"
    queue_item_path.parent.mkdir(parents=True)
    queue_item = {
        "id": "aqi-vlad-dxf-test",
        "agent_id": "vlad",
        "source_type": "intake_record",
        "source_id": "intake-dxf-test",
        "status": "queued",
        "priority": "medium",
        "payload_json": {
            "asset_path": str(dxf_path),
            "asset_format": "dxf",
            "expected_checks": ["integrity", "dxf_parse", "dxf_metadata", "dxf_derivative"],
            "raw_object_key": "raw-submissions/pump-symbol.dxf",
            "candidate_symbol_id": "PUMP-SYMBOL",
            "candidate_title": "Pump Symbol",
        },
    }
    queue_item_path.write_text(json.dumps(queue_item), encoding="utf-8")

    class FakeProcessBridge(FakeDerivativeBridge):
        instances = []

        def __init__(self, env_file=None):
            super().__init__()
            self.env_file = env_file
            self.persisted_execution = None
            self.created_artifacts = []
            FakeProcessBridge.instances.append(self)

        def persist_agent_execution(self, **kwargs):
            self.persisted_execution = kwargs
            return {"status": "persisted"}

        def create_agent_output_artifact(self, **kwargs):
            self.created_artifacts.append(kwargs)
            return {"id": f"artifact-{len(self.created_artifacts)}"}

        def create_review_case(self, **kwargs):
            raise AssertionError("DXF pass should not open a review case")

        def create_control_exception(self, **kwargs):
            raise AssertionError("DXF pass should not create control exceptions")

    monkeypatch.setattr(vlad_runner, "RuntimePersistenceBridge", FakeProcessBridge)
    monkeypatch.setattr(vlad_runner, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    result = vlad_runner.process_queue_item(
        queue_item_path,
        runtime_root,
        persist_db=True,
        db_env_file="/tmp/db.env",
        storage_env_file="/tmp/storage.env",
    )

    assert result["db_persistence"] == {"status": "persisted"}
    assert len(FakeProcessBridge.instances) == 1
    bridge = FakeProcessBridge.instances[0]
    assert bridge.uploads[0]["object_key"] == "dxf-derivatives/aqi-vlad-dxf-test/pump-symbol.svg"
    persisted_report = bridge.persisted_execution["durable_record"]
    persisted_derivative = persisted_report["normalized_payload_json"]["dxf_derivative"]
    assert persisted_derivative["attachment_id"] == "att-dxf-preview"
    assert persisted_derivative["attachment_storage"]["status"] == "uploaded"
    assert bridge.created_artifacts[0]["payload_json"]["attachment_id"] == "att-dxf-preview"

    report_payload = json.loads(Path(result["validation_report_path"]).read_text(encoding="utf-8"))[
        "normalized_payload_json"
    ]
    assert report_payload["dxf_derivative"]["attachment_id"] == "att-dxf-preview"
    assert result["artifact"]["normalized_technical_metadata"]["visual_assets"]["preview"]["attachment_id"] == "att-dxf-preview"
