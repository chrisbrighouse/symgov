import unittest

from symgov_backend.property_options import (
    normalize_property_option_value,
    property_option_key,
    resolve_property_option_display_value,
)


class PropertyOptionResolutionTests(unittest.TestCase):
    def test_resolve_property_option_uses_existing_short_phrase_for_close_match(self):
        resolved = resolve_property_option_display_value(
            "category",
            "gate valve",
            existing_options=["Cylinder", "Gate Valves", "Motor"],
        )

        self.assertEqual(resolved.value, "Gate Valves")
        self.assertFalse(resolved.created)
        self.assertEqual(resolved.normalized_key, "gatevalves")

    def test_resolve_property_option_allows_new_short_saved_list_phrase(self):
        resolved = resolve_property_option_display_value(
            "discipline",
            "Process Control",
            existing_options=["Mechanical", "Piping", "Process"],
        )

        self.assertEqual(resolved.value, "Process Control")
        self.assertTrue(resolved.created)
        self.assertEqual(resolved.normalized_key, "processcontrol")

    def test_property_option_normalization_compacts_case_and_punctuation(self):
        self.assertEqual(normalize_property_option_value("  process   control "), "Process Control")
        self.assertEqual(property_option_key("Process Control"), "processcontrol")


if __name__ == "__main__":
    unittest.main()
