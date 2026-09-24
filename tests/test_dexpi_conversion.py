"""Cover for the DEXPI/Proteus shape-catalogue converter (WP1 of the DEXPI pilot).

The converter is a pure file service, so everything here is DB-free and runs
off two fixtures: a handwritten catalogue exercising every primitive and both
refusal paths, and the real DEXPI 1.3 reference P&ID.

See `docs/plans/2026-09-22-dexpi-symbol-library-pilot-implementation-plan.md`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FIXTURE_ROOT = REPO_ROOT / "integrations" / "dexpi" / "fixtures"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.services.dexpi_converter import (  # noqa: E402
    MAX_STROKE_RATIO,
    MIN_STROKE_RATIO,
    STROKE_COLOUR,
    DexpiConversionError,
    convert_dexpi,
)

MINIMAL = FIXTURE_ROOT / "minimal_catalogue.xml"
REFERENCE = FIXTURE_ROOT / "C01V04-VER.EX01.xml"

# The digest pinned in the fixtures README, and the one an ingestion run would
# record as `SourcePackageEntry.original_asset_sha256`.
REFERENCE_SHA256 = "a2b172f04e0dcf9a668e158c6dee3b5fd0dd4e9027b572dc39e54470562b809c"

ATTRIBUTION = {
    "creator": "DEXPI e.V. and corpus contributors",
    "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
    "source": "https://gitlab.com/dexpi/TrainingTestCases",
}


def convert(path, destination, **kwargs):
    kwargs.setdefault("attribution", ATTRIBUTION)
    return convert_dexpi(path, destination, **kwargs)


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """Convert the 445 KB reference P&ID once rather than per test.

    Six tests read it, and the full backend sweep already runs long enough that
    re-converting it each time is a cost worth not paying.
    """
    destination = tmp_path_factory.mktemp("dexpi-reference")
    return convert(REFERENCE, destination), destination


def symbol_named(manifest, name):
    for symbol in manifest["symbols"]:
        if symbol["component_name"] == name:
            return symbol
    raise AssertionError(f"no symbol named {name}")


# --------------------------------------------------------------------------
# The happy path


def test_every_supported_primitive_reaches_the_svg(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    symbol = symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")
    svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")

    assert "<circle" in svg
    assert svg.count("<polyline") == 2  # the PolyLine and the Line
    assert "<polygon" in svg
    assert "<path" in svg  # the TrimmedCurve, as an arc
    assert "<text" in svg
    assert symbol["primitive_count"] == 6


def test_the_source_y_up_axis_is_flipped(tmp_path):
    """Proteus is Y-up and SVG is Y-down; a polyline rising to the right in the
    source must fall to the right in the emitted document."""
    manifest = convert(MINIMAL, tmp_path)
    symbol = symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")
    svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")

    points = re.search(r'<polyline points="([^"]+)"', svg).group(1).split()
    (first_x, first_y), (last_x, last_y) = (
        tuple(float(value) for value in point.split(",")) for point in points
    )
    assert last_x > first_x
    # Source ran -5 -> +5 (rising). Emitted must run +5 -> -5 (falling).
    assert first_y > last_y


def test_a_filled_shape_is_filled_and_a_polyline_is_not(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    svg = (tmp_path / symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")["svg"]).read_text(encoding="utf-8")

    polygon = re.search(r"<polygon[^>]*>", svg).group(0)
    assert 'fill="currentColor"' in polygon
    polyline = re.search(r"<polyline[^>]*>", svg).group(0)
    assert 'fill="none"' in polyline


def test_the_registration_number_is_carried_into_the_manifest(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    assert symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")["registration_number"] == "ISO10628:2012-9999-A"
    assert symbol_named(manifest, "ABSURD_LINE_WEIGHT_SHAPE")["registration_number"] is None


def test_the_dexpi_class_and_element_are_reported_not_filtered(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    symbol = symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")
    assert symbol["component_class"] == "Tank"
    assert symbol["dexpi_element"] == "Equipment"


def test_an_entry_without_a_component_name_is_not_a_catalogue_entry(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    assert len(manifest["symbols"]) == 4


# --------------------------------------------------------------------------
# Presentation normalisation


def test_stroke_colour_is_normalised_so_a_white_symbol_stays_visible(tmp_path):
    """The fixture's circle is pure white, which would vanish on a light page."""
    manifest = convert(MINIMAL, tmp_path)
    symbol = symbol_named(manifest, "EVERY_PRIMITIVE_SHAPE")
    svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")

    assert f'stroke="{STROKE_COLOUR}"' in svg
    assert "#ffffff" not in svg
    # and the source colour survives in the manifest rather than being lost
    assert "#ffffff" in symbol["source_stroke_colours"]


