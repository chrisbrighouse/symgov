"""Bounded DEXPI/Proteus XML shape-catalogue conversion.

The converter reads the `ShapeCatalogue` of a Proteus XML P&ID -- the form
DEXPI's public example corpus ships (`gitlab.com/dexpi/TrainingTestCases`,
CC BY 4.0) -- and emits one isolated SVG per catalogue shape.  It is a pure
file service: callers own queues, storage, and access control.  It is the
sibling of `btx_converter.py` and deliberately shares its shape: the same
error/warning vocabulary, the same per-symbol failure isolation, the same
manifest contract.

**Units are recorded, never converted.**  `PlantInformation/@Units` varies
across the corpus (`mm`, `Metre`, `Millimetre`, absent, and one file declaring
`Angstrom`), which is enough to show the declaration cannot be trusted as a
scale factor.  Each SVG is fitted to its own shape's bounding box, so symbol
geometry renders correctly whatever the source declared, and the declared unit
travels into the manifest for a reader to judge.

**Attribution is embedded, not appended.**  Every emitted SVG carries a
Dublin Core `<metadata>` block naming the creator, the licence and the source.
CC BY 4.0 section 3(a) attaches to *sharing*, which includes catalogue download
and the Catalog API, so attribution that lived only in a UI panel would not
travel with the asset.  The caller supplies the three values; the converter
refuses to emit without them.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

# The transformation tool's own version, distinct from the manifest's
# `schema_version`. Section 7.12 asks for the tool *and* its version because
# reproducing a transformation needs both, and WP4 records this string in
# every `asset_transformations` row, so it changes when the emitted geometry
# would change -- not when the manifest grows a field.
CONVERTER_NAME = "symgov-dexpi-converter"
CONVERTER_VERSION = "1.0"

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SYMBOLS = 2_000
MAX_PRIMITIVES_PER_SYMBOL = 10_000

# The six primitives the corpus actually uses.  `Ellipse` is in the Proteus
# schema but appears in no file in the DEXPI example corpus; it is listed so an
# unexpected one is reported as unsupported rather than silently dropped.
GEOMETRY_TAGS = frozenset({"Line", "PolyLine", "Circle", "TrimmedCurve", "Shape", "Text"})
UNSUPPORTED_GEOMETRY_TAGS = frozenset({"Ellipse"})

# --------------------------------------------------------------------------
# Presentation normalisation.
#
# Stroke colour and weight are the source's *canvas* assumptions, not its
# geometry, and measured across the DEXPI corpus they do not survive the move
# to a governed catalogue:
#
#   * 718 of 3629 strokes are pure white and a further 590 are saturated green
#     or cyan -- CAD dark-background palettes, invisible or garish on a light
#     catalogue page.
#   * `LineWeight` is expressed in the file's own units in most files but not
#     all.  Relative to each symbol's bounding-box diagonal the median is a
#     reasonable 1.6%, but the 99th percentile is 179% and the worst is over
#     17000x -- a stroke that would swallow the drawing whole.
#
# So the emitted SVG strokes in `currentColor`, letting the page theme the
# symbol in light and dark, and clamps the weight into a legible band. Both
# source values are recorded per symbol in the manifest, and the manifest
# declares that the normalisation happened: this is a rendering decision that
# a reader must be able to see and reverse, not a silent correction.
STROKE_COLOUR = "currentColor"
DEFAULT_STROKE_RATIO = 0.012
MIN_STROKE_RATIO = 0.004
MAX_STROKE_RATIO = 0.060

# A catalogue entry is any direct child of `ShapeCatalogue` carrying a
# `ComponentName`.  The tag itself is the DEXPI class (`Equipment`, `Nozzle`,
# `PipingComponent`, ...) and is reported rather than filtered: which classes a
# caller wants is a selection decision, not a conversion one.
COMPONENT_NAME_ATTRIBUTE = "ComponentName"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class DexpiConversionError(ValueError):
    """Expected, structured DEXPI conversion failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _warning(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _read_input(path: Path) -> tuple[bytes, str]:
    if not path.is_file():
        raise DexpiConversionError("input_not_found", f"{path} is not a file.")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise DexpiConversionError("input_size_limit", "Input exceeds the maximum accepted size.")
    return path.read_bytes(), path.name


def _parse(data: bytes) -> ET.Element:
    """Parse Proteus XML, refusing any document that declares a DTD or entity.

    The same guard `btx_converter` applies, for the same reason: the input is
    an untrusted file and `ElementTree` will resolve an internal entity
    declaration.  Checked on the raw bytes before parsing, so nothing is
    expanded in order to discover it should not have been.
    """
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise DexpiConversionError("unsafe_xml", "Proteus XML may not declare DTDs or entities.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DexpiConversionError("malformed_xml", "Proteus XML could not be parsed.") from exc
    if _local(root.tag) != "PlantModel":
        raise DexpiConversionError("unexpected_xml_root", "Expected a PlantModel XML root.")
    return root


def _local(tag: str) -> str:
    """Proteus ships unqualified, but tolerate a namespaced document."""
    return tag.rsplit("}", 1)[-1]


def _find(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _number(value: str | None, default: float | None = None) -> float | None:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _location(element: ET.Element) -> tuple[float, float]:
    """The centre of a positioned primitive, defaulting to the origin."""
    position = _find(element, "Position")
    node = _find(position, "Location") if position is not None else None
    if node is None:
        return 0.0, 0.0
    return _number(node.get("X"), 0.0) or 0.0, _number(node.get("Y"), 0.0) or 0.0


def _coordinates(element: ET.Element) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for child in element:
        if _local(child.tag) != "Coordinate":
            continue
        x = _number(child.get("X"))
        y = _number(child.get("Y"))
        if x is None or y is None:
            continue
        points.append((x, y))
    return points


def _presentation(element: ET.Element) -> dict[str, Any]:
    """Stroke colour and weight, as the source declares them.

    `LineWeight` is used unscaled.  It is expressed in the file's own
    coordinate units, and because each SVG is fitted to its shape's bounding
    box in those same units, the ratio a draughtsman chose is preserved
    whether the file counts in millimetres or metres.
    """
    node = _find(element, "Presentation")
    if node is None:
        return {"stroke": "#000000", "stroke_width": None}
    red, green, blue = (_number(node.get(key)) for key in ("R", "G", "B"))
    if None not in (red, green, blue):
        channels = tuple(max(0, min(255, round((value or 0.0) * 255))) for value in (red, green, blue))
        stroke = "#%02x%02x%02x" % channels
    else:
        stroke = (node.get("Color") or "black").lower()
    return {"stroke": stroke, "stroke_width": _number(node.get("LineWeight"))}


def _arc(element: ET.Element) -> dict[str, Any] | None:
    """A `TrimmedCurve` wrapping a `Circle`, as start/end angles in degrees."""
    circle = _find(element, "Circle")
    if circle is None:
        return None
    radius = _number(circle.get("Radius"))
    if radius is None or radius <= 0:
        return None
    centre_x, centre_y = _location(circle)
    start = _number(element.get("StartAngle"), 0.0) or 0.0
    end = _number(element.get("EndAngle"), 0.0) or 0.0
    return {
        "type": "arc",
        "cx": centre_x,
        "cy": centre_y,
        "r": radius,
        "start": start,
        "end": end,
        **_presentation(circle),
    }


def _primitives(element: ET.Element) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Flatten one catalogue entry into drawable primitives.

    Walks the whole subtree so a nested `Shape` reference is picked up, and
    skips a `Circle` that is a `TrimmedCurve`'s own child -- that circle is the
    arc's construction geometry, not a circle to draw.
    """
    primitives: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    consumed: set[int] = set()

    for node in element.iter():
        tag = _local(node.tag)
        if tag == "TrimmedCurve":
            circle = _find(node, "Circle")
            if circle is not None:
                consumed.add(id(circle))

    for node in element.iter():
        if node is element:
            continue
        tag = _local(node.tag)
        if tag in UNSUPPORTED_GEOMETRY_TAGS:
            warnings.append(_warning("unsupported_primitive", f"{tag} is not supported and was skipped."))
            continue
        if tag not in GEOMETRY_TAGS:
            continue
        if len(primitives) >= MAX_PRIMITIVES_PER_SYMBOL:
            warnings.append(_warning("primitive_limit", "Primitive limit reached; the rest were skipped."))
            break
        style = _presentation(node)

        if tag in {"Line", "PolyLine"}:
            points = _coordinates(node)
            if len(points) < 2:
                warnings.append(_warning("degenerate_primitive", f"{tag} has fewer than two points."))
                continue
            primitives.append({"type": "polyline", "points": points, **style})
        elif tag == "Shape":
            points = _coordinates(node)
            if len(points) < 3:
                warnings.append(_warning("degenerate_primitive", "Shape has fewer than three points."))
                continue
            filled = (node.get("Filled") or "").strip().lower() not in {"", "none", "false"}
            primitives.append({"type": "polygon", "points": points, "filled": filled, **style})
        elif tag == "Circle":
            if id(node) in consumed:
                continue
            radius = _number(node.get("Radius"))
            if radius is None or radius <= 0:
                warnings.append(_warning("degenerate_primitive", "Circle has no positive radius."))
                continue
            centre_x, centre_y = _location(node)
            primitives.append({"type": "circle", "cx": centre_x, "cy": centre_y, "r": radius, **style})
        elif tag == "TrimmedCurve":
            arc = _arc(node)
            if arc is None:
                warnings.append(_warning("degenerate_primitive", "TrimmedCurve has no usable circle."))
                continue
            primitives.append(arc)
        elif tag == "Text":
            content = node.get("String")
            if not content:
                continue
            anchor_x, anchor_y = _location(node)
            primitives.append(
                {
                    "type": "text",
                    "x": anchor_x,
                    "y": anchor_y,
                    "text": content,
                    "height": _number(node.get("Height")),
                    "angle": _number(node.get("TextAngle"), 0.0) or 0.0,
                    "justification": node.get("Justification") or "",
                    **style,
                }
            )

    return primitives, warnings


def geometry_signature(primitives: list[dict[str, Any]]) -> str:
    """A stable identity for a shape's drawing, ignoring how it was presented.

    Two catalogue entries with the same signature are the same drawing, whatever
    each vendor named it.  This is what lets selection collapse 877 catalogue
    entries into the distinct geometries behind them, so it has to depend on
    geometry alone: stroke colour and line weight are excluded for the reason
    they are normalised at all, and the primitive order is sorted so that two
    files listing the same shapes differently still agree.

    Coordinates are rounded to 6 decimal places.  The corpus mixes millimetre
    files with metre files, so the tolerance has to survive geometry whose whole
    extent is 0.0025 units wide.
    """
    tokens: list[tuple] = []
    for primitive in primitives:
        kind = primitive["type"]
        if kind in {"polyline", "polygon"}:
            values = tuple(round(value, 6) for point in primitive["points"] for value in point)
        elif kind == "circle":
            values = tuple(round(primitive[key], 6) for key in ("cx", "cy", "r"))
        elif kind == "arc":
            values = tuple(round(primitive[key], 6) for key in ("cx", "cy", "r", "start", "end"))
        elif kind == "text":
            values = (round(primitive["x"], 6), round(primitive["y"], 6), primitive["text"])
        else:  # pragma: no cover - every kind above is covered
            values = ()
        tokens.append((kind, values))
    tokens.sort(key=repr)
    return hashlib.sha256(repr(tokens).encode("utf-8")).hexdigest()


def _arc_endpoints(primitive: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float], int]:
    start = math.radians(primitive["start"])
    end = math.radians(primitive["end"])
    centre_x, centre_y, radius = primitive["cx"], primitive["cy"], primitive["r"]
    first = (centre_x + radius * math.cos(start), centre_y + radius * math.sin(start))
    last = (centre_x + radius * math.cos(end), centre_y + radius * math.sin(end))
    sweep = (primitive["end"] - primitive["start"]) % 360.0
    return first, last, 1 if sweep > 180.0 else 0


def _bounds(primitives: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for primitive in primitives:
        kind = primitive["type"]
        if kind in {"polyline", "polygon"}:
            xs.extend(x for x, _ in primitive["points"])
            ys.extend(y for _, y in primitive["points"])
        elif kind == "circle":
            xs.extend((primitive["cx"] - primitive["r"], primitive["cx"] + primitive["r"]))
            ys.extend((primitive["cy"] - primitive["r"], primitive["cy"] + primitive["r"]))
        elif kind == "arc":
            # The arc's own extremes are not computed; its circle's are, which
            # never crops the drawing and at worst pads it.
            xs.extend((primitive["cx"] - primitive["r"], primitive["cx"] + primitive["r"]))
            ys.extend((primitive["cy"] - primitive["r"], primitive["cy"] + primitive["r"]))
        elif kind == "text":
            xs.append(primitive["x"])
            ys.append(primitive["y"])
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _format(value: float) -> str:
    """Render a coordinate without losing small-scale geometry.

    Six decimals was not enough: the corpus mixes millimetre files whose
    coordinates run to 21.25 with metre files whose whole symbol spans 0.0025,
    and a fixed 6dp silently rounded the latter's stroke widths to `0` -- an
    invisible symbol rather than a failed one.  Nine decimals covers the
    smallest real geometry in the corpus, and exponent notation is avoided
    because not every SVG consumer accepts it.
    """
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    # Negating a zero Y produces "-0", which is valid SVG but noise in a diff.
    return text if text not in {"", "-", "-0"} else "0"


def _point(x: float, y: float) -> str:
    """Emit a source point with Y negated, flipping the Y-up source axis."""
    return f"{_format(x)},{_format(-y)}"


def _stroke_width(declared: float | None, diagonal: float) -> float:
    """Clamp a declared line weight into a legible band for this symbol."""
    if diagonal <= 0:
        return DEFAULT_STROKE_RATIO
    if declared is None or declared <= 0 or not math.isfinite(declared):
        return diagonal * DEFAULT_STROKE_RATIO
    return min(max(declared, diagonal * MIN_STROKE_RATIO), diagonal * MAX_STROKE_RATIO)


def _svg_elements(primitives: list[dict[str, Any]], diagonal: float) -> list[str]:
    elements: list[str] = []
    for primitive in primitives:
        stroke = STROKE_COLOUR
        width = _stroke_width(primitive.get("stroke_width"), diagonal)
        common = f'stroke="{stroke}" stroke-width="{_format(width)}" fill="none"'
        kind = primitive["type"]
        if kind == "polyline":
            points = " ".join(_point(x, y) for x, y in primitive["points"])
            elements.append(f'<polyline points="{points}" {common} stroke-linecap="round"/>')
        elif kind == "polygon":
            points = " ".join(_point(x, y) for x, y in primitive["points"])
            fill = stroke if primitive.get("filled") else "none"
            elements.append(
                f'<polygon points="{points}" stroke="{stroke}" stroke-width="{_format(width)}" fill="{fill}"/>'
            )
        elif kind == "circle":
            elements.append(
                f'<circle cx="{_format(primitive["cx"])}" cy="{_format(-primitive["cy"])}"'
                f' r="{_format(primitive["r"])}" {common}/>'
            )
        elif kind == "arc":
            (start_x, start_y), (end_x, end_y), large = _arc_endpoints(primitive)
            radius = _format(primitive["r"])
            # Sweep 0: negating Y reverses the direction of increasing angle.
            path = (
                f'M {_format(start_x)} {_format(-start_y)} '
                f'A {radius} {radius} 0 {large} 0 {_format(end_x)} {_format(-end_y)}'
            )
            elements.append(f'<path d="{path}" {common}/>')
        elif kind == "text":
            size = primitive.get("height") or diagonal * DEFAULT_STROKE_RATIO * 8
            anchor = "middle" if "center" in primitive["justification"].lower() else "start"
            content = (
                primitive["text"]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            elements.append(
                f'<text x="{_format(primitive["x"])}" y="{_format(-primitive["y"])}"'
                f' font-size="{_format(size)}" fill="{stroke}" stroke="none"'
                f' text-anchor="{anchor}" dominant-baseline="middle">{content}</text>'
            )
    return elements


def _metadata_block(attribution: dict[str, str]) -> str:
    def escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return (
        "  <metadata>\n"
        '    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '      <rdf:Description rdf:about="">\n'
        f'        <dc:creator>{escape(attribution["creator"])}</dc:creator>\n'
        f'        <dc:rights>{escape(attribution["licence"])}</dc:rights>\n'
        f'        <dc:source>{escape(attribution["source"])}</dc:source>\n'
        "      </rdf:Description>\n"
        "    </rdf:RDF>\n"
        "  </metadata>\n"
    )


def _emit_svg(symbol: dict[str, Any], path: Path, attribution: dict[str, str]) -> None:
    bounds = symbol["bounds"]
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    # A shape may be perfectly flat in one axis (a bare horizontal line).
    pad_x = width * 0.05 if width else max(height * 0.05, 1e-6)
    pad_y = height * 0.05 if height else max(width * 0.05, 1e-6)
    view_x = min_x - pad_x
    view_y = -max_y - pad_y
    view_w = (width or pad_x * 2) + pad_x * 2
    view_h = (height or pad_y * 2) + pad_y * 2
    diagonal = math.hypot(view_w, view_h)

    body = "\n".join(f"  {element}" for element in _svg_elements(symbol["primitives"], diagonal))
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"'
        f' viewBox="{_format(view_x)} {_format(view_y)} {_format(view_w)} {_format(view_h)}">\n'
        f"  <title>{symbol['component_name']}</title>\n"
        f"{_metadata_block(attribution)}"
        f"{body}\n"
        "</svg>\n"
    )
    path.write_text(document, encoding="utf-8")


def _safe_name(value: str, ordinal: int) -> str:
    cleaned = _SAFE_NAME.sub("_", value).strip("._-")
    return cleaned[:80] if cleaned else f"shape_{ordinal + 1}"


def _require_attribution(attribution: Any) -> dict[str, str]:
    if not isinstance(attribution, dict):
        raise DexpiConversionError("attribution_required", "Attribution must be supplied as a mapping.")
    resolved: dict[str, str] = {}
    for key in ("creator", "licence", "source"):
        value = attribution.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DexpiConversionError(
                "attribution_required", f"Attribution is missing a non-empty {key!r}."
            )
        resolved[key] = value.strip()
    return resolved


def convert_dexpi(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    attribution: dict[str, str],
    formats: tuple[str, ...] = ("svg",),
) -> dict[str, Any]:
    """Convert one Proteus XML file's ShapeCatalogue into isolated symbol assets."""
    unsupported = tuple(value for value in formats if value != "svg")
    if unsupported:
        raise DexpiConversionError(
            "unsupported_format", f"Only 'svg' is supported; got {', '.join(unsupported)}."
        )
    resolved_attribution = _require_attribution(attribution)

    source = Path(input_path)
    output = Path(output_dir)
    data, source_name = _read_input(source)
    root = _parse(data)

    information = _find(root, "PlantInformation")
    declared_units = information.get("Units") if information is not None else None

    catalogue = None
    for node in root.iter():
        if _local(node.tag) == "ShapeCatalogue":
            catalogue = node
            break
    if catalogue is None:
        raise DexpiConversionError("no_shape_catalogue", "PlantModel contains no ShapeCatalogue.")

    entries = [child for child in catalogue if child.get(COMPONENT_NAME_ATTRIBUTE)]
    if len(entries) > MAX_SYMBOLS:
        raise DexpiConversionError("symbol_limit", "ShapeCatalogue contains too many shapes.")

    output.mkdir(parents=True, exist_ok=True)
    symbols: list[dict[str, Any]] = []
    used_stems: dict[str, int] = {}

    for ordinal, entry in enumerate(entries):
        component_name = entry.get(COMPONENT_NAME_ATTRIBUTE) or f"Shape {ordinal + 1}"
        try:
            primitives, warnings = _primitives(entry)
            if not primitives:
                raise DexpiConversionError("no_geometry", "Shape declares no drawable geometry.")
            bounds = _bounds(primitives)
            if bounds is None:
                raise DexpiConversionError("no_geometry", "Shape geometry has no extent.")
            if bounds[2] - bounds[0] == 0 and bounds[3] - bounds[1] == 0:
                # Every primitive collapsed onto one point. Padding would still
                # produce a viewBox, so this has to be refused explicitly or it
                # ships as a blank symbol rather than a reported failure.
                raise DexpiConversionError("degenerate_extent", "Shape collapses to a single point.")

            stem = _safe_name(component_name, ordinal)
            used_stems[stem] = used_stems.get(stem, 0) + 1
            if used_stems[stem] > 1:
                stem = f"{stem}_{used_stems[stem]}"

            registration = None
            for attribute in entry.iter():
                if _local(attribute.tag) != "GenericAttribute":
                    continue
                if attribute.get("Name") == "SymbolRegistrationNumberAssignmentClass":
                    registration = attribute.get("Value")
                    break

            symbol = {
                "ordinal": ordinal,
                "component_name": component_name,
                "component_class": entry.get("ComponentClass"),
                "dexpi_element": _local(entry.tag),
                "registration_number": registration,
                "primitive_count": len(primitives),
                "geometry_signature": geometry_signature(primitives),
                "bounds": list(bounds),
                # What the source declared, before normalisation. Kept so the
                # rendering decision above can be audited or undone.
                "source_stroke_colours": sorted(
                    {primitive.get("stroke") for primitive in primitives if primitive.get("stroke")}
                ),
                "source_line_weights": sorted(
                    {
                        primitive["stroke_width"]
                        for primitive in primitives
                        if primitive.get("stroke_width") is not None
                    }
                ),
                "warnings": warnings,
                "primitives": primitives,
            }
            if "svg" in formats:
                _emit_svg(symbol, output / f"{stem}.svg", resolved_attribution)
                asset = output / f"{stem}.svg"
                symbol["svg"] = f"{stem}.svg"
                symbol["svg_sha256"] = hashlib.sha256(asset.read_bytes()).hexdigest()
            symbols.append({key: value for key, value in symbol.items() if key != "primitives"})
        except DexpiConversionError as exc:
            symbols.append(
                {
                    "ordinal": ordinal,
                    "component_name": component_name,
                    "status": "failed",
                    "warnings": [_warning(exc.code, exc.detail)],
                }
            )

    manifest = {
        "schema_version": "1.0",
        "converter_name": CONVERTER_NAME,
        "converter_version": CONVERTER_VERSION,
        "source_filename": source_name,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "declared_units": declared_units,
        "specification_version": information.get("ApplicationVersion") if information is not None else None,
        "originating_system": information.get("OriginatingSystem") if information is not None else None,
        "originating_system_vendor": (
            information.get("OriginatingSystemVendor") if information is not None else None
        ),
        "attribution": resolved_attribution,
        # Declared, not implied: the emitted SVG does not carry the source's
        # stroke colour or weight, and a reader comparing the two must be told.
        "presentation_normalisation": {
            "stroke_colour": STROKE_COLOUR,
            "stroke_width_ratio_default": DEFAULT_STROKE_RATIO,
            "stroke_width_ratio_bounds": [MIN_STROKE_RATIO, MAX_STROKE_RATIO],
            "basis": "source stroke colour and line weight are canvas assumptions, not geometry",
        },
        "formats": list(formats),
        "symbols": symbols,
        "successful_symbol_count": sum("status" not in symbol for symbol in symbols),
        "failed_symbol_count": sum(symbol.get("status") == "failed" for symbol in symbols),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
