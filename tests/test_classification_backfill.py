"""Contract cover for SM-P0-10: the legacy classification backfill.

DB-free, like its SM-P0-07 counterpart and for the same reason: the rules
that decide what a legacy `category`/`discipline` value means are a pure
function of that value, so every one of them is provable here. What needs a
real server -- that the sweep writes `proposed`/`legacy_backfill` rows, that
such a row refuses verification, that a rerun adds nothing, that a revision
already carrying a primary is left alone -- lives in
`test_classification_backfill_postgresql.py`.

Two things this file pins that are easy to lose.

The **third matching rule** added here reads the catalogue's own
`_DISCIPLINE_MAP`/`_CATEGORY_MAP`. It closes forty short forms the exact and
trailing-S rules cannot reach, and it is facet-specific: `hvac` is the
discipline `HVAC` and the category `Heating / HVAC`. A rule that ignored the
scheme would classify heating symbols as an engineering discipline.

**`plan_facet` is shared** between promotion and the backfill. SM-P0-09
derives the legacy column back from whichever of the two wrote the
assignment, so the moment they disagree about a value the derived column
starts depending on a symbol's history instead of its content. The test
below compares the two paths on the same input rather than trusting the
docstring.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from symgov_backend.automation_policy import (
    PLACEHOLDER_CATEGORIES,
    PLACEHOLDER_DISCIPLINES,
)
from symgov_backend.catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    LEGACY_TAXONOMY_FACETS,
    legacy_taxonomy_labels,
)
from symgov_backend.classification_assignments import (
    BACKFILL_METHODS,
    CLASSIFICATION_ASSIGNMENT_METHODS,
    SYMBOL_CLASSIFICATION_ROLES,
)
from symgov_backend.classification_backfill import (
    BACKFILL_METHOD,
    BACKFILL_SKIP_REASONS,
    BACKFILLER_VERSION,
    LEGACY_FACETS,
    BackfillReport,
    BackfillTarget,
    PlannedBackfill,
    SkippedBackfill,
)
from symgov_backend.classification_mapping import (
    MAPPING_GAP_REASONS,
    MAPPING_METHOD,
    MATCH_BASES,
    ClassificationFields,
    MappingGap,
    PlannedAssignment,
    _candidate_node_codes,
    plan_classification_mapping,
    plan_facet,
)
from symgov_backend.classification_schemes import derive_classification_node_code
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.rights_provenance import RIGHTS_DETERMINATION_METHODS
from symgov_backend.source_package_acquisition import PACKAGE_ACQUISITION_METHODS
from symgov_backend.standard_sources import STANDARD_VERIFICATION_METHODS
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"
CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"
NOW = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)


def _seeded_codes(labels: list[str]) -> dict[str, str]:
    return {derive_classification_node_code(label): label for label in labels}


DISCIPLINE_NODES = _seeded_codes(CATALOG_DISCIPLINE_ORDER)
CATEGORY_NODES = _seeded_codes(CATALOG_CATEGORY_ORDER)


def _first_seeded_match(value: str, *, scheme_code: str) -> tuple[str, str] | None:
    """Resolve a value the way `resolve_node` would against the seeded nodes."""
    nodes = DISCIPLINE_NODES if scheme_code == DISCIPLINE_SCHEME else CATEGORY_NODES
    for code, basis in _candidate_node_codes(value, scheme_code=scheme_code):
        if code in nodes:
            return nodes[code], basis
    return None


# --- the three matching rules ------------------------------------------------


def test_a_seeded_label_matches_itself_exactly():
    """The seeded nodes *are* the hard-coded catalogue lists, so a value
    already holding a seeded label derives back byte-identical. This is what
    makes SM-P0-09's dual write a no-op for a tidy symbol."""
    for scheme_code, labels in (
        (DISCIPLINE_SCHEME, CATALOG_DISCIPLINE_ORDER),
        (CATEGORY_SCHEME, CATALOG_CATEGORY_ORDER),
    ):
        for label in labels:
            match = _first_seeded_match(label, scheme_code=scheme_code)
            assert match == (label, "exact"), label


def test_the_trailing_s_rule_is_one_character_in_either_direction():
    assert _first_seeded_match("door", scheme_code=CATEGORY_SCHEME) == ("Doors", "plural_variant")
    assert _first_seeded_match("valve", scheme_code=CATEGORY_SCHEME) == ("Valves", "plural_variant")
    # And the other way: a plural raw value against a singular seeded label.
    assert _candidate_node_codes("lightings", scheme_code=CATEGORY_SCHEME)[1] == (
        "LIGHTING",
        "plural_variant",
    )


