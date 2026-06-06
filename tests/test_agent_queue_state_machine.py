from __future__ import annotations

import pathlib
import sys
import unittest
from uuid import UUID

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.agent_queue_reconciliation import (
    QUEUE_STATUS_GROUPS,
    build_reggie_queue_control_suggestions,
    queue_status_group,
)


class AgentQueueStateMachineTests(unittest.TestCase):
    def test_queue_status_groups_are_explicit_and_non_overlapping(self) -> None:
        all_statuses = [status for statuses in QUEUE_STATUS_GROUPS.values() for status in statuses]
        self.assertEqual(len(all_statuses), len(set(all_statuses)))
        self.assertEqual(queue_status_group("queued"), "active")
        self.assertEqual(queue_status_group("running"), "active")
        self.assertEqual(queue_status_group("escalated"), "waiting_operator")
        self.assertEqual(queue_status_group("progress_saved"), "terminal")
        self.assertEqual(queue_status_group("published"), "terminal")
        self.assertEqual(queue_status_group("mystery"), "unknown")

    def test_reggie_suggestions_identify_active_db_rows_with_missing_runtime_without_auto_fixing(self) -> None:
        queue_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        suggestions = build_reggie_queue_control_suggestions(
            missing_runtime=[
                {
                    "queue_item_id": str(queue_id),
                    "agent": "hannah",
                    "db_status": "queued",
                    "source_type": "published_page",
                }
            ],
            skipped=[],
            runtime_orphans=[],
        )

        self.assertEqual(len(suggestions), 1)
        suggestion = suggestions[0]
        self.assertEqual(suggestion["rule_code"], "agent_queue_active_db_missing_runtime")
        self.assertEqual(suggestion["severity"], "warning")
        self.assertEqual(suggestion["source_type"], "agent_queue_item")
        self.assertEqual(suggestion["source_id"], queue_id)
        self.assertIn("suggested_remediation", suggestion)
        self.assertTrue(suggestion["observational_only"])
        self.assertNotIn("auto_fix", suggestion)

    def test_reggie_suggestions_identify_runtime_terminal_status_that_can_reconcile_db(self) -> None:
        queue_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        suggestions = build_reggie_queue_control_suggestions(
            missing_runtime=[],
            skipped=[],
            runtime_orphans=[],
            changes=[
                {
                    "queue_item_id": str(queue_id),
                    "agent": "scott",
                    "db_status": "queued",
                    "runtime_status": "completed",
                    "runtime_path": "/runtime/scott/agent_queue_items/item.json",
                }
            ],
        )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["rule_code"], "agent_queue_db_runtime_terminal_mismatch")
        self.assertEqual(suggestions[0]["severity"], "info")
        self.assertEqual(suggestions[0]["source_id"], queue_id)
        self.assertIn("runtime terminal status", suggestions[0]["suggested_remediation"])
        self.assertTrue(suggestions[0]["observational_only"])

    def test_reggie_suggestions_identify_runtime_orphans(self) -> None:
        queue_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
        suggestions = build_reggie_queue_control_suggestions(
            missing_runtime=[],
            skipped=[],
            runtime_orphans=[
                {
                    "queue_item_id": str(queue_id),
                    "agent": "rupert",
                    "runtime_status": "completed",
                    "runtime_path": "/runtime/rupert/agent_queue_items/item.json",
                }
            ],
        )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["rule_code"], "agent_queue_runtime_without_db_mirror")
        self.assertEqual(suggestions[0]["severity"], "warning")
        self.assertTrue(suggestions[0]["observational_only"])


if __name__ == "__main__":
    unittest.main()
