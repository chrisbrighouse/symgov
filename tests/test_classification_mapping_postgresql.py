"""Real-PostgreSQL cover for SM-P0-07: promotion writes governed proposals.

The mapping *rules* are proved DB-free in `test_classification_mapping.py`.
What needs a real server is everything about the write: that promoting a
reviewed symbol actually creates `symbol_revision_classifications` rows
against the seeded schemes, that a record whose every value is unmappable
still promotes, that the child-split path reaches its own classification
record rather than the parent sheet's, that nothing lands `verified`, that
the legacy `GovernedSymbol.category`/`.discipline` columns receive
byte-identical values before and after this package -- the SM-P0-09 line --
and that a second promotion of the same decision creates no second proposal.

Migration head is unchanged by SM-P0-07: no table, column or constraint is
added, so no `*_postgresql.py` fixture constant moves and no sole-head
assertion changes. The package is wiring, not schema.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.classification_mapping import MAPPING_METHOD  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    AgentDefinition,
    AgentQueueItem,
    ClassificationNode,
    ClassificationRecord,
    ClassificationScheme,
    GovernedSymbol,
    HumanReviewDecision,
    IntakeRecord,
    ReviewCase,
    ReviewSplitItem,
    ReviewSymbolProperty,
    SourcePackage,
    SourcePackageEntry,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
    SymbolStandardLink,
    ValidationReport,
)
from symgov_backend.publication_handoff import (  # noqa: E402
    ensure_approved_child_symbol_revision,
    ensure_approved_symbol_revision,
)
from symgov_backend.rights_provenance import symbol_revision_rights_provenance  # noqa: E402

NOW = datetime(2026, 9, 11, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def session_factory():
    with _database("symgov-sm-p0-07") as (engine, url, _raw):
        _alembic(url, "upgrade", "head")
        yield sessionmaker(bind=engine, expire_on_commit=False)


def _seed_case(
    session,
    *,
    label: str,
    classification: dict | None,
    with_package: bool = True,
    reviewed_properties: dict | None = None,
) -> tuple[ReviewCase, HumanReviewDecision]:
    """Build the smallest promotable review case the handoff will accept."""
    agent = AgentDefinition(
        id=uuid.uuid4(),
        slug=f"libby-{label}",
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

    package = None
    if with_package:
        package = SourcePackage(
            id=uuid.uuid4(),
            package_code=f"{abs(hash(label)) % 0x10000:04X}",
            title=f"{label} submission",
            provider="contributor@example.test",
            package_type="submission_sheet",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(package)
        session.flush()

    intake = IntakeRecord(
        id=uuid.uuid4(),
        queue_item_id=queue_item.id,
        source_type="submission",
        source_ref=f"{label}-ref",
        submitter="contributor@example.test",
        submission_kind="single_symbol",
        intake_status="accepted",
        eligibility_status="eligible",
        source_package_id=package.id if package else None,
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

    if classification is not None:
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
                created_at=NOW,
                updated_at=NOW,
                **classification,
            )
        )

    if reviewed_properties is not None:
        session.add(
            ReviewSymbolProperty(
                id=uuid.uuid4(),
                review_case_id=review_case.id,
                symbol_record_key=str(review_case.id),
                name=reviewed_properties.get("name", label),
                description=reviewed_properties.get("description", ""),
                category=reviewed_properties.get("category"),
                discipline=reviewed_properties.get("discipline"),
                created_at=NOW,
                updated_at=NOW,
            )
        )

    decision = HumanReviewDecision(
        id=uuid.uuid4(),
        review_case_id=review_case.id,
        decision_code="approve",
        decision_summary="approved",
        decision_note="Approved for the SM-P0-07 rehearsal.",
        decider_name="Reviewer",
        decider_role="reviewer",
        from_stage="classification_review",
        to_stage="approved",
        created_at=NOW,
    )
    session.add(decision)
    session.flush()
    return review_case, decision


def _assignments(session, revision_id) -> list[SymbolRevisionClassificationAssignment]:
    return (
        session.query(SymbolRevisionClassificationAssignment)
        .filter(SymbolRevisionClassificationAssignment.symbol_revision_id == revision_id)
        .all()
    )


def _scheme_of(session, assignment) -> str:
    return (
        session.query(ClassificationScheme.scheme_code)
        .filter(ClassificationScheme.id == assignment.classification_scheme_id)
        .scalar()
    )


def _node_of(session, assignment) -> ClassificationNode:
    return session.get(ClassificationNode, assignment.classification_node_id)


FULL_CLASSIFICATION = {
    "category": "Valves",
    "discipline": "Mechanical",
    "industry": "process_engineering",
    "symbol_family": "door",
    "process_category": "flow_control",
    "parent_equipment_class": "valve",
    "standards_source": "https://example.test/isa-5-1",
    "library_provenance_class": "contributor_submission",
    "source_classification": "contributor_asserted",
    "format": "svg",
    "aliases_json": ["Gate valve"],
    "search_terms_json": ["valve", "gate"],
    "source_refs_json": ["https://example.test/source"],
}


def test_promotion_creates_proposed_assignments_against_the_seeded_schemes(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="full", classification=FULL_CLASSIFICATION)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        rows = _assignments(session, revision.id)
        assert len(rows) == 3
        by_scheme = {}
        for row in rows:
            by_scheme.setdefault(_scheme_of(session, row), []).append(row)
        assert set(by_scheme) == {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"}
        (discipline_row,) = by_scheme["ENGINEERING-DISCIPLINE"]
        assert _node_of(session, discipline_row).preferred_label == "Mechanical"
        assert discipline_row.assignment_role == "primary"

        labels = {
            _node_of(session, row).preferred_label: row.assignment_role
            for row in by_scheme["SYMBOL-CATEGORY-FAMILY"]
        }
        # `door` matched the seeded `Doors` through the plural variant.
        assert labels == {"Valves": "primary", "Doors": "secondary"}


def test_no_assignment_this_package_writes_is_verified(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="governance", classification=FULL_CLASSIFICATION)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        rows = _assignments(session, revision.id)
        assert rows
        for row in rows:
            assert row.status == "proposed"
            assert row.method == MAPPING_METHOD
            assert row.method != "legacy_backfill"
            assert row.reviewed_by_user_id is None
            assert row.confidence == Decimal("0.8400")


def test_every_field_is_either_assigned_or_reported_in_the_payload(session_factory):
    """Section 16.1: no section 9.3 field is silently dropped."""
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="report", classification=FULL_CLASSIFICATION)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        report = revision.payload_json["classification_mapping"]
        assert report["status"] == "mapped"
        accounted = (
            {row["field"] for row in report["assignments"]}
            | {row["field"] for row in report["resolved"]}
            | {gap["field"] for gap in report["gaps"]}
        )
        for field in (
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
        ):
            assert field in accounted, field

        # And the four that reached no payload at all before this package.
        stored = revision.payload_json["classification"]
        assert stored["industry"] == "process_engineering"
        assert stored["standards_source"] == "https://example.test/isa-5-1"
        assert stored["library_provenance_class"] == "contributor_submission"
        assert stored["format"] == "svg"


def test_the_raw_value_of_an_unmapped_field_survives_in_evidence(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="evidence", classification=FULL_CLASSIFICATION)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        gaps = {gap["field"]: gap for gap in revision.payload_json["classification_mapping"]["gaps"]}
        assert gaps["industry"]["raw_value"] == "process_engineering"
        assert gaps["industry"]["reason"] == "no_scheme"
        assert gaps["processCategory"]["raw_value"] == "flow_control"
        assert gaps["parentEquipmentClass"]["reason"] == "no_relationship_table"


def test_an_entirely_unmappable_classification_still_promotes(session_factory):
    """Decided 2026-09-11: a mapping failure never blocks a promotion."""
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="unmappable",
            classification={
                "category": "wholly-unknown-category",
                "discipline": "wholly-unknown-discipline",
                "industry": "general_industry",
                "symbol_family": "candidate:sg-000123",
                "standards_source": "not a standard code at all",
                "library_provenance_class": "internet_research_candidate",
            },
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        assert revision.lifecycle_state == "approved"
        symbol = session.get(GovernedSymbol, revision.symbol_id)
        assert symbol.current_revision_id == revision.id
        assert _assignments(session, revision.id) == []
        report = revision.payload_json["classification_mapping"]
        assert report["status"] == "mapped"
        assert report["assignments"] == []
        reasons = {gap["field"]: gap["reason"] for gap in report["gaps"]}
        assert reasons["engineeringDiscipline"] == "no_node_match"
        assert reasons["standardsSource"] == "no_standard_match"


def test_the_legacy_columns_receive_exactly_what_they_did_before(session_factory):
    """The SM-P0-09 line, pinned. `category` and `discipline` come from the
    reviewed property first and the classification record second, with the
    same `symbol`/`general` fallbacks -- unchanged by this package."""
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="legacy",
            classification=FULL_CLASSIFICATION,
            reviewed_properties={"category": "Pumps", "discipline": "Process", "name": "Reviewed name"},
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        symbol = session.get(GovernedSymbol, revision.symbol_id)
        assert symbol.category == "Pumps"
        assert symbol.discipline == "Process"
        assert symbol.canonical_name == "Reviewed name"

    with session_factory() as session:
        review_case, decision = _seed_case(session, label="legacy-fallback", classification=None)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        symbol = session.get(GovernedSymbol, revision.symbol_id)
        assert symbol.category == "symbol"
        assert symbol.discipline == "general"


def test_the_structured_primary_follows_the_reviewed_value_not_the_record(session_factory):
    """Same precedence as the legacy column, so SM-P0-09 can later derive one
    from the other without either value moving."""
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="precedence",
            classification=FULL_CLASSIFICATION,
            reviewed_properties={"category": "Pumps", "discipline": "Process"},
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        primaries = {
            _scheme_of(session, row): _node_of(session, row).preferred_label
            for row in _assignments(session, revision.id)
            if row.assignment_role == "primary"
        }
        assert primaries["ENGINEERING-DISCIPLINE"] == "Process"
        assert primaries["SYMBOL-CATEGORY-FAMILY"] == "Pumps"


def test_promoting_the_same_decision_twice_creates_no_second_proposal(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="idempotent", classification=FULL_CLASSIFICATION)
        first = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()
        before = len(_assignments(session, first.id))

        second = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        assert second.id == first.id
        assert len(_assignments(session, first.id)) == before
        assert (
            session.query(SourcePackageEntry)
            .filter(SourcePackageEntry.symbol_revision_id == first.id)
            .count()
            == 1
        )


def test_promotion_places_the_revision_in_its_source_package(session_factory):
    """Nothing created a `SourcePackageEntry` before SM-P0-07, which is why
    SM-P0-06's rights report found nothing for a promoted symbol."""
    with session_factory() as session:
        review_case, decision = _seed_case(session, label="package", classification=FULL_CLASSIFICATION)
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        entry = (
            session.query(SourcePackageEntry)
            .filter(SourcePackageEntry.symbol_revision_id == revision.id)
            .one()
        )
        assert entry.source_label == "contributor_submission"

        provenance = symbol_revision_rights_provenance(session, revision.id)
        assert provenance["source_packages"]


