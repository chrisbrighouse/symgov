import pathlib
import sys
import unittest

SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from run_hannah_curation import build_symbol_curation_queue_item, final_queue_status_for_artifact, select_task_symbols


class HannahQueueCardTests(unittest.TestCase):
    def test_builds_traceable_symbol_queue_card(self):
        symbol = {
            "symbol_id": "11111111-1111-1111-1111-111111111111",
            "symbol_slug": "gate-valve",
            "name": "Gate Valve",
            "category": "Valves",
            "discipline": "Piping",
            "symbol_revision_id": "22222222-2222-2222-2222-222222222222",
            "published_page_id": "33333333-3333-3333-3333-333333333333",
            "page_title": "Gate Valve",
            "photo_count": 1,
        }

        card = build_symbol_curation_queue_item(
            symbol,
            hannah_agent_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            created_at="2026-06-06T12:00:00Z",
        )

        self.assertEqual(card["agent_id"], "hannah")
        self.assertEqual(card["source_type"], "published_symbol_photo_enrichment")
        self.assertEqual(card["source_id"], symbol["symbol_id"])
        self.assertEqual(card["status"], "queued")
        self.assertEqual(card["payload_json"]["symbol_id"], symbol["symbol_id"])
        self.assertEqual(card["payload_json"]["symbol_slug"], "gate-valve")
        self.assertEqual(card["payload_json"]["max_photos_per_symbol"], 2)
        self.assertEqual(card["payload_json"]["cooldown_days"], 7)

    def test_select_task_symbols_uses_single_payload_symbol_when_present(self):
        payload_symbol = {"symbol_id": "symbol-a", "name": "Gate Valve", "category": "Valves", "discipline": "Piping"}

        selected = select_task_symbols({"symbol": payload_symbol}, fallback_loader=lambda: [{"symbol_id": "other"}])

        self.assertEqual(selected, [payload_symbol])

    def test_final_status_success_for_attached_image(self):
        self.assertEqual(final_queue_status_for_artifact({"attached_count": 1, "candidate_count": 1}), "success")

    def test_final_status_candidate_when_candidates_need_review(self):
        self.assertEqual(final_queue_status_for_artifact({"attached_count": 0, "candidate_count": 2}), "candidate")

    def test_final_status_completed_when_no_candidate_found(self):
        self.assertEqual(final_queue_status_for_artifact({"attached_count": 0, "candidate_count": 0}), "completed")


if __name__ == "__main__":
    unittest.main()
