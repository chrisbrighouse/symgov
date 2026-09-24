"""Cover for the DEXPI pilot's semantic concept plan (WP3, decision D7).

The plan is a pure function of the WP2 selection manifest, so all but the last
test runs on small synthetic selections. The last one runs the real one, which
is what proves the count the decision actually committed to.

See `docs/plans/2026-09-22-dexpi-symbol-library-pilot-implementation-plan.md`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.semantic_concepts import (  # noqa: E402
    ALIAS_MAX_LENGTH,
    ALIASES_MAX_COUNT,
    DEFINITION_MAX_LENGTH,
    NOTES_MAX_LENGTH,
    PREFERRED_NAME_MAX_LENGTH,
    RATIONALE_MAX_LENGTH,
    SEMANTIC_CONCEPT_KINDS,
)
from symgov_backend.services.dexpi_concepts import (  # noqa: E402
    DEFAULT_CONCEPT_KIND,
    NAME_BASIS_COMPONENT_CLASS,
    NAME_BASIS_REFERENCE,
    concept_key,
    plan_concepts,
)

SELECTION_PATH = REPO_ROOT / "integrations" / "dexpi" / "selection.json"


def entry(
    signature,
    base_name,
    *,
    basis=NAME_BASIS_COMPONENT_CLASS,
    classes=(),
    elements=("Equipment",),
    names=(),
    vendors=("VER",),
):
    return {
        "geometry_signature": signature,
        "base_name": base_name,
        "canonical_name": base_name,
        "name_basis": basis,
        "component_classes": list(classes),
        "dexpi_elements": list(elements),
        "observed_component_names": list(names),
        "vendors": list(vendors),
    }


def selection(*entries):
    return {"selected": list(entries)}


def by_key(plan):
    return {concept["concept_key"]: concept for concept in plan["concepts"]}


class TestConceptKey:
    def test_a_component_class_is_already_the_key(self):
        assert concept_key("BallValve") == "BallValve"

    def test_the_shape_token_is_dropped(self):
        assert concept_key("BALL_VALVE_SHAPE") == "BallValve"

    def test_a_medial_shape_token_keeps_what_follows_it(self):
        # INSTRUMENTATION_BUBBLE_SHAPE_CENTRAL and ..._FIELD are two different
        # bubbles; truncating at the SHAPE token would merge them.
        assert concept_key("INSTRUMENTATION_BUBBLE_SHAPE_CENTRAL") == "InstrumentationBubbleCentral"
        assert concept_key("INSTRUMENTATION_BUBBLE_SHAPE_FIELD") == "InstrumentationBubbleField"

    def test_a_trailing_separator_yields_no_empty_word(self):
        assert concept_key("PIPING_INSULATED_SHORT_SHAPE_") == "PipingInsulatedShort"

    def test_a_name_that_is_only_the_shape_token_is_kept_verbatim(self):
        # Nothing would be left to name the concept with, so the raw name
        # stands rather than the key collapsing to an empty string.
        assert concept_key("_SHAPE_") == "_SHAPE_"


class TestGrouping:
    def test_a_reference_shape_joins_the_class_it_names(self):
        plan = plan_concepts(
            selection(
                entry("g1", "BallValve", classes=["BallValve"], names=["X807128"]),
                entry(
                    "g2",
                    "BALL_VALVE_SHAPE",
                    basis=NAME_BASIS_REFERENCE,
                    names=["BALL_VALVE_SHAPE"],
                ),
            )
        )
        assert [concept["concept_key"] for concept in plan["concepts"]] == ["BallValve"]
        concept = plan["concepts"][0]
        assert concept["symbol_count"] == 2
        # The class is the classification; the reference name merely agrees.
        assert concept["name_basis"] == NAME_BASIS_COMPONENT_CLASS
        assert concept["aliases"] == ["X807128", "BALL_VALVE_SHAPE"]

    def test_a_reference_shape_with_no_class_gets_its_own_concept(self):
        plan = plan_concepts(
            selection(
                entry(
                    "g1",
                    "BLIND_COVER_SHAPE",
                    basis=NAME_BASIS_REFERENCE,
                    names=["BLIND_COVER_SHAPE"],
                )
            )
        )
        concept = plan["concepts"][0]
        assert concept["concept_key"] == "BlindCover"
        assert concept["name_basis"] == NAME_BASIS_REFERENCE
        assert "reference shape" in concept["definition"]
        assert "D7" in concept["rationale"]

    def test_every_selected_symbol_is_assigned_exactly_once(self):
        plan = plan_concepts(
            selection(
                entry("g1", "Tank", classes=["Tank"]),
                entry("g2", "Tank", classes=["Tank"]),
                entry("g3", "PLUG_SHAPE", basis=NAME_BASIS_REFERENCE),
            )
        )
        assert plan["summary"]["assigned_symbols"] == 3
        assert plan["summary"]["unassigned_symbols"] == 0
        assert [item["geometry_signature"] for item in plan["assignments"]] == ["g1", "g2", "g3"]
        assert {item["concept_key"] for item in plan["assignments"]} == {"Tank", "Plug"}

    def test_concepts_come_out_in_stable_key_order(self):
        plan = plan_concepts(
            selection(
                entry("g1", "Tank", classes=["Tank"]),
                entry("g2", "BallValve", classes=["BallValve"]),
            )
        )
        keys = [concept["concept_key"] for concept in plan["concepts"]]
        assert keys == sorted(keys)


class TestUnnamedClasses:
    """A class observed on a geometry another class gave its name to."""

    def _plan(self):
        return plan_concepts(
            selection(
                entry(
                    "g1",
                    "MeasurementFunctionFlow",
                    classes=["MeasurementFunctionFlow", "PressureFunction"],
                    elements=["ProcessInstrumentationFunction"],
                    names=["@30|M00|A60"],
                )
            )
        )

    def test_it_is_still_seeded(self):
        assert set(by_key(self._plan())) == {"MeasurementFunctionFlow", "PressureFunction"}

    def test_it_owns_no_geometry_and_says_so(self):
        concept = by_key(self._plan())["PressureFunction"]
        assert concept["symbol_count"] == 0
        assert "no distinct catalogue geometry of its own" in concept["definition"]
        assert "`MeasurementFunctionFlow`" in concept["definition"]
        assert "No symbol in this pilot takes this concept as its primary meaning." in concept["notes"]

    def test_no_symbol_is_assigned_to_it(self):
        assert {item["concept_key"] for item in self._plan()["assignments"]} == {
            "MeasurementFunctionFlow"
        }


class TestConceptKind:
    def test_the_kind_comes_from_the_dexpi_element(self):
        plan = plan_concepts(
            selection(
                entry("g1", "BallValve", classes=["BallValve"], elements=["PipingComponent"]),
                entry(
                    "g2",
                    "ProcessInstrumentationFunction",
                    classes=["ProcessInstrumentationFunction"],
                    elements=["ProcessInstrumentationFunction"],
                ),
                entry("g3", "PipeFlowArrow", classes=["PipeFlowArrow"], elements=["PipeFlowArrow"]),
            )
        )
        kinds = {key: concept["concept_kind"] for key, concept in by_key(plan).items()}
        assert kinds == {
            "BallValve": "physical_equipment",
            "ProcessInstrumentationFunction": "function",
            "PipeFlowArrow": "annotation",
        }

    def test_an_unmapped_element_degrades_rather_than_guesses(self):
        plan = plan_concepts(
            selection(entry("g1", "Novelty", classes=["Novelty"], elements=["SomethingNew"]))
        )
        assert plan["concepts"][0]["concept_kind"] == DEFAULT_CONCEPT_KIND

    def test_a_disagreement_the_dexpi_name_settles(self):
        concept = plan_concepts(
            selection(
                entry(
                    "g1",
                    "MeasurementFunctionFlow",
                    classes=["MeasurementFunctionFlow"],
                    elements=["ProcessInstrument", "ProcessInstrumentationFunction"],
                )
            )
        )["concepts"][0]
        assert concept["concept_kind"] == "function"
        assert "more than one DEXPI element" in concept["notes"]
        assert "settles the kind as a function" in concept["notes"]

    def test_a_disagreement_nothing_settles_takes_other_and_records_it(self):
        concept = plan_concepts(
            selection(
                entry("g1", "Vessel", classes=["Vessel"], elements=["Equipment", "PipeFlowArrow"])
            )
        )["concepts"][0]
        assert concept["concept_kind"] == DEFAULT_CONCEPT_KIND
        assert "No corpus fact settles it" in concept["notes"]

    def test_function_must_be_a_whole_word_in_the_name(self):
        # `Functional...` is not a function; only DEXPI's own `Function` token
        # is allowed to settle the tie.
        concept = plan_concepts(
            selection(
                entry(
                    "g1",
                    "FunctionalUnit",
                    classes=["FunctionalUnit"],
                    elements=["ProcessInstrument", "ProcessInstrumentationFunction"],
                )
            )
        )["concepts"][0]
        assert concept["concept_kind"] == DEFAULT_CONCEPT_KIND


class TestAliases:
    def test_the_concept_name_is_not_repeated_as_its_own_alias(self):
        concept = plan_concepts(
            selection(entry("g1", "Tank", classes=["Tank"], names=["Tank", "TANK", "19"]))
        )["concepts"][0]
        assert concept["aliases"] == ["19"]

    def test_aliases_deduplicate_case_insensitively_in_first_seen_order(self):
        concept = plan_concepts(
            selection(
                entry("g1", "Tank", classes=["Tank"], names=["ZZ", "aa"]),
                entry("g2", "Tank", classes=["Tank"], names=["AA", "bb"]),
            )
        )["concepts"][0]
        assert concept["aliases"] == ["ZZ", "aa", "bb"]


class TestRealSelection:
    """The numbers decisions D3 and D7 actually committed to."""

    @pytest.fixture(scope="class")
    def plan(self):
        if not SELECTION_PATH.is_file():
            pytest.skip("the WP2 selection manifest has not been generated")
        return plan_concepts(json.loads(SELECTION_PATH.read_text(encoding="utf-8")))

    def test_it_seeds_eighty_three_concepts(self, plan):
        # 57 DEXPI ComponentClasses under D3 plus 26 reference shapes under D7.
        assert plan["summary"]["concept_count"] == 83
        assert plan["summary"]["name_basis_counts"] == {
            NAME_BASIS_COMPONENT_CLASS: 57,
            NAME_BASIS_REFERENCE: 26,
        }

    def test_every_selected_symbol_has_a_concept(self, plan):
        # This is what D7 exists for: with D3 alone, 43 of the 174 would have
        # reached the publication gate with no verified primary concept.
        assert plan["summary"]["assigned_symbols"] == 174
        assert plan["summary"]["unassigned_symbols"] == 0

    def test_every_class_the_corpus_asserts_is_seeded(self, plan):
        selection_manifest = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
        assert set(selection_manifest["summary"]["concepts"]) <= set(by_key(plan))

    def test_no_concept_is_seeded_twice(self, plan):
        keys = [concept["concept_key"] for concept in plan["concepts"]]
        assert len(set(keys)) == len(keys)

    def test_every_concept_is_storable_as_written(self, plan):
        """The plan is what `create_semantic_concept` will be handed, so every
        one of its limits is checked here rather than 83 rows into a seed."""
        for concept in plan["concepts"]:
            assert concept["concept_kind"] in SEMANTIC_CONCEPT_KINDS
            assert concept["preferred_name"].strip()
            assert len(concept["preferred_name"]) <= PREFERRED_NAME_MAX_LENGTH
            assert concept["definition"].strip()
            assert len(concept["definition"]) <= DEFINITION_MAX_LENGTH
            assert len(concept["notes"]) <= NOTES_MAX_LENGTH
            assert len(concept["rationale"]) <= RATIONALE_MAX_LENGTH
            assert len(concept["aliases"]) <= ALIASES_MAX_COUNT
            for alias in concept["aliases"]:
                assert alias.strip()
                assert len(alias) <= ALIAS_MAX_LENGTH

    def test_the_corpus_version_caveat_is_on_every_concept(self, plan):
        for concept in plan["concepts"]:
            assert "DEXPI 1.2 and 1.3 only" in concept["notes"]

    def test_no_concept_claims_a_definition_dexpi_does_not_publish(self, plan):
        for concept in plan["concepts"]:
            assert "DEXPI publishes no definitional text for it" in concept["definition"]
