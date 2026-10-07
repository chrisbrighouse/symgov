"""A small stand-in for the DISC DEXPI import package, with the real one's shape.

It exists so the planner, the driver and the API can be tested without the
339 real SVGs. The attribution wording here is invented for the tests; the real
text lives only in the import package and, once imported, on the rights record.

Four symbols carry what the real package's hardest cases do:

* ND0004: a custom type, three piping connection points, one label slot, and
  two options, one of which the source gives no condition for (the real
  ND0168 and ND0248B are the same case).
* ND0136: a plain class shared with the pilot (`CentrifugalPump`).
* ND0114: a heat exchanger whose source name carries a typo to correct.
* ND0050: an annotation with no geometry, no options and a register warning.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

ATTRIBUTION = "Stand-in attribution wording for the test library, supplied by nobody."
COMMIT = "0123456789abcdef0123456789abcdef01234567"
SOURCE_URL = "https://example.test/disc-library/tree/main"

CONFIG = {
    "attribution_text": ATTRIBUTION,
    "attribution_is_placeholder": True,
    "creator": "The Example project (example.test)",
    "licensor": "A. Licensor",
    "source_url": SOURCE_URL,
    "organisation_url": "https://example.test",
    "pack_code": "disc-dexpi",
    "pack_name": "DISC DEXPI symbol library (DISC Profile 0.6.3)",
    "profile_version": "DISC Profile 0.6.3",
    "dexpi_version": "1.3",
    "default_stroke_mm": 0.25,
    "importable_mapping_status": ["OK"],
}

SCHEME = {
    "code": "DEXPI-CLASS",
    "name": "DEXPI class",
    "version": "DEXPI 1.3 + DISC Profile 0.6.3 extensions",
    "description": "Governed classification of symbols by the DEXPI class they represent.",
    "nodes": [
        {"code": "Equipment", "label": "Equipment", "parent": None, "kind": "package"},
        {"code": "Piping", "label": "Piping", "parent": None, "kind": "package"},
        {"code": "Annotation", "label": "Annotation", "parent": None, "kind": "package"},
        {"code": "Equipment", "label": "Equipment", "parent": "Equipment", "kind": "dexpi_class",
         "description": "An apparatus or machine.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "CentrifugalPump", "label": "Centrifugal Pump", "parent": "Equipment", "kind": "dexpi_class",
         "description": "A pump.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "CustomHeatExchanger", "label": "Custom Heat Exchanger", "parent": "Equipment", "kind": "dexpi_class",
         "description": "A custom heat exchanger.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "CustomOperatedValve", "label": "Custom Operated Valve", "parent": "Piping", "kind": "dexpi_class",
         "description": "A custom operated valve.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "BallValve", "label": "Ball Valve", "parent": "Piping", "kind": "dexpi_class",
         "description": "A ball valve.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "Label", "label": "Label", "parent": "Annotation", "kind": "dexpi_class",
         "description": "A label.", "uri": None, "source": "DEXPI 1.3 class list"},
        {"code": "CustomOperatedValve/DoubleBlockAndBleedValve", "label": "Double Block And Bleed Valve",
         "parent": "CustomOperatedValve", "kind": "custom_type", "uri": "http://rdl.example.test/RDS552689",
         "source": "DISC Symbols.xlsm custom type"},
        {"code": "CustomHeatExchanger/ShellAndFixedTubeHeatExchanger", "label": "Shell And Fixed Tube Heat Exchanger",
         "parent": "CustomHeatExchanger", "kind": "custom_type", "uri": "http://rdl.example.test/RDS439604",
         "source": "DISC Symbols.xlsm custom type"},
    ],
}


def svg_bytes(label: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" width="10mm" height="10mm" viewBox="0 0 10 10">'
        f"<title>{label}</title><metadata><rdf:RDF><dc:rights>{ATTRIBUTION}</dc:rights></rdf:RDF></metadata>"
        '<path d="M0 0 L10 10" stroke="#000" stroke-width="0.25"/></svg>'
    ).encode("utf-8")


_BYTES: dict[str, bytes] = {}


def _asset(role: str, filename: str, source_path: str, label: str, **extra) -> dict:
    data = svg_bytes(label)
    _BYTES[filename] = data
    return {
        "role": role,
        "filename": filename,
        "format": "svg",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "source_path": source_path,
        **extra,
    }


def _classifications(node, discipline, category, grouping, package) -> list[dict]:
    return [
        {"scheme": "DEXPI-CLASS", "node": node, "role": "primary", "method": "source_mapping",
         "evidence": {"source": "DISC Symbols.xlsm", "dexpi_class": node.split("/")[0]}},
        {"scheme": "ENGINEERING-DISCIPLINE", "node_label": discipline, "role": "primary", "method": "rule",
         "evidence": {"rule": "convert_disc.discipline_category", "grouping": grouping, "dexpi_package": package}},
        {"scheme": "SYMBOL-CATEGORY-FAMILY", "node_label": category, "role": "primary", "method": "rule",
         "evidence": {"rule": "convert_disc.discipline_category", "grouping": grouping}},
    ]


def _record(nd, name, summary_name, category, discipline, grouping, package, element, classes, concept_key,
            node, assets, geometry, options, mappings, custom_type=None, warnings=()) -> dict:
    dexpi = {
        "vendors": ["DISC"],
        "base_name": concept_key,
        "name_basis": "disc_symbol_register",
        "attribution": ATTRIBUTION,
        "attribution_is_placeholder": True,
        "declared_units": "mm",
        "dexpi_elements": [element],
        "dexpi_package": package,
        "component_classes": classes,
        "custom_type": custom_type,
        "semantic_concept": {"concept_key": concept_key, "rdl_uri": (custom_type or {}).get("rdl_uri")},
        "geometry_signature": hashlib.sha256(f"geometry-{nd}".encode()).hexdigest(),
        "standard_assertion": {
            "standard_code": "DEXPI",
            "version_label": "DISC Profile 0.6.3 (DEXPI 1.3)",
            "relationship_type": "normative_definition",
            "source_symbol_identifier": nd,
        },
        "specification_versions": ["1.3"],
        "registrations": [],
    }
    return {
        "source_key": f"disc-dexpi-{nd}",
        "name": f"{name} (DISC {nd})",
        "summary": f"{summary_name}, from the DISC DEXPI project symbol legend (DISC Profile 0.6.3).",
        "rationale": "Imported from the test library; register mapping status OK.",
        "category": category,
        "discipline": discipline,
        "keywords": ["DEXPI", "DISC", *classes, concept_key],
        "pack": {"code": "disc-dexpi", "name": CONFIG["pack_name"]},
        "assets": assets,
        "payload": {
            "name": f"{name} (DISC {nd})",
            "description": (
                f"{name} as defined in the DISC DEXPI project symbol legend, carrying the meaning of the "
                f"{concept_key} concept. {ATTRIBUTION}"
            ),
            "aliases": [nd, *classes, concept_key],
            "dexpi": dexpi,
            "disc": {"disc_id": nd, "grouping": grouping, "label_templates": {"A": "<ObjectDisplayName>"}},
            "geometry": geometry,
            "options": options,
        },
        "classifications": _classifications(node, discipline, category, grouping, package),
        "external_mappings": mappings,
        "rights": {
            "status": "licensed",
            "licensor": CONFIG["licensor"],
            "creator": CONFIG["creator"],
            "attribution_text": ATTRIBUTION,
            "attribution_is_placeholder": True,
            "source_url": SOURCE_URL,
            "source_commit": COMMIT,
        },
        "warnings": list(warnings),
    }


def build_records() -> list[dict]:
    valve_geometry = {
        "units": "mm", "origin": [0, 0], "y_axis": "down", "bbox_mm": [-9, -9, 9, 2],
        "connection_points": [
            {"index": 1, "x_mm": 9.0, "y_mm": 0.0625, "directions_deg": [0.0], "kinds": ["piping"]},
            {"index": 2, "x_mm": -9.0, "y_mm": 0.0625, "directions_deg": [180.0], "kinds": ["piping"]},
            {"index": 3, "x_mm": 0.0, "y_mm": -8.9375, "directions_deg": [270.0], "kinds": ["piping"]},
        ],
        "label_slots": [{"label_index": "A", "lines": 3, "box_mm": [3, -3.9, 8, -2.9], "template": "<ObjectDisplayName>"}],
    }
    plain_geometry = {"units": "mm", "origin": [0, 0], "y_axis": "down", "bbox_mm": [-5, -5, 5, 5],
                      "connection_points": [], "label_slots": []}
    return [
        _record(
            "ND0004", "Modular Valve Double Isolation and Bleed", "Modular Valve Double Isolation and Bleed",
            "Valves", "Piping / P&ID", "Valve", "Piping", "PipingComponent", ["CustomOperatedValve"],
            "DoubleBlockAndBleedValve", "CustomOperatedValve/DoubleBlockAndBleedValve",
            [
                _asset("primary", "ND0004.svg", "Symbols/ND0004.svg", "ND0004"),
                _asset("option", "ND0004_option1.svg", "Symbols/ND0004_Option1.svg", "ND0004 option 1",
                       option_index=1, condition="ValvePosition = 'NC'"),
                _asset("option", "ND0004_option2.svg", "Symbols/ND0004_Option2.svg", "ND0004 option 2",
                       option_index=2, condition=None),
            ],
            valve_geometry,
            [{"index": 1, "condition": "ValvePosition = 'NC'", "asset": "ND0004_option1.svg"},
             {"index": 2, "condition": None, "asset": "ND0004_option2.svg"}],
            [
                {"system": "DEXPI RDL", "identifier": "http://sandbox.example.test/rdl/CustomOperatedValve",
                 "label": "CustomOperatedValve", "relation": "broader"},
                {"system": "POSC Caesar RDL", "identifier": "http://rdl.example.test/RDS552689",
                 "label": "DOUBLE BLOCK AND BLEED VALVE", "relation": "exact"},
            ],
            custom_type={"label": "DOUBLE BLOCK AND BLEED VALVE", "rdl_uri": "http://rdl.example.test/RDS552689"},
            warnings=["Origo and base SVG differ in Symbol cells; base SVG geometry used"],
        ),
        _record(
            "ND0136", "Pump Centrifugal", "Pump Centrifugal", "Pumps", "Piping / P&ID", "Pump", "Equipment",
            "Equipment", ["CentrifugalPump"], "CentrifugalPump", "CentrifugalPump",
            [_asset("primary", "ND0136.svg", "Symbols/PP001A.svg", "ND0136")],
            {**plain_geometry, "connection_points": [
                {"index": 1, "x_mm": 0.0, "y_mm": 0.0, "directions_deg": [180.0], "kinds": ["piping"]},
                {"index": 2, "x_mm": 0.0, "y_mm": -8.0, "directions_deg": [0.0], "kinds": ["piping"]}],
             "label_slots": [{"label_index": "A", "lines": 1, "box_mm": [-2, 11, 2, 12], "template": "<ObjectDisplayName>"}]},
            [], [],
        ),
        _record(
            "ND0114", "Exch. Shell and Fuced Tube", "Exch. Shell and Fuced Tube", "Equipment", "Piping / P&ID",
            "Equipment", "Equipment", "Equipment", ["CustomHeatExchanger"], "ShellAndFixedTubeHeatExchanger",
            "CustomHeatExchanger/ShellAndFixedTubeHeatExchanger",
            [_asset("primary", "ND0114.svg", "Symbols/PE037A.svg", "ND0114")],
            {**plain_geometry, "connection_points": [
                {"index": i, "x_mm": float(i), "y_mm": 8.0, "directions_deg": [90.0], "kinds": ["piping"]} for i in range(1, 5)]},
            [], [
                {"system": "POSC Caesar RDL", "identifier": "http://rdl.example.test/RDS439604",
                 "label": "SHELL AND FIXED TUBE HEAT EXCHANGER", "relation": "exact"},
            ],
            custom_type={"label": "SHELL AND FIXED TUBE HEAT EXCHANGER", "rdl_uri": "http://rdl.example.test/RDS439604"},
        ),
        _record(
            "ND0050", "Label Plain", "Label Plain", "Annotations / Tags", "Piping / P&ID", "Label", "Annotation",
            "Symbol", ["Label"], "Label", "Label",
            [_asset("primary", "ND0050.svg", "Symbols/ND0050.svg", "ND0050")],
            plain_geometry, [], [],
            warnings=["base SVG has no Symbol layer; used all non-construction cells"],
        ),
    ]


def build_package() -> dict:
    manifest = build_records()
    svg: dict[str, bytes] = {}
    for record in manifest:
        for asset in record["assets"]:
            svg[asset["filename"]] = _BYTES[asset["filename"]]
    geometry = [r["payload"]["geometry"] for r in manifest]
    report = {
        "imported": len(manifest),
        "held_back": 2,
        "option_assets": sum(1 for r in manifest for a in r["assets"] if a["role"] == "option"),
        "with_connection_points": sum(1 for g in geometry if g["connection_points"]),
        "with_label_slots": sum(1 for g in geometry if g["label_slots"]),
        "categories": {},
        "disciplines": {},
        "scheme_nodes": {"package": 3, "dexpi_class": 6, "custom_type": 2},
    }
    for record in manifest:
        report["categories"][record["category"]] = report["categories"].get(record["category"], 0) + 1
        report["disciplines"][record["discipline"]] = report["disciplines"].get(record["discipline"], 0) + 1
    held_back = [
        {"disc_id": "ND0900", "description": "Held back one", "dexpi_class": "Label",
         "mapping_status": "TBD", "reason": "register mapping status TBD"},
        {"disc_id": "ND0901", "description": "Held back two", "dexpi_class": "Label",
         "mapping_status": "", "reason": "register mapping status blank"},
    ]
    return {
        "manifest": manifest,
        "scheme": copy.deepcopy(SCHEME),
        "report": report,
        "config": dict(CONFIG),
        "held_back": held_back,
        "svg": svg,
        "converter_version": "sha256:0000000000000000",
    }


def write_package(package: dict, directory: Path) -> Path:
    """Write a package in the layout `disc_dexpi_ingest.load_package` reads."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(package["manifest"]), encoding="utf-8")
    (directory / "dexpi_class_scheme.json").write_text(json.dumps(package["scheme"]), encoding="utf-8")
    (directory / "report.json").write_text(json.dumps(package["report"]), encoding="utf-8")
    (directory / "config.json").write_text(json.dumps(package["config"]), encoding="utf-8")
    (directory / "convert_disc.py").write_text("# stand-in converter\n", encoding="utf-8")
    with (directory / "held_back.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["disc_id", "description", "dexpi_class", "mapping_status", "reason"])
        writer.writeheader()
        writer.writerows(package["held_back"])
    svg_dir = directory / "svg"
    svg_dir.mkdir(exist_ok=True)
    for name, data in package["svg"].items():
        (svg_dir / name).write_bytes(data)
    return directory


def change_primary(package: dict, nd: str, label: str) -> dict:
    """The same package with one symbol's primary SVG replaced, as a re-run after a fix would be."""
    changed = copy.deepcopy(package)
    record = next(r for r in changed["manifest"] if r["source_key"] == f"disc-dexpi-{nd}")
    primary = next(a for a in record["assets"] if a["role"] == "primary")
    data = svg_bytes(label)
    changed["svg"][primary["filename"]] = data
    primary["sha256"] = hashlib.sha256(data).hexdigest()
    primary["size_bytes"] = len(data)
    return changed
