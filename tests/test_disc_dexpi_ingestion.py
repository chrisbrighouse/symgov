"""The DISC DEXPI import's planner, decided without a database."""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from disc_dexpi_fixtures import ATTRIBUTION, build_package, change_primary  # noqa: E402

from symgov_backend.asset_manifest import (  # noqa: E402
    list_available_assets,
    list_download_assets,
    list_preview_assets,
    list_state_variant_assets,
    select_preview_asset,
)
from symgov_backend.services import disc_dexpi_ingestion as plan_module  # noqa: E402
from symgov_backend.services.disc_dexpi_ingestion import DiscPlanError  # noqa: E402


@pytest.fixture()
def package():
    return build_package()


@pytest.fixture()
def plan(package):
    return plan_module.plan_import(package)


class TestPackageValidation:
    def test_a_consistent_package_has_no_problems(self, package):
        assert plan_module.validate_package(package) == []

    def test_a_changed_svg_is_caught_by_its_digest(self, package):
        package["svg"]["ND0136.svg"] = b"<svg/>"
        problems = plan_module.validate_package(package)
        assert any("ND0136.svg does not match its sha256" in problem for problem in problems)

    def test_a_missing_file_and_an_unreferenced_file_are_both_caught(self, package):
        del package["svg"]["ND0050.svg"]
        package["svg"]["stray.svg"] = b"<svg/>"
        problems = plan_module.validate_package(package)
        assert any("ND0050.svg is not in svg/" in problem for problem in problems)
        assert any("no record references" in problem for problem in problems)

    def test_counts_must_agree_with_the_report(self, package):
        package["report"]["with_connection_points"] += 1
        assert any("connection points" in problem for problem in plan_module.validate_package(package))

    def test_a_held_back_symbol_in_the_manifest_is_caught(self, package):
        package["held_back"].append({"disc_id": "ND0136", "description": "x", "dexpi_class": "x", "mapping_status": "TBD", "reason": "x"})
        package["report"]["held_back"] += 1
        assert any("held-back symbols are in the manifest" in problem for problem in plan_module.validate_package(package))

    def test_the_attribution_must_be_identical_everywhere(self, package):
        package["manifest"][1]["rights"]["attribution_text"] += " (edited)"
        assert any("not identical" in problem for problem in plan_module.validate_package(package))


class TestSchemeNodes:
    def test_the_node_code_grammar_is_met_and_the_package_clash_is_resolved_by_rule(self, plan):
        codes = plan["scheme"]["code_map"]
        assert codes["package:Equipment"] == "PACKAGE_EQUIPMENT"
        assert codes["dexpi_class:Equipment"] == "EQUIPMENT"
        assert codes["dexpi_class:CentrifugalPump"] == "CENTRIFUGAL_PUMP"
        assert codes["custom_type:DoubleBlockAndBleedValve"] == "DOUBLE_BLOCK_AND_BLEED_VALVE"

    def test_two_names_that_become_one_code_fail_the_plan(self, package):
        package["scheme"]["nodes"].append(
            {"code": "centrifugal_pump", "label": "Dupe", "parent": "Equipment", "kind": "dexpi_class",
             "description": "x", "uri": None, "source": "x"}
        )
        with pytest.raises(DiscPlanError, match="node code CENTRIFUGAL_PUMP would be used by"):
            plan_module.plan_import(package)

    def test_a_code_the_grammar_refuses_fails_the_plan(self, package):
        package["scheme"]["nodes"][4]["code"] = "X"
        with pytest.raises(DiscPlanError, match="grammar"):
            plan_module.plan_import(package)

    def test_a_node_whose_parent_is_the_wrong_kind_fails_the_plan(self, package):
        package["scheme"]["nodes"][4]["parent"] = "CentrifugalPump"
        with pytest.raises(DiscPlanError, match="not a package"):
            plan_module.plan_import(package)

    def test_the_manifest_reaches_custom_types_by_class_and_name(self, plan):
        node = plan["nodes_by_ref"]["CustomOperatedValve/DoubleBlockAndBleedValve"]
        assert node["kind"] == "custom_type"
        assert node["label"] == "DoubleBlockAndBleedValve"
        assert node["friendly_label"] == "Double Block And Bleed Valve"
        assert plan["nodes_by_ref"]["Equipment"]["kind"] == "dexpi_class"

    def test_a_pilot_class_is_the_first_component_class_or_the_concept_key(self):
        component = {"dexpi": {"name_basis": "dexpi_component_class", "component_classes": ["BallValve", "Other"]}}
        reference = {"dexpi": {"name_basis": "dexpi_reference_shape", "semantic_concept": {"concept_key": "InstrumentationBubbleCentral"}}}
        assert plan_module.ttc_class_target(component) == "BallValve"
        assert plan_module.ttc_class_target(reference) == "InstrumentationBubbleCentral"
        assert plan_module.ttc_class_target({"dexpi": {"name_basis": "something_else"}}) is None
        node = plan_module.ttc_missing_node("InstrumentationBubbleCentral")
        assert (node["parent_dexpi_name"], node["source"]) == ("Other", "DEXPI TrainingTestCases")