def test_the_legacy_taxonomy_rule_reaches_what_the_first_two_cannot():
    """`PIPING` is not `PIPING_P_ID`, so no amount of case folding or
    pluralising gets `piping` to the seeded "Piping / P&ID" node. The
    catalogue's own table already knows the answer."""
    assert _first_seeded_match("piping", scheme_code=DISCIPLINE_SCHEME) == (
        "Piping / P&ID",
        "legacy_taxonomy",
    )
    assert _first_seeded_match("vessel", scheme_code=CATEGORY_SCHEME) == (
        "Vessels / Tanks",
        "legacy_taxonomy",
    )
    assert _first_seeded_match("motor", scheme_code=CATEGORY_SCHEME) == (
        "Motors / Drives",
        "legacy_taxonomy",
    )


def test_the_legacy_taxonomy_rule_is_facet_specific():
    """`hvac` means two different things. A rule that ignored the scheme
    would file heating symbols under an engineering discipline."""
    assert _first_seeded_match("hvac", scheme_code=DISCIPLINE_SCHEME) == ("HVAC", "exact")
    assert _first_seeded_match("hvac", scheme_code=CATEGORY_SCHEME) == (
        "Heating / HVAC",
        "legacy_taxonomy",
    )


def test_a_multi_label_legacy_entry_keeps_the_browse_taxonomy_order():
    """`fire_alarm` is Fire & Life Safety first, Electrical second, which is
    the order the catalogue already applies. Both become candidates; the
    first is the one a primary assignment lands on."""
    codes = _candidate_node_codes("fire_alarm", scheme_code=DISCIPLINE_SCHEME)
    legacy = [code for code, basis in codes if basis == "legacy_taxonomy"]
    assert legacy == ["FIRE_LIFE_SAFETY", "ELECTRICAL"]
    assert _first_seeded_match("fire_alarm", scheme_code=DISCIPLINE_SCHEME) == (
        "Fire & Life Safety",
        "legacy_taxonomy",
    )


def test_the_rules_are_tried_in_order_and_the_codes_de_duplicate():
    """For `valve` the legacy table agrees with the trailing-S rule, so the
    candidate list is unchanged from before this package -- the third rule
    only ever appends codes the first two did not produce."""
    assert _candidate_node_codes("valve", scheme_code=CATEGORY_SCHEME) == (
        ("VALVE", "exact"),
        ("VALVES", "plural_variant"),
    )
    assert _candidate_node_codes("Valves", scheme_code=CATEGORY_SCHEME) == (
        ("VALVES", "exact"),
        ("VALVE", "plural_variant"),
    )


def test_every_recognised_legacy_value_now_resolves():
    """The measurement this package turns on: before the third rule, forty of
    the short forms the browse taxonomy understands matched no seeded node,
    so a backfill would have left them with no assignment at all. None do
    now, placeholders aside."""
    unresolved = []
    for facet, scheme_code, placeholders in (
        ("discipline", DISCIPLINE_SCHEME, PLACEHOLDER_DISCIPLINES),
        ("category", CATEGORY_SCHEME, PLACEHOLDER_CATEGORIES),
    ):
        for raw in _recognised_raw_values(facet):
            if raw.strip().casefold() in placeholders:
                continue
            if _first_seeded_match(raw, scheme_code=scheme_code) is None:
                unresolved.append((facet, raw))
    assert unresolved == []


def _recognised_raw_values(facet: str) -> list[str]:
    """Every short form `legacy_taxonomy_labels` has an answer for."""
    from symgov_backend.catalog_taxonomy import _LEGACY_FACET_MAPS

    return sorted(key for key in _LEGACY_FACET_MAPS[facet] if key)


def test_an_unknown_facet_is_refused_rather_than_silently_empty():
    assert legacy_taxonomy_labels("category", "not-a-known-short-form") == ()
    with pytest.raises(ValueError, match="unknown legacy taxonomy facet"):
        legacy_taxonomy_labels("industry", "chemical")
    assert set(LEGACY_TAXONOMY_FACETS) == {"discipline", "category"}


