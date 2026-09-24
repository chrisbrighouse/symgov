"""Selection of the DEXPI pilot symbol set from converted catalogues (WP2).

Consumes the manifests `dexpi_converter` writes -- one per source file -- and
collapses their catalogue entries into the distinct geometries behind them,
choosing a canonical name for each and recording everything a later ingestion
needs to trace it back.  Like the converter it is a pure function of its input:
no database, no filesystem beyond reading manifests.

**Decision D1 lives here.**  A geometry is ingestible only if it can be named
from something the corpus asserts: the DEXPI reference solutions (`VER`), whose
`*_SHAPE` names are the only clean ones in the corpus, or failing that its
DEXPI `ComponentClass`.  A geometry with neither is *excluded and recorded*,
never renamed into existence -- the shape migration `20260910_0055` used when
it declined to split 67 CFIHOS codes rather than regex an edition out of them.

**Vendor attribution comes from the filename.**  The corpus encodes the
contributing vendor in a code before the extension -- `C01V04-VER.EX01.xml`,
`I11V01_AUD.EX01.xml` -- which is the only place it is recorded at all.  Under
D4 that vendor reaches storage as part of the entry's `source_path` rather than
as a column, so nothing here has to be authoritative about it; it is reported so
a reader can see which vendor drew the chosen rendering.

**The two ISO registration spellings are not merged.**  The corpus writes both
`ISO10628:2012-2322-A` and `ISO10628-X2322A-A01`.  They look like the same
symbol family and they are *not* treated as one: no geometry in the corpus
carries both, so nothing in the data establishes the equivalence, and asserting
it would invent the very traceability the registration number exists to
provide.  The colon form states an edition and the dash form does not, so only
the former can support an edition-pinned standard link.  See
`normalise_registration`.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# `C01V04-VER.EX01.xml` and `I11V01_AUD.EX01.xml`. The vendor code is the
# alphabetic run between the separator and the first dot.
VENDOR_PATTERN = re.compile(r"^[A-Z0-9]+[V_]?\d*[-_]([A-Z]{2,4})\.", re.IGNORECASE)

# The DEXPI reference solutions. Their names are the only human-meaningful ones
# in the corpus -- 37 of 47 are clean `*_SHAPE` names, against zero for SAG --
# so they take precedence over a class-derived name wherever they exist.
REFERENCE_VENDOR = "VER"

# `ISO10628:2012-2322-A`: the edition is stated, so a link can be pinned to it.
REGISTRATION_WITH_EDITION = re.compile(r"^(ISO\s*10628):(\d{4})-(.+)$", re.IGNORECASE)
# `ISO10628-X2322A-A01`: no edition is stated anywhere in the string.
REGISTRATION_WITHOUT_EDITION = re.compile(r"^(ISO\s*10628)-(.+)$", re.IGNORECASE)

# The naming bases, in the order they are tried. Recorded per symbol so a
# reviewer can see why a symbol is called what it is called.
NAME_BASIS_REFERENCE = "dexpi_reference_shape"
NAME_BASIS_COMPONENT_CLASS = "dexpi_component_class"

# Why a geometry was not selected. D1's exclusion is the only one that is a
# decision rather than a conversion failure.
EXCLUSION_NO_NAMING_BASIS = "no_naming_basis"

# `Nozzle` entries are connection stubs rather than catalogue symbols: 185 of
# the corpus's 573 names are nozzles, and none is a symbol a catalogue reader
# would look for. Excluded before selection rather than counted and dropped.
EXCLUDED_DEXPI_ELEMENTS = frozenset({"Nozzle"})


def vendor_for(source_filename: str) -> str | None:
    """The contributing vendor's code, or None when the name does not carry one."""
    match = VENDOR_PATTERN.match(source_filename)
    return match.group(1).upper() if match else None


