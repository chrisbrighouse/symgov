"""Contract cover for SM-P0-09: the legacy field dual write and read.

DB-free. The derivation rules, the precedence between competing proposals,
and -- the property the whole staged rollout rests on -- that the catalogue
SQL with the flag off is *byte-identical* to what it was before this package,
are all provable without a server.

What needs a real one -- that promotion actually rewrites `door` to `Doors`,
that a column matching no node keeps its value, that the assignment-aware
filter executes and matches -- lives in
`test_legacy_classification_sync_postgresql.py`.

The two facts worth stating outright, because both are decisions rather than
readings of the specification:

* The derivation prefers a `verified` primary and falls back to a `proposed`
  one. `verified_primary_symbol_classification`'s SM-P0-04 docstring names
  itself as the source, but nothing in production can create a verified
  assignment, so verified-only would leave section 12.2 permanently inert.
* No refusal ever empties a column. Both are `nullable=False`, and
  `LegacyDerivation.changes` is False for every reason the derivation can
  give, which is what the parametrised test below asserts one reason at a
  time.
* A `legacy_taxonomy` match creates the assignment but does not drive the
  durable column. Measured against production first: without that rule, 63
  of 96 symbols had a column rewritten and about half were coarsenings
  (`Cylinder` -> `Equipment`) rather than normalisations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from symgov_backend.catalog_search import catalog_symbol_filters
from symgov_backend.classification_assignments import (
    CLASSIFICATION_ASSIGNMENT_METHODS,
    CLASSIFICATION_ASSIGNMENT_STATUSES,
    SYMBOL_CLASSIFICATION_ROLES,
)
from symgov_backend.classification_mapping import MAPPING_GAP_REASONS
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS
from symgov_backend.legacy_classification_sync import (
    DERIVABLE_STATUSES,
    NON_DISPLAY_MATCH_BASES,
    SHARED_WITH_MAPPING_GAP_REASONS,
    LEGACY_COLUMN_SCHEMES,
    METHOD_PREFERENCE,
    SYNC_SKIP_REASONS,
    LegacyDerivation,
    LegacySyncReport,
    _method_rank,
    _status_rank,
    may_drive_display_value,
)
from symgov_backend.rights_provenance import RIGHTS_DETERMINATION_METHODS
from symgov_backend.source_package_acquisition import PACKAGE_ACQUISITION_METHODS
from symgov_backend.standard_sources import STANDARD_VERIFICATION_METHODS
from symgov_backend.symbol_semantic_assignments import SEMANTIC_ASSIGNMENT_METHODS

NOW = datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)

# The exact strings this module built before SM-P0-09. Written out rather than
# generated, so the byte-identity claim is checked against a literal.
HISTORICAL_DISCIPLINE_FILTER = (
    "(gs.discipline ILIKE :discipline OR CAST(sr.payload_json AS TEXT) ILIKE :discipline)"
)
HISTORICAL_CATEGORY_FILTER = (
    "(gs.category ILIKE :category OR CAST(sr.payload_json AS TEXT) ILIKE :category)"
)

FILTER_KWARGS = dict(
    q=None,
    use_case=None,
    format_=None,
    pack=None,
    symbol_family=None,
    has_preview=None,
    updated_since=None,
)


# --- what derives from what --------------------------------------------------


def test_each_legacy_column_derives_from_the_scheme_it_was_mapped_into():
    assert LEGACY_COLUMN_SCHEMES == (
        ("category", "SYMBOL-CATEGORY-FAMILY"),
        ("discipline", "ENGINEERING-DISCIPLINE"),
    )
    assert len({scheme for _column, scheme in LEGACY_COLUMN_SCHEMES}) == 2


def test_only_an_open_assertion_can_drive_a_display_value():
    """A `rejected` or `retired` primary is a closed question. Deriving a
    catalogue label from one would show a value a reviewer has ruled out."""
    assert DERIVABLE_STATUSES == ("verified", "proposed")
    assert set(DERIVABLE_STATUSES) < CLASSIFICATION_ASSIGNMENT_STATUSES
    assert "rejected" not in DERIVABLE_STATUSES
    assert "retired" not in DERIVABLE_STATUSES


def test_verified_outranks_proposed():
    assert _status_rank("verified") < _status_rank("proposed")


def test_a_human_assertion_outranks_a_mapper_which_outranks_a_backfill():
    """The ordering that stops the displayed value depending on row order.
    A `legacy_backfill` row was copied out of the very column being derived,
    so it must lose to anything that looked at the symbol itself."""
    assert _method_rank("manual") < _method_rank("source_mapping")
    assert _method_rank("source_mapping") < _method_rank("legacy_backfill")
    assert _method_rank("rule") < _method_rank("ai_assisted")
    assert set(METHOD_PREFERENCE) == CLASSIFICATION_ASSIGNMENT_METHODS


def test_an_unknown_method_sorts_last_rather_than_raising():
    """The method vocabulary can grow without this module refusing to
    derive; a value it has never heard of simply loses to every value it
    has."""
    unknown = _method_rank("some_future_method")
    assert unknown == len(METHOD_PREFERENCE)
    assert all(_method_rank(method) < unknown for method in METHOD_PREFERENCE)


def test_the_derivation_only_ever_reads_a_primary():
    assert "primary" in SYMBOL_CLASSIFICATION_ROLES


# --- no refusal ever empties a column ----------------------------------------


def _derivation(**overrides) -> LegacyDerivation:
    values = dict(
        column="category",
        scheme_code="SYMBOL-CATEGORY-FAMILY",
        current_value="door",
        derived_value=None,
    )
    values.update(overrides)
    return LegacyDerivation(**values)


@pytest.mark.parametrize("reason", sorted(SYNC_SKIP_REASONS))
def test_no_reason_the_derivation_can_give_ever_empties_the_column(reason):
    """`GovernedSymbol.category` and `.discipline` are both
    `nullable=False`, so "blank it" was never an available answer. This is
    the property stated one reason at a time."""
    derivation = _derivation(reason=reason)
    if reason == "value_unchanged":
        derivation = _derivation(reason=reason, derived_value="door")
    assert derivation.changes is False


def test_a_derived_value_equal_to_the_current_one_is_not_a_change():
    derivation = _derivation(derived_value="door", reason="value_unchanged")
    assert derivation.derived_value == derivation.current_value
    assert derivation.changes is False


def test_a_different_derived_value_is_a_change():
    derivation = _derivation(
        derived_value="Doors",
        assignment_id=uuid.uuid4(),
        assignment_status="proposed",
        assignment_method="source_mapping",
    )
    assert derivation.changes is True
    payload = derivation.as_dict()
    assert payload["current_value"] == "door"
    assert payload["derived_value"] == "Doors"
    assert payload["changes"] is True
    assert payload["assignment_status"] == "proposed"


def test_the_report_names_only_the_columns_that_moved():
    report = LegacySyncReport(
        symbol_revision_id=uuid.uuid4(),
        derivations=(
            _derivation(derived_value="Doors", assignment_status="proposed"),
            _derivation(column="discipline", current_value="Process", derived_value="Process"),
        ),
        applied=True,
    )
    assert report.changed_columns == ("category",)
    payload = report.as_dict()
    assert payload["changed_columns"] == ["category"]
    assert len(payload["derivations"]) == 2


def test_the_skip_reasons_are_their_own_diagnostic_vocabulary():
    """Disjoint from every method vocabulary, and sharing exactly one value
    with the mapping gap reasons. `no_scheme` means the same thing in both
    places -- the scheme is not seeded -- so it is shared deliberately
    rather than renamed; anything else appearing in both would make a sync
    refusal indistinguishable from a mapping gap in a joined report."""
    assert SYNC_SKIP_REASONS & MAPPING_GAP_REASONS == SHARED_WITH_MAPPING_GAP_REASONS
    for vocabulary in (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
        RIGHTS_DETERMINATION_METHODS,
    ):
        assert SYNC_SKIP_REASONS != vocabulary
        assert not (SYNC_SKIP_REASONS & vocabulary)


# --- M5: the catalogue read, and the flag ------------------------------------


def test_with_the_flag_off_the_catalogue_sql_is_byte_identical_to_before():
    """The whole point of section 12.1 M5's staged rollout. If this fails,
    turning the flag off is no longer a way back."""
    filters, params, response_filters = catalog_symbol_filters(
        discipline="Process", category="Doors", assignments_enabled=False, **FILTER_KWARGS
    )
    assert filters == [HISTORICAL_DISCIPLINE_FILTER, HISTORICAL_CATEGORY_FILTER]
    assert params == {"discipline": "%Process%", "category": "%Doors%"}
    assert response_filters == {"discipline": "Process", "category": "Doors"}


def test_with_the_flag_on_the_legacy_match_is_kept_and_the_assignment_added():
    """Section 10.1 keeps the text search as the compatibility path, so the
    assignment match is an `OR`, never a replacement."""
    filters, params, _ = catalog_symbol_filters(
        discipline="Process", category="Doors", assignments_enabled=True, **FILTER_KWARGS
    )
    discipline_filter, category_filter = filters
    for clause, historical in (
        (discipline_filter, HISTORICAL_DISCIPLINE_FILTER),
        (category_filter, HISTORICAL_CATEGORY_FILTER),
    ):
        # The historical clause is still there in full, minus its closing
        # bracket, with the assignment match appended inside it.
        assert clause.startswith(historical[:-1])
        assert "OR EXISTS (" in clause
        assert clause.endswith(")")
    assert "cs.scheme_code = :discipline_scheme" in discipline_filter
    assert "cs.scheme_code = :category_scheme" in category_filter
    assert params["discipline_scheme"] == "ENGINEERING-DISCIPLINE"
    assert params["category_scheme"] == "SYMBOL-CATEGORY-FAMILY"


def test_the_scheme_code_is_bound_not_interpolated():
    """A scheme code reaching the SQL as a literal would be a string built
    into a query. It is a bound parameter in both clauses."""
    filters, _params, _ = catalog_symbol_filters(
        discipline="Process", category="Doors", assignments_enabled=True, **FILTER_KWARGS
    )
    for clause in filters:
        assert "ENGINEERING-DISCIPLINE" not in clause
        assert "SYMBOL-CATEGORY-FAMILY" not in clause


def test_the_assignment_match_reads_only_open_primaries():
    filters, _params, _ = catalog_symbol_filters(
        category="Doors", discipline=None, assignments_enabled=True, **FILTER_KWARGS
    )
    clause = filters[0]
    assert "src.assignment_role = 'primary'" in clause
    assert "src.status IN ('verified', 'proposed')" in clause
    assert "src.symbol_revision_id = sr.id" in clause


def test_the_assignment_match_adds_no_table_to_the_from_chain():
    """Section 14.2: private symbol existence must not leak. The match is an
    `EXISTS` correlated on `sr.id` inside the existing `WHERE`, so it can
    only narrow or widen matches *among* the rows the surrounding query
    already admits -- it cannot reach a symbol the public projection
    excludes."""
    filters, _params, _ = catalog_symbol_filters(
        category="Doors", discipline=None, assignments_enabled=True, **FILTER_KWARGS
    )
    clause = filters[0]
    assert clause.lstrip().startswith("(gs.category")
    assert "EXISTS (" in clause
    assert "governed_symbols" not in clause
    assert "active_public_symbol_projections" not in clause


def test_a_facet_that_is_not_filtered_binds_no_scheme_parameter():
    _filters, params, _ = catalog_symbol_filters(
        discipline=None, category=None, assignments_enabled=True, **FILTER_KWARGS
    )
    assert "discipline_scheme" not in params
    assert "category_scheme" not in params


def test_the_flag_defaults_off():
    """Section 12.1 M5 asks for a staged rollout, and the established
    pattern in `settings.py` is an off-by-default `SYMGOV_*_ENABLED`."""
    from symgov_backend.settings import SymgovAPISettings

    assert SymgovAPISettings().catalog_classification_assignments_enabled is False


def test_the_flag_reads_the_documented_environment_variable():
    """Pinned from the source, not by monkeypatching the environment: every
    flag in `settings.py` evaluates `os.environ.get` at class-definition
    time, so all eight need a process restart to change and none of them can
    be flipped inside a test. That is the existing convention, and this
    package follows it rather than inventing a second one."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "backend/symgov_backend/settings.py"
    ).read_text()
    assert "SYMGOV_CATALOG_CLASSIFICATION_ASSIGNMENTS_ENABLED" in source
    assert source.count("catalog_classification_assignments_enabled") == 1
    # The same truthy vocabulary the other flags accept.
    index = source.index("SYMGOV_CATALOG_CLASSIFICATION_ASSIGNMENTS_ENABLED")
    assert '{"1", "true", "yes", "on"}' in source[index : index + 200]


