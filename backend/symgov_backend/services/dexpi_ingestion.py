"""What the DEXPI pilot ingestion records, decided without a database (WP4).

`dexpi_ingest` writes what this decides, the way `dexpi_seed` writes what
`dexpi_concepts` decides.  Everything here is a pure function of the WP2
selection manifest and the `concept_key -> SGC-########` map that
`dexpi_seed apply --output` produced: no database, no filesystem, no clock.

**The five governed facts each symbol carries** are plan section 4's WP4 list,
and each is decided here rather than in the driver:

1. the `GovernedSymbol` and `SymbolRevision` fields, including the section 1.1
   version caveat and the aliases catalogue search indexes
2. the `source_package_entries` row -- one package, per decision D4
3. the `asset_transformations` step: source XML SHA-256 -> converter and
   version -> SVG SHA-256
4. the `symbol_standard_links` assertion and its edition, per decision D2
5. the semantic concept the symbol belongs to, per decisions D3 and D7, and
   its classification

**Decision D9 (2026-09-23) -- catalogue category, discipline and the
classification node.** `governed_symbols.category` and `.discipline` are NOT
NULL, and section 9.2's classification dimension wants a governed assignment,
so three values have to come from somewhere for all 174 symbols. None of them
exists in the corpus, and inventing a per-symbol judgement is what CLAUDE.md
forbids, so all three are derived from facts the corpus does assert:

* **Discipline** is the corpus itself. `TrainingTestCases` is a P&ID corpus,
  so every symbol is `Piping / P&ID` -- except those whose concept kind is
  `function`, which are instrumentation functions rather than pipework and
  take `Instrumentation & Controls`.
* **Category** is derived from the concept kind first, because the kind comes
  from the DEXPI element that owns the catalogue entry and is therefore
  corpus-asserted, and only then from the concept name. A concept the table
  below does not recognise takes `Equipment` rather than a guess, and the
  driver reports how many did.
* **The classification assignment** goes to the CFIHOS Representation Type
  scheme's `INTELLIGENT_VECTOR_DRAWING_CAD` node, which migration
  `20260909_0052` seeded and which is the one representation-oriented
  vocabulary a migrated database actually has. It is true of all 174 by
  construction -- the converter emits vector SVG and nothing else -- which is
  why the method is `rule` and the assignment verifies without a reviewer.
  The catalogue's own three schemes (`ENGINEERING-DISCIPLINE`,
  `SYMBOL-CATEGORY-FAMILY`, `USE-CASE`) are seeded by no migration and exist
  in tests only, so an assignment against one of them would point at a node
  that is not there.

**What this does not decide.** Whether a symbol publishes. The driver creates
revisions in `approved`, never `published`: the public Catalogue is reached
through `published_pages`, `publication_packs` and
`active_public_symbol_projections`, none of which this touches, and the rights
approval decision D4 leaves to a human has to come first either way.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .dexpi_concepts import NAME_BASIS_REFERENCE, concept_key, plan_concepts

# Decision D4: one package over the whole corpus, so one rights record and one
# human approval.
PACKAGE_CODE = "DEXPI-TTC-1.2-1.3"
PACKAGE_TITLE = "DEXPI TrainingTestCases example corpus"
PACKAGE_PROVIDER = "DEXPI e.V."
PACKAGE_SOURCE_URI = "https://gitlab.com/dexpi/TrainingTestCases"

# Section 1.1's caveat, in `SourcePackage.release_version`: the field the
# CFIHOS seed used to pin an edition, and the key a later DEXPI 2.0 corpus
# would reconcile against.
PACKAGE_RELEASE_VERSION = "DEXPI 1.2/1.3 example corpus"

LICENCE_REFERENCE = "https://creativecommons.org/licenses/by/4.0/"
LICENCE_NAME = "CC BY 4.0"

# `public_download`: the corpus is a public GitLab repository and nobody
# accepted terms to obtain it. `requires_licence_reference` is therefore False
# for it, which is a statement about acquisition and not about rights -- the
# CC BY attribution obligation is recorded in the rights record regardless.
PACKAGE_ACQUISITION_METHOD = "public_download"

# Section 7.12's rights subject is the package, and the determination reads
# the repository's own LICENSE file rather than a human's judgement of it.
RIGHTS_STATUS = "open"
RIGHTS_DISPOSITION = "distribute"
RIGHTS_DETERMINATION_METHOD = "licence_document"

# The standards the assertions point at. DEXPI publishes its P&ID
# specification under two editions in this corpus, and ISO 10628 is the only
# standard any registration number in the selection names.
DEXPI_STANDARD_CODE = "DEXPI"
DEXPI_STANDARD_TITLE = "DEXPI Process Industry Data Exchange P&ID specification"
DEXPI_ISSUING_BODY = "DEXPI e.V."
ISO_10628_CODE = "ISO 10628"
ISO_10628_TITLE = "ISO 10628 -- Diagrams for the chemical and petrochemical industry"
ISO_10628_ISSUING_BODY = "ISO"

# Section 7.10's two controlled-system verification methods. The assertions
# below are read out of the WP2 manifest, which is the provider's own shipped
# content, so `import_manifest` is the honest one and no named verifier is
# needed -- `standard_sources._check_verification` says the same.
LINK_VERIFICATION_METHOD = "import_manifest"

# The corpus directory a source path starts with is the DEXPI edition that
# shipped it. Measured: all 131 class-named symbols are 1.2 and all 43
# reference shapes are 1.3.
_EDITION_BY_CORPUS_DIRECTORY = {"dexpi 1.2": "1.2", "dexpi 1.3": "1.3"}

# Decision D9's classification target, seeded by migration 20260909_0052.
REPRESENTATION_TYPE_SCHEME_CODE = "REPRESENTATION-TYPE"
REPRESENTATION_TYPE_NODE_CODE = "INTELLIGENT_VECTOR_DRAWING_CAD"

# `rule` and `source_mapping` are the two classification methods
# `classification_assignments` auto-verifies. This assignment is a rule in the
# strict sense: the converter emits SVG for every symbol it converts, so the
# node follows from the format without anyone looking at the drawing.
CLASSIFICATION_METHOD = "rule"

# The semantic assignment reads the concept out of the corpus's own naming, so
# it is a source mapping rather than a rule or a judgement.
SEMANTIC_METHOD = "source_mapping"

SVG_CONTENT_TYPE = "image/svg+xml"

REVISION_LABEL = "r1"

# Revisions are created `approved`, not `published`. See the module docstring.
REVISION_LIFECYCLE_STATE = "approved"

# Decision D9. Category from the concept kind where the kind decides it; the
# kinds below come from `dexpi_concepts.CONCEPT_KIND_BY_DEXPI_ELEMENT`, which
# reads the DEXPI element that owns the catalogue entry.
_CATEGORY_BY_CONCEPT_KIND = {
    "annotation": "Annotations / Tags",
    "connection": "Drawing Symbols",
    "function": "Instruments",
}

# Decision D9. Applied to the concept key in order, first match wins, so
# `SwingCheckValve` is a valve rather than an item of equipment. Tokens are
# matched as CamelCase words, never as substrings.
_CATEGORY_BY_CONCEPT_TOKEN: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Valve",), "Valves"),
    (("Pump",), "Pumps"),
    (("Actuator", "Handwheel", "Positioner", "Spring", "Diaphragm"), "Actuators"),
    (("Indicator", "Instrumentation", "Instrument", "Alarm", "Orifice", "Element"), "Instruments"),
    (("Vessel", "Tank", "Column", "Bed", "Tray"), "Vessels / Tanks"),
    (
        ("Pipe", "Piping", "Flange", "Reducer", "Plug", "Tee", "Cover", "Connection"),
        "Pipework / Fittings",
    ),
    (("Arrow", "Label", "Symbol", "Insulation", "Slope", "Break"), "Annotations / Tags"),
)

# What a concept the tables above do not recognise takes. `Equipment` is a
# real catalogue category and says only what the corpus says: a drawn item of
# process plant.
DEFAULT_CATEGORY = "Equipment"

DEFAULT_DISCIPLINE = "Piping / P&ID"
FUNCTION_DISCIPLINE = "Instrumentation & Controls"

# Section 1.1, on every revision, because `payload_json` is what the Catalogue
# renders and a provenance caveat nobody can see is not recorded.
VERSION_NOTE = (
    "Converted from the DEXPI TrainingTestCases example corpus, which publishes "
    "DEXPI 1.2 and 1.3 only; no DEXPI 2.0 example set exists. The shape geometry "
    "is unchanged across 1.2 to 2.0, so this is a provenance caveat rather than a "
    "data-quality one."
)

ATTRIBUTION_NOTE = (
    f"{LICENCE_NAME} requires attribution on every share. Creator: DEXPI e.V. and "
    f"corpus contributors. Source: {PACKAGE_SOURCE_URI}. Licence: {LICENCE_REFERENCE}"
)


class DexpiIngestionPlanError(ValueError):
    """The manifests do not support an ingestion. Names data, never a credential."""


def _camel_tokens(value: str) -> list[str]:
    return re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", value)


def catalogue_category(concept_kind: str, key: str) -> str:
    """Decision D9's category for one symbol, from its concept."""
    by_kind = _CATEGORY_BY_CONCEPT_KIND.get(concept_kind)
    if by_kind is not None:
        return by_kind
    tokens = {token.casefold() for token in _camel_tokens(key)}
    for candidates, category in _CATEGORY_BY_CONCEPT_TOKEN:
        if tokens & {candidate.casefold() for candidate in candidates}:
            return category
    return DEFAULT_CATEGORY


