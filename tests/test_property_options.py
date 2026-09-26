import unittest

from datetime import datetime, timezone

from symgov_backend.property_options import (
    UnknownDisciplineError,
    normalize_property_option_value,
    remember_property_option,
    standard_discipline_options,
    standard_discipline_value,
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


class _NoQuerySession:
    """A discipline save must never read or write remembered options."""

    def query(self, *entities):
        raise AssertionError("discipline options are not remembered")

    def add(self, instance):
        raise AssertionError("discipline options are not remembered")


class StandardDisciplineTests(unittest.TestCase):
    """X-04: disciplines are the Catalog's fixed list, never remembered text."""

    def test_the_options_are_the_catalog_list(self):
        options = standard_discipline_options()
        self.assertEqual(len(options), 11)
        self.assertIn("Civil / Structural", options)
        self.assertNotIn("Piping", options)

    def test_a_saved_legacy_spelling_is_stored_under_its_standard_name(self):
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        for value, expected in [
            ("Piping", "Piping / P&ID"),
            ("instrumentation", "Instrumentation & Controls"),
            ("Process_instrumentation", "Instrumentation & Controls"),
            ("general", "General / Annotation"),
            ("Mechanical", "Mechanical"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    remember_property_option(_NoQuerySession(), field_name="discipline", value=value, now=now),
                    expected,
                )

    def test_an_empty_discipline_stays_empty_and_an_unknown_one_is_refused(self):
        self.assertIsNone(standard_discipline_value("  "))
        with self.assertRaises(UnknownDisciplineError) as raised:
            standard_discipline_value("Process Control")
        self.assertIn("Choose one of", str(raised.exception))
