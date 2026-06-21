from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACY_RUNNER = REPO_ROOT / "scripts" / "run_tracy_provenance.py"
REPO_BACKEND_ROOT = REPO_ROOT / "backend"


def load_tracy_runner():
    # Simulate a standalone runner process without invalidating already-imported
    # repo modules used by other tests. Only clear Symgov modules that came from a
    # non-repo path; deleting repo modules mid-suite breaks monkeypatch targets in
    # tests that imported functions during collection.
    for module_name, module in list(sys.modules.items()):
        if module_name == "symgov_backend" or module_name.startswith("symgov_backend."):
            module_file = getattr(module, "__file__", None)
            if module_file is None:
                continue
            try:
                pathlib.Path(module_file).resolve().relative_to(REPO_BACKEND_ROOT.resolve())
            except ValueError:
                del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location("symgov_tracy_runner_under_test", TRACY_RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_imports_runtime_bridge_from_repo_backend(monkeypatch):
    monkeypatch.delenv("SYMGOV_BACKEND_ROOT", raising=False)

    tracy = load_tracy_runner()

    runtime_module = sys.modules[tracy.RuntimePersistenceBridge.__module__]
    assert runtime_module.__file__ is not None
    runtime_path = pathlib.Path(runtime_module.__file__).resolve()
    assert tracy.BACKEND_ROOT == REPO_BACKEND_ROOT.resolve()
    assert REPO_BACKEND_ROOT.resolve() in runtime_path.parents


def test_package_code_only_ambiguous_rights_do_not_create_provenance_review_stop_card():
    tracy = load_tracy_runner()

    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-firealarms-0001",
            "intake_record_id": "intake-firealarms-0001",
            "source_ref": "FireAlarms package FA-0001",
            "submitted_by": "Scott",
            "contributor_declaration": "FireAlarms package code FA-0001",
            "standards_source_refs": [],
            "evidence_links": [],
            "rights_documents": [],
        }
    )

    assert artifact["decision"] == "escalate"
    assert artifact["rights_status"] == "unknown"
    assert artifact["review_recommendation"] is None


def test_ambiguous_rights_still_feed_libby_classification_without_review_case():
    tracy = load_tracy_runner()
    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-firealarms-0002",
            "intake_record_id": "intake-firealarms-0002",
            "source_ref": "FireAlarms package FA-0002",
            "submitted_by": "Scott",
            "contributor_declaration": "FireAlarms package code FA-0002",
        }
    )

    libby_item = tracy.build_libby_queue_item(
        queue_item={
            "id": "aqi-tracy-firealarms-0002",
            "priority": "medium",
            "payload_json": {
                "candidate_symbol_id": "FA-0002",
                "candidate_title": "Fire alarm symbol",
                "declared_format": "dxf",
                "submission_batch_id": "firealarms",
            },
        },
        task={"intake_record_id": "intake-firealarms-0002"},
        artifact=artifact,
        provenance_assessment_id="pa-firealarms-0002",
        created_review_case=None,
        timestamp="20260617T000000Z",
    )

    assert libby_item["agent_id"] == "libby"
    assert libby_item["status"] == "queued"
    assert libby_item["payload_json"]["rights_status"] == "unknown"
    assert libby_item["payload_json"]["review_case_id"] is None
    assert libby_item["payload_json"]["current_stage"] is None


def test_restricted_rights_recommend_daisy_coordination_before_rights_review_queue():
    tracy = load_tracy_runner()
    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-restricted-0001",
            "intake_record_id": "intake-restricted-0001",
            "source_ref": "contractor-library",
            "submitted_by": "Tester",
            "contributor_declaration": "Third-party licensed symbol; no redistribution allowed.",
            "standards_source_refs": ["contractor-library"],
        }
    )

    assert artifact["rights_status"] == "restricted"
    assert artifact["review_recommendation"] == {
        "current_stage": "provenance_rights_review",
        "escalation_level": "high",
        "detail": "Tracy flagged rights disposition restricted with high risk for human review.",
        "coordination_step": "daisy_rights_review_coordination",
        "review_queue_family": "review_coordination",
        "review_queue_label": "Daisy Rights Review Coordination",
    }

    daisy_item = tracy.build_daisy_rights_coordination_queue_item(
        queue_item={
            "id": "aqi-tracy-restricted-0001",
            "priority": "high",
            "payload_json": {
                "candidate_symbol_id": "0001-1",
                "candidate_title": "Restricted symbol",
                "source_notes": "https://example.test/restricted-symbol",
            },
        },
        task={"source_ref": "contractor-library"},
        artifact=artifact,
        provenance_assessment_id="pa-restricted-0001",
        created_review_case={
            "id": "review-restricted-0001",
            "current_stage": "provenance_rights_review",
            "source_entity_type": "provenance_assessment",
            "source_entity_id": "pa-restricted-0001",
            "escalation_level": "high",
        },
        timestamp="20260619T000000Z",
    )

    assert daisy_item["agent_id"] == "daisy"
    assert daisy_item["source_type"] == "provenance_rights_coordination"
    assert daisy_item["payload_json"]["review_queue_family"] == "review_coordination"
    assert daisy_item["payload_json"]["coordination_step"] == "daisy_rights_review_coordination"
    assert daisy_item["payload_json"]["target_review_queue_family"] == "rights_review"
    assert daisy_item["payload_json"]["rights_status"] == "restricted"
    assert daisy_item["payload_json"]["review_case_id"] == "review-restricted-0001"
    assert daisy_item["payload_json"]["tracy_provenance_assessment_id"] == "pa-restricted-0001"


