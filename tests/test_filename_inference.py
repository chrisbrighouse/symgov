from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.filename_inference import infer_filename_metadata


def test_filename_inference_preserves_engineering_compounds_and_discipline_prefix():
    inferred = infer_filename_metadata("Elec_FireAlarm_BreakGlass.dxf")

    assert inferred["inferred_name"] == "Electrical FireAlarm BreakGlass"
    assert inferred["display_tokens"] == ["Electrical", "FireAlarm", "BreakGlass"]
    assert inferred["discipline_hint"] == "Electrical"
    assert inferred["confidence"] >= 0.85
    assert inferred["description_fallback"] == "Electrical FireAlarm BreakGlass"


def test_filename_inference_handles_delimited_mechanical_names():
    inferred = infer_filename_metadata("Mech_Pressure_Relief_Valve.svg")

    assert inferred["inferred_name"] == "Mechanical Pressure Relief Valve"
    assert inferred["discipline_hint"] == "Mechanical"
    assert inferred["display_tokens"] == ["Mechanical", "Pressure", "Relief", "Valve"]


def test_filename_inference_is_conservative_for_generic_sheet_names():
    inferred = infer_filename_metadata("sheet_01.jpg")

    assert inferred["inferred_name"] == "Sheet 01"
    assert inferred["discipline_hint"] is None
    assert inferred["confidence"] <= 0.45
    assert "generic_token" in inferred["evidence"]
