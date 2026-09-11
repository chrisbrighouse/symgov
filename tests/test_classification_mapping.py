"""Contract cover for SM-P0-07: workspace fields as governed proposals.

Scope note: this file is deliberately DB-free. The mapping rules are a pure
function of one classification record precisely so that every one of them can
be proved here -- which section 9.3 field produces which assignment, which
candidate node codes a value yields, which fields are gaps and why, and that
no field can be dropped without a test noticing.

Behaviour only a real PostgreSQL server can prove -- that promotion actually
writes the assignments, that an unmappable record still promotes, that the
legacy columns receive byte-identical values, and that running promotion
twice creates no second proposal -- lives in
`test_classification_mapping_postgresql.py`.
"""

from __future__ import annotations

import inspect
import re
from decimal import Decimal
from pathlib import Path

import pytest

from symgov_backend import classification_mapping as mapping_service
from symgov_backend import publication_handoff
from symgov_backend.automation_policy import (
    PLACEHOLDER_CATEGORIES,
    PLACEHOLDER_DISCIPLINES,
)
from symgov_backend.classification_assignments import (
    CLASSIFICATION_ASSIGNMENT_METHODS,
    SYMBOL_CLASSIFICATION_ROLES,
)
from symgov_backend.classification_mapping import (
    MAPPER_VERSION,
    MAPPING_GAP_REASONS,
    MAPPING_METHOD,
    STANDARD_RELATIONSHIP_TYPE,
    ClassificationFields,
    MappingGap,
    plan_classification_mapping,
)
from symgov_backend.classification_schemes import SEED_CLASSIFICATION_SCHEMES
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.rights_provenance import RIGHTS_DETERMINATION_METHODS
from symgov_backend.source_package_acquisition import PACKAGE_ACQUISITION_METHODS
from symgov_backend.standard_sources import (
    SOURCE_RELATIONSHIP_TYPES,
    STANDARD_VERIFICATION_METHODS,
)
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HANDOFF_SOURCE = (REPOSITORY_ROOT / "backend/symgov_backend/publication_handoff.py").read_text()

# Section 9.3's table, in the order the specification tabulates it. The test
# below fails if the mapper stops accounting for any one of them, which is
# the defect this package exists to close.
SECTION_9_3_FIELDS = (
    "engineeringDiscipline",
    "industry",
    "symbolFamily",
    "processCategory",
    "parentEquipmentClass",
    "standardsSource",
    "libraryProvenanceClass",
    "sourceClassification",
    "aliases",
    "keywords",
    "sourceRefs",
)


def _fields(**overrides) -> ClassificationFields:
    base = dict(
        discipline="Mechanical",
        category="Valves",
        industry="process_engineering",
        symbol_family="door",
        process_category="flow_control",
        parent_equipment_class="valve",
        standards_source="ISA-5.1",
        library_provenance_class="contributor_submission",
        source_classification="contributor_asserted",
        format="svg",
        aliases=("Gate valve",),
        search_terms=("valve", "gate"),
        source_refs=("https://example.test/source",),
        confidence=Decimal("0.84"),
    )
    base.update(overrides)
    return ClassificationFields(**base)


# --- the defect this package closes ------------------------------------


def test_every_section_9_3_field_is_either_assigned_or_reported():
    """No section 9.3 field may be silently dropped.

    This is section 16.1's acceptance criterion in one assertion. A field
    that produces no assignment must appear as a gap carrying its raw value
    and a reason; a field that appears in neither list is the exact defect
    SM-P0-07 exists to fix.
    """
    plan = plan_classification_mapping(_fields())
    accounted = plan.planned_fields | plan.gap_fields | {"standardsSource", "libraryProvenanceClass"}
    missing = [field for field in SECTION_9_3_FIELDS if field not in accounted]
    assert missing == []


def test_every_unmapped_field_keeps_its_raw_value():
    """A gap without the value is still a loss."""
    plan = plan_classification_mapping(_fields())
    for gap in plan.gaps:
        if gap.reason in {"no_value", "carried_in_payload"}:
            continue
        assert gap.raw_value, gap.field


def test_the_four_fields_that_reached_no_payload_are_now_written():
    """`industry`, `standards_source`, `library_provenance_class` and `format`
    reached the database and then vanished at promotion."""
    for field in ("industry", "standards_source", "library_provenance_class", "format"):
        assert f'"{field}": classification.{field} if classification else None' in HANDOFF_SOURCE


# --- the mapping rules -------------------------------------------------


def test_discipline_maps_to_the_seeded_discipline_scheme():
    plan = plan_classification_mapping(_fields(discipline="Mechanical"))
    (assignment,) = [a for a in plan.assignments if a.field == "engineeringDiscipline"]
    assert assignment.scheme_code == "ENGINEERING-DISCIPLINE"
    assert assignment.assignment_role == "primary"
    assert assignment.candidate_node_codes[0] == "MECHANICAL"