class TestSymbolsAndPayloads:
    def test_one_slug_per_source_key_and_the_manifest_order_is_kept(self, plan):
        assert [symbol["slug"] for symbol in plan["symbols"]] == [
            "disc-dexpi-nd0004", "disc-dexpi-nd0136", "disc-dexpi-nd0114", "disc-dexpi-nd0050"
        ]
        assert [symbol["sort_order"] for symbol in plan["symbols"]] == [0, 1, 2, 3]

    def test_the_attribution_is_never_in_a_revision_payload(self, plan):
        for symbol in plan["symbols"]:
            payload = plan_module.build_revision_payload(symbol["record"], revision_label="r1", attribution=ATTRIBUTION)
            assert ATTRIBUTION not in repr({key: value for key, value in payload.items() if key != "disc"})
            assert "attribution" not in payload["dexpi"]
            assert "attribution_is_placeholder" not in payload["dexpi"]
            assert payload["dexpi"]["attribution_source"] == "rights_record"
            assert not payload["description"].endswith(".  ")
            assert payload["description"].endswith("concept.")

    def test_a_leak_of_the_attribution_into_the_payload_is_refused(self, package):
        package["manifest"][0]["keywords"].append(ATTRIBUTION)
        symbol = plan_module.plan_import(package)["symbols"][0]
        with pytest.raises(DiscPlanError, match="would be stored in the revision payload"):
            plan_module.build_revision_payload(symbol["record"], revision_label="r1", attribution=ATTRIBUTION)

    def test_the_heat_exchanger_name_is_corrected_in_the_display_fields_only(self, plan):
        symbol = next(item for item in plan["symbols"] if item["disc_id"] == "ND0114")
        payload = plan_module.build_revision_payload(symbol["record"], revision_label="r1", attribution=ATTRIBUTION)
        assert symbol["canonical_name"] == "Exch. Shell and Fixed Tube (DISC ND0114)"
        assert "Fuced" not in repr(payload)
        assert payload["name"] == symbol["canonical_name"]
        assert payload["summary"].startswith("Exch. Shell and Fixed Tube,")

    def test_options_are_kept_apart_from_the_drawing_and_keyed_per_revision(self, plan):
        symbol = plan["symbols"][0]
        payload = plan_module.build_revision_payload(symbol["record"], revision_label="r2", attribution=ATTRIBUTION)
        assert [a["role"] for a in payload["visual_assets"]["source_assets"]] == ["primary"]
        variants = payload["visual_assets"]["state_variants"]
        assert [(v["role"], v["option_index"], v["condition"]) for v in variants] == [
            ("option", 1, "ValvePosition = 'NC'"), ("option", 2, None)
        ]
        keys = [payload["assets"][0]["object_key"], *[v["object_key"] for v in variants]]
        assert len(set(keys)) == 3 and all("/r2/" in key for key in keys)
        assert payload["options"][1] == {"index": 2, "condition": None, "filename": "ND0004_option2.svg", "sha256": variants[1]["sha256"]}
        assert payload["geometry"]["connection_points"][0]["x_mm"] == 9.0
        assert payload["disc"]["disc_id"] == "ND0004"

    def test_zero_from_an_older_package_reads_as_absent(self, package):
        package["manifest"][1]["payload"]["dexpi"]["semantic_concept"]["rdl_uri"] = 0
        package["manifest"][0]["payload"]["dexpi"]["custom_type"]["rdl_uri"] = 0
        plan = plan_module.plan_import(package)
        payload = plan_module.build_revision_payload(plan["symbols"][1]["record"], revision_label="r1", attribution=ATTRIBUTION)
        assert payload["dexpi"]["semantic_concept"]["rdl_uri"] is None
        assert plan_module.null_if_zero(0) is None and plan_module.null_if_zero("x") == "x"
        assert plan_module.null_if_zero(False) is False

    def test_a_revision_is_needed_only_when_a_digest_or_the_geometry_signature_changes(self, package, plan):
        symbol = plan["symbols"][0]
        stored = plan_module.build_revision_payload(symbol["record"], revision_label="r1", attribution=ATTRIBUTION)
        assert plan_module.revision_needs_bump(stored, symbol) == []
        assert plan_module.revision_needs_bump(None, symbol) == ["no revision"]
        changed = plan_module.plan_import(change_primary(package, "ND0004", "a fixed drawing"))["symbols"][0]
        assert plan_module.revision_needs_bump(stored, changed) == ["primary sha256 changed"]
        moved = copy.deepcopy(package)
        moved["manifest"][0]["payload"]["dexpi"]["geometry_signature"] = "f" * 64
        assert plan_module.revision_needs_bump(
            stored, plan_module.plan_import(moved)["symbols"][0]
        ) == ["geometry_signature changed"]
        fewer = copy.deepcopy(package)
        fewer["manifest"][0]["assets"].pop()
        fewer["report"]["option_assets"] -= 1
        fewer["manifest"][0]["payload"]["options"].pop()
        assert plan_module.revision_needs_bump(
            stored, plan_module.plan_import(fewer)["symbols"][0]
        ) == ["option sha256 values changed"]

    def test_an_option_condition_is_what_the_source_states_or_nothing(self, package):
        package["manifest"][0]["assets"][1]["condition"] = "   "
        symbol = plan_module.plan_import(package)["symbols"][0]
        payload = plan_module.build_revision_payload(symbol["record"], revision_label="r1", attribution=ATTRIBUTION)
        assert payload["visual_assets"]["state_variants"][0]["condition"] is None

    def test_option_numbers_must_be_distinct_whole_numbers_from_one(self, package):
        package["manifest"][0]["assets"][2]["option_index"] = 1
        assert any("option numbers" in problem for problem in plan_module.validate_package(package))
        package["manifest"][0]["assets"][2]["option_index"] = 0
        assert any("option numbers" in problem for problem in plan_module.validate_package(package))

    def test_revision_labels_count_on(self):
        assert plan_module.next_revision_label([]) == "r1"
        assert plan_module.next_revision_label(["r1", "r2", "r10"]) == "r11"


