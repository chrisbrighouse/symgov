"""What the DISC DEXPI import records, decided without a database.

`disc_dexpi_ingest` writes what this decides, the way `dexpi_ingest` writes
what `dexpi_ingestion` decides for the TrainingTestCases pilot. Everything here
is a pure function of the parsed import package (`manifest.json`,
`dexpi_class_scheme.json`, `report.json`, `held_back.csv`, `config.json` and
the SVG bytes): no database, no filesystem, no clock.

**What differs from the pilot.**

* The key is the manifest's `source_key` (`disc-dexpi-ND0004`), lower-cased as
  the symbol's slug. The pilot keyed on a geometry signature and never wrote a
  symbol twice; here a changed primary or option digest, or a changed geometry
  signature, makes a new revision of the same symbol.
* The attribution text is stored once, on the rights record. It is read from
  the package, checked to be identical wherever the package repeats it, and
  removed from everything else this plan produces; `payload.dexpi.attribution`
  is filled from the record when a response is built.
* The DEXPI class scheme, the concept matching and the RDL mappings are part of
  the plan, so a dry run can report them before anything is written.

The attribution wording never appears in this module. It comes from the
package, and the tests use their own stand-in text.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from .dexpi_concepts import DEFAULT_CONCEPT_KIND, _resolve_kind

PACKAGE_CODE = "DISC-DEXPI-0.6.3"
PUBLICATION_PACK_CODE = "disc-dexpi"
SCHEME_CODE = "DEXPI-CLASS"
SVG_CONTENT_TYPE = "image/svg+xml"
REVISION_LIFECYCLE_STATE = "approved"
FIRST_REVISION_LABEL = "r1"

STANDARD_CODE = "DEXPI"
SEMANTIC_METHOD = "source_mapping"
CLASSIFICATION_RULE_METHOD = "rule"
SOURCE_MAPPING_METHOD = "source_mapping"

# The two catalogue schemes the manifest assigns by node label, and the one
# representation node the pilot gives every converted vector drawing.
DISCIPLINE_SCHEME_CODE = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME_CODE = "SYMBOL-CATEGORY-FAMILY"
REPRESENTATION_TYPE_SCHEME_CODE = "REPRESENTATION-TYPE"
REPRESENTATION_TYPE_NODE_CODE = "INTELLIGENT_VECTOR_DRAWING_CAD"

# The classes the brief says DISC and the pilot share. The dry run stops if the
# concepts found in the database are not exactly these.
EXPECTED_OVERLAP_CLASSES = (
    "BallValve",
    "GateValve",
    "GlobeValve",
    "ButterflyValve",
    "PlugValve",
    "CheckValve",
    "CentrifugalPump",
    "ReciprocatingPump",
    "RotaryPump",
    "Pump",
    "ControlledActuator",
    "ProcessInstrumentationFunction",
    "BlindFlange",
    "PipeReducer",
    "PropertyBreak",
    "HeatExchanger",
)

# The manifest names three external systems; each is an `external_semantic_schemes`
# row. DEXPI-RDL and ISO15926-RDL-PCA are seeded by migration 20260909_0049;
# the third is registered by the import.
EXTERNAL_SYSTEMS = {
    "DEXPI RDL": {
        "scheme_code": "DEXPI-RDL",
        "title": "DEXPI Sandbox Reference Data Library",
        "issuing_body": "DEXPI e.V.",
        "base_uri": "https://dexpi.org/tools-service/",
    },
    "POSC Caesar RDL": {
        "scheme_code": "ISO15926-RDL-PCA",
        "title": "ISO 15926 Reference Data Library",
        "issuing_body": "POSC Caesar Association",
        "base_uri": None,
    },
    "DISC/NOAKA RDL": {
        "scheme_code": "DISC-NOAKA-RDL",
        "title": "DISC / NOAKA Reference Data Library",
        "issuing_body": "NOAKA, as referenced by the DISC DEXPI project",
        "base_uri": "http://noaka.org/rdl/",
    },
}
EXTERNAL_MAPPING_RELATIONS = {"exact", "broader"}

# The only display-name corrections made. The SVG bytes keep the source's
# spelling (they are hash-verified), and `payload.disc` is left as it is.
DISPLAY_NAME_CORRECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "disc-dexpi-ND0114": (("Fuced", "Fixed"),),
}

_NODE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$")
PACKAGE_NODE_PREFIX = "PACKAGE_"
OTHER_PACKAGE_NAME = "Other"
TTC_NODE_SOURCE = "DEXPI TrainingTestCases"


class DiscPlanError(ValueError):
    """The package does not support an import. Names data, never a credential."""


# --------------------------------------------------------------------------
# Small helpers.
# --------------------------------------------------------------------------


def null_if_zero(value: Any) -> Any:
    """Package v1 wrote empty Excel cells as 0. v2 writes null; this stays as a safety net."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 0:
        return None
    return value


