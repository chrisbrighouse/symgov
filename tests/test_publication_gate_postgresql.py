"""Real-PostgreSQL cover for SM-P0-08: the section 9.2 publication gate.

The gate's *rules* are proved DB-free in `test_publication_gate.py`. What
needs a real server is everything about the reading and the writing: that
`collect_publication_gate_facts` gathers each dimension from the rows that
actually hold it, that a refusal really stops a real promotion and leaves the
symbol organisation-private, that a grandfathered symbol still publishes
through the same function, that the decision survives in
`publication_gate_evaluations`, that every check constraint on the two new
tables rejects a row rather than accepting a NULL, and that the promotion path
now seeds a durable proposed `RightsRecord` from the intake assessment.

Migration `20260911_0057` is purely additive -- two new tables, no column on
any existing one -- so every `*_postgresql.py` fixture constant pinned below
head still opens a `Session` against models it does not touch. What does move
is the seven sole-head assertions, which now read `20260911_0057`.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.classification_assignments import (  # noqa: E402
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from symgov_backend.classification_schemes import get_classification_scheme  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AgentDefinition,
    AgentQueueItem,
    ClassificationNode,
    ClassificationRecord,
    GovernedSymbol,
    HumanReviewDecision,
    IntakeRecord,
    Organization,
    PromotionRequest,
    ProvenanceAssessment,
    PublicationGateEvaluation,
    PublicationGateException,
    ReviewCase,
    ReviewCaseAction,
    RightsRecord,
    SourcePackage,
    SymbolRevision,
    ValidationReport,
)
from symgov_backend.organization_promotion_handoff import (  # noqa: E402
    execute_organization_promotion_handoff,
)
from symgov_backend.publication_gate import (  # noqa: E402
    PUBLICATION_GATE_POLICY_VERSION,
    approved_gate_exceptions,
    collect_publication_gate_facts,
    enforce_publication_gate,
    evaluate_publication_gate,
    propose_publication_gate_exception,
    transition_publication_gate_exception,
)
from symgov_backend.publication_handoff import ensure_approved_symbol_revision  # noqa: E402
from symgov_backend.rights_provenance import (  # noqa: E402
    propose_rights_record,
    transition_rights_record,
)
from symgov_backend.source_package_acquisition import (  # noqa: E402
    add_source_package_entry,
    register_source_package,
)
from symgov_backend.standard_sources import (  # noqa: E402
    assert_symbol_standard_link,
    register_standard,
    register_standard_version,
    transition_symbol_standard_link,
)

NOW = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)

# The gate's own migration. Pinned at head deliberately: this file opens a
# `Session` over `publication_gate_evaluations`, which nothing before
# 20260911_0057 has.
GATE_REVISION = "20260911_0057"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def session_factory():
    with _database("symgov-sm-p0-08") as (engine, url, _raw):
        _alembic(url, "upgrade", "head")
        yield sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def session(session_factory):
    with session_factory() as active:
        yield active
        active.rollback()


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


def _user(session, label: str) -> uuid.UUID:
    identifier = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
            "must_change_pin,is_active,created_at,updated_at) "
            "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
        ),
        {"id": identifier, "email": f"{label}-{identifier.hex[:8]}@example.test", "now": NOW},
    )
    session.flush()
    return identifier


def _symbol_revision(session, author: uuid.UUID, **symbol_kwargs) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a governed symbol and one draft revision of it.

    Draft on purpose: 20260826_0031's publication invariant demands a
    canonical catalog identifier of a published revision, which is not what
    any of these tests are about.
    """
    slug = f"gate-symbol-{uuid.uuid4().hex[:10]}"
    symbol = GovernedSymbol(
        id=uuid.uuid4(),
        slug=slug,
        canonical_name=slug,
        category="Valves",
        discipline="Piping / P&ID",
        owner_id=author,
        created_at=NOW,
        updated_at=NOW,
        **symbol_kwargs,
    )
    session.add(symbol)
    session.flush()
    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label="1",
        lifecycle_state="draft",
        payload_json={},
        author_id=author,
        created_at=NOW,
    )
    session.add(revision)
    session.flush()
    return symbol.id, revision.id


def _package(session, *, package_type: str, **kwargs) -> SourcePackage:
    package = register_source_package(
        session,
        package_code=f"GATEPKG-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative gate test package",
        registered_at=NOW,
        package_type=package_type,
        **kwargs,
    )
    session.flush()
    return package


def _standard_version(session) -> uuid.UUID:
    """A synthetic standard and one edition.

    Synthetic because 20260910_0055 seeded 219 real CFIHOS standards and every
    plausible real code is taken.
    """
    standard = register_standard(
        session,
        standard_code=f"GATE-{uuid.uuid4().hex[:8].upper()}",
        title="Illustrative gate test standard",
        registered_at=NOW,
    )
    session.flush()
    version = register_standard_version(
        session, standard_id=standard.id, version_label="2026", registered_at=NOW
    )
    session.flush()
    return version.id


def _a_node(session) -> ClassificationNode:
    scheme = get_classification_scheme(session, "ENGINEERING-DISCIPLINE")
    return (
        session.query(ClassificationNode)
        .filter(ClassificationNode.scheme_id == scheme.id)
        .order_by(ClassificationNode.sort_order)
        .first()
    )


