from __future__ import annotations

import importlib.util
import pathlib


TRACY_RUNNER = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_tracy_provenance.py"


def load_tracy_runner():
    spec = importlib.util.spec_from_file_location("symgov_tracy_runner_under_test", TRACY_RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
