"""Contract cover for SM-P1-01 WP1.1: the cross-target semantic review queue.

Scope note: this file is deliberately DB-free. It pins the decision-capability
rules and the filter vocabularies, which are pure functions of the frozen
governance vocabularies the P0 services already own. Behaviour only a real
PostgreSQL server can prove -- ordering across symbols, the joined
human-readable symbol identity, pagination bounds, and section 14.2's
tenant isolation -- lives in `test_semantic_review_queries_postgresql.py`.

The capabilities are computed here rather than in the frontend because the
rule they encode is a database CHECK constraint
(`ck_symbol_revision_classifications_backfill_not_verified`, specification
section 12.3): a `legacy_backfill` assignment can never be verified, only
rejected and re-proposed with a real method. A UI that offered an approve
button would be offering one the database refuses. All 132 rows the SM-P0-10
backfill applied in production are exactly this case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symgov_backend import semantic_review

from symgov_backend.classification_assignments import (
    BACKFILL_METHODS,
    CLASSIFICATION_ASSIGNMENT_METHODS,
    CLASSIFICATION_ASSIGNMENT_STATUSES,
    CLASSIFICATION_ASSIGNMENT_TRANSITIONS,
)
from symgov_backend.concept_external_references import (
    EXTERNAL_MAPPING_STATUSES,
    EXTERNAL_MAPPING_TRANSITIONS,
)
from symgov_backend.rights_provenance import (
    NON_APPROVING_DETERMINATION_METHODS,
    RIGHTS_DECISION_STATUSES,
    RIGHTS_DECISION_TRANSITIONS,
    RIGHTS_DETERMINATION_METHODS,
)
from symgov_backend.symbol_semantic_assignments import (
    SEMANTIC_ASSIGNMENT_METHODS,
    SEMANTIC_ASSIGNMENT_STATUSES,
    SEMANTIC_ASSIGNMENT_TRANSITIONS,
)
from symgov_backend.semantic_review import (
    DecisionCapabilities,
    classification_decision_capabilities,
    external_mapping_decision_capabilities,
    rights_decision_capabilities,
    semantic_assignment_decision_capabilities,
)


def test_a_backfilled_proposal_may_be_rejected_but_never_verified():
    """Section 12.3, and the `backfill_not_verified` CHECK constraint.

    This is the whole production review population: 132 assignments across 73
    of 96 symbols, every one `proposed`/`legacy_backfill`.
    """
    capabilities = classification_decision_capabilities(status="proposed", method="legacy_backfill")

    assert capabilities.can_verify is False
    assert capabilities.can_reject is True
    assert capabilities.must_repropose is True
    assert capabilities.blocked_reason is not None
    assert "legacy_backfill" in capabilities.blocked_reason


def test_an_ordinary_proposal_may_be_verified():
    capabilities = classification_decision_capabilities(status="proposed", method="source_mapping")

    assert capabilities.can_verify is True
    assert capabilities.can_reject is True
    assert capabilities.must_repropose is False
    assert capabilities.blocked_reason is None


def test_a_terminal_row_offers_no_decision_at_all():
    """`rejected` and `retired` have empty transition sets, so a queue row in
    either state must offer nothing -- not even a reject."""
    for status in ("rejected", "retired"):
        capabilities = classification_decision_capabilities(status=status, method="manual")
        assert capabilities.can_verify is False, status
        assert capabilities.can_reject is False, status
        assert capabilities.can_retire is False, status


def test_a_verified_row_may_only_be_retired():
    capabilities = classification_decision_capabilities(status="verified", method="manual")

    assert capabilities.can_verify is False
    assert capabilities.can_reject is False
    assert capabilities.can_retire is True


@pytest.mark.parametrize("status", sorted(CLASSIFICATION_ASSIGNMENT_STATUSES))
@pytest.mark.parametrize("method", sorted(CLASSIFICATION_ASSIGNMENT_METHODS))
def test_every_capability_agrees_with_the_service_that_enforces_it(status, method):
    """The capabilities must be *derived* from
    `CLASSIFICATION_ASSIGNMENT_TRANSITIONS` and `BACKFILL_METHODS`, not
    restated alongside them. A second copy of the rule is a copy that can
    drift from the constraint and from `transition_symbol_revision_classification`.
    """
    capabilities = classification_decision_capabilities(status=status, method=method)
    allowed = CLASSIFICATION_ASSIGNMENT_TRANSITIONS[status]

    assert capabilities.can_reject is ("rejected" in allowed)
    assert capabilities.can_retire is ("retired" in allowed)
    assert capabilities.can_verify is ("verified" in allowed and method not in BACKFILL_METHODS)
    # A row that is *only* barred from verification by the backfill rule is
    # the one a reviewer must re-propose; a terminal row simply has no action.
    assert capabilities.must_repropose is (
        method in BACKFILL_METHODS and "verified" in allowed
    )


def test_capabilities_reject_a_value_outside_the_frozen_vocabularies():
    with pytest.raises(ValueError):
        classification_decision_capabilities(status="approved", method="manual")
    with pytest.raises(ValueError):
        classification_decision_capabilities(status="proposed", method="imported")


def test_decision_capabilities_is_a_frozen_value():
    capabilities = classification_decision_capabilities(status="proposed", method="manual")
    assert isinstance(capabilities, DecisionCapabilities)
    with pytest.raises(Exception):
        capabilities.can_verify = True


# --------------------------------------------------------------------------
# The other three decision vocabularies
#
# Each governed table has its own transition table and its own permanent bars,
# and they are deliberately not the same -- section 7.5 names `imported` where
# section 7.9 names `source_mapping`, and only two of the four carry a bar that
# no reviewer can lift. Deriving each from its own service is what stops this
# module from quietly flattening four vocabularies into one.
# --------------------------------------------------------------------------


def test_a_semantic_assignment_has_no_permanent_bar_on_verification():
    """Unlike classifications and rights, section 7.9 has no method that can
    never be verified: `manual` and `ai_assisted` need a named reviewer, which
    a review UI always has."""
    for method in sorted(SEMANTIC_ASSIGNMENT_METHODS):
        capabilities = semantic_assignment_decision_capabilities(status="proposed", method=method)
        assert capabilities.can_verify is True, method
        assert capabilities.must_repropose is False, method
        assert capabilities.blocked_reason is None, method


@pytest.mark.parametrize("status", sorted(SEMANTIC_ASSIGNMENT_STATUSES))
def test_semantic_assignment_capabilities_follow_their_own_transition_table(status):
    capabilities = semantic_assignment_decision_capabilities(status=status, method="manual")
    allowed = SEMANTIC_ASSIGNMENT_TRANSITIONS[status]

    assert capabilities.can_verify is ("verified" in allowed)
    assert capabilities.can_reject is ("rejected" in allowed)
    assert capabilities.can_retire is ("retired" in allowed)


@pytest.mark.parametrize("status", sorted(EXTERNAL_MAPPING_STATUSES))
def test_external_mapping_capabilities_follow_their_own_transition_table(status):
    capabilities = external_mapping_decision_capabilities(status=status, method="manual")
    allowed = EXTERNAL_MAPPING_TRANSITIONS[status]

    assert capabilities.can_verify is ("verified" in allowed)
    assert capabilities.can_reject is ("rejected" in allowed)
    assert capabilities.can_retire is ("retired" in allowed)
    # Section 16.2's "no verified exact mapping from string similarity alone"
    # is a decision-time check on the basis the verifier supplies, not a
    # property of the stored row, so it is not a row capability.
    assert capabilities.must_repropose is False


def test_external_mapping_capabilities_use_their_own_method_vocabulary():
    """`imported`, not `source_mapping` (section 7.5 vs 7.9)."""
    assert external_mapping_decision_capabilities(status="proposed", method="imported").can_verify is True
    with pytest.raises(ValueError):
        external_mapping_decision_capabilities(status="proposed", method="source_mapping")


def test_an_ai_assisted_rights_record_can_never_be_approved():
    """Section 8.4 forbids an `ai_assisted` determination from approving
    itself, and `publication_gate.propose_intake_rights_record` writes exactly
    that -- so every rights record production holds today is in this state.
    A reviewer's route forward is their own proposal, not an approve button.
    """
    capabilities = rights_decision_capabilities(status="proposed", determination_method="ai_assisted")

    assert capabilities.can_verify is False
    assert capabilities.can_reject is True
    assert capabilities.must_repropose is True
    assert capabilities.blocked_reason is not None
    assert "ai_assisted" in capabilities.blocked_reason


def test_a_human_determined_rights_record_can_be_approved():
    for method in ("manual", "licence_document"):
        capabilities = rights_decision_capabilities(status="proposed", determination_method=method)
        assert capabilities.can_verify is True, method
        assert capabilities.must_repropose is False, method


@pytest.mark.parametrize("status", sorted(RIGHTS_DECISION_STATUSES))
@pytest.mark.parametrize("method", sorted(RIGHTS_DETERMINATION_METHODS))
def test_rights_capabilities_agree_with_the_service_that_enforces_them(status, method):
    capabilities = rights_decision_capabilities(status=status, determination_method=method)
    allowed = RIGHTS_DECISION_TRANSITIONS[status]

    # `approved` is this table's name for the affirmative decision; `verified`
    # is the others'. The capability keeps one name so a queue can render one
    # control, and the reason line says which word the service uses.
    assert capabilities.can_verify is (
        "approved" in allowed and method not in NON_APPROVING_DETERMINATION_METHODS
    )
    assert capabilities.can_reject is ("rejected" in allowed)
    assert capabilities.can_retire is ("retired" in allowed)
    assert capabilities.must_repropose is (
        method in NON_APPROVING_DETERMINATION_METHODS and "approved" in allowed
    )


def test_the_review_queue_module_is_a_pure_read_path():
    """WP1.1 is queries only: routes are WP1.2 and every governance act
    already exists as a tested service function. A write reaching this module
    would mean a second, untested path to a governed decision.
    """
    source = (
        Path(semantic_review.__file__).read_text(encoding="utf-8")
    )
    for forbidden in ("session.add(", "session.commit(", "session.flush(", "session.delete("):
        assert forbidden not in source, f"{forbidden} has no place in a read-only queue module"
    # Routers are WP1.2's file, not this one.
    assert "APIRouter" not in source
    assert "fastapi" not in source