def test_a_value_that_yields_no_code_at_all_yields_no_candidates():
    assert _candidate_node_codes("///", scheme_code=CATEGORY_SCHEME) == ()


# --- one rule surface, shared with promotion ---------------------------------


def test_promotion_and_the_backfill_plan_the_same_candidates():
    """The property SM-P0-09 depends on. Both paths call `plan_facet`, so
    this compares the promotion planner's output against a direct call for
    the same value -- if the two ever diverge, the legacy column SM-P0-09
    derives would depend on how the symbol reached the catalogue."""
    for raw_category in ("door", "Valves", "vessel", "hvac"):
        promotion = {
            assignment.field: assignment
            for assignment in plan_classification_mapping(
                ClassificationFields(category=raw_category)
            ).assignments
        }["category"]
        backfill = plan_facet(
            field="category",
            value=raw_category,
            scheme_code=CATEGORY_SCHEME,
            assignment_role="primary",
            placeholders=PLACEHOLDER_CATEGORIES,
        )
        assert isinstance(backfill, PlannedAssignment)
        assert backfill.candidate_node_codes == promotion.candidate_node_codes
        assert backfill.candidate_match_bases == promotion.candidate_match_bases


def test_the_candidate_tuples_stay_parallel():
    planned = plan_facet(
        field="discipline",
        value="piping",
        scheme_code=DISCIPLINE_SCHEME,
        assignment_role="primary",
        placeholders=PLACEHOLDER_DISCIPLINES,
    )
    assert isinstance(planned, PlannedAssignment)
    assert len(planned.candidate_node_codes) == len(planned.candidate_match_bases)
    assert planned.candidates == (
        ("PIPING", "exact"),
        ("PIPINGS", "plural_variant"),
        ("PIPING_P_ID", "legacy_taxonomy"),
    )


def test_plan_facet_reports_the_three_reasons_it_can_refuse():
    empty = plan_facet(
        field="category",
        value=None,
        scheme_code=CATEGORY_SCHEME,
        assignment_role="primary",
        placeholders=PLACEHOLDER_CATEGORIES,
    )
    assert isinstance(empty, MappingGap) and empty.reason == "no_value"

    unusable = plan_facet(
        field="category",
        value="///",
        scheme_code=CATEGORY_SCHEME,
        assignment_role="primary",
        placeholders=PLACEHOLDER_CATEGORIES,
    )
    assert isinstance(unusable, MappingGap) and unusable.reason == "no_node_match"


def test_the_placeholder_guard_is_the_only_thing_holding_the_fallbacks_back():
    """`publication_handoff` falls back to category `symbol` and discipline
    `general` when a review has no classification record
    (`publication_handoff.py:452,457` and `:718,719`). Before the third
    matching rule, two independent barriers kept those out of the structured
    model: they are placeholder values, *and* they matched no seeded node.

    The legacy taxonomy table has an answer for both -- `symbol` is
    "Drawing Symbols", `general` is "General / Annotation" -- so the second
    barrier is gone and the placeholder guard is now load-bearing on its
    own. It has to stay: a fallback is a value nobody asserted, and
    deriving a catalogue label from one would be SM-P0-09 inventing a
    classification, which is exactly what section 12.3 forbids.
    """
    for value, scheme_code, placeholders, would_match in (
        ("symbol", CATEGORY_SCHEME, PLACEHOLDER_CATEGORIES, "Drawing Symbols"),
        ("general", DISCIPLINE_SCHEME, PLACEHOLDER_DISCIPLINES, "General / Annotation"),
    ):
        planned = plan_facet(
            field="category",
            value=value,
            scheme_code=scheme_code,
            assignment_role="primary",
            placeholders=placeholders,
        )
        assert isinstance(planned, MappingGap)
        assert planned.reason == "placeholder_value"
        # The guard, not the absence of a node, is what refused it.
        assert _first_seeded_match(value, scheme_code=scheme_code) == (
            would_match,
            "legacy_taxonomy",
        )


def test_every_promotion_fallback_value_is_a_placeholder():
    """Reading the fallbacks out of the handoff source rather than repeating
    them, so a change to either one fails here."""
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend/symgov_backend/publication_handoff.py"
    ).read_text()
    assert 'fallback="symbol"' in source
    assert 'fallback="general"' in source
    assert "symbol" in PLACEHOLDER_CATEGORIES
    assert "general" in PLACEHOLDER_DISCIPLINES


