"""The Catalog facet store's port of the browser rules.

`catalog_facets.py` must derive exactly what the browser derives, or the
Catalog's filters would disagree with the chips on its own cards once
searching moves to the database. The golden file is generated from the real
browser functions (`scripts/generate-catalog-facet-golden.mjs`) and checked by
`frontend/src/catalogFacetGolden.test.js` as well, so a change to either copy
fails one of the two suites.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.catalog_facets import (  # noqa: E402
    app_display_symbol_id,
    app_display_symbol_name,
    build_catalog_search_text,
    catalog_taxonomy_for_symbol,
    facet_values_for_row,
    natural_sort_key,
)

GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "catalog_browser_facets_golden.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[case["name"] for case in GOLDEN["cases"]])
def test_taxonomy_matches_the_browser(case):
    assert catalog_taxonomy_for_symbol(case["input"]) == case["expected"]["taxonomy"]


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[case["name"] for case in GOLDEN["cases"]])
def test_search_text_matches_the_browser(case):
    assert build_catalog_search_text(case["input"]) == case["expected"]["searchText"]


def test_the_golden_file_has_been_generated():
    assert all(case["expected"] for case in GOLDEN["cases"])


def test_stored_search_text_leaves_out_the_live_pack_and_page_fields():
    row = next(case["input"] for case in GOLDEN["cases"] if case["name"] == "public valve row")
    values = facet_values_for_row(row)
    assert "dexpi pilot" not in values["search_text"]
    assert "dexpi-1" not in values["search_text"]
    assert "p-014" not in values["search_text"]
    assert "gate valve" in values["search_text"]
    assert values["search_text"] == values["search_text"].lower()


def test_stored_values_for_a_public_row():
    row = next(case["input"] for case in GOLDEN["cases"] if case["name"] == "public valve row")
    values = facet_values_for_row(row)
    assert values["display_id"] == "S-000096-014"
    assert values["display_name"] == "Gate valve"
    assert values["disciplines"] == ["Piping / P&ID"]
    assert values["categories"] == ["Valves"]
    assert values["formats"] == ["DXF", "SVG"]
    assert values["use_cases"] == ["Insert into CAD drawing", "Mark up / annotate drawing", "Use in PDF/report"]
    # No served row has `symbolFamily`, so the browser shows the raw category.
    assert values["symbol_family"] == "valve"


# `displaySymbolId` in App.jsx, which the ID column shows and sorts on. It is
# not importable into the node test runner (App.jsx is JSX), so its cases are
# pinned here by hand.
@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"displayName": "S-000096-014", "slug": "gate-valve"}, "S-000096-014"),
        ({"displayName": "Acme strainer", "slug": "ab12-7"}, "ab12-7"),
        ({"displayName": "Acme strainer", "packageDisplayId": "AB12", "packageSymbolSequence": 3}, "AB12-3"),
        ({"packageDisplayId": "PKG", "packageSymbolSequence": 0}, "PKG-0"),
        ({"packageDisplayId": "PKG"}, "PKG"),
        ({"symbolId": "5d2c7a1e", "id": "x"}, "5d2c7a1e"),
        ({"id": "only-id"}, "only-id"),
        ({}, ""),
    ],
)
def test_app_display_symbol_id(record, expected):
    assert app_display_symbol_id(record) == expected


def test_app_display_symbol_name_prefers_the_served_name():
    assert app_display_symbol_name({"name": "Gate valve", "payload": {"name": "Other"}}) == "Gate valve"
    assert app_display_symbol_name({"payload": {"canonical_name": "Canonical"}}) == "Canonical"
    assert app_display_symbol_name({}) == ""


def test_natural_sort_key_orders_numbers_by_value():
    ordered = sorted(["SYM-10", "sym-2", "SYM-1", "SYM-002a"], key=natural_sort_key)
    assert ordered == ["SYM-1", "sym-2", "SYM-002a", "SYM-10"]
