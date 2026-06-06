from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.publication_handoff import publication_duplicate_override_for_decision
from symgov_backend.routes.workspace import (
    OPEN_SPLIT_ITEM_STATUSES,
    is_open_split_item_status,
    is_terminal_split_item_status,
    split_item_status_after_handoff,
    split_item_status_group,
)


class DuplicateExceptionWorkflowTests(unittest.TestCase):
    def test_duplicate_override_is_detected_from_decision_payload(self) -> None:
        decision = SimpleNamespace(
            decision_payload_json={
                "duplicate_gate_override": {
                    "outcome": "false_duplicate",
                    "reason": "Human reviewer confirmed the symbols are visually similar but distinct.",
                }
            }
        )

        override = publication_duplicate_override_for_decision(decision)

        self.assertEqual(override["outcome"], "false_duplicate")
        self.assertEqual(override["reason"], "Human reviewer confirmed the symbols are visually similar but distinct.")

    def test_duplicate_override_rejects_non_false_duplicate_outcomes(self) -> None:
        decision = SimpleNamespace(decision_payload_json={"duplicate_gate_override": {"outcome": "duplicate_confirmed"}})

        self.assertIsNone(publication_duplicate_override_for_decision(decision))

    def test_split_status_after_handoff_preserves_completed_publication_state(self) -> None:
        item = SimpleNamespace(status="published", downstream_agent_slug="rupert")

        status, target_agent = split_item_status_after_handoff(item, is_approval=True)

        self.assertEqual(status, "published")
        self.assertEqual(target_agent, "rupert")

    def test_split_status_after_handoff_preserves_duplicate_pending_state(self) -> None:
        item = SimpleNamespace(status="duplicate_pending", downstream_agent_slug="libby")

        status, target_agent = split_item_status_after_handoff(item, is_approval=True)

        self.assertEqual(status, "duplicate_pending")
        self.assertEqual(target_agent, "libby")

    def test_review_split_status_groups_make_active_and_terminal_states_explicit(self) -> None:
        self.assertEqual(set(OPEN_SPLIT_ITEM_STATUSES), {"awaiting_decision", "returned_for_review", "duplicate_exception"})
        self.assertEqual(split_item_status_group("duplicate_exception"), "active_review")
        self.assertTrue(is_open_split_item_status("duplicate_exception"))
        self.assertFalse(is_open_split_item_status("duplicate_pending"))
        self.assertEqual(split_item_status_group("duplicate_pending"), "active_downstream")
        self.assertTrue(is_terminal_split_item_status("published"))
        self.assertTrue(is_terminal_split_item_status("duplicate_resolved"))
        self.assertTrue(is_terminal_split_item_status("deleted"))
        self.assertFalse(is_terminal_split_item_status("queued_rupert"))
        self.assertEqual(split_item_status_group("something_new"), "unknown")

    def test_split_status_after_handoff_preserves_libby_followup_states(self) -> None:
        returned = SimpleNamespace(status="returned_for_review", downstream_agent_slug=None)
        deleted = SimpleNamespace(status="deleted", downstream_agent_slug=None)
        queued = SimpleNamespace(status="queued_libby", downstream_agent_slug="libby")

        self.assertEqual(split_item_status_after_handoff(returned, is_approval=False), ("returned_for_review", None))
        self.assertEqual(split_item_status_after_handoff(deleted, is_approval=False), ("deleted", None))
        self.assertEqual(split_item_status_after_handoff(queued, is_approval=False), ("queued_libby", "libby"))


if __name__ == "__main__":
    unittest.main()
