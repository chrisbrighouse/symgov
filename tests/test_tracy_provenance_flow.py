from __future__ import annotations

import importlib.util
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACY_RUNNER = REPO_ROOT / "scripts" / "run_tracy_provenance.py"
REPO_BACKEND_ROOT = REPO_ROOT / "backend"


def load_tracy_runner():
    # Simulate a standalone runner process: clear only Symgov project modules so the
    # import path configured by the runner decides which backend package is loaded.
    for module_name in list(sys.modules):
        if module_name == "symgov_backend" or module_name.startswith("symgov_backend."):
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
