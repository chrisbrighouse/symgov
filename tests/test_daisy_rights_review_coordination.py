from __future__ import annotations

import importlib.util
import pathlib


DAISY_RUNNER = pathlib.Path("/data/.openclaw/workspaces/daisy/run_daisy_coordination.py")


def load_daisy_runner():
    spec = importlib.util.spec_from_file_location("symgov_daisy_runner_under_test", DAISY_RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daisy_emits_rights_review_card_from_coordination_item():
    daisy = load_daisy_runner()

    source_item = {
        "id": "aqi-daisy-coord-pa-1",
        "agent_id": "daisy",
        "source_type": "provenance_rights_coordination",
        "source_id": "review-case-1",
        "status": "queued",
        "priority": "high",
        "payload_json": {
            "review_case_id": "review-case-1",
            "current_stage": "provenance_rights_review",
            "source_entity_type": "provenance_assessment",
            "source_entity_id": "pa-1",
            "escalation_level": "high",
            "validation_status": None,
            "rights_status": "restricted",
            "risk_level": "high",
            "reviewer_pool": ["rights_reviewer", "qa_admin"],
            "coordination_step": "daisy_rights_review_coordination",
            "target_review_queue_family": "rights_review",
            "target_review_queue_label": "Provenance/Rights Review",
            "candidate_symbol_id": "0001-1",
            "candidate_title": "Restricted symbol",
            "tracy_provenance_assessment_id": "pa-1",
            "tracy_rights_summary": "Rights status is restricted.",
            "tracy_recommended_actions": ["Block publication routing."],
            "tracy_defects": [{"code": "TRACY-RIGHTS-001"}],
        },
    }

    task = daisy.queue_item_payload_to_task(source_item)
    artifact = daisy.run_coordination_task(task)
    rights_item = daisy.build_rights_review_queue_item(source_item, task, artifact, "20260619T000000Z")

    assert artifact["downstream_review_queue"] == {
        "queue_family": "rights_review",
        "queue_label": "Provenance/Rights Review",
        "source_type": "provenance_rights_review",
    }
    assert rights_item["agent_id"] == "daisy"
    assert rights_item["source_type"] == "provenance_rights_review"
    assert rights_item["payload_json"]["review_queue_family"] == "rights_review"
    assert rights_item["payload_json"]["review_queue_label"] == "Provenance/Rights Review"
    assert rights_item["payload_json"]["coordination_source_queue_item_id"] == "aqi-daisy-coord-pa-1"
    assert rights_item["payload_json"]["tracy_provenance_assessment_id"] == "pa-1"


def test_daisy_process_leaves_emitted_rights_review_card_queued(tmp_path):
    daisy = load_daisy_runner()
    runtime_root = tmp_path / "runtime"
    queue_dir = runtime_root / "agent_queue_items"
    queue_dir.mkdir(parents=True)
    queue_path = queue_dir / "coord.json"
    source_item = {
        "id": "aqi-daisy-coord-pa-2",
        "agent_id": "daisy",
        "source_type": "provenance_rights_coordination",
        "source_id": "11111111-1111-1111-1111-111111111111",
        "status": "queued",
        "priority": "high",
        "payload_json": {
            "review_case_id": "11111111-1111-1111-1111-111111111111",
            "current_stage": "provenance_rights_review",
            "source_entity_type": "provenance_assessment",
            "source_entity_id": "22222222-2222-2222-2222-222222222222",
            "escalation_level": "high",
            "rights_status": "restricted",
            "reviewer_pool": ["rights_reviewer"],
            "coordination_step": "daisy_rights_review_coordination",
            "target_review_queue_family": "rights_review",
            "target_review_queue_label": "Provenance/Rights Review",
            "tracy_provenance_assessment_id": "22222222-2222-2222-2222-222222222222",
        },
    }
    daisy.write_json(queue_path, source_item)

    result = daisy.process_queue_item(queue_path, runtime_root)

    rights_path = result["downstream_created"]["rights_review_queue_item_path"]
    assert rights_path
    emitted = daisy.json.loads(pathlib.Path(rights_path).read_text(encoding="utf-8"))
    original = daisy.json.loads(queue_path.read_text(encoding="utf-8"))
    assert original["status"] == "completed"
    assert emitted["status"] == "queued"
    assert emitted["payload_json"]["review_queue_family"] == "rights_review"
