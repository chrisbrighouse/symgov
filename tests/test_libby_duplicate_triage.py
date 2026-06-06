import importlib.util
from pathlib import Path
import unittest


RUNNER_PATH = Path("/data/.openclaw/workspaces/libby/run_libby_classification.py")
spec = importlib.util.spec_from_file_location("run_libby_classification", RUNNER_PATH)
libby = importlib.util.module_from_spec(spec)
spec.loader.exec_module(libby)


class LibbyDuplicateTriageTests(unittest.TestCase):
    def test_strong_duplicate_evidence_is_auto_confirmed(self):
        task = {
            "queue_item_id": "aqi-libby-duplicate-example",
            "task_type": "publication_duplicate_detected",
            "libby_follow_up_type": "duplicate_resolution",
            "review_case_id": "case-1",
            "review_decision_id": "decision-1",
            "duplicate_evidence": [
                {
                    "candidate_revision_id": "candidate-rev",
                    "matched_revision_id": "matched-rev",
                    "matched_symbol_slug": "single-acting-telescopic",
                    "hamming_distance": 1,
                    "distance_threshold": 4,
                    "pixel_difference": 0.004901,
                    "pixel_difference_threshold": 0.08,
                }
            ],
        }

        artifact = libby.run_duplicate_resolution_task(task)

        self.assertEqual(artifact["decision"], "pass")
        self.assertEqual(artifact["duplicate_resolution"]["outcome"], "duplicate_confirmed")
        self.assertEqual(artifact["duplicate_resolution"]["recommended_action"], "do_not_publish")
        self.assertEqual(artifact["next_agent"], "none")
        self.assertIn("record_duplicate_resolution", artifact["direct_actions"])

    def test_weak_or_missing_duplicate_evidence_routes_to_daisy_exception(self):
        task = {
            "queue_item_id": "aqi-libby-duplicate-weak",
            "task_type": "publication_duplicate_detected",
            "libby_follow_up_type": "duplicate_resolution",
            "review_case_id": "case-1",
            "review_decision_id": "decision-1",
            "duplicate_evidence": [
                {
                    "candidate_revision_id": "candidate-rev",
                    "matched_revision_id": "matched-rev",
                    "matched_symbol_slug": "nearby-symbol",
                    "hamming_distance": 4,
                    "distance_threshold": 4,
                    "pixel_difference": 0.079,
                    "pixel_difference_threshold": 0.08,
                }
            ],
        }

        artifact = libby.run_duplicate_resolution_task(task)

        self.assertEqual(artifact["decision"], "escalate")
        self.assertEqual(artifact["duplicate_resolution"]["outcome"], "needs_human_review")
        self.assertEqual(artifact["next_agent"], "daisy")
        self.assertIn("prepare_duplicate_exception_for_daisy", artifact["direct_actions"])

    def test_duplicate_resolution_task_is_used_by_run_task(self):
        artifact, durable_kind = libby.run_task(
            {
                "queue_item_id": "aqi-libby-duplicate-example",
                "task_type": "publication_duplicate_detected",
                "libby_follow_up_type": "duplicate_resolution",
                "review_case_id": "case-1",
                "review_decision_id": "decision-1",
                "duplicate_evidence": [
                    {
                        "candidate_revision_id": "candidate-rev",
                        "matched_revision_id": "matched-rev",
                        "matched_symbol_slug": "matched-symbol",
                        "hamming_distance": 2,
                        "distance_threshold": 4,
                        "pixel_difference": 0.02,
                        "pixel_difference_threshold": 0.08,
                    }
                ],
            }
        )

        self.assertEqual(durable_kind, "review_followup_report")
        self.assertEqual(artifact["follow_up_type"], "duplicate_resolution")
        self.assertEqual(artifact["duplicate_resolution"]["outcome"], "duplicate_confirmed")


if __name__ == "__main__":
    unittest.main()
