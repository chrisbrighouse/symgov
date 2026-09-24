"""What the DEXPI pilot ingestion decides, without a database (WP4).

The database half of WP4 is `test_dexpi_ingest_postgresql.py`; everything here
is the pure plan, which is where decisions D2, D4 and D9 actually live. The
counts asserted below are the plan document's own -- 174 selected, 43
normative and 131 vendor, 19 registrations that state an edition and 7 that do
not -- so a change that quietly moves one of them fails here rather than in a
rehearsal an hour later.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.services.dexpi_concepts import plan_concepts  # noqa: E402
from symgov_backend.services.dexpi_ingestion import (  # noqa: E402
    DEFAULT_CATEGORY,
    DEFAULT_DISCIPLINE,
    FUNCTION_DISCIPLINE,
    LICENCE_NAME,
    LICENCE_REFERENCE,
    PACKAGE_CODE,
    PACKAGE_SOURCE_URI,
    REPRESENTATION_TYPE_NODE_CODE,
    REPRESENTATION_TYPE_SCHEME_CODE,
    REVISION_LIFECYCLE_STATE,
    DexpiIngestionPlanError,
    catalogue_category,
    catalogue_discipline,
    package_metadata,
    plan_ingestion,
    source_edition,
    standard_assertion,
    symbol_slug,
)

SELECTION_PATH = REPO_ROOT / "integrations" / "dexpi" / "selection.json"
SOURCE_PANEL_PATH = BACKEND_ROOT / "symgov_backend" / "data" / "dexpi-source.json"


@pytest.fixture(scope="module")
def selection() -> dict:
    if not SELECTION_PATH.is_file():
        pytest.skip("the WP2 selection manifest has not been generated")
    return json.loads(SELECTION_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def concept_map(selection) -> dict:
    """A stand-in for what `dexpi_seed apply --output` writes.

    The plan only needs the code and the id, and inventing them here keeps this
    file free of a database while still exercising the join the driver makes.
    """
    concepts = plan_concepts(selection)["concepts"]
    return {
        "schema_version": "1.0",
        "concepts": {
            concept["concept_key"]: {
                "concept_code": f"SGC-{index:08d}",
                "concept_id": str(uuid.uuid5(uuid.NAMESPACE_URL, concept["concept_key"])),
            }
            for index, concept in enumerate(concepts)
        },
    }


@pytest.fixture(scope="module")
def plan(selection, concept_map) -> dict:
    return plan_ingestion(selection, concept_map)


class TestDecisionD2:
    def test_the_relationship_split_is_43_normative_and_131_vendor(self, plan):
        assert plan["summary"]["relationship_types"] == {
            "normative_definition": 43,
            "vendor_implementation": 131,
        }

    def test_only_editioned_registrations_reach_iso_10628(self, plan):
        iso = [
            symbol
            for symbol in plan["symbols"]
            if symbol["assertion"]["standard_code"] == "ISO 10628"
        ]
        assert len(iso) == 19
        assert {symbol["assertion"]["version_label"] for symbol in iso} == {"2012"}
        assert {symbol["assertion"]["relationship_type"] for symbol in iso} == {
            "normative_definition"
        }

    def test_a_registration_without_an_edition_keeps_its_number_but_not_the_claim(self, plan):
        weaker = [
            symbol
            for symbol in plan["symbols"]
            if symbol["assertion"]["basis"] == "iso_registration_without_edition"
        ]
        assert len(weaker) == 7
        for symbol in weaker:
            # The ISO number survives; the edition claim does not.
            assert symbol["assertion"]["relationship_type"] == "vendor_implementation"
            assert symbol["assertion"]["standard_code"] == "DEXPI"
            assert symbol["assertion"]["source_symbol_identifier"]

    def test_a_reference_shape_with_no_registration_is_named_by_its_own_name(self, plan):
        unregistered = [
            symbol
            for symbol in plan["symbols"]
            if symbol["assertion"]["basis"] == "dexpi_reference_shape_name"
        ]
        assert len(unregistered) == 24
        for symbol in unregistered:
            # `_check_verification` refuses a normative assertion that cannot
            # say which symbol of the standard it means.
            assert symbol["assertion"]["source_symbol_identifier"]
            assert symbol["assertion"]["version_label"] == "1.3"

    def test_every_assertion_carries_an_identifier(self, plan):
        assert all(
            symbol["assertion"]["source_symbol_identifier"] for symbol in plan["symbols"]
        )

    def test_the_edition_comes_from_the_corpus_directory(self):
        assert source_edition("dexpi 1.2/example pids/C01V01-HEX.EX02.xml") == "1.2"
        assert source_edition("dexpi 1.3/example pids/C01V04-VER.EX01.xml") == "1.3"
        with pytest.raises(DexpiIngestionPlanError):
            source_edition("dexpi 2.0/example pids/nothing.xml")

    def test_a_standard_with_no_recorded_title_is_refused(self):
        """An assertion may name any standard; the plan may not invent its title."""
        item = {
            "geometry_signature": "f" * 64,
            "base_name": "X_SHAPE",
            "canonical_name": "X_SHAPE (VER 1)",
            "name_basis": "dexpi_reference_shape",
            "component_classes": [],
            "dexpi_elements": ["PipingComponent"],
            "observed_component_names": ["X_SHAPE"],
            "vendors": ["VER"],
            "registrations": [
                {
                    "raw": "BS 1234:2020-1",
                    "standard_code": "BS 1234",
                    "edition": "2020",
                    "symbol_identifier": "1",
                }
            ],
            "source_path": "dexpi 1.3/a.xml",
            "source_filename": "a.xml",
            "source_sha256": "a" * 64,
            "provider_entry_identifier": "X_SHAPE",
            "declared_units": "mm",
            "svg": "X_SHAPE.svg",
            "svg_sha256": "b" * 64,
            "occurrence_count": 1,
            "name_disambiguated": True,
        }
        assert standard_assertion(item)["standard_code"] == "BS 1234"
        with pytest.raises(DexpiIngestionPlanError, match="no title is recorded"):
            plan_ingestion(
                {"selected": [item], "summary": {}},
                {"concepts": {"X": {"concept_code": "SGC-1", "concept_id": str(uuid.uuid4())}}},
            )


class TestDecisionD9:
    def test_the_concept_kind_decides_the_category_before_the_name_does(self):
        # `PipeSlopeSymbol` is an annotation element, so it is an annotation
        # however pipe-shaped its name reads.
        assert catalogue_category("annotation", "PipeSlopeSymbol") == "Annotations / Tags"
        assert catalogue_category("connection", "FlowInPipeConnectorSymbol") == "Drawing Symbols"
        assert catalogue_category("function", "MeasurementFunctionFlow") == "Instruments"

    def test_names_are_matched_as_whole_camel_case_words(self):
        assert catalogue_category("physical_equipment", "SwingCheckValve") == "Valves"
        assert catalogue_category("physical_equipment", "CentrifugalPump") == "Pumps"
        assert catalogue_category("physical_equipment", "VesselWithDishedHeads") == "Vessels / Tanks"
        assert catalogue_category("physical_equipment", "BlindFlange") == "Pipework / Fittings"

    def test_an_unrecognised_concept_takes_the_default_rather_than_a_guess(self):
        assert catalogue_category("physical_equipment", "Thingummy") == DEFAULT_CATEGORY

    def test_the_corpus_is_p_and_id_and_its_functions_are_instrumentation(self):
        assert catalogue_discipline("physical_equipment") == DEFAULT_DISCIPLINE
        assert catalogue_discipline("function") == FUNCTION_DISCIPLINE

    def test_every_category_is_one_the_catalogue_already_orders(self, plan):
        from symgov_backend.catalog_taxonomy import (
            CATALOG_CATEGORY_ORDER,
            CATALOG_DISCIPLINE_ORDER,
        )

        assert set(plan["summary"]["categories"]) <= set(CATALOG_CATEGORY_ORDER)
        assert set(plan["summary"]["disciplines"]) <= set(CATALOG_DISCIPLINE_ORDER)

    def test_every_symbol_is_classified_against_the_seeded_representation_node(self, plan):
        targets = {
            (symbol["classification"]["scheme_code"], symbol["classification"]["node_code"])
            for symbol in plan["symbols"]
        }
        assert targets == {(REPRESENTATION_TYPE_SCHEME_CODE, REPRESENTATION_TYPE_NODE_CODE)}


class TestThePlan:
    def test_it_covers_every_selected_symbol(self, plan, selection):
        assert plan["summary"]["symbol_count"] == len(selection["selected"]) == 174

    def test_one_package_carries_all_of_them(self, plan):
        assert plan["package"]["package_code"] == PACKAGE_CODE
        assert plan["package"]["acquisition_method"] == "public_download"
        assert plan["rights"]["disposition"] == "distribute"
        assert plan["rights"]["rights_status"] == "open"

    def test_the_package_records_the_section_1_1_caveat(self, plan, selection):
        metadata = package_metadata(selection)
        assert metadata["specification_versions_present"] == ["1.2", "1.3"]
        assert metadata["dexpi_2_0_example_set_published"] is False
        assert plan["package"]["release_version"] == "DEXPI 1.2/1.3 example corpus"

    def test_slugs_are_stable_and_unique(self, plan):
        slugs = [symbol["slug"] for symbol in plan["symbols"]]
        assert len(set(slugs)) == len(slugs)
        assert symbol_slug("abc123" * 8) == symbol_slug("abc123" * 8)
        assert all(slug.startswith("dexpi-ttc-") for slug in slugs)

    def test_each_revision_carries_the_version_note_and_its_attribution(self, plan):
        for symbol in plan["symbols"]:
            dexpi = symbol["payload"]["dexpi"]
            assert "no DEXPI 2.0 example set exists" in dexpi["version_note"]
            assert "CC BY 4.0" in dexpi["attribution"]
            assert "creativecommons.org/licenses/by/4.0" in dexpi["attribution"]

    def test_aliases_carry_every_other_name_the_corpus_gave_the_drawing(self, plan, selection):
        by_signature = {item["geometry_signature"]: item for item in selection["selected"]}
        for symbol in plan["symbols"]:
            item = by_signature[symbol["geometry_signature"]]
            aliases = symbol["payload"]["aliases"]
            assert symbol["canonical_name"] not in aliases
            for name in item["observed_component_names"]:
                if name.casefold() != symbol["canonical_name"].casefold():
                    assert name in aliases

    def test_the_transformation_runs_from_the_source_xml_to_the_svg(self, plan):
        for symbol in plan["symbols"]:
            transformation = symbol["transformation"]
            assert len(transformation["source_asset_sha256"]) == 64
            assert len(transformation["derived_asset_sha256"]) == 64
            assert transformation["source_asset_sha256"] != transformation["derived_asset_sha256"]
            # The entry the chain starts from names the same source digest.
            assert symbol["entry"]["original_asset_sha256"] == transformation["source_asset_sha256"]

    def test_nothing_is_planned_as_published(self, plan):
        assert REVISION_LIFECYCLE_STATE == "approved"

    def test_a_missing_concept_refuses_the_whole_plan(self, selection, concept_map):
        thinned = {
            "concepts": {
                key: value
                for key, value in list(concept_map["concepts"].items())[:10]
            }
        }
        with pytest.raises(DexpiIngestionPlanError, match="does not carry every selected symbol"):
            plan_ingestion(selection, thinned)

    def test_an_empty_concept_map_is_refused_before_anything_else(self, selection):
        with pytest.raises(DexpiIngestionPlanError, match="carries no concepts"):
            plan_ingestion(selection, {"concepts": {}})

    def test_svg_sizes_reach_the_payload_when_they_are_measured(self, selection, concept_map):
        signature = selection["selected"][0]["geometry_signature"]
        sized = plan_ingestion(selection, concept_map, svg_sizes={signature: 1234})
        first = next(
            symbol for symbol in sized["symbols"] if symbol["geometry_signature"] == signature
        )
        assert first["payload"]["assets"][0]["size_bytes"] == 1234
        # A symbol nobody measured carries no invented size.
        others = [
            symbol for symbol in sized["symbols"] if symbol["geometry_signature"] != signature
        ]
        assert "size_bytes" not in others[0]["payload"]["assets"][0]


class TestTheAttributionPanel:
    """D5's panel restates what the SVG metadata and the package record carry.

    `dexpi-source.json` is read by the Support page, so it must name the same
    creator, source and licence the ingestion records; a panel that drifted
    from the package would be an attribution nobody's data supports.
    """

    def test_the_panel_names_the_package_source_and_licence(self):
        panel = json.loads(SOURCE_PANEL_PATH.read_text(encoding="utf-8"))
        assert panel["package_code"] == PACKAGE_CODE
        assert panel["source_url"] == PACKAGE_SOURCE_URI
        assert panel["license_url"] == LICENCE_REFERENCE
        assert panel["license"] == LICENCE_NAME
        assert panel["specification_versions"] == ["1.2", "1.3"]

    def test_the_panel_names_the_creator_the_svgs_carry(self, selection):
        panel = json.loads(SOURCE_PANEL_PATH.read_text(encoding="utf-8"))
        assert panel["creator"] == selection["attribution"]["creator"]
        assert panel["source_url"] == selection["attribution"]["source"]
        assert panel["creator"] in panel["attribution"]
