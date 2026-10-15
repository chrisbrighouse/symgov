"""A small stand-in for the Wikimedia Commons harvest package, with the real one's shape.

It exists so the planner, the importer and the checks can be tested without
the 416 real files. Seven rows carry the real package's cases:

* 1001: an ISO 10628-2 file, already clean, on an existing concept (`Pump`).
* 1002: a German ventilation file (HVAC) whose description cites DIN EN 12792,
  with no `viewBox`: a fire damper, so it is also Fire & Life Safety.
* 1003: `import_after_fix` with a raster `<image>` shown through a `<use>`, and
  vector line work that remains. It shares a `NEW:` concept with 1004.
* 1004: an XML comment before the root `<svg>`, which the backend's own image
  validation refuses until the fix removes it.
* 1005: a drop.
* 1006: `import_after_fix` whose only drawing is the raster: excluded.
* 1007: a file that cites no standard, which the gate cannot pass.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

WORKSHEET_COLUMNS = [
    "pageid", "action", "proposed_name", "original_name", "discipline", "category", "concept_key",
    "concept_match", "heuristic_concept", "licence", "author", "commons_url", "local_file", "notes", "confirmed",
]

RASTER = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

SVGS = {
    1001: (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">'
        '<circle cx="20" cy="20" r="15" fill="none" stroke="#000"/><path d="M5 20 H35" stroke="#000"/></svg>'
    ),
    1002: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="774" height="378">'
        '<path d="M0 0 L774 378 M0 378 L774 0" stroke="#000" stroke-width="8"/><rect x="10" y="10" width="754" height="358" fill="none" stroke="#000"/></svg>'
    ),
    1003: (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- Created with Inkscape -->\n'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="30mm" height="20mm" viewBox="0 0 30 20">'
        f'<defs><image id="raster1" width="30" height="20" xlink:href="{RASTER}"/></defs>'
        '<use xlink:href="#raster1"/>'
        '<path d="M2 2 H28 V18 H2 Z" fill="none" stroke="#000" stroke-width="0.5"/></svg>'
    ),
    1004: (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!-- Created with Inkscape (http://www.inkscape.org/) -->\n\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="20" viewBox="0 0 50 20">'
        '<path d="M0 10 H50" stroke="#000"/><path d="M20 0 V20" stroke="#000"/></svg>'
    ),
    1005: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><path d="M0 0 H10"/></svg>',
    1006: (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10" viewBox="0 0 10 10">'
        f'<image width="10" height="10" xlink:href="{RASTER}"/></svg>'
    ),
    1007: (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">'
        '<polygon points="0,0 20,20 20,0 0,20" fill="none" stroke="#000"/></svg>'
    ),
}

ROWS = [
    # pageid, action, name, original, discipline, category, concept, title, author, description, licence family
    (1001, "import", "Pump, centrifugal", "Centrifugal pump (ISO 10628-2)", "Piping / P&ID", "Pumps", "Pump",
     "File:Centrifugal pump (ISO 10628-2).svg", "ChemSim", "Symbol for a centrifugal pump", "pd"),
    (1002, "import", "Fire damper", "Brandschutzklappe", "HVAC", "Heating / HVAC", "NEW:FireDamper",
     "File:Brandschutzklappe.svg", "BentaxGermany", "Graphical symbol in accordance with DIN EN 12792 for a fire damper .", "pd"),
    (1003, "import_after_fix", "Agitator, anchor type", "Anchor type (ISO 10628-2)", "Piping / P&ID", "Equipment",
     "NEW:AnchorAgitator", "File:Anchor type (ISO 10628-2).svg", "ChemSim", "Symbol for an anchor agitator", "pd"),
    (1004, "import", "Agitator, anchor type (variant)", "Anchor agitator - ISO 10628-2", "Piping / P&ID", "Equipment",
     "NEW:AnchorAgitator", "File:Anchor agitator - ISO 10628-2.svg", "Chem Sim 2001", "Symbol for an anchor agitator", "cc0"),
    (1005, "drop", "Diagram", "Diagram", "Piping / P&ID", "Equipment", "NEW:Diagram",
     "File:Diagram.svg", "Somebody", "a whole diagram", "pd"),
    (1006, "import_after_fix", "Raster only", "Raster only (ISO 10628-2)", "Piping / P&ID", "Equipment", "NEW:RasterOnly",
     "File:Raster only (ISO 10628-2).svg", "ChemSim", "Only a picture", "pd"),
    (1007, "import", "Valve", "Valve", "Piping / P&ID", "Valves", "NEW:PlainValve",
     "File:Valve.svg", "Snipre", "Symbol for valve", "pd"),
]


def svg_bytes(pageid: int) -> bytes:
    return SVGS[pageid].encode("utf-8")


def build_package() -> dict:
    manifest, worksheet, svg = [], [], {}
    for pageid, action, name, original, discipline, category, concept, title, author, description, family in ROWS:
        data = svg_bytes(pageid)
        local = f"svg/commons-{pageid}.svg"
        svg[local] = data
        page_url = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
        manifest.append(
            {
                "pageid": pageid,
                "title": title,
                "page_url": page_url,
                "file_url": f"https://upload.example.test/{pageid}.svg?utm_source=commons",
                "artist": author,
                "licence_family": family,
                "licence_short_name": "CC0" if family == "cc0" else "Public domain",
                "licence_templates": ["Cc-zero"] if family == "cc0" else ["PD-self", "PD-Layout"],
                "licence_url": None,
                "attribution_required": "false",
                "credit": "Own work",
                "description": description,
                "commons_sha1": hashlib.sha1(data).hexdigest(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "local_file": local,
                "commons_categories": ["Category:P&ID symbols"],
                "uploaded_at": "2014-01-09T15:04:38Z",
                "decision": "candidate",
                "svg_scan": {"problems": []},
            }
        )
        worksheet.append(
            {
                "pageid": str(pageid), "action": action, "proposed_name": name, "original_name": original,
                "discipline": discipline, "category": category, "concept_key": concept, "concept_match": "",
                "heuristic_concept": "", "licence": "CC0" if family == "cc0" else "Public domain", "author": author,
                "commons_url": page_url, "local_file": local, "notes": "", "confirmed": "yes",
            }
        )
    return {"manifest": manifest, "worksheet": worksheet, "svg": svg, "fixed": {}}


def copy_package(package: dict) -> dict:
    return {key: copy.deepcopy(value) for key, value in package.items() if not key.startswith("_")}


def write_package(package: dict, directory: Path) -> Path:
    directory = Path(directory)
    (directory / "svg").mkdir(parents=True, exist_ok=True)
    (directory / "curation").mkdir(exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(package["manifest"], indent=1), encoding="utf-8")
    with (directory / "curation" / "worksheet.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(package["worksheet"])
    for name, data in package["svg"].items():
        (directory / name).write_bytes(data)
    return directory
