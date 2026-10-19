import json
import tempfile
import unittest
from pathlib import Path

from symgov_backend.hannah_photos import (
    MAX_UNMATCHED_CHECKS,
    MAX_VISION_CHECKS_PER_CONCEPT,
    Candidate,
    TargetView,
    candidate_from_commons_page,
    check_candidates,
    distinctive_words,
    gather_candidates,
    parse_verdict,
    prefilter_reasons,
    rank_and_propose,
    score_verdict,
    text_relevance,
)


def view(**overrides):
    values = {
        "concept_id": "00000000-0000-0000-0000-000000000001",
        "concept_code": "SGC-00000387",
        "name": "HEPA filter",
        "definition": "HEPA filter: a P&ID symbol concept.",
        "search_terms": ["hepa filter", "hepa air filter"],
        "commons_category": "Filters",
    }
    values.update(overrides)
    return TargetView(**values)


def candidate(title, *, source="commons_category", mime="image/jpeg", width=1200, height=900, description=None):
    return Candidate(
        source=source, source_url=f"https://commons.example/{title}", image_url=f"https://upload.example/{title}",
        thumb_url=f"https://thumb.example/{title}", title=title, author=None, license_label=None, license_url=None,
        width=width, height=height, mime=mime, description=description,
    )


def page(title, **info):
    return {
        "title": f"File:{title}",
        "imageinfo": [
            {
                "url": f"https://upload.example/{title}",
                "thumburl": f"https://thumb.example/{title}",
                "descriptionurl": f"https://commons.example/{title}",
                "width": 1200,
                "height": 900,
                "mime": "image/jpeg",
                **info,
            }
        ],
    }


VERDICT = {
    "is_photograph": True, "shows_concept": "yes", "depicts": "a filter", "is_diagram_or_symbol": False,
    "text_or_watermark_heavy": False, "quality": 4, "confidence": 0.9, "reason": "A photo of a filter.",
}


class CommonsParsingTests(unittest.TestCase):
    def test_credit_fields_lose_their_markup(self):
        parsed = candidate_from_commons_page(
            page(
                "Filter.jpg",
                extmetadata={
                    "Artist": {"value": '<a href="//commons.example/User:Ann">Ann &amp; Bo</a>'},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "ImageDescription": {"value": "<p>A HEPA filter</p>"},
                },
            ),
            "commons_category",
        )
        self.assertEqual((parsed.title, parsed.author, parsed.license_label), ("Filter.jpg", "Ann & Bo", "CC BY-SA 4.0"))
        self.assertEqual(parsed.description, "A HEPA filter")

    def test_a_page_without_imageinfo_is_skipped(self):
        self.assertIsNone(candidate_from_commons_page({"title": "File:x.jpg"}, "commons_search"))


class PrefilterTests(unittest.TestCase):
    def test_format_size_shape_and_title_are_checked(self):
        self.assertEqual(prefilter_reasons(candidate("a.jpg")), [])
        self.assertIn("not_a_photo_format", prefilter_reasons(candidate("a.svg", mime="image/svg+xml")))
        self.assertIn("too_small", prefilter_reasons(candidate("a.jpg", width=300, height=900)))
        self.assertIn("extreme_shape", prefilter_reasons(candidate("a.jpg", width=4000, height=1000)))
        self.assertIn("title_names_a_drawing", prefilter_reasons(candidate("Valve symbol.jpg")))


class RelevanceTests(unittest.TestCase):
    def test_a_broader_category_name_does_not_count(self):
        self.assertEqual(distinctive_words(["hepa filter", "hepa air filter"], "Filters"), {"hepa", "air"})

    def test_a_category_that_is_the_concept_keeps_every_word(self):
        self.assertEqual(distinctive_words(["ball valve"], "Ball valves"), {"ball", "valve"})

    def test_relevance_counts_distinct_words_in_title_and_description(self):
        words = {"hepa", "air"}
        self.assertEqual(text_relevance(candidate("Kitchen filter.jpg"), words), 0)
        self.assertEqual(text_relevance(candidate("HEPA unit.jpg", description="Air cleaner"), words), 2)


class VerdictTests(unittest.TestCase):
    def test_values_are_clamped_and_unknown_answers_are_no(self):
        parsed = parse_verdict(json.dumps({**VERDICT, "quality": 9, "confidence": -1, "shows_concept": "maybe"}))
        self.assertEqual((parsed["quality"], parsed["confidence"], parsed["shows_concept"]), (5, 0, "no"))

    def test_hard_rejects_and_scores(self):
        self.assertEqual(score_verdict(parse_verdict(json.dumps(VERDICT))), (0.72, []))
        partial = parse_verdict(json.dumps({**VERDICT, "shows_concept": "partial"}))
        self.assertEqual(score_verdict(partial), (0.504, []))
        drawing = parse_verdict(json.dumps({**VERDICT, "is_photograph": False, "is_diagram_or_symbol": True}))
        self.assertEqual(score_verdict(drawing), (None, ["vision:not_a_photograph", "vision:diagram_or_symbol"]))


