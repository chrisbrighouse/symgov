"""What the Wikimedia Commons P&ID import records, decided without a database.

`commons_ingest` writes what this decides, the way `disc_dexpi_ingest` writes
what `disc_dexpi_ingestion` decides. Everything here is a pure function of the
harvest package -- `manifest.json`, the curated `curation/worksheet.csv`, the
downloaded `svg/` files and any `svg-fixed/` files -- with no database, no
filesystem and no clock.

**The package.** The harvester found every file in Commons'
`Category:P&ID symbols` with its licence evidence (`manifest.json`). The product
owner curated one worksheet row per candidate, every row `confirmed=yes`, and
the curated values are final: this module refuses a value it cannot honour, it
never corrects one.

**What is decided here.**

* Which rows are imported (`import`, `import_after_fix`) and which are dropped.
* The SVG each symbol is published from. Three deterministic fixes exist
  (`fix_svg`), each recorded later as one step of the revision's transformation
  chain: an XML comment before the root `<svg>` is removed (the backend's own
  `validate_stored_image` refuses such a file), an embedded raster `<image>`
  is removed, and a missing `viewBox` is derived from `width` and `height`. A
  file that a fix leaves with nothing visible is excluded, not shipped blank.
  `fix-svgs` writes the fixed bytes to `svg-fixed/`; the plan recomputes them
  and refuses a fixed file that disagrees.
* The concept each symbol carries: an existing concept key is reused, a
  `NEW:<Key>` creates one concept per key however many rows share it.
* The standard links. A file whose name or title cites ISO 10628-2 is a
  `vendor_implementation` of ISO 10628 (drawn to the standard, not copied
  from it). See `STATED_STANDARDS` for the other standards a file's own
  Commons description cites.
* The rights record (`open`, `distribute`) with per-file licence evidence.
* What the product owner decided, in `curation/comparisons.csv`, for a symbol
  whose file cites no standard and which the gate therefore holds: compared
  with the ISO 10628-2 drawings already published (a `comparison_only` link),
  kept on hold, or dropped. See `plan_comparisons`.
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..catalog_facets import CATALOG_CATEGORY_ORDER as FACET_CATEGORY_ORDER
from ..catalog_facets import CATALOG_DISCIPLINE_ORDER as FACET_DISCIPLINE_ORDER
from ..catalog_facets import canonical_discipline
from ..catalog_taxonomy import CATALOG_CATEGORY_ORDER as TAXONOMY_CATEGORY_ORDER
from ..catalog_taxonomy import CATALOG_DISCIPLINE_ORDER as TAXONOMY_DISCIPLINE_ORDER
from ..image_content import UnsafeImageContentError, validate_stored_image
from .dexpi_concepts import DEFAULT_CONCEPT_KIND

PACKAGE_CODE = "WIKIMEDIA-COMMONS-PID-20261013"
PUBLICATION_PACK_CODE = "commons-pid"
PUBLICATION_PACK_TITLE = "Wikimedia Commons P&ID symbols (public domain)"
SOURCE_URI = "https://commons.wikimedia.org/wiki/Category:P%26ID_symbols"
RETRIEVED_ON = "2026-10-13"
SLUG_PREFIX = "commons-"
SVG_CONTENT_TYPE = "image/svg+xml"
REVISION_LIFECYCLE_STATE = "approved"
FIRST_REVISION_LABEL = "r1"

IMPORT_ACTIONS = frozenset({"import", "import_after_fix"})
DROP_ACTION = "drop"
ACTIONS = IMPORT_ACTIONS | {DROP_ACTION}
LICENCE_FAMILIES = frozenset({"pd", "cc0"})
NEW_CONCEPT_PREFIX = "NEW:"
# Written on every concept this import creates, so a re-run recognises its own.
NEW_CONCEPT_NOTES = "Concept created by the Wikimedia Commons P&ID import (curation worksheet, 2026-10-13)."
_NEW_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_EXISTING_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

SEMANTIC_METHOD = "source_mapping"
CLASSIFICATION_RULE_METHOD = "rule"
LINK_VERIFICATION_METHOD = "import_manifest"
DISCIPLINE_SCHEME_CODE = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME_CODE = "SYMBOL-CATEGORY-FAMILY"
REPRESENTATION_TYPE_SCHEME_CODE = "REPRESENTATION-TYPE"
REPRESENTATION_TYPE_NODE_CODE = "INTELLIGENT_VECTOR_DRAWING_CAD"

# Decision 5: a fire, smoke, or fire-and-smoke damper is HVAC and also Fire &
# Life Safety. The second discipline is a `secondary` ENGINEERING-DISCIPLINE
# assignment; the symbol's own `discipline` column stays the curated HVAC.
FIRE_DISCIPLINE = "Fire & Life Safety"
FIRE_DAMPER_PATTERN = re.compile(r"\b(fire|smoke)\b[\w\s&-]*\bdampers?\b", re.IGNORECASE)

FIX_TOOL_NAME = "symgov_backend.services.commons_ingestion.fix_svg"
FIX_TOOL_VERSION = "1"

ISO_10628_CODE = "ISO 10628"
ISO_10628_TITLE = "ISO 10628 -- Diagrams for the chemical and petrochemical industry"
ISO_10628_EDITION = "2012"
ISO_10628_PART_2 = "ISO 10628-2"

# Decision 3, and the only standard the product decisions name. Matched on the
# worksheet's `original_name` or the Commons title.
TITLE_STANDARD = {
    "cited_as": ISO_10628_PART_2,
    "standard_code": ISO_10628_CODE,
    "title": ISO_10628_TITLE,
    "issuing_body": "ISO",
    # The file cites the part and no edition. ISO 10628-2:2012 is that part's
    # only edition, and it is the edition the existing ISO 10628 record
    # (registered by the DEXPI pilot) carries.
    "version_label": ISO_10628_EDITION,
    "edition_basis": "ISO 10628-2 is cited without an edition; 2012 is its only edition and the one the existing ISO 10628 record carries",
}

# NOT a product decision: standards a file's own Commons description says it is
# drawn "in accordance with". Without a verified standard link the publication
# gate refuses a symbol (`graphical_authority_unasserted`), so the 40
# ventilation symbols -- every HVAC symbol in the package -- could never be
# published. Each is the same honest `vendor_implementation`, against the
# standard the author names, with the edition left as unstated because the
# author states none. Remove an entry to withdraw it.
STATED_STANDARDS: tuple[dict[str, Any], ...] = (
    {
        "pattern": re.compile(r"\b(?:DIN\s+)?EN\s*12792\b"),
        "cited_as": "DIN EN 12792",
        "standard_code": "DIN EN 12792",
        "title": "Ventilation for buildings -- Symbols, terminology and graphical symbols",
        "issuing_body": "DIN (national adoption of CEN EN 12792)",
        "version_label": "edition not stated",
        "edition_basis": "the Commons description cites the standard without an edition",
    },
    {
        "pattern": re.compile(r"\bDIN\s*2429\b"),
        "cited_as": "DIN 2429",
        "standard_code": "DIN 2429",
        "title": "Graphical symbols for technical drawings -- Pipework",
        "issuing_body": "DIN",
        "version_label": "edition not stated",
        "edition_basis": "the Commons description cites the standard without an edition",
    },
)

ATTRIBUTION_TEXT = (
    "P&ID symbols from Wikimedia Commons (Category:P&ID symbols), released into the public domain "
    "or under CC0 by their authors. Each symbol credits its author and links its Commons file page."
)

SVG_NS = "{http://www.w3.org/2000/svg}"
# Subtrees that never draw by themselves.
_NON_RENDERING = frozenset(
    {
        "defs", "metadata", "title", "desc", "clipPath", "mask", "symbol", "pattern", "marker",
        "linearGradient", "radialGradient", "filter", "style", "script",
    }
)
_DRAWING = frozenset({"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"})
_ROOT_START = re.compile(rb"<svg[\s>/]")
_COMMENT = re.compile(rb"<!--.*?-->\s*", re.S)
_IMAGE_ELEMENT = re.compile(rb"<(?:svg:)?image\b[^>]*?(?:/>|>.*?</(?:svg:)?image\s*>)", re.S)
_ID_ATTRIBUTE = re.compile(rb"""\bid\s*=\s*["']([^"']+)["']""")
_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(px|mm|cm|in|pt|pc)?\s*$")
_PX_PER_UNIT = {None: 1.0, "px": 1.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "pt": 96 / 72, "pc": 16.0}


class CommonsPlanError(ValueError):
    """The package does not support an import. Names data, never a credential."""


# --------------------------------------------------------------------------
# Small helpers.
# --------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug_for(pageid: Any) -> str:
    return f"{SLUG_PREFIX}{int(pageid)}"


def svg_filename(pageid: Any) -> str:
    return f"commons-{int(pageid)}.svg"


def asset_object_key(slug: str, revision_label: str, digest: str) -> str:
    """Content-addressed and scoped to the revision, as the DISC import keys its SVGs."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise CommonsPlanError(f"{slug}: no SHA-256 digest for its SVG")
    return f"commons/{PACKAGE_CODE}/{slug}/{revision_label}/primary-{digest}.svg"


