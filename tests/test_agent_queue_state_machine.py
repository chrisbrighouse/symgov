from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace
from uuid import UUID

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.agent_queue_reconciliation import (
    QUEUE_STATUS_GROUPS,
    build_reggie_queue_control_suggestions,
    queue_status_group,
)
from symgov_backend.routes.workspace import _build_reggie_queue_control_response, get_workspace_agent_worker_health
from symgov_backend.agent_queue_worker import (
    AgentQueueWorkerConfig,
    AgentQueueWorkerState,
    agent_worker_health_payload,
    run_agent_queue_worker,
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

    def test_reggie_endpoint_response_is_observational_and_camel_case(self) -> None:
        queue_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
        payload = {
            "dry_run": True,
            "active_only": True,
            "agents": ["scott"],
            "runtime_records_seen": 3,
            "db_active_rows_inspected": 0,
            "change_count": 0,
            "missing_runtime_count": 1,
            "runtime_orphan_count": 0,
            "skipped_count": 0,
            "control_suggestion_count": 1,
            "control_suggestions": build_reggie_queue_control_suggestions(
                missing_runtime=[
                    {
                        "queue_item_id": str(queue_id),
                        "agent": "scott",
                        "db_status": "queued",
                        "source_type": "external_submission",
                        "created_at": "2026-07-08T09:15:00+00:00",
                        "candidate_symbol_id": "TRACY-SMOKE-RESTRICTED-V2",
                    }
                ],
                skipped=[],
                runtime_orphans=[],
            ),
        }

        response = _build_reggie_queue_control_response(payload)
        dumped = response.model_dump()

        self.assertTrue(dumped["dryRun"])
        self.assertEqual(dumped["runtimeRecordsSeen"], 3)
        self.assertEqual(dumped["controlSuggestionCount"], 1)
        self.assertEqual(dumped["items"][0]["sourceId"], str(queue_id))
        self.assertEqual(dumped["items"][0]["createdAt"], "2026-07-08T09:15:00+00:00")
        self.assertEqual(dumped["items"][0]["evidence"]["candidate_symbol_id"], "TRACY-SMOKE-RESTRICTED-V2")
        self.assertEqual(dumped["items"][0]["ruleCode"], "agent_queue_active_db_missing_runtime")
        self.assertTrue(dumped["items"][0]["observationalOnly"])

    def test_reggie_response_prefers_symbol_display_id_in_operator_detail(self) -> None:
        queue_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        payload = {
            "dry_run": True,
            "active_only": True,
            "agents": ["ed"],
            "runtime_records_seen": 0,
            "db_active_rows_inspected": 0,
            "change_count": 0,
            "missing_runtime_count": 1,
            "runtime_orphan_count": 0,
            "skipped_count": 0,
            "control_suggestion_count": 1,
            "control_suggestions": build_reggie_queue_control_suggestions(
                missing_runtime=[
                    {
                        "queue_item_id": str(queue_id),
                        "agent": "ed",
                        "db_status": "queued",
                        "source_type": "published_symbol_review_request",
                    }
                ],
                skipped=[],
                runtime_orphans=[],
            ),
        }

        response = _build_reggie_queue_control_response(
            payload,
            queue_display_lookup={str(queue_id): "0009-18"},
        )
        item = response.model_dump()["items"][0]

        self.assertIn("ed queue item 0009-18", item["detail"])
        self.assertIn(str(queue_id), item["detail"])
        self.assertEqual(item["evidence"]["symbol_display_id"], "0009-18")

    def test_agent_worker_survives_a_failed_drain_cycle_and_records_health(self) -> None:
        async def exercise_worker() -> None:
            stop_event = asyncio.Event()
            state = AgentQueueWorkerState(configured_agents=("vlad",))
            calls: list[int] = []

            def drain(_config: AgentQueueWorkerConfig) -> dict:
                calls.append(len(calls) + 1)
                if len(calls) == 1:
                    raise RuntimeError("synthetic worker failure")
                stop_event.set()
                return {"processedCount": 1, "errorCount": 0}

            from unittest.mock import patch

            with patch("symgov_backend.agent_queue_worker.drain_agent_queues", side_effect=drain):
                await run_agent_queue_worker(
                    AgentQueueWorkerConfig(agents=("vlad",), drain=True, interval_seconds=0),
                    stop_event,
                    state,
                )

            self.assertEqual(calls, [1, 2])
            self.assertEqual(state.last_error, "synthetic worker failure")
            self.assertIsNotNone(state.last_started_at)
            self.assertIsNotNone(state.last_success_at)
            self.assertEqual(state.last_result, {"processedCount": 1, "errorCount": 0})

        asyncio.run(exercise_worker())

    def test_agent_worker_health_payload_reports_configuration_activity_error_and_task_status(self) -> None:
        state = AgentQueueWorkerState(
            configured_agents=("scott", "vlad"),
            last_started_at="2026-07-10T14:02:00Z",
            last_success_at="2026-07-10T14:02:01Z",
            last_error="synthetic worker failure",
            last_result={"processedCount": 1, "errorCount": 0},
        )

        payload = agent_worker_health_payload(state, task_done=False)

        self.assertEqual(payload["configuredAgents"], ["scott", "vlad"])
        self.assertEqual(payload["lastStartedAt"], "2026-07-10T14:02:00Z")
        self.assertEqual(payload["lastSuccessAt"], "2026-07-10T14:02:01Z")
        self.assertEqual(payload["lastError"], "synthetic worker failure")
        self.assertEqual(payload["lastResult"], {"processedCount": 1, "errorCount": 0})
        self.assertTrue(payload["taskRunning"])
        self.assertFalse(payload["taskDone"])

    def test_workspace_agent_worker_health_exposes_live_state_without_queue_mutation(self) -> None:
        state = AgentQueueWorkerState(configured_agents=("vlad",), last_success_at="2026-07-10T14:02:01Z")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_worker_state=state, agent_worker_task=None)))

        payload = get_workspace_agent_worker_health(request)

        self.assertEqual(payload["configuredAgents"], ["vlad"])
        self.assertEqual(payload["lastSuccessAt"], "2026-07-10T14:02:01Z")
        self.assertFalse(payload["taskRunning"])
        self.assertIsNone(payload["taskDone"])


if __name__ == "__main__":
    unittest.main()