def snake_code(name: str) -> str:
    """`CustomOperatedValve` -> `CUSTOM_OPERATED_VALVE`: the node-code grammar is upper-case snake."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", str(name))
    return re.sub(r"[^A-Za-z0-9]+", "_", spaced).strip("_").upper()


def slug_for(source_key: str) -> str:
    return source_key.lower()


def disc_id_for(source_key: str) -> str:
    return source_key.rsplit("-", 1)[-1]


def asset_object_key(slug: str, revision_label: str, asset: dict[str, Any]) -> str:
    """Where one SVG lives in object storage.

    Content-addressed and scoped to the revision: `attachments.object_key` is
    unique across revisions, so a revision whose option SVG is byte-identical to
    the previous revision's still needs a key of its own.
    """
    digest = str(asset["sha256"]).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DiscPlanError(f"{slug}: {asset['filename']} has no SHA-256 digest")
    stem = "primary" if asset["role"] == "primary" else f"option{int(asset['option_index'])}"
    return f"dexpi/{PACKAGE_CODE}/{slug}/{revision_label}/{stem}-{digest}.svg"


# --------------------------------------------------------------------------
# Validating the package.
# --------------------------------------------------------------------------


def validate_package(package: dict[str, Any]) -> list[str]:
    """Every way the package disagrees with itself, as sentences. Empty means consistent.

    `package` carries `manifest` (list), `scheme`, `report`, `held_back` (list of
    rows), `config` and `svg` (filename -> bytes).
    """
    problems: list[str] = []
    manifest = package["manifest"]
    report = package["report"]
    svg = package["svg"]
    config = package["config"]

    if len(manifest) != report.get("imported"):
        problems.append(f"manifest has {len(manifest)} records, report.json says {report.get('imported')}")
    keys = [record["source_key"] for record in manifest]
    if len(set(keys)) != len(keys):
        problems.append("source_key values are not unique")

    referenced: set[str] = set()
    option_assets = 0
    for record in manifest:
        for asset in record["assets"]:
            referenced.add(asset["filename"])
            data = svg.get(asset["filename"])
            if data is None:
                problems.append(f"{record['source_key']}: {asset['filename']} is not in svg/")
                continue
            if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                problems.append(f"{record['source_key']}: {asset['filename']} does not match its sha256")
            if len(data) != asset["size_bytes"]:
                problems.append(f"{record['source_key']}: {asset['filename']} does not match its size")
            if asset["role"] == "option":
                option_assets += 1
        primaries = [asset for asset in record["assets"] if asset["role"] == "primary"]
        if len(primaries) != 1:
            problems.append(f"{record['source_key']}: expected one primary asset, found {len(primaries)}")
        indexes = [asset.get("option_index") for asset in record["assets"] if asset["role"] == "option"]
        if any(not isinstance(index, int) or isinstance(index, bool) or index < 1 for index in indexes) or len(set(indexes)) != len(indexes):
            problems.append(f"{record['source_key']}: option numbers must be distinct whole numbers from 1")
        if any(asset["role"] not in {"primary", "option"} for asset in record["assets"]):
            problems.append(f"{record['source_key']}: an asset has a role other than primary or option")
    unreferenced = sorted(set(svg) - referenced)
    if unreferenced:
        problems.append(f"{len(unreferenced)} SVG files no record references, first {unreferenced[0]}")
    if len(svg) != report.get("imported", 0) + report.get("option_assets", 0):
        problems.append(f"svg/ has {len(svg)} files, report.json implies {report.get('imported', 0) + report.get('option_assets', 0)}")
    if option_assets != report.get("option_assets"):
        problems.append(f"manifest has {option_assets} option assets, report.json says {report.get('option_assets')}")

    def counted(predicate) -> int:
        return sum(1 for record in manifest if predicate(record))

    with_points = counted(lambda r: (r["payload"].get("geometry") or {}).get("connection_points"))
    with_slots = counted(lambda r: (r["payload"].get("geometry") or {}).get("label_slots"))
    if with_points != report.get("with_connection_points"):
        problems.append(f"{with_points} records have connection points, report.json says {report.get('with_connection_points')}")
    if with_slots != report.get("with_label_slots"):
        problems.append(f"{with_slots} records have label slots, report.json says {report.get('with_label_slots')}")
    if dict(Counter(r["category"] for r in manifest)) != report.get("categories"):
        problems.append("category counts differ from report.json")
    if dict(Counter(r["discipline"] for r in manifest)) != report.get("disciplines"):
        problems.append("discipline counts differ from report.json")
    scheme_counts = Counter(node["kind"] for node in package["scheme"]["nodes"])
    expected_nodes = report.get("scheme_nodes") or {}
    for kind in ("package", "dexpi_class", "custom_type"):
        if scheme_counts.get(kind) != expected_nodes.get(kind):
            problems.append(f"{scheme_counts.get(kind)} {kind} nodes, report.json says {expected_nodes.get(kind)}")

    held_ids = {row["disc_id"] for row in package["held_back"]}
    if len(held_ids) != report.get("held_back"):
        problems.append(f"held_back.csv lists {len(held_ids)} symbols, report.json says {report.get('held_back')}")
    present = sorted(held_ids & {disc_id_for(key) for key in keys})
    if present:
        problems.append(f"{len(present)} held-back symbols are in the manifest, first {present[0]}")

    texts = {record["rights"]["attribution_text"] for record in manifest}
    texts.add(config["attribution_text"])
    if len(texts) != 1:
        problems.append(f"the attribution text is not identical across the package ({len(texts)} versions)")
    placeholders = {bool(record["rights"]["attribution_is_placeholder"]) for record in manifest}
    placeholders.add(bool(config["attribution_is_placeholder"]))
    if len(placeholders) != 1:
        problems.append("attribution_is_placeholder is not the same across the package")
    commits = {record["rights"]["source_commit"] for record in manifest}
    if len(commits) != 1:
        problems.append("the records name more than one source commit")
    return problems


# --------------------------------------------------------------------------
# The DEXPI-CLASS scheme.
# --------------------------------------------------------------------------


def plan_scheme_nodes(scheme: dict[str, Any]) -> dict[str, Any]:
    """The nodes to write, their codes, and the DEXPI-name -> node_code map.

    Raises `DiscPlanError` if two names turn into one code, or a code does not
    fit the grammar. One collision is resolved by a rule and not by luck: the
    package `Equipment` and the class `Equipment` are both spelled `Equipment`
    in the source, so every package node takes a `PACKAGE_` prefix.
    """
    nodes = scheme["nodes"]
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        by_kind[node["kind"]].append(node)
    package_names = {node["code"] for node in by_kind["package"]}
    class_names = {node["code"] for node in by_kind["dexpi_class"]}
    planned: list[dict[str, Any]] = []
    problems: list[str] = []

    for node in by_kind["package"]:
        planned.append(
            {
                "kind": "package",
                "dexpi_name": node["code"],
                "parent_dexpi_name": None,
                "parent_kind": None,
                "node_code": PACKAGE_NODE_PREFIX + snake_code(node["code"]),
                "label": node["label"],
                "friendly_label": None,
                "description": None,
                "source": "DEXPI 1.3 package",
                "uri": None,
            }
        )
    for node in by_kind["dexpi_class"]:
        parent = node["parent"]
        if parent not in package_names:
            problems.append(f"class {node['code']} names parent {parent!r}, which is not a package")
        planned.append(
            {
                "kind": "dexpi_class",
                "dexpi_name": node["code"],
                "parent_dexpi_name": parent,
                "parent_kind": "package",
                "node_code": snake_code(node["code"]),
                "label": node["label"],
                "friendly_label": None,
                "description": node.get("description"),
                "source": node.get("source"),
                "uri": node.get("uri"),
            }
        )
    for node in by_kind["custom_type"]:
        parent = node["parent"]
        if parent not in class_names:
            problems.append(f"custom type {node['code']} names parent {parent!r}, which is not a class")
        name = node["code"].rsplit("/", 1)[-1]
        planned.append(
            {
                "kind": "custom_type",
                "dexpi_name": name,
                # The manifest refers to a custom type by `Class/Name`.
                "manifest_reference": node["code"],
                "parent_dexpi_name": parent,
                "parent_kind": "dexpi_class",
                "node_code": snake_code(name),
                "label": name,
                "friendly_label": node["label"],
                "description": None,
                "source": node.get("source"),
                "uri": node.get("uri"),
            }
        )

    owners: dict[str, list[str]] = defaultdict(list)
    for node in planned:
        code = node["node_code"]
        if not _NODE_CODE_PATTERN.match(code):
            problems.append(f"{node['kind']} {node['dexpi_name']!r} gives node code {code!r}, which the grammar refuses")
        owners[code].append(f"{node['kind']} {node['dexpi_name']}")
    for code, names in sorted(owners.items()):
        if len(names) > 1:
            problems.append(f"node code {code} would be used by: {', '.join(names)}")
    if problems:
        raise DiscPlanError("; ".join(problems))

    return {
        "nodes": planned,
        "code_map": {f"{node['kind']}:{node['dexpi_name']}": node["node_code"] for node in planned},
        "counts": dict(Counter(node["kind"] for node in planned)),
    }


def node_index(plan_nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Class and custom-type nodes by the name the manifest and the pilot use for them.

    A package is never a classification target, so it has no entry (which is
    also why the package/class `Equipment` clash cannot make this ambiguous).
    """
    index: dict[str, dict[str, Any]] = {}
    for node in plan_nodes:
        if node["kind"] == "dexpi_class":
            index[node["dexpi_name"]] = node
        elif node["kind"] == "custom_type":
            index[node["manifest_reference"]] = node
    return index


