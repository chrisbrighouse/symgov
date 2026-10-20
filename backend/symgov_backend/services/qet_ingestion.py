"""What the QElectroTech elements import records, decided without a database.

`qet_ingest` writes what this decides, the way `commons_ingest` writes what
`commons_ingestion` decides. Everything here is a pure function of the import
package -- `manifest.json`, the curated `curation/worksheet.csv` and the
original `elmt/*.elmt` element definitions -- with no database, no filesystem
and no clock.

**The package.** Built by `tools/build_package.py` in
`symgov-qet-harvest-20261020` from github.com/qelectrotech/qelectrotech-elements.
Phase 1, as the product owner chose on 2026-10-20: every element of the EN
60617 set, plus the pole-set elements whose own notes cite an EN 60617 clause;
the home-installation folders dropped; the one MIT-licensed element held. The
product owner confirmed every worksheet row, and the curated values are final:
this module refuses a value it cannot honour, it never corrects one.

**What is decided here.**

* The SVG each symbol is published from. It is converted from the .elmt by
  `qet_elmt_svg.convert` here, at plan time, so the stored drawing can never
  drift from its source. The conversion is one step of the revision's
  transformation chain (.elmt digest -> SVG digest); a "Form 1 / Forme 1"
  caption the converter removes is named in that step.
* The concept each symbol carries. `NEW:<Key>` makes one concept per key. A
  key whose name exactly matches an existing concept reuses it, and the dry
  run lists every such reuse (the product owner's rule for this import: the
  Catalog's electrical coverage is thin and a same-named concept is the same
  thing). A near match is reported, never acted on.
* The standard link: each element's EN 60617 clause, as a verified
  `vendor_implementation` of EN 60617, "edition not stated" (product owner,
  2026-10-20). QElectroTech draws to the standard; it does not copy it.
* The rights record (`open`, `distribute`): CC BY 3.0, with QElectroTech's
  written permission of 2026-10-19 as the basis while the machine-learning
  clause is still in `ELEMENTS.LICENSE`. The attribution wording is
  QElectroTech's own, held on the record as a template; the Details view
  fills in each symbol's own author (`payload.attribution.creator`).

QElectroTech's conditions, which this import honours and nothing here may
weaken: the attribution wording, a visible qelectrotech.org link on every
symbol page (the record's `source_url`), each element's own author credited,
and QElectroTech never presented as a partner or endorser.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from ..catalog_facets import CATALOG_CATEGORY_ORDER as FACET_CATEGORY_ORDER
from ..catalog_facets import CATALOG_DISCIPLINE_ORDER as FACET_DISCIPLINE_ORDER
from ..catalog_facets import canonical_discipline
from ..catalog_taxonomy import CATALOG_CATEGORY_ORDER as TAXONOMY_CATEGORY_ORDER
from ..catalog_taxonomy import CATALOG_DISCIPLINE_ORDER as TAXONOMY_DISCIPLINE_ORDER
from .commons_ingestion import final_svg_problems
from .dexpi_concepts import DEFAULT_CONCEPT_KIND
from .qet_elmt_svg import CONVERTER_NAME, CONVERTER_VERSION, ElementDefinitionError, convert

PACKAGE_CODE = "QELECTROTECH-ELEMENTS-20261020"
PUBLICATION_PACK_CODE = "qelectrotech-elements"
PUBLICATION_PACK_TITLE = "QElectroTech elements collection (CC BY 3.0)"
REPOSITORY_URL = "https://github.com/qelectrotech/qelectrotech-elements"
QET_URL = "https://qelectrotech.org"
RETRIEVED_ON = "2026-10-20"
SLUG_PREFIX = "qet-"
SVG_CONTENT_TYPE = "image/svg+xml"
REVISION_LIFECYCLE_STATE = "approved"
FIRST_REVISION_LABEL = "r1"

IMPORT_ACTION = "import"
DROP_ACTION = "drop"
ACTIONS = frozenset({IMPORT_ACTION, DROP_ACTION})
# `cc_by_3_0`: the element says CC BY 3.0. `collection_default`: it says
# nothing, so ELEMENTS.LICENSE (CC BY 3.0 for an element outside a diagram) governs.
LICENCE_CLASSES = frozenset({"cc_by_3_0", "collection_default"})
NEW_CONCEPT_PREFIX = "NEW:"
NEW_CONCEPT_NOTES = "Concept created by the QElectroTech elements import (phase 1 worksheet, 2026-10-20)."
_NEW_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CLAUSE = re.compile(r"^[0-9]{2}-[0-9]{2}-[0-9]{2}$")

SEMANTIC_METHOD = "source_mapping"
CLASSIFICATION_RULE_METHOD = "rule"
LINK_VERIFICATION_METHOD = "import_manifest"
DISCIPLINE_SCHEME_CODE = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME_CODE = "SYMBOL-CATEGORY-FAMILY"
REPRESENTATION_TYPE_SCHEME_CODE = "REPRESENTATION-TYPE"
REPRESENTATION_TYPE_NODE_CODE = "INTELLIGENT_VECTOR_DRAWING_CAD"

EN_60617 = {
    "standard_code": "EN 60617",
    "title": "Graphical symbols for diagrams",
    "issuing_body": "CENELEC (European adoption of IEC 60617)",
    "version_label": "edition not stated",
    "edition_basis": "QElectroTech cites the EN 60617 clause number and no edition",
}

FALLBACK_CREDIT = "QElectroTech contributors"
CREATOR_PLACEHOLDER = "{creator}"
# QElectroTech's own wording (Laurent Trinques, 2026-10-19). Do not edit it
# without their agreement.
ATTRIBUTION_TEMPLATE = (
    "From the QElectroTech elements collection (https://qelectrotech.org), by {creator}. "
    "Licensed under CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/). "
    "Converted to SVG from the original .elmt element."
)


class QetPlanError(ValueError):
    """The package does not support an import. Names data, never a credential."""


# --------------------------------------------------------------------------
# Small helpers.
# --------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug_for(qet_uuid: str) -> str:
    return f"{SLUG_PREFIX}{qet_uuid}"


def svg_filename(qet_uuid: str) -> str:
    return f"qet-{qet_uuid}.svg"


def asset_object_key(slug: str, revision_label: str, digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise QetPlanError(f"{slug}: no SHA-256 digest for its SVG")
    return f"qet/{PACKAGE_CODE}/{slug}/{revision_label}/primary-{digest}.svg"


def concept_name_for(concept_key: str) -> str:
    return concept_key[len(NEW_CONCEPT_PREFIX):] if concept_key.startswith(NEW_CONCEPT_PREFIX) else concept_key


def attribution_text(creator: str) -> str:
    return ATTRIBUTION_TEMPLATE.replace(CREATOR_PLACEHOLDER, creator)


def _loose(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _elements(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["qet_uuid"]: entry for entry in package["manifest"]["elements"]}


def conversions(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`convert` for every imported row, keyed by uuid. Cached on the package dict."""
    cached = package.get("_conversions")
    if cached is not None:
        return cached
    results: dict[str, dict[str, Any]] = {}
    elements = _elements(package)
    for row in package["worksheet"]:
        entry = elements.get((row.get("qet_uuid") or "").strip())
        if row.get("action") != IMPORT_ACTION or entry is None:
            continue
        source = package["elmt"].get(entry["elmt_file"])
        if source is None:
            continue
        try:
            result = convert(source)
        except ElementDefinitionError as exc:
            results[entry["qet_uuid"]] = {"error": str(exc)}
            continue
        data = result.svg.encode("utf-8")
        results[entry["qet_uuid"]] = {
            "bytes": data,
            "sha256": sha256_hex(data),
            "warnings": list(result.warnings),
            "removed_labels": list(result.removed_labels),
            "terminals": len(result.terminals),
        }
    package["_conversions"] = results
    return results


