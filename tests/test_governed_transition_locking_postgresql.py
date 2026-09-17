"""The locking read in every governed transition must be the read the guards see.

The defect this file pins: a caller that loads a row *before* the service takes
its lock -- which every decision route in `routes/semantic_review.py` does, to
resolve tenancy or to 404 -- puts that row in the session's identity map with
its pre-lock attributes. `Session.get(..., with_for_update=True)` then locks the
row in the database but, without `populate_existing=True`, hands back the
identity-mapped instance *unrefreshed*. Every guard in the transition is
therefore evaluated against values that were true before the lock was taken.

Nothing in the stack closes that window. No `isolation_level` is set anywhere in
this repository, so PostgreSQL's default READ COMMITTED applies: the second
writer's UPDATE is accepted and the overwrite is silent. Under REPEATABLE READ
it would at least be a serialization failure.

The consequence is concrete: two reviewers open the same queue row, the first
verifies it, and the second's decision is evaluated against `proposed` and
overwrites the first -- including `reviewed_by_user_id`, so the governance
record names the wrong reviewer with no error anywhere.

The behavioural proof below uses the two concept-targeted services, which need
only a concept and a migration-seeded classification node to set up. The
source-level guard at the end covers all four locking reads, including the two
whose fixtures would need a whole symbol revision.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email.
"""

from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.classification_assignments import (  # noqa: E402
    propose_concept_classification,
    transition_concept_classification,
)
from symgov_backend.concept_external_references import (  # noqa: E402
    propose_concept_external_reference,
    transition_concept_external_reference,
)
from symgov_backend.external_semantic_schemes import (  # noqa: E402
    register_external_scheme_version,
    register_external_semantic_scheme,
)
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    ConceptClassificationAssignment,
    ConceptExternalReference,
)
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend" / "symgov_backend"

HEAD_REVISION = "20260917_0060"

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)

# The governed semantic transitions that lock a row a caller may already hold.
# The first four are the routes in `routes/semantic_review.py`; the fifth has no
# route yet and is here so it does not acquire one with the trap already set.
#
# Scope note: 24 further locking reads elsewhere in the backend -- promotion,
# demotion, symbol sets, catalogue identifiers, ICS, the publication gate --
# share the shape and are NOT covered here. Whether each of those has a caller
# that preloads is a separate question per module and was not measured, so this
# guard deliberately names the modules whose callers were.
LOCKING_READS = (
    ("classification_assignments.py", "session.get(\n        model, assignment_id"),
    ("concept_external_references.py", "session.get(\n        ConceptExternalReference, reference_id"),
    ("symbol_semantic_assignments.py", "session.get(\n        SymbolSemanticAssignment, assignment_id"),
    ("rights_provenance.py", "session.get(\n        RightsRecord, record_id"),
    ("concept_relationships.py", "session.get(\n        SemanticConceptRelationship,"),
)


@pytest.fixture(scope="module")
def locking_database():
    with _database("symgov-transition-locking") as (engine, url, raw_url):
        _alembic(url, "upgrade", HEAD_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(locking_database) -> uuid.UUID:
    engine, _ = locking_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "locking-reviewer@example.test", "now": NOW},
        )
    return identifier


def _concept_id(engine, author_id: uuid.UUID, name: str) -> uuid.UUID:
    with Session(engine) as session:
        concept, _ = create_semantic_concept(
            session,
            concept_kind="physical_equipment",
            preferred_name=name,
            definition=f"{name}, seeded for the locking rehearsal.",
            created_by_user_id=author_id,
            created_at=NOW,
        )
        session.commit()
        return concept.id


def _node_id(engine) -> uuid.UUID:
    """Any node of a migration-seeded scheme; which one is immaterial here."""
    with Session(engine) as session:
        node = session.query(ClassificationNode).order_by(ClassificationNode.id).first()
        assert node is not None, "expected the migration-seeded classification nodes"
        return node.id


# --------------------------------------------------------------------------
# Concept classification assignments
# --------------------------------------------------------------------------