def test_ambiguous_rights_emit_canonical_warning_and_non_blocking_processing_state():
    tracy = load_tracy_runner()

    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-canonical-0001",
            "intake_record_id": "intake-canonical-0001",
            "source_ref": "package-code-only",
            "submitted_by": "Scott",
            "contributor_declaration": "Package code only",
            "standards_source_refs": [],
            "evidence_links": [],
            "rights_documents": [],
        }
    )

    assert artifact["rights_disposition"] == "unknown_warning"
    assert artifact["processing_outcome"] == "review_required"
    assert tracy.queue_escalation_reason_for_artifact(artifact) is None


def test_restricted_rights_emit_canonical_blocking_states():
    tracy = load_tracy_runner()

    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-canonical-0002",
            "intake_record_id": "intake-canonical-0002",
            "source_ref": "contractor-library",
            "submitted_by": "Tester",
            "contributor_declaration": "Third-party licensed symbol; no redistribution allowed.",
            "standards_source_refs": ["contractor-library"],
        }
    )

    assert artifact["rights_disposition"] == "restricted"
    assert artifact["processing_outcome"] == "failed"
    assert tracy.queue_escalation_reason_for_artifact(artifact) == "provenance_rights_review_required"


def test_non_blocking_rights_get_libby_gate_review_case_recommendation():
    tracy = load_tracy_runner()

    recommendation = tracy.review_case_recommendation_for_libby_handoff(
        {
            "processing_outcome": "review_required",
            "risk_level": "medium",
        }
    )

    assert recommendation == {
        "current_stage": "libby_disposition_review",
        "escalation_level": "medium",
        "detail": "Tracy completed non-blocking provenance checks and routed through Libby before human classification review.",
    }


