"""The semantic concept plan behind the DEXPI pilot selection (WP3).

Turns the WP2 selection manifest into the set of `SemanticConcept` rows the
pilot needs and the concept each selected symbol belongs to.  Like the
converter and the selector this is a pure function of its input: no database,
no filesystem.  `dexpi_seed` writes what this decides.

**D3 as originally written covered 131 of the 174 symbols.**  One concept per
DEXPI `ComponentClass` leaves out every geometry that carries no class, and 43
of the selection do -- all of them DEXPI's own `VER` reference shapes, which
name a geometry precisely and classify it not at all.  Those 43 would have
reached the publication gate with no verified primary concept and been refused
on `semantic_identity` (`publication_gate.py:454`).

**D7 (2026-09-23) settles it by naming a concept from the reference shape
name.**  That is the same basis D1 already trusts to name the symbol, and the
cleanest naming the corpus contains, so it asserts nothing the corpus does not.
The alternative -- a section 9.2 `semantic_identity` waiver for each -- was
declined: the exception is scoped to "non-engineering/annotation symbols" and
most of these are valves, vessels and exchangers.

Stripping the `SHAPE` token and case-folding merges 8 reference shapes into
the class concept they were always the same meaning as -- `BALL_VALVE_SHAPE`
is `BallValve` -- which is a merge the corpus supports rather than a rename.
26 distinct reference names remain, for 83 concepts in all.

**Concept kind comes from the DEXPI element, not from the name.**  The corpus
carries the element that owns each catalogue entry -- `Equipment`,
`PipingComponent`, `ProcessInstrumentationFunction` -- and that is a
corpus-asserted category; the 15 observed elements map to SymGov's nine kinds
below.  Where a concept's entries disagree, see `_resolve_kind`.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# The DEXPI 1.2/1.3 elements observed across the selection, mapped to the nine
# kinds `SEMANTIC_CONCEPT_KINDS` allows.  An element outside this table takes
# DEFAULT_CONCEPT_KIND rather than a guess, so a corpus that grows a new
# element degrades to "other" instead of being mis-filed.
CONCEPT_KIND_BY_DEXPI_ELEMENT: dict[str, str] = {
    "Equipment": "physical_equipment",
    "PipingComponent": "physical_equipment",
    "ActuatingSystemComponent": "physical_equipment",
    "InstrumentComponent": "physical_equipment",
    "ProcessInstrument": "physical_equipment",
    "ProcessInstrumentationFunction": "function",
    "ProcessSignalGeneratingFunction": "function",
    "InstrumentationLoopFunction": "function",
    "PipeConnectorSymbol": "connection",
    "PipeOffPageConnector": "connection",
    "PipeFlowArrow": "annotation",
    "PipeSlopeSymbol": "annotation",
    "InsulationSymbol": "annotation",
    "PropertyBreak": "annotation",
    # `Symbol` is DEXPI's untyped container. It classifies nothing, so neither
    # does the concept drawn from it.
    "Symbol": "other",
}
DEFAULT_CONCEPT_KIND = "other"

NAME_BASIS_REFERENCE = "dexpi_reference_shape"
NAME_BASIS_COMPONENT_CLASS = "dexpi_component_class"

# The token DEXPI's reference solutions append (and sometimes infix) to mark a
# catalogue shape. It names the artefact, not the meaning, so the concept drops
# it: `INSTRUMENTATION_BUBBLE_SHAPE_CENTRAL` is the central bubble, not a
# shape of one.
_SHAPE_TOKEN = "SHAPE"

# The word DEXPI puts in a class name when the class is a function rather than
# a device. It is what settles `_resolve_kind`'s tie.
_FUNCTION_TOKEN = "Function"

_CORPUS_RELEASE = "DEXPI 1.2 and 1.3"

_RELEASE_NOTE = (
    f"Seeded from the DEXPI TrainingTestCases corpus, which is {_CORPUS_RELEASE} only; "
    "no DEXPI 2.0 example set exists."
)

_NO_DEFINITION_CLAUSE = (
    "DEXPI publishes no definitional text for it, so this concept records the "
    "identity the corpus asserts and nothing further."
)


def concept_key(base_name: str) -> str:
    """The concept a selected symbol's naming basis points at.

    A `ComponentClass` is already the key -- it carries no underscore and is
    returned untouched. A reference shape name is folded onto the same spelling
    so the two naming bases meet on one concept wherever they mean one thing.
    """
    if "_" not in base_name:
        return base_name
    tokens = [token for token in base_name.split("_") if token and token.upper() != _SHAPE_TOKEN]
    if not tokens:
        return base_name
    return "".join(token[:1].upper() + token[1:].lower() for token in tokens)


def _camel_tokens(key: str) -> list[str]:
    """The CamelCase words in a concept key, so a token test cannot half-match.

    `MeasurementFunctionFlow` yields the token `Function`; a hypothetical
    `Functional...` would not.
    """
    return re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", key)


def _resolve_kind(elements: list[str], key: str) -> tuple[str, str | None]:
    """The concept's kind, and a note when the corpus disagreed with itself.

    Eight instrument classes are carried by catalogue entries that DEXPI files
    under both `ProcessInstrument` and `ProcessInstrumentationFunction` -- a
    device reading and a function reading of the same drawing, on the same two
    entries, so neither is in a majority. Rather than pick by frequency where
    there is none, the class token decides where it speaks plainly: a concept
    DEXPI itself names `...Function` is a function. Anything still ambiguous
    takes `other` and says so, because a wrong kind is worse than an honest
    absence of one.
    """
    kinds = sorted({CONCEPT_KIND_BY_DEXPI_ELEMENT.get(element, DEFAULT_CONCEPT_KIND) for element in elements})
    if not kinds:
        return DEFAULT_CONCEPT_KIND, "No DEXPI element was observed for this concept."
    if len(kinds) == 1:
        return kinds[0], None
    disagreement = (
        "The corpus files this concept under more than one DEXPI element "
        f"({', '.join(sorted(elements))}), which read as {', '.join(kinds)}."
    )
    if _FUNCTION_TOKEN in _camel_tokens(key):
        return "function", f"{disagreement} DEXPI's own name for it settles the kind as a function."
    return DEFAULT_CONCEPT_KIND, f"{disagreement} No corpus fact settles it, so the kind is other."


def _definition(key: str, basis: str, sources: dict[str, Any]) -> str:
    geometry_count = sources["symbol_count"]
    geometries = "geometry" if geometry_count == 1 else "geometries"
    vendor_count = len(sources["vendors"])
    vendors = "vendor" if vendor_count == 1 else "vendors"
    if basis == NAME_BASIS_COMPONENT_CLASS:
        return (
            f"DEXPI ComponentClass `{key}`. Seeded from the DEXPI TrainingTestCases "
            f"corpus ({_CORPUS_RELEASE}), where it classifies {geometry_count} distinct "
            f"catalogue {geometries} drawn by {vendor_count} {vendors}. "
            f"{_NO_DEFINITION_CLAUSE}"
        )
    observed = ", ".join(f"`{name}`" for name in sources["reference_names"])
    return (
        f"DEXPI reference shape {observed}. Seeded from the DEXPI TrainingTestCases "
        f"corpus ({_CORPUS_RELEASE}), whose reference solutions name this geometry but "
        f"assign it no ComponentClass. "
        f"{_NO_DEFINITION_CLAUSE}"
    )


def _shared_geometry_definition(key: str, carriers: list[str]) -> str:
    """For a class the corpus asserts but never names a geometry after.

    Five classes are observed only alongside another class on the same drawing,
    and `_canonical_name` gave the drawing the other one's name. The class is
    still a meaning the corpus asserts, so D3 seeds it; what it has not got is a
    geometry of its own, and the definition says so rather than implying one.
    """
    named = ", ".join(f"`{carrier}`" for carrier in carriers)
    geometries = "geometry" if len(carriers) == 1 else "geometries"
    return (
        f"DEXPI ComponentClass `{key}`. Seeded from the DEXPI TrainingTestCases corpus "
        f"({_CORPUS_RELEASE}), where it is observed only on catalogue {geometries} the "
        f"corpus also classifies as {named}, which carries the name; it has no distinct "
        f"catalogue geometry of its own. "
        f"{_NO_DEFINITION_CLAUSE}"
    )


def _rationale(basis: str) -> str:
    if basis == NAME_BASIS_COMPONENT_CLASS:
        return (
            "Named from the DEXPI ComponentClass the corpus assigns, per decision D3 of the "
            "DEXPI symbol library pilot."
        )
    return (
        "Named from the DEXPI reference shape name, per decision D7 of the DEXPI symbol "
        "library pilot: the reference solutions name this geometry and classify it not at all."
    )


def plan_concepts(selection: dict[str, Any]) -> dict[str, Any]:
    """The concepts to seed, and the concept each selected symbol belongs to."""
    grouped: dict[str, dict[str, Any]] = {}
    assignments: list[dict[str, Any]] = []

    for item in selection["selected"]:
        key = concept_key(item["base_name"])
        bucket = grouped.setdefault(
            key,
            {
                "concept_key": key,
                "bases": set(),
                "aliases": [],
                "alias_keys": set(),
                "dexpi_elements": set(),
                "component_classes": set(),
                "reference_names": set(),
                "vendors": set(),
                "symbol_count": 0,
            },
        )
        bucket["bases"].add(item["name_basis"])
        bucket["symbol_count"] += 1
        bucket["dexpi_elements"].update(item["dexpi_elements"])
        bucket["component_classes"].update(item["component_classes"])
        bucket["vendors"].update(item["vendors"])
        if item["name_basis"] == NAME_BASIS_REFERENCE:
            bucket["reference_names"].add(item["base_name"])
        # Every name any vendor gave a drawing of this concept, first-seen
        # order preserved so the seed is stable run to run.
        for alias in item["observed_component_names"]:
            fold = alias.casefold()
            if fold in bucket["alias_keys"] or fold == key.casefold():
                continue
            bucket["alias_keys"].add(fold)
            bucket["aliases"].append(alias)
        assignments.append(
            {
                "geometry_signature": item["geometry_signature"],
                "canonical_name": item["canonical_name"],
                "concept_key": key,
            }
        )

    # D3 seeds every ComponentClass the corpus asserts, and five of them never
    # name a geometry -- see `_shared_geometry_definition`. They are added here
    # rather than in the loop above because they have no symbol of their own to
    # be found from.
    unnamed: dict[str, dict[str, Any]] = {}
    for item in selection["selected"]:
        for component_class in item["component_classes"]:
            if component_class in grouped:
                continue
            bucket = unnamed.setdefault(
                component_class,
                {"carriers": set(), "elements": set(), "aliases": [], "alias_keys": set(), "vendors": set()},
            )
            bucket["carriers"].add(concept_key(item["base_name"]))
            bucket["elements"].update(item["dexpi_elements"])
            bucket["vendors"].update(item["vendors"])
            for alias in item["observed_component_names"]:
                fold = alias.casefold()
                if fold in bucket["alias_keys"] or fold == component_class.casefold():
                    continue
                bucket["alias_keys"].add(fold)
                bucket["aliases"].append(alias)

    concepts: list[dict[str, Any]] = []
    for key in sorted(unnamed):
        bucket = unnamed[key]
        elements = sorted(bucket["elements"])
        kind, disagreement = _resolve_kind(elements, key)
        notes = [_RELEASE_NOTE, "No symbol in this pilot takes this concept as its primary meaning."]
        if disagreement:
            notes.insert(1, disagreement)
        concepts.append(
            {
                "concept_key": key,
                "preferred_name": key,
                "concept_kind": kind,
                "definition": _shared_geometry_definition(key, sorted(bucket["carriers"])),
                "aliases": bucket["aliases"],
                "notes": " ".join(notes),
                "rationale": _rationale(NAME_BASIS_COMPONENT_CLASS),
                "name_basis": NAME_BASIS_COMPONENT_CLASS,
                "dexpi_elements": elements,
                "component_classes": [key],
                "reference_names": [],
                "vendors": sorted(bucket["vendors"]),
                "symbol_count": 0,
            }
        )

    for key in sorted(grouped):
        bucket = grouped[key]
        # A concept the corpus reaches by both routes rests on the class, which
        # is the classification; the reference name merely agrees with it.
        basis = (
            NAME_BASIS_COMPONENT_CLASS
            if NAME_BASIS_COMPONENT_CLASS in bucket["bases"]
            else NAME_BASIS_REFERENCE
        )
        sources = {
            "symbol_count": bucket["symbol_count"],
            "vendors": sorted(bucket["vendors"]),
            "reference_names": sorted(bucket["reference_names"]),
        }
        elements = sorted(bucket["dexpi_elements"])
        kind, disagreement = _resolve_kind(elements, key)
        notes = [_RELEASE_NOTE]
        if disagreement:
            notes.append(disagreement)
        concepts.append(
            {
                "concept_key": key,
                "preferred_name": key,
                "concept_kind": kind,
                "definition": _definition(key, basis, sources),
                "aliases": bucket["aliases"],
                "notes": " ".join(notes),
                "rationale": _rationale(basis),
                "name_basis": basis,
                "dexpi_elements": elements,
                "component_classes": sorted(bucket["component_classes"]),
                "reference_names": sources["reference_names"],
                "vendors": sources["vendors"],
                "symbol_count": bucket["symbol_count"],
            }
        )

    concepts.sort(key=lambda concept: concept["concept_key"])

    return {
        "schema_version": "1.0",
        "concepts": concepts,
        "assignments": assignments,
        "summary": {
            "concept_count": len(concepts),
            "assigned_symbols": len(assignments),
            "unassigned_symbols": len(selection["selected"]) - len(assignments),
            "name_basis_counts": dict(Counter(concept["name_basis"] for concept in concepts)),
            "kind_counts": dict(Counter(concept["concept_kind"] for concept in concepts)),
            "max_aliases": max((len(concept["aliases"]) for concept in concepts), default=0),
        },
    }