def test_a_classification_decision_reads_the_row_it_locked(locking_database, author_id):
    """Two reviewers, one row. The second decision must see the first."""
    engine, _ = locking_database
    concept_id = _concept_id(engine, author_id, "Contested classification concept")
    node_id = _node_id(engine)

    with Session(engine) as setup:
        assignment = propose_concept_classification(
            setup,
            semantic_concept_id=concept_id,
            classification_node_id=node_id,
            assignment_role="secondary",
            method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        setup.commit()
        assignment_id = assignment.id

    with Session(engine) as first, Session(engine) as second:
        # What every decision route does before calling the service: resolve the
        # row, outside any lock, to check tenancy and to 404.
        preloaded = first.get(ConceptClassificationAssignment, assignment_id)
        assert preloaded is not None and preloaded.status == "proposed"

        # A concurrent reviewer decides first, and commits.
        transition_concept_classification(
            second,
            assignment_id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
        second.commit()

        # The slow reviewer now decides. The service locks the row, so the guard
        # must be evaluated against `verified` -- not against the `proposed` the
        # route preloaded.
        with pytest.raises(ValueError, match="cannot move from verified to verified"):
            transition_concept_classification(
                first,
                assignment_id,
                target_status="verified",
                occurred_at=LATER,
                reviewed_by_user_id=author_id,
            )


def test_the_first_reviewer_is_not_overwritten(locking_database, author_id):
    """The damage the guard prevents, stated as the record it protects: the
    decision keeps the reviewer and the time the *first* decision recorded."""
    engine, _ = locking_database
    concept_id = _concept_id(engine, author_id, "Attribution concept")
    node_id = _node_id(engine)
    other_reviewer = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": other_reviewer, "email": "second-reviewer@example.test", "now": NOW},
        )

    with Session(engine) as setup:
        assignment = propose_concept_classification(
            setup,
            semantic_concept_id=concept_id,
            classification_node_id=node_id,
            assignment_role="secondary",
            method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        setup.commit()
        assignment_id = assignment.id

    with Session(engine) as first, Session(engine) as second:
        first.get(ConceptClassificationAssignment, assignment_id)
        transition_concept_classification(
            second,
            assignment_id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
        second.commit()

        with pytest.raises(ValueError):
            transition_concept_classification(
                first,
                assignment_id,
                target_status="verified",
                occurred_at=LATER,
                reviewed_by_user_id=other_reviewer,
            )
        first.rollback()

    with Session(engine) as check:
        stored = check.get(ConceptClassificationAssignment, assignment_id)
        assert stored.reviewed_by_user_id == author_id
        assert stored.reviewed_at == NOW


# --------------------------------------------------------------------------
# Concept external mappings
# --------------------------------------------------------------------------


def test_an_external_mapping_decision_reads_the_row_it_locked(locking_database, author_id):
    engine, _ = locking_database
    concept_id = _concept_id(engine, author_id, "Contested mapping concept")

    with Session(engine) as setup:
        scheme = register_external_semantic_scheme(
            setup,
            scheme_code="LOCKING-TEST",
            title="Locking rehearsal scheme",
            issuing_body="SymGov test fixtures",
            registered_at=NOW,
        )
        setup.flush()
        version = register_external_scheme_version(
            setup,
            scheme_id=scheme.id,
            version_label="2026-09",
            registered_at=NOW,
        )
        setup.flush()
        reference = propose_concept_external_reference(
            setup,
            semantic_concept_id=concept_id,
            scheme_version_id=version.id,
            external_identifier="LOCK-0001",
            mapping_type="close",
            mapping_method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        setup.commit()
        reference_id = reference.id

    with Session(engine) as first, Session(engine) as second:
        preloaded = first.get(ConceptExternalReference, reference_id)
        assert preloaded is not None and preloaded.mapping_status == "proposed"

        transition_concept_external_reference(
            second,
            reference_id,
            target_status="rejected",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
        second.commit()

        # `rejected` is terminal, and the slow reviewer must be told so.
        with pytest.raises(ValueError, match="cannot move from rejected"):
            transition_concept_external_reference(
                first,
                reference_id,
                target_status="verified",
                occurred_at=LATER,
                reviewed_by_user_id=author_id,
                verification_basis="human_review",
            )


# --------------------------------------------------------------------------
# Source-level guard, covering all four
# --------------------------------------------------------------------------


@pytest.mark.parametrize("module,call", LOCKING_READS, ids=[name for name, _ in LOCKING_READS])
def test_every_locking_read_refreshes_what_it_locked(module, call):
    """`with_for_update=True` locks the row; `populate_existing=True` is what
    makes the ORM read it. Without the second, a caller that preloaded the row
    keeps its pre-lock attributes and every guard below is evaluated against
    them -- silently, because READ COMMITTED accepts the write."""
    source = (BACKEND / module).read_text(encoding="utf-8")
    index = source.find(call)
    assert index != -1, f"{module}: the locking read moved -- {call!r}"
    statement = source[index : source.index(")", index) + 1]
    assert "with_for_update=True" in statement, f"{module}: the row is no longer locked"
    assert "populate_existing=True" in statement, (
        f"{module}: the locking read does not refresh the row it locked"
    )


def test_the_modules_this_guard_covers_hold_no_unrefreshed_lock():
    """A sweep of the five, so a *second* locking read added to one of them is
    caught too. Deliberately not repository-wide: 24 further locking reads share
    the shape, and whether each has a caller that preloads is a separate
    question per module that this package did not measure."""
    covered = {module for module, _ in LOCKING_READS}
    offenders = []
    for path in sorted(BACKEND.glob("*.py")):
        if path.name not in covered:
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"session\.get\((?:[^()]|\([^()]*\))*\)", source):
            call = match.group(0)
            if "with_for_update=True" in call and "populate_existing=True" not in call:
                offenders.append(f"{path.name}: {' '.join(call.split())}")
    assert offenders == [], (
        "a locking Session.get must pass populate_existing=True, or a caller that "
        f"preloaded the row evaluates its guards against pre-lock values: {offenders}"
    )