def catalogue_discipline(concept_kind: str) -> str:
    """Decision D9's discipline. The corpus is P&ID; its functions are I&C."""
    return FUNCTION_DISCIPLINE if concept_kind == "function" else DEFAULT_DISCIPLINE


def symbol_slug(geometry_signature: str) -> str:
    """The stable identity of one ingested drawing.

    Derived from the geometry signature rather than from the name, because the
    signature is what decided the symbol existed at all (WP2) and because a
    re-run has to recognise its own earlier work: `governed_symbols.slug` is
    unique, so this is what makes `apply` idempotent by check.
    """
    return f"dexpi-ttc-{geometry_signature[:32]}"


def source_edition(source_path: str) -> str:
    """The DEXPI edition that shipped this file, from the corpus directory."""
    head = source_path.split("/", 1)[0]
    edition = _EDITION_BY_CORPUS_DIRECTORY.get(head)
    if edition is None:
        raise DexpiIngestionPlanError(
            f"source path does not start with a known DEXPI corpus directory: {source_path!r}"
        )
    return edition


def _editioned_registration(item: dict[str, Any]) -> dict[str, Any] | None:
    for registration in item["registrations"]:
        if registration.get("edition"):
            return registration
    return None


def _any_registration(item: dict[str, Any]) -> dict[str, Any] | None:
    return item["registrations"][0] if item["registrations"] else None