class ProposeTests(unittest.TestCase):
    def test_the_best_open_rows_fill_the_slots_approved_photos_leave(self):
        rows = [
            {"status": "approved", "score": 0.9, "rank": 1},
            {"status": "candidate", "score": 0.6, "rank": None},
            {"status": "proposed", "score": 0.8, "rank": 1},
            {"status": "rejected", "score": 0.95, "rank": None},
            {"status": "candidate", "score": 0.2, "rank": None},
        ]
        rank_and_propose(rows)
        self.assertEqual([row["status"] for row in rows], ["approved", "candidate", "proposed", "rejected", "candidate"])
        self.assertEqual([row["rank"] for row in rows], [1, 2, 1, None, 3])

    def test_a_low_score_is_never_proposed(self):
        rows = [{"status": "candidate", "score": 0.1, "rank": None}]
        rank_and_propose(rows)
        self.assertEqual(rows[0]["status"], "candidate")


class GatherTests(unittest.TestCase):
    def test_sources_run_in_order_unique_by_url_and_one_failure_keeps_the_rest(self):
        calls = []

        def http(url, params):
            calls.append(params.get("generator") or params.get("titles") or params.get("prop"))
            if params.get("generator") == "categorymembers":
                return {"query": {"pages": {"1": page("Shared.jpg"), "2": page("Category only.jpg")}}}
            if params.get("generator") == "search":
                return {"query": {"pages": {"1": page("Shared.jpg"), "2": page(f"Search {params['gsrsearch']}.jpg")}}}
            if "titles" in params:
                raise OSError("commons down")
            return {}

        found, errors = gather_candidates(
            view(wikidata_image="Lead.jpg", search_terms=["hepa filter"]), http, pause=lambda: None
        )

        self.assertEqual(errors, ["wikidata_image: OSError"])
        self.assertEqual(
            [(item.source, item.title) for item in found],
            [
                ("commons_category", "Shared.jpg"),
                ("commons_category", "Category only.jpg"),
                ("commons_search", "Search hepa filter filetype:bitmap.jpg"),
            ],
        )


class CheckTests(unittest.TestCase):
    def run_check(self, candidates, *, max_vision_calls=100, reply=None, fetch=None):
        asked = []

        def complete(messages):
            asked.append(json.loads(messages[1]["content"][0]["text"])["image_caption"])
            return reply or json.dumps(VERDICT)

        counts = check_candidates(
            view(), candidates, fetch_bytes=fetch or (lambda url: (url.encode(), "image/jpeg")),
            complete_vision=complete, max_vision_calls=max_vision_calls, pause=lambda: None,
        )
        return counts, asked

    def test_matching_titles_go_first_and_unmatched_ones_are_capped(self):
        unmatched = [candidate(f"Kitchen {index}.jpg") for index in range(MAX_UNMATCHED_CHECKS + 2)]
        matched = candidate("HEPA unit.jpg")
        counts, asked = self.run_check(unmatched + [matched])

        self.assertEqual(asked[0], "HEPA unit.jpg")
        self.assertEqual(counts["vision_calls"], 1 + MAX_UNMATCHED_CHECKS)
        self.assertEqual([item.filter_reasons for item in unmatched[-2:]], [["no_term_match"], ["no_term_match"]])

    def test_the_budget_and_the_per_concept_limit_leave_the_rest_unchecked(self):
        many = [candidate(f"HEPA {index}.jpg") for index in range(MAX_VISION_CHECKS_PER_CONCEPT + 3)]
        counts, _ = self.run_check(many)
        self.assertEqual((counts["vision_calls"], counts["unchecked"]), (MAX_VISION_CHECKS_PER_CONCEPT, 3))

        counts, _ = self.run_check([candidate("HEPA a.jpg"), candidate("HEPA b.jpg")], max_vision_calls=1)
        self.assertEqual((counts["vision_calls"], counts["unchecked"]), (1, 1))

    def test_the_same_bytes_twice_are_a_duplicate(self):
        first, second = candidate("HEPA a.jpg"), candidate("HEPA b.jpg")
        counts, _ = self.run_check([first, second], fetch=lambda url: (b"same", "image/jpeg"))
        self.assertEqual((counts["vision_calls"], second.filter_reasons), (1, ["duplicate_image"]))

    def test_an_unreadable_reply_leaves_the_image_for_later(self):
        item = candidate("HEPA a.jpg")
        counts, _ = self.run_check([item], reply="no json")
        self.assertEqual((counts["vision_errors"], item.status, item.score), (1, "candidate", None))
        self.assertIn("vision failed", item.verdict["error"])

    def test_a_hard_reject_is_filtered_with_its_reasons(self):
        item = candidate("HEPA a.jpg")
        self.run_check([item], reply=json.dumps({**VERDICT, "is_photograph": False}))
        self.assertEqual((item.status, item.filter_reasons), ("filtered", ["vision:not_a_photograph"]))


class RetiredCardTests(unittest.TestCase):
    def test_a_per_symbol_card_is_cancelled_not_failed(self):
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "run_hannah_concept_curation.py"
        spec = importlib.util.spec_from_file_location("hannah_concept_runner_under_test", script)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory) / "card.json"
            card.write_text(json.dumps({"id": "c1", "agent_id": "hannah", "source_type": "published_symbol_photo_enrichment", "status": "queued"}))

            result = runner.process_queue_item(card, directory, persist_db=False)

            self.assertEqual(result["queue_item_status"], "cancelled")
            self.assertEqual(json.loads(card.read_text())["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