def test_the_other_filters_are_untouched_by_this_package():
    """The duplicate hint in `promotion_requests` and the remaining
    payload-JSON filters are section 10.2's weak legacy signals, to be
    downgraded when concept-aware signals arrive in SM-P1-05."""
    filters, _params, _ = catalog_symbol_filters(
        discipline=None,
        category=None,
        q=None,
        use_case="Insert into CAD drawing",
        format_="SVG",
        pack=None,
        symbol_family=None,
        has_preview=None,
        updated_since=None,
        assignments_enabled=True,
    )
    assert filters == [
        "CAST(sr.payload_json AS TEXT) ILIKE :use_case",
        "CAST(sr.payload_json AS TEXT) ILIKE :format",
    ]


def test_the_sync_is_wired_into_both_promotion_paths():
    """Section 12.1 M4 names "new/updated symbols", and promotion has two
    paths: the single symbol and the raster-split child. Read out of the
    source rather than asserted from memory."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend/symgov_backend/publication_handoff.py"
    ).read_text()
    assert source.count("\n    record_legacy_classification_sync(\n") == 2
    # After the mapping, because it derives from what the mapping proposes.
    assert source.index("record_classification_mapping(\n        session") < source.index(
        "record_legacy_classification_sync(\n        session"
    )


# --- a coarse match may assign but not display -------------------------------


class _FakeAssignment:
    """Just the two fields `may_drive_display_value` reads."""

    def __init__(self, evidence):
        self.evidence_json = evidence


def test_only_the_legacy_taxonomy_basis_is_barred_from_the_column():
    assert NON_DISPLAY_MATCH_BASES == {"legacy_taxonomy"}
    assert may_drive_display_value(_FakeAssignment({"match_basis": "legacy_taxonomy"})) is False
    assert may_drive_display_value(_FakeAssignment({"match_basis": "exact"})) is True
    assert may_drive_display_value(_FakeAssignment({"match_basis": "plural_variant"})) is True


def test_an_assignment_with_no_recorded_basis_may_still_display():
    """A deny-list, not an allow-list, and this is why: a reviewer's own
    choice through a future review UI carries no `match_basis` at all, and it
    is the most authoritative assertion there is. It must not be excluded by
    a rule aimed at a coarse machine match."""
    for evidence in ({}, None, {"source": "manual_review"}, "not-a-dict"):
        assert may_drive_display_value(_FakeAssignment(evidence)) is True


def test_the_barred_basis_is_one_the_mapper_can_actually_produce():
    """Guards against the deny-list naming a basis that no longer exists,
    which would silently stop barring anything."""
    from symgov_backend.classification_mapping import MATCH_BASES

    assert NON_DISPLAY_MATCH_BASES < MATCH_BASES


def test_the_coarse_refusal_has_its_own_reason():
    """Distinct from `no_derivable_assignment`, so a report can tell "nothing
    classifies this symbol" from "the classification was too coarse to
    show"."""
    assert "coarse_match_basis" in SYNC_SKIP_REASONS
    assert "coarse_match_basis" not in MAPPING_GAP_REASONS
    derivation = _derivation(reason="coarse_match_basis")
    assert derivation.changes is False
    assert derivation.as_dict()["current_value"] == "door"