def clean_text(value: Any) -> str:
    """Commons descriptions are HTML fragments: unescape, drop tags, collapse whitespace."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_url(value: Any) -> str | None:
    """A Commons file URL without the tracking query the API appends."""
    if not value:
        return None
    parts = urlsplit(str(value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def concept_name_for(concept_key: str) -> str:
    return concept_key[len(NEW_CONCEPT_PREFIX):] if concept_key.startswith(NEW_CONCEPT_PREFIX) else concept_key


def is_new_concept(concept_key: str) -> bool:
    return concept_key.startswith(NEW_CONCEPT_PREFIX)


def _fold(value: str) -> str:
    return value.casefold()


def _loose(value: str) -> str:
    """The near-miss key `disc_dexpi_ingestion` uses: letters and digits only, case-folded."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def is_fire_damper(name: str, discipline: str) -> bool:
    return discipline == "HVAC" and bool(FIRE_DAMPER_PATTERN.search(name or ""))


# --------------------------------------------------------------------------
# SVG safety and fixes.
# --------------------------------------------------------------------------


def scan_svg(raw: bytes) -> list[str]:
    """The harvester's safety scan (`tools/harvest_commons.py`), unchanged in substance.

    Never expands entities. Every finding is a problem except `embedded_raster`,
    which `fix_svg` removes. Any `<image>` element is reported as a raster,
    whatever its href.
    """
    problems: list[str] = []
    if b"<!ENTITY" in raw:
        return ["entity_declaration"]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        return [f"parse_error: {error}"]
    if root.tag != SVG_NS + "svg":
        problems.append(f"root_not_svg:{root.tag}")
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in ("script", "foreignObject"):
            problems.append(f"element:{local}")
        if local == "image":
            problems.append("embedded_raster")
        for name, value in element.attrib.items():
            attr = name.rsplit("}", 1)[-1]
            if attr.lower().startswith("on"):
                problems.append(f"event_attribute:{attr}")
            if attr == "href":
                if value.startswith("data:image"):
                    problems.append("embedded_raster")
                elif not value.startswith("#"):
                    problems.append(f"external_href:{value[:80]}")
    return sorted(set(problems))