# --------------------------------------------------------------------------
# Validating the package.
# --------------------------------------------------------------------------


def validate_package(package: dict[str, Any]) -> list[str]:
    """Every way the package cannot be imported as curated. Each one blocks."""
    problems: list[str] = []
    source = package["manifest"].get("source") or {}
    for key in ("repository", "commit", "retrieved_on"):
        if not source.get(key):
            problems.append(f"manifest.json source has no {key}")
    elements = _elements(package)
    converted = conversions(package)
    disciplines = set(FACET_DISCIPLINE_ORDER) & set(TAXONOMY_DISCIPLINE_ORDER)
    categories = set(FACET_CATEGORY_ORDER) & set(TAXONOMY_CATEGORY_ORDER)
    seen: set[str] = set()
    for row in package["worksheet"]:
        uid = (row.get("qet_uuid") or "").strip()
        label = f"row {uid or '?'}"
        if not _UUID.match(uid):
            problems.append(f"{label}: qet_uuid is not a lower-case UUID")
            continue
        if uid in seen:
            problems.append(f"{label}: appears twice in the worksheet")
        seen.add(uid)
        if (row.get("confirmed") or "").strip().lower() != "yes":
            problems.append(f"{label}: not confirmed")
        action = row.get("action")
        if action not in ACTIONS:
            problems.append(f"{label}: action {action!r} is not import or drop")
            continue
        entry = elements.get(uid)
        if entry is None:
            problems.append(f"{label}: not in manifest.json")
            continue
        if action == DROP_ACTION:
            continue
        if not (row.get("proposed_name") or "").strip():
            problems.append(f"{label}: no proposed_name")
        discipline = row.get("discipline") or ""
        if discipline not in disciplines or canonical_discipline(discipline) != discipline:
            problems.append(f"{label}: discipline {discipline!r} is not a canonical discipline")
        if (row.get("category") or "") not in categories:
            problems.append(f"{label}: category {row.get('category')!r} is not a canonical category")
        key = (row.get("concept_key") or "").strip()
        if not key.startswith(NEW_CONCEPT_PREFIX) or not _NEW_KEY.match(concept_name_for(key)):
            problems.append(f"{label}: concept key {key!r} is not NEW:<CamelCaseKey>")
        if not _CLAUSE.match(entry.get("en60617_ref") or ""):
            problems.append(f"{label}: no EN 60617 clause, so the gate would hold it")
        elif (row.get("en60617_ref") or entry["en60617_ref"]) != entry["en60617_ref"]:
            problems.append(f"{label}: worksheet clause differs from the manifest's")
        if entry.get("licence_class") not in LICENCE_CLASSES:
            problems.append(f"{label}: licence {entry.get('licence_class')!r} is not CC BY 3.0 or the collection default")
        if entry.get("decision") != "candidate":
            problems.append(f"{label}: the harvester did not make it a candidate ({entry.get('decision')})")
        if not (entry.get("credit") or "").strip():
            problems.append(f"{label}: no author credit")
        data = package["elmt"].get(entry["elmt_file"])
        if data is None:
            problems.append(f"{label}: {entry['elmt_file']} is not in the package")
            continue
        if sha256_hex(data) != entry.get("elmt_sha256") or len(data) != entry.get("size_bytes"):
            problems.append(f"{label}: {entry['elmt_file']} does not match its recorded digest and size")
            continue
        result = converted.get(uid) or {"error": "not converted"}
        if "error" in result:
            problems.append(f"{label}: {result['error']}")
            continue
        if result["warnings"]:
            problems.append(f"{label}: conversion warnings: {'; '.join(result['warnings'])}")
        for problem in final_svg_problems(result["bytes"]):
            problems.append(f"{label}: the SVG to upload fails the safety check ({problem})")
    return problems


