from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from uuid import UUID
from datetime import datetime, timezone

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import symgov_backend.routes.published as published_routes
from symgov_backend.routes.published import run_published_symbol_command
from symgov_backend.models import SymbolRevision, ReviewCase, AgentQueueItem, GovernedSymbol, User, AgentDefinition

class PublishedSymbolReviewWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_for_review_unpublishes_symbol_and_sets_coordination_stage(self) -> None:
        symbol_id = UUID("11111111-1111-1111-1111-111111111111")
        revision_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        page_id = UUID("33333333-3333-3333-3333-333333333333")
        ed_user_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        ed_agent_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

        symbol = SimpleNamespace(
            id=symbol_id,
            slug="0001-3",
            canonical_name="Test Symbol",
            category="Test",
            discipline="Test",
        )
        revision = SimpleNamespace(
            id=revision_id,
            symbol_id=symbol_id,
            lifecycle_state="published",
            payload_json={"package_display_id": "0001", "package_symbol_sequence": 3},
        )

        # Mocking the session and rows
        class FakeRow:
            def __init__(self):
                self.symbol_id = symbol_id
                self.slug = "0001-3"
                self.canonical_name = "Test Symbol"
                self.category = "Test"
                self.discipline = "Test"
                self.symbol_revision_id = revision_id
                self.page_id = page_id
                self.payload_json = revision.payload_json
                self.pack_code = "0001"

        class FakeSession:
            def __init__(self):
                self.added = []
                self.flushed = False
                self.committed = False

            def execute(self, *args, **kwargs):
                return SimpleNamespace(all=lambda: [FakeRow()])

            def query(self, model):
                if model == User:
                    return SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(one_or_none=lambda: SimpleNamespace(id=ed_user_id)))
                if model == AgentDefinition:
                    return SimpleNamespace(filter_by=lambda *a, **k: SimpleNamespace(one_or_none=lambda: SimpleNamespace(id=ed_agent_id)))
                if model == ReviewCase:
                    # Return None to simulate a new case
                    return SimpleNamespace(filter_by=lambda *a, **k: SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(one_or_none=lambda: None)))
                if model == SymbolRevision:
                    return SimpleNamespace(get=lambda key: revision if key == revision_id else None)
                return SimpleNamespace(filter_by=lambda *a, **k: SimpleNamespace(one_or_none=lambda: None))

            def get(self, model, key):
                if model == SymbolRevision and key == revision_id:
                    return revision
                return None

            def add(self, obj):
                self.added.append(obj)

            def flush(self):
                self.flushed = True

            def commit(self):
                self.committed = True

        session = FakeSession()
        original_queue_dir = published_routes.ED_RUNTIME_QUEUE_DIR
        temp_dir = tempfile.TemporaryDirectory()
        published_routes.ED_RUNTIME_QUEUE_DIR = pathlib.Path(temp_dir.name)
        self.addCleanup(temp_dir.cleanup)
        self.addCleanup(setattr, published_routes, "ED_RUNTIME_QUEUE_DIR", original_queue_dir)

        class FakeRequest:
            async def json(self):
                return {"command": "send_for_review", "symbolIds": ["0001-3"], "comment": "Needs review."}

        request = FakeRequest()
        await run_published_symbol_command(request, session)

        # Verify revision was unpublished
        self.assertEqual(revision.lifecycle_state, "review")

        # Verify review case was created with correct initial stage
        review_case = next(obj for obj in session.added if isinstance(obj, ReviewCase))
        self.assertEqual(review_case.current_stage, "ux_feedback_coordination")
        self.assertEqual(review_case.source_entity_id, symbol_id)

        # Verify Ed queue item was created in the DB and mirrored to Ed's runtime queue
        queue_item = next(obj for obj in session.added if isinstance(obj, AgentQueueItem))
        self.assertEqual(queue_item.agent_id, ed_agent_id)
        self.assertEqual(queue_item.payload_json["task_type"], "published_symbol_review_request")
        self.assertEqual(queue_item.payload_json["next_stage"], "classification_review")
        runtime_path = published_routes.ED_RUNTIME_QUEUE_DIR / f"{queue_item.id}.json"
        runtime_payload = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime_payload["id"], str(queue_item.id))
        self.assertEqual(runtime_payload["agent_id"], "ed")
        self.assertEqual(runtime_payload["status"], "queued")
        self.assertEqual(runtime_payload["payload_json"]["next_stage"], "classification_review")

    async def test_ed_completes_review_request_handoff(self) -> None:
        # This tests the logic in run_ed_feedback.py
        # We need to add the ed workspace to the path to import the module
        ed_workspace = pathlib.Path("/data/.openclaw/workspaces/ed")
        if str(ed_workspace) not in sys.path:
            sys.path.insert(0, str(ed_workspace))

        import run_ed_feedback

        review_case_id = UUID("55555555-5555-5555-5555-555555555555")
        symbol_id = UUID("11111111-1111-1111-1111-111111111111")

        task = {
            "queue_item_id": "aqi-ed-1",
            "task_type": "published_symbol_review_request",
            "review_case_id": str(review_case_id),
            "symbol_id": str(symbol_id),
            "symbol_display_id": "0001-3",
            "comment": "Needs review.",
            "next_stage": "classification_review"
        }

        artifact = run_ed_feedback.handle_published_symbol_review_request(task, persist_db=False, db_env_file=None)

        self.assertEqual(artifact["decision"], "coordinate")
        self.assertEqual(artifact["next_stage"], "classification_review")
        self.assertIn("coordinated review handoff", artifact["feedback_summary"])

    async def test_ed_legacy_daisy_coordination_stage_resolves_to_review_queue(self) -> None:
        ed_workspace = pathlib.Path("/data/.openclaw/workspaces/ed")
        if str(ed_workspace) not in sys.path:
            sys.path.insert(0, str(ed_workspace))

        import run_ed_feedback

        task = {
            "queue_item_id": "aqi-ed-legacy",
            "task_type": "published_symbol_review_request",
            "review_case_id": "55555555-5555-5555-5555-555555555555",
            "symbol_id": "11111111-1111-1111-1111-111111111111",
            "symbol_display_id": "0001-3",
            "comment": "Needs review.",
            "next_stage": "daisy_human_review_coordination",
        }

        artifact = run_ed_feedback.handle_published_symbol_review_request(task, persist_db=False, db_env_file=None)

        self.assertEqual(artifact["next_stage"], "classification_review")

if __name__ == "__main__":
    unittest.main()