def has_raster(raw: bytes) -> bool:
    return "embedded_raster" in scan_svg(raw)


def _root_match(raw: bytes) -> re.Match:
    match = _ROOT_START.search(raw)
    if match is None:
        raise CommonsPlanError("no <svg> root element")
    return match


def _remove_prologue_comments(raw: bytes) -> tuple[bytes, dict[str, Any] | None]:
    start = _root_match(raw).start()
    prefix = raw[:start]
    if b"<!--" not in prefix:
        return raw, None
    removed = [comment.strip().decode("utf-8", "replace") for comment in _COMMENT.findall(prefix)]
    return _COMMENT.sub(b"", prefix) + raw[start:], {"removed_comments": removed}


def _remove_rasters(raw: bytes) -> tuple[bytes, dict[str, Any] | None]:
    images = list(_IMAGE_ELEMENT.finditer(raw))
    if not images:
        return raw, None
    ids = []
    removed_bytes = 0
    for match in images:
        removed_bytes += len(match.group(0))
        found = _ID_ATTRIBUTE.search(match.group(0))
        if found:
            ids.append(found.group(1))
    fixed = _IMAGE_ELEMENT.sub(b"", raw)
    removed_uses = 0
    for identifier in ids:
        use = re.compile(
            rb"<(?:svg:)?use\b[^>]*?href\s*=\s*[\"']#" + re.escape(identifier) + rb"[\"'][^>]*?(?:/>|>.*?</(?:svg:)?use\s*>)",
            re.S,
        )
        fixed, count = use.subn(b"", fixed)
        removed_uses += count
    return fixed, {
        "removed_images": len(images),
        "removed_image_bytes": removed_bytes,
        "removed_uses_of_images": removed_uses,
    }


def _px(value: str | None) -> float | None:
    if value is None:
        return None
    match = _LENGTH.match(value)
    if match is None:
        return None
    return float(match.group(1)) * _PX_PER_UNIT[match.group(2)]


def _number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _add_viewbox(raw: bytes) -> tuple[bytes, dict[str, Any] | None]:
    root = ET.fromstring(raw)
    if root.get("viewBox"):
        return raw, None
    width, height = root.get("width"), root.get("height")
    width_px, height_px = _px(width), _px(height)
    if not width_px or not height_px:
        raise CommonsPlanError(f"no viewBox, and width {width!r} / height {height!r} give no size to derive one from")
    view_box = f"0 0 {_number(width_px)} {_number(height_px)}"
    end = _root_match(raw).start() + len(b"<svg")
    return raw[:end] + f' viewBox="{view_box}"'.encode("ascii") + raw[end:], {
        "viewBox": view_box,
        "width": width,
        "height": height,
    }


def visible_elements(raw: bytes) -> int:
    """How many drawing elements would render: outside defs and the like, not hidden."""
    root = ET.fromstring(raw)
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    count = 0

    def hidden(element) -> bool:
        style = (element.get("style") or "").replace(" ", "").lower()
        return element.get("display") == "none" or "display:none" in style or element.get("visibility") == "hidden"

    def walk(element) -> None:
        nonlocal count
        local = element.tag.rsplit("}", 1)[-1]
        if local in _NON_RENDERING or hidden(element):
            return
        if local in _DRAWING:
            if local == "path" and not (element.get("d") or "").strip():
                pass
            elif local == "use":
                target = None
                for name, value in element.attrib.items():
                    if name.rsplit("}", 1)[-1] == "href":
                        target = value
                if target and target.startswith("#") and target[1:] in ids:
                    count += 1
            elif local == "text" and not "".join(element.itertext()).strip():
                pass
            else:
                count += 1
        for child in element:
            walk(child)

    walk(root)
    return count


def fix_svg(raw: bytes) -> dict[str, Any]:
    """The fixed bytes and the steps that produced them, or the reason to exclude the file.

    Pure and deterministic, so the plan can recompute what `fix-svgs` wrote and
    refuse a fixed file that disagrees. Each step names its input and output
    digest; they become the revision's transformation chain.
    """
    steps: list[dict[str, Any]] = []
    current = raw
    original_visible = visible_elements(raw)
    for name, function in (
        ("removed_prologue_comment", _remove_prologue_comments),
        ("removed_embedded_raster", _remove_rasters),
        ("added_viewbox", _add_viewbox),
    ):
        fixed, detail = function(current)
        if detail is None:
            continue
        steps.append(
            {
                "fix": name,
                "source_sha256": sha256_hex(current),
                "derived_sha256": sha256_hex(fixed),
                "detail": detail,
            }
        )
        current = fixed
    result = {
        "original_sha256": sha256_hex(raw),
        "fixed_sha256": sha256_hex(current),
        "bytes": current,
        "steps": steps,
        "excluded": None,
        "visible_elements": visible_elements(current),
        "original_visible_elements": original_visible,
    }
    if result["visible_elements"] == 0:
        result["excluded"] = "nothing visible remains once the embedded raster is removed"
    return result


