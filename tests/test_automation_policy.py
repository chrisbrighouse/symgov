import pathlib
import sys
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.automation_policy import evaluate_symbol_metadata_gate


class SymbolMetadataGateTests(unittest.TestCase):
    def test_blocks_vlad_region_fallback_even_with_category_and_discipline(self):
        decision = evaluate_symbol_metadata_gate(
            name="01-CommonValves Region 15",
            category="Gate Valves",
            discipline="Piping",
            proposed_symbol_id="01-COMMONVALVES-REGION-15",
            file_name="01-commonvalves-region-15.png",
            name_source="fallback",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("symbol_name_is_generic_split_fallback", decision.reasons)

    def test_allows_libby_assigned_specific_metadata(self):
        decision = evaluate_symbol_metadata_gate(
            name="Spring Loaded Pressure Relief Valve",
            category="Relief Valves",
            discipline="Piping",
            proposed_symbol_id="01-COMMONVALVES-REGION-15",
            file_name="01-commonvalves-region-15.png",
            name_source="libby_inferred_history",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, [])

    def test_allows_ocr_name_when_category_and_discipline_are_specific(self):
        decision = evaluate_symbol_metadata_gate(
            name="Globe Valves",
            category="Gate Valves",
            discipline="Piping",
            proposed_symbol_id="GLOBE-VALVES",
            file_name="globe-valves.png",
            name_source="ocr_label",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, [])

    def test_blocks_missing_category_or_discipline(self):
        decision = evaluate_symbol_metadata_gate(
            name="Fail Closed",
            category=None,
            discipline="Piping",
            proposed_symbol_id="FAIL-CLOSED",
            file_name="fail-closed.png",
            name_source="ocr_label",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("category_missing_or_placeholder", decision.reasons)


if __name__ == "__main__":
    unittest.main()