def _satisfy_all_dimensions(session, actor: uuid.UUID, *, package_type: str):
    """Seed one revision that satisfies every section 9.2 dimension.

    Semantic identity is satisfied by section 9.2's own exception rather than
    by a verified concept assignment: nothing in production creates a
    `SemanticConcept`, so the exception is the only path there is.
    """
    _symbol_id, revision_id = _symbol_revision(session, actor)

    package = _package(
        session,
        package_type=package_type,
        release_version="2026.1",
        acquisition_method="licensed_download",
        acquired_at=NOW,
        licence_reference="CONTRACT-GATE-001",
    )
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_label="entry",
        source_path="library/valves/gate.svg",
        original_asset_sha256="b" * 64,
    )

    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision_id,
        standard_version_id=_standard_version(session),
        relationship_type="normative_definition",
        asserted_at=NOW,
        source_symbol_identifier="A-123",
    )
    session.flush()
    transition_symbol_standard_link(
        session,
        link.id,
        target_status="verified",
        occurred_at=NOW,
        verification_method="manual",
        verified_by_user_id=actor,
    )

    rights = propose_rights_record(
        session,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="licensed",
        symbol_revision_id=revision_id,
        licence_reference="CONTRACT-GATE-001",
    )
    session.flush()
    transition_rights_record(
        session,
        rights.id,
        target_status="approved",
        occurred_at=NOW,
        decided_by_user_id=actor,
        decision_reason="Distribution is permitted under the recorded contract.",
    )

    # Integrity: the shape `organization_symbol_drafts.upload_draft_asset`
    # writes, which is the only one that carries a hash today.
    revision = session.get(SymbolRevision, revision_id)
    revision.payload_json = {"assets": [{"object_key": "o", "sha256": "c" * 64}]}

    propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision_id,
        classification_node_id=_a_node(session).id,
        assignment_role="primary",
        method="source_mapping",
        proposed_at=NOW,
    )

    waiver = propose_publication_gate_exception(
        session,
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        proposed_at=NOW,
        proposed_by_user_id=actor,
    )
    session.flush()
    transition_publication_gate_exception(
        session,
        waiver.id,
        target_status="approved",
        occurred_at=NOW,
        approved_by_user_id=actor,
        approval_reason="Annotation symbol; no engineering concept applies.",
    )
    session.flush()
    return revision_id, package


# --------------------------------------------------------------------------
# The migration itself
# --------------------------------------------------------------------------


def test_the_two_tables_exist_at_head(session):
    for table in ("publication_gate_evaluations", "publication_gate_exceptions"):
        assert session.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def test_no_constraint_name_was_truncated_or_double_prefixed(session):
    """PostgreSQL's 63-character identifier limit silently hash-truncates a
    long name inside `create_table`, and an already-prefixed name gets
    double-prefixed -- the defect 20260909_0050 and 20260909_0053 repaired."""
    rows = session.execute(
        text(
            "SELECT conname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname IN ('publication_gate_evaluations', 'publication_gate_exceptions')"
        )
    ).scalars().all()
    assert rows
    for name in rows:
        assert len(name) < 63, name
        assert "ck_ck_" not in name
        assert not name.startswith("ck_publication_gate_evaluations_ck_")
        assert not name.startswith("ck_publication_gate_exceptions_ck_")


