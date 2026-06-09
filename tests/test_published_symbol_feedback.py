from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from uuid import UUID

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.routes.published import (
    MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION,
    normalize_published_symbol_command_request,
    published_symbol_row,
)
from symgov_backend.routes.workspace import build_published_symbol_workspace_item, queue_item_display_parts


class PublishedSymbolFeedbackTests(unittest.TestCase):
    def test_command_request_rejects_more_than_five_symbols(self) -> None:
        with self.assertRaises(ValueError):
            normalize_published_symbol_command_request(
                {
                    "command": "comment",
                    "symbolIds": [str(UUID(int=index + 1)) for index in range(MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION + 1)],
                    "comment": "Please check these.",
                }
            )

    def test_command_request_accepts_comment_and_send_for_review_commands(self) -> None:
        normalized = normalize_published_symbol_command_request(
            {
                "command": "send_for_review",
                "symbolIds": [" 0002-32 ", "0002-33"],
                "comment": "Wrong designation on both records.",
            }
        )

        self.assertEqual(normalized["command"], "send_for_review")
        self.assertEqual(normalized["symbol_ids"], ["0002-32", "0002-33"])
        self.assertEqual(normalized["comment"], "Wrong designation on both records.")

    def test_ed_queue_display_parts_use_published_symbol_readable_id(self) -> None:
        queue_item = SimpleNamespace(
            payload_json={
                "task_type": "published_symbol_review_request",
                "symbol_id": "11111111-1111-1111-1111-111111111111",
                "symbol_slug": "0002-12",
                "published_display_id": "0002-12",
                "symbol_name": "Check valve",
            },
            source_type="published_symbol_review_request",
            source_id=UUID("11111111-1111-1111-1111-111111111111"),
            id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        package_id, sequence, display_name = queue_item_display_parts(None, queue_item)

        self.assertIsNone(package_id)
        self.assertIsNone(sequence)
        self.assertEqual(display_name, "0002-12")

    def test_ed_queue_display_prefers_readable_id_over_symbol_name(self) -> None:
        queue_item = SimpleNamespace(
            payload_json={
                "task_type": "published_symbol_review_request",
                "display_name": "Check valve",
                "workspace_display_name": "Check valve",
                "symbol_slug": "0002-12",
                "published_display_id": "0002-12",
                "symbol_name": "Check valve",
            },
            source_type="published_symbol_review_request",
            source_id=UUID("11111111-1111-1111-1111-111111111111"),
            id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        package_id, sequence, display_name = queue_item_display_parts(None, queue_item)

        self.assertIsNone(package_id)
        self.assertIsNone(sequence)
        self.assertEqual(display_name, "0002-12")

    def test_published_symbol_row_exposes_comment_indicator_fields(self) -> None:
        row = SimpleNamespace(
            symbol_id=UUID("11111111-1111-1111-1111-111111111111"),
            slug="0002-32",
            canonical_name="Check valve",
            category="Valve",
            discipline="Piping",
            symbol_revision_id=UUID("22222222-2222-2222-2222-222222222222"),
            revision_label="Rev 1",
            revision_created_at=None,
            payload_json={"display_name": "0002-32", "name": "Check valve"},
            rationale="",
            page_id=UUID("33333333-3333-3333-3333-333333333333"),
            page_code="PID-0002-32",
            page_title="Check valve",
            effective_date=SimpleNamespace(isoformat=lambda: "2026-06-09"),
            last_updated_at=None,
            pack_id=UUID("44444444-4444-4444-4444-444444444444"),
            pack_code="0002",
            pack_title="Piping symbols",
            audience="public",
            sort_order=32,
        )

        payload = published_symbol_row(row, comment_counts_by_symbol={str(row.symbol_id): 2})

        self.assertTrue(payload["hasComments"])
        self.assertEqual(payload["commentCount"], 2)

    def test_published_symbol_review_case_builds_human_queue_payload(self) -> None:
        review_case = SimpleNamespace(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            current_stage="classification_review",
            escalation_level="medium",
            opened_at=SimpleNamespace(isoformat=lambda: "2026-06-09T12:00:00+00:00"),
        )
        symbol = SimpleNamespace(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            slug="0002-32",
            canonical_name="Check valve",
            category="Valve",
            discipline="Piping",
            current_revision_id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        item = build_published_symbol_workspace_item(
            review_case=review_case,
            symbol=symbol,
            comment="Needs correcting before it stays published.",
        )

        self.assertEqual(item.reviewItemType, "published_symbol")
        self.assertEqual(item.symbolId, "0002-32")
        self.assertEqual(item.displayName, "0002-32")
        self.assertIn("Needs correcting", item.summary)
        self.assertEqual(item.currentStage, "classification_review")


if __name__ == "__main__":
    unittest.main()
