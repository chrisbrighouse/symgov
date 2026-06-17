import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.routes.workspace import infer_split_item_seed_properties


class ReviewSymbolPropertySeedingTests(unittest.TestCase):
    def test_split_item_seed_properties_humanize_filename_and_infer_discipline(self):
        split_item = SimpleNamespace(
            proposed_symbol_name="Elec_SmallPower_SO_Single_Unswitched_110VIndustrial Region 01",
            file_name="elec-smallpower-so-single-unswitched-110vindustrial-region-01.png",
            payload_json={
                "proposed_symbol_name": "Elec_SmallPower_SO_Single_Unswitched_110VIndustrial Region 01",
                "file_name": "elec-smallpower-so-single-unswitched-110vindustrial-region-01.png",
                "workspace_display_name": "0042-1",
            },
        )

        seeded = infer_split_item_seed_properties(split_item)

        self.assertEqual(seeded["name"], "Electrical SmallPower So Single Unswitched 110VIndustrial")
        self.assertEqual(seeded["discipline"], "Electrical")

    def test_split_item_seed_properties_avoids_generic_region_only_names(self):
        split_item = SimpleNamespace(
            proposed_symbol_name="Region 01",
            file_name="region-01.png",
            payload_json={"workspace_display_name": "0042-1"},
        )

        seeded = infer_split_item_seed_properties(split_item)

        self.assertIsNone(seeded["name"])
        self.assertIsNone(seeded["discipline"])


if __name__ == "__main__":
    unittest.main()