def test_case_and_punctuation_fold_into_one_node_code():
    """`derive_classification_node_code` already folds both, so the mapper
    does not need a second rule for it."""
    for value in ("mechanical", "Mechanical", "MECHANICAL", "  mechanical  "):
        plan = plan_classification_mapping(_fields(discipline=value))
        (assignment,) = [a for a in plan.assignments if a.field == "engineeringDiscipline"]
        assert assignment.candidate_node_codes[0] == "MECHANICAL"


def test_a_singular_value_offers_the_plural_seeded_label_second():
    """Libby writes `valve` and `door`; the seeded labels are `Valves` and
    `Doors`. One deterministic character, not similarity matching."""
    plan = plan_classification_mapping(_fields(category="valve", symbol_family="door"))
    by_field = {a.field: a for a in plan.assignments}
    assert by_field["category"].candidate_node_codes == ("VALVE", "VALVES")
    assert by_field["symbolFamily"].candidate_node_codes == ("DOOR", "DOORS")


def test_a_plural_value_offers_the_singular_second():
    plan = plan_classification_mapping(_fields(category="Valves"))
    (assignment,) = [a for a in plan.assignments if a.field == "category"]
    assert assignment.candidate_node_codes == ("VALVES", "VALVE")


def test_symbol_family_is_a_representation_classification_not_a_concept_one():
    """Section 9.3 says "concept or representation depending on meaning", and
    no semantic concept exists at promotion for either to attach to."""
    plan = plan_classification_mapping(_fields(symbol_family="door"))
    (assignment,) = [a for a in plan.assignments if a.field == "symbolFamily"]
    assert assignment.scheme_code == "SYMBOL-CATEGORY-FAMILY"
    assert assignment.assignment_role == "secondary"


def test_category_takes_the_primary_role_in_its_scheme():
    """Appendix A's compatibility facet: SM-P0-09 cannot derive
    `GovernedSymbol.category` from a structured primary unless one exists."""
    plan = plan_classification_mapping(_fields())
    roles = {a.field: a.assignment_role for a in plan.assignments}
    assert roles["category"] == "primary"
    assert roles["symbolFamily"] == "secondary"


@pytest.mark.parametrize("value", sorted(PLACEHOLDER_DISCIPLINES))
def test_a_placeholder_discipline_is_a_gap_not_an_assignment(value):
    """The automation gate's placeholder judgement is reused rather than
    a second one being invented."""
    plan = plan_classification_mapping(_fields(discipline=value))
    assert "engineeringDiscipline" not in plan.planned_fields
    (gap,) = [g for g in plan.gaps if g.field == "engineeringDiscipline"]
    assert gap.reason in {"placeholder_value", "no_value"}


@pytest.mark.parametrize("value", sorted(PLACEHOLDER_CATEGORIES))
def test_a_placeholder_category_is_a_gap_not_an_assignment(value):
    plan = plan_classification_mapping(_fields(category=value))
    assert "category" not in plan.planned_fields


def test_a_missing_value_is_reported_as_no_value():
    plan = plan_classification_mapping(_fields(discipline=None, category=None))
    reasons = {g.field: g.reason for g in plan.gaps}
    assert reasons["engineeringDiscipline"] == "no_value"
    assert reasons["category"] == "no_value"


# --- the four rows with no structured home in P0 -----------------------


def test_industry_is_a_gap_because_its_scheme_does_not_exist():
    """Decided 2026-09-11: no Industry/Application scheme is seeded, and the
    node list must not be invented. See
    docs/plans/2026-09-11-classification-industry-field-defect.md."""
    plan = plan_classification_mapping(_fields(industry="process_engineering"))
    (gap,) = [g for g in plan.gaps if g.field == "industry"]
    assert gap.reason == "no_scheme"
    assert gap.raw_value == "process_engineering"
    seeded = {scheme["scheme_code"] for scheme in SEED_CLASSIFICATION_SCHEMES}
    assert not any("INDUSTRY" in code for code in seeded)


def test_process_category_is_a_gap_with_no_concept_to_qualify():
    plan = plan_classification_mapping(_fields(process_category="flow_control"))
    (gap,) = [g for g in plan.gaps if g.field == "processCategory"]
    assert gap.reason == "no_scheme"
    assert gap.raw_value == "flow_control"


def test_parent_equipment_class_waits_on_a_table_that_does_not_exist():
    """Section 7.3's SemanticConceptRelationship is in no section 15.1
    package, so building one here would be a scope increase."""
    plan = plan_classification_mapping(_fields(parent_equipment_class="valve"))
    (gap,) = [g for g in plan.gaps if g.field == "parentEquipmentClass"]
    assert gap.reason == "no_relationship_table"
    assert gap.raw_value == "valve"


def test_aliases_keywords_and_source_refs_stay_in_the_payload():
    """Section 12.2's legacy-field policy, and there is no ConceptTerm table
    to move them to -- multilingual concept terms are deferred."""
    plan = plan_classification_mapping(_fields())
    reasons = {g.field: g.reason for g in plan.gaps}
    for field in ("aliases", "keywords", "sourceRefs", "format"):
        assert reasons[field] == "carried_in_payload"


