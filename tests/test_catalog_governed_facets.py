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
    GOVERNED_SECONDARY_DISCIPLINES_COLUMN_SQL,
    PUBLISHED_SYMBOLS_SQL,
    PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL,
    RIGHTS_ATTRIBUTION_COLUMN_SQL,
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


def test_the_served_rows_sql_counts_only_proposed_and_verified_assignments():
    for column in (GOVERNED_DISCIPLINE_COLUMN_SQL, GOVERNED_CATEGORY_COLUMN_SQL):
        assert "src.status IN ('proposed', 'verified')" in column
        assert column in PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
    assert RIGHTS_ATTRIBUTION_COLUMN_SQL in PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
    # Only an approved rights record supplies attribution.
    assert "rr.decision_status = 'approved'" in RIGHTS_ATTRIBUTION_COLUMN_SQL


def test_the_base_published_sql_is_left_exactly_as_it_was():
    """Visibility tests and candidate scans use it directly, on databases that
    may predate the tables the served-rows columns read."""
    for needle in ("symbol_revision_classifications", "rights_records", "source_package_entries", "governed_discipline", "governed_category"):
        assert needle not in PUBLISHED_SYMBOLS_SQL
    assert PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL.startswith(PUBLISHED_SYMBOLS_SQL[: PUBLISHED_SYMBOLS_SQL.index("AS last_updated_at")])


def _fire_damper(governed):
    return {
        "name": "Fire Damper",
        "displayName": "S-150",
        "category": "Equipment",
        "discipline": "HVAC",
        "summary": "Fire damper, from the Wikimedia Commons import.",
        "governedTaxonomy": governed,
    }


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_verified_secondary_discipline_is_listed_beside_the_primary(taxonomy):
    governed = {"discipline": True, "category": True, "secondaryDisciplines": ["Fire & Life Safety"]}
    result = taxonomy(_fire_damper(governed))
    assert set(result["disciplines"]) == {"HVAC", "Fire & Life Safety"}


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_no_secondary_discipline_leaves_the_primary_alone(taxonomy):
    result = taxonomy(_fire_damper({"discipline": True, "category": True}))
    assert result["disciplines"] == ["HVAC"]


@pytest.mark.parametrize("taxonomy", [browser_taxonomy, api_taxonomy])
def test_a_secondary_equal_to_the_primary_is_not_duplicated(taxonomy):
    governed = {"discipline": True, "category": True, "secondaryDisciplines": ["HVAC"]}
    assert taxonomy(_fire_damper(governed))["disciplines"] == ["HVAC"]


def test_secondary_disciplines_come_from_the_published_row_column():
    row = SimpleNamespace(
        governed_discipline=True,
        governed_category=True,
        governed_secondary_disciplines=["Fire & Life Safety"],
    )
    assert governed_taxonomy_for_row(row)["secondaryDisciplines"] == ["Fire & Life Safety"]
    row.governed_secondary_disciplines = []
    assert "secondaryDisciplines" not in governed_taxonomy_for_row(row)


def test_the_secondary_column_reads_verified_secondary_discipline_assignments_only():
    sql = " ".join(GOVERNED_SECONDARY_DISCIPLINES_COLUMN_SQL.split())
    assert "assignment_role = 'secondary'" in sql
    assert "src.status = 'verified'" in sql
    assert "scheme_code = 'ENGINEERING-DISCIPLINE'" in sql
    assert GOVERNED_SECONDARY_DISCIPLINES_COLUMN_SQL in PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
    assert GOVERNED_SECONDARY_DISCIPLINES_COLUMN_SQL not in PUBLISHED_SYMBOLS_SQL