# --- the backfill's own contract ---------------------------------------------


def test_the_backfill_writes_exactly_one_method_and_it_is_unverifiable():
    """Section 12.3. The method is taken from `BACKFILL_METHODS`, the same
    frozenset the `backfill_not_verified` check constraint mirrors, so the
    module cannot drift from the constraint."""
    assert BACKFILL_METHOD == "legacy_backfill"
    assert BACKFILL_METHODS == {BACKFILL_METHOD}
    assert BACKFILL_METHOD in CLASSIFICATION_ASSIGNMENT_METHODS
    assert BACKFILL_METHOD != MAPPING_METHOD


def test_both_legacy_columns_are_backfilled_as_primary_in_their_own_scheme():
    """One-verified-primary is per scheme, so a discipline primary and a
    category primary on the same revision do not compete."""
    assert [facet["field"] for facet in LEGACY_FACETS] == ["discipline", "category"]
    assert [facet["scheme_code"] for facet in LEGACY_FACETS] == [
        DISCIPLINE_SCHEME,
        CATEGORY_SCHEME,
    ]
    assert len({facet["scheme_code"] for facet in LEGACY_FACETS}) == len(LEGACY_FACETS)
    assert "primary" in SYMBOL_CLASSIFICATION_ROLES


def test_the_skip_reasons_are_not_a_new_method_vocabulary():
    """Seven method vocabularies exist and are deliberately not unified.
    These skip reasons are diagnostics: they never reach a `method` column,
    they share no value with any of them, and they live inside the mapping
    gap vocabulary rather than starting a rival one."""
    for vocabulary in (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
        RIGHTS_DETERMINATION_METHODS,
    ):
        assert BACKFILL_SKIP_REASONS != vocabulary
        assert not (BACKFILL_SKIP_REASONS & vocabulary)
    assert BACKFILL_SKIP_REASONS <= MAPPING_GAP_REASONS
    assert "already_assigned" in MAPPING_GAP_REASONS


def test_the_match_bases_are_not_a_new_method_vocabulary_either():
    for vocabulary in (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
        RIGHTS_DETERMINATION_METHODS,
    ):
        assert not (MATCH_BASES & vocabulary)
    assert MATCH_BASES == {"exact", "plural_variant", "legacy_taxonomy"}


def test_every_basis_the_rules_can_produce_is_in_the_vocabulary():
    for facet, scheme_code in (("discipline", DISCIPLINE_SCHEME), ("category", CATEGORY_SCHEME)):
        for raw in _recognised_raw_values(facet):
            for _code, basis in _candidate_node_codes(raw, scheme_code=scheme_code):
                assert basis in MATCH_BASES


# --- what the report says ----------------------------------------------------


def _planned(raw_value: str, node_label: str, basis: str = "exact") -> PlannedBackfill:
    return PlannedBackfill(
        symbol_revision_id=uuid.uuid4(),
        slug="sym-1",
        field="category",
        raw_value=raw_value,
        scheme_code=CATEGORY_SCHEME,
        node_code=derive_classification_node_code(node_label),
        node_label=node_label,
        match_basis=basis,
        classification_node_id=uuid.uuid4(),
        reused=False,
    )


def test_a_rewrite_is_reported_as_one_and_an_exact_match_is_not():
    """"Which catalogue values visibly change" has to be answerable from the
    dry run, before anything is written."""
    assert _planned("Doors", "Doors").rewrites_legacy_value is False
    assert _planned("door", "Doors", "plural_variant").rewrites_legacy_value is True
    assert _planned("piping", "Piping / P&ID", "legacy_taxonomy").rewrites_legacy_value is True


def test_the_report_counts_rewrites_and_skips_separately():
    report = BackfillReport(applied=False, targets_examined=3)
    report.planned.append(_planned("Doors", "Doors"))
    report.planned.append(_planned("door", "Doors", "plural_variant"))
    report.skipped.append(
        SkippedBackfill(
            symbol_revision_id=uuid.uuid4(),
            slug="sym-2",
            field="category",
            raw_value="symbol",
            reason="placeholder_value",
        )
    )
    report.skipped.append(
        SkippedBackfill(
            symbol_revision_id=uuid.uuid4(),
            slug="sym-3",
            field="category",
            raw_value="Pumps",
            reason="already_assigned",
        )
    )
    payload = report.as_dict()
    assert payload["applied"] is False
    assert payload["assignments_planned"] == 2
    assert payload["assignments_written"] == 0
    assert payload["legacy_value_rewrites"] == 1
    assert payload["skip_counts"] == {"placeholder_value": 1, "already_assigned": 1}
    assert payload["method"] == BACKFILL_METHOD
    assert payload["backfiller_version"] == BACKFILLER_VERSION


