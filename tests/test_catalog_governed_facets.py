"""Governed category and discipline win over the Catalog's keyword facet rules.

The keyword rules read "heat" in "Heat Exchanger" as a fire-alarm word and put
every heat exchanger under Fire & Life Safety. A symbol with a proposed or
verified discipline / category assignment now keeps its governed values.
"""

from types import SimpleNamespace

import pytest

from symgov_backend.catalog_facets import catalog_taxonomy_for_symbol as browser_taxonomy
from symgov_backend.catalog_search import row_taxonomy_input
from symgov_backend.catalog_taxonomy import catalog_taxonomy_for_symbol as api_taxonomy
from symgov_backend.published_catalog import (
    GOVERNED_CATEGORY_COLUMN_SQL,
    GOVERNED_DISCIPLINE_COLUMN_SQL,
    PUBLISHED_SYMBOLS_SQL,
    governed_taxonomy_for_row,
)


def _heat_exchanger(governed):
    row = {
        "name": "Heat Exchanger",
        "displayName": "S-99",
        "category": "Equipment",
        "discipline": "Piping / P&ID",
        "summary": "Heat Exchanger, converted from the DEXPI TrainingTestCases corpus.",
        "keywords": ["DEXPI", "HeatExchanger"],
    }
    if governed is not None:
        row["governedTaxonomy"] = governed
    return row


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_governed_heat_exchanger_is_not_fire_and_life_safety(taxonomy):
    result = taxonomy(_heat_exchanger({"discipline": True, "category": True}))
    assert result["disciplines"] == ["Piping / P&ID"]
    assert result["categories"] == ["Equipment"]


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_ungoverned_row_keeps_the_keyword_fallback(taxonomy):
    result = taxonomy(_heat_exchanger(None))
    assert "Fire & Life Safety" in result["disciplines"]
    assert "Fire Alarm Devices" in result["categories"]


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_each_field_is_governed_on_its_own(taxonomy):
    result = taxonomy(_heat_exchanger({"discipline": True, "category": False}))
    assert result["disciplines"] == ["Piping / P&ID"]
    assert "Fire Alarm Devices" in result["categories"]


def test_governed_flags_come_from_the_published_row_columns():
    row = SimpleNamespace(governed_discipline=True, governed_category=False)
    assert governed_taxonomy_for_row(row) == {"discipline": True, "category": False}
    assert governed_taxonomy_for_row(SimpleNamespace()) is None


def test_catalog_api_input_carries_the_flags():
    row = SimpleNamespace(
        payload_json={},
        canonical_name="Heat Exchanger",
        catalog_symbol_id="S-99",
        category="Equipment",
        discipline="Piping / P&ID",
        governed_discipline=True,
        governed_category=True,
    )
    assert row_taxonomy_input(row)["governedTaxonomy"] == {"discipline": True, "category": True}


def test_published_sql_counts_only_proposed_and_verified_assignments():
    for column in (GOVERNED_DISCIPLINE_COLUMN_SQL, GOVERNED_CATEGORY_COLUMN_SQL):
        assert "src.status IN ('proposed', 'verified')" in column
        assert column in PUBLISHED_SYMBOLS_SQL
