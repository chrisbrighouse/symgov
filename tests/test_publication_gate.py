"""SM-P0-08 contract tests: the section 9.2 publication gate, without a database.

`evaluate_publication_gate` and `derive_traceability_level` are pure functions
of `PublicationGateFacts`, so every rule the gate applies is provable here.
`tests/test_publication_gate_postgresql.py` proves that the facts are gathered
correctly from real rows and that the gate actually refuses a real promotion.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from symgov_backend.automation_policy import LOW_RISK_RIGHTS_STATUSES  # noqa: E402
from symgov_backend.classification_assignments import (  # noqa: E402
    CLASSIFICATION_ASSIGNMENT_METHODS,
)
from symgov_backend.classification_mapping import MAPPING_GAP_REASONS  # noqa: E402
from symgov_backend.concept_external_references import EXTERNAL_MAPPING_METHODS  # noqa: E402
from symgov_backend.publication_gate import (  # noqa: E402
    GATE_DIMENSIONS,
    GATE_EXCEPTION_STATUSES,
    GATE_EXCEPTION_TRANSITIONS,
    GATE_OUTCOMES,
    GATE_REFUSAL_REASONS,
    GATED_PACKAGE_TYPES,
    INTAKE_RIGHTS_DETERMINATION_METHOD,
    INTAKE_RIGHTS_DISPOSITION_PROPOSALS,
    PUBLICATION_GATE_POLICY_VERSION,
    REFUSAL_REASON_DIMENSIONS,
    TRACEABILITY_LEVELS,
    WAIVABLE_DIMENSIONS,
    PublicationGateFacts,
    derive_traceability_level,
    describe_refusal,
    evaluate_publication_gate,
)
from symgov_backend.rights_provenance import (  # noqa: E402
    NON_APPROVING_DETERMINATION_METHODS,
    PERMISSIVE_DISPOSITIONS,
    RIGHTS_DETERMINATION_METHODS,
    RIGHTS_DISPOSITIONS,
    RIGHTS_STATUSES,
)
from symgov_backend.source_package_acquisition import (  # noqa: E402
    AUTHORITATIVE_PACKAGE_TYPE,
    PACKAGE_ACQUISITION_METHODS,
)
from symgov_backend.standard_sources import (  # noqa: E402
    AUTHORITATIVE_RELATIONSHIP_TYPES,
    SOURCE_RELATIONSHIP_TYPES,
    STANDARD_VERIFICATION_METHODS,
)
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    SEMANTIC_ASSIGNMENT_METHODS,
)

REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PACKAGE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
SECOND_PACKAGE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
EDITION_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
DIGEST = "a" * 64


def _facts(**overrides) -> PublicationGateFacts:
    """A revision that satisfies all six dimensions and is in scope."""
    base = dict(
        symbol_revision_id=REVISION_ID,
        gated_package_ids=(PACKAGE_ID,),
        source_package_ids=(PACKAGE_ID,),
        source_locator_recorded=True,
        package_release_identified=True,
        verified_primary_concept=True,
        live_semantic_assignment_count=1,
        waived_dimensions=frozenset(),
        verified_relationship_types=("normative_definition",),
        live_relationship_types=("normative_definition",),
        revision_rights=("licensed", "distribute"),
        package_rights=((PACKAGE_ID, ("licensed", "distribute")),),
        edition_rights=(),
        final_asset_sha256=DIGEST,
        final_asset_hash_source="asset_transformation",
        source_asset_hash_recorded=True,
        lineage_reaches_source_package=True,
        live_classification_count=2,
        verified_classification_count=1,
        external_mapping_count=0,
        verified_external_mapping_count=0,
    )
    base.update(overrides)
    return PublicationGateFacts(**base)


def _result(decision, dimension):
    return next(item for item in decision.dimensions if item.dimension == dimension)


# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------


def test_the_dimensions_are_section_9_2s_six_in_order():
    assert GATE_DIMENSIONS == (
        "semantic_identity",
        "source",
        "graphical_authority",
        "rights",
        "integrity",
        "classification",
    )


def test_the_refusal_reasons_are_not_a_seventh_method_vocabulary():
    """Six `method` vocabularies exist and are deliberately not unified. This
    is the eighth vocabulary in the model and the second diagnostic one: it
    never reaches a method column, and it shares no value with any of them."""
    method_vocabularies = (
        SEMANTIC_ASSIGNMENT_METHODS,
        EXTERNAL_MAPPING_METHODS,
        CLASSIFICATION_ASSIGNMENT_METHODS,
        STANDARD_VERIFICATION_METHODS,
        PACKAGE_ACQUISITION_METHODS,
        RIGHTS_DETERMINATION_METHODS,
    )
    for vocabulary in method_vocabularies:
        assert GATE_REFUSAL_REASONS != vocabulary
        assert not (GATE_REFUSAL_REASONS & vocabulary)


def test_the_refusal_reasons_are_distinct_from_the_mapping_gap_reasons():
    """Two diagnostic vocabularies, both deliberately distinct. A value in
    both would make a gate refusal indistinguishable from a mapping gap in a
    report that joined them."""
    assert GATE_REFUSAL_REASONS != MAPPING_GAP_REASONS
    assert not (GATE_REFUSAL_REASONS & MAPPING_GAP_REASONS)


def test_every_refusal_reason_names_exactly_one_dimension():
    assert set(REFUSAL_REASON_DIMENSIONS) == set(GATE_REFUSAL_REASONS)
    assert set(REFUSAL_REASON_DIMENSIONS.values()) <= set(GATE_DIMENSIONS)


def test_the_outcomes_and_levels_are_the_specifications():
    assert GATE_OUTCOMES == {"permitted", "refused", "not_in_scope"}
    assert TRACEABILITY_LEVELS == ("T0", "T1", "T2", "T3", "T4", "T5")


def test_only_semantic_identity_may_be_waived():
    """Section 9.2 offers an exception for semantic identity and for nothing
    else. Section 16.2 -- "no public authoritative-source symbol is newly
    published with unresolved rights" -- is why `rights` is not in this set."""
    assert WAIVABLE_DIMENSIONS == {"semantic_identity"}
    assert WAIVABLE_DIMENSIONS <= set(GATE_DIMENSIONS)
    assert "rights" not in WAIVABLE_DIMENSIONS