def test_a_submission_with_no_package_reports_the_gap_rather_than_failing(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(
            session, label="nopackage", classification=FULL_CLASSIFICATION, with_package=False
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        gaps = {gap["field"]: gap["reason"] for gap in revision.payload_json["classification_mapping"]["gaps"]}
        assert gaps["libraryProvenanceClass"] == "no_source_package"
        assert (
            session.query(SourcePackageEntry)
            .filter(SourcePackageEntry.symbol_revision_id == revision.id)
            .count()
            == 0
        )


def test_a_standards_source_that_names_no_registered_standard_is_a_gap(session_factory):
    """The 219 seeded CFIHOS standards are real; `API SPEC 6D` is taken, so
    this asserts the miss rather than registering a synthetic standard."""
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="standard-miss",
            classification={**FULL_CLASSIFICATION, "standards_source": "ZZZ-NOT-A-STANDARD-9999"},
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        gaps = {gap["field"]: gap for gap in revision.payload_json["classification_mapping"]["gaps"]}
        assert gaps["standardsSource"]["reason"] == "no_standard_match"
        assert (
            session.query(SymbolStandardLink)
            .filter(SymbolStandardLink.symbol_revision_id == revision.id)
            .count()
            == 0
        )


def test_the_child_split_path_uses_its_own_classification_record(session_factory):
    """Q5, decided 2026-09-11: the child's own record, never the parent
    sheet's sheet-level placeholders."""
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="split",
            classification={
                **FULL_CLASSIFICATION,
                # what Libby writes for a raster sheet
                "category": "symbol_sheet",
                "symbol_family": "mixed_symbol_set",
                "process_category": "review_required",
                "parent_equipment_class": "mixed_equipment",
            },
        )
        split_item = ReviewSplitItem(
            id=uuid.uuid4(),
            review_case_id=review_case.id,
            child_key="child-1",
            proposed_symbol_id="SG-CHILD-1",
            proposed_symbol_name="Child one",
            file_name="child-1.svg",
            parent_file_name="split.svg",
            attachment_object_key="raw/child-1.svg",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(split_item)

        child_record = ClassificationRecord(
            id=uuid.uuid4(),
            review_case_id=None,
            parent_review_case_id=review_case.id,
            symbol_key="child-1",
            symbol_region_index=0,
            status="current",
            classification_status="provisional",
            source_id=review_case.id,
            source_type="review_case",
            category="Pumps",
            discipline="Process",
            symbol_family="valve",
            process_category="flow_control",
            parent_equipment_class="valve",
            industry="process_engineering",
            confidence=Decimal("0.77"),
            libby_approved=False,
            created_at=NOW + timedelta(minutes=1),
            updated_at=NOW + timedelta(minutes=1),
        )
        session.add(child_record)
        session.flush()

        revision = ensure_approved_child_symbol_revision(
            session,
            review_case=review_case,
            decision=decision,
            child_decision={"childId": "child-1", "proposedSymbolId": "SG-CHILD-1"},
            child_manifest={"proposed_symbol_id": "SG-CHILD-1", "file_name": "child-1.svg"},
            index=0,
        )
        session.commit()

        stored = revision.payload_json["classification"]
        # Previously hardcoded to None even where a record existed.
        assert stored["symbol_family"] == "valve"
        assert stored["process_category"] == "flow_control"
        assert stored["parent_equipment_class"] == "valve"
        # And never the parent sheet's placeholders.
        assert stored["symbol_family"] != "mixed_symbol_set"
        assert revision.payload_json["classification_record_id"] == str(child_record.id)

        labels = {
            _node_of(session, row).preferred_label
            for row in _assignments(session, revision.id)
        }
        assert "Valves" in labels


def test_a_child_with_no_record_of_its_own_inherits_nothing(session_factory):
    with session_factory() as session:
        review_case, decision = _seed_case(
            session,
            label="split-orphan",
            classification={
                **FULL_CLASSIFICATION,
                "symbol_family": "mixed_symbol_set",
                "process_category": "review_required",
                "parent_equipment_class": "mixed_equipment",
            },
        )
        revision = ensure_approved_child_symbol_revision(
            session,
            review_case=review_case,
            decision=decision,
            child_decision={"childId": "orphan-1", "proposedSymbolId": "SG-ORPHAN-1"},
            child_manifest=None,
            index=0,
        )
        session.commit()

        stored = revision.payload_json["classification"]
        assert stored["symbol_family"] is None
        assert stored["process_category"] is None
        assert stored["parent_equipment_class"] is None
        assert revision.payload_json["classification_record_id"] is None


def _register_standard(session, *, code: str, editions: int) -> None:
    """Register a synthetic standard with `editions` active versions.

    Synthetic because the database holds the 219 seeded CFIHOS standards and
    a real code such as `API SPEC 6D` is already taken.
    """
    from symgov_backend.standard_sources import register_standard, register_standard_version

    standard = register_standard(
        session,
        standard_code=code,
        title=f"{code} synthetic standard",
        issuing_body="SymGov Test Body",
        registered_at=NOW,
    )
    session.flush()
    for index in range(editions):
        register_standard_version(
            session,
            standard_id=standard.id,
            version_label=f"{2020 + index}",
            registered_at=NOW,
        )
    session.flush()


def test_a_standards_source_naming_one_edition_is_asserted_as_a_proposed_link(session_factory):
    with session_factory() as session:
        _register_standard(session, code="SMP007-SINGLE-1", editions=1)
        review_case, decision = _seed_case(
            session,
            label="standard-hit",
            classification={**FULL_CLASSIFICATION, "standards_source": "SMP007-SINGLE-1"},
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        link = (
            session.query(SymbolStandardLink)
            .filter(SymbolStandardLink.symbol_revision_id == revision.id)
            .one()
        )
        assert link.assertion_status == "proposed"
        assert link.relationship_type == "derived_from"
        # Section 7.10's verification is a separate act; nothing here verified.
        assert link.verification_method is None
        assert link.verified_by_user_id is None
        assert link.evidence_json["match_basis"] == "exact_standard_code"

        resolved = {row["field"] for row in revision.payload_json["classification_mapping"]["resolved"]}
        assert "standardsSource" in resolved


def test_a_standard_with_two_active_editions_is_ambiguous_not_guessed(session_factory):
    """Picking the newest edition would put a guess in a governed table."""
    with session_factory() as session:
        _register_standard(session, code="SMP007-MULTI-1", editions=2)
        review_case, decision = _seed_case(
            session,
            label="standard-ambiguous",
            classification={**FULL_CLASSIFICATION, "standards_source": "SMP007-MULTI-1"},
        )
        revision = ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)
        session.commit()

        gaps = {gap["field"]: gap for gap in revision.payload_json["classification_mapping"]["gaps"]}
        assert gaps["standardsSource"]["reason"] == "ambiguous_standard_version"
        assert "2 active editions" in gaps["standardsSource"]["detail"]
        assert (
            session.query(SymbolStandardLink)
            .filter(SymbolStandardLink.symbol_revision_id == revision.id)
            .count()
            == 0
        )
