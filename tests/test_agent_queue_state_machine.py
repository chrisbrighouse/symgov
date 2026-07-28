from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.agent_queue_reconciliation import (
    QUEUE_STATUS_GROUPS,
    build_reggie_queue_control_suggestions,
    queue_status_group,
)
from symgov_backend.routes.workspace import _build_reggie_queue_control_response, get_workspace_agent_worker_health
from symgov_backend.models import AgentDefinition, AgentQueueItem
import symgov_backend.agent_queue_worker as queue_worker
from symgov_backend.agent_queue_worker import (
    AgentQueueWorkerConfig,
    AgentQueueWorkerState,
    agent_worker_health_payload,
    process_agent_queue_once,
    queue_item_claim_blocked,
    run_agent_queue_worker,
)


class AgentQueueStateMachineTests(unittest.TestCase):
    def test_ed_claim_acceptance_uses_real_sqlalchemy_transactions(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        claim_test = source.rsplit(
            "def test_repository_claims_real_ed_runner_boundary_with_live_marker_and_durable_row", 1
        )[1].split("def test_workspace_agent_worker_health", 1)[0]

        self.assertNotIn("class DurableSession", claim_test)
        self.assertIn("create_engine", claim_test)
        self.assertIn("sessionmaker", claim_test)

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

    def test_published_feedback_pause_blocks_only_matching_ed_claims(self) -> None:
        published = {
            "source_type": "published_symbol_review_request",
            "payload_json": {"task_type": "published_symbol_review_request"},
        }
        unrelated = {
            "source_type": "support_request",
            "payload_json": {"task_type": "support_request"},
        }

        self.assertTrue(queue_item_claim_blocked("ed", published, published_feedback_paused=True))
        self.assertFalse(queue_item_claim_blocked("ed", unrelated, published_feedback_paused=True))
        self.assertFalse(queue_item_claim_blocked("scott", published, published_feedback_paused=True))
        self.assertFalse(queue_item_claim_blocked("ed", published, published_feedback_paused=False))

    def test_repository_claims_real_ed_runner_boundary_with_live_marker_and_durable_row(self) -> None:
        database_url = os.environ.get("SYMGOV_F04_TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("SYMGOV_F04_TEST_DATABASE_URL must point to a disposable PostgreSQL database")
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = pathlib.Path(directory)
            queue_dir = runtime_root / "agent_queue_items"
            queue_dir.mkdir(parents=True)
            pause_file = runtime_root / "published-feedback.pause"
            published_path = queue_dir / "11111111-1111-4111-8111-111111111111.json"
            unrelated_path = queue_dir / "22222222-2222-4222-8222-222222222222.json"
            unrelated = {
                "id": unrelated_path.stem,
                "agent_id": "ed",
                "source_type": "support_request",
                "status": "queued",
                "source_id": "33333333-3333-4333-8333-333333333333",
                "priority": "medium",
                "created_at": "2026-07-28T00:00:01Z",
                "started_at": None,
                "completed_at": None,
                "payload_json": {"task_type": "support_request"},
            }
            published = {
                "id": published_path.stem,
                "agent_id": "ed",
                "source_type": "published_symbol_review_request",
                "status": "queued",
                "source_id": "44444444-4444-4444-8444-444444444444",
                "priority": "medium",
                "created_at": "2026-07-28T00:00:00Z",
                "started_at": None,
                "completed_at": None,
                "payload_json": {
                    "task_type": "published_symbol_review_request",
                    "queue_item_id": published_path.stem,
                    "symbol_id": "44444444-4444-4444-8444-444444444444",
                    "symbol_display_id": "0009-18",
                    "comment": "Review this.",
                    "next_stage": "classification_review",
                },
            }
            unrelated_path.write_text(json.dumps(unrelated), encoding="utf-8")
            published_path.write_text(json.dumps(published), encoding="utf-8")
            original_published_bytes = published_path.read_bytes()
            engine = create_engine(database_url)
            AgentQueueItem.__table__.drop(engine, checkfirst=True)
            AgentDefinition.__table__.drop(engine, checkfirst=True)
            AgentDefinition.__table__.create(engine)
            AgentQueueItem.__table__.create(engine)
            Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            ed_agent_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
            with Session.begin() as session:
                session.add(AgentDefinition(
                    id=ed_agent_id,
                    slug="ed",
                    display_name="Ed",
                    role="feedback",
                    model="test",
                    status="active",
                    queue_family="feedback",
                    created_at=now,
                    updated_at=now,
                ))
                session.add_all([
                    AgentQueueItem(
                        id=UUID(item["id"]),
                        agent_id=ed_agent_id,
                        source_type=item["source_type"],
                        source_id=UUID(item["source_id"]),
                        status="queued",
                        priority="medium",
                        payload_json=item["payload_json"],
                        created_at=now,
                    )
                    for item in (published, unrelated)
                ])
            env_file = runtime_root / "test-db.env"
            env_file.write_text(f"SYMGOV_DATABASE_URL={database_url}\n", encoding="utf-8")
            actual_runner = queue_worker._load_module(
                queue_worker.AGENT_SPECS["ed"]["module"],
                queue_worker.AGENT_SPECS["ed"]["runner_path"],
            )
            actual_process = actual_runner.process_queue_item
            runner_entries = []

            def recording_actual_process(**kwargs):
                item = json.loads(pathlib.Path(kwargs["queue_item_path"]).read_text(encoding="utf-8"))
                with Session() as session:
                    durable_item = session.get(AgentQueueItem, UUID(item["id"]))
                    self.assertIsNotNone(durable_item)
                    durable_status = durable_item.status
                runner_entries.append((item["id"], item["status"], durable_status))
                return actual_process(**kwargs)

            actual_runner.process_queue_item = recording_actual_process
            original_enumerate = queue_worker.queued_item_paths
            enumeration_count = 0

            def enumerate_then_pause(*args, **kwargs):
                nonlocal enumeration_count
                paths = original_enumerate(*args, **kwargs)
                if enumeration_count == 0:
                    pause_file.write_text("paused\n", encoding="utf-8")
                enumeration_count += 1
                return paths

            original_gate = queue_worker.published_feedback_claims_paused
            gate_callers = []

            def recording_gate():
                gate_callers.append(inspect.stack()[1].function)
                return original_gate()

            config = AgentQueueWorkerConfig(
                agents=("ed",),
                limit=10,
                runtime_roots={"ed": runtime_root},
                db_env_file=env_file,
            )
            with (
                patch.dict("os.environ", {"SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE": str(pause_file)}),
                patch("symgov_backend.agent_queue_worker._load_module", return_value=actual_runner),
                patch("symgov_backend.agent_queue_worker.queued_item_paths", side_effect=enumerate_then_pause),
                patch("symgov_backend.agent_queue_worker.published_feedback_claims_paused", new=recording_gate),
            ):
                first = process_agent_queue_once("ed", config)
                self.assertEqual(first["processedCount"], 1)
                with Session() as session:
                    self.assertEqual(session.get(AgentQueueItem, UUID(published["id"])).status, "queued")
                    self.assertEqual(session.get(AgentQueueItem, UUID(unrelated["id"])).status, "completed")
                self.assertEqual(published_path.read_bytes(), original_published_bytes)
                self.assertEqual(json.loads(unrelated_path.read_text())["status"], "completed")
                self.assertEqual(runner_entries, [(unrelated["id"], "queued", "queued")])
                self.assertFalse(any(published["id"] in entry[0] for entry in runner_entries))
                pause_file.unlink()

                lock_observed = False

                def pause_after_locked_claim_query(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal lock_observed
                    if "agent_queue_items" in statement and "FOR UPDATE" in statement:
                        lock_observed = True
                        pause_file.write_text("paused after row lock\n", encoding="utf-8")

                event.listen(Engine, "after_cursor_execute", pause_after_locked_claim_query)
                try:
                    self.assertFalse(queue_worker.claim_published_feedback_queue_item(published_path, config))
                finally:
                    event.remove(Engine, "after_cursor_execute", pause_after_locked_claim_query)
                self.assertTrue(lock_observed)
                with Session() as session:
                    locked_pause_row = session.get(AgentQueueItem, UUID(published["id"]))
                    self.assertEqual(locked_pause_row.status, "queued")
                    self.assertIsNone(locked_pause_row.started_at)
                self.assertEqual(published_path.read_bytes(), original_published_bytes)
                pause_file.unlink()
                second = process_agent_queue_once("ed", config)

            self.assertEqual(second["processedCount"], 1)
            with Session() as session:
                self.assertEqual(session.get(AgentQueueItem, UUID(published["id"])).status, "completed")
            self.assertEqual(runner_entries[-1], (published["id"], "running", "running"))
            self.assertEqual(json.loads(published_path.read_text())["status"], "completed")
            self.assertIn("claim_published_feedback_queue_item", gate_callers)

            with Session.begin() as session:
                durable = session.get(AgentQueueItem, UUID(published["id"]), with_for_update=True)
                durable.status = "queued"
                durable.started_at = None
            published_path.write_bytes(original_published_bytes)
            with (
                patch.dict("os.environ", {"SYMGOV_PUBLISHED_FEEDBACK_PAUSE_FILE": str(pause_file)}),
                patch("symgov_backend.agent_queue_worker._write_queue_item_atomically", side_effect=RuntimeError("disk unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "disk unavailable"):
                    queue_worker.claim_published_feedback_queue_item(published_path, config)
            with Session() as session:
                rolled_back = session.get(AgentQueueItem, UUID(published["id"]))
                self.assertEqual(rolled_back.status, "queued")
                self.assertIsNone(rolled_back.started_at)
            self.assertEqual(published_path.read_bytes(), original_published_bytes)

            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE OR REPLACE FUNCTION reject_running_claim() RETURNS trigger
                    LANGUAGE plpgsql AS $$
                    BEGIN
                        IF NEW.status = 'running' THEN
                            RAISE EXCEPTION 'synthetic claim commit failure';
                        END IF;
                        RETURN NEW;
                    END
                    $$
                """))
                connection.execute(text("""
                    CREATE CONSTRAINT TRIGGER reject_running_claim_trigger
                    AFTER UPDATE ON agent_queue_items
                    DEFERRABLE INITIALLY DEFERRED
                    FOR EACH ROW EXECUTE FUNCTION reject_running_claim()
                """))
            with self.assertRaisesRegex(Exception, "synthetic claim commit failure"):
                queue_worker.claim_published_feedback_queue_item(published_path, config)
            with Session() as session:
                commit_failed = session.get(AgentQueueItem, UUID(published["id"]))
                self.assertEqual(commit_failed.status, "queued")
                self.assertIsNone(commit_failed.started_at)
            self.assertEqual(published_path.read_bytes(), original_published_bytes)
            with engine.begin() as connection:
                connection.execute(text("DROP TRIGGER reject_running_claim_trigger ON agent_queue_items"))
                connection.execute(text("DROP FUNCTION reject_running_claim()"))
            AgentQueueItem.__table__.drop(engine)
            AgentDefinition.__table__.drop(engine)
            engine.dispose()

    def test_workspace_agent_worker_health_exposes_live_state_without_queue_mutation(self) -> None:
        state = AgentQueueWorkerState(configured_agents=("vlad",), last_success_at="2026-07-10T14:02:01Z")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_worker_state=state, agent_worker_task=None)))

        from unittest.mock import patch

        with patch("symgov_backend.routes.workspace.published_feedback_claims_paused", return_value=True):
            payload = get_workspace_agent_worker_health(request)

        self.assertEqual(payload["configuredAgents"], ["vlad"])
        self.assertEqual(payload["lastSuccessAt"], "2026-07-10T14:02:01Z")
        self.assertFalse(payload["taskRunning"])
        self.assertIsNone(payload["taskDone"])
        self.assertTrue(payload["publishedFeedbackClaimsPaused"])


if __name__ == "__main__":
    unittest.main()