def standard_assertion(item: dict[str, Any]) -> dict[str, Any]:
    """Decision D2's relationship for one symbol, and the edition it names.

    Three cases, and the counts they produce are measured rather than assumed:

    * 19 reference shapes carry an ISO 10628 registration that states an
      edition. Those are `normative_definition` against ISO 10628:2012, with
      the registration number as `source_symbol_identifier`.
    * 24 reference shapes carry no registration. They are still DEXPI's own
      normative drawings, so they are `normative_definition` against the DEXPI
      edition that shipped them, identified by the reference shape name --
      which `_check_verification` requires and which is exactly what the
      reference solution calls this symbol.
    * 131 vendor renderings are `vendor_implementation` against their DEXPI
      edition. 7 of them carry an ISO registration number that states no
      edition; the number still reaches `source_symbol_identifier`, so the
      traceability survives the honest weaker relationship type.
    """
    edition = source_edition(item["source_path"])
    if item["name_basis"] == NAME_BASIS_REFERENCE:
        registration = _editioned_registration(item)
        if registration is not None:
            return {
                "relationship_type": "normative_definition",
                "standard_code": registration["standard_code"],
                "version_label": registration["edition"],
                "source_symbol_identifier": registration["symbol_identifier"],
                "basis": "iso_registration_with_edition",
            }
        return {
            "relationship_type": "normative_definition",
            "standard_code": DEXPI_STANDARD_CODE,
            "version_label": edition,
            "source_symbol_identifier": item["base_name"],
            "basis": "dexpi_reference_shape_name",
        }

    registration = _any_registration(item)
    return {
        "relationship_type": "vendor_implementation",
        "standard_code": DEXPI_STANDARD_CODE,
        "version_label": edition,
        "source_symbol_identifier": (
            registration["symbol_identifier"]
            if registration is not None
            else item["provider_entry_identifier"]
        ),
        "basis": (
            "iso_registration_without_edition"
            if registration is not None
            else "vendor_component_name"
        ),
    }


def _aliases(item: dict[str, Any]) -> list[str]:
    """Every other name the corpus gave this drawing, for catalogue search.

    `catalog_search` indexes `aliases`, `keywords` and `search_terms`, and the
    vendor `ComponentName`s are the only alternative names that exist. The
    canonical name is not repeated into its own alias list.
    """
    seen = {item["canonical_name"].casefold()}
    aliases = []
    for name in [item["base_name"], *item["observed_component_names"]]:
        fold = name.casefold()
        if fold in seen:
            continue
        seen.add(fold)
        aliases.append(name)
    return aliases