def test_the_gated_package_type_is_sm_p0_05s_constant():
    """Scope is SM-P0-05's `authoritative_library`, which
    `register_source_package` already defaults to. `submission_sheet` -- every
    package the live intake path has ever created -- is deliberately not
    gated, which is how section 17's grandfathering falls out of the data."""
    assert GATED_PACKAGE_TYPES == {AUTHORITATIVE_PACKAGE_TYPE}
    assert AUTHORITATIVE_PACKAGE_TYPE == "authoritative_library"
    assert "submission_sheet" not in GATED_PACKAGE_TYPES


def test_the_exception_lifecycle_is_the_shared_shape():
    assert GATE_EXCEPTION_STATUSES == {"proposed", "approved", "rejected", "retired"}
    # A rejected waiver is terminal; a reviewer who changes their mind
    # proposes afresh.
    assert GATE_EXCEPTION_TRANSITIONS["rejected"] == frozenset()
    assert GATE_EXCEPTION_TRANSITIONS["retired"] == frozenset()
    assert GATE_EXCEPTION_TRANSITIONS["approved"] == frozenset({"retired"})


# --------------------------------------------------------------------------
# The automation gate is a different gate
# --------------------------------------------------------------------------


def test_this_is_not_the_automation_publication_gate():
    """`automation_policy.evaluate_publication_automation_gate` is live, is
    also called a publication gate, and reads `provenance_assessments` --
    whose rights vocabulary shares not one value with `rights_records`'. The
    two gates coexist as strangers, and a refusal reason that appeared in
    both would be the first sign of them being confused."""
    assert not (GATE_REFUSAL_REASONS & LOW_RISK_RIGHTS_STATUSES)
    # The finding SM-P0-06 recorded, re-asserted here because SM-P0-08 is
    # where mixing the two would have done the damage.
    deployed_intake_dispositions = set(INTAKE_RIGHTS_DISPOSITION_PROPOSALS)
    assert deployed_intake_dispositions == {
        "cleared",
        "unknown_warning",
        "restricted",
        "conflict",
        "failed",
    }
    assert not (deployed_intake_dispositions & RIGHTS_DISPOSITIONS)


