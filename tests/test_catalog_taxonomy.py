import unittest

from symgov_backend.catalog_taxonomy import (
    available_formats_for_symbol,
    catalog_taxonomy_for_symbol,
    normalize_catalog_category,
    normalize_catalog_discipline,
    use_cases_for_formats,
)


FIRE_ALARM_SYMBOL = {
    "id": "smoke-detector",
    "displayName": "007F-2",
    "name": "Smoke Detector",
    "category": "symbol",
    "discipline": "Electrical",
    "keywords": ["fire alarm", "detector"],
    "downloads": ["smoke-detector.dxf", "smoke-detector.png"],
    "downloadAssets": [
        {"format": "dxf", "filename": "smoke-detector.dxf"},
        {"format": "png", "filename": "smoke-detector.png"},
    ],
}


class CatalogTaxonomyTests(unittest.TestCase):
    def test_normalizes_raw_discipline_values_into_catalog_groups(self) -> None:
        self.assertEqual(
            normalize_catalog_discipline("process_instrumentation"),
            ["Instrumentation & Controls", "Piping / P&ID"],
        )
        self.assertEqual(normalize_catalog_discipline("general"), ["General / Annotation"])
        self.assertEqual(normalize_catalog_discipline("Electrical"), ["Electrical"])

    def test_normalizes_category_from_raw_value_and_symbol_context(self) -> None:
        self.assertEqual(
            normalize_catalog_category("symbol", FIRE_ALARM_SYMBOL),
            ["Fire Alarm Devices", "Sensors / Detectors", "Drawing Symbols"],
        )
        self.assertEqual(
            normalize_catalog_category(
                "Gate Valves",
                {"name": "Gate Valve", "keywords": ["P&ID", "valve"]},
            ),
            ["Valves"],
        )
        self.assertEqual(normalize_catalog_category("symbol_sheet", {}), ["Drawing Symbols"])

    def test_extracts_formats_from_payload_and_manifest_assets(self) -> None:
        symbol = {
            "format": "image/svg+xml",
            "contentType": "image/png",
            "downloads": ["legacy/call-point.dxf"],
            "downloadAssets": [
                {"filename": "call-point.dwg"},
                {"content_type": "application/pdf"},
            ],
            "payload": {
                "source_format": "zip",
                "downloads": [
                    "package.ifc",
                    {"object_key": "derived/call-point.rfa"},
                    {"contentType": "image/jpeg"},
                ],
            },
        }

        self.assertEqual(
            available_formats_for_symbol(symbol),
            ["DXF", "DWG", "SVG", "PNG", "JPG", "PDF", "RFA", "IFC", "ZIP"],
        )

    def test_derives_use_cases_from_available_formats(self) -> None:
        self.assertEqual(
            use_cases_for_formats(["dxf", "png", "svg"]),
            ["Insert into CAD drawing", "Mark up / annotate drawing", "Use in PDF/report"],
        )

    def test_catalog_taxonomy_preserves_raw_values_separately(self) -> None:
        taxonomy = catalog_taxonomy_for_symbol(FIRE_ALARM_SYMBOL)

        self.assertEqual(taxonomy["disciplines"], ["Electrical", "Fire & Life Safety"])
        self.assertEqual(
            taxonomy["categories"],
            ["Fire Alarm Devices", "Sensors / Detectors", "Drawing Symbols"],
        )
        self.assertEqual(taxonomy["available_formats"], ["DXF", "PNG"])
        self.assertEqual(
            taxonomy["use_cases"],
            ["Insert into CAD drawing", "Mark up / annotate drawing", "Use in PDF/report"],
        )
        self.assertEqual(taxonomy["raw_disciplines"], ["Electrical"])
        self.assertEqual(taxonomy["raw_categories"], ["symbol"])


if __name__ == "__main__":
    unittest.main()