# --------------------------------------------------------------------------
# Concepts.
# --------------------------------------------------------------------------


def plan_concepts(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct concept key, in key order."""
    grouped: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        bucket = grouped.setdefault(symbol["concept_key"], {"names": [], "clauses": [], "symbols": []})
        bucket["names"].append(symbol["qet"]["names"].get("en") or symbol["canonical_name"])
        bucket["clauses"].append(symbol["qet"]["en60617_ref"])
        bucket["symbols"].append(symbol["slug"])
    concepts = []
    for key in sorted(grouped):
        bucket = grouped[key]
        name = concept_name_for(key)
        aliases: list[str] = []
        for label in bucket["names"]:
            if label and label.casefold() != name.casefold() and label not in aliases:
                aliases.append(label)
        clauses = sorted(set(bucket["clauses"]))
        concepts.append({
            "concept_key": key,
            "preferred_name": name,
            "is_new": True,
            "symbols": sorted(bucket["symbols"]),
            "concept_kind": DEFAULT_CONCEPT_KIND,
            "definition": (
                f"{bucket['names'][0]}: an electrical diagram symbol concept, EN 60617 "
                f"{', '.join(clauses[:6])}{' and others' if len(clauses) > 6 else ''}. Created from the "
                "QElectroTech elements import, which names the drawing and its clause but publishes no "
                "further definition."
            )[:4000],
            "aliases": aliases,
            "notes": NEW_CONCEPT_NOTES,
            "rationale": "Proposed in the phase 1 worksheet and confirmed by the product owner.",
        })
    return concepts


def match_concepts(
    planned: list[dict[str, Any]], existing: dict[str, dict[str, Any]], own: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Reuse, create, and near misses. Never a problem: an exact name reuses.

    `existing` is `dexpi_seed.index_existing`: every concept by folded
    preferred name. `own` holds the folded names this import created on an
    earlier run, so a re-run reuses them and changes nothing.
    """
    reuse, create, near_misses = [], [], []
    loose_existing: dict[str, list[str]] = defaultdict(list)
    for name in existing:
        loose_existing[_loose(name)].append(name)
    for concept in planned:
        folded = concept["preferred_name"].casefold()
        match = existing.get(folded)
        if match is not None:
            reuse.append({
                **concept, "concept_code": match["concept_code"], "concept_id": str(match["concept_id"]),
                "created_by_this_import": folded in own,
            })
            continue
        similar = [name for name in loose_existing.get(_loose(concept["preferred_name"]), []) if name != folded]
        if similar:
            near_misses.append({"concept_key": concept["concept_key"], "existing_names": sorted(similar)})
        create.append(concept)
    return {"reuse": reuse, "create": create, "problems": [], "near_misses": near_misses}


# --------------------------------------------------------------------------
# The symbols.
# --------------------------------------------------------------------------


def _qet_block(entry: dict[str, Any], source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "uuid": entry["qet_uuid"],
        "path": entry["path"],
        "set": entry["set"],
        "folder": entry.get("folder_names"),
        "names": dict(entry.get("names") or {}),
        "en60617_ref": entry["en60617_ref"],
        "author": entry["credit"],
        "credit_basis": entry["credit_basis"],
        "converted_by": entry.get("converter"),
        "licence": "CC BY 3.0",
        "licence_basis": entry["licence_class"],
        "licence_text": entry.get("licence_text"),
        "terminals": result["terminals"],
        "removed_labels": list(result["removed_labels"]),
        "repository": source["repository"],
        "commit": source["commit"],
        "retrieved_on": source["retrieved_on"],
        "element_url": f"{source['repository']}/blob/{source['commit']}/{entry['path']}",
        "original_sha256": entry["elmt_sha256"],
    }


def build_revision_payload(symbol: dict[str, Any], *, revision_label: str) -> dict[str, Any]:
    """The payload the Catalogue renders. `attribution.creator` is what Details credits."""
    asset = {
        "object_key": asset_object_key(symbol["slug"], revision_label, symbol["final_sha256"]),
        "filename": svg_filename(symbol["qet"]["uuid"]),
        "content_type": SVG_CONTENT_TYPE,
        "format": "svg",
        "role": "primary",
        "sha256": symbol["final_sha256"],
        "size_bytes": symbol["final_size"],
        "source_path": symbol["entry"]["source_path"],
    }
    qet = symbol["qet"]
    aliases = []
    for alias in [qet["names"].get("en"), qet["names"].get("fr"), qet["names"].get("de")]:
        # A translation that is a whole description ("Objet, par exemple: ...") is no alias.
        if alias and len(alias) <= 80 and ":" not in alias and alias != symbol["canonical_name"] and alias not in aliases:
            aliases.append(alias)
    keywords = ["EN 60617", f"EN 60617 {qet['en60617_ref']}", "QElectroTech", symbol["category"], symbol["discipline"]]
    summary = (
        f"{symbol['canonical_name']}. Electrical symbol, EN 60617 {qet['en60617_ref']}, "
        f"from the QElectroTech elements collection by {qet['author']} (CC BY 3.0)."
    )
    return {
        "name": symbol["canonical_name"],
        "summary": summary,
        "description": summary,
        "aliases": aliases,
        "keywords": keywords,
        "assets": [dict(asset)],
        "visual_assets": {"source_assets": [dict(asset)], "state_variants": []},
        "semantic_concept": {"concept_key": concept_name_for(symbol["concept_key"])},
        "standards": [
            {"standard_code": item["standard_code"], "cited_as": item["cited_as"], "relationship_type": item["relationship_type"]}
            for item in symbol["standards"]
        ],
        "attribution": {"creator": qet["author"], "text": attribution_text(qet["author"]), "source_url": QET_URL},
        "qet": dict(qet),
    }


def plan_symbols(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Every importable row. Rows the validator would refuse are left out, so a dry run can still report."""
    elements = _elements(package)
    converted = conversions(package)
    source = package["manifest"]["source"]
    symbols = []
    for order, row in enumerate(package["worksheet"]):
        uid = (row.get("qet_uuid") or "").strip()
        entry = elements.get(uid)
        result = converted.get(uid)
        if row.get("action") != IMPORT_ACTION or entry is None or not result or "error" in result:
            continue
        name = (row.get("proposed_name") or "").strip()
        if not name or not _CLAUSE.match(entry.get("en60617_ref") or ""):
            continue
        ref = entry["en60617_ref"]
        symbols.append({
            "qet_uuid": uid,
            "slug": slug_for(uid),
            "canonical_name": name,
            "category": row["category"],
            "discipline": row["discipline"],
            "sort_order": order,
            "concept_key": row["concept_key"].strip(),
            "original_sha256": entry["elmt_sha256"],
            "final_sha256": result["sha256"],
            "final_size": len(result["bytes"]),
            "conversion": {
                "tool_name": CONVERTER_NAME,
                "tool_version": CONVERTER_VERSION,
                "source_sha256": entry["elmt_sha256"],
                "derived_sha256": result["sha256"],
                "removed_labels": list(result["removed_labels"]),
            },
            "standards": [{
                **EN_60617,
                "cited_as": f"EN 60617 {ref}",
                "clause_reference": ref,
                "relationship_type": "vendor_implementation",
                "basis": "qet_element_cites_clause" if entry["set"] != "10_electric/91_en_60617" else "qet_en60617_set",
            }],
            "qet": _qet_block(entry, source, result),
            "entry": {
                "source_label": name,
                "provider_entry_identifier": uid,
                "source_path": entry["path"],
                "original_asset_sha256": entry["elmt_sha256"],
            },
            "rationale": (
                f"Imported from QElectroTech element {entry['path']} (CC BY 3.0) at commit {source['commit'][:12]}; "
                "name, discipline, category and concept as confirmed by the product owner."
            ),
        })
    return symbols


def revision_needs_bump(existing_payload: dict[str, Any] | None, symbol: dict[str, Any]) -> list[str]:
    if existing_payload is None:
        return ["no revision"]
    reasons = []
    if ((existing_payload.get("assets") or [{}])[0]).get("sha256") != symbol["final_sha256"]:
        reasons.append("SVG sha256 changed")
    if existing_payload.get("name") != symbol["canonical_name"]:
        reasons.append("name changed")
    if (existing_payload.get("semantic_concept") or {}).get("concept_key") != concept_name_for(symbol["concept_key"]):
        reasons.append("concept changed")
    if (existing_payload.get("attribution") or {}).get("creator") != symbol["qet"]["author"]:
        reasons.append("author changed")
    return reasons


def next_revision_label(labels: list[str]) -> str:
    numbers = [int(label[1:]) for label in labels if re.fullmatch(r"r[0-9]+", label)]
    return f"r{max(numbers) + 1}" if numbers else FIRST_REVISION_LABEL


# --------------------------------------------------------------------------
# Rights.
# --------------------------------------------------------------------------


def rights_plan(symbols: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    """One `open` record on the package; its attribution is QElectroTech's template."""
    credits = Counter(symbol["qet"]["author"] for symbol in symbols)
    return {
        "rights_status": "open",
        "disposition": "distribute",
        "determination_method": "licence_document",
        "licence_reference": (
            f"CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/), per ELEMENTS.LICENSE at "
            f"{source['repository']} commit {source['commit'][:12]} and each element's own licence field; "
            "use in Symgov confirmed in writing by QElectroTech on 2026-10-19."
        ),
        "decision_reason": (
            "The elements are CC BY 3.0. QElectroTech (Laurent Trinques) confirmed on 2026-10-19 that Symgov may "
            "include them with their attribution wording, a visible qelectrotech.org link, credit to each element's "
            "author, and no suggestion of partnership or endorsement; the machine-learning clause in ELEMENTS.LICENSE "
            "is being removed and their email is the written basis until it is."
        ),
        "evidence": {
            "basis": "ELEMENTS.LICENSE (CC BY 3.0) and QElectroTech's written permission of 2026-10-19",
            "source": source["repository"],
            "source_commit": source["commit"],
            "retrieved_on": source["retrieved_on"],
            "source_url": QET_URL,
            "attribution_template": ATTRIBUTION_TEMPLATE,
            "attribution_text": attribution_text(FALLBACK_CREDIT),
            "attribution_is_placeholder": False,
            "attribution_required": True,
            "licensor": "QElectroTech",
            "creator": FALLBACK_CREDIT,
            "permission": {
                "from": "Laurent Trinques, QElectroTech",
                "date": "2026-10-19",
                "conditions": [
                    "use QElectroTech's attribution wording",
                    "a clearly visible link to qelectrotech.org on each symbol page",
                    "credit each element's own author",
                    "never present QElectroTech as a partner or endorser",
                    "Ed may use element names and descriptions, never the drawings",
                ],
            },
            "no_endorsement": True,
            "credits": dict(sorted(credits.items())),
            "file_count": len(symbols),
        },
    }


# --------------------------------------------------------------------------
# The whole plan.
# --------------------------------------------------------------------------


def plan_import(package: dict[str, Any]) -> dict[str, Any]:
    """Everything the importer can decide without a database."""
    problems = validate_package(package)
    source = package["manifest"].get("source") or {}
    symbols = plan_symbols(package) if source.get("commit") else []
    concepts = plan_concepts(symbols)
    worksheet = package["worksheet"]
    drops = [
        {"qet_uuid": row.get("qet_uuid"), "name": row.get("proposed_name"), "notes": row.get("notes")}
        for row in worksheet if row.get("action") == DROP_ACTION
    ]
    standards = []
    if symbols:
        standards = [{
            "standard_code": EN_60617["standard_code"], "title": EN_60617["title"],
            "issuing_body": EN_60617["issuing_body"], "versions": [EN_60617["version_label"]],
        }]
    return {
        "package_problems": problems,
        "excluded": [],
        "drops": drops,
        "symbols": symbols,
        "concepts": concepts,
        "standards": standards,
        "source_package": {
            "package_code": PACKAGE_CODE,
            "title": "QElectroTech elements collection: EN 60617 symbols (phase 1)",
            "provider": "QElectroTech",
            "source_uri": source.get("repository") or REPOSITORY_URL,
            "release_version": f"commit {str(source.get('commit') or '')[:12]}",
            "acquisition_method": "public_download",
            "metadata": {
                "retrieved_on": source.get("retrieved_on"),
                "source_commit": source.get("commit"),
                "organisation_url": QET_URL,
                "licensor": "QElectroTech",
                "creator": FALLBACK_CREDIT,
                "harvest": "symgov-qet-harvest-20261020",
                "phase": package["manifest"].get("phase"),
                "worksheet": "curation/worksheet.csv (every row confirmed by the product owner)",
                "imported_symbols": len(symbols),
                "dropped_rows": len(drops),
            },
        },
        "rights": rights_plan(symbols, source) if source.get("commit") else None,
        "publication": {"pack_code": PUBLICATION_PACK_CODE, "pack_title": PUBLICATION_PACK_TITLE},
        "summary": {
            "worksheet_rows": len(worksheet),
            "actions": dict(Counter(row.get("action") for row in worksheet)),
            "symbols": len(symbols),
            "dropped": len(drops),
            "disciplines": dict(Counter(symbol["discipline"] for symbol in symbols)),
            "categories": dict(Counter(symbol["category"] for symbol in symbols)),
            "concepts": len(concepts),
            "credits": dict(Counter(symbol["qet"]["author"] for symbol in symbols)),
            "labels_removed": sum(1 for symbol in symbols if symbol["conversion"]["removed_labels"]),
            "standard_links": dict(Counter(item["standard_code"] for symbol in symbols for item in symbol["standards"])),
            "without_standard_link": [symbol["slug"] for symbol in symbols if not symbol["standards"]],
        },
    }