def test_the_intake_rights_proposal_maps_only_the_disposition():
    """Every proposal is `unknown` on status: an intake assessment never reads
    a licence, so it cannot establish `open` or `licensed`. Only the
    disposition is mapped, and every mapped value is section 7.12's."""
    assert set(INTAKE_RIGHTS_DISPOSITION_PROPOSALS.values()) <= RIGHTS_DISPOSITIONS
    assert "unknown" in RIGHTS_STATUSES
    # `cleared` carries the assessment's positive finding forward as a
    # rendering proposal rather than discarding it; `conflict` proposes the
    # refusal a reviewer must actively overturn.
    assert INTAKE_RIGHTS_DISPOSITION_PROPOSALS["cleared"] == "display"
    assert INTAKE_RIGHTS_DISPOSITION_PROPOSALS["conflict"] == "reject"


def test_an_intake_proposal_can_never_approve_itself():
    """Section 8.4. `ai_assisted` is refused by `rights_records`'
    `approved_not_ai_determined` constraint, so the seeded proposal is durable
    evidence and a reviewer still proposes their own record."""
    assert INTAKE_RIGHTS_DETERMINATION_METHOD in RIGHTS_DETERMINATION_METHODS
    assert INTAKE_RIGHTS_DETERMINATION_METHOD in NON_APPROVING_DETERMINATION_METHODS


# --------------------------------------------------------------------------
# Scope and grandfathering
# --------------------------------------------------------------------------


def test_a_fully_satisfied_in_scope_revision_is_permitted():
    decision = evaluate_publication_gate(_facts())
    assert decision.in_scope is True
    assert decision.outcome == "permitted"
    assert decision.permitted is True
    assert decision.refusal_reasons == ()
    assert all(result.satisfied for result in decision.dimensions)
    assert decision.source_package_id == PACKAGE_ID
    assert decision.policy_version == PUBLICATION_GATE_POLICY_VERSION


def test_a_grandfathered_revision_publishes_and_says_why():
    """Section 17 grandfathers current public data, and section 12.1's M6 asks
    for the gaps to be *reported*. So an out-of-scope revision publishes, and
    the record says `not_in_scope` rather than `permitted` and still carries
    every reason it would have failed on."""
    decision = evaluate_publication_gate(
        _facts(
            gated_package_ids=(),
            verified_primary_concept=False,
            revision_rights=None,
            package_rights=((PACKAGE_ID, None),),
            final_asset_sha256=None,
            final_asset_hash_source=None,
        )
    )
    assert decision.in_scope is False
    assert decision.outcome == "not_in_scope"
    assert decision.permitted is True
    assert decision.source_package_id is None
    # Published, but not silently: the gaps are on the record.
    assert set(decision.refusal_reasons) == {
        "semantic_identity_unverified",
        "rights_undecided",
        "final_asset_hash_absent",
    }


def test_a_revision_with_no_authoritative_package_is_out_of_scope():
    """The live intake path's `submission_sheet` packages are not gated, so a
    revision that traces only to those is out of scope however complete it is."""
    decision = evaluate_publication_gate(_facts(gated_package_ids=()))
    assert decision.outcome == "not_in_scope"
    assert decision.permitted is True


def test_all_six_dimensions_are_always_evaluated():
    """A partial evaluation is not a gate decision, and it is what the
    `dimension_results_complete` check constraint refuses to store."""
    for facts in (_facts(), _facts(gated_package_ids=()), _facts(source_package_ids=())):
        decision = evaluate_publication_gate(facts)
        assert len(decision.dimensions) == 6
        assert tuple(result.dimension for result in decision.dimensions) == GATE_DIMENSIONS
        assert len(decision.as_report()["dimensions"]) == 6


