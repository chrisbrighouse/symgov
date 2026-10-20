"""The QElectroTech import's pure plan and converter: no database."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from qet_fixtures import ELEMENTS, build_package, write_package  # noqa: E402

from symgov_backend import qet_ingest  # noqa: E402
from symgov_backend.image_content import validate_stored_image  # noqa: E402
from symgov_backend.services import qet_ingestion as plan_module  # noqa: E402
from symgov_backend.services.qet_elmt_svg import ElementDefinitionError, convert  # noqa: E402


@pytest.fixture
def package(tmp_path):
    return qet_ingest.load_package(write_package(build_package(), tmp_path))


def _plan(package):
    return plan_module.plan_import(package)


def test_the_fixture_package_plans_cleanly(package):
    plan = _plan(package)
    assert plan["package_problems"] == []
    assert [s["slug"] for s in plan["symbols"]] == [f"qet-{item['qet_uuid']}" for item in ELEMENTS]
    assert plan["summary"]["standard_links"] == {"EN 60617": 4}
    assert plan["standards"][0]["versions"] == ["edition not stated"]
    assert {c["concept_key"] for c in plan["concepts"]} == {"NEW:FuseGeneralSymbol", "NEW:TerminalStrip", "NEW:Lamp"}


def test_each_svg_is_converted_from_its_elmt_and_passes_the_stored_image_check(package):
    for symbol, result in zip(_plan(package)["symbols"], plan_module.conversions(package).values()):
        validate_stored_image(result["bytes"], "image/svg+xml")
        assert symbol["conversion"]["source_sha256"] == symbol["original_sha256"]
        assert symbol["conversion"]["derived_sha256"] == symbol["final_sha256"] == plan_module.sha256_hex(result["bytes"])


def test_a_variant_caption_is_removed_and_named_in_the_conversion_step(package):
    second = _plan(package)["symbols"][1]
    assert second["conversion"]["removed_labels"] == ["Form 2 / Forme 2"]
    assert b"Forme" not in plan_module.conversions(package)[second["qet_uuid"]]["bytes"]
    assert _plan(package)["summary"]["labels_removed"] == 1


def test_other_text_in_a_drawing_is_kept():
    data = (b'<definition type="element"><description><text x="0" y="0" text="N=5" font="Sans,4"/>'
            b'<text x="0" y="5" text="Form 1 / Forme 1" font="Sans,4"/></description></definition>')
    result = convert(data)
    assert result.texts == ["N=5"] and result.removed_labels == ["Form 1 / Forme 1"]


@pytest.mark.parametrize("data", [
    b'<!DOCTYPE x [<!ENTITY a "aaaa">]><definition/>',
    b"<svg/>",
    b"<definition",
])
def test_the_converter_refuses_what_is_not_a_plain_element_definition(data):
    with pytest.raises(ElementDefinitionError):
        convert(data)


def test_a_colour_attribute_cannot_inject_markup():
    data = (b'<definition type="element"><description>'
            b'<text x="0" y="0" text="A" color="#000&quot; onload=&quot;x" font="Sans,4"/></description></definition>')
    assert "onload" not in convert(data).svg


def test_the_payload_credits_the_elements_own_author_and_links_qelectrotech(package):
    symbols = _plan(package)["symbols"]
    payload = plan_module.build_revision_payload(symbols[3], revision_label="r1")
    assert payload["attribution"] == {
        "creator": "Ada Element",
        "text": plan_module.attribution_text("Ada Element"),
        "source_url": "https://qelectrotech.org",
    }
    assert "by Ada Element" in payload["attribution"]["text"]
    assert plan_module.build_revision_payload(symbols[2], revision_label="r1")["attribution"]["creator"] == "QElectroTech contributors"
    assert payload["qet"]["element_url"].endswith(f"/blob/{payload['qet']['commit']}/{ELEMENTS[3]['path']}")


def test_the_rights_plan_carries_qelectrotechs_template_and_conditions(package):
    rights = _plan(package)["rights"]
    assert (rights["rights_status"], rights["disposition"]) == ("open", "distribute")
    evidence = rights["evidence"]
    assert evidence["attribution_template"] == plan_module.ATTRIBUTION_TEMPLATE
    assert plan_module.CREATOR_PLACEHOLDER in evidence["attribution_template"]
    assert evidence["source_url"] == "https://qelectrotech.org"
    assert evidence["no_endorsement"] is True
    assert evidence["credits"] == {"Ada Element": 1, "QElectroTech contributors": 1, "The QElectroTech team": 2}


def test_an_exact_concept_name_reuses_the_existing_concept_and_a_near_one_is_only_reported(package):
    plan = _plan(package)
    existing = {"terminalstrip": {"concept_code": "C-1", "concept_id": "00000000-0000-0000-0000-000000000001"},
                "lamp ": {"concept_code": "C-2", "concept_id": "00000000-0000-0000-0000-000000000002"}}
    matched = plan_module.match_concepts(plan["concepts"], existing)
    assert [c["concept_key"] for c in matched["reuse"]] == ["NEW:TerminalStrip"]
    assert matched["problems"] == []
    assert matched["near_misses"] == [{"concept_key": "NEW:Lamp", "existing_names": ["lamp "]}]


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p["worksheet"][0].update(confirmed="no"), "not confirmed"),
    (lambda p: p["worksheet"][0].update(category="Valvez"), "is not a canonical category"),
    (lambda p: p["worksheet"][0].update(concept_key="FuseGeneralSymbol"), "is not NEW:<CamelCaseKey>"),
    (lambda p: p["manifest"]["elements"][0].update(licence_class="mit"), "is not CC BY 3.0"),
    (lambda p: p["manifest"]["elements"][0].update(en60617_ref=""), "no EN 60617 clause"),
    (lambda p: p["manifest"]["elements"][0].update(credit=""), "no author credit"),
    (lambda p: p["elmt"].update({p["manifest"]["elements"][0]["elmt_file"]: b"<definition/>"}), "does not match its recorded digest"),
])
def test_the_validator_refuses_what_it_cannot_honour(package, mutate, expected):
    changed = copy.deepcopy({key: value for key, value in package.items() if not key.startswith("_")})
    mutate(changed)
    problems = plan_module.validate_package(changed)
    assert any(expected in problem for problem in problems), problems


def test_a_changed_author_makes_a_new_revision(package):
    symbol = _plan(package)["symbols"][3]
    payload = plan_module.build_revision_payload(symbol, revision_label="r1")
    assert plan_module.revision_needs_bump(payload, symbol) == []
    payload["attribution"]["creator"] = "Someone Else"
    assert plan_module.revision_needs_bump(payload, symbol) == ["author changed"]


def test_nothing_in_the_plan_presents_qelectrotech_as_a_partner(package):
    from symgov_backend.qet_verify import ENDORSEMENT_WORDS

    plan = _plan(package)
    texts = [plan["publication"]["pack_title"], plan["source_package"]["title"], plan_module.ATTRIBUTION_TEMPLATE,
             plan["rights"]["decision_reason"].replace("no suggestion of partnership or endorsement", "")]
    for symbol in plan["symbols"]:
        payload = plan_module.build_revision_payload(symbol, revision_label="r1")
        texts += [payload["name"], payload["summary"]]
    assert not [t for t in texts if ENDORSEMENT_WORDS.search(t)]