def normalise_registration(value: str | None) -> dict[str, Any] | None:
    """Split an ISO registration number into what it actually asserts.

    Returns the standard code, the edition *where the string states one*, and
    the provider's own identifier verbatim.  The raw value is always carried:
    it is what reaches `SymbolStandardLink.source_symbol_identifier`, and a
    normalisation that discarded it would break the trace back to the file.
    """
    if not value or not value.strip():
        return None
    raw = value.strip()

    match = REGISTRATION_WITH_EDITION.match(raw)
    if match:
        return {
            "raw": raw,
            "standard_code": "ISO 10628",
            "edition": match.group(2),
            "symbol_identifier": match.group(3),
        }

    match = REGISTRATION_WITHOUT_EDITION.match(raw)
    if match:
        # No edition is stated. Supplying 2012 because the other spelling says
        # so would assert an equivalence the corpus never makes.
        return {
            "raw": raw,
            "standard_code": "ISO 10628",
            "edition": None,
            "symbol_identifier": match.group(2),
        }

    return {"raw": raw, "standard_code": None, "edition": None, "symbol_identifier": raw}


def _canonical_name(occurrences: list[dict[str, Any]]) -> tuple[str, str] | None:
    """The chosen name and the basis it rests on, or None when D1 excludes it."""
    reference_names = sorted(
        {
            occurrence["component_name"]
            for occurrence in occurrences
            if occurrence["vendor"] == REFERENCE_VENDOR and occurrence["component_name"]
        }
    )
    if reference_names:
        return reference_names[0], NAME_BASIS_REFERENCE

    classes = sorted(
        {occurrence["component_class"] for occurrence in occurrences if occurrence["component_class"]}
    )
    if classes:
        # Three geometries in the corpus have occurrences disagreeing on class.
        # The lowest name is taken and every observed class is reported, so the
        # disagreement is visible rather than resolved silently.
        return classes[0], NAME_BASIS_COMPONENT_CLASS

    return None