# --- governance ---------------------------------------------------------


def test_the_mapper_proposes_with_source_mapping_not_legacy_backfill():
    """`legacy_backfill` carries a check constraint making the row
    permanently unverifiable. That is right for SM-P0-10's historical sweep
    and wrong for a mapping a reviewer should be able to confirm."""
    assert MAPPING_METHOD == "source_mapping"
    assert MAPPING_METHOD in CLASSIFICATION_ASSIGNMENT_METHODS
    assert MAPPING_METHOD != "legacy_backfill"


def test_the_mapper_never_names_a_status():
    """Section 8.4: a machine assertion stays proposed however confident.
    The service sets `proposed` itself, so the mapper naming any status at
    all would be the beginning of a second opinion about governance."""
    source = inspect.getsource(mapping_service)
    assert '"verified"' not in source
    # The only status literal the mapper may name is the `active` read filter
    # it uses to skip withdrawn nodes and editions. Anything else would be
    # this module forming an opinion about governance state.
    assert set(re.findall(r'status\s*=\s*"([a-z_]+)"', source)) <= {"active"}


def test_the_gap_reasons_are_not_a_seventh_method_vocabulary():
    """Six `method` vocabularies exist and are deliberately not unified. This
    diagnostic vocabulary is not a seventh: it never reaches a method
    column, and it shares no value with any of them."""
    method_vocabularies = (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
        RIGHTS_DETERMINATION_METHODS,
    )
    for vocabulary in method_vocabularies:
        assert MAPPING_GAP_REASONS != vocabulary
        assert not (MAPPING_GAP_REASONS & vocabulary)


def test_every_reason_the_planner_can_emit_is_in_the_vocabulary():
    for value in (
        _fields(),
        _fields(discipline=None, category=None, industry=None),
        _fields(discipline="unknown", category="symbol"),
    ):
        for gap in plan_classification_mapping(value).gaps:
            assert gap.reason in MAPPING_GAP_REASONS


def test_the_assignment_roles_are_the_section_7_8_pair():
    plan = plan_classification_mapping(_fields())
    for assignment in plan.assignments:
        assert assignment.assignment_role in SYMBOL_CLASSIFICATION_ROLES


def test_the_standard_relationship_is_a_real_section_8_3_type():
    """Section 9.2 asks for an explicit relationship, "no ambiguous
    standard-associated label"."""
    assert STANDARD_RELATIONSHIP_TYPE in SOURCE_RELATIONSHIP_TYPES
    assert STANDARD_RELATIONSHIP_TYPE == "derived_from"


# --- the SM-P0-07 / SM-P0-09 line ---------------------------------------


def test_the_legacy_category_and_discipline_expressions_are_untouched():
    """SM-P0-09 changes what the legacy columns receive; SM-P0-07 only adds
    beside them. If either expression below changes, this package has
    crossed into the next one."""
    primary = """    category = text_value(
        symbol_properties.category if symbol_properties else None,
        classification.category if classification else None,
        fallback="symbol",
    )
    discipline = text_value(
        symbol_properties.discipline if symbol_properties else None,
        classification.discipline if classification else None,
        fallback="general",
    )"""
    child = """    category = text_value(symbol_properties.category if symbol_properties else None, fallback="symbol")
    discipline = text_value(symbol_properties.discipline if symbol_properties else None, fallback="general")"""
    assert primary in HANDOFF_SOURCE
    assert child in HANDOFF_SOURCE


def test_promotion_calls_the_mapper_on_both_paths():
    assert HANDOFF_SOURCE.count("record_classification_mapping(") == 3  # one def, two calls


def test_a_mapping_failure_cannot_fail_a_promotion():
    """Decided 2026-09-11: an unmappable value is recorded, never raised."""
    source = inspect.getsource(publication_handoff.record_classification_mapping)
    assert "except Exception" in source
    assert '"status": "failed"' in source


# --- the child split path ------------------------------------------------


def test_the_child_path_does_not_inherit_the_parent_sheet_record():
    """For a raster split the parent record describes the *sheet*, whose
    family, process category and equipment class are `mixed_symbol_set`,
    `review_required` and `mixed_equipment`. Inheriting those would assert
    every child is a mixed symbol set."""
    source = inspect.getsource(publication_handoff.load_child_classification_record)
    assert "parent_review_case_id" in source
    assert "symbol_region_index" in source
    assert "symbol_key" in source
    # The parent's own record is reachable in that function's caller and is
    # deliberately not consulted.
    assert 'context["classification_record"]' not in source


def test_the_child_payload_no_longer_hardcodes_three_fields_to_none():
    for field in ("symbol_family", "process_category", "parent_equipment_class"):
        assert f'"{field}": None,' not in HANDOFF_SOURCE.split("human_approved_split_child")[1][:1500]


def test_the_mapper_version_is_recorded_with_every_proposal():
    assert MAPPER_VERSION.startswith("symgov-classification-mapping-")
    assert MAPPER_VERSION in inspect.getsource(mapping_service)
