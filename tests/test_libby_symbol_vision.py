import importlib.util
from pathlib import Path
import unittest

import pytest


pytestmark = pytest.mark.external_workspace


RUNNER_PATH = Path("/data/symgov/scripts/run_libby_classification.py")
spec = importlib.util.spec_from_file_location("run_libby_classification", RUNNER_PATH)
libby = importlib.util.module_from_spec(spec)
spec.loader.exec_module(libby)


class LibbySymbolVisionTests(unittest.TestCase):
    def test_infer_classification_uses_filename_evidence_for_single_symbol_cases(self):
        artifact = libby.infer_classification(
            {
                "queue_item_id": "aqi-libby-fire-breakglass",
                "origin_file_name": "Elec_FireAlarm_BreakGlass.dxf",
                "declared_format": "dxf",
                "candidate_symbol_id": "ELEC-FIREALARM-BREAKGLASS-001",
                "package_symbol_grouping": "paired_dxf_raster_symbol",
            }
        )

        self.assertEqual(artifact["symbol_name"], "Electrical FireAlarm BreakGlass")
        self.assertEqual(artifact["discipline"], "Electrical")
        self.assertEqual(artifact["classification_summary"], "Electrical FireAlarm BreakGlass")
        self.assertIn("Electrical FireAlarm BreakGlass", artifact["aliases"])
        self.assertIn("BreakGlass", artifact["search_terms"])
        self.assertEqual(artifact["evidence"]["filename_inference"]["discipline_hint"], "Electrical")

    def test_parse_litellm_symbol_property_json_from_output_text(self):
        response = {
            "outputText": "```json\n{\"name\": \"Gate valve\", \"description\": \"Manual isolation valve symbol\", \"category\": \"gate valve\", \"discipline\": \"piping\"}\n```"
        }

        parsed = libby.parse_symbol_property_response(response)

        self.assertEqual(parsed["name"], "Gate valve")
        self.assertEqual(parsed["category"], "gate valve")
        self.assertEqual(parsed["discipline"], "piping")

    def test_apply_symbol_property_description_updates_classification_fields(self):
        artifact = {
            "category": "unclassified_symbol",
            "discipline": "instrumentation",
            "classification_summary": "old summary",
            "evidence": {},
            "evidence_trace": [],
            "taxonomy_terms_created": [],
            "confidence": 0.58,
        }
        llm_properties = {
            "name": "Gate Valve",
            "description": "A gate valve isolation symbol.",
            "category": "Gate Valves",
            "discipline": "Piping",
        }

        libby.apply_symbol_property_description(artifact, llm_properties)

        self.assertEqual(artifact["category"], "Gate Valves")
        self.assertEqual(artifact["discipline"], "Piping")
        self.assertEqual(artifact["symbol_name"], "Gate Valve")
        self.assertIn("A gate valve isolation symbol.", artifact["classification_summary"])
        self.assertEqual(artifact["evidence"]["llm_symbol_properties"], llm_properties)

    def test_litellm_symbol_property_prompt_includes_filename_hints(self):
        captured = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return {
                "outputText": '{"name":"Electrical FireAlarm BreakGlass","description":"Electrical fire alarm break glass symbol.","category":"Fire alarm symbols","discipline":"Electrical"}'
            }

        original_completion = libby.request_llm_completion
        libby.request_llm_completion = fake_completion
        try:
            properties = libby.call_symbol_property_review(
                b"fake-image",
                "image/png",
                submission_context={
                    "submission_batch_summary": "Fire alarm symbols for building services classification.",
                    "source_notes": "Installer pack from manual submission.",
                    "file_note": "Break glass call point variant.",
                    "contributor_declaration": "Operations team says these are electrical fire alarm symbols.",
                },
                filename_hints={
                    "original_filename": "Elec_FireAlarm_BreakGlass.dxf",
                    "inferred_name": "Electrical FireAlarm BreakGlass",
                    "discipline_hint": "Electrical",
                    "confidence": 0.91,
                },
            )
        finally:
            libby.request_llm_completion = original_completion

        content = captured["messages"][0]["content"]
        prompt = content[0]["text"]
        self.assertEqual(captured["provider"], "google")
        self.assertEqual(captured["use_case"], "symbol_property_vision")
        self.assertEqual(captured["service_name"], "libby")
        self.assertEqual(captured["request_kind"], "vision")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("Filename hints", prompt)
        self.assertIn("Elec_FireAlarm_BreakGlass.dxf", prompt)
        self.assertIn("Electrical FireAlarm BreakGlass", prompt)
        self.assertIn("Submission context", prompt)
        self.assertIn("Fire alarm symbols for building services classification", prompt)
        self.assertIn("Installer pack from manual submission", prompt)
        self.assertIn("advisory", prompt.lower())
        self.assertEqual(properties["discipline"], "Electrical")


if __name__ == "__main__":
    unittest.main()