def final_svg_problems(data: bytes) -> list[str]:
    """Why a file cannot be uploaded as it stands: the safety scan, then the backend's own check."""
    problems = scan_svg(data)
    try:
        validate_stored_image(data, SVG_CONTENT_TYPE)
    except UnsafeImageContentError as exc:
        problems.append(f"validate_stored_image: {exc}")
    return problems


# --------------------------------------------------------------------------
# Standards.
# --------------------------------------------------------------------------


def standard_assertions(row: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The standards this file says it is drawn to. Always `vendor_implementation`."""
    assertions = []
    title = str(entry.get("title") or "")
    if ISO_10628_PART_2 in row.get("original_name", "") or ISO_10628_PART_2 in title:
        assertions.append({**TITLE_STANDARD, "basis": "commons_title_cites_standard"})
    else:
        stated = " ".join(str(entry.get(field) or "") for field in ("description", "credit"))
        for rule in STATED_STANDARDS:
            if rule["pattern"].search(stated):
                assertions.append(
                    {key: value for key, value in rule.items() if key != "pattern"}
                    | {"basis": "commons_description_cites_standard"}
                )
    for assertion in assertions:
        assertion["relationship_type"] = "vendor_implementation"
    return assertions


# --------------------------------------------------------------------------
# Validating the package.
# --------------------------------------------------------------------------


def _manifest_index(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(entry["pageid"]): entry for entry in package["manifest"]}


def fix_results(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`fix_svg` for every imported row, keyed by pageid. Cached on the package dict."""
    cached = package.get("_fixes")
    if cached is not None:
        return cached
    results: dict[str, dict[str, Any]] = {}
    manifest = _manifest_index(package)
    for row in package["worksheet"]:
        if row.get("action") not in IMPORT_ACTIONS:
            continue
        entry = manifest.get(str(row.get("pageid")))
        if entry is None:
            continue
        data = package["svg"].get(entry["local_file"])
        if data is None:
            continue
        try:
            results[str(row["pageid"])] = fix_svg(data)
        except (CommonsPlanError, ET.ParseError) as exc:
            results[str(row["pageid"])] = {"error": str(exc)}
    package["_fixes"] = results
    return results


def validate_package(package: dict[str, Any]) -> dict[str, Any]:
    """Every way the package cannot be imported as curated, and what the fixes do.

    Returns `problems` (each blocks the import) and `excluded` (files a fix
    would leave blank; they are reported and not imported).
    """
    problems: list[str] = []
    excluded: list[dict[str, Any]] = []
    manifest = _manifest_index(package)
    worksheet = package["worksheet"]
    fixed_files = package.get("fixed") or {}
    seen: set[str] = set()
    disciplines = set(FACET_DISCIPLINE_ORDER) & set(TAXONOMY_DISCIPLINE_ORDER)
    categories = set(FACET_CATEGORY_ORDER) & set(TAXONOMY_CATEGORY_ORDER)
    fixes = fix_results(package)
    for row in worksheet:
        pageid = str(row.get("pageid") or "").strip()
        label = f"row {pageid or '?'}"
        if not pageid.isdigit():
            problems.append(f"{label}: pageid is not a number")
            continue
        if pageid in seen:
            problems.append(f"{label}: pageid appears twice in the worksheet")
        seen.add(pageid)
        if (row.get("confirmed") or "").strip().lower() != "yes":
            problems.append(f"{label}: not confirmed")
        action = row.get("action")
        if action not in ACTIONS:
            problems.append(f"{label}: action {action!r} is not import, import_after_fix or drop")
            continue
        entry = manifest.get(pageid)
        if entry is None:
            problems.append(f"{label}: not in manifest.json")
            continue
        if action == DROP_ACTION:
            continue
        if row.get("local_file") and row["local_file"] != entry["local_file"]:
            problems.append(f"{label}: worksheet local_file {row['local_file']} differs from the manifest's {entry['local_file']}")
        if not (row.get("proposed_name") or "").strip():
            problems.append(f"{label}: no proposed_name")
        discipline = row.get("discipline") or ""
        if discipline not in disciplines or canonical_discipline(discipline) != discipline:
            problems.append(f"{label}: discipline {discipline!r} is not a canonical discipline")
        if (row.get("category") or "") not in categories:
            problems.append(f"{label}: category {row.get('category')!r} is not a canonical category")
        key = (row.get("concept_key") or "").strip()
        if is_new_concept(key):
            if not _NEW_KEY.match(concept_name_for(key)):
                problems.append(f"{label}: concept key {key!r} is not NEW:<CamelCaseKey>")
        elif not _EXISTING_KEY.match(key):
            problems.append(f"{label}: concept key {key!r} is neither an existing key nor NEW:<Key>")
        if entry.get("licence_family") not in LICENCE_FAMILIES:
            problems.append(f"{label}: licence family {entry.get('licence_family')!r} is not pd or cc0")
        if entry.get("decision") != "candidate":
            problems.append(f"{label}: the harvester did not make it a candidate ({entry.get('decision')})")
        data = package["svg"].get(entry["local_file"])
        if data is None:
            problems.append(f"{label}: {entry['local_file']} is not in svg/")
            continue
        if sha256_hex(data) != entry.get("sha256"):
            problems.append(f"{label}: {entry['local_file']} does not match its sha256")
            continue
        if entry.get("size_bytes") is not None and len(data) != entry["size_bytes"]:
            problems.append(f"{label}: {entry['local_file']} does not match its size")
        fix = fixes.get(pageid) or {}
        if "error" in fix:
            problems.append(f"{label}: {fix['error']}")
            continue
        if fix["excluded"]:
            excluded.append(
                {"pageid": int(pageid), "name": row.get("proposed_name"), "reason": fix["excluded"],
                 "original_sha256": fix["original_sha256"]}
            )
            continue
        raster = any(step["fix"] == "removed_embedded_raster" for step in fix["steps"])
        filename = svg_filename(pageid)
        stored_fix = fixed_files.get(filename)
        if fix["steps"]:
            if stored_fix is None:
                if raster:
                    problems.append(f"{label}: embeds a raster <image> and svg-fixed/{filename} does not exist; run fix-svgs")
                else:
                    problems.append(f"{label}: needs {', '.join(s['fix'] for s in fix['steps'])} and svg-fixed/{filename} does not exist; run fix-svgs")
                continue
            if sha256_hex(stored_fix) != fix["fixed_sha256"]:
                problems.append(f"{label}: svg-fixed/{filename} is not what fix-svgs produces; run fix-svgs again")
                continue
        elif stored_fix is not None and stored_fix != data:
            problems.append(f"{label}: svg-fixed/{filename} exists but the file needs no fix")
            continue
        final = stored_fix if fix["steps"] else data
        for problem in final_svg_problems(final):
            problems.append(f"{label}: the SVG to upload fails the safety check ({problem})")
        if action == "import_after_fix" and not fix["steps"]:
            problems.append(f"{label}: import_after_fix, but the file needs no fix")
    return {"problems": problems, "excluded": excluded}


# --------------------------------------------------------------------------
# Concepts.
# --------------------------------------------------------------------------


def plan_concepts(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct concept key, in key order. Shared `NEW:` keys make one concept."""
    grouped: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        key = symbol["concept_key"]
        bucket = grouped.setdefault(key, {"names": [], "descriptions": [], "symbols": []})
        bucket["names"].append(symbol["canonical_name"])
        if symbol["commons"]["description"]:
            bucket["descriptions"].append(symbol["commons"]["description"])
        bucket["symbols"].append(symbol["slug"])
    concepts = []
    for key in sorted(grouped):
        bucket = grouped[key]
        name = concept_name_for(key)
        concept = {
            "concept_key": key,
            "preferred_name": name,
            "is_new": is_new_concept(key),
            "symbols": sorted(bucket["symbols"]),
        }
        if concept["is_new"]:
            aliases: list[str] = []
            for label in bucket["names"]:
                if label and _fold(label) != _fold(name) and label not in aliases:
                    aliases.append(label)
            described = bucket["descriptions"][0] if bucket["descriptions"] else None
            concept.update(
                {
                    "concept_kind": DEFAULT_CONCEPT_KIND,
                    "definition": (
                        f"{', '.join(bucket['names'][:3])}: a P&ID symbol concept. "
                        + (f"Its Wikimedia Commons description reads: \"{described}\". " if described else "")
                        + "Created from the curated Wikimedia Commons P&ID symbol import, which names the drawing "
                        "but publishes no further definition."
                    )[:4000],
                    "aliases": aliases,
                    "notes": NEW_CONCEPT_NOTES,
                    "rationale": "Named by the product owner in the curation worksheet as a new concept.",
                }
            )
        concepts.append(concept)
    return concepts


def match_concepts(
    planned: list[dict[str, Any]], existing: dict[str, dict[str, Any]], own: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Reuse, create, and what cannot be honoured.

    `existing` is `dexpi_seed.index_existing`: every concept by folded preferred
    name. An existing key must be there exactly. A `NEW:` key that exactly names
    an existing concept cannot be created without duplicating it, so it is a
    problem. A near match (`disc_dexpi_ingestion`'s letters-and-digits rule) is
    reported and does not stop the import: the curated value is final.
    `own` holds the folded names of concepts an earlier run of this import
    created; a `NEW:` key that finds one of those reuses it, so a re-run
    changes nothing.
    """
    reuse, create, problems, near_misses = [], [], [], []
    loose_existing: dict[str, list[str]] = defaultdict(list)
    for name in existing:
        loose_existing[_loose(name)].append(name)
    new_loose: dict[str, list[str]] = defaultdict(list)
    for concept in planned:
        folded = _fold(concept["preferred_name"])
        match = existing.get(folded)
        if not concept["is_new"]:
            if match is None:
                problems.append(f"concept key {concept['concept_key']} does not exist")
            else:
                reuse.append({**concept, "concept_code": match["concept_code"], "concept_id": str(match["concept_id"])})
            continue
        if match is not None and folded in own:
            reuse.append({**concept, "concept_code": match["concept_code"], "concept_id": str(match["concept_id"]),
                          "created_by_this_import": True})
            continue
        if match is not None:
            problems.append(
                f"{concept['concept_key']} asks for a new concept, but {match['concept_code']} is already named "
                f"{concept['preferred_name']}"
            )
            continue
        similar = [name for name in loose_existing.get(_loose(concept["preferred_name"]), []) if name != folded]
        if similar:
            near_misses.append({"concept_key": concept["concept_key"], "existing_names": sorted(similar)})
        new_loose[_loose(concept["preferred_name"])].append(concept["concept_key"])
        create.append(concept)
    within = [sorted(keys) for keys in new_loose.values() if len(keys) > 1]
    for keys in within:
        near_misses.append({"concept_key": keys[0], "new_keys_alike": keys})
    return {"reuse": reuse, "create": create, "problems": problems, "near_misses": near_misses}


# --------------------------------------------------------------------------
# The symbols.
# --------------------------------------------------------------------------


def _commons_block(row: dict[str, Any], entry: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    return {
        "pageid": int(entry["pageid"]),
        "title": entry.get("title"),
        "page_url": entry.get("page_url"),
        "file_url": clean_url(entry.get("file_url")),
        "author": (row.get("author") or entry.get("artist") or "").strip() or None,
        "licence": entry.get("licence_short_name"),
        "licence_family": entry.get("licence_family"),
        "licence_templates": list(entry.get("licence_templates") or []),
        "licence_url": entry.get("licence_url"),
        "attribution_required": str(entry.get("attribution_required")).lower() == "true",
        "credit": clean_text(entry.get("credit")) or None,
        "description": clean_text(entry.get("description")) or None,
        "original_name": row.get("original_name") or None,
        "uploaded_at": entry.get("uploaded_at"),
        "retrieved_on": RETRIEVED_ON,
        "commons_sha1": entry.get("commons_sha1"),
        "original_sha256": entry.get("sha256"),
        "categories": list(entry.get("commons_categories") or []),
        "fixes": [step["fix"] for step in fix["steps"]],
    }


def build_revision_payload(symbol: dict[str, Any], *, revision_label: str) -> dict[str, Any]:
    """The payload the Catalogue renders. `commons` is the provenance the Details view can show."""
    asset = {
        "object_key": asset_object_key(symbol["slug"], revision_label, symbol["final_sha256"]),
        "filename": svg_filename(symbol["pageid"]),
        "content_type": SVG_CONTENT_TYPE,
        "format": "svg",
        "role": "primary",
        "sha256": symbol["final_sha256"],
        "size_bytes": symbol["final_size"],
        "source_path": symbol["entry"]["source_path"],
    }
    commons = symbol["commons"]
    aliases = [alias for alias in [commons["original_name"]] if alias and alias != symbol["canonical_name"]]
    keywords = ["P&ID", "Wikimedia Commons", symbol["category"], symbol["discipline"]]
    for alias in aliases:
        if alias not in keywords:
            keywords.append(alias)
    summary = f"{symbol['canonical_name']}. P&ID symbol from Wikimedia Commons by {commons['author'] or 'an unnamed author'} ({commons['licence']})."
    return {
        "name": symbol["canonical_name"],
        "summary": summary,
        "description": commons["description"] or summary,
        "aliases": aliases,
        "keywords": keywords,
        "assets": [dict(asset)],
        "visual_assets": {"source_assets": [dict(asset)], "state_variants": []},
        "semantic_concept": {"concept_key": concept_name_for(symbol["concept_key"])},
        "standards": [
            {"standard_code": item["standard_code"], "cited_as": item["cited_as"], "relationship_type": item["relationship_type"]}
            for item in symbol["standards"]
        ],
        "commons": dict(commons),
    }


def plan_symbols(package: dict[str, Any], excluded: set[str]) -> list[dict[str, Any]]:
    manifest = _manifest_index(package)
    fixes = fix_results(package)
    symbols = []
    for order, row in enumerate(package["worksheet"]):
        if row.get("action") not in IMPORT_ACTIONS:
            continue
        pageid = str(row["pageid"]).strip()
        if pageid in excluded:
            continue
        entry = manifest[pageid]
        fix = fixes[pageid]
        name = row["proposed_name"].strip()
        key = row["concept_key"].strip()
        symbol = {
            "pageid": int(pageid),
            "slug": slug_for(pageid),
            "action": row["action"],
            "canonical_name": name,
            "category": row["category"],
            "discipline": row["discipline"],
            "sort_order": order,
            "concept_key": key,
            "fire": is_fire_damper(name, row["discipline"]),
            "original_sha256": entry["sha256"],
            "final_sha256": fix["fixed_sha256"],
            "final_size": len(fix["bytes"]),
            "fix_steps": fix["steps"],
            "standards": standard_assertions(row, entry),
            "commons": _commons_block(row, entry, fix),
            "entry": {
                "source_label": name,
                "provider_entry_identifier": pageid,
                "source_path": str(entry.get("title") or entry["local_file"]),
                "original_asset_sha256": entry["sha256"],
            },
            "rationale": (
                f"Imported from Wikimedia Commons file {entry.get('title')} ({entry.get('licence_short_name')}); "
                "name, discipline, category and concept as curated by the product owner."
            ),
        }
        symbols.append(symbol)
    return symbols


def revision_needs_bump(existing_payload: dict[str, Any] | None, symbol: dict[str, Any]) -> list[str]:
    """Why an existing revision no longer matches the plan, as reasons. Empty means unchanged."""
    if existing_payload is None:
        return ["no revision"]
    reasons = []
    stored = ((existing_payload.get("assets") or [{}])[0]).get("sha256")
    if stored != symbol["final_sha256"]:
        reasons.append("SVG sha256 changed")
    if existing_payload.get("name") != symbol["canonical_name"]:
        reasons.append("name changed")
    if (existing_payload.get("semantic_concept") or {}).get("concept_key") != concept_name_for(symbol["concept_key"]):
        reasons.append("concept changed")
    return reasons


def next_revision_label(labels: list[str]) -> str:
    numbers = [int(label[1:]) for label in labels if re.fullmatch(r"r[0-9]+", label)]
    return f"r{max(numbers) + 1}" if numbers else FIRST_REVISION_LABEL


# --------------------------------------------------------------------------
# Rights.
# --------------------------------------------------------------------------


def rights_plan(symbols: list[dict[str, Any]]) -> dict[str, Any]:
    """Decision 1: one `open` record on the package, with a per-file evidence list."""
    files = [
        {
            "pageid": symbol["pageid"],
            "commons_url": symbol["commons"]["page_url"],
            "author": symbol["commons"]["author"],
            "licence_short_name": symbol["commons"]["licence"],
            "licence_templates": symbol["commons"]["licence_templates"],
            "commons_sha1": symbol["commons"]["commons_sha1"],
            "retrieved_on": RETRIEVED_ON,
        }
        for symbol in symbols
    ]
    families = Counter(symbol["commons"]["licence_family"] for symbol in symbols)
    return {
        "rights_status": "open",
        "disposition": "distribute",
        "determination_method": "licence_document",
        "licence_reference": (
            "Public domain or CC0 1.0, per each file's Wikimedia Commons licence templates "
            f"(retrieved {RETRIEVED_ON}); the per-file list is in this record's evidence."
        ),
        "decision_reason": (
            "Every file carries a public-domain or CC0 licence template on its Commons page; none states a "
            "restriction or requires attribution. Authors are credited anyway, as good practice."
        ),
        "evidence": {
            "basis": "Wikimedia Commons file pages: public-domain and CC0 licence templates",
            "source": SOURCE_URI,
            "retrieved_on": RETRIEVED_ON,
            "attribution_text": ATTRIBUTION_TEXT,
            "attribution_is_placeholder": False,
            "attribution_required": False,
            "licensor": "Wikimedia Commons contributors (each file's author)",
            "creator": "Wikimedia Commons contributors",
            "licence_families": dict(sorted(families.items())),
            "file_count": len(files),
            "files": files,
        },
    }


def required_evidence_keys() -> tuple[str, ...]:
    return ("pageid", "commons_url", "author", "licence_short_name", "licence_templates", "commons_sha1", "retrieved_on")


# --------------------------------------------------------------------------
# The whole plan.
# --------------------------------------------------------------------------


def plan_import(package: dict[str, Any]) -> dict[str, Any]:
    """Everything the importer can decide without a database."""
    validation = validate_package(package)
    excluded = {str(item["pageid"]) for item in validation["excluded"]}
    problems = list(validation["problems"])
    symbols = plan_symbols(package, excluded) if not problems else _plan_symbols_tolerant(package, excluded)
    concepts = plan_concepts(symbols)
    standards: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        for item in symbol["standards"]:
            definition = standards.setdefault(
                item["standard_code"],
                {"standard_code": item["standard_code"], "title": item["title"], "issuing_body": item["issuing_body"], "versions": set()},
            )
            definition["versions"].add(item["version_label"])
    worksheet = package["worksheet"]
    drops = [
        {"pageid": int(row["pageid"]), "name": row.get("proposed_name"), "notes": row.get("notes")}
        for row in worksheet
        if row.get("action") == DROP_ACTION
    ]
    return {
        "package_problems": problems,
        "excluded": validation["excluded"],
        "drops": drops,
        "symbols": symbols,
        "concepts": concepts,
        "standards": [
            {**definition, "versions": sorted(definition["versions"])} for _code, definition in sorted(standards.items())
        ],
        "source_package": {
            "package_code": PACKAGE_CODE,
            "title": "Wikimedia Commons: Category:P&ID symbols (public domain and CC0 SVGs)",
            "provider": "Wikimedia Commons",
            "source_uri": SOURCE_URI,
            "release_version": f"retrieved {RETRIEVED_ON}",
            "acquisition_method": "public_download",
            "metadata": {
                "retrieved_on": RETRIEVED_ON,
                "harvest": "symgov-commons-harvest-20261013",
                "worksheet": "curation/worksheet.csv (every row confirmed by the product owner)",
                "imported_symbols": len(symbols),
                "dropped_rows": len(drops),
                "excluded_files": len(validation["excluded"]),
            },
        },
        "rights": rights_plan(symbols),
        "publication": {"pack_code": PUBLICATION_PACK_CODE, "pack_title": PUBLICATION_PACK_TITLE},
        "summary": {
            "worksheet_rows": len(worksheet),
            "actions": dict(Counter(row.get("action") for row in worksheet)),
            "symbols": len(symbols),
            "excluded": len(validation["excluded"]),
            "dropped": len(drops),
            "disciplines": dict(Counter(symbol["discipline"] for symbol in symbols)),
            "categories": dict(Counter(symbol["category"] for symbol in symbols)),
            "concepts": len(concepts),
            "new_concepts": sum(1 for concept in concepts if concept["is_new"]),
            "existing_concepts": sum(1 for concept in concepts if not concept["is_new"]),
            "fire_dampers": [symbol["slug"] for symbol in symbols if symbol["fire"]],
            "fixes": dict(Counter(step["fix"] for symbol in symbols for step in symbol["fix_steps"])),
            "fixed_files": sum(1 for symbol in symbols if symbol["fix_steps"]),
            "standard_links": dict(
                Counter(item["standard_code"] for symbol in symbols for item in symbol["standards"])
            ),
            "without_standard_link": [symbol["slug"] for symbol in symbols if not symbol["standards"]],
            "licence_families": dict(Counter(symbol["commons"]["licence_family"] for symbol in symbols)),
        },
    }


# --------------------------------------------------------------------------
# Comparisons for the symbols held without a standard.
# --------------------------------------------------------------------------

COMPARISONS_FILE = "comparisons.csv"
COMPARISON_RELATIONSHIP_TYPE = "comparison_only"
COMPARISON_VERIFICATION_METHOD = "manual"
COMPARED = "compared"
COMPARISON_DECISIONS = frozenset({COMPARED, "hold", "drop"})


def plan_comparisons(plan: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Read `curation/comparisons.csv` against the plan. Pure.

    A symbol whose Commons file cites no standard has no verified standard
    link, so the gate holds it (`graphical_authority_unasserted`). For each
    such symbol the product owner compared the drawing with the ISO 10628-2
    drawings this package already publishes and decided one of:

    * `compared` -- record a `comparison_only` link to ISO 10628:2012. It says
      the drawing was compared with the standard's published drawings, never
      that it implements the standard; the file does not claim that.
    * `hold` -- leave it unpublished for now.
    * `drop` -- never publish it. The worksheet row stays, because the symbol
      is already recorded and `apply` refuses a recorded symbol it no longer plans.

    The references must be planned symbols that cite ISO 10628-2.
    """
    by_pageid = {str(symbol["pageid"]): symbol for symbol in plan["symbols"]}
    problems: list[str] = []
    compared: list[dict[str, Any]] = []
    held_by_decision: dict[str, str] = {}
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):
        pageid = str(row.get("pageid") or "").strip()
        decision = str(row.get("decision") or "").strip()
        where = f"{COMPARISONS_FILE} line {number}"
        symbol = by_pageid.get(pageid)
        if symbol is None:
            problems.append(f"{where}: pageid {pageid or '(blank)'} is not a planned symbol")
            continue
        if pageid in seen:
            problems.append(f"{where}: pageid {pageid} appears more than once")
            continue
        seen.add(pageid)
        if symbol["standards"]:
            problems.append(f"{where}: {symbol['slug']} already cites a standard and needs no comparison")
            continue
        if decision not in COMPARISON_DECISIONS:
            problems.append(f"{where}: decision must be one of {', '.join(sorted(COMPARISON_DECISIONS))}")
            continue
        if decision != COMPARED:
            held_by_decision[symbol["slug"]] = decision
            continue
        finding = clean_text(row.get("finding"))
        if not finding:
            problems.append(f"{where}: a compared symbol needs a finding")
        reference_ids = [part.strip() for part in str(row.get("reference_pageids") or "").split(";") if part.strip()]
        if not reference_ids:
            problems.append(f"{where}: a compared symbol needs at least one reference pageid")
        references = []
        for reference_id in reference_ids:
            reference = by_pageid.get(reference_id)
            if reference is None or not any(item["standard_code"] == ISO_10628_CODE for item in reference["standards"]):
                problems.append(f"{where}: reference {reference_id} is not a planned ISO 10628-2 drawing")
                continue
            references.append({"pageid": reference["pageid"], "slug": reference["slug"], "name": reference["canonical_name"]})
        compared.append(
            {"pageid": symbol["pageid"], "slug": symbol["slug"], "name": symbol["canonical_name"],
             "page_url": symbol["commons"]["page_url"], "finding": finding, "references": references}
        )
    return {"compared": compared, "held_by_decision": held_by_decision, "problems": problems}


def _plan_symbols_tolerant(package: dict[str, Any], excluded: set[str]) -> list[dict[str, Any]]:
    """The symbols the package can describe even though it has problems, so a dry run can report."""
    manifest = _manifest_index(package)
    fixes = fix_results(package)
    usable = []
    for row in package["worksheet"]:
        pageid = str(row.get("pageid") or "").strip()
        if row.get("action") not in IMPORT_ACTIONS or pageid in excluded:
            continue
        fix = fixes.get(pageid)
        if pageid not in manifest or not fix or "error" in fix or not (row.get("proposed_name") or "").strip():
            continue
        usable.append(pageid)
    keep = set(usable)
    narrowed = {**package, "worksheet": [row for row in package["worksheet"] if str(row.get("pageid") or "").strip() in keep]}
    narrowed["_fixes"] = fixes
    return plan_symbols(narrowed, excluded)