# --------------------------------------------------------------------------
# Each dimension refuses independently, with the reason named
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, dimension, reason",
    [
        (
            {"verified_primary_concept": False},
            "semantic_identity",
            "semantic_identity_unverified",
        ),
        (
            {"source_package_ids": (), "package_rights": ()},
            "source",
            "source_package_unrecorded",
        ),
        (
            {"verified_relationship_types": ()},
            "graphical_authority",
            "graphical_authority_unasserted",
        ),
        (
            {"revision_rights": None, "package_rights": ((PACKAGE_ID, None),)},
            "rights",
            "rights_undecided",
        ),
        (
            {"revision_rights": ("restricted", "metadata_only")},
            "rights",
            "rights_disposition_withholds",
        ),
        ({"final_asset_sha256": None}, "integrity", "final_asset_hash_absent"),
        ({"live_classification_count": 0}, "classification", "classification_absent"),
    ],
)
def test_each_dimension_refuses_independently(overrides, dimension, reason):
    decision = evaluate_publication_gate(_facts(**overrides))
    assert decision.outcome == "refused"
    assert decision.permitted is False
    assert decision.refusal_reasons == (reason,)
    assert reason in GATE_REFUSAL_REASONS
    result = _result(decision, dimension)
    assert result.satisfied is False
    assert result.reason == reason
    # Every other dimension still passed, so the refusal is attributable.
    assert all(item.satisfied for item in decision.dimensions if item.dimension != dimension)


def test_every_reason_the_evaluator_can_emit_is_in_the_vocabulary():
    variants = [
        _facts(),
        _facts(
            verified_primary_concept=False,
            source_package_ids=(),
            package_rights=(),
            verified_relationship_types=(),
            revision_rights=None,
            final_asset_sha256=None,
            live_classification_count=0,
        ),
        _facts(revision_rights=("unknown", "reject")),
        _facts(gated_package_ids=()),
    ]
    for facts in variants:
        decision = evaluate_publication_gate(facts)
        for reason in decision.refusal_reasons:
            assert reason in GATE_REFUSAL_REASONS
        for result in decision.dimensions:
            assert result.reason is None or result.reason in GATE_REFUSAL_REASONS


def test_a_refusal_names_every_failing_dimension_not_only_the_first():
    decision = evaluate_publication_gate(
        _facts(verified_primary_concept=False, final_asset_sha256=None)
    )
    assert set(decision.refusal_reasons) == {
        "semantic_identity_unverified",
        "final_asset_hash_absent",
    }
    assert "semantic_identity" in describe_refusal(decision)
    assert "integrity" in describe_refusal(decision)


def test_describe_refusal_says_nothing_when_nothing_refused():
    assert "did not refuse" in describe_refusal(evaluate_publication_gate(_facts()))


# --------------------------------------------------------------------------
# Section 9.2's semantic-identity exception
# --------------------------------------------------------------------------


def test_an_approved_exception_satisfies_semantic_identity_and_is_flagged():
    """Section 9.2's escape hatch, and the only reason the gate can pass at
    all today: nothing in production creates a `SemanticConcept`."""
    decision = evaluate_publication_gate(
        _facts(
            verified_primary_concept=False,
            waived_dimensions=frozenset({"semantic_identity"}),
        )
    )
    assert decision.outcome == "permitted"
    result = _result(decision, "semantic_identity")
    assert result.satisfied is True
    # Satisfied *and* waived. A waiver is not a pass and the record must be
    # able to tell them apart.
    assert result.waived is True
    assert result.evidence["verified_primary_concept"] is False


def test_a_waiver_of_another_dimension_does_not_satisfy_it():
    """`publication_gate_exceptions.dimension` accepts all six names, so a
    `rights` row is storable; `propose_publication_gate_exception` refuses to
    create one, and the evaluator would not honour it either."""
    decision = evaluate_publication_gate(
        _facts(
            revision_rights=None,
            package_rights=((PACKAGE_ID, None),),
            waived_dimensions=frozenset({"rights", "integrity"}),
        )
    )
    assert decision.outcome == "refused"
    assert decision.refusal_reasons == ("rights_undecided",)
    assert _result(decision, "rights").waived is False


def test_a_waiver_never_claims_semantic_identity_for_traceability():
    """Section 9.2's exception lets a symbol publish; it does not let it claim
    a verified concept, so T4 stays out of reach."""
    facts = _facts(
        verified_primary_concept=False, waived_dimensions=frozenset({"semantic_identity"})
    )
    decision = evaluate_publication_gate(facts)
    assert decision.outcome == "permitted"
    assert decision.traceability_level == "T3"


# --------------------------------------------------------------------------
# The rights precedence rule SM-P0-06 left to this package
# --------------------------------------------------------------------------