def ttc_class_target(payload: dict[str, Any]) -> str | None:
    """The DEXPI class name a pilot symbol is classified against, or None.

    Decision (brief task 3): `name_basis = dexpi_component_class` takes the
    first component class; `dexpi_reference_shape` takes the concept key.
    """
    dexpi = (payload or {}).get("dexpi") or {}
    basis = dexpi.get("name_basis")
    if basis == "dexpi_component_class":
        classes = dexpi.get("component_classes") or []
        return classes[0] if classes else None
    if basis == "dexpi_reference_shape":
        return ((dexpi.get("semantic_concept") or {}).get("concept_key")) or None
    return None


def ttc_missing_node(name: str) -> dict[str, Any]:
    """A class the pilot uses that the DEXPI 1.3 list lacks, added under `Other`."""
    return {
        "kind": "dexpi_class",
        "dexpi_name": name,
        "parent_dexpi_name": OTHER_PACKAGE_NAME,
        "parent_kind": "package",
        "node_code": snake_code(name),
        "label": name,
        "friendly_label": None,
        "description": None,
        "source": TTC_NODE_SOURCE,
        "uri": None,
    }


# --------------------------------------------------------------------------
# Concepts and external mappings.
# --------------------------------------------------------------------------


def _fold(value: str) -> str:
    return value.casefold()