@pytest.mark.parametrize(
    "overrides",
    [
        # Every branch of every three-valued-logic-prone constraint, proved to
        # reject a row on a real server rather than passing on a NULL.
        {"outcome": "invented"},
        {"traceability_level": "T9"},
        {"in_scope": True, "source_package_id": None},  # scope_requires_package
        {"in_scope": True, "outcome": "not_in_scope"},  # scope_matches_outcome
        {"in_scope": False, "outcome": "refused"},  # scope_matches_outcome
        {"outcome": "refused", "refusal_reasons_json": []},  # refusal_names_a_reason
        {"outcome": "permitted", "refusal_reasons_json": ["rights_undecided"]},
        {"dimension_results_json": []},  # dimension_results_complete
        {"dimension_results_json": {}},  # dimension_results_array
        {"policy_version": "   "},
    ],
)
def test_every_evaluation_constraint_rejects_a_row(session, overrides):
    actor = _user(session, "gate-constraint")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    package = _package(session, package_type="authoritative_library")
    base = dict(
        id=uuid.uuid4(),
        symbol_revision_id=revision_id,
        source_package_id=package.id,
        in_scope=True,
        outcome="permitted",
        traceability_level="T3",
        dimension_results_json=[{"dimension": name} for name in range(6)],
        refusal_reasons_json=[],
        policy_version=PUBLICATION_GATE_POLICY_VERSION,
        evaluated_at=NOW,
    )
    base.update(overrides)
    session.add(PublicationGateEvaluation(**base))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"dimension": "not_a_dimension"},
        {"decision_status": "invented"},
        # decision_actor: approved with no approver, and with no timestamp.
        {"decision_status": "approved", "approval_reason": "why", "approved_by_user_id": None},
        {"decision_status": "approved", "approval_reason": "why", "approved_at": None},
        # approved_reason: approved with no reason at all.
        {"decision_status": "approved", "approval_reason": None},
        {"approval_reason": "  "},
    ],
)
def test_every_exception_constraint_rejects_a_row(session, overrides):
    actor = _user(session, "gate-exception-constraint")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    base = dict(
        id=uuid.uuid4(),
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        decision_status="proposed",
        approved_by_user_id=actor,
        approved_at=NOW,
        approval_reason="Annotation symbol.",
        evidence_json={},
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    session.add(PublicationGateException(**base))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_only_one_approved_waiver_per_revision_and_dimension(session):
    actor = _user(session, "gate-waiver-unique")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    for _ in range(2):
        session.add(
            PublicationGateException(
                id=uuid.uuid4(),
                symbol_revision_id=revision_id,
                dimension="semantic_identity",
                decision_status="approved",
                approved_by_user_id=actor,
                approved_at=NOW,
                approval_reason="Annotation symbol.",
                evidence_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# --------------------------------------------------------------------------
# Reading the facts from real rows
# --------------------------------------------------------------------------


def test_a_fully_satisfied_authoritative_revision_is_permitted(session):
    actor = _user(session, "gate-permit")
    revision_id, package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    facts = collect_publication_gate_facts(session, revision_id)
    decision = evaluate_publication_gate(facts)

    assert facts.gated_package_ids == (package.id,)
    assert decision.in_scope is True
    assert decision.outcome == "permitted"
    assert decision.refusal_reasons == ()
    assert all(result.satisfied for result in decision.dimensions)
    # Semantic identity passed by section 9.2's exception, and the record says
    # so rather than claiming a verified concept.
    semantic = next(r for r in decision.dimensions if r.dimension == "semantic_identity")
    assert semantic.waived is True
    assert facts.verified_primary_concept is False


def test_the_same_symbol_under_a_submission_package_is_grandfathered(session):
    """Section 17's scope, proved against the package type the live intake
    path actually writes. `runtime.ensure_source_package_for_intake` creates
    every production package as `submission_sheet`."""
    actor = _user(session, "gate-grandfather")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="submission_sheet"
    )
    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.in_scope is False
    assert decision.outcome == "not_in_scope"
    assert decision.permitted is True


@pytest.mark.parametrize(
    "remove, reason",
    [
        ("semantic_identity", "semantic_identity_unverified"),
        ("graphical_authority", "graphical_authority_unasserted"),
        ("rights", "rights_undecided"),
        ("integrity", "final_asset_hash_absent"),
        ("classification", "classification_absent"),
    ],
)
def test_each_dimension_refuses_from_real_rows(session, remove, reason):
    """The DB-free tests prove the rules; this proves each rule is reading the
    table that actually holds the fact."""
    actor = _user(session, "gate-dimension")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )

    if remove == "semantic_identity":
        session.query(PublicationGateException).filter(
            PublicationGateException.symbol_revision_id == revision_id
        ).delete()
    elif remove == "graphical_authority":
        session.execute(
            text("DELETE FROM symbol_standard_links WHERE symbol_revision_id = :r"),
            {"r": revision_id},
        )
    elif remove == "rights":
        session.execute(
            text("DELETE FROM rights_records WHERE symbol_revision_id = :r"), {"r": revision_id}
        )
    elif remove == "integrity":
        session.get(SymbolRevision, revision_id).payload_json = {}
    elif remove == "classification":
        session.execute(
            text("DELETE FROM symbol_revision_classifications WHERE symbol_revision_id = :r"),
            {"r": revision_id},
        )
    session.flush()
    session.expire_all()

    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.outcome == "refused"
    assert decision.refusal_reasons == (reason,)


def test_the_source_dimension_refuses_a_revision_with_no_package(session):
    """Necessarily out of scope as well -- a revision reaching no package
    reaches no authoritative one -- so the outcome is `not_in_scope` and the
    reason is still recorded. That pairing is section 12.1 M6's whole point."""
    actor = _user(session, "gate-no-package")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.outcome == "not_in_scope"
    assert "source_package_unrecorded" in decision.refusal_reasons


def test_a_proposed_standard_link_does_not_assert_graphical_authority(session):
    """SM-P0-07's mapping only ever writes `proposed`, which is section 9.2's
    ambiguous standard-association."""
    actor = _user(session, "gate-proposed-link")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    session.execute(
        text("UPDATE symbol_standard_links SET assertion_status='proposed' WHERE symbol_revision_id=:r"),
        {"r": revision_id},
    )
    session.flush()
    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.refusal_reasons == ("graphical_authority_unasserted",)


def test_a_proposed_classification_does_satisfy_its_dimension(session):
    actor = _user(session, "gate-proposed-classification")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    facts = collect_publication_gate_facts(session, revision_id)
    assert facts.live_classification_count == 1
    assert facts.verified_classification_count == 0
    assert evaluate_publication_gate(facts).outcome == "permitted"


def test_a_verified_classification_lifts_the_traceability_level(session):
    actor = _user(session, "gate-verified-classification")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    before = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assignment_id = session.execute(
        text("SELECT id FROM symbol_revision_classifications WHERE symbol_revision_id = :r"),
        {"r": revision_id},
    ).scalar_one()
    transition_symbol_revision_classification(
        session,
        assignment_id,
        target_status="verified",
        occurred_at=NOW,
        reviewed_by_user_id=actor,
    )
    session.flush()
    after = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    # Still no verified concept, so T4 is out of reach either way; what the
    # verification changes is visible in the dimension evidence.
    assert before.traceability_level == after.traceability_level == "T3"
    classification = next(r for r in after.dimensions if r.dimension == "classification")
    assert classification.evidence["verified_classification_assignments"] == 1


def test_a_packages_approved_rights_govern_when_the_revision_has_none(session):
    """The inheritance rule SM-P0-06 deliberately left to this package."""
    actor = _user(session, "gate-package-rights")
    revision_id, package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    session.execute(
        text("DELETE FROM rights_records WHERE symbol_revision_id = :r"), {"r": revision_id}
    )
    record = propose_rights_record(
        session,
        disposition="display",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="open",
        source_package_id=package.id,
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=NOW,
        decided_by_user_id=actor,
        decision_reason="Published under an open licence.",
    )
    session.flush()

    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.outcome == "permitted"
    rights = next(r for r in decision.dimensions if r.dimension == "rights")
    assert rights.evidence["governing_subject"] == "source_package"


def test_an_approved_but_withholding_disposition_refuses(session):
    actor = _user(session, "gate-withholding")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    session.execute(
        text("DELETE FROM rights_records WHERE symbol_revision_id = :r"), {"r": revision_id}
    )
    record = propose_rights_record(
        session,
        disposition="metadata_only",
        determination_method="manual",
        proposed_at=NOW,
        rights_status="prohibited",
        symbol_revision_id=revision_id,
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=NOW,
        decided_by_user_id=actor,
        decision_reason="Rights holder refused permission to reproduce.",
    )
    session.flush()

    decision = evaluate_publication_gate(collect_publication_gate_facts(session, revision_id))
    assert decision.refusal_reasons == ("rights_disposition_withholds",)


def test_the_integrity_hash_is_found_in_each_of_its_three_homes(session):
    actor = _user(session, "gate-integrity")
    _symbol_id, revision_id = _symbol_revision(session, actor)

    # Nowhere yet.
    assert collect_publication_gate_facts(session, revision_id).final_asset_sha256 is None

    # 3. An attachment parented on the revision -- `runtime.py`'s shape.
    session.execute(
        text(
            "INSERT INTO attachments (id,parent_type,parent_id,filename,object_key,"
            "content_type,size_bytes,sha256,created_at) "
            "VALUES (:id,'symbol_revision',:parent,'a.svg',:key,'image/svg+xml',10,:d,:now)"
        ),
        {
            "id": uuid.uuid4(),
            "parent": revision_id,
            "key": f"o/{uuid.uuid4().hex}",
            "d": "d" * 64,
            "now": NOW,
        },
    )
    session.flush()
    facts = collect_publication_gate_facts(session, revision_id)
    assert (facts.final_asset_sha256, facts.final_asset_hash_source) == ("d" * 64, "attachment")

    # 2. The payload asset, which outranks an attachment.
    session.get(SymbolRevision, revision_id).payload_json = {
        "assets": [{"object_key": "o", "sha256": "e" * 64}]
    }
    session.flush()
    facts = collect_publication_gate_facts(session, revision_id)
    assert facts.final_asset_hash_source == "revision_payload_asset"

    # 1. SM-P0-06's transformation chain, which outranks both: its last step's
    # derived digest is by definition the asset SymGov ended up with.
    from symgov_backend.rights_provenance import record_asset_transformation

    record_asset_transformation(
        session,
        symbol_revision_id=revision_id,
        derived_asset_sha256="f" * 64,
        source_asset_sha256="0" * 64,
        tool_name="illustrative-converter",
        tool_version="1.0.0",
        performed_at=NOW,
    )
    session.flush()
    facts = collect_publication_gate_facts(session, revision_id)
    assert (facts.final_asset_sha256, facts.final_asset_hash_source) == (
        "f" * 64,
        "asset_transformation",
    )


# --------------------------------------------------------------------------
# Section 9.2's exception, as a service
# --------------------------------------------------------------------------


def test_only_semantic_identity_can_be_proposed_as_a_waiver(session):
    """Section 16.2 is the reason `rights` is refused here. The column accepts
    the name -- the vocabulary is storage -- and the service does not, which is
    the split 20260910_0056 settled on for `disposition_is_permitted`."""
    actor = _user(session, "gate-waiver-policy")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    for dimension in ("rights", "source", "integrity", "graphical_authority", "classification"):
        with pytest.raises(ValueError, match="cannot be waived"):
            propose_publication_gate_exception(
                session,
                symbol_revision_id=revision_id,
                dimension=dimension,
                proposed_at=NOW,
                proposed_by_user_id=actor,
            )
    record = propose_publication_gate_exception(
        session,
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        proposed_at=NOW,
        proposed_by_user_id=actor,
    )
    assert record.decision_status == "proposed"


def test_a_waiver_starts_proposed_and_does_not_yet_excuse_anything(session):
    actor = _user(session, "gate-waiver-proposed")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    propose_publication_gate_exception(
        session,
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        proposed_at=NOW,
        proposed_by_user_id=actor,
    )
    session.flush()
    assert approved_gate_exceptions(session, revision_id) == frozenset()


def test_approving_a_waiver_demands_a_named_approver_and_a_reason(session):
    actor = _user(session, "gate-waiver-approval")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    record = propose_publication_gate_exception(
        session,
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        proposed_at=NOW,
        proposed_by_user_id=actor,
    )
    session.flush()
    with pytest.raises(ValueError, match="named approver"):
        transition_publication_gate_exception(
            session, record.id, target_status="approved", occurred_at=NOW
        )
    with pytest.raises(ValueError, match="recorded reason"):
        transition_publication_gate_exception(
            session,
            record.id,
            target_status="approved",
            occurred_at=NOW,
            approved_by_user_id=actor,
        )
    transition_publication_gate_exception(
        session,
        record.id,
        target_status="approved",
        occurred_at=NOW,
        approved_by_user_id=actor,
        approval_reason="Annotation symbol; no engineering concept applies.",
    )
    session.flush()
    assert approved_gate_exceptions(session, revision_id) == frozenset({"semantic_identity"})


def test_approving_a_second_waiver_retires_the_first(session):
    """Supersession rather than refusal -- the shape SM-P0-01 through -06 use,
    and what keeps the partial unique index satisfiable."""
    actor = _user(session, "gate-waiver-supersede")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    records = []
    for _ in range(2):
        record = propose_publication_gate_exception(
            session,
            symbol_revision_id=revision_id,
            dimension="semantic_identity",
            proposed_at=NOW,
            proposed_by_user_id=actor,
        )
        session.flush()
        records.append(record)
    for record in records:
        transition_publication_gate_exception(
            session,
            record.id,
            target_status="approved",
            occurred_at=NOW,
            approved_by_user_id=actor,
            approval_reason="Annotation symbol.",
        )
        session.flush()
    assert records[0].decision_status == "retired"
    assert records[1].decision_status == "approved"
    assert approved_gate_exceptions(session, revision_id) == frozenset({"semantic_identity"})


def test_a_rejected_waiver_is_terminal(session):
    actor = _user(session, "gate-waiver-rejected")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    record = propose_publication_gate_exception(
        session,
        symbol_revision_id=revision_id,
        dimension="semantic_identity",
        proposed_at=NOW,
        proposed_by_user_id=actor,
    )
    session.flush()
    transition_publication_gate_exception(
        session,
        record.id,
        target_status="rejected",
        occurred_at=NOW,
        approved_by_user_id=actor,
    )
    session.flush()
    with pytest.raises(ValueError, match="cannot move from rejected"):
        transition_publication_gate_exception(
            session,
            record.id,
            target_status="approved",
            occurred_at=NOW,
            approved_by_user_id=actor,
            approval_reason="Changed my mind.",
        )


# --------------------------------------------------------------------------
# The decision is durable
# --------------------------------------------------------------------------


def _evaluations(session, revision_id) -> list[PublicationGateEvaluation]:
    return (
        session.query(PublicationGateEvaluation)
        .filter(PublicationGateEvaluation.symbol_revision_id == revision_id)
        .order_by(PublicationGateEvaluation.evaluated_at)
        .all()
    )


def test_the_decision_is_recorded_not_only_returned(session):
    actor = _user(session, "gate-durable")
    revision_id, package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    decision = enforce_publication_gate(
        session, symbol_revision_id=revision_id, evaluated_at=NOW, evaluated_by_user_id=actor
    )
    session.flush()

    (row,) = _evaluations(session, revision_id)
    assert row.outcome == decision.outcome == "permitted"
    assert row.in_scope is True
    assert row.source_package_id == package.id
    assert row.policy_version == PUBLICATION_GATE_POLICY_VERSION
    assert row.traceability_level == decision.traceability_level
    assert row.refusal_reasons_json == []
    assert [item["dimension"] for item in row.dimension_results_json] == [
        "semantic_identity",
        "source",
        "graphical_authority",
        "rights",
        "integrity",
        "classification",
    ]
    assert row.evaluated_by_user_id == actor


def test_a_grandfathered_evaluation_records_the_gaps_it_did_not_enforce(session):
    """Section 12.1's M6: "existing published symbols grandfathered with
    traceability gaps reported". `refusal_names_a_reason` constrains only
    `refused`, which is what lets this row carry both."""
    actor = _user(session, "gate-grandfather-record")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    package = _package(session, package_type="submission_sheet")
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_label="entry",
    )
    session.flush()

    decision = enforce_publication_gate(
        session, symbol_revision_id=revision_id, evaluated_at=NOW, evaluated_by_user_id=actor
    )
    session.flush()
    (row,) = _evaluations(session, revision_id)
    assert row.outcome == "not_in_scope"
    assert row.in_scope is False
    assert row.source_package_id is None
    assert set(row.refusal_reasons_json) == {
        "semantic_identity_unverified",
        "graphical_authority_unasserted",
        "rights_undecided",
        "final_asset_hash_absent",
        "classification_absent",
    }
    assert decision.permitted is True
    assert row.traceability_level == "T1"


