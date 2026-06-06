from __future__ import annotations

import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.publication_handoff import (
    mark_split_item_duplicate_pending_for_decision,
    mark_split_items_published_for_revisions,
)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def get(self, model, row_id):  # noqa: ANN001 - mirrors SQLAlchemy's Session.get signature for the helper under test
        return self.rows.get(row_id)


class PublicationHandoffSplitStatusTests(unittest.TestCase):
    def test_marks_split_item_published_from_revision_lineage(self) -> None:
        split_id = uuid.uuid4()
        split_item = SimpleNamespace(
            id=split_id,
            status="queued_rupert",
            downstream_agent_slug="rupert",
            downstream_queue_item_id="old-queue",
            processed_at=None,
            updated_at=None,
        )
        revision = SimpleNamespace(
            payload_json={
                "lineage": {
                    "review_split_item_id": str(split_id),
                }
            }
        )
        completed_at = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

        published_ids = mark_split_items_published_for_revisions(
            FakeSession({split_id: split_item}),
            revisions=[revision],
            downstream_queue_item_id="aqi-rupert-review-example",
            completed_at=completed_at,
        )

        self.assertEqual(published_ids, [str(split_id)])
        self.assertEqual(split_item.status, "published")
        self.assertEqual(split_item.downstream_agent_slug, "rupert")
        self.assertEqual(split_item.downstream_queue_item_id, "aqi-rupert-review-example")
        self.assertEqual(split_item.processed_at, completed_at)
        self.assertEqual(split_item.updated_at, completed_at)

    def test_ignores_revisions_without_valid_split_lineage(self) -> None:
        bad_revision = SimpleNamespace(payload_json={"lineage": {"review_split_item_id": "not-a-uuid"}})
        no_lineage_revision = SimpleNamespace(payload_json={})

        published_ids = mark_split_items_published_for_revisions(
            FakeSession({}),
            revisions=[bad_revision, no_lineage_revision],
            downstream_queue_item_id="aqi-rupert-review-example",
            completed_at=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(published_ids, [])

    def test_marks_split_item_duplicate_pending_when_publication_gate_blocks(self) -> None:
        split_id = uuid.uuid4()
        split_item = SimpleNamespace(
            id=split_id,
            status="queued_rupert",
            downstream_agent_slug="rupert",
            downstream_queue_item_id="aqi-rupert-old",
            updated_at=None,
        )
        decision = SimpleNamespace(decision_payload_json={"split_child_item_id": str(split_id)})
        updated_at = datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc)

        duplicate_split_item_id = mark_split_item_duplicate_pending_for_decision(
            FakeSession({split_id: split_item}),
            decision=decision,
            downstream_queue_item_id="aqi-libby-duplicate-example",
            updated_at=updated_at,
        )

        self.assertEqual(duplicate_split_item_id, str(split_id))
        self.assertEqual(split_item.status, "duplicate_pending")
        self.assertEqual(split_item.downstream_agent_slug, "libby")
        self.assertEqual(split_item.downstream_queue_item_id, "aqi-libby-duplicate-example")
        self.assertEqual(split_item.updated_at, updated_at)

    def test_duplicate_pending_helper_ignores_missing_or_invalid_split_id(self) -> None:
        invalid_decision = SimpleNamespace(decision_payload_json={"split_child_item_id": "not-a-uuid"})
        missing_decision = SimpleNamespace(decision_payload_json={})

        self.assertIsNone(
            mark_split_item_duplicate_pending_for_decision(
                FakeSession({}),
                decision=invalid_decision,
                downstream_queue_item_id="aqi-libby-duplicate-example",
                updated_at=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
            )
        )
        self.assertIsNone(
            mark_split_item_duplicate_pending_for_decision(
                FakeSession({}),
                decision=missing_decision,
                downstream_queue_item_id="aqi-libby-duplicate-example",
                updated_at=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
            )
        )


if __name__ == "__main__":
    unittest.main()
