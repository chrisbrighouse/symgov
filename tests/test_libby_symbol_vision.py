import importlib.util
import json
import os
from pathlib import Path
import unittest


RUNNER_PATH = Path("/data/.openclaw/workspaces/libby/run_libby_classification.py")
spec = importlib.util.spec_from_file_location("run_libby_classification", RUNNER_PATH)
libby = importlib.util.module_from_spec(spec)
spec.loader.exec_module(libby)


class LibbySymbolVisionTests(unittest.TestCase):
    def test_parse_gemini_symbol_property_json_from_text_part(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "```json\n{\"name\": \"Gate valve\", \"description\": \"Manual isolation valve symbol\", \"category\": \"gate valve\", \"discipline\": \"piping\"}\n```"
                            }
                        ]
                    }
                }
            ]
        }

        parsed = libby.parse_gemini_symbol_property_response(response)

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


if __name__ == "__main__":
    unittest.main()
