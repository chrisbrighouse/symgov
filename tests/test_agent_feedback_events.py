from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from uuid import UUID
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.agent_feedback import (
    build_duplicate_decision_feedback_events,
    build_symbol_property_feedback_events,
)


class AgentFeedbackEventTests(unittest.TestCase):
    def test_property_updates_create_observational_libby_feedback_only_for_changed_fields(self) -> None:
        source_id = UUID("11111111-1111-1111-1111-111111111111")
        events = build_symbol_property_feedback_events(
            source_entity_type="review_symbol_property",
            source_entity_id=source_id,
            previous={"name": "Old Valve", "category": "symbol", "discipline": "general", "format": "png"},
            updated={"name": "Old Valve", "category": "Valves", "discipline": "Piping", "format": "png"},
            reviewer_name="Chris",
            reviewer_role="methods_lead",
            reason="Reviewer corrected taxonomy",
            evidence={"review_case_id": "case-1"},
        )

        self.assertEqual([event["feedback_type"] for event in events], ["metadata_category_corrected", "metadata_discipline_corrected"])
        self.assertEqual({event["agent_slug"] for event in events}, {"libby"})
        self.assertEqual(events[0]["source_entity_type"], "review_symbol_property")
        self.assertEqual(events[0]["source_entity_id"], source_id)
        self.assertEqual(events[0]["original_value"], {"field": "category", "value": "symbol"})
        self.assertEqual(events[0]["corrected_value"], {"field": "category", "value": "Valves"})
        self.assertEqual(events[0]["reviewer_name"], "Chris")
        self.assertIsNone(events[0]["applied_to_rules_at"])
        self.assertIsNone(events[0]["applied_to_prompt_version"])

    def test_name_corrections_are_observational_feedback_for_libby_and_vlad(self) -> None:
        source_id = UUID("22222222-2222-2222-2222-222222222222")
        events = build_symbol_property_feedback_events(
            source_entity_type="review_symbol_property",
            source_entity_id=source_id,
            previous={"name": "REGION-06", "category": "Pumps"},
            updated={"name": "Centrifugal Pump", "category": "Pumps"},
            reviewer_name="Chris",
            reviewer_role="methods_lead",
            reason=None,
            evidence={},
        )

        self.assertEqual(len(events), 2)
        self.assertEqual({event["agent_slug"] for event in events}, {"libby", "vlad"})
        self.assertEqual({event["feedback_type"] for event in events}, {"metadata_name_corrected"})
        self.assertTrue(all(event["original_value"] == {"field": "name", "value": "REGION-06"} for event in events))
        self.assertTrue(all(event["corrected_value"] == {"field": "name", "value": "Centrifugal Pump"} for event in events))

    def test_duplicate_decisions_create_feedback_for_rupert_and_libby(self) -> None:
        split_item = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), proposed_symbol_id="0003-12")
        events = build_duplicate_decision_feedback_events(
            split_item=split_item,
            action_code="duplicate",
            reviewer_name="Alfi COO smoke test",
            reviewer_role="operator",
            reason="Human confirmed the duplicate.",
            evidence={"matched_symbol_slug": "double-acting-cylinder"},
        )

        self.assertEqual(len(events), 2)
        self.assertEqual({event["agent_slug"] for event in events}, {"rupert", "libby"})
        self.assertEqual({event["feedback_type"] for event in events}, {"duplicate_confirmed"})
        self.assertTrue(all(event["source_entity_type"] == "review_split_item" for event in events))
        self.assertTrue(all(event["source_entity_id"] == split_item.id for event in events))
        self.assertTrue(all(event["original_value"]["duplicate_status"] == "duplicate_exception" for event in events))
        self.assertTrue(all(event["corrected_value"]["duplicate_outcome"] == "duplicate_confirmed" for event in events))
        self.assertTrue(all(event["evidence_json"]["matched_symbol_slug"] == "double-acting-cylinder" for event in events))

    def test_false_duplicate_override_creates_feedback_for_rupert_and_libby(self) -> None:
        split_item = SimpleNamespace(id=UUID("44444444-4444-4444-4444-444444444444"), proposed_symbol_id="0003-13")
        events = build_duplicate_decision_feedback_events(
            split_item=split_item,
            action_code="approve",
            reviewer_name="Reviewer",
            reviewer_role="methods_lead",
            reason="Visual match was a false positive.",
            evidence={},
        )

        self.assertEqual({event["agent_slug"] for event in events}, {"rupert", "libby"})
        self.assertEqual({event["feedback_type"] for event in events}, {"duplicate_false_positive"})
        self.assertTrue(all(event["corrected_value"]["duplicate_outcome"] == "false_duplicate" for event in events))


if __name__ == "__main__":
    unittest.main()