def test_a_dry_run_report_never_claims_a_write():
    report = BackfillReport(applied=False)
    report.planned.append(_planned("door", "Doors", "plural_variant"))
    assert report.as_dict()["assignments_written"] == 0


def test_a_target_reads_the_column_its_facet_names():
    target = BackfillTarget(
        symbol_id=uuid.uuid4(),
        symbol_revision_id=uuid.uuid4(),
        slug="sym-4",
        category="door",
        discipline="piping",
    )
    assert [target.legacy_value(facet["column"]) for facet in LEGACY_FACETS] == [
        "piping",
        "door",
    ]


# --- the operator-facing utility ---------------------------------------------
#
# Section 15.1 calls SM-P0-10 "migration/backfill *utilities*", so the sweep
# has to be runnable by an operator rather than only by a test. These cover
# the command wiring DB-free, following `test_manage_catalog_api_keys.py`:
# whether a dry run commits is the property that matters, and it is decided in
# `manage_symgov`, not in the sweep.


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _install(monkeypatch, *, report=None, error=None):
    import manage_symgov

    session = _FakeSession()
    monkeypatch.setattr(manage_symgov, "create_session_factory", lambda **_: lambda: session)
    calls = []

    def sweep(passed_session, **kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return report

    monkeypatch.setattr(manage_symgov, "run_legacy_classification_backfill", sweep)
    return manage_symgov, session, calls


def _report_with_one_rewrite() -> BackfillReport:
    report = BackfillReport(applied=True, targets_examined=1)
    report.planned.append(_planned("door", "Doors", "plural_variant"))
    report.written_assignment_ids.append(uuid.uuid4())
    return report


def test_the_command_is_registered_without_disturbing_the_others():
    import manage_symgov

    parser = manage_symgov.build_parser()
    command = next(action for action in parser._actions if action.dest == "command")
    assert "backfill-legacy-classifications" in command.choices
    assert {"backfill-tracy-libby-review-cases", "evaluate-automation-gates"} <= set(
        command.choices
    )


def test_a_dry_run_rolls_back_and_an_apply_commits(monkeypatch, capsys):
    import json

    manage_symgov, session, calls = _install(monkeypatch, report=_report_with_one_rewrite())

    assert manage_symgov.main(["backfill-legacy-classifications"]) == 0
    assert (session.commits, session.rollbacks) == (0, 1)
    assert calls[-1]["apply"] is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["legacy_value_rewrites"] == 1
    assert payload["planned"][0]["raw_value"] == "door"

    assert manage_symgov.main(["backfill-legacy-classifications", "--apply"]) == 0
    assert (session.commits, session.rollbacks) == (1, 1)
    assert calls[-1]["apply"] is True
    capsys.readouterr()


def test_the_limit_and_slug_reach_the_sweep(monkeypatch, capsys):
    manage_symgov, _session, calls = _install(monkeypatch, report=_report_with_one_rewrite())

    manage_symgov.main(
        ["backfill-legacy-classifications", "--limit", "25", "--symbol-slug", "sym-9"]
    )
    capsys.readouterr()
    assert calls[-1]["limit"] == 25
    assert calls[-1]["symbol_slug"] == "sym-9"


def test_summary_only_drops_the_per_row_detail(monkeypatch, capsys):
    import json

    manage_symgov, _session, _calls = _install(monkeypatch, report=_report_with_one_rewrite())

    manage_symgov.main(["backfill-legacy-classifications", "--summary-only"])
    payload = json.loads(capsys.readouterr().out)
    assert "planned" not in payload
    assert "skipped_detail" not in payload
    assert payload["legacy_value_rewrites"] == 1


def test_a_failed_sweep_rolls_back_and_exits_non_zero(monkeypatch, capsys):
    manage_symgov, session, _calls = _install(
        monkeypatch, error=RuntimeError("server went away")
    )

    assert manage_symgov.main(["backfill-legacy-classifications", "--apply"]) == 1
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed == 1
    assert "server went away" in capsys.readouterr().err