class TestConceptsAndMappings:
    def _existing(self, *names):
        return {
            name.casefold(): {"concept_id": f"id-{name}", "concept_code": f"SGC-{index:08d}", "status": "active",
                              "concept_kind": "physical_equipment", "lifecycle_state": "published",
                              "definition": "x", "aliases": []}
            for index, name in enumerate(names, 1)
        }

    def test_a_custom_type_is_a_concept_under_its_class(self, plan):
        concept = next(c for c in plan["concepts"] if c["concept_key"] == "DoubleBlockAndBleedValve")
        assert "CustomOperatedValve" in concept["definition"]
        assert concept["dexpi_node_reference"] == "CustomOperatedValve/DoubleBlockAndBleedValve"
        assert concept["aliases"] == ["DOUBLE BLOCK AND BLEED VALVE", "Double Block And Bleed Valve"]

    def test_an_exact_name_match_reuses_the_existing_concept(self, plan, monkeypatch):
        monkeypatch.setattr(plan_module, "EXPECTED_OVERLAP_CLASSES", ("CentrifugalPump",))
        matching = plan_module.match_concepts(plan["concepts"], self._existing("CentrifugalPump", "Unrelated"))
        assert [c["concept_key"] for c in matching["reuse"]] == ["CentrifugalPump"]
        assert matching["reuse"][0]["concept_code"] == "SGC-00000001"
        assert len(matching["create"]) == len(plan["concepts"]) - 1
        assert matching["overlap"] == [{"concept_key": "CentrifugalPump", "pilot_concept_code": "SGC-00000001", "matched": True}]
        assert matching["expected_but_unmatched"] == [] and matching["near_misses"] == []

    def test_an_expected_class_with_no_concept_is_reported(self, plan, monkeypatch):
        monkeypatch.setattr(plan_module, "EXPECTED_OVERLAP_CLASSES", ("CentrifugalPump", "Label"))
        matching = plan_module.match_concepts(plan["concepts"], self._existing("CentrifugalPump"))
        assert matching["expected_but_unmatched"] == ["Label"]

    def test_a_near_match_is_a_reason_to_stop_not_a_new_concept(self, plan):
        matching = plan_module.match_concepts(plan["concepts"], self._existing("Centrifugal Pump"))
        assert matching["near_misses"] == [{"concept_key": "CentrifugalPump", "existing_names": ["centrifugal pump"]}]

    def test_mappings_are_written_once_per_concept(self, package):
        package["manifest"][1]["external_mappings"] = [
            {"system": "DEXPI RDL", "identifier": "http://sandbox.example.test/rdl/Pump", "label": "Pump", "relation": "broader"}
        ]
        twin = copy.deepcopy(package["manifest"][1])
        twin["source_key"] = "disc-dexpi-ND0137"
        package["manifest"].append(twin)
        mappings = plan_module.plan_external_mappings(package["manifest"])
        pump = [m for m in mappings["mappings"] if m["concept_key"] == "CentrifugalPump"]
        assert len(pump) == 1 and pump[0]["symbols"] == ["ND0136", "ND0137"]
        assert mappings["conflicts"] == []

    def test_two_exact_identifiers_for_one_concept_and_system_are_a_conflict(self, package):
        package["manifest"][1]["external_mappings"] = [
            {"system": "POSC Caesar RDL", "identifier": "http://rdl.example.test/A", "label": "A", "relation": "exact"}
        ]
        twin = copy.deepcopy(package["manifest"][1])
        twin["source_key"] = "disc-dexpi-ND0137"
        twin["external_mappings"][0]["identifier"] = "http://rdl.example.test/B"
        package["manifest"].append(twin)
        conflicts = plan_module.plan_external_mappings(package["manifest"])["conflicts"]
        assert conflicts == [{"concept_key": "CentrifugalPump", "system": "POSC Caesar RDL",
                              "identifiers": ["http://rdl.example.test/A", "http://rdl.example.test/B"]}]

    def test_several_broader_identifiers_are_not_a_conflict(self, package):
        for index, name in enumerate(("CustomMotor", "CustomEquipment")):
            record = copy.deepcopy(package["manifest"][1])
            record["source_key"] = f"disc-dexpi-ND02{index}"
            record["external_mappings"] = [
                {"system": "DEXPI RDL", "identifier": f"http://sandbox.example.test/rdl/{name}", "label": name, "relation": "broader"}
            ]
            package["manifest"].append(record)
        mappings = plan_module.plan_external_mappings(package["manifest"])
        assert mappings["conflicts"] == []

    def test_an_unknown_system_or_relation_is_a_problem(self, package):
        package["manifest"][0]["external_mappings"].append({"system": "Mystery RDL", "identifier": "x", "label": "x", "relation": "exact"})
        package["manifest"][0]["external_mappings"].append({"system": "DEXPI RDL", "identifier": "y", "label": "y", "relation": "narrower"})
        problems = plan_module.plan_external_mappings(package["manifest"])["problems"]
        assert len(problems) == 2