def _keywords(item: dict[str, Any], *, concept_key_value: str) -> list[str]:
    values = [
        "DEXPI",
        *item["vendors"],
        *item["dexpi_elements"],
        *item["component_classes"],
        concept_key_value,
    ]
    seen: set[str] = set()
    keywords = []
    for value in values:
        fold = value.casefold()
        if fold in seen:
            continue
        seen.add(fold)
        keywords.append(value)
    return keywords


def revision_payload(
    item: dict[str, Any],
    *,
    concept: dict[str, Any],
    assertion: dict[str, Any],
    svg_size_bytes: int | None,
) -> dict[str, Any]:
    """The revision payload the Catalogue renders and search indexes.

    The asset entry carries no `object_key`. Uploading the SVG to object
    storage is a live mutation on a deployment's bucket, which this driver
    deliberately does not perform; the digest is what section 9.2's integrity
    dimension reads, and it is recorded here and in the transformation chain.
    """
    key = concept["concept_key"]
    asset: dict[str, Any] = {
        "filename": item["svg"],
        "content_type": SVG_CONTENT_TYPE,
        "role": "primary",
        "sha256": item["svg_sha256"],
        "source_path": item["source_path"],
    }
    if svg_size_bytes is not None:
        asset["size_bytes"] = svg_size_bytes
    return {
        "name": item["canonical_name"],
        "summary": (
            f"{item['canonical_name']}, converted from the DEXPI "
            f"{source_edition(item['source_path'])} example corpus."
        ),
        "description": (
            f"{item['canonical_name']} as drawn by {', '.join(item['vendors'])} in the DEXPI "
            f"TrainingTestCases corpus, carrying the meaning of the {key} concept. "
            f"{VERSION_NOTE}"
        ),
        "aliases": _aliases(item),
        "keywords": _keywords(item, concept_key_value=key),
        "assets": [asset],
        "dexpi": {
            "geometry_signature": item["geometry_signature"],
            "base_name": item["base_name"],
            "name_basis": item["name_basis"],
            "component_classes": list(item["component_classes"]),
            "dexpi_elements": list(item["dexpi_elements"]),
            "vendors": list(item["vendors"]),
            "declared_units": item["declared_units"],
            "registrations": list(item["registrations"]),
            "specification_versions": ["1.2", "1.3"],
            "version_note": VERSION_NOTE,
            "attribution": ATTRIBUTION_NOTE,
            "semantic_concept": {
                "concept_key": key,
                "concept_code": concept["concept_code"],
            },
            "standard_assertion": {
                "relationship_type": assertion["relationship_type"],
                "standard_code": assertion["standard_code"],
                "version_label": assertion["version_label"],
                "source_symbol_identifier": assertion["source_symbol_identifier"],
            },
        },
    }


def package_metadata(selection: dict[str, Any]) -> dict[str, Any]:
    """Section 1.1's structured caveat, on the package rather than in prose."""
    summary = selection.get("summary") or {}
    return {
        "specification_versions_present": ["1.2", "1.3"],
        "dexpi_2_0_example_set_published": False,
        "geometry_stable_across_editions": True,
        "distinct_geometries": summary.get("distinct_geometries"),
        "selected_symbols": summary.get("selected_count"),
        "excluded_symbols": summary.get("excluded_count"),
        "selection_schema_version": selection.get("schema_version"),
        "attribution": selection.get("attribution"),
        "caveat": VERSION_NOTE,
    }


