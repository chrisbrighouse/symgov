import json
import unittest

from symgov_backend.hannah_targets import (
    CLASSIFY_BATCH_SIZE,
    ConceptScope,
    classify_concepts,
    fallback_search_terms,
    link_concept,
    parse_first_json_object,
    pick_wikidata_item,
    readable_name,
    short_definition,
    wikidata_queries,
    wikidata_details,
)


def scope(code="SGC-00000001", name="SeparatorCycloneType", aliases=("Separator, cyclone type",), kind="other"):
    return ConceptScope(
        concept_id="00000000-0000-0000-0000-000000000001",
        concept_code=code,
        concept_kind=kind,
        preferred_name=name,
        definition=(
            "Separator, cyclone type: a P&ID symbol concept. Its Wikimedia Commons description reads: "
            "\"Symbol of separator\". Created from the curated Wikimedia Commons P&ID symbol import."
        ),
        aliases=list(aliases),
        symbol_count=1,
    )


class SearchTermTests(unittest.TestCase):
    def test_fallback_terms_use_aliases_then_the_split_preferred_name(self):
        # The alias and the split name give the same words, so they count once.
        self.assertEqual(fallback_search_terms(scope()), ["separator cyclone type"])
        self.assertEqual(
            fallback_search_terms(scope(name="YGlobeValve", aliases=("Y-GLOBE VALVE",))),
            ["y globe valve"],
        )
        self.assertEqual(fallback_search_terms(scope(name="CentrifugalPump", aliases=())), ["centrifugal pump"])

    def test_codes_in_the_aliases_are_skipped_for_the_name_and_the_terms(self):
        coded = scope(name="BallValve", aliases=("X807128", "@30|M00|A50", "511", "BLIND_COVER_SHAPE"))
        self.assertEqual(readable_name(coded), "Ball Valve")
        self.assertEqual(fallback_search_terms(coded), ["ball valve"])
        self.assertEqual(readable_name(scope()), "Separator, cyclone type")

    def test_short_definition_drops_the_import_boilerplate(self):
        self.assertEqual(short_definition(scope().definition), "Separator, cyclone type: a P&ID symbol concept.")
        self.assertTrue(short_definition("word " * 200, limit=50).endswith("…"))


class ParseTests(unittest.TestCase):
    def test_the_first_object_wins_over_a_preamble_and_a_second_object(self):
        self.assertEqual(parse_first_json_object('Sure: {"a": 1}\n{"b": 2}'), {"a": 1})

    def test_a_reply_without_an_object_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_first_json_object("no json here")


class ClassifyTests(unittest.TestCase):
    def test_answers_are_read_cleaned_and_batched(self):
        scopes = [scope(code=f"SGC-{index:08d}") for index in range(CLASSIFY_BATCH_SIZE + 2)]
        calls = []

        def complete(messages):
            items = json.loads(messages[1]["content"])["concepts"]
            calls.append(len(items))
            return json.dumps(
                {
                    "concepts": [
                        {"code": item["code"], "photographable": "YES", "search_terms": ["Cyclone Separator", "x", "cyclone separator"]}
                        for item in items
                    ]
                }
            )

        results = classify_concepts(scopes, complete)

        self.assertEqual(calls, [CLASSIFY_BATCH_SIZE, 2])
        self.assertEqual(results["SGC-00000000"], {"photographable": "yes", "search_terms": ["cyclone separator"]})

    def test_an_unknown_answer_becomes_unsure_and_an_omitted_concept_is_an_error(self):
        first, second = scope(code="SGC-00000001"), scope(code="SGC-00000002")

        def complete(messages):
            return json.dumps({"concepts": [{"code": "SGC-00000001", "photographable": "maybe", "search_terms": []}]})

        results = classify_concepts([first, second], complete)

        self.assertEqual(results["SGC-00000001"]["photographable"], "unsure")
        self.assertIn("omitted", results["SGC-00000002"]["error"])

    def test_an_unreadable_reply_marks_its_batch_and_does_not_raise(self):
        results = classify_concepts([scope()], lambda messages: "not json")
        self.assertIn("classification failed", results["SGC-00000001"]["error"])


class WikidataTests(unittest.TestCase):
    CANDIDATES = [{"qid": "Q633570", "label": "cyclonic separation", "description": "a method"}]

    def test_a_pick_outside_the_candidates_is_refused(self):
        self.assertEqual(
            pick_wikidata_item(scope(), ["cyclone separator"], self.CANDIDATES, lambda m: '{"qid": "Q1", "confidence": 0.9}'),
            (None, None),
        )

    def test_a_pick_inside_the_candidates_is_kept_with_a_clamped_confidence(self):
        self.assertEqual(
            pick_wikidata_item(scope(), ["cyclone separator"], self.CANDIDATES, lambda m: '{"qid": "Q633570", "confidence": 3}'),
            ("Q633570", 1.0),
        )

    def test_details_read_the_image_category_and_english_page(self):
        payload = {
            "entities": {
                "Q42": {
                    "claims": {
                        "P18": [{"mainsnak": {"datavalue": {"value": "Cyclone separator.jpg"}}}],
                        "P373": [{"mainsnak": {"datavalue": {"value": "Cyclone separators"}}}],
                    },
                    "sitelinks": {"enwiki": {"title": "Cyclonic separation"}},
                }
            }
        }
        self.assertEqual(
            wikidata_details("Q42", lambda url, params: payload),
            {
                "image": "Cyclone separator.jpg",
                "commons_category": "Cyclone separators",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Cyclonic_separation",
            },
        )

    def test_queries_add_the_head_nouns_once(self):
        self.assertEqual(
            wikidata_queries(["anchor agitator", "paddle agitator", "tank mixer"]),
            ["anchor agitator", "paddle agitator", "agitator"],
        )
        self.assertEqual(wikidata_queries(["autoclave"]), ["autoclave"])

    def test_a_failed_lookup_is_reported_not_raised(self):
        def broken(url, params):
            raise OSError("network down")

        result = link_concept(scope(), ["cyclone separator"], complete=lambda m: "{}", http_get_json=broken, pause=lambda: None)
        self.assertIn("wikidata link failed", result["error"])

    def test_no_search_results_means_no_link_and_no_model_call(self):
        def complete(messages):
            raise AssertionError("the model should not be asked")

        result = link_concept(
            scope(), ["cyclone separator"], complete=complete, http_get_json=lambda url, params: {"search": []}, pause=lambda: None
        )
        self.assertEqual(result, {"qid": None, "candidates": 0})


if __name__ == "__main__":
    unittest.main()