class TestPlanSummary:
    def test_the_summary_counts_what_the_package_holds(self, plan):
        summary = plan["summary"]
        assert summary["symbols"] == 4 and summary["option_assets"] == 2
        assert summary["with_connection_points"] == 3 and summary["with_label_slots"] == 2
        assert summary["warnings"] == 2
        assert summary["scheme_nodes"] == {"package": 3, "dexpi_class": 6, "custom_type": 2}

    def test_the_rights_record_is_a_licensed_distribute_permission_holding_the_text_once(self, plan):
        rights = plan["rights"]
        assert (rights["rights_status"], rights["disposition"]) == ("licensed", "distribute")
        assert rights["evidence"]["attribution_text"] == ATTRIBUTION
        assert rights["evidence"]["attribution_is_placeholder"] is True
        assert rights["licence_reference"] == plan_module.PERMISSION_GRANT == rights["evidence"]["permission"]
        assert rights["licence_reference"].startswith("Permission granted by Tonia Pedersen to Chris Brighouse, Idox Group,")
        for company in ("AIBEL ASA", "AKER BP ASA", "AKER SOLUTIONS ASA", "EQUINOR ASA", "dexpi.org"):
            assert company in rights["licence_reference"]
        assert len(rights["licence_reference"]) <= 512
        assert ATTRIBUTION not in repr(plan["source_package"])

    def test_the_source_package_records_where_the_library_came_from(self, plan):
        package = plan["source_package"]
        assert package["package_code"] == "DISC-DEXPI-0.6.3"
        assert package["metadata"]["source_commit"] == "0123456789abcdef0123456789abcdef01234567"
        assert package["metadata"]["held_back_symbols"] == 2
        assert package["source_uri"] == "https://example.test/disc-library/tree/main"


