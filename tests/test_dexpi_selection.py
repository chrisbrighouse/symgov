"""Cover for DEXPI pilot symbol selection (WP2).

Selection is a pure function of converter manifests, so almost everything here
runs on small synthetic manifests rather than the corpus.  One test drives the
real reference P&ID through the converter to prove the two halves fit.

See `docs/plans/2026-09-22-dexpi-symbol-library-pilot-implementation-plan.md`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FIXTURE_ROOT = REPO_ROOT / "integrations" / "dexpi" / "fixtures"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.services.dexpi_converter import convert_dexpi  # noqa: E402
from symgov_backend.services.dexpi_selection import (  # noqa: E402
    EXCLUSION_NO_NAMING_BASIS,
    NAME_BASIS_COMPONENT_CLASS,
    NAME_BASIS_REFERENCE,
    normalise_registration,
    select_symbols,
    vendor_for,
)

ATTRIBUTION = {
    "creator": "DEXPI e.V. and corpus contributors",
    "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
    "source": "https://gitlab.com/dexpi/TrainingTestCases",
}


def symbol(signature, name, *, component_class=None, element="Equipment", registration=None):
    return {
        "component_name": name,
        "component_class": component_class,
        "dexpi_element": element,
        "registration_number": registration,
        "geometry_signature": signature,
        "svg": f"{name}.svg",
        "svg_sha256": "a" * 64,
    }


def manifest(filename, symbols, *, sha="b" * 64, units="mm"):
    return {
        "source_filename": filename,
        "source_sha256": sha,
        "declared_units": units,
        "symbols": symbols,
    }


def selected_named(result, name):
    for item in result["selected"]:
        if item["canonical_name"] == name:
            return item
    raise AssertionError(f"no selected symbol named {name}; got {[i['canonical_name'] for i in result['selected']]}")


# --------------------------------------------------------------------------
# Vendor attribution


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("C01V04-VER.EX01.xml", "VER"),
        ("I11V01_AUD.EX01.xml", "AUD"),          # underscore form
        ("P04V01-SAG.EX01.XML", "SAG"),          # uppercase extension
        ("C01V01-HEX.EX01.xml", "HEX"),
        ("no-vendor-code-here", None),
    ],
)
def test_the_vendor_is_read_from_the_filename(filename, expected):
    assert vendor_for(filename) == expected


# --------------------------------------------------------------------------
# ISO registration numbers


def test_the_editioned_spelling_yields_an_edition():
    parsed = normalise_registration("ISO10628:2012-2322-A")
    assert parsed["standard_code"] == "ISO 10628"
    assert parsed["edition"] == "2012"
    assert parsed["symbol_identifier"] == "2322-A"
    assert parsed["raw"] == "ISO10628:2012-2322-A"


def test_the_other_spelling_yields_no_edition_rather_than_a_guessed_one():
    """`ISO10628-X2322A-A01` states no edition. No geometry in the corpus
    carries both spellings, so nothing establishes that it means 2012."""
    parsed = normalise_registration("ISO10628-X2322A-A01")
    assert parsed["standard_code"] == "ISO 10628"
    assert parsed["edition"] is None
    assert parsed["symbol_identifier"] == "X2322A-A01"


def test_an_unrecognised_registration_is_carried_verbatim_not_dropped():
    parsed = normalise_registration("SOMETHING-ELSE-1")
    assert parsed["standard_code"] is None
    assert parsed["raw"] == "SOMETHING-ELSE-1"
    assert parsed["symbol_identifier"] == "SOMETHING-ELSE-1"


def test_an_absent_registration_is_none():
    assert normalise_registration(None) is None
    assert normalise_registration("   ") is None


# --------------------------------------------------------------------------
# D1: the naming rule


def test_a_reference_name_beats_a_class_name():
    result = select_symbols(
        [
            (manifest("C01V04-VER.EX01.xml", [symbol("sig1", "BALL_VALVE_SHAPE")]), "a/C01V04-VER.EX01.xml"),
            (manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "hexValve", component_class="BallValve")]), "b.xml"),
        ]
    )
    item = selected_named(result, "BALL_VALVE_SHAPE")
    assert item["name_basis"] == NAME_BASIS_REFERENCE


def test_a_class_name_is_used_when_no_reference_drawing_exists():
    result = select_symbols(
        [(manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "hexValve", component_class="BallValve")]), "b.xml")]
    )
    item = selected_named(result, "BallValve")
    assert item["name_basis"] == NAME_BASIS_COMPONENT_CLASS


def test_a_geometry_with_no_naming_basis_is_excluded_and_recorded():
    """D1: excluded, never renamed into existence."""
    result = select_symbols(
        [(manifest("I11V01_AUD.EX01.xml", [symbol("sig1", "A5052ContRoomXMP_20363")]), "c.xml")]
    )
    assert result["selected"] == []
    assert len(result["excluded"]) == 1
    exclusion = result["excluded"][0]
    assert exclusion["reason"] == EXCLUSION_NO_NAMING_BASIS
    assert exclusion["observed_component_names"] == ["A5052ContRoomXMP_20363"]
    assert exclusion["vendors"] == ["AUD"]


def test_nozzles_are_not_catalogue_symbols():
    result = select_symbols(
        [
            (
                manifest(
                    "C01V04-VER.EX01.xml",
                    [
                        symbol("sig1", "NOZZLE_SHAPE", element="Nozzle"),
                        symbol("sig2", "BALL_VALVE_SHAPE"),
                    ],
                ),
                "a.xml",
            )
        ]
    )
    assert [item["canonical_name"] for item in result["selected"]] == ["BALL_VALVE_SHAPE"]


def test_a_failed_conversion_is_not_selected():
    failed = {"component_name": "BROKEN", "status": "failed", "warnings": []}
    result = select_symbols([(manifest("C01V04-VER.EX01.xml", [failed]), "a.xml")])
    assert result["selected"] == []
    assert result["excluded"] == []


# --------------------------------------------------------------------------
# Collapsing occurrences


def test_the_same_geometry_under_many_names_becomes_one_symbol():
    result = select_symbols(
        [
            (manifest("C01V04-VER.EX01.xml", [symbol("sig1", "TANK_SHAPE")]), "a.xml"),
            (manifest("C01V01-SAG.EX01.XML", [symbol("sig1", "19", component_class="Tank")]), "b.xml"),
            (manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "hexTank", component_class="Tank")]), "c.xml"),
        ]
    )
    assert len(result["selected"]) == 1
    item = result["selected"][0]
    assert item["occurrence_count"] == 3
    # Every vendor's name survives; under D3 these become concept aliases.
    assert item["observed_component_names"] == ["19", "TANK_SHAPE", "hexTank"]
    assert item["vendors"] == ["HEX", "SAG", "VER"]


def test_the_reference_drawing_is_the_one_ingested():
    result = select_symbols(
        [
            (manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "hexTank", component_class="Tank")]), "hex.xml"),
            (manifest("C01V04-VER.EX01.xml", [symbol("sig1", "TANK_SHAPE")]), "ver.xml"),
        ]
    )
    item = result["selected"][0]
    assert item["source_path"] == "ver.xml"
    assert item["provider_entry_identifier"] == "TANK_SHAPE"


def test_disagreeing_classes_are_reported_rather_than_resolved_silently():
    result = select_symbols(
        [
            (manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "a", component_class="Pump")]), "a.xml"),
            (manifest("C01V01-SAG.EX01.XML", [symbol("sig1", "b", component_class="CentrifugalPump")]), "b.xml"),
        ]
    )
    assert result["selected"][0]["component_classes"] == ["CentrifugalPump", "Pump"]


# --------------------------------------------------------------------------
# Name disambiguation


def test_names_shared_by_several_geometries_are_disambiguated():
    """The corpus yields 11 distinct geometries whose only name is `Tank`."""
    result = select_symbols(
        [
            (
                manifest(
                    "C01V01-HEX.EX01.xml",
                    [
                        symbol("sig1", "a", component_class="Tank"),
                        symbol("sig2", "b", component_class="Tank"),
                    ],
                ),
                "hex.xml",
            ),
            (manifest("C01V01-SAG.EX01.XML", [symbol("sig3", "c", component_class="Tank")]), "sag.xml"),
        ]
    )
    names = sorted(item["canonical_name"] for item in result["selected"])
    assert names == ["Tank (HEX 1)", "Tank (HEX 2)", "Tank (SAG 1)"]
    assert len(set(names)) == 3
    # The base name survives, because that is what maps to the concept under D3.
    assert {item["base_name"] for item in result["selected"]} == {"Tank"}
    assert all(item["name_disambiguated"] for item in result["selected"])


def test_a_unique_name_is_left_alone():
    result = select_symbols(
        [(manifest("C01V01-HEX.EX01.xml", [symbol("sig1", "a", component_class="Tank")]), "hex.xml")]
    )
    item = result["selected"][0]
    assert item["canonical_name"] == "Tank"
    assert item["name_disambiguated"] is False


# --------------------------------------------------------------------------
# The summary WP3 and WP4 read


def test_the_concept_count_covers_every_class_not_one_per_symbol():
    result = select_symbols(
        [
            (
                manifest(
                    "C01V01-HEX.EX01.xml",
                    [
                        symbol("sig1", "a", component_class="Tank"),
                        symbol("sig2", "b", component_class="BallValve"),
                    ],
                ),
                "hex.xml",
            )
        ]
    )
    assert result["summary"]["concept_count"] == 2
    assert result["summary"]["concepts"] == ["BallValve", "Tank"]


def test_registrations_are_summarised_by_whether_an_edition_is_pinned():
    result = select_symbols(
        [
            (
                manifest(
                    "C01V04-VER.EX01.xml",
                    [
                        symbol("sig1", "A_SHAPE", registration="ISO10628:2012-2322-A"),
                        symbol("sig2", "B_SHAPE", registration="ISO10628-X2322A-A01"),
                        symbol("sig3", "C_SHAPE"),
                    ],
                ),
                "a.xml",
            )
        ]
    )
    assert result["summary"]["with_registration"] == 2
    assert result["summary"]["with_editioned_registration"] == 1


# --------------------------------------------------------------------------
# Converter and selection together


def test_the_reference_pid_selects_its_named_shapes(tmp_path):
    converted = convert_dexpi(
        FIXTURE_ROOT / "C01V04-VER.EX01.xml", tmp_path, attribution=ATTRIBUTION
    )
    result = select_symbols([(converted, "dexpi 1.3/example pids/C01V04-VER.EX01.xml")])

    assert result["summary"]["selected_count"] > 0
    assert result["summary"]["excluded_count"] == 0
    # Every one of them is a reference drawing, so every name is a `*_SHAPE`.
    assert all(item["name_basis"] == NAME_BASIS_REFERENCE for item in result["selected"])

    pump = selected_named(result, "CENTRIFUGAL_PUMP_SHAPE")
    assert pump["vendors"] == ["VER"]
    assert pump["source_path"] == "dexpi 1.3/example pids/C01V04-VER.EX01.xml"
    assert pump["source_sha256"] == converted["source_sha256"]
    assert pump["registrations"][0]["edition"] == "2012"
    assert pump["registrations"][0]["symbol_identifier"] == "2322-A"


def test_nozzles_in_the_reference_pid_are_dropped(tmp_path):
    converted = convert_dexpi(
        FIXTURE_ROOT / "C01V04-VER.EX01.xml", tmp_path, attribution=ATTRIBUTION
    )
    result = select_symbols([(converted, "a.xml")])
    assert "NOZZLE_SHAPE" not in {item["canonical_name"] for item in result["selected"]}
    assert any(s["component_name"] == "NOZZLE_SHAPE" for s in converted["symbols"])