def _occurrences(manifests: Iterable[tuple[dict[str, Any], str]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for manifest, source_path in manifests:
        source_filename = manifest.get("source_filename") or Path(source_path).name
        vendor = vendor_for(source_filename)
        for symbol in manifest.get("symbols") or []:
            if symbol.get("status") == "failed":
                continue
            if symbol.get("dexpi_element") in EXCLUDED_DEXPI_ELEMENTS:
                continue
            signature = symbol.get("geometry_signature")
            if not signature:
                continue
            grouped.setdefault(signature, []).append(
                {
                    "component_name": symbol.get("component_name"),
                    "component_class": symbol.get("component_class"),
                    "dexpi_element": symbol.get("dexpi_element"),
                    "registration_number": symbol.get("registration_number"),
                    "vendor": vendor,
                    "source_path": source_path,
                    "source_filename": source_filename,
                    "source_sha256": manifest.get("source_sha256"),
                    "declared_units": manifest.get("declared_units"),
                    "svg": symbol.get("svg"),
                    "svg_sha256": symbol.get("svg_sha256"),
                }
            )
    return grouped


def _disambiguate(selected: list[dict[str, Any]]) -> None:
    """Make the canonical names unique, in place.

    D1 settled *where a name comes from*, not that the result is unique, and it
    is not: the corpus yields 11 distinct geometries whose only naming basis is
    the class `Tank` and 12 more that are all `ProcessInstrumentationFunction`.
    A catalogue cannot ship eleven symbols called Tank.

    The distinguishing fact is the vendor -- these are different vendors'
    renderings of the same class, which is exactly what D2 records as
    `vendor_implementation` -- so the vendor and a stable index disambiguate
    them.  `base_name` is kept, because that is what maps to the semantic
    concept under D3; the suffix distinguishes the drawing, not the meaning.
    """
    counts = Counter(item["base_name"] for item in selected)
    running: Counter = Counter()
    for item in selected:
        base = item["base_name"]
        if counts[base] == 1:
            item["canonical_name"] = base
            item["name_disambiguated"] = False
            continue
        vendor = item["vendors"][0] if item["vendors"] else "UNKNOWN"
        running[(base, vendor)] += 1
        item["canonical_name"] = f"{base} ({vendor} {running[(base, vendor)]})"
        item["name_disambiguated"] = True


def _representative(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """Which occurrence's SVG is the one ingested.

    A DEXPI reference occurrence wins where one exists, because that is the
    rendering DEXPI itself publishes.  Otherwise the first by source path, which
    is arbitrary but stable -- the geometries are identical by definition of the
    signature, so the choice affects provenance, not the drawing.
    """
    reference = [item for item in occurrences if item["vendor"] == REFERENCE_VENDOR]
    pool = reference or occurrences
    return sorted(pool, key=lambda item: (item["source_path"], item["component_name"] or ""))[0]


def select_symbols(manifests: Iterable[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    """Collapse converted catalogues into the pilot's selected symbol set."""
    grouped = _occurrences(manifests)

    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for signature in sorted(grouped):
        occurrences = grouped[signature]
        naming = _canonical_name(occurrences)
        observed_names = sorted({item["component_name"] for item in occurrences if item["component_name"]})
        vendors = sorted({item["vendor"] for item in occurrences if item["vendor"]})

        if naming is None:
            excluded.append(
                {
                    "geometry_signature": signature,
                    "reason": EXCLUSION_NO_NAMING_BASIS,
                    "observed_component_names": observed_names,
                    "vendors": vendors,
                    "occurrence_count": len(occurrences),
                }
            )
            continue

        canonical_name, basis = naming
        representative = _representative(occurrences)
        registrations = [
            normalise_registration(item["registration_number"])
            for item in occurrences
            if item["registration_number"]
        ]
        distinct_registrations = {
            entry["raw"]: entry for entry in registrations if entry is not None
        }

        selected.append(
            {
                "geometry_signature": signature,
                # `base_name` is the naming basis D1 chose and what maps to the
                # semantic concept under D3; `canonical_name` is filled in by
                # `_disambiguate` below and may carry a vendor suffix.
                "base_name": canonical_name,
                "canonical_name": canonical_name,
                "name_basis": basis,
                "component_classes": sorted(
                    {item["component_class"] for item in occurrences if item["component_class"]}
                ),
                "dexpi_elements": sorted(
                    {item["dexpi_element"] for item in occurrences if item["dexpi_element"]}
                ),
                # Every name any vendor gave this drawing. Under D3 these become
                # the aliases on the symbol's semantic concept.
                "observed_component_names": observed_names,
                "vendors": vendors,
                "registrations": sorted(distinct_registrations.values(), key=lambda entry: entry["raw"]),
                # What an ingestion run records: the entry it came from, the
                # digest of the file it came from, and the asset it produced.
                "source_path": representative["source_path"],
                "source_filename": representative["source_filename"],
                "source_sha256": representative["source_sha256"],
                "provider_entry_identifier": representative["component_name"],
                "declared_units": representative["declared_units"],
                "svg": representative["svg"],
                "svg_sha256": representative["svg_sha256"],
                "occurrence_count": len(occurrences),
            }
        )

    _disambiguate(selected)

    concepts = {
        component_class for item in selected for component_class in item["component_classes"]
    }
    return {
        "schema_version": "1.0",
        "selected": selected,
        "excluded": excluded,
        "summary": {
            "distinct_geometries": len(grouped),
            "selected_count": len(selected),
            "excluded_count": len(excluded),
            "name_basis_counts": dict(Counter(item["name_basis"] for item in selected)),
            "disambiguated_names": sum(1 for item in selected if item["name_disambiguated"]),
            "with_registration": sum(1 for item in selected if item["registrations"]),
            "with_editioned_registration": sum(
                1
                for item in selected
                if any(entry["edition"] for entry in item["registrations"])
            ),
            # Every distinct class across the selection, not one per symbol:
            # this is the number of SemanticConcept rows WP3 has to seed.
            "concept_count": len(concepts),
            "concepts": sorted(concepts),
            "vendors": dict(Counter(vendor for item in selected for vendor in item["vendors"])),
        },
    }


def load_manifests(root: str | Path) -> list[tuple[dict[str, Any], str]]:
    """Read every `manifest.json` under `root`.

    The path paired with each manifest is the file's location *within the
    corpus*, which is what reaches `SourcePackageEntry.source_path` under D4.
    The converter records only the basename -- it has no idea where the file
    sits in a package -- so the harvest driver writes `source_path` into the
    manifest as it converts, and that is what is read back here.  A manifest
    without one falls back to the basename, which still identifies the entry
    via `provider_entry_identifier` but locates it less precisely.
    """
    base = Path(root)
    manifests: list[tuple[dict[str, Any], str]] = []
    for path in sorted(base.rglob("manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        source_path = manifest.get("source_path") or manifest.get("source_filename") or str(path.parent)
        manifests.append((manifest, source_path))
    return manifests
