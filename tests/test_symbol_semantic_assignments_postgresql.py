"""Migration and service rehearsal for SM-P0-02 against a real PostgreSQL server.

Why this file exists: the rule that makes semantic assignment trustworthy --
at most one *verified primary* concept per symbol revision, with proposals left
free to compete -- is a partial unique index. So are the review-decision,
confidence-range and evidence-shape guarantees. None of that can be proven by
reading the migration text, so this file upgrades a disposable database to
20260909_0048 and makes the server reject each violation.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.semantic_concepts import (  # noqa: E402
    create_semantic_concept,
    transition_semantic_concept_revision,
)
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    list_symbol_semantic_assignments,
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
    verified_primary_assignment,
)

ASSIGNMENT_REVISION = "20260909_0048"
PREVIOUS_REVISION = "20260909_0047"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def assignment_database():
    with _database("symgov-semantic-assign") as (engine, url, raw_url):
        _alembic(url, "upgrade", ASSIGNMENT_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(assignment_database) -> uuid.UUID:
    engine, _ = assignment_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "assignment-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(assignment_database) -> Session:
    engine, _ = assignment_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


def _symbol_revision(session: Session, author: uuid.UUID) -> uuid.UUID:
    """Seed a draft governed symbol revision.

    Deliberately draft: 20260826_0031's publication invariant would otherwise
    demand a canonical catalog identifier, which is irrelevant to this package.
    """
    symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    slug = f"assignment-symbol-{uuid.uuid4().hex[:10]}"
    session.execute(
        text(
            "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,"
            "owner_id,created_at,updated_at) "
            "VALUES (:id,:slug,:slug,'Valves','Piping / P&ID',:owner,:now,:now)"
        ),
        {"id": symbol_id, "slug": slug, "owner": author, "now": NOW},
    )
    session.execute(
        text(
            "INSERT INTO symbol_revisions (id,symbol_id,revision_label,lifecycle_state,"
            "payload_json,author_id,created_at) "
            "VALUES (:id,:symbol,'1','draft','{}'::jsonb,:owner,:now)"
        ),
        {"id": revision_id, "symbol": symbol_id, "owner": author, "now": NOW},
    )
    return revision_id


def _concept(session: Session, author: uuid.UUID, name: str) -> uuid.UUID:
    concept, _revision = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name=name,
        definition=f"Illustrative definition for {name}.",
        created_by_user_id=author,
        created_at=NOW,
    )
    session.flush()
    return concept.id


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


def test_upgrade_creates_the_assignment_table_and_partial_index(assignment_database):
    engine, _ = assignment_database
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name='symbol_semantic_assignments'"
            )
        ).scalar_one() == 1
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname='uq_symbol_semantic_assignments_verified_primary'"
            )
        ).scalar_one()
    assert "UNIQUE" in definition
    assert "symbol_revision_id" in definition
    assert "primary" in definition and "verified" in definition


def test_upgrade_left_the_semantic_concept_core_untouched(assignment_database):
    engine, _ = assignment_database
    with engine.begin() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name IN ('semantic_concepts','semantic_concept_revisions')"
                )
            ).scalars()
        )
    assert tables == {"semantic_concepts", "semantic_concept_revisions"}


# --------------------------------------------------------------------------
# The one-verified-primary rule
# --------------------------------------------------------------------------


def test_database_refuses_a_second_verified_primary(session, author_id):
    revision = _symbol_revision(session, author_id)
    first, second = _concept(session, author_id, "Gate valve"), _concept(session, author_id, "Ball valve")
    session.flush()

    insert = text(
        "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,semantic_concept_id,"
        "assignment_role,status,method,reviewed_at,created_at,updated_at) "
        "VALUES (:id,:revision,:concept,'primary','verified','manual',:now,:now,:now)"
    )
    session.execute(insert, {"id": uuid.uuid4(), "revision": revision, "concept": first, "now": NOW})
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(insert, {"id": uuid.uuid4(), "revision": revision, "concept": second, "now": NOW})
        session.flush()


def test_competing_primary_proposals_are_allowed(session, author_id):
    """Only *verified* primaries are unique. Reviewers must be able to see rival
    candidates side by side before deciding."""
    revision = _symbol_revision(session, author_id)
    first, second = _concept(session, author_id, "Gate valve alt"), _concept(session, author_id, "Sluice valve alt")
    session.flush()

    for concept_id, method in ((first, "manual"), (second, "ai_assisted")):
        propose_symbol_semantic_assignment(
            session,
            symbol_revision_id=revision,
            semantic_concept_id=concept_id,
            assignment_role="primary",
            method=method,
            proposed_at=NOW,
            proposed_by_user_id=author_id,
            confidence=0.91,
        )
    session.flush()

    proposals = list_symbol_semantic_assignments(session, revision, status="proposed")
    assert len(proposals) == 2
    assert {p.assignment_role for p in proposals} == {"primary"}


def test_multiple_qualifiers_and_components_are_allowed(session, author_id):
    revision = _symbol_revision(session, author_id)
    session.flush()
    for index, role in enumerate(("qualifier", "qualifier", "component", "component")):
        concept_id = _concept(session, author_id, f"Modifier {index}")
        assignment = propose_symbol_semantic_assignment(
            session,
            symbol_revision_id=revision,
            semantic_concept_id=concept_id,
            assignment_role=role,
            method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        transition_symbol_semantic_assignment(
            session,
            assignment.id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
    session.flush()
    verified = list_symbol_semantic_assignments(session, revision, status="verified")
    assert len(verified) == 4


def test_verifying_a_new_primary_retires_the_previous_one(session, author_id):
    revision = _symbol_revision(session, author_id)
    first, second = _concept(session, author_id, "Gate valve v1"), _concept(session, author_id, "Gate valve v2")
    session.flush()

    original = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=first,
        assignment_role="primary", method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
    )
    transition_symbol_semantic_assignment(
        session, original.id, target_status="verified", occurred_at=NOW, reviewed_by_user_id=author_id,
    )
    session.flush()
    assert verified_primary_assignment(session, revision).id == original.id

    successor = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=second,
        assignment_role="primary", method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
    )
    transition_symbol_semantic_assignment(
        session, successor.id, target_status="verified", occurred_at=NOW, reviewed_by_user_id=author_id,
    )
    session.flush()

    assert original.status == "retired"
    assert successor.status == "verified"
    assert verified_primary_assignment(session, revision).id == successor.id


def test_verified_primary_lookup_returns_none_before_any_decision(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Undecided concept")
    session.flush()
    propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="primary", method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
    )
    session.flush()
    assert verified_primary_assignment(session, revision) is None


# --------------------------------------------------------------------------
# Server-side storage guarantees
# --------------------------------------------------------------------------


def test_a_verified_row_must_record_when_it_was_decided(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Undated decision")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,semantic_concept_id,"
                "assignment_role,status,method,created_at,updated_at) "
                "VALUES (:id,:revision,:concept,'qualifier','verified','manual',:now,:now)"
            ),
            {"id": uuid.uuid4(), "revision": revision, "concept": concept_id, "now": NOW},
        )
        session.flush()


@pytest.mark.parametrize("confidence", ["-0.0001", "1.0001"])
def test_database_enforces_the_confidence_range(session, author_id, confidence):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, f"Confidence {confidence}")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,semantic_concept_id,"
                "assignment_role,status,method,confidence,created_at,updated_at) "
                "VALUES (:id,:revision,:concept,'qualifier','proposed','ai_assisted',:confidence,:now,:now)"
            ),
            {
                "id": uuid.uuid4(), "revision": revision, "concept": concept_id,
                "confidence": confidence, "now": NOW,
            },
        )
        session.flush()


def test_evidence_must_be_a_json_object(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Evidence shape")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_semantic_assignments (id,symbol_revision_id,semantic_concept_id,"
                "assignment_role,status,method,evidence_json,created_at,updated_at) "
                "VALUES (:id,:revision,:concept,'qualifier','proposed','manual','[\"list\"]'::jsonb,:now,:now)"
            ),
            {"id": uuid.uuid4(), "revision": revision, "concept": concept_id, "now": NOW},
        )
        session.flush()


def test_a_concept_carrying_assignments_cannot_be_deleted(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Referenced concept")
    session.flush()
    propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="qualifier", method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(text("DELETE FROM semantic_concepts WHERE id=:id"), {"id": concept_id})
        session.flush()


# --------------------------------------------------------------------------
# Governance rules
# --------------------------------------------------------------------------


def test_high_confidence_machine_output_is_still_only_proposed(session, author_id):
    """Specification section 8.4: confidence is not a governance state."""
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Very confident guess")
    session.flush()
    assignment = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="primary", method="ai_assisted", proposed_at=NOW, confidence=0.99,
    )
    session.flush()
    assert assignment.status == "proposed"
    assert assignment.reviewed_at is None
    assert verified_primary_assignment(session, revision) is None


def test_ai_assisted_verification_requires_a_reviewer(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Needs a human")
    session.flush()
    assignment = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="primary", method="ai_assisted", proposed_at=NOW, confidence=0.99,
    )
    session.flush()
    with pytest.raises(ValueError, match="requires a reviewer"):
        transition_symbol_semantic_assignment(
            session, assignment.id, target_status="verified", occurred_at=NOW,
        )


def test_a_deterministic_import_may_be_auto_verified(session, author_id):
    """Section 8.4 allows deterministic authoritative-source verification, so a
    source_mapping/rule assignment may be verified without a named reviewer."""
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Deterministic import")
    session.flush()
    assignment = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="primary", method="source_mapping", proposed_at=NOW,
    )
    transition_symbol_semantic_assignment(
        session, assignment.id, target_status="verified", occurred_at=NOW,
    )
    session.flush()
    assert assignment.status == "verified"
    assert assignment.reviewed_by_user_id is None
    assert assignment.reviewed_at == NOW


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [("rejected", "verified"), ("retired", "verified"), ("verified", "rejected"), ("rejected", "retired")],
)
def test_illegal_status_transitions_are_refused(session, author_id, from_status, to_status):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, f"Transition {from_status} {to_status}")
    session.flush()
    assignment = propose_symbol_semantic_assignment(
        session, symbol_revision_id=revision, semantic_concept_id=concept_id,
        assignment_role="qualifier", method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
    )
    transition_symbol_semantic_assignment(
        session, assignment.id, target_status=from_status, occurred_at=NOW,
        reviewed_by_user_id=author_id,
    )
    session.flush()
    assert assignment.status == from_status

    with pytest.raises(ValueError, match="cannot move from"):
        transition_symbol_semantic_assignment(
            session, assignment.id, target_status=to_status, occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )


def test_a_withdrawn_concept_accepts_no_new_assignments(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept, concept_revision = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Withdrawn concept",
        definition="A concept that will be withdrawn before assignment.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    for state in ("review", "approved", "published"):
        transition_semantic_concept_revision(
            session, concept_revision.id, target_state=state, actor_id=author_id, occurred_at=NOW,
        )
    transition_semantic_concept_revision(
        session, concept_revision.id, target_state="withdrawn", actor_id=author_id, occurred_at=NOW,
    )
    session.flush()
    assert concept.status == "withdrawn"

    with pytest.raises(ValueError, match="accepts no new assignments"):
        propose_symbol_semantic_assignment(
            session, symbol_revision_id=revision, semantic_concept_id=concept.id,
            assignment_role="primary", method="manual", proposed_at=NOW,
            proposed_by_user_id=author_id,
        )


def test_proposing_against_a_missing_revision_or_concept_is_refused(session, author_id):
    revision = _symbol_revision(session, author_id)
    concept_id = _concept(session, author_id, "Real concept")
    session.flush()
    with pytest.raises(LookupError, match="symbol revision not found"):
        propose_symbol_semantic_assignment(
            session, symbol_revision_id=uuid.uuid4(), semantic_concept_id=concept_id,
            assignment_role="primary", method="manual", proposed_at=NOW,
        )
    with pytest.raises(LookupError, match="semantic concept not found"):
        propose_symbol_semantic_assignment(
            session, symbol_revision_id=revision, semantic_concept_id=uuid.uuid4(),
            assignment_role="primary", method="manual", proposed_at=NOW,
        )


# --------------------------------------------------------------------------
# Read services
# --------------------------------------------------------------------------


def test_listing_puts_the_primary_first_and_filters_by_role(session, author_id):
    revision = _symbol_revision(session, author_id)
    session.flush()
    for role in ("qualifier", "component", "primary"):
        concept_id = _concept(session, author_id, f"Ordering {role}")
        propose_symbol_semantic_assignment(
            session, symbol_revision_id=revision, semantic_concept_id=concept_id,
            assignment_role=role, method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
        )
    session.flush()

    listed = list_symbol_semantic_assignments(session, revision)
    assert [a.assignment_role for a in listed][0] == "primary"
    assert len(listed) == 3
    assert len(list_symbol_semantic_assignments(session, revision, assignment_role="qualifier")) == 1

    with pytest.raises(ValueError):
        list_symbol_semantic_assignments(session, revision, status="not_a_status")
    with pytest.raises(ValueError):
        list_symbol_semantic_assignments(session, revision, assignment_role="not_a_role")


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_the_assignment_table_and_keeps_the_concept_core():
    with _database("symgov-assign-down") as (engine, url, _raw):
        _alembic(url, "upgrade", ASSIGNMENT_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name='symbol_semantic_assignments'"
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name IN ('semantic_concepts','semantic_concept_revisions')"
                )
            ).scalar_one() == 2
