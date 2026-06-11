from decimal import Decimal
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.runtime import (
    SCOTT_SOURCE_DISCOVERY_DEFAULT_SEED_QUERY,
    SCOTT_SOURCE_DISCOVERY_SITE_SEEDS,
)


def seed_by_domain():
    return {seed["domain"]: seed for seed in SCOTT_SOURCE_DISCOVERY_SITE_SEEDS}


def test_scott_default_seed_query_prioritizes_recommended_symbol_backbone():
    query = SCOTT_SOURCE_DISCOVERY_DEFAULT_SEED_QUERY

    assert "ProjectMaterials" in query
    assert "ISA-5.1" in query
    assert "ISO 14617" in query
    assert "IEC 60617" in query
    assert "QElectroTech" in query
    assert "commons.wikimedia.org" not in query


def test_scott_seed_sources_capture_authoritative_standards_backbone():
    seeds = seed_by_domain()

    assert seeds["webstore.iec.ch"]["evidence_json"]["recommended_use"] == "authoritative_taxonomy_backbone"
    assert "electrical" in seeds["webstore.iec.ch"]["evidence_json"]["applies_to"]
    assert seeds["isa.org"]["evidence_json"]["recommended_use"] == "authoritative_taxonomy_backbone"
    assert "ISO 14617" in seeds["iso.org"]["evidence_json"]["standards"]
    assert "ISO 1101" in seeds["iso.org"]["evidence_json"]["standards"]
    assert seeds["asme.org"]["evidence_json"]["recommended_use"] == "authoritative_taxonomy_backbone"


def test_scott_first_pass_sources_are_marked_for_next_run():
    seeds = seed_by_domain()

    for domain in [
        "projectmaterials.com",
        "vistaprojects.com",
        "qelectrotech.org",
        "necanet.org",
        "webstore.iec.ch",
        "isa.org",
        "iso.org",
        "asme.org",
        "keyence.com",
        "gdandtbasics.com",
    ]:
        assert seeds[domain]["status"] == "recommended"
        assert seeds[domain]["include_next_run"] is True
        assert seeds[domain]["relevance_score"] > Decimal("0.8800")


def test_scott_downloadable_cad_sources_are_reference_only_until_rights_checked():
    seeds = seed_by_domain()

    for domain in ["freecad.org", "traceparts.com"]:
        seed = seeds[domain]
        assert seed["status"] == "candidate"
        assert seed["include_next_run"] is False
        assert "reference/intake only" in seed["source_prompt"]
        assert "rights" in seed["evidence_json"]["rights_note"].lower()
        assert "provenance" in seed["evidence_json"]["rights_note"].lower()


def test_projectmaterials_is_immediate_seed_mapped_back_to_standards():
    seed = seed_by_domain()["projectmaterials.com"]

    assert seed["evidence_json"]["recommended_use"] == "immediate_seed_source"
    assert seed["evidence_json"]["authority_role"] == "candidate_source_only"
    assert seed["evidence_json"]["map_back_to"] == ["ISA-5.1", "ISO 14617"]
    assert "not the authority" in seed["source_prompt"]