def _loose(value: str) -> str:
    """A spelling-insensitive key, to catch `Heat Exchanger` against `HeatExchanger`."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def plan_concepts(manifest: list[dict[str, Any]], nodes_by_ref: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One concept per distinct `concept_key`, in key order."""
    grouped: dict[str, dict[str, Any]] = {}
    for record in manifest:
        dexpi = record["payload"]["dexpi"]
        key = dexpi["semantic_concept"]["concept_key"]
        bucket = grouped.setdefault(
            key,
            {"concept_key": key, "elements": set(), "classes": set(), "custom": {}, "symbols": [], "node": None},
        )
        bucket["elements"].update(dexpi.get("dexpi_elements") or [])
        bucket["classes"].update(dexpi.get("component_classes") or [])
        custom = dexpi.get("custom_type")
        if isinstance(custom, dict) and null_if_zero(custom.get("label")):
            bucket["custom"][custom["label"]] = null_if_zero(custom.get("rdl_uri"))
        bucket["symbols"].append(disc_id_for(record["source_key"]))
        for classification in record["classifications"]:
            if classification["scheme"] == SCHEME_CODE:
                bucket["node"] = classification["node"]
    concepts = []
    for key in sorted(grouped):
        bucket = grouped[key]
        elements = sorted(bucket["elements"])
        kind, disagreement = _resolve_kind(elements, key)
        node = nodes_by_ref.get(bucket["node"]) if bucket["node"] else None
        labels = sorted(bucket["custom"])
        if node is not None and node["kind"] == "custom_type":
            parent_class = node["parent_dexpi_name"]
            definition = (
                f"DISC custom type `{node['dexpi_name']}` ({', '.join(labels) or node['friendly_label']}), "
                f"a kind of DEXPI class `{parent_class}`. Taken from the DISC DEXPI symbol register "
                f"(DISC Profile 0.6.3), which maps it to a drawing but publishes no further definition."
            )
        else:
            described = (node or {}).get("description")
            definition = (
                f"DEXPI class `{key}`. {described + ' ' if described else ''}"
                f"Taken from the DISC DEXPI symbol register (DISC Profile 0.6.3), which maps it to a "
                f"drawing but publishes no further definition."
            )
        aliases: list[str] = []
        for label in labels + ([node["friendly_label"]] if node and node.get("friendly_label") else []):
            if label and _fold(label) != _fold(key) and label not in aliases:
                aliases.append(label)
        notes = "Concept taken from the DISC DEXPI symbol library (DISC Profile 0.6.3)."
        if disagreement:
            notes += f" {disagreement}"
        concepts.append(
            {
                "concept_key": key,
                "preferred_name": key,
                "concept_kind": kind if elements else DEFAULT_CONCEPT_KIND,
                "definition": definition,
                "aliases": aliases,
                "notes": notes,
                "rationale": "Named from the DEXPI class or DISC custom type the DISC symbol register maps this drawing to.",
                "dexpi_node_reference": bucket["node"],
                "symbols": sorted(bucket["symbols"]),
            }
        )
    return concepts