def test_process_queue_item_creates_libby_gate_review_case_when_provenance_is_non_blocking(monkeypatch, tmp_path):
    tracy = load_tracy_runner()

    queue_dir = tmp_path / "runtime" / "agent_queue_items"
    queue_dir.mkdir(parents=True)
    queue_item_path = queue_dir / "aqi-tracy-test-nonblocking.json"
    queue_item_path.write_text(
        json.dumps(
            {
                "id": "aqi-tracy-test-nonblocking",
                "agent_id": "tracy",
                "source_type": "intake_record",
                "source_id": "ir-test-nonblocking",
                "status": "queued",
                "priority": "medium",
                "payload_json": {
                    "intake_record_id": "ir-test-nonblocking",
                    "candidate_symbol_id": "ELEC-LIGHTING-TEST-001",
                    "candidate_title": "Lighting Test Symbol",
                    "declared_format": "dxf",
                    "raw_object_key": "external-submissions/test/lighting.zip/members/0001/Lighting_Test_Symbol.dxf",
                    "submission_batch_id": "subext-test",
                },
            }
        ),
        encoding="utf-8",
    )

    artifact = {
        "schema_version": "tracy-local-contract-0.2.0",
        "queue_item_id": "aqi-tracy-test-nonblocking",
        "intake_record_id": "ir-test-nonblocking",
        "decision": "escalate",
        "rights_status": "unknown",
        "rights_disposition": "unknown_warning",
        "processing_outcome": "review_required",
        "risk_level": "medium",
        "confidence": 0.42,
        "reviewer_summary": "Package-code-only provenance warning; continue to Libby.",
        "evidence": {"standards_source_refs": []},
        "recommended_actions": ["route_to_libby"],
        "defects": [],
        "evidence_trace": [],
        "escalation_target": "libby",
        "review_recommendation": None,
    }

    monkeypatch.setattr(tracy, "run_provenance_task", lambda task: artifact)
    monkeypatch.setattr(tracy, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    written_payloads = []

    def fake_write_json(path, payload):
        written_payloads.append((str(path), payload))

    monkeypatch.setattr(tracy, "write_json", fake_write_json)

    class FakeBridge:
        last_instance = None

        def __init__(self, env_file=None):
            self.env_file = env_file
            self.created_review_cases = []
            FakeBridge.last_instance = self

        def persist_agent_execution(self, **kwargs):
            return {"status": "persisted"}

        def create_review_case(self, **kwargs):
            self.created_review_cases.append(kwargs)
            return {
                "id": "review-libby-gate-0001",
                "source_entity_type": kwargs["source_entity_type"],
                "source_entity_id": kwargs["source_entity_id"],
                "current_stage": kwargs["current_stage"],
                "escalation_level": kwargs["escalation_level"],
            }

        def upsert_agent_queue_item(self, queue_item):
            return {"status": "upserted", "id": queue_item["id"]}

    monkeypatch.setattr(tracy, "RuntimePersistenceBridge", FakeBridge)

    result = tracy.process_queue_item(
        queue_item_path,
        tmp_path / "runtime",
        persist_db=True,
        db_env_file=tmp_path / "db.env",
    )

    assert result["additional_db_records"]["review_case"]["id"] == "review-libby-gate-0001"
    assert result["additional_db_records"]["review_case"]["current_stage"] == "libby_disposition_review"

    assert FakeBridge.last_instance is not None
    assert FakeBridge.last_instance.created_review_cases[0]["current_stage"] == "libby_disposition_review"
    assert FakeBridge.last_instance.created_review_cases[0]["escalation_level"] == "medium"

    libby_handoff_payloads = [
        payload for path, payload in written_payloads if "/workspaces/libby/runtime/agent_queue_items/" in path
    ]
    assert len(libby_handoff_payloads) == 1
    libby_payload = libby_handoff_payloads[0]["payload_json"]
    assert libby_payload["review_case_id"] == "review-libby-gate-0001"
    assert libby_payload["current_stage"] == "libby_disposition_review"


def test_source_notes_with_restricted_library_terms_are_blocking_rights():
    tracy = load_tracy_runner()

    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-source-notes-restricted",
            "intake_record_id": "intake-source-notes-restricted",
            "source_ref": "manufacturer-cad-library",
            "submitted_by": "Scott",
            "contributor_declaration": "Package sourced from manufacturer CAD library for reference only.",
            "source_notes": "Downloaded from a third-party manufacturer CAD library; licence terms are reference-only and do not grant redistribution.",
            "standards_source_refs": ["manufacturer-cad-library"],
        }
    )

    assert artifact["rights_disposition"] == "restricted"
    assert artifact["processing_outcome"] == "failed"
    assert artifact["risk_level"] == "high"
    assert any(defect["code"] == "TRACY-RIGHTS-003" for defect in artifact["defects"])


def test_internal_original_submission_with_source_refs_is_cleared():
    tracy = load_tracy_runner()

    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-internal-clear",
            "intake_record_id": "intake-internal-clear",
            "source_ref": "internal-original-submission",
            "submitted_by": "Symgov",
            "contributor_declaration": "Original company-authored symbol owned by Symgov for unrestricted internal catalogue reuse.",
            "source_notes": "Contributor confirms this is original authored work, not copied from a third-party CAD library.",
            "standards_source_refs": ["internal-symbol-library"],
        }
    )

    assert artifact["rights_disposition"] == "cleared"
    assert artifact["processing_outcome"] == "pass"
    assert artifact["risk_level"] == "low"
    assert artifact["evidence"]["source_context"]["source_classification"] == "contributor_original"


def test_tracy_worker_spec_uses_repo_managed_runner():
    backend_root = REPO_ROOT / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from symgov_backend.agent_queue_worker import AGENT_SPECS

    assert AGENT_SPECS["tracy"]["runner_path"] == pathlib.Path("/data/symgov/scripts/run_tracy_provenance.py")