def test_an_absurd_line_weight_is_clamped_into_the_legible_band(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    symbol = symbol_named(manifest, "ABSURD_LINE_WEIGHT_SHAPE")
    svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")

    view_box = [float(value) for value in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    diagonal = (view_box[2] ** 2 + view_box[3] ** 2) ** 0.5
    width = float(re.search(r'stroke-width="([\d.]+)"', svg).group(1))

    assert width <= diagonal * MAX_STROKE_RATIO
    assert width >= diagonal * MIN_STROKE_RATIO
    assert 5000.0 in symbol["source_line_weights"]


def test_the_normalisation_is_declared_in_the_manifest(tmp_path):
    """A reader comparing source to output has to be told what was changed."""
    manifest = convert(MINIMAL, tmp_path)
    normalisation = manifest["presentation_normalisation"]
    assert normalisation["stroke_colour"] == STROKE_COLOUR
    assert normalisation["stroke_width_ratio_bounds"] == [MIN_STROKE_RATIO, MAX_STROKE_RATIO]


# --------------------------------------------------------------------------
# Attribution (D5)


def test_attribution_is_embedded_in_every_svg(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    for symbol in manifest["symbols"]:
        if symbol.get("status") == "failed":
            continue
        svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")
        assert "<metadata>" in svg
        assert ATTRIBUTION["creator"] in svg
        assert ATTRIBUTION["licence"] in svg
        assert ATTRIBUTION["source"] in svg


@pytest.mark.parametrize("missing", ["creator", "licence", "source"])
def test_conversion_refuses_without_complete_attribution(tmp_path, missing):
    attribution = {key: value for key, value in ATTRIBUTION.items() if key != missing}
    with pytest.raises(DexpiConversionError) as error:
        convert(MINIMAL, tmp_path, attribution=attribution)
    assert error.value.code == "attribution_required"


def test_conversion_refuses_a_blank_attribution_value(tmp_path):
    with pytest.raises(DexpiConversionError) as error:
        convert(MINIMAL, tmp_path, attribution={**ATTRIBUTION, "creator": "   "})
    assert error.value.code == "attribution_required"


# --------------------------------------------------------------------------
# Per-symbol failure isolation


def test_a_shape_with_no_geometry_fails_alone(tmp_path):
    manifest = convert(MINIMAL, tmp_path)
    failed = symbol_named(manifest, "NO_GEOMETRY_SHAPE")
    assert failed["status"] == "failed"
    assert failed["warnings"][0]["code"] == "no_geometry"
    assert manifest["successful_symbol_count"] == 2
    assert manifest["failed_symbol_count"] == 2


def test_a_point_extent_shape_is_refused_rather_than_shipped_blank(tmp_path):
    """Padding would still produce a viewBox, so this has to be explicit."""
    manifest = convert(MINIMAL, tmp_path)
    failed = symbol_named(manifest, "POINT_ONLY_SHAPE")
    assert failed["status"] == "failed"
    assert failed["warnings"][0]["code"] == "degenerate_extent"
    assert not (tmp_path / "POINT_ONLY_SHAPE.svg").exists()


# --------------------------------------------------------------------------
# Input handling


def test_a_dtd_declaration_is_refused_before_parsing(tmp_path):
    hostile = tmp_path / "hostile.xml"
    hostile.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE PlantModel [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        "<PlantModel><ShapeCatalogue/></PlantModel>\n",
        encoding="utf-8",
    )
    with pytest.raises(DexpiConversionError) as error:
        convert(hostile, tmp_path / "out")
    assert error.value.code == "unsafe_xml"


def test_a_non_plantmodel_root_is_refused(tmp_path):
    other = tmp_path / "other.xml"
    other.write_text("<BluebeamRevuToolSet/>\n", encoding="utf-8")
    with pytest.raises(DexpiConversionError) as error:
        convert(other, tmp_path / "out")
    assert error.value.code == "unexpected_xml_root"


def test_a_plantmodel_without_a_catalogue_is_refused(tmp_path):
    empty = tmp_path / "empty.xml"
    empty.write_text("<PlantModel><Drawing/></PlantModel>\n", encoding="utf-8")
    with pytest.raises(DexpiConversionError) as error:
        convert(empty, tmp_path / "out")
    assert error.value.code == "no_shape_catalogue"


def test_malformed_xml_is_refused(tmp_path):
    broken = tmp_path / "broken.xml"
    broken.write_text("<PlantModel><ShapeCatalogue>\n", encoding="utf-8")
    with pytest.raises(DexpiConversionError) as error:
        convert(broken, tmp_path / "out")
    assert error.value.code == "malformed_xml"


def test_a_missing_input_is_refused(tmp_path):
    with pytest.raises(DexpiConversionError) as error:
        convert(tmp_path / "absent.xml", tmp_path / "out")
    assert error.value.code == "input_not_found"


def test_only_svg_is_offered(tmp_path):
    """BTX emits three formats; this converter emits one, and says so rather
    than silently ignoring the request."""
    with pytest.raises(DexpiConversionError) as error:
        convert(MINIMAL, tmp_path, formats=("svg", "dxf"))
    assert error.value.code == "unsupported_format"


# --------------------------------------------------------------------------
# The real reference P&ID


def test_the_reference_pid_converts_completely(reference):
    manifest, _ = reference
    assert manifest["successful_symbol_count"] == 24
    assert manifest["failed_symbol_count"] == 0
    assert manifest["declared_units"] == "mm"


def test_the_reference_pid_digest_is_the_one_the_fixtures_readme_pins(reference):
    """`source_sha256` is what an ingestion run records as the entry's
    `original_asset_sha256`, so a silent re-vendoring must break a test."""
    manifest, _ = reference
    assert manifest["source_sha256"] == REFERENCE_SHA256


def test_the_centrifugal_pump_keeps_its_source_geometry(reference):
    """Radius 7.5 circle, a diameter line and the impeller chevron, straight
    out of the DEXPI reference solution."""
    manifest, tmp_path = reference
    symbol = symbol_named(manifest, "CENTRIFUGAL_PUMP_SHAPE")
    assert symbol["registration_number"] == "ISO10628:2012-2322-A"
    assert symbol["bounds"] == [-7.5, -7.5, 7.5, 7.5]

    svg = (tmp_path / symbol["svg"]).read_text(encoding="utf-8")
    assert 'r="7.5"' in svg
    assert svg.count("<polyline") == 2


def test_every_emitted_asset_is_hashed_for_the_transformation_chain(reference):
    """`record_asset_transformation` needs the derived digest; the converter
    must supply it rather than leave the caller to re-read the file."""
    import hashlib

    manifest, tmp_path = reference
    for symbol in manifest["symbols"]:
        digest = hashlib.sha256((tmp_path / symbol["svg"]).read_bytes()).hexdigest()
        assert symbol["svg_sha256"] == digest


def test_the_manifest_is_written_to_disk_as_well_as_returned(reference):
    manifest, tmp_path = reference
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["source_sha256"] == manifest["source_sha256"]
    assert on_disk["schema_version"] == "1.0"