def plan_ingestion(
    selection: dict[str, Any],
    concept_map: dict[str, Any],
    *,
    svg_sizes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Everything the driver records, decided from the two manifests.

    `concept_map` is what `dexpi_seed apply --output` wrote. A symbol whose
    concept it does not carry is refused rather than ingested without one:
    section 9.2's `semantic_identity` would refuse it at the gate anyway, and
    decisions D3 and D7 exist precisely so that no such symbol remains.
    """
    concepts = concept_map.get("concepts") or {}
    if not concepts:
        raise DexpiIngestionPlanError(
            "the concept map carries no concepts; run `dexpi_seed apply --output` first"
        )

    # The kind each concept was seeded with, from the same pure function that
    # decided it. Decision D9's category reads it, and re-deriving it here
    # keeps the driver from having to carry a field `dexpi_seed`'s map does
    # not record.
    kinds = {
        concept["concept_key"]: concept["concept_kind"]
        for concept in plan_concepts(selection)["concepts"]
    }

    sizes = svg_sizes or {}
    symbols: list[dict[str, Any]] = []
    missing_concepts: list[str] = []

    for sort_order, item in enumerate(selection["selected"]):
        key = concept_key(item["base_name"])
        recorded = concepts.get(key)
        if recorded is None:
            missing_concepts.append(key)
            continue
        concept = {"concept_key": key, **recorded}
        assertion = standard_assertion(item)
        kind = kinds.get(key, "other")
        symbols.append(
            {
                "geometry_signature": item["geometry_signature"],
                "slug": symbol_slug(item["geometry_signature"]),
                "canonical_name": item["canonical_name"],
                "category": catalogue_category(kind, key),
                "discipline": catalogue_discipline(kind),
                "sort_order": sort_order,
                "payload": revision_payload(
                    item,
                    concept=concept,
                    assertion=assertion,
                    svg_size_bytes=sizes.get(item["geometry_signature"]),
                ),
                "entry": {
                    "source_path": item["source_path"],
                    "provider_entry_identifier": item["provider_entry_identifier"],
                    "original_asset_sha256": item["source_sha256"],
                    "source_label": item["canonical_name"],
                },
                "transformation": {
                    "source_asset_sha256": item["source_sha256"],
                    "derived_asset_sha256": item["svg_sha256"],
                    "svg": item["svg"],
                },
                "assertion": assertion,
                "concept": concept,
                "classification": {
                    "scheme_code": REPRESENTATION_TYPE_SCHEME_CODE,
                    "node_code": REPRESENTATION_TYPE_NODE_CODE,
                },
            }
        )

    if missing_concepts:
        raise DexpiIngestionPlanError(
            "the concept map does not carry every selected symbol's concept: "
            + ", ".join(sorted(set(missing_concepts)))
        )

    return {
        "schema_version": "1.0",
        "package": {
            "package_code": PACKAGE_CODE,
            "title": PACKAGE_TITLE,
            "provider": PACKAGE_PROVIDER,
            "source_uri": PACKAGE_SOURCE_URI,
            "release_version": PACKAGE_RELEASE_VERSION,
            "acquisition_method": PACKAGE_ACQUISITION_METHOD,
            "licence_reference": LICENCE_REFERENCE,
            "metadata": package_metadata(selection),
        },
        "rights": {
            "rights_status": RIGHTS_STATUS,
            "disposition": RIGHTS_DISPOSITION,
            "determination_method": RIGHTS_DETERMINATION_METHOD,
            "licence_reference": LICENCE_REFERENCE,
        },
        "standards": _standards(symbols),
        "symbols": symbols,
        "summary": {
            "symbol_count": len(symbols),
            "relationship_types": dict(
                Counter(symbol["assertion"]["relationship_type"] for symbol in symbols)
            ),
            "assertion_bases": dict(
                Counter(symbol["assertion"]["basis"] for symbol in symbols)
            ),
            "categories": dict(Counter(symbol["category"] for symbol in symbols)),
            "disciplines": dict(Counter(symbol["discipline"] for symbol in symbols)),
            "default_category_symbols": sum(
                1 for symbol in symbols if symbol["category"] == DEFAULT_CATEGORY
            ),
            "concepts_used": len({symbol["concept"]["concept_key"] for symbol in symbols}),
        },
    }


def _standards(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The standards and editions the assertions need, deduplicated."""
    titles = {
        DEXPI_STANDARD_CODE: (DEXPI_STANDARD_TITLE, DEXPI_ISSUING_BODY),
        ISO_10628_CODE: (ISO_10628_TITLE, ISO_10628_ISSUING_BODY),
    }
    editions: dict[str, set[str]] = {}
    for symbol in symbols:
        assertion = symbol["assertion"]
        editions.setdefault(assertion["standard_code"], set()).add(assertion["version_label"])
    standards = []
    for code in sorted(editions):
        if code not in titles:
            raise DexpiIngestionPlanError(f"no title is recorded for standard {code!r}")
        title, issuing_body = titles[code]
        standards.append(
            {
                "standard_code": code,
                "title": title,
                "issuing_body": issuing_body,
                "versions": sorted(editions[code]),
            }
        )
    return standards


__all__ = [
    "DexpiIngestionPlanError",
    "catalogue_category",
    "catalogue_discipline",
    "package_metadata",
    "plan_ingestion",
    "revision_payload",
    "source_edition",
    "standard_assertion",
    "symbol_slug",
]