class TestOptionAssetsNeverShadowThePrimary:
    """The must-fix: preview, thumbnail and download choose the primary explicitly."""

    def _payload(self, plan):
        return plan_module.build_revision_payload(plan["symbols"][0]["record"], revision_label="r1", attribution=ATTRIBUTION)

    def test_download_lists_only_the_primary(self, plan):
        payload = self._payload(plan)
        assert [a["role"] for a in list_download_assets(payload)] == ["primary"]
        assert all(a["role"] == "primary" for a in list_available_assets(payload))

    def test_preview_is_the_primary_even_if_an_option_is_first_in_the_payload(self, plan):
        payload = self._payload(plan)
        option = payload["visual_assets"]["state_variants"][0]
        payload["visual_assets"]["source_assets"].insert(0, dict(option))
        payload["visual_assets"]["preview"] = dict(option)
        payload["downloads"] = [dict(option)]
        primary_key = payload["assets"][0]["object_key"]
        from symgov_backend.asset_manifest import choose_preview_asset

        assert choose_preview_asset(payload)["object_key"] == primary_key
        assert [a["object_key"] for a in list_preview_assets(payload)] == [primary_key]
        assert select_preview_asset(payload, requested_format="svg")["object_key"] == primary_key
        assert [a["object_key"] for a in list_download_assets(payload)] == [primary_key]

    def test_state_variants_list_in_option_order_from_their_own_key(self, plan):
        payload = self._payload(plan)
        assert [a["option_index"] for a in list_state_variant_assets(payload)] == [1, 2]
        assert list_state_variant_assets({"visual_assets": {"source_assets": [{"object_key": "k", "role": "option"}]}}) == []
        assert list_state_variant_assets(None) == []


def test_the_tests_never_depend_on_the_real_attribution_wording():
    assert "DISCDEXPI" not in ATTRIBUTION and "Tonia" not in ATTRIBUTION
    assert hashlib.sha256(ATTRIBUTION.encode()).hexdigest()
