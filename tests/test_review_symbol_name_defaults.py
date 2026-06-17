import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.routes.workspace import default_review_symbol_name


class ReviewSymbolNameDefaultTests(unittest.TestCase):
    def test_single_symbol_review_prefers_classification_symbol_key_over_package_id(self):
        class ClassificationRecord:
            symbol_key = "QET-HYDRAULIC-FIXED-DISPLACEMENT-PUMP-A11Y"
            origin_file_name = "qet-hydraulic-fixed-displacement-pump-a11y.svg"

        self.assertEqual(
            default_review_symbol_name(
                package_id="000D",
                primary_symbol_id="QET-HYDRAULIC-FIXED-DISPLACEMENT-PUMP-A11Y",
                classification_record=ClassificationRecord(),
            ),
            "Qet Hydraulic Fixed Displacement Pump",
        )

    def test_single_symbol_review_uses_filename_when_symbol_key_is_unhelpful(self):
        class ClassificationRecord:
            symbol_key = "000E"
            origin_file_name = "qet-pressure-switch-no-a11y.svg"

        self.assertEqual(
            default_review_symbol_name(
                package_id="000E",
                primary_symbol_id="000E",
                classification_record=ClassificationRecord(),
            ),
            "Qet Pressure Switch No",
        )

    def test_single_symbol_review_preserves_engineering_compounds_from_filename(self):
        class ClassificationRecord:
            symbol_key = "000F"
            origin_file_name = "Elec_FireAlarm_BreakGlass.dxf"

        self.assertEqual(
            default_review_symbol_name(
                package_id="000F",
                primary_symbol_id="000F",
                classification_record=ClassificationRecord(),
            ),
            "Electrical FireAlarm BreakGlass",
        )


if __name__ == "__main__":
    unittest.main()
