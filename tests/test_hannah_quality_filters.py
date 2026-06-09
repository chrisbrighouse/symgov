import pathlib
import sys
import unittest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1] / "backend"
SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(SCRIPTS_ROOT))

from run_hannah_curation import build_candidate, build_feedback_candidate, candidate_is_auto_attachable, search_images


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

    def test_feedback_candidate_records_search_failures_for_ui(self):
        symbol = {
            "symbol_id": "symbol-4",
            "symbol_revision_id": "revision-4",
            "published_page_id": "page-4",
            "symbol_slug": "gate-valve",
            "name": "Gate Valve",
            "category": "Valves",
            "discipline": "Piping",
        }

        feedback = build_feedback_candidate(
            symbol,
            status="no_candidate_found",
            source_url="https://commons.wikimedia.org/w/index.php?search=Gate%20Valve",
            title="No acceptable image candidates found for Gate Valve",
            description="Hannah searched the configured sources but did not receive any image-like results to score.",
            evidence={"feedback_type": "no_candidate_found", "searched_sources": ["Commons MediaSearch"]},
        )

        self.assertEqual(feedback["status"], "no_candidate_found")
        self.assertEqual(feedback["source_domain"], "commons.wikimedia.org")
        self.assertEqual(feedback["evidence"]["feedback_type"], "no_candidate_found")

    def test_search_images_combines_all_source_strategies(self):
        import run_hannah_curation as hannah

        symbol = {"name": "Gate Valve", "category": "Valves", "discipline": "Piping"}
        calls = []

        def empty_commons(symbol, trace):
            calls.append("commons")
            return []

        def one_media(symbol, trace):
            calls.append("media")
            return [hannah.image_page(source="media", image_url="https://example.com/media.jpg")]

        def one_wikipedia(symbol, trace):
            calls.append("wikipedia")
            return [hannah.image_page(source="wikipedia", image_url="https://example.com/wiki.jpg")]

        def duplicate_duckduckgo(symbol, trace):
            calls.append("duckduckgo")
            return [hannah.image_page(source="ddg", image_url="https://example.com/wiki.jpg")]

        original = (
            hannah.search_commons_images,
            hannah.search_commons_media_search,
            hannah.search_wikipedia_page_images,
            hannah.search_duckduckgo_images,
            hannah.time.sleep,
        )
        try:
            hannah.search_commons_images = empty_commons
            hannah.search_commons_media_search = one_media
            hannah.search_wikipedia_page_images = one_wikipedia
            hannah.search_duckduckgo_images = duplicate_duckduckgo
            hannah.time.sleep = lambda seconds: None

            results = search_images(symbol, [])
        finally:
            (
                hannah.search_commons_images,
                hannah.search_commons_media_search,
                hannah.search_wikipedia_page_images,
                hannah.search_duckduckgo_images,
                hannah.time.sleep,
            ) = original

        self.assertEqual(calls, ["commons", "media", "wikipedia", "duckduckgo"])
        self.assertEqual([item["imageinfo"][0]["url"] for item in results], ["https://example.com/media.jpg", "https://example.com/wiki.jpg"])


if __name__ == "__main__":
    unittest.main()