def test_the_revisions_own_record_governs_over_its_packages():
    decision = evaluate_publication_gate(
        _facts(
            revision_rights=("open", "distribute"),
            package_rights=((PACKAGE_ID, ("unknown", "reject")),),
        )
    )
    assert _result(decision, "rights").satisfied is True
    assert _result(decision, "rights").evidence["governing_subject"] == "symbol_revision"


def test_a_withholding_revision_record_is_not_rescued_by_its_package():
    """Most specific wins in both directions. A revision recorded as
    `metadata_only` is not published because the package it came in was
    licensed for distribution."""
    decision = evaluate_publication_gate(
        _facts(
            revision_rights=("licensed", "metadata_only"),
            package_rights=((PACKAGE_ID, ("licensed", "distribute")),),
        )
    )
    assert decision.refusal_reasons == ("rights_disposition_withholds",)


def test_every_source_package_must_be_approved_not_merely_one():
    """A revision assembled from two packages needs permission from both: a
    licence over one release says nothing about another."""
    decision = evaluate_publication_gate(
        _facts(
            revision_rights=None,
            source_package_ids=(PACKAGE_ID, SECOND_PACKAGE_ID),
            package_rights=(
                (PACKAGE_ID, ("licensed", "distribute")),
                (SECOND_PACKAGE_ID, None),
            ),
        )
    )
    assert decision.refusal_reasons == ("rights_undecided",)

    decision = evaluate_publication_gate(
        _facts(
            revision_rights=None,
            source_package_ids=(PACKAGE_ID, SECOND_PACKAGE_ID),
            package_rights=(
                (PACKAGE_ID, ("licensed", "distribute")),
                (SECOND_PACKAGE_ID, ("open", "display")),
            ),
        )
    )
    assert _result(decision, "rights").satisfied is True
    assert _result(decision, "rights").evidence["governing_subject"] == "source_package"


def test_a_standard_editions_rights_never_satisfy_the_dimension():
    """Rights to read a standard are not rights to publish a symbol derived
    from it, and section 8.3's `derived_from` is exactly that case. The
    edition is reported and never sufficient."""
    decision = evaluate_publication_gate(
        _facts(
            revision_rights=None,
            package_rights=((PACKAGE_ID, None),),
            edition_rights=((EDITION_ID, ("licensed", "distribute")),),
        )
    )
    assert decision.refusal_reasons == ("rights_undecided",)
    assert _result(decision, "rights").evidence["standard_editions_with_approved_rights"] == 1


@pytest.mark.parametrize("disposition", sorted(RIGHTS_DISPOSITIONS))
def test_only_a_permissive_disposition_satisfies_the_rights_dimension(disposition):
    """Publishing to the public catalogue renders and hands on the asset, so
    `metadata_only` and `reject` are honest records of a work SymGov may not
    publish rather than permissions to publish it."""
    decision = evaluate_publication_gate(_facts(revision_rights=("licensed", disposition)))
    expected = disposition in PERMISSIVE_DISPOSITIONS
    assert _result(decision, "rights").satisfied is expected
    if not expected:
        assert decision.refusal_reasons == ("rights_disposition_withholds",)


def test_undecided_and_withholding_are_different_refusals():
    """Section 13.1's rights dimension distinguishes "unknown" from a decided
    restriction, so the gate does too."""
    undecided = evaluate_publication_gate(
        _facts(revision_rights=None, package_rights=((PACKAGE_ID, None),))
    )
    withholding = evaluate_publication_gate(_facts(revision_rights=("open", "metadata_only")))
    assert undecided.refusal_reasons != withholding.refusal_reasons


# --------------------------------------------------------------------------
# Graphical authority and classification read different statuses, on purpose
# --------------------------------------------------------------------------


def test_graphical_authority_needs_a_verified_link_not_a_proposal():
    """Section 8.4: "normative graphical-source assertions should require
    human or deterministic authoritative-source verification for public
    publication". A `proposed` link -- all SM-P0-07's mapping ever writes --
    is the ambiguous standard-association section 9.2 refuses."""
    decision = evaluate_publication_gate(
        _facts(verified_relationship_types=(), live_relationship_types=("derived_from",))
    )
    assert decision.refusal_reasons == ("graphical_authority_unasserted",)
    result = _result(decision, "graphical_authority")
    assert result.evidence["live_relationship_types"] == ["derived_from"]


