"""The Wikimedia Commons import plan, decided without a database.

Portable: the planner is a pure function of the package, and the CLI's
`fix-svgs` writes only into the package's `svg-fixed/`.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from commons_fixtures import build_package, copy_package, write_package  # noqa: E402

from symgov_backend import commons_ingest as ingest  # noqa: E402
from symgov_backend import commons_verify  # noqa: E402
from symgov_backend.image_content import UnsafeImageContentError, validate_stored_image  # noqa: E402
from symgov_backend.services import commons_ingestion as plan_module  # noqa: E402


def _fixed(package: dict) -> dict:
    """The package with svg-fixed/ filled the way `fix-svgs` fills it."""
    fixed = copy_package(package)
    for pageid, result in plan_module.fix_results(copy_package(package)).items():
        if "error" not in result and result["steps"] and not result["excluded"]:
            fixed["fixed"][plan_module.svg_filename(pageid)] = result["bytes"]
    return fixed


@pytest.fixture()
def package():
    return _fixed(build_package())


@pytest.fixture()
def plan(package):
    return plan_module.plan_import(package)


def _row(package, pageid):
    return next(row for row in package["worksheet"] if row["pageid"] == str(pageid))


class TestWorksheet:
    def test_a_clean_package_plans_without_problems(self, plan):
        assert plan["package_problems"] == []
        assert plan["summary"]["actions"] == {"import": 4, "import_after_fix": 2, "drop": 1}

    def test_drops_are_honoured_and_never_planned(self, plan):
        assert [item["pageid"] for item in plan["drops"]] == [1005]
        assert "commons-1005" not in {symbol["slug"] for symbol in plan["symbols"]}

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("action", "review", "is not import, import_after_fix or drop"),
            ("discipline", "Ventilation", "is not a canonical discipline"),
            ("discipline", "hvac", "is not a canonical discipline"),
            ("category", "Dampers", "is not a canonical category"),
            ("confirmed", "", "not confirmed"),
            ("concept_key", "NEW:not a key", "is not NEW:<CamelCaseKey>"),
            ("proposed_name", " ", "no proposed_name"),
        ],
    )
    def test_a_value_that_cannot_be_honoured_is_a_problem(self, package, field, value, message):
        _row(package, 1001)[field] = value
        problems = plan_module.plan_import(package)["package_problems"]
        assert any(message in problem and "row 1001" in problem for problem in problems), problems

    def test_a_licence_family_other_than_pd_or_cc0_is_refused(self, package):
        package["manifest"][0]["licence_family"] = "cc-by-sa-4.0"
        problems = plan_module.plan_import(package)["package_problems"]
        assert any("is not pd or cc0" in problem for problem in problems)

    def test_a_file_that_does_not_match_its_sha256_is_refused(self, package):
        package["svg"]["svg/commons-1001.svg"] += b" "
        problems = plan_module.plan_import(package)["package_problems"]
        assert any("does not match its sha256" in problem for problem in problems)

    def test_a_duplicate_pageid_is_refused(self, package):
        package["worksheet"].append(dict(_row(package, 1007)))
        problems = plan_module.plan_import(package)["package_problems"]
        assert any("appears twice" in problem for problem in problems)


class TestSvgFixes:
    def test_a_raster_without_a_fixed_file_is_refused(self):
        raw = build_package()
        problems = plan_module.plan_import(raw)["package_problems"]
        assert any("row 1003" in p and "embeds a raster <image>" in p and "run fix-svgs" in p for p in problems)

    def test_removing_the_raster_keeps_the_vector_drawing(self):
        result = plan_module.fix_svg(build_package()["svg"]["svg/commons-1003.svg"])
        fixes = [step["fix"] for step in result["steps"]]
        assert fixes == ["removed_prologue_comment", "removed_embedded_raster"]
        assert b"<image" not in result["bytes"] and b"#raster1" not in result["bytes"]
        assert result["visible_elements"] == 1 and result["excluded"] is None
        assert plan_module.scan_svg(result["bytes"]) == []

    def test_a_file_left_blank_is_excluded_not_shipped(self, plan):
        assert [item["pageid"] for item in plan["excluded"]] == [1006]
        assert "commons-1006" not in {symbol["slug"] for symbol in plan["symbols"]}

    def test_a_missing_viewbox_is_derived_from_width_and_height(self):
        result = plan_module.fix_svg(build_package()["svg"]["svg/commons-1002.svg"])
        assert [step["fix"] for step in result["steps"]] == ["added_viewbox"]
        assert b'viewBox="0 0 774 378"' in result["bytes"]

    def test_a_millimetre_size_becomes_css_pixels(self):
        raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="5mm"><path d="M0 0 H10"/></svg>'
        assert b'viewBox="0 0 37.795276 18.897638"' in plan_module.fix_svg(raw)["bytes"]

    def test_a_prologue_comment_is_removed_so_the_backend_accepts_the_file(self):
        raw = build_package()["svg"]["svg/commons-1004.svg"]
        with pytest.raises(UnsafeImageContentError):
            validate_stored_image(raw, "image/svg+xml")
        fixed = plan_module.fix_svg(raw)["bytes"]
        assert validate_stored_image(fixed, "image/svg+xml") == "image/svg+xml"
        assert fixed.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<svg')

    def test_each_step_chains_from_the_last(self):
        result = plan_module.fix_svg(build_package()["svg"]["svg/commons-1003.svg"])
        first, second = result["steps"]
        assert first["source_sha256"] == result["original_sha256"]
        assert first["derived_sha256"] == second["source_sha256"]
        assert second["derived_sha256"] == result["fixed_sha256"]

    def test_fixing_is_idempotent(self):
        once = plan_module.fix_svg(build_package()["svg"]["svg/commons-1003.svg"])["bytes"]
        assert plan_module.fix_svg(once)["steps"] == []

    def test_a_fixed_file_that_disagrees_with_the_fix_is_refused(self, package):
        package["fixed"]["commons-1002.svg"] += b"\n"
        problems = plan_module.plan_import(package)["package_problems"]
        assert any("is not what fix-svgs produces" in p for p in problems)

    @pytest.mark.parametrize(
        ("snippet", "finding"),
        [
            ("<script>alert(1)</script>", "element:script"),
            ('<foreignObject width="1" height="1"/>', "element:foreignObject"),
            ('<path d="M0 0" onclick="x()"/>', "event_attribute:onclick"),
            ('<use href="https://evil.test/a.svg#x"/>', "external_href:https://evil.test/a.svg#x"),
        ],
    )
    def test_the_safety_scan_refuses_what_the_harvester_refuses(self, package, snippet, finding):
        bad = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0 H10"/>{snippet}</svg>'.encode()
        package["svg"]["svg/commons-1007.svg"] = bad
        package["manifest"][-1]["sha256"] = hashlib.sha256(bad).hexdigest()
        package["manifest"][-1]["size_bytes"] = len(bad)
        problems = plan_module.plan_import(package)["package_problems"]
        assert any(finding in p for p in problems), problems

    def test_an_entity_declaration_is_refused(self):
        assert plan_module.scan_svg(b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>') == ["entity_declaration"]

    def test_fix_svgs_writes_only_into_svg_fixed(self, tmp_path):
        directory = write_package(build_package(), tmp_path / "package")
        before = {path for path in directory.rglob("*")}
        report = ingest.fix_svgs(ingest.load_package(directory), dry_run=False)
        created = {path for path in directory.rglob("*")} - before
        assert created and all(path.relative_to(directory).parts[0] == "svg-fixed" for path in created)
        assert report["written"] == 3 and [item["pageid"] for item in report["excluded"]] == [1006]
        assert json.loads((directory / "svg-fixed" / "fixes.json").read_text())["written"] == 3
        again = ingest.fix_svgs(ingest.load_package(directory), dry_run=False)
        assert again["written"] == 0 and again["already_present"] == 3
        assert plan_module.plan_import(ingest.load_package(directory))["package_problems"] == []


class TestConcepts:
    EXISTING = {
        "pump": {"concept_id": "00000000-0000-0000-0000-000000000001", "concept_code": "SGC-00000001"},
        "fire-damper": {"concept_id": "00000000-0000-0000-0000-000000000002", "concept_code": "SGC-00000002"},
    }

    def test_shared_new_keys_make_one_concept(self, plan):
        anchor = [c for c in plan["concepts"] if c["concept_key"] == "NEW:AnchorAgitator"]
        assert len(anchor) == 1 and anchor[0]["symbols"] == ["commons-1003", "commons-1004"]
        assert anchor[0]["aliases"] == ["Agitator, anchor type", "Agitator, anchor type (variant)"]

    def test_existing_keys_are_reused_and_near_misses_are_reported_not_merged(self, plan):
        matching = plan_module.match_concepts(plan["concepts"], self.EXISTING)
        assert [c["concept_key"] for c in matching["reuse"]] == ["Pump"]
        assert {"concept_key": "NEW:FireDamper", "existing_names": ["fire-damper"]} in matching["near_misses"]
        assert "NEW:FireDamper" in [c["concept_key"] for c in matching["create"]]
        assert matching["problems"] == []

    def test_an_existing_key_that_does_not_exist_is_a_problem(self, plan):
        matching = plan_module.match_concepts(plan["concepts"], {})
        assert matching["problems"] == ["concept key Pump does not exist"]

    def test_a_new_key_that_names_an_existing_concept_is_a_problem(self, plan):
        existing = {**self.EXISTING, "firedamper": {"concept_id": "00000000-0000-0000-0000-000000000003", "concept_code": "SGC-00000003"}}
        matching = plan_module.match_concepts(plan["concepts"], existing)
        assert any("NEW:FireDamper asks for a new concept" in p for p in matching["problems"])

    def test_a_concept_this_import_created_is_reused_on_a_rerun(self, plan):
        existing = {**self.EXISTING, "firedamper": {"concept_id": "00000000-0000-0000-0000-000000000003", "concept_code": "SGC-00000003"}}
        matching = plan_module.match_concepts(plan["concepts"], existing, own={"firedamper"})
        assert matching["problems"] == []
        assert "NEW:FireDamper" in [c["concept_key"] for c in matching["reuse"]]


class TestWhatIsRecorded:
    def test_the_rights_evidence_lists_every_file(self, plan):
        rights = plan["rights"]
        assert (rights["rights_status"], rights["disposition"]) == ("open", "distribute")
        files = rights["evidence"]["files"]
        assert [item["pageid"] for item in files] == [1001, 1002, 1003, 1004, 1007]
        for item in files:
            assert set(plan_module.required_evidence_keys()) <= set(item)
            assert item["retrieved_on"] == "2026-10-13" and len(item["commons_sha1"]) == 40
        assert rights["evidence"]["licence_families"] == {"cc0": 1, "pd": 4}
        assert rights["evidence"]["attribution_text"] and rights["evidence"]["attribution_is_placeholder"] is False

    def test_iso_10628_2_is_a_vendor_implementation_of_the_existing_record(self, plan):
        by_slug = {symbol["slug"]: symbol for symbol in plan["symbols"]}
        iso = by_slug["commons-1001"]["standards"]
        assert [(a["standard_code"], a["version_label"], a["relationship_type"]) for a in iso] == [
            ("ISO 10628", "2012", "vendor_implementation")
        ]
        assert all(a["relationship_type"] == "vendor_implementation" for s in plan["symbols"] for a in s["standards"])
        assert by_slug["commons-1002"]["standards"][0]["standard_code"] == "DIN EN 12792"
        assert by_slug["commons-1007"]["standards"] == []
        assert plan["summary"]["without_standard_link"] == ["commons-1007"]

    def test_only_the_dampers_are_fire_and_life_safety(self, plan):
        assert plan["summary"]["fire_dampers"] == ["commons-1002"]
        assert plan_module.is_fire_damper("Fire and smoke damper", "HVAC")
        assert plan_module.is_fire_damper("Smoke damper", "HVAC")
        assert not plan_module.is_fire_damper("Fire damper", "Piping / P&ID")
        assert not plan_module.is_fire_damper("Volume control damper", "HVAC")

    def test_the_payload_carries_the_commons_provenance(self, plan):
        symbol = next(s for s in plan["symbols"] if s["slug"] == "commons-1003")
        payload = plan_module.build_revision_payload(symbol, revision_label="r1")
        commons = payload["commons"]
        assert commons["author"] == "ChemSim" and commons["licence"] == "Public domain"
        assert commons["pageid"] == 1003 and commons["page_url"].startswith("https://commons.wikimedia.org/wiki/File:")
        assert commons["file_url"] == "https://upload.example.test/1003.svg"
        assert commons["fixes"] == ["removed_prologue_comment", "removed_embedded_raster"]
        asset = payload["assets"][0]
        assert asset["sha256"] == symbol["final_sha256"] != symbol["original_sha256"]
        assert asset["object_key"] == f"commons/WIKIMEDIA-COMMONS-PID-20261013/commons-1003/r1/primary-{symbol['final_sha256']}.svg"
        assert payload["aliases"] == ["Anchor type (ISO 10628-2)"]

    def test_a_rerun_with_the_same_plan_needs_no_new_revision(self, plan):
        symbol = plan["symbols"][0]
        payload = plan_module.build_revision_payload(symbol, revision_label="r1")
        assert plan_module.revision_needs_bump(payload, symbol) == []
        assert plan_module.revision_needs_bump({**payload, "name": "Other"}, symbol) == ["name changed"]


class TestDisciplineParity:
    def test_the_three_lists_agree(self):
        detail = commons_verify.discipline_parity()
        assert detail["agree"] is True
        assert "HVAC" in detail["catalog_facets"] and isinstance(detail["catalogWorkbench.js"], list)

    def test_a_drifted_frontend_list_is_caught(self, tmp_path):
        drifted = tmp_path / "catalogWorkbench.js"
        drifted.write_text("export const CATALOG_DISCIPLINE_ORDER = [\n  'Electrical',\n  'HVAC'\n];\n")
        assert commons_verify.discipline_parity(drifted)["agree"] is False