def test_re_evaluating_records_a_second_row_rather_than_replacing_the_first(session):
    """Section 14.4 retains the governance decision history with the governed
    data, and why a symbol was refused earlier is part of it."""
    actor = _user(session, "gate-history")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    session.execute(
        text("DELETE FROM rights_records WHERE symbol_revision_id = :r"), {"r": revision_id}
    )
    session.flush()
    first = enforce_publication_gate(
        session, symbol_revision_id=revision_id, evaluated_at=NOW, evaluated_by_user_id=actor
    )
    assert first.outcome == "refused"

    record = propose_rights_record(
        session,
        disposition="distribute",
        determination_method="licence_document",
        proposed_at=NOW,
        rights_status="licensed",
        symbol_revision_id=revision_id,
        licence_reference="CONTRACT-GATE-001",
    )
    session.flush()
    transition_rights_record(
        session,
        record.id,
        target_status="approved",
        occurred_at=NOW,
        decided_by_user_id=actor,
        decision_reason="Contract located.",
    )
    session.flush()
    second = enforce_publication_gate(
        session,
        symbol_revision_id=revision_id,
        evaluated_at=NOW.replace(hour=11),
        evaluated_by_user_id=actor,
    )
    session.flush()

    rows = _evaluations(session, revision_id)
    assert [row.outcome for row in rows] == ["refused", "permitted"]
    assert second.outcome == "permitted"
    assert rows[0].refusal_reasons_json == ["rights_undecided"]


