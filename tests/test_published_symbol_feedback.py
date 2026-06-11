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
    published_symbol_comment_item,
    published_symbol_display_id,
    published_symbol_row,
)
from symgov_backend.routes.workspace import build_published_symbol_workspace_item, queue_item_display_parts
from symgov_backend.models import SymbolRevision


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

    def test_ed_queue_display_uses_symbol_revision_short_id_when_payload_has_name(self) -> None:
        symbol_id = UUID("11111111-1111-1111-1111-111111111111")
        revision_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        queue_item = SimpleNamespace(
            payload_json={
                "task_type": "published_symbol_review_request",
                "display_name": "Check valve",
                "workspace_display_name": "Check valve",
                "symbol_slug": "4-way-valve",
                "published_display_id": "4-way-valve",
                "symbol_name": "Check valve",
            },
            source_type="published_symbol_review_request",
            source_id=symbol_id,
            id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        class FakeSession:
            def get(self, model, key):
                if model.__name__ == "GovernedSymbol" and key == symbol_id:
                    return SimpleNamespace(current_revision_id=revision_id)
                if model.__name__ == "SymbolRevision" and key == revision_id:
                    return SimpleNamespace(payload_json={"package_display_id": "0001", "package_symbol_sequence": 3})
                return None

        package_id, sequence, display_name = queue_item_display_parts(FakeSession(), queue_item)

        self.assertIsNone(package_id)
        self.assertIsNone(sequence)
        self.assertEqual(display_name, "0001-3")

    def test_queue_display_prefers_short_symbol_id_for_all_queue_cards(self) -> None:
        queue_item = SimpleNamespace(
            payload_json={
                "task_type": "symbol_classification",
                "display_name": "Check valve",
                "symbol_display_id": "0007-4",
                "symbol_name": "Check valve",
            },
            source_type="classification_record",
            source_id=UUID("11111111-1111-1111-1111-111111111111"),
            id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        package_id, sequence, display_name = queue_item_display_parts(None, queue_item)

        self.assertIsNone(package_id)
        self.assertIsNone(sequence)
        self.assertEqual(display_name, "0007-4")

    def test_published_symbol_display_id_prefers_pack_code_and_sequence(self) -> None:
        row = SimpleNamespace(
            slug="4-way-valve",
            pack_code="0001",
            sort_order=3,
            payload_json={"display_name": "4-way-valve", "package_display_id": "0001", "package_symbol_sequence": 3},
        )

        self.assertEqual(published_symbol_display_id(row), "0001-3")

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
            payload_json={"display_name": "0002-32", "name": "Check valve", "package_display_id": "0002", "package_symbol_sequence": 32},
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

        self.assertEqual(payload["displayName"], "0002-32")
        self.assertTrue(payload["hasComments"])
        self.assertEqual(payload["commentCount"], 2)

    def test_published_symbol_comment_item_serializes_history_entry(self) -> None:
        comment = SimpleNamespace(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            kind="comment",
            status="open",
            source="published_symbol_command_menu",
            detail="Second comment should appear first.",
            created_at=SimpleNamespace(isoformat=lambda: "2026-06-10T13:00:00+00:00"),
            updated_at=SimpleNamespace(isoformat=lambda: "2026-06-10T13:00:00+00:00"),
        )

        payload = published_symbol_comment_item(comment, submitter_name="Ed")

        self.assertEqual(payload["id"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        self.assertEqual(payload["kind"], "comment")
        self.assertEqual(payload["detail"], "Second comment should appear first.")
        self.assertEqual(payload["submittedBy"], "Ed")
        self.assertEqual(payload["createdAt"], "2026-06-10T13:00:00+00:00")

    def test_published_symbol_review_case_builds_human_queue_payload(self) -> None:
        review_case = SimpleNamespace(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            current_stage="classification_review",
            escalation_level="medium",
            opened_at=SimpleNamespace(isoformat=lambda: "2026-06-09T12:00:00+00:00"),
        )
        symbol = SimpleNamespace(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            slug="check-valve",
            canonical_name="Check valve",
            category="Valve",
            discipline="Piping",
            current_revision_id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        class FakeSession:
            def get(self, model, key):
                if model == SymbolRevision and key == symbol.current_revision_id:
                    return SimpleNamespace(payload_json={"package_display_id": "0002", "package_symbol_sequence": 32})
                return None

        item = build_published_symbol_workspace_item(
            session=FakeSession(),
            review_case=review_case,
            symbol=symbol,
            comment="Needs correcting before it stays published.",
        )

        self.assertEqual(item.reviewItemType, "published_symbol")
        self.assertEqual(item.splitChildStatus, "returned_for_review")
        self.assertEqual(item.status, "Returned")
        self.assertEqual(item.symbolId, "0002-32")
        self.assertEqual(item.displayName, "0002-32")
        self.assertIn("Needs correcting", item.summary)
        self.assertEqual(item.currentStage, "classification_review")
        self.assertEqual(item.sourcePreviewUrl, "/api/v1/published/symbols/check-valve/preview")


if __name__ == "__main__":
    unittest.main()