def match_concepts(
    planned: list[dict[str, Any]], existing: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Split the planned concepts into reuse and create, and test the 16-class overlap.

    `existing` is `dexpi_seed.index_existing`: every concept, by folded preferred
    name. A match is on the exact folded name. A near match (the same letters
    and digits spelled differently) is reported as `near_misses` and is a
    reason to stop, because creating it would duplicate a concept.
    """
    reuse, create, near_misses = [], [], []
    loose_existing: dict[str, list[str]] = defaultdict(list)
    for name in existing:
        loose_existing[_loose(name)].append(name)
    for concept in planned:
        folded = _fold(concept["preferred_name"])
        match = existing.get(folded)
        if match is not None:
            reuse.append({**concept, "concept_code": match["concept_code"], "concept_id": str(match["concept_id"])})
            continue
        similar = [name for name in loose_existing.get(_loose(concept["preferred_name"]), []) if name != folded]
        if similar:
            near_misses.append({"concept_key": concept["concept_key"], "existing_names": sorted(similar)})
        create.append(concept)
    reused_keys = {concept["concept_key"] for concept in reuse}
    expected = set(EXPECTED_OVERLAP_CLASSES)
    overlap_table = []
    for key in EXPECTED_OVERLAP_CLASSES:
        found = next((concept for concept in reuse if concept["concept_key"] == key), None)
        overlap_table.append(
            {
                "concept_key": key,
                "pilot_concept_code": found["concept_code"] if found else None,
                "matched": found is not None,
            }
        )
    return {
        "reuse": reuse,
        "create": create,
        "near_misses": near_misses,
        "overlap": overlap_table,
        "expected_but_unmatched": sorted(expected - reused_keys),
        "matched_but_unexpected": sorted(reused_keys - expected),
    }


def plan_external_mappings(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    """Concept -> RDL mappings, de-duplicated per concept.

    One concept is reached by many symbols, so the same mapping is read many
    times; it is written once. `exact` is the strong claim and the database
    keeps one verified exact per concept and scheme version, so two different
    exact identifiers for one concept and system are a conflict to report, not
    to resolve. `broader` can be several.
    """
    mappings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    problems: list[str] = []
    for record in manifest:
        key = record["payload"]["dexpi"]["semantic_concept"]["concept_key"]
        for mapping in record["external_mappings"]:
            system = mapping["system"]
            relation = mapping["relation"]
            if system not in EXTERNAL_SYSTEMS:
                problems.append(f"{record['source_key']}: unknown external system {system!r}")
                continue
            if relation not in EXTERNAL_MAPPING_RELATIONS:
                problems.append(f"{record['source_key']}: unknown relation {relation!r}")
                continue
            identifier = null_if_zero(mapping.get("identifier"))
            if not identifier:
                problems.append(f"{record['source_key']}: a {system} mapping has no identifier")
                continue
            entry = mappings.setdefault(
                (key, system, relation, identifier),
                {
                    "concept_key": key,
                    "system": system,
                    "scheme_code": EXTERNAL_SYSTEMS[system]["scheme_code"],
                    "relation": relation,
                    "identifier": identifier,
                    "label": null_if_zero(mapping.get("label")),
                    "symbols": [],
                },
            )
            entry["symbols"].append(disc_id_for(record["source_key"]))
    exact_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (key, system, relation, identifier) in mappings:
        if relation == "exact":
            exact_ids[(key, system)].add(identifier)
    conflicts = [
        {"concept_key": key, "system": system, "identifiers": sorted(ids)}
        for (key, system), ids in sorted(exact_ids.items())
        if len(ids) > 1
    ]
    ordered = [mappings[k] for k in sorted(mappings)]
    for entry in ordered:
        entry["symbols"] = sorted(set(entry["symbols"]))
    return {"mappings": ordered, "conflicts": conflicts, "problems": problems}


# --------------------------------------------------------------------------
# The symbols.
# --------------------------------------------------------------------------


def _strip_attribution(text: str, attribution: str) -> str:
    """Remove the attribution wording from a text field, tidying the join."""
    if not text or attribution not in text:
        return text
    return re.sub(r"\s{2,}", " ", text.replace(attribution, "")).strip()


def _corrected(source_key: str, text: Any) -> Any:
    if not isinstance(text, str):
        return text
    for wrong, right in DISPLAY_NAME_CORRECTIONS.get(source_key, ()):
        text = text.replace(wrong, right)
    return text


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains(child, needle) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains(child, needle) for child in value)
    return False


def build_revision_payload(
    record: dict[str, Any], *, revision_label: str, attribution: str
) -> dict[str, Any]:
    """The payload the Catalogue renders. The attribution text is not in it."""
    source_key = record["source_key"]
    slug = slug_for(source_key)
    manifest_payload = record["payload"]
    dexpi = {
        key: value
        for key, value in manifest_payload["dexpi"].items()
        if key not in {"attribution", "attribution_is_placeholder"}
    }
    # v1 wrote empty cells as 0; v2 writes null. Either way, absent means absent.
    custom = dexpi.get("custom_type")
    if isinstance(custom, dict):
        custom = {key: null_if_zero(value) for key, value in custom.items()}
        dexpi["custom_type"] = custom
    concept = dexpi.get("semantic_concept")
    if isinstance(concept, dict):
        dexpi["semantic_concept"] = {key: null_if_zero(value) for key, value in concept.items()}
    dexpi["attribution_source"] = "rights_record"

    assets_by_role = {"primary": None, "option": []}
    for asset in record["assets"]:
        stored = {
            "object_key": asset_object_key(slug, revision_label, asset),
            "filename": asset["filename"],
            "content_type": SVG_CONTENT_TYPE,
            "format": "svg",
            "role": asset["role"],
            "sha256": asset["sha256"],
            "size_bytes": asset["size_bytes"],
            "source_path": asset["source_path"],
        }
        if asset["role"] == "option":
            stored["option_index"] = asset["option_index"]
            # What the source states, or nothing: never an empty string.
            stored["condition"] = (asset.get("condition") or "").strip() or None
            assets_by_role["option"].append(stored)
        else:
            assets_by_role["primary"] = stored
    primary = assets_by_role["primary"]
    options = sorted(assets_by_role["option"], key=lambda item: item["option_index"])
    by_filename = {item["filename"]: item for item in options}

    aliases: list[str] = []
    name = _corrected(source_key, record["name"])
    for alias in manifest_payload.get("aliases") or []:
        if alias and alias != name and alias not in aliases:
            aliases.append(alias)

    payload = {
        "name": name,
        "summary": _corrected(source_key, record["summary"]),
        "description": _strip_attribution(_corrected(source_key, manifest_payload.get("description") or ""), attribution),
        "aliases": aliases,
        "keywords": list(record["keywords"]),
        "assets": [dict(primary)],
        # Where the Catalogue looks. Option variants sit apart from
        # `source_assets` so no format-based selection can reach them.
        "visual_assets": {
            "source_assets": [dict(primary)],
            "state_variants": [dict(item) for item in options],
        },
        "dexpi": dexpi,
        "disc": manifest_payload.get("disc"),
        "geometry": manifest_payload.get("geometry"),
        "options": [
            {
                "index": option["index"],
                "condition": option["condition"],
                "filename": option["asset"],
                "sha256": by_filename[option["asset"]]["sha256"] if option["asset"] in by_filename else None,
            }
            for option in (manifest_payload.get("options") or [])
        ],
    }
    # The disc block keeps the register's own wording, typo included.
    if _contains({key: value for key, value in payload.items() if key != "disc"}, attribution):
        raise DiscPlanError(f"{source_key}: the attribution text would be stored in the revision payload")
    return payload


def _classification(record: dict[str, Any], scheme: str) -> dict[str, Any]:
    matches = [item for item in record["classifications"] if item["scheme"] == scheme]
    if len(matches) != 1:
        raise DiscPlanError(f"{record['source_key']}: expected one {scheme} classification, found {len(matches)}")
    return matches[0]


def plan_symbols(package: dict[str, Any], nodes_by_ref: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    manifest = package["manifest"]
    attribution = package["config"]["attribution_text"]
    symbols = []
    for order, record in enumerate(manifest):
        source_key = record["source_key"]
        dexpi_class = _classification(record, SCHEME_CODE)
        if dexpi_class["node"] not in nodes_by_ref:
            raise DiscPlanError(f"{source_key}: DEXPI-CLASS node {dexpi_class['node']!r} is not in the scheme")
        primary = next(asset for asset in record["assets"] if asset["role"] == "primary")
        options = sorted(
            (asset for asset in record["assets"] if asset["role"] == "option"), key=lambda a: a["option_index"]
        )
        dexpi = record["payload"]["dexpi"]
        standard = dexpi["standard_assertion"]
        symbols.append(
            {
                "source_key": source_key,
                "slug": slug_for(source_key),
                "disc_id": disc_id_for(source_key),
                "canonical_name": _corrected(source_key, record["name"]),
                "category": record["category"],
                "discipline": record["discipline"],
                "sort_order": order,
                "rationale": record["rationale"],
                "geometry_signature": dexpi["geometry_signature"],
                "primary_sha256": primary["sha256"],
                "option_sha256": [asset["sha256"] for asset in options],
                "concept_key": dexpi["semantic_concept"]["concept_key"],
                "dexpi_node_reference": dexpi_class["node"],
                "dexpi_class_evidence": dexpi_class.get("evidence") or {},
                "discipline_node_label": _classification(record, DISCIPLINE_SCHEME_CODE)["node_label"],
                "category_node_label": _classification(record, CATEGORY_SCHEME_CODE)["node_label"],
                "classification_evidence": {
                    DISCIPLINE_SCHEME_CODE: _classification(record, DISCIPLINE_SCHEME_CODE).get("evidence") or {},
                    CATEGORY_SCHEME_CODE: _classification(record, CATEGORY_SCHEME_CODE).get("evidence") or {},
                },
                "assertion": {
                    "relationship_type": standard["relationship_type"],
                    "standard_code": standard["standard_code"],
                    "version_label": standard["version_label"],
                    "source_symbol_identifier": standard["source_symbol_identifier"],
                    "basis": "disc_symbol_register",
                },
                "entry": {
                    "source_label": _corrected(source_key, record["name"]),
                    "provider_entry_identifier": disc_id_for(source_key),
                    "source_path": primary["source_path"],
                },
                "warnings": list(record.get("warnings") or []),
                "record": record,
                "attribution": attribution,
            }
        )
    return symbols


def revision_needs_bump(existing_payload: dict[str, Any] | None, symbol: dict[str, Any]) -> list[str]:
    """Why an existing revision no longer matches the package, as reasons. Empty means unchanged."""
    if existing_payload is None:
        return ["no revision"]
    reasons = []
    dexpi = existing_payload.get("dexpi") or {}
    if dexpi.get("geometry_signature") != symbol["geometry_signature"]:
        reasons.append("geometry_signature changed")
    stored_primary = ((existing_payload.get("assets") or [{}])[0]).get("sha256")
    if stored_primary != symbol["primary_sha256"]:
        reasons.append("primary sha256 changed")
    stored_options = [
        item.get("sha256")
        for item in sorted(
            (existing_payload.get("visual_assets") or {}).get("state_variants") or [],
            key=lambda item: item.get("option_index") or 0,
        )
    ]
    if stored_options != symbol["option_sha256"]:
        reasons.append("option sha256 values changed")
    return reasons


def next_revision_label(labels: list[str]) -> str:
    numbers = [int(label[1:]) for label in labels if re.fullmatch(r"r[0-9]+", label)]
    return f"r{max(numbers) + 1}" if numbers else FIRST_REVISION_LABEL


# --------------------------------------------------------------------------
# The whole plan.
# --------------------------------------------------------------------------


def plan_import(package: dict[str, Any]) -> dict[str, Any]:
    """Everything the importer can decide without a database."""
    problems = validate_package(package)
    scheme_plan = plan_scheme_nodes(package["scheme"])
    nodes_by_ref = node_index(scheme_plan["nodes"])
    symbols = plan_symbols(package, nodes_by_ref)
    concepts = plan_concepts(package["manifest"], nodes_by_ref)
    mappings = plan_external_mappings(package["manifest"])
    config = package["config"]
    first = package["manifest"][0]["rights"]
    return {
        "package_problems": problems,
        "scheme": {
            "scheme_code": package["scheme"]["code"],
            "name": package["scheme"]["name"],
            "version_label": package["scheme"]["version"],
            "description": package["scheme"]["description"],
            **scheme_plan,
        },
        "nodes_by_ref": nodes_by_ref,
        "symbols": symbols,
        "concepts": concepts,
        "external": {
            "systems": EXTERNAL_SYSTEMS,
            "version_label": f"referenced by {config['profile_version']}",
            **mappings,
        },
        "source_package": {
            "package_code": PACKAGE_CODE,
            "title": config["pack_name"],
            "provider": f"{config['licensor']} ({config['creator']})",
            "source_uri": config["source_url"],
            "release_version": f"{config['profile_version']} @ {first['source_commit'][:12]}",
            "acquisition_method": "contributed",
            "metadata": {
                "source_commit": first["source_commit"],
                "licensor": config["licensor"],
                "creator": config["creator"],
                "organisation_url": config["organisation_url"],
                "profile_version": config["profile_version"],
                "dexpi_version": config["dexpi_version"],
                "imported_symbols": len(symbols),
                "held_back_symbols": package["report"]["held_back"],
            },
        },
        "rights": {
            "rights_status": "licensed",
            "disposition": "distribute",
            "determination_method": "manual",
            "licence_reference": (
                "Permission granted by Tonia Pedersen to Chris Brighouse, Oct 2026, "
                "conditional on attribution to the DISC DEXPI organisation (dexpi.org)"
            ),
            "evidence": {
                "permission": (
                    "Permission granted by Tonia Pedersen to Chris Brighouse, Oct 2026, "
                    "conditional on attribution to the DISC DEXPI organisation (dexpi.org)"
                ),
                "attribution_text": config["attribution_text"],
                "attribution_is_placeholder": bool(config["attribution_is_placeholder"]),
                "licensor": config["licensor"],
                "creator": config["creator"],
                "source_url": config["source_url"],
                "source_commit": first["source_commit"],
            },
        },
        "standards": [
            {
                "standard_code": STANDARD_CODE,
                "title": "DEXPI Process Industry Data Exchange P&ID specification",
                "issuing_body": "DEXPI e.V.",
                "versions": sorted({symbol["assertion"]["version_label"] for symbol in symbols}),
            }
        ],
        "publication": {"pack_code": PUBLICATION_PACK_CODE, "pack_title": config["pack_name"]},
        "summary": {
            "symbols": len(symbols),
            "option_assets": sum(len(symbol["option_sha256"]) for symbol in symbols),
            "with_connection_points": sum(
                1 for s in symbols if (s["record"]["payload"].get("geometry") or {}).get("connection_points")
            ),
            "with_label_slots": sum(
                1 for s in symbols if (s["record"]["payload"].get("geometry") or {}).get("label_slots")
            ),
            "categories": dict(Counter(symbol["category"] for symbol in symbols)),
            "disciplines": dict(Counter(symbol["discipline"] for symbol in symbols)),
            "concepts": len(concepts),
            "scheme_nodes": scheme_plan["counts"],
            "external_mappings": len(mappings["mappings"]),
            "warnings": sum(len(symbol["warnings"]) for symbol in symbols),
        },
    }