# --------------------------------------------------------------------------
# The real organisation-promotion path
# --------------------------------------------------------------------------


def _promotion_case(session, *, revision_id, symbol_id, reviewer, submitter):
    # `ck_organizations_code_format` requires normalized_code = lower(code).
    code = f"GATE{uuid.uuid4().hex[:6].upper()}"
    organization = Organization(
        id=uuid.uuid4(),
        code=code,
        normalized_code=code.lower(),
        display_name="Gate Test Organization",
        name_key=f"gate-test-{uuid.uuid4().hex[:8]}",
        fallback_icon_svg="<svg/>",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(organization)
    session.flush()

    symbol = session.get(GovernedSymbol, symbol_id)
    symbol.owner_organization_id = organization.id
    symbol.visibility = "organization_private"
    symbol.current_revision_id = revision_id
    session.flush()

    promotion_request = PromotionRequest(
        id=uuid.uuid4(),
        governed_symbol_id=symbol_id,
        organization_id=organization.id,
        symbol_revision_id=revision_id,
        status="in_review",
        reason="Broadly useful across organizations.",
        sharing_acknowledgment=True,
        submitted_by_user_id=submitter,
        submitted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(promotion_request)
    session.flush()

    review_case = ReviewCase(
        id=uuid.uuid4(),
        source_entity_type="organization_symbol_promotion",
        source_entity_id=promotion_request.id,
        current_stage="human_review",
        escalation_level="standard",
        opened_at=NOW,
    )
    session.add(review_case)
    session.flush()
    promotion_request.review_case_id = review_case.id

    decision = HumanReviewDecision(
        id=uuid.uuid4(),
        review_case_id=review_case.id,
        decision_code="approve",
        decision_summary="approved",
        decision_note="Approved for the SM-P0-08 rehearsal.",
        decided_by=reviewer,
        decider_name="Reviewer",
        decider_role="reviewer",
        from_stage="human_review",
        to_stage="approved",
        created_at=NOW,
    )
    session.add(decision)
    action = ReviewCaseAction(
        id=uuid.uuid4(),
        review_case_id=review_case.id,
        decision_id=decision.id,
        action_code="prepare_publication_handoff",
        action_status="queued",
        target_agent_slug="rupert",
        action_payload_json={},
        created_by_type="service",
        created_at=NOW,
    )
    session.add(action)
    session.flush()
    return review_case, decision, action


def test_the_gate_refuses_a_real_promotion_and_leaves_the_symbol_private(session):
    actor = _user(session, "gate-promotion-refuse")
    reviewer = _user(session, "gate-promotion-reviewer")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    session.execute(
        text("DELETE FROM rights_records WHERE symbol_revision_id = :r"), {"r": revision_id}
    )
    session.flush()
    revision = session.get(SymbolRevision, revision_id)
    review_case, decision, action = _promotion_case(
        session,
        revision_id=revision_id,
        symbol_id=revision.symbol_id,
        reviewer=reviewer,
        submitter=actor,
    )

    result = execute_organization_promotion_handoff(
        session,
        review_case=review_case,
        decision=decision,
        action=action,
        approval_actor={"id": str(reviewer), "type": "user"},
        commit_transaction=False,
    )
    assert result["status"] == "failed"
    assert "rights_undecided" in result["detail"]

    symbol = session.get(GovernedSymbol, revision.symbol_id)
    assert symbol.visibility == "organization_private"
    assert symbol.catalog_symbol_id is None
    assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"
    # Refused, and the refusal is on the record.
    assert _evaluations(session, revision_id)[-1].outcome == "refused"


def test_a_satisfied_authoritative_revision_promotes_through_the_same_path(session):
    actor = _user(session, "gate-promotion-permit")
    reviewer = _user(session, "gate-promotion-permit-reviewer")
    revision_id, _package = _satisfy_all_dimensions(
        session, actor, package_type="authoritative_library"
    )
    revision = session.get(SymbolRevision, revision_id)
    review_case, decision, action = _promotion_case(
        session,
        revision_id=revision_id,
        symbol_id=revision.symbol_id,
        reviewer=reviewer,
        submitter=actor,
    )

    result = execute_organization_promotion_handoff(
        session,
        review_case=review_case,
        decision=decision,
        action=action,
        approval_actor={"id": str(reviewer), "type": "user"},
        commit_transaction=False,
    )
    assert result["status"] == "completed", result
    symbol = session.get(GovernedSymbol, revision.symbol_id)
    assert symbol.visibility == "public"
    assert symbol.catalog_symbol_id is not None
    assert _evaluations(session, revision_id)[-1].outcome == "permitted"


def test_a_grandfathered_symbol_still_promotes_and_says_why_it_was_exempt(session):
    """Section 12.3 and section 17: nothing that could publish before this
    package becomes unpublishable by it."""
    actor = _user(session, "gate-promotion-grandfather")
    reviewer = _user(session, "gate-promotion-grandfather-reviewer")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    package = _package(session, package_type="submission_sheet")
    add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision_id,
        added_at=NOW,
        source_label="entry",
    )
    session.flush()
    revision = session.get(SymbolRevision, revision_id)
    review_case, decision, action = _promotion_case(
        session,
        revision_id=revision_id,
        symbol_id=revision.symbol_id,
        reviewer=reviewer,
        submitter=actor,
    )

    result = execute_organization_promotion_handoff(
        session,
        review_case=review_case,
        decision=decision,
        action=action,
        approval_actor={"id": str(reviewer), "type": "user"},
        commit_transaction=False,
    )
    # A revision satisfying not one section 9.2 dimension beyond `source`
    # still publishes, because section 17 grandfathers it.
    assert result["status"] == "completed", result
    assert session.get(GovernedSymbol, revision.symbol_id).visibility == "public"

    row = _evaluations(session, revision_id)[-1]
    assert row.outcome == "not_in_scope"
    assert row.refusal_reasons_json  # exempt, not silently passing
    assert row.in_scope is False


def test_publishing_does_not_disturb_an_already_published_symbol(session):
    """Section 12.3: existing published symbols are unaffected. A second
    promotion of a symbol already public is refused by the path's own
    pre-existing eligibility check, before the gate is reached, so no
    evaluation row is written for it either."""
    actor = _user(session, "gate-already-public")
    reviewer = _user(session, "gate-already-public-reviewer")
    _symbol_id, revision_id = _symbol_revision(session, actor)
    revision = session.get(SymbolRevision, revision_id)
    review_case, decision, action = _promotion_case(
        session,
        revision_id=revision_id,
        symbol_id=revision.symbol_id,
        reviewer=reviewer,
        submitter=actor,
    )
    symbol = session.get(GovernedSymbol, revision.symbol_id)
    symbol.visibility = "public"
    session.flush()

    result = execute_organization_promotion_handoff(
        session,
        review_case=review_case,
        decision=decision,
        action=action,
        approval_actor={"id": str(reviewer), "type": "user"},
        commit_transaction=False,
    )
    assert result["status"] == "failed"
    assert _evaluations(session, revision_id) == []
    assert session.get(SymbolRevision, revision_id).lifecycle_state == "draft"


# --------------------------------------------------------------------------
# The intake rights assessment, made durable
# --------------------------------------------------------------------------


def _rupert_case(session, *, label: str, rights_disposition: str | None):
    """The smallest promotable review case, after
    `test_classification_mapping_postgresql._seed_case`, plus the provenance
    assessment that file did not need."""
    agent = AgentDefinition(
        id=uuid.uuid4(),
        slug=f"libby-{label}-{uuid.uuid4().hex[:6]}",
        display_name="Libby",
        role="classification",
        model="test",
        status="active",
        queue_family="classification",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(agent)
    session.flush()
    queue_item = AgentQueueItem(
        id=uuid.uuid4(),
        agent_id=agent.id,
        source_type="intake_record",
        source_id=uuid.uuid4(),
        status="completed",
        priority="normal",
        payload_json={},
        created_at=NOW,
    )
    session.add(queue_item)
    session.flush()

    package = _package(session, package_type="submission_sheet")
    intake = IntakeRecord(
        id=uuid.uuid4(),
        queue_item_id=queue_item.id,
        source_type="submission",
        source_ref=f"{label}-ref",
        submitter="contributor@example.test",
        submission_kind="single_symbol",
        intake_status="accepted",
        eligibility_status="eligible",
        source_package_id=package.id,
        raw_object_key=f"raw/{label}.svg",
        normalized_submission_json={"original_filename": f"{label}.svg", "candidate_title": label},
        routing_recommendation_json={},
        report_json={},
        created_at=NOW,
    )
    session.add(intake)
    session.flush()
    validation = ValidationReport(
        id=uuid.uuid4(),
        queue_item_id=queue_item.id,
        source_type="intake_record",
        source_id=intake.id,
        validation_status="pass",
        defect_count=0,
        normalized_payload_json={},
        report_json={},
        created_at=NOW,
    )
    session.add(validation)
    session.flush()
    review_case = ReviewCase(
        id=uuid.uuid4(),
        source_entity_type="validation_report",
        source_entity_id=validation.id,
        current_stage="classification_review",
        escalation_level="standard",
        opened_at=NOW,
    )
    session.add(review_case)
    session.flush()
    session.add(
        ClassificationRecord(
            id=uuid.uuid4(),
            queue_item_id=queue_item.id,
            intake_record_id=intake.id,
            validation_report_id=validation.id,
            review_case_id=review_case.id,
            symbol_key=label,
            status="current",
            classification_status="provisional",
            source_id=intake.id,
            source_type="intake_record",
            confidence=Decimal("0.84"),
            libby_approved=False,
            category="Valves",
            discipline="Mechanical",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    if rights_disposition is not None:
        session.add(
            ProvenanceAssessment(
                id=uuid.uuid4(),
                queue_item_id=queue_item.id,
                intake_record_id=intake.id,
                rights_status="cleared",
                rights_disposition=rights_disposition,
                processing_outcome="pass",
                risk_level="low",
                confidence=Decimal("0.9"),
                summary="Illustrative provenance assessment.",
                evidence_json={},
                report_json={},
                assessed_at=NOW,
            )
        )
    decision = HumanReviewDecision(
        id=uuid.uuid4(),
        review_case_id=review_case.id,
        decision_code="approve",
        decision_summary="approved",
        decision_note="Approved for the SM-P0-08 rehearsal.",
        decider_name="Reviewer",
        decider_role="reviewer",
        from_stage="classification_review",
        to_stage="approved",
        created_at=NOW,
    )
    session.add(decision)
    session.flush()
    return review_case, decision


@pytest.mark.parametrize(
    "intake_disposition, expected",
    [
        ("cleared", "display"),
        ("unknown_warning", "metadata_only"),
        ("restricted", "metadata_only"),
        ("conflict", "reject"),
        ("failed", "metadata_only"),
    ],
)
def test_promotion_seeds_a_proposed_rights_record(session, intake_disposition, expected):
    """Nothing in production wrote a `RightsRecord` before this package, so
    section 9.2's rights dimension could never pass for any symbol and the
    reviewer who must resolve it started from a blank page."""
    review_case, decision = _rupert_case(
        session, label="rights", rights_disposition=intake_disposition
    )
    revision = ensure_approved_symbol_revision(
        session, review_case=review_case, decision=decision
    )
    session.flush()

    record = (
        session.query(RightsRecord)
        .filter(RightsRecord.symbol_revision_id == revision.id)
        .one()
    )
    assert record.disposition == expected
    # Always `unknown`: an intake assessment never reads a licence, so it
    # cannot establish `open` or `licensed`.
    assert record.rights_status == "unknown"
    assert record.decision_status == "proposed"
    assert record.determination_method == "ai_assisted"
    assert record.evidence_json["intake_rights_disposition"] == intake_disposition
    assert revision.payload_json["rights_proposal"]["status"] == "proposed"


def test_the_seeded_proposal_can_never_approve_itself(session):
    """Section 8.4, enforced twice: by `rights_records`'
    `approved_not_ai_determined` constraint and by
    `transition_rights_record`."""
    review_case, decision = _rupert_case(session, label="rights-approve", rights_disposition="cleared")
    revision = ensure_approved_symbol_revision(
        session, review_case=review_case, decision=decision
    )
    session.flush()
    record = (
        session.query(RightsRecord)
        .filter(RightsRecord.symbol_revision_id == revision.id)
        .one()
    )
    actor = _user(session, "gate-rights-approver")
    with pytest.raises(ValueError, match="cannot be approved"):
        transition_rights_record(
            session,
            record.id,
            target_status="approved",
            occurred_at=NOW,
            decided_by_user_id=actor,
            decision_reason="Looks fine to me.",
        )


def test_a_promotion_with_no_provenance_assessment_still_promotes(session):
    """SM-P0-07's rule holds for this proposal too: it is a proposal, not the
    gate, and must not stop a reviewed symbol from being promoted."""
    review_case, decision = _rupert_case(session, label="rights-none", rights_disposition=None)
    revision = ensure_approved_symbol_revision(
        session, review_case=review_case, decision=decision
    )
    session.flush()
    assert revision.lifecycle_state == "approved"
    assert (
        session.query(RightsRecord)
        .filter(RightsRecord.symbol_revision_id == revision.id)
        .count()
        == 0
    )
    assert revision.payload_json["rights_proposal"]["status"] == "skipped"


def test_a_second_promotion_does_not_duplicate_or_overwrite_the_proposal(session):
    review_case, decision = _rupert_case(session, label="rights-twice", rights_disposition="cleared")
    first = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
    session.flush()
    record_id = (
        session.query(RightsRecord.id)
        .filter(RightsRecord.symbol_revision_id == first.id)
        .scalar()
    )
    second = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
    session.flush()
    assert second.id == first.id
    rows = session.query(RightsRecord).filter(RightsRecord.symbol_revision_id == first.id).all()
    assert [row.id for row in rows] == [record_id]
    assert second.payload_json["rights_proposal"]["status"] == "skipped"
