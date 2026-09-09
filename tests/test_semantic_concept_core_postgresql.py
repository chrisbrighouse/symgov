"""Migration and service rehearsal for SM-P0-01 against a real PostgreSQL server.

Why this file exists: the storage guarantees that make the semantic concept
layer trustworthy are all server-side -- the SGC-######## grammar check, the
unique concept_code index, the per-concept unique revision label, the JSONB
array check, the capped sequence, and the circular current_revision_id foreign
key. A DB-free test can only assert that the migration *text* mentions them.
This file upgrades a disposable database to 20260909_0047 and proves each one
actually rejects bad data, then proves the downgrade is clean.

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
    add_semantic_concept_revision,
    allocate_semantic_concept_code,
    create_semantic_concept,
    get_semantic_concept_by_code,
    list_semantic_concept_revisions,
    list_semantic_concepts,
    transition_semantic_concept_revision,
)

SEMANTIC_CONCEPT_REVISION = "20260909_0047"
PREVIOUS_REVISION = "20260908_0046"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def semantic_database():
    with _database("symgov-semantic-core") as (engine, url, raw_url):
        _alembic(url, "upgrade", SEMANTIC_CONCEPT_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(semantic_database) -> uuid.UUID:
    engine, _ = semantic_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "semantic-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(semantic_database) -> Session:
    engine, _ = semantic_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


def test_upgrade_creates_both_semantic_tables_and_the_sequence(semantic_database):
    engine, _ = semantic_database
    with engine.begin() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('semantic_concepts','semantic_concept_revisions')"
                )
            ).scalars()
        )
        assert tables == {"semantic_concepts", "semantic_concept_revisions"}

        sequence_max = connection.execute(
            text(
                "SELECT maximum_value FROM information_schema.sequences "
                "WHERE sequence_name='semantic_concept_code_seq'"
            )
        ).scalar_one()
        assert int(sequence_max) == 99_999_999


def test_current_revision_foreign_key_exists_and_points_at_revisions(semantic_database):
    engine, _ = semantic_database
    with engine.begin() as connection:
        referenced = connection.execute(
            text(
                """
                SELECT ccu.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type='FOREIGN KEY'
                  AND tc.table_name='semantic_concepts'
                  AND tc.constraint_name=
                    'fk_semantic_concepts_current_revision_id'
                """
            )
        ).scalar_one()
        assert referenced == "semantic_concept_revisions"


def test_upgrade_left_the_governed_symbol_catalogue_columns_untouched(semantic_database):
    """SM-P0-01 is additive: category and discipline must survive verbatim."""
    engine, _ = semantic_database
    with engine.begin() as connection:
        columns = dict(
            connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name='governed_symbols' AND column_name IN "
                    "('category','discipline')"
                )
            ).all()
        )
    assert columns == {"category": "NO", "discipline": "NO"}


# --------------------------------------------------------------------------
# Server-side storage guarantees
# --------------------------------------------------------------------------


def test_concept_code_grammar_is_enforced_by_the_database(session, author_id):
    for bad_code in ("SGC-1284", "sgc-00001284", "SG-00001284", "SGC-0000128A"):
        with pytest.raises((IntegrityError, DBAPIError)):
            session.execute(
                text(
                    "INSERT INTO semantic_concepts (id,concept_code,concept_kind,status,"
                    "created_by_user_id,created_at,updated_at) VALUES "
                    "(:id,:code,'physical_equipment','draft',:author,:now,:now)"
                ),
                {"id": uuid.uuid4(), "code": bad_code, "author": author_id, "now": NOW},
            )
            session.flush()
        session.rollback()


def test_concept_code_is_unique(session, author_id):
    first, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Gate valve",
        definition="A valve whose closure member moves perpendicular to the flow path.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO semantic_concepts (id,concept_code,concept_kind,status,"
                "created_by_user_id,created_at,updated_at) VALUES "
                "(:id,:code,'function','draft',:author,:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "code": first.concept_code,
                "author": author_id,
                "now": NOW,
            },
        )
        session.flush()


def test_revision_label_is_unique_per_concept_but_reusable_across_concepts(session, author_id):
    first, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Ball valve",
        definition="A quarter-turn valve using a rotary ball to control flow.",
        created_by_user_id=author_id,
        created_at=NOW,
        revision_label="r1",
    )
    second, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Globe valve",
        definition="A valve using a movable disc against a stationary ring seat.",
        created_by_user_id=author_id,
        created_at=NOW,
        revision_label="r1",
    )
    session.flush()
    assert first.concept_code != second.concept_code

    with pytest.raises((IntegrityError, DBAPIError)):
        add_semantic_concept_revision(
            session,
            first.id,
            revision_label="r1",
            preferred_name="Ball valve",
            definition="A duplicate label for the same concept must be refused.",
            author_id=author_id,
            created_at=NOW,
        )
        session.flush()


def test_aliases_must_be_a_json_array(session, author_id):
    concept, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Check valve",
        definition="A valve permitting flow in one direction only.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO semantic_concept_revisions (id,concept_id,revision_label,"
                "lifecycle_state,preferred_name,definition,aliases_json,author_id,created_at) "
                "VALUES (:id,:concept,'r9','draft','Check valve','A definition.',"
                "'{\"not\":\"an array\"}'::jsonb,:author,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "concept": concept.id,
                "author": author_id,
                "now": NOW,
            },
        )
        session.flush()


def test_blank_preferred_name_and_definition_are_refused(session, author_id):
    concept, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Butterfly valve",
        definition="A quarter-turn valve using a rotating disc.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    for column, value in (("preferred_name", "   "), ("definition", "")):
        payload = {"preferred_name": "Name", "definition": "A definition.", column: value}
        with pytest.raises((IntegrityError, DBAPIError)):
            session.execute(
                text(
                    "INSERT INTO semantic_concept_revisions (id,concept_id,revision_label,"
                    "lifecycle_state,preferred_name,definition,author_id,created_at) "
                    "VALUES (:id,:concept,:label,'draft',:preferred_name,:definition,:author,:now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "concept": concept.id,
                    "label": f"blank-{column}",
                    "author": author_id,
                    "now": NOW,
                    **payload,
                },
            )
            session.flush()
        session.rollback()


# --------------------------------------------------------------------------
# Allocation
# --------------------------------------------------------------------------


def test_allocation_is_sequential_and_zero_padded(session):
    codes = [allocate_semantic_concept_code(session, allocated_at=NOW) for _ in range(3)]
    assert all(code.startswith("SGC-") and len(code) == 12 for code in codes)
    values = [int(code.removeprefix("SGC-")) for code in codes]
    assert values == sorted(values)
    assert values[1] == values[0] + 1 and values[2] == values[1] + 1


def test_creation_allocates_a_distinct_code_per_concept(session, author_id):
    codes = set()
    for index in range(5):
        concept, revision = create_semantic_concept(
            session,
            concept_kind="function",
            preferred_name=f"Concept {index}",
            definition=f"Illustrative definition {index}.",
            created_by_user_id=author_id,
            created_at=NOW,
        )
        codes.add(concept.concept_code)
        assert revision.lifecycle_state == "draft"
        assert concept.status == "draft"
        assert concept.current_revision_id is None
    session.flush()
    assert len(codes) == 5


def test_concept_code_survives_a_preferred_name_change(session, author_id):
    """Specification section 16.2: renaming must not change the concept code."""
    concept, first_revision = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Gate valve",
        definition="A valve whose closure member moves perpendicular to the flow path.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    original_code = concept.concept_code

    transition_semantic_concept_revision(
        session, first_revision.id, target_state="review", actor_id=author_id, occurred_at=NOW
    )
    transition_semantic_concept_revision(
        session, first_revision.id, target_state="approved", actor_id=author_id, occurred_at=NOW
    )
    transition_semantic_concept_revision(
        session, first_revision.id, target_state="published", actor_id=author_id, occurred_at=NOW
    )
    session.flush()

    renamed = add_semantic_concept_revision(
        session,
        concept.id,
        revision_label="r2",
        preferred_name="Sluice valve",
        definition="Renamed but semantically identical.",
        author_id=author_id,
        created_at=NOW,
        rationale="Align the preferred term with the source standard.",
    )
    session.flush()
    assert concept.concept_code == original_code
    assert renamed.concept_id == concept.id
    assert [r.revision_label for r in list_semantic_concept_revisions(session, concept.id)] == [
        "r1",
        "r2",
    ]


# --------------------------------------------------------------------------
# Lifecycle governance
# --------------------------------------------------------------------------


def test_publication_repoints_current_revision_and_activates_the_concept(session, author_id):
    concept, revision = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Centrifugal pump",
        definition="A rotodynamic pump using an impeller to raise fluid pressure.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()

    transition_semantic_concept_revision(
        session, revision.id, target_state="review", actor_id=author_id, occurred_at=NOW
    )
    assert revision.reviewed_at is None, "moving into review is not itself a decision"

    transition_semantic_concept_revision(
        session, revision.id, target_state="approved", actor_id=author_id, occurred_at=NOW
    )
    assert revision.reviewed_by_user_id == author_id
    assert revision.reviewed_at == NOW
    assert concept.current_revision_id is None, "approval alone must not publish"

    transition_semantic_concept_revision(
        session, revision.id, target_state="published", actor_id=author_id, occurred_at=NOW
    )
    session.flush()
    assert concept.current_revision_id == revision.id
    assert concept.status == "active"


def test_publishing_a_successor_deprecates_the_superseded_revision(session, author_id):
    concept, first = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Screw pump",
        definition="A positive-displacement pump using intermeshing screws.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    for state in ("review", "approved", "published"):
        transition_semantic_concept_revision(
            session, first.id, target_state=state, actor_id=author_id, occurred_at=NOW
        )

    second = add_semantic_concept_revision(
        session,
        concept.id,
        revision_label="r2",
        preferred_name="Screw pump",
        definition="Definition clarified after review.",
        author_id=author_id,
        created_at=NOW,
    )
    session.flush()
    for state in ("review", "approved", "published"):
        transition_semantic_concept_revision(
            session, second.id, target_state=state, actor_id=author_id, occurred_at=NOW
        )
    session.flush()

    assert concept.current_revision_id == second.id
    assert first.lifecycle_state == "deprecated"
    assert second.lifecycle_state == "published"
    published = [
        r for r in list_semantic_concept_revisions(session, concept.id)
        if r.lifecycle_state == "published"
    ]
    assert len(published) == 1


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("draft", "approved"),
        ("draft", "published"),
        ("review", "published"),
        ("published", "approved"),
        ("withdrawn", "draft"),
    ],
)
def test_illegal_lifecycle_transitions_are_refused(session, author_id, from_state, to_state):
    concept, revision = create_semantic_concept(
        session,
        concept_kind="other",
        preferred_name=f"Transition {from_state} to {to_state}",
        definition="Illustrative concept for lifecycle enforcement.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()

    route = {
        "draft": [],
        "review": ["review"],
        "approved": ["review", "approved"],
        "published": ["review", "approved", "published"],
        "withdrawn": ["withdrawn"],
    }[from_state]
    for state in route:
        transition_semantic_concept_revision(
            session, revision.id, target_state=state, actor_id=author_id, occurred_at=NOW
        )
    assert revision.lifecycle_state == from_state

    with pytest.raises(ValueError, match="cannot move from"):
        transition_semantic_concept_revision(
            session, revision.id, target_state=to_state, actor_id=author_id, occurred_at=NOW
        )


def test_deprecated_concept_accepts_no_further_revisions(session, author_id):
    concept, revision = create_semantic_concept(
        session,
        concept_kind="annotation",
        preferred_name="Retired annotation",
        definition="A concept that will be deprecated.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()
    for state in ("review", "approved", "published", "deprecated"):
        transition_semantic_concept_revision(
            session, revision.id, target_state=state, actor_id=author_id, occurred_at=NOW
        )
    session.flush()
    assert concept.status == "deprecated"
    assert concept.current_revision_id is None

    with pytest.raises(ValueError, match="accepts no new revisions"):
        add_semantic_concept_revision(
            session,
            concept.id,
            revision_label="r2",
            preferred_name="Retired annotation",
            definition="Should be refused.",
            author_id=author_id,
            created_at=NOW,
        )


# --------------------------------------------------------------------------
# Read services
# --------------------------------------------------------------------------


def test_lookup_by_code_is_case_insensitive_on_input(session, author_id):
    concept, _ = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Heat exchanger",
        definition="A device transferring heat between two or more fluids.",
        created_by_user_id=author_id,
        created_at=NOW,
    )
    session.flush()

    assert get_semantic_concept_by_code(session, concept.concept_code).id == concept.id
    assert get_semantic_concept_by_code(session, concept.concept_code.lower()).id == concept.id
    missing = get_semantic_concept_by_code(session, "SGC-99999999")
    assert missing is None


def test_listing_filters_by_kind_and_orders_by_concept_code(session, author_id):
    for index in range(3):
        create_semantic_concept(
            session,
            concept_kind="safety_function",
            preferred_name=f"Safety function {index}",
            definition=f"Illustrative safety function {index}.",
            created_by_user_id=author_id,
            created_at=NOW,
        )
    session.flush()

    listed = list_semantic_concepts(session, concept_kind="safety_function", limit=500)
    assert len(listed) >= 3
    assert all(concept.concept_kind == "safety_function" for concept in listed)
    codes = [concept.concept_code for concept in listed]
    assert codes == sorted(codes)

    with pytest.raises(ValueError):
        list_semantic_concepts(session, concept_kind="not_a_kind")
    with pytest.raises(ValueError):
        list_semantic_concepts(session, limit=0)
    with pytest.raises(ValueError):
        list_semantic_concepts(session, offset=-1)


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_the_semantic_layer_and_leaves_the_catalogue_intact():
    """Run on its own database so the module fixture's data is not disturbed."""
    with _database("symgov-semantic-down") as (engine, url, _raw):
        _alembic(url, "upgrade", SEMANTIC_CONCEPT_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            remaining = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name IN "
                        "('semantic_concepts','semantic_concept_revisions')"
                    )
                ).scalars()
            )
            assert remaining == set()
            sequences = set(
                connection.execute(
                    text(
                        "SELECT sequence_name FROM information_schema.sequences "
                        "WHERE sequence_name='semantic_concept_code_seq'"
                    )
                ).scalars()
            )
            assert sequences == set()
            # The pre-existing catalogue must still be there.
            governed_symbols = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='governed_symbols'"
                )
            ).scalar_one()
            assert governed_symbols == 1