@pytest.mark.parametrize("relationship", sorted(SOURCE_RELATIONSHIP_TYPES))
def test_any_of_section_8_3s_eight_types_satisfies_graphical_authority(relationship):
    """The dimension asks that the relationship be *stated*, not that it be
    normative: `vendor_implementation` is as explicit an assertion as
    `normative_definition`. Whether it is authoritative is evidence."""
    decision = evaluate_publication_gate(
        _facts(verified_relationship_types=(relationship,), live_relationship_types=(relationship,))
    )
    result = _result(decision, "graphical_authority")
    assert result.satisfied is True
    assert result.evidence["authoritative_relationship_types"] == (
        [relationship] if relationship in AUTHORITATIVE_RELATIONSHIP_TYPES else []
    )


def test_classification_accepts_a_proposed_assignment():
    """Deliberate drafting, not an omission: section 9.2's semantic row says
    "verified primary" in as many words and its classification row says only
    "governed ... assignment for discoverability". Discovery works from a
    proposal; engineering identity does not."""
    decision = evaluate_publication_gate(
        _facts(live_classification_count=1, verified_classification_count=0)
    )
    assert _result(decision, "classification").satisfied is True
    assert decision.outcome == "permitted"


# --------------------------------------------------------------------------
# Section 13.2's derived level
# --------------------------------------------------------------------------


def _level(**overrides) -> str:
    facts = _facts(**overrides)
    return evaluate_publication_gate(facts).traceability_level


def test_the_traceability_ladder_stops_at_the_first_gap():
    assert _level(source_package_ids=(), package_rights=()) == "T0"
    assert _level(package_release_identified=False) == "T1"
    assert _level(source_locator_recorded=False) == "T2"
    assert _level(verified_primary_concept=False) == "T3"
    assert _level(lineage_reaches_source_package=False) == "T4"
    assert _level() == "T5"


def test_every_derived_level_is_in_the_vocabulary():
    for facts in (
        _facts(),
        _facts(source_package_ids=(), package_rights=()),
        _facts(verified_primary_concept=False),
        _facts(revision_rights=None, package_rights=((PACKAGE_ID, None),)),
        _facts(external_mapping_count=3, verified_external_mapping_count=0),
    ):
        decision = evaluate_publication_gate(facts)
        assert decision.traceability_level in TRACEABILITY_LEVELS
        assert derive_traceability_level(facts, decision.dimensions) == decision.traceability_level


def test_t4_does_not_require_an_external_mapping_that_does_not_exist():
    """Section 13.2's "where applicable": "an architectural or safety symbol
    may be fully traceable without a DEXPI mapping"."""
    assert _level(external_mapping_count=0, verified_external_mapping_count=0) == "T5"
    # But an unverified one that does exist holds the level back.
    assert _level(external_mapping_count=2, verified_external_mapping_count=0) == "T3"
    assert _level(external_mapping_count=2, verified_external_mapping_count=1) == "T5"


def test_an_unresolved_rights_disposition_caps_the_level_at_t2():
    """Section 13.2's T3 is "exact source entry/symbol *and rights
    disposition* established"."""
    assert _level(revision_rights=None, package_rights=((PACKAGE_ID, None),)) == "T2"


def test_the_level_is_recorded_even_for_a_grandfathered_revision():
    decision = evaluate_publication_gate(_facts(gated_package_ids=()))
    assert decision.outcome == "not_in_scope"
    assert decision.traceability_level == "T5"


# --------------------------------------------------------------------------
# The report shape the evaluation row stores
# --------------------------------------------------------------------------


def test_the_report_is_json_safe_and_complete():
    report = evaluate_publication_gate(_facts()).as_report()
    assert report["symbol_revision_id"] == str(REVISION_ID)
    assert report["source_package_id"] == str(PACKAGE_ID)
    assert report["policy_version"] == PUBLICATION_GATE_POLICY_VERSION
    assert report["traceability_level"] in TRACEABILITY_LEVELS
    assert [item["dimension"] for item in report["dimensions"]] == list(GATE_DIMENSIONS)
    import json

    json.dumps(report)
