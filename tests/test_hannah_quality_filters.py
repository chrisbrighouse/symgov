import pathlib
import sys
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(SCRIPTS_ROOT))

from run_hannah_curation import build_candidate, candidate_is_auto_attachable


class HannahQualityFilterTests(unittest.TestCase):
    def test_rejects_duckduckgo_asset_icons(self):
        symbol = {
            "symbol_id": "symbol-1",
            "symbol_revision_id": "revision-1",
            "published_page_id": "page-1",
            "symbol_slug": "gate-valve",
            "name": "Gate Valve",
            "category": "Valves",
            "discipline": "Piping",
        }
        page = {
            "pageid": "ddg-icon",
            "title": "DDG Result",
            "imageinfo": [
                {
                    "url": "//duckduckgo.com/assets/icons/meta/DDG-icon_256x256.png",
                    "descriptionshorturl": "//duckduckgo.com/assets/icons/meta/DDG-icon_256x256.png",
                    "extmetadata": {"LicenseShortName": {"value": "Needs Review (DDG)"}},
                }
            ],
        }

        self.assertIsNone(build_candidate(symbol, page, []))

    def test_rejects_news_or_political_wikimedia_false_positive(self):
        symbol = {
            "symbol_id": "symbol-2",
            "symbol_revision_id": "revision-2",
            "published_page_id": "page-2",
            "symbol_slug": "normally-closed",
            "name": "Normally Closed",
            "category": "Valves",
            "discipline": "Piping",
        }
        page = {
            "pageid": 123,
            "title": "File:Responsibility for war crimes of the Russian military is inevitable - address by the President of Ukraine.jpg",
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f1/ukraine-president-address.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Responsibility_for_war_crimes.jpg",
                    "mime": "image/jpeg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY 4.0"},
                        "UsageTerms": {"value": "Creative Commons Attribution"},
                        "ImageDescription": {"value": "President of Ukraine address about Russian military war crimes."},
                    },
                }
            ],
        }

        self.assertIsNone(build_candidate(symbol, page, []))

    def test_accepts_equipment_photo_with_symbol_match_and_low_risk_license(self):
        symbol = {
            "symbol_id": "symbol-3",
            "symbol_revision_id": "revision-3",
            "published_page_id": "page-3",
            "symbol_slug": "gate-valve",
            "name": "Gate Valve",
            "category": "Valves",
            "discipline": "Piping",
        }
        page = {
            "pageid": 456,
            "title": "File:Industrial gate valve piping equipment photograph.jpg",
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/industrial-gate-valve-photograph.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Industrial_gate_valve.jpg",
                    "mime": "image/jpeg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "UsageTerms": {"value": "Creative Commons Attribution"},
                        "ImageDescription": {"value": "Industrial gate valve installed in piping equipment."},
                    },
                }
            ],
        }

        candidate = build_candidate(symbol, page, [])

        self.assertIsNotNone(candidate)
        self.assertGreaterEqual(candidate["relevance_score"], 0.7)
        self.assertTrue(candidate_is_auto_attachable(candidate))

    def test_low_risk_license_alone_is_not_enough_to_auto_attach(self):
        candidate = {
            "rights_status": "low_risk",
            "relevance_score": 0.35,
            "quality_reasons": ["low_symbol_relevance"],
        }

        self.assertFalse(candidate_is_auto_attachable(candidate))


if __name__ == "__main__":
    unittest.main()