def test_process_queue_item_does_not_create_libby_handoff_for_blocking_rights(monkeypatch, tmp_path):
    tracy = load_tracy_runner()
    queue_dir = tmp_path / "runtime" / "agent_queue_items"
    queue_dir.mkdir(parents=True)
    queue_item_path = queue_dir / "aqi-tracy-test-blocking.json"
    queue_item_path.write_text(
        json.dumps(
            {
                "id": "aqi-tracy-test-blocking",
                "agent_id": "tracy",
                "source_type": "intake_record",
                "source_id": "ir-test-blocking",
                "status": "queued",
                "priority": "high",
                "payload_json": {"intake_record_id": "ir-test-blocking", "candidate_symbol_id": "BLOCKED"},
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "schema_version": "tracy-local-contract-0.2.0",
        "queue_item_id": "aqi-tracy-test-blocking",
        "intake_record_id": "ir-test-blocking",
        "decision": "fail",
        "rights_status": "restricted",
        "rights_disposition": "restricted",
        "processing_outcome": "failed",
        "risk_level": "high",
        "confidence": 0.9,
        "reviewer_summary": "Restricted rights must stop before Libby classification.",
        "evidence": {"standards_source_refs": []},
        "recommended_actions": [],
        "defects": [],
        "evidence_trace": [],
        "escalation_target": "human_reviewer",
        "review_recommendation": {
            "current_stage": "provenance_rights_review",
            "escalation_level": "high",
            "detail": "blocking",
            "coordination_step": "daisy_rights_review_coordination",
            "review_queue_family": "review_coordination",
            "review_queue_label": "Daisy Rights Review Coordination",
        },
    }
    monkeypatch.setattr(tracy, "run_provenance_task", lambda task: artifact)
    monkeypatch.setattr(tracy, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})
    written_payloads = []
    monkeypatch.setattr(tracy, "write_json", lambda path, payload: written_payloads.append((str(path), payload)))

    class FakeBridge:
        def __init__(self, env_file=None):
            pass
        def persist_agent_execution(self, **kwargs):
            return {"status": "persisted"}
        def create_review_case(self, **kwargs):
            return {"id": "review-blocking", "source_entity_type": kwargs["source_entity_type"], "source_entity_id": kwargs["source_entity_id"], "current_stage": kwargs["current_stage"], "escalation_level": kwargs["escalation_level"]}
        def upsert_agent_queue_item(self, queue_item):
            return {"status": "upserted", "id": queue_item["id"]}
    monkeypatch.setattr(tracy, "RuntimePersistenceBridge", FakeBridge)

    result = tracy.process_queue_item(queue_item_path, tmp_path / "runtime", persist_db=True)

    assert result["downstream_created"]["daisy_rights_coordination_queue_item_path"]
    assert result["downstream_created"]["libby_queue_item_path"] is None
    assert not [payload for path, payload in written_payloads if "/workspaces/libby/runtime/agent_queue_items/" in path]


def test_process_queue_item_file_only_blocking_rights_does_not_raise(monkeypatch, tmp_path):
    tracy = load_tracy_runner()
    queue_dir = tmp_path / "runtime" / "agent_queue_items"
    queue_dir.mkdir(parents=True)
    queue_item_path = queue_dir / "aqi-tracy-file-only-blocking.json"
    queue_item_path.write_text(json.dumps({"id": "aqi-tracy-file-only-blocking", "agent_id": "tracy", "source_type": "intake_record", "source_id": "ir-file-only", "status": "queued", "priority": "high", "payload_json": {"intake_record_id": "ir-file-only"}}), encoding="utf-8")
    artifact = {"schema_version": "tracy-local-contract-0.2.0", "queue_item_id": "aqi-tracy-file-only-blocking", "intake_record_id": "ir-file-only", "decision": "fail", "rights_status": "restricted", "rights_disposition": "restricted", "processing_outcome": "failed", "risk_level": "high", "confidence": 0.9, "reviewer_summary": "Restricted.", "evidence": {"standards_source_refs": []}, "recommended_actions": [], "defects": [], "evidence_trace": [], "escalation_target": "human_reviewer", "review_recommendation": {"current_stage": "provenance_rights_review", "escalation_level": "high", "detail": "blocking"}}
    monkeypatch.setattr(tracy, "run_provenance_task", lambda task: artifact)
    monkeypatch.setattr(tracy, "send_agent_status_update", lambda *args, **kwargs: {"status": "skipped"})

    result = tracy.process_queue_item(queue_item_path, tmp_path / "runtime", persist_db=False)

    assert result["queue_item_status"] == "failed"
    assert result["downstream_created"]["libby_queue_item_path"] is None


def test_negated_restricted_source_context_does_not_block_original_work():
    tracy = load_tracy_runner()
    artifact = tracy.run_provenance_task(
        {
            "queue_item_id": "aqi-tracy-negated-restricted",
            "intake_record_id": "intake-negated-restricted",
            "source_ref": "internal-original-submission",
            "submitted_by": "Symgov",
            "contributor_declaration": "Original company-authored symbol owned by Symgov for unrestricted internal catalogue reuse.",
            "source_notes": "Contributor confirms this is original authored work and not copied from a manufacturer CAD library; not reference-only.",
            "standards_source_refs": ["internal-symbol-library"],
        }
    )
    assert artifact["rights_disposition"] == "cleared"
    assert artifact["evidence"]["source_context"]["restricted_markers_detected"] == []
