from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VLAD_RUNNER_PATH = REPO_ROOT / "scripts" / "run_vlad_validation.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vlad_runner = load_module("vlad_runner_standalone_package_symbols", VLAD_RUNNER_PATH)


def test_vlad_does_not_force_raster_sheet_analysis_for_standalone_zip_symbol_jpeg(tmp_path):
    image_path = tmp_path / "Elec_FireAlarms_Detector_Heat_RateOfRise.jpg"
    image_path.write_bytes(b"not a real jpeg, but integrity-only validation should not decode it")

    artifact = vlad_runner.run_validation_task(
        {
            "queue_item_id": "aqi-vlad-fire-standalone-test",
            "source_type": "intake_record",
            "source_id": "intake-fire-standalone-test",
            "asset_path": str(image_path),
            "asset_format": "jpeg",
            "expected_checks": ["integrity"],
            "candidate_symbol_id": "ELEC-FIREALARMS-DETECTOR-HEAT-RATEOFRISE-001",
            "candidate_title": "Electrical FireAlarms Detector Heat Rate Of Rise",
            "origin_file_name": image_path.name,
            "package_member_relationship": "standalone_symbol_file",
            "package_symbol_grouping": "standalone_package_symbol_file",
        }
    )

    assert artifact["decision"] == "pass"
    assert artifact["normalized_technical_metadata"]["package_symbol_grouping"] == "standalone_package_symbol_file"
    checks = [entry["check"] for entry in artifact["evidence_trace"]]
    assert "integrity" in checks
    assert "raster_sheet_analysis" not in checks
    assert not artifact["additional_artifacts"]
