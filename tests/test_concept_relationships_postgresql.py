"""Migration and service rehearsal for SM-P1-03 WP3.2 against a real PostgreSQL server.

Why this file exists: everything that makes section 7.3 trustworthy is
server-side -- the relationship-type and method vocabularies, the
`source <> target` refusal, the review-decision constraint, and the partial
unique index that allows one *live* assertion per (source, target, type) while
letting the rejected and retired history stand beside it. A DB-free test can
only assert that the migration text mentions them. This file upgrades a
disposable database to `20260917_0060`, proves each one rejects what it was
written to reject, drives the service against stored rows, and proves the
downgrade is clean.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.concept_relationships import (  # noqa: E402
    broader_concept_ids,
    find_concept_relationships_to,
    list_concept_relationships,
    propose_concept_relationship,
    transition_concept_relationship,
    verified_broader_concept_ids,
)
from symgov_backend.models import SemanticConcept, SemanticConceptRelationship  # noqa: E402
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402

RELATIONSHIP_REVISION = "20260917_0060"
PREVIOUS_REVISION = "20260916_0059"

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)


@pytest.fixture(scope="module")
def relationship_database():
    with _database("symgov-concept-relationships") as (engine, url, raw_url):
        _alembic(url, "upgrade", RELATIONSHIP_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(relationship_database) -> uuid.UUID:
    engine, _ = relationship_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "relationship-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(relationship_database) -> Session:
    engine, _ = relationship_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


def _concept(session: Session, author_id: uuid.UUID, name: str) -> SemanticConcept:
    concept, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name=name,
        definition=f"{name}, seeded for the section 7.3 rehearsal.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    return concept


def _insert(session: Session, **overrides) -> None:
    """Insert a row past the service, so a check constraint is what refuses it."""
    row = {
        "id": uuid.uuid4(),
        "relationship_type": "broader",
        "status": "proposed",
        "method": "manual",
        "confidence": None,
        "evidence_json": "{}",
        "reviewed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    session.execute(
        text(
            "INSERT INTO semantic_concept_relationships "
            "(id,source_concept_id,target_concept_id,relationship_type,status,method,"
            "confidence,evidence_json,created_at,updated_at,reviewed_at) VALUES "
            "(:id,:source_concept_id,:target_concept_id,:relationship_type,:status,:method,"
            ":confidence,cast(:evidence_json as jsonb),:created_at,:updated_at,:reviewed_at)"
        ),
        row,
    )


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


def test_upgrade_creates_the_table_and_its_three_indexes(relationship_database):
    engine, _ = relationship_database
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name='semantic_concept_relationships'"
            )
        ).scalar_one() == 1
        indexes = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='semantic_concept_relationships'"
                )
            ).scalars()
        )
    assert {
        "pk_semantic_concept_relationships",
        "ix_semantic_concept_relationships_source_status",
        "ix_semantic_concept_relationships_target_status",
        "uq_semantic_concept_relationships_active",
    } <= indexes


def test_every_identifier_the_migration_created_fits_the_limit(relationship_database):
    """PostgreSQL truncates at 63 characters silently, so an over-long name
    would exist only in the database and match nothing the ORM emits."""
    engine, _ = relationship_database
    with engine.begin() as connection:
        names = list(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = "
                    "'semantic_concept_relationships'::regclass"
                )
            ).scalars()
        )
    assert names
    assert [name for name in names if len(name) > 63] == []
    assert [name for name in names if name.startswith("ck_semantic_concept_relationships_ck_")] == []


# --------------------------------------------------------------------------
# Storage refuses what the specification forbids
# --------------------------------------------------------------------------


def test_a_concept_cannot_be_related_to_itself(session, author_id):
    concept = _concept(session, author_id, "Self relating concept")
    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            session,
            source_concept_id=concept.id,
            target_concept_id=concept.id,
        )
    assert "distinct_concepts" in str(excinfo.value)


@pytest.mark.parametrize(
    "column,value,constraint",
    [
        ("relationship_type", "parent_of", "relationship_type"),
        ("status", "approved", "status"),
        ("method", "legacy_backfill", "method"),
        ("confidence", 1.5, "confidence"),
    ],
)
def test_storage_refuses_a_value_outside_the_vocabulary(
    session, author_id, column, value, constraint
):
    source = _concept(session, author_id, f"Source for {column}")
    target = _concept(session, author_id, f"Target for {column}")
    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            session,
            source_concept_id=source.id,
            target_concept_id=target.id,
            **{column: value},
        )
    assert constraint in str(excinfo.value)


def test_a_verified_row_must_record_when_it_was_decided(session, author_id):
    source = _concept(session, author_id, "Decided source")
    target = _concept(session, author_id, "Decided target")
    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            session,
            source_concept_id=source.id,
            target_concept_id=target.id,
            status="verified",
            reviewed_at=None,
        )
    assert "review_decision" in str(excinfo.value)


def test_evidence_must_be_a_json_object(session, author_id):
    source = _concept(session, author_id, "Evidence source")
    target = _concept(session, author_id, "Evidence target")
    with pytest.raises(IntegrityError) as excinfo:
        _insert(
            session,
            source_concept_id=source.id,
            target_concept_id=target.id,
            evidence_json="[]",
        )
    assert "evidence_json_object" in str(excinfo.value)


def test_one_live_assertion_per_triple_but_history_survives(session, author_id):
    """The partial unique index covers `proposed` and `verified` only, so a
    rejected assertion stays in the record beside its replacement."""
    source = _concept(session, author_id, "Ball valve")
    target = _concept(session, author_id, "Valve")

    first = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="broader",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()

    # A savepoint, so the refusal does not take the seeded concepts with it.
    with pytest.raises(IntegrityError) as excinfo:
        with session.begin_nested():
            propose_concept_relationship(
                session,
                source_concept_id=source.id,
                target_concept_id=target.id,
                relationship_type="broader",
                method="manual",
                proposed_at=NOW,
                proposed_by_user_id=author_id,
            )
    assert "uq_semantic_concept_relationships_active" in str(excinfo.value)

    # Reject the first, and the same triple may be asserted again.
    transition_concept_relationship(
        session,
        first.id,
        target_status="rejected",
        occurred_at=LATER,
        reviewed_by_user_id=author_id,
    )
    session.flush()
    successor = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="broader",
        method="manual",
        proposed_at=LATER,
        proposed_by_user_id=author_id,
    )
    session.flush()
    assert successor.id != first.id
    assert first.status == "rejected"


def test_the_opposite_direction_is_a_separate_assertion(session, author_id):
    """Section 7.3 ships both directions, so `narrower(B, A)` is neither
    created by, nor refused as a duplicate of, `broader(A, B)`."""
    source = _concept(session, author_id, "Gate valve")
    target = _concept(session, author_id, "Isolation valve")

    propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="broader",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()

    # Nothing was minted in the other direction.
    assert list_concept_relationships(session, target.id, direction="outgoing") == []

    inverse = propose_concept_relationship(
        session,
        source_concept_id=target.id,
        target_concept_id=source.id,
        relationship_type="narrower",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    assert inverse.status == "proposed"


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


def test_a_proposal_starts_proposed_whatever_its_confidence(session, author_id):
    source = _concept(session, author_id, "Confident source")
    target = _concept(session, author_id, "Confident target")
    relationship = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="component_of",
        method="rule",
        proposed_at=NOW,
        confidence=1,
        evidence={"rule": "section 9.3 row 5"},
    )
    session.flush()
    assert relationship.status == "proposed"
    assert relationship.reviewed_at is None


def test_the_service_refuses_a_self_relationship_before_storage_does(session, author_id):
    concept = _concept(session, author_id, "Reflexive concept")
    with pytest.raises(ValueError, match="itself"):
        propose_concept_relationship(
            session,
            source_concept_id=concept.id,
            target_concept_id=concept.id,
            relationship_type="related",
            method="manual",
            proposed_at=NOW,
        )


def test_a_withdrawn_concept_takes_no_new_relationships_at_either_end(session, author_id):
    live = _concept(session, author_id, "Live concept")
    withdrawn = _concept(session, author_id, "Withdrawn concept")
    withdrawn.status = "withdrawn"
    session.flush()

    with pytest.raises(ValueError, match="target semantic concept is withdrawn"):
        propose_concept_relationship(
            session,
            source_concept_id=live.id,
            target_concept_id=withdrawn.id,
            relationship_type="broader",
            method="manual",
            proposed_at=NOW,
        )
    with pytest.raises(ValueError, match="source semantic concept is withdrawn"):
        propose_concept_relationship(
            session,
            source_concept_id=withdrawn.id,
            target_concept_id=live.id,
            relationship_type="broader",
            method="manual",
            proposed_at=NOW,
        )


def test_a_missing_concept_is_a_lookup_error(session, author_id):
    live = _concept(session, author_id, "Existing concept")
    with pytest.raises(LookupError):
        propose_concept_relationship(
            session,
            source_concept_id=live.id,
            target_concept_id=uuid.uuid4(),
            relationship_type="related",
            method="manual",
            proposed_at=NOW,
        )


def test_verifying_a_manual_assertion_requires_a_reviewer(session, author_id):
    source = _concept(session, author_id, "Manual source")
    target = _concept(session, author_id, "Manual target")
    relationship = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="related",
        method="manual",
        proposed_at=NOW,
    )
    session.flush()
    with pytest.raises(ValueError, match="requires a reviewer"):
        transition_concept_relationship(
            session, relationship.id, target_status="verified", occurred_at=LATER
        )


def test_a_deterministic_assertion_may_be_verified_with_no_named_reviewer(session, author_id):
    """Section 8.4's explicit policy. The decision time is still recorded, so
    the row satisfies `review_decision` in storage."""
    source = _concept(session, author_id, "Rule source")
    target = _concept(session, author_id, "Rule target")
    relationship = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="has_function",
        method="source_mapping",
        proposed_at=NOW,
    )
    session.flush()
    transition_concept_relationship(
        session, relationship.id, target_status="verified", occurred_at=LATER
    )
    session.flush()
    assert relationship.status == "verified"
    assert relationship.reviewed_by_user_id is None
    assert relationship.reviewed_at == LATER


def test_rejected_is_terminal(session, author_id):
    source = _concept(session, author_id, "Terminal source")
    target = _concept(session, author_id, "Terminal target")
    relationship = propose_concept_relationship(
        session,
        source_concept_id=source.id,
        target_concept_id=target.id,
        relationship_type="related",
        method="manual",
        proposed_at=NOW,
    )
    session.flush()
    transition_concept_relationship(
        session,
        relationship.id,
        target_status="rejected",
        occurred_at=LATER,
        reviewed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises(ValueError, match="cannot move from rejected"):
        transition_concept_relationship(
            session,
            relationship.id,
            target_status="verified",
            occurred_at=LATER,
            reviewed_by_user_id=author_id,
        )


def test_verifying_retires_nothing(session, author_id):
    """Unlike SM-P0-02, -03 and -04 there is no succession rule: a concept can
    legitimately be narrower than two others at once."""
    child = _concept(session, author_id, "Centrifugal pump")
    first_parent = _concept(session, author_id, "Pump")
    second_parent = _concept(session, author_id, "Rotating equipment")

    verified = []
    for parent in (first_parent, second_parent):
        relationship = propose_concept_relationship(
            session,
            source_concept_id=child.id,
            target_concept_id=parent.id,
            relationship_type="broader",
            method="manual",
            proposed_at=NOW,
        )
        session.flush()
        transition_concept_relationship(
            session,
            relationship.id,
            target_status="verified",
            occurred_at=LATER,
            reviewed_by_user_id=author_id,
        )
        session.flush()
        verified.append(relationship)

    assert [row.status for row in verified] == ["verified", "verified"]
    assert set(verified_broader_concept_ids(session, child.id)) == {
        first_parent.id,
        second_parent.id,
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def test_reads_are_directed_and_a_proposal_never_reaches_the_verified_read(session, author_id):
    child = _concept(session, author_id, "Butterfly valve")
    parent = _concept(session, author_id, "Quarter-turn valve")
    relationship = propose_concept_relationship(
        session,
        source_concept_id=child.id,
        target_concept_id=parent.id,
        relationship_type="broader",
        method="manual",
        proposed_at=NOW,
    )
    session.flush()

    assert [row.id for row in list_concept_relationships(session, child.id)] == [relationship.id]
    assert list_concept_relationships(session, parent.id) == []
    assert [row.id for row in list_concept_relationships(session, parent.id, direction="incoming")] == [
        relationship.id
    ]
    assert [row.id for row in list_concept_relationships(session, parent.id, direction="both")] == [
        relationship.id
    ]
    assert [row.id for row in find_concept_relationships_to(session, parent.id)] == [
        relationship.id
    ]

    # Principle P-07: an unreviewed proposal is not a fact a display may read.
    assert verified_broader_concept_ids(session, child.id) == []


def test_the_broader_walk_survives_a_stored_cycle(session, author_id):
    """Storage cannot refuse a loop spread across rows, so the reader has to.
    The starting concept is never returned, even though the cycle leads back."""
    first = _concept(session, author_id, "Cycle A")
    second = _concept(session, author_id, "Cycle B")
    third = _concept(session, author_id, "Cycle C")

    for source, target in ((first, second), (second, third), (third, first)):
        relationship = propose_concept_relationship(
            session,
            source_concept_id=source.id,
            target_concept_id=target.id,
            relationship_type="broader",
            method="manual",
            proposed_at=NOW,
        )
        session.flush()
        transition_concept_relationship(
            session,
            relationship.id,
            target_status="verified",
            occurred_at=LATER,
            reviewed_by_user_id=author_id,
        )
        session.flush()

    ancestry = broader_concept_ids(session, first.id)
    assert ancestry == [second.id, third.id]
    assert first.id not in ancestry


def test_an_invalid_filter_is_refused_rather_than_ignored(session, author_id):
    concept = _concept(session, author_id, "Filtered concept")
    with pytest.raises(ValueError, match="direction"):
        list_concept_relationships(session, concept.id, direction="sideways")
    with pytest.raises(ValueError, match="status filter"):
        list_concept_relationships(session, concept.id, status="approved")
    with pytest.raises(ValueError, match="type filter"):
        list_concept_relationships(session, concept.id, relationship_type="parent_of")


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_the_table_and_upgrade_restores_it(relationship_database):
    engine, url = relationship_database
    _alembic(url, "downgrade", PREVIOUS_REVISION)
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name='semantic_concept_relationships'"
            )
        ).scalar_one() == 0
    _alembic(url, "upgrade", RELATIONSHIP_REVISION)
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name='semantic_concept_relationships'"
            )
        ).scalar_one() == 1


def test_the_orm_model_and_the_migration_agree_on_the_columns(relationship_database):
    engine, _ = relationship_database
    with engine.begin() as connection:
        stored = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='semantic_concept_relationships'"
                )
            ).scalars()
        )
    assert stored == {column.name for column in SemanticConceptRelationship.__table__.columns}
