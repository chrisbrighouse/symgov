"""Migration and service rehearsal for SM-P0-03 against a real PostgreSQL server.

Why this file exists: the guarantees that make an external mapping
trustworthy are storage-level, and none of them can be proven by reading the
migration text.

* Specification section 16.2 -- no mapping without a scheme version -- is a
  NOT NULL column, so the server must refuse the row.
* One live assertion per (concept, release, identifier), and one verified
  `exact` per (concept, release), are partial unique indexes.
* The seeded scheme definitions must actually land, exactly three of them,
  with no reference data alongside.

Redaction: this file never prints the disposable container's connection string
and every seeded identity uses a synthetic `@example.test` email.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

from symgov_backend.concept_external_references import (  # noqa: E402
    find_references_by_external_identifier,
    list_concept_external_references,
    propose_concept_external_reference,
    transition_concept_external_reference,
    verified_exact_reference,
)
from symgov_backend.external_semantic_schemes import (  # noqa: E402
    SEED_EXTERNAL_SEMANTIC_SCHEMES,
    get_external_semantic_scheme,
    list_external_scheme_versions,
    list_external_semantic_schemes,
    register_external_scheme_version,
    register_external_semantic_scheme,
    seed_external_semantic_schemes,
    set_external_scheme_status,
    set_external_scheme_version_status,
)
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402

SCHEME_REVISION = "20260909_0049"
PREVIOUS_REVISION = "20260909_0048"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def scheme_database():
    with _database("symgov-external-schemes") as (engine, url, raw_url):
        _alembic(url, "upgrade", SCHEME_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(scheme_database) -> uuid.UUID:
    engine, _ = scheme_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "scheme-registrar@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(scheme_database) -> Session:
    engine, _ = scheme_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


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


def _version(session: Session, author: uuid.UUID, label: str | None = None) -> uuid.UUID:
    """Register a throwaway scheme and one release of it."""
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"TEST-{uuid.uuid4().hex[:10].upper()}",
        title="Illustrative test scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
        created_by_user_id=author,
    )
    session.flush()
    version = register_external_scheme_version(
        session,
        scheme_id=scheme.id,
        version_label=label or "1.0",
        registered_at=NOW,
        release_date=date(2026, 6, 1),
        created_by_user_id=author,
    )
    session.flush()
    return version.id


# --------------------------------------------------------------------------
# Migration shape and seed
# --------------------------------------------------------------------------


def test_upgrade_creates_all_three_tables(scheme_database):
    engine, _ = scheme_database
    with engine.begin() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name IN ('external_semantic_schemes',"
                    "'external_semantic_scheme_versions','concept_external_references')"
                )
            ).scalars()
        )
    assert tables == {
        "external_semantic_schemes",
        "external_semantic_scheme_versions",
        "concept_external_references",
    }


def test_upgrade_left_the_earlier_semantic_packages_untouched(scheme_database):
    engine, _ = scheme_database
    with engine.begin() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name IN ('semantic_concepts','semantic_concept_revisions',"
                "'symbol_semantic_assignments')"
            )
        ).scalar_one()
    assert count == 3


def test_every_constraint_name_survived_the_63_character_limit(scheme_database):
    """A name PostgreSQL had to truncate would come back shortened or
    hash-suffixed. Both failure modes named in migration 20260909_0049's
    docstring show up here."""
    engine, _ = scheme_database
    with engine.begin() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname IN ('external_semantic_schemes',"
                    "'external_semantic_scheme_versions','concept_external_references')"
                )
            ).scalars()
        )
    for expected in (
        "ck_concept_external_references_verified_exact_reviewer",
        "ck_concept_external_references_verified_exact_evidence",
        "ck_external_semantic_scheme_versions_integrity_retrieval",
        "fk_concept_external_references_scheme_version_id",
        "fk_external_semantic_scheme_versions_scheme_id",
    ):
        assert expected in names, sorted(names)
    assert all(len(name) <= 63 for name in names)
    assert not any(name.startswith("ck_concept_external_references_ck_") for name in names)


def test_the_migration_seeded_exactly_the_three_scheme_definitions(scheme_database):
    engine, _ = scheme_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, scheme_code, title, issuing_body, base_uri, status "
                "FROM external_semantic_schemes ORDER BY scheme_code"
            )
        ).all()
        versions = connection.execute(
            text("SELECT count(*) FROM external_semantic_scheme_versions")
        ).scalar_one()
        mappings = connection.execute(
            text("SELECT count(*) FROM concept_external_references")
        ).scalar_one()

    seeded = {row.scheme_code: row for row in rows}
    assert set(seeded) == {"CFIHOS-RDL", "DEXPI-RDL", "ISO15926-RDL-PCA"}
    for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES:
        row = seeded[definition["scheme_code"]]
        assert row.id == definition["id"]
        assert row.title == definition["title"]
        assert row.issuing_body == definition["issuing_body"]
        assert row.base_uri == definition["base_uri"]
        assert row.status == "active"
    # Section 15.1: definitions only. No release rows, no reference data.
    assert versions == 0
    assert mappings == 0


def test_seeding_again_is_idempotent(session):
    before = len(list_external_semantic_schemes(session))
    seeded = seed_external_semantic_schemes(session, seeded_at=NOW)
    session.flush()
    assert len(seeded) == 3
    assert len(list_external_semantic_schemes(session)) == before
    assert get_external_semantic_scheme(session, "dexpi-rdl").id == SEED_EXTERNAL_SEMANTIC_SCHEMES[0]["id"]


# --------------------------------------------------------------------------
# No mapping without a scheme version (section 16.2)
# --------------------------------------------------------------------------


def test_database_refuses_a_mapping_with_no_scheme_version(session, author_id):
    concept_id = _concept(session, author_id, "Unversioned mapping")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,created_at,updated_at) "
                "VALUES (:id,:concept,NULL,'X-1','close','proposed','manual',:now,:now)"
            ),
            {"id": uuid.uuid4(), "concept": concept_id, "now": NOW},
        )
        session.flush()


def test_database_refuses_a_mapping_to_a_scheme_version_that_does_not_exist(session, author_id):
    concept_id = _concept(session, author_id, "Dangling release")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','close','proposed','manual',:now,:now)"
            ),
            {"id": uuid.uuid4(), "concept": concept_id, "version": uuid.uuid4(), "now": NOW},
        )
        session.flush()


def test_a_scheme_version_carrying_mappings_cannot_be_deleted(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Retained release")
    propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0001",
        mapping_type="close",
        mapping_method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text("DELETE FROM external_semantic_scheme_versions WHERE id=:id"), {"id": version_id}
        )
        session.flush()


# --------------------------------------------------------------------------
# Server-side storage guarantees
# --------------------------------------------------------------------------


def test_scheme_codes_are_unique(session, author_id):
    code = f"UNIQ-{uuid.uuid4().hex[:8].upper()}"
    for _ in range(2):
        register_external_semantic_scheme(
            session,
            scheme_code=code,
            title="Duplicate scheme",
            issuing_body="SymGov test fixture",
            registered_at=NOW,
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_a_release_label_is_unique_within_its_scheme(session, author_id):
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"DUP-{uuid.uuid4().hex[:8].upper()}",
        title="Repeat release scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
    )
    session.flush()
    for _ in range(2):
        register_external_scheme_version(
            session, scheme_id=scheme.id, version_label="2.0", registered_at=NOW
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


@pytest.mark.parametrize(
    ("column", "value"),
    [("scheme_code", "not lower case"), ("scheme_code", "TRAILING-"), ("status", "archived")],
)
def test_database_enforces_the_scheme_grammar_and_status(session, column, value):
    row = {
        "id": uuid.uuid4(),
        "scheme_code": f"OK-{uuid.uuid4().hex[:8].upper()}",
        "status": "active",
        "now": NOW,
    }
    row[column] = value
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO external_semantic_schemes (id,scheme_code,title,issuing_body,status,"
                "created_at,updated_at) "
                "VALUES (:id,:scheme_code,'Title','Body',:status,:now,:now)"
            ),
            row,
        )
        session.flush()


@pytest.mark.parametrize(
    "assignment",
    ["checksum=:checksum", "checksum_algorithm='sha256'"],
)
def test_database_refuses_half_a_checksum_pair(session, author_id, assignment):
    """A NULL on either side must make the constraint false, not NULL --
    PostgreSQL accepts a check constraint that evaluates to NULL."""
    version_id = _version(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                f"UPDATE external_semantic_scheme_versions SET {assignment}, retrieved_at=:now "
                "WHERE id=:id"
            ),
            {"id": version_id, "checksum": "a" * 64, "now": NOW},
        )
        session.flush()


def test_database_refuses_a_checksum_with_no_retrieval_time(session, author_id):
    version_id = _version(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "UPDATE external_semantic_scheme_versions "
                "SET checksum=:checksum, checksum_algorithm='sha256' WHERE id=:id"
            ),
            {"id": version_id, "checksum": "a" * 64},
        )
        session.flush()


def test_a_release_may_record_a_verified_checksum(session, author_id):
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"HASH-{uuid.uuid4().hex[:8].upper()}",
        title="Hashed release scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
    )
    session.flush()
    version = register_external_scheme_version(
        session,
        scheme_id=scheme.id,
        version_label="2.0 Core",
        registered_at=NOW,
        release_date=date(2026, 6, 1),
        source_uri="https://example.test/rdl/2.0",
        checksum="B" * 64,
        checksum_algorithm="sha256",
        etag='W/"rdl-2-0"',
        retrieved_at=NOW,
    )
    session.flush()
    assert version.checksum == "b" * 64
    assert version.release_date == date(2026, 6, 1)


@pytest.mark.parametrize("confidence", ["-0.0001", "1.0001"])
def test_database_enforces_the_confidence_range(session, author_id, confidence):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, f"Mapping confidence {confidence}")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,confidence,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','close','proposed','ai_assisted',:confidence,:now,:now)"
            ),
            {
                "id": uuid.uuid4(), "concept": concept_id, "version": version_id,
                "confidence": confidence, "now": NOW,
            },
        )
        session.flush()


def test_a_verified_mapping_must_record_when_it_was_decided(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Undated mapping decision")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,reviewed_by_user_id,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','close','verified','manual',:actor,:now,:now)"
            ),
            {
                "id": uuid.uuid4(), "concept": concept_id, "version": version_id,
                "actor": author_id, "now": NOW,
            },
        )
        session.flush()


def test_evidence_must_be_a_json_object(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Mapping evidence shape")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,evidence_json,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','close','proposed','manual',"
                "'[\"list\"]'::jsonb,:now,:now)"
            ),
            {"id": uuid.uuid4(), "concept": concept_id, "version": version_id, "now": NOW},
        )
        session.flush()


def test_database_refuses_an_anonymous_verified_exact_mapping(session, author_id):
    """Specification section 16.2, as far as a constraint can carry it: only a
    deterministic import reaches verified `exact` with no named reviewer."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Anonymous exact mapping")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,evidence_json,"
                "reviewed_at,created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','exact','verified','ai_assisted',"
                "'{\"source_uri\": \"https://example.test\"}'::jsonb,:now,:now,:now)"
            ),
            {"id": uuid.uuid4(), "concept": concept_id, "version": version_id, "now": NOW},
        )
        session.flush()


def test_database_refuses_a_verified_exact_mapping_with_no_evidence(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Evidence-free exact mapping")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
                "external_identifier,mapping_type,mapping_status,mapping_method,reviewed_by_user_id,"
                "reviewed_at,created_at,updated_at) "
                "VALUES (:id,:concept,:version,'X-1','exact','verified','manual',:actor,:now,:now,:now)"
            ),
            {
                "id": uuid.uuid4(), "concept": concept_id, "version": version_id,
                "actor": author_id, "now": NOW,
            },
        )
        session.flush()


def test_a_concept_carrying_mappings_cannot_be_deleted(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Mapped concept")
    propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0002",
        mapping_type="related",
        mapping_method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(text("DELETE FROM semantic_concepts WHERE id=:id"), {"id": concept_id})
        session.flush()


# --------------------------------------------------------------------------
# The uniqueness rules
# --------------------------------------------------------------------------


def test_the_same_identifier_cannot_be_asserted_twice_while_live(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Duplicate assertion")
    session.flush()
    for _ in range(2):
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept_id,
            scheme_version_id=version_id,
            external_identifier="TEST-0003",
            mapping_type="close",
            mapping_method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_a_rejected_mapping_leaves_room_to_propose_the_same_identifier_again(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Second attempt")
    session.flush()
    first = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0004",
        mapping_type="exact",
        mapping_method="ai_assisted",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
        confidence=0.97,
    )
    session.flush()
    transition_concept_external_reference(
        session, first.id, target_status="rejected", occurred_at=NOW, reviewed_by_user_id=author_id
    )
    session.flush()

    second = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0004",
        mapping_type="close",
        mapping_method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    assert second.mapping_status == "proposed"
    assert first.mapping_status == "rejected"


def test_competing_candidate_identifiers_may_be_proposed_side_by_side(session, author_id):
    """Different external classes are rival candidates, not duplicates: a
    reviewer must be able to see them together before deciding."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Rival candidates")
    session.flush()
    for identifier in ("TEST-0005", "TEST-0006"):
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept_id,
            scheme_version_id=version_id,
            external_identifier=identifier,
            mapping_type="exact",
            mapping_method="ai_assisted",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
            confidence=0.88,
        )
    session.flush()
    assert len(list_concept_external_references(session, concept_id, mapping_status="proposed")) == 2


def test_database_refuses_a_second_verified_exact_mapping_in_one_release(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Two exact classes")
    session.flush()
    insert = text(
        "INSERT INTO concept_external_references (id,semantic_concept_id,scheme_version_id,"
        "external_identifier,mapping_type,mapping_status,mapping_method,evidence_json,"
        "reviewed_by_user_id,reviewed_at,created_at,updated_at) "
        "VALUES (:id,:concept,:version,:identifier,'exact','verified','manual',"
        "'{\"source_uri\": \"https://example.test\"}'::jsonb,:actor,:now,:now,:now)"
    )
    session.execute(
        insert,
        {
            "id": uuid.uuid4(), "concept": concept_id, "version": version_id,
            "identifier": "TEST-0007", "actor": author_id, "now": NOW,
        },
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            insert,
            {
                "id": uuid.uuid4(), "concept": concept_id, "version": version_id,
                "identifier": "TEST-0008", "actor": author_id, "now": NOW,
            },
        )
        session.flush()


def test_one_external_identifier_may_serve_several_concepts(session, author_id):
    """Only the forward direction is constrained. Two SymGov concepts may
    legitimately both map to one external class."""
    version_id = _version(session, author_id)
    first = _concept(session, author_id, "Shared external class A")
    second = _concept(session, author_id, "Shared external class B")
    session.flush()
    for concept_id in (first, second):
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept_id,
            scheme_version_id=version_id,
            external_identifier="TEST-0009",
            mapping_type="broader",
            mapping_method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
    session.flush()
    found = find_references_by_external_identifier(session, version_id, " TEST-0009 ")
    assert {reference.semantic_concept_id for reference in found} == {first, second}


def test_the_same_identifier_may_be_mapped_again_in_a_later_release(session, author_id):
    """Section 3.4: a mapping is an assertion about one release. Re-asserting
    it against the next release must not collide with the previous one."""
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"REL-{uuid.uuid4().hex[:8].upper()}",
        title="Two-release scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
    )
    session.flush()
    versions = [
        register_external_scheme_version(
            session, scheme_id=scheme.id, version_label=label, registered_at=NOW
        )
        for label in ("1.0", "2.0")
    ]
    session.flush()
    concept_id = _concept(session, author_id, "Stable across releases")
    session.flush()
    for version in versions:
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept_id,
            scheme_version_id=version.id,
            external_identifier="TEST-0010",
            mapping_type="exact",
            mapping_method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
    session.flush()
    assert len(list_concept_external_references(session, concept_id)) == 2


# --------------------------------------------------------------------------
# Governance rules
# --------------------------------------------------------------------------


def test_high_confidence_machine_output_is_still_only_proposed(session, author_id):
    """Specification section 8.4: confidence is not a governance state."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Confident mapping guess")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0011",
        mapping_type="exact",
        mapping_method="ai_assisted",
        proposed_at=NOW,
        confidence=0.99,
    )
    session.flush()
    assert reference.mapping_status == "proposed"
    assert reference.reviewed_at is None
    assert verified_exact_reference(session, concept_id, version_id) is None


def test_an_exact_mapping_cannot_be_verified_from_string_similarity(session, author_id):
    """Specification section 16.2, the rule a check constraint cannot see."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Similar label only")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0012",
        external_label="Gate valve",
        mapping_type="exact",
        mapping_method="ai_assisted",
        proposed_at=NOW,
        confidence=0.99,
        evidence={"similarity_score": 0.99},
    )
    session.flush()
    with pytest.raises(ValueError, match="string similarity"):
        transition_concept_external_reference(
            session,
            reference.id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
            verification_basis="string_similarity",
        )


def test_a_weaker_mapping_type_may_be_verified_from_string_similarity(session, author_id):
    """Section 8.1 prefers `close` over forcing an `exact` match, so the
    similarity basis has to remain usable for the weaker types."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Close enough")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0013",
        mapping_type="close",
        mapping_method="ai_assisted",
        proposed_at=NOW,
        confidence=0.8,
    )
    session.flush()
    transition_concept_external_reference(
        session,
        reference.id,
        target_status="verified",
        occurred_at=NOW,
        reviewed_by_user_id=author_id,
        verification_basis="string_similarity",
    )
    session.flush()
    assert reference.mapping_status == "verified"
    assert reference.evidence_json["verification_basis"] == "string_similarity"


def test_verifying_requires_a_basis_and_records_it(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Basis recorded")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0014",
        mapping_type="exact",
        mapping_method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
        evidence={"source_uri": "https://example.test/rdl/1.0/TEST-0014"},
    )
    session.flush()
    with pytest.raises(ValueError, match="verification basis"):
        transition_concept_external_reference(
            session, reference.id, target_status="verified", occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
    transition_concept_external_reference(
        session, reference.id, target_status="verified", occurred_at=NOW,
        reviewed_by_user_id=author_id, verification_basis="human_review",
    )
    session.flush()
    assert reference.evidence_json["verification_basis"] == "human_review"
    assert reference.evidence_json["source_uri"] == "https://example.test/rdl/1.0/TEST-0014"
    assert reference.reviewed_by_user_id == author_id


def test_an_exact_mapping_cannot_be_verified_without_evidence(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "No evidence at all")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0015",
        mapping_type="exact",
        mapping_method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises(ValueError, match="without evidence"):
        transition_concept_external_reference(
            session, reference.id, target_status="verified", occurred_at=NOW,
            reviewed_by_user_id=author_id, verification_basis="human_review",
        )


def test_ai_assisted_verification_requires_a_reviewer(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Needs a human reviewer")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0016",
        mapping_type="close",
        mapping_method="ai_assisted",
        proposed_at=NOW,
        confidence=0.99,
    )
    session.flush()
    with pytest.raises(ValueError, match="requires a reviewer"):
        transition_concept_external_reference(
            session, reference.id, target_status="verified", occurred_at=NOW,
            verification_basis="human_review",
        )


def test_a_deterministic_import_may_be_auto_verified_on_an_authoritative_basis(session, author_id):
    """Section 8.4 allows deterministic authoritative-source verification, and
    only that: an import claiming a human basis with no human is refused."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Deterministic mapping import")
    session.flush()
    reference = propose_concept_external_reference(
        session,
        semantic_concept_id=concept_id,
        scheme_version_id=version_id,
        external_identifier="TEST-0017",
        mapping_type="exact",
        mapping_method="imported",
        proposed_at=NOW,
        evidence={"source_uri": "https://example.test/rdl/1.0/TEST-0017"},
    )
    session.flush()
    with pytest.raises(ValueError, match="authoritative-source"):
        transition_concept_external_reference(
            session, reference.id, target_status="verified", occurred_at=NOW,
            verification_basis="human_review",
        )
    transition_concept_external_reference(
        session, reference.id, target_status="verified", occurred_at=NOW,
        verification_basis="authoritative_source",
    )
    session.flush()
    assert reference.mapping_status == "verified"
    assert reference.reviewed_by_user_id is None
    assert reference.reviewed_at == NOW


def test_verifying_a_new_exact_mapping_retires_the_previous_one(session, author_id):
    """The supersession pattern SM-P0-02 established: a newer decision retires
    the one it replaces rather than being refused."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Reconsidered exact mapping")
    session.flush()
    evidence = {"source_uri": "https://example.test/rdl/1.0"}
    original = propose_concept_external_reference(
        session, semantic_concept_id=concept_id, scheme_version_id=version_id,
        external_identifier="TEST-0018", mapping_type="exact", mapping_method="manual",
        proposed_at=NOW, proposed_by_user_id=author_id, evidence=evidence,
    )
    transition_concept_external_reference(
        session, original.id, target_status="verified", occurred_at=NOW,
        reviewed_by_user_id=author_id, verification_basis="human_review",
    )
    session.flush()
    assert verified_exact_reference(session, concept_id, version_id).id == original.id

    successor = propose_concept_external_reference(
        session, semantic_concept_id=concept_id, scheme_version_id=version_id,
        external_identifier="TEST-0019", mapping_type="exact", mapping_method="manual",
        proposed_at=NOW, proposed_by_user_id=author_id, evidence=evidence,
    )
    transition_concept_external_reference(
        session, successor.id, target_status="verified", occurred_at=NOW,
        reviewed_by_user_id=author_id, verification_basis="human_review",
    )
    session.flush()

    assert original.mapping_status == "retired"
    assert successor.mapping_status == "verified"
    assert verified_exact_reference(session, concept_id, version_id).id == successor.id


def test_several_non_exact_mappings_may_be_verified_in_one_release(session, author_id):
    """Section 3.3: one concept needs more than one external identifier and
    more than one kind of mapping."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Many mapping types")
    session.flush()
    for index, mapping_type in enumerate(("close", "broader", "narrower", "related")):
        reference = propose_concept_external_reference(
            session, semantic_concept_id=concept_id, scheme_version_id=version_id,
            external_identifier=f"TEST-002{index}", mapping_type=mapping_type,
            mapping_method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
        )
        transition_concept_external_reference(
            session, reference.id, target_status="verified", occurred_at=NOW,
            reviewed_by_user_id=author_id, verification_basis="human_review",
        )
    session.flush()
    assert len(list_concept_external_references(session, concept_id, mapping_status="verified")) == 4


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [("rejected", "verified"), ("retired", "verified"), ("verified", "rejected"), ("rejected", "retired")],
)
def test_illegal_status_transitions_are_refused(session, author_id, from_status, to_status):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, f"Mapping transition {from_status} {to_status}")
    session.flush()
    reference = propose_concept_external_reference(
        session, semantic_concept_id=concept_id, scheme_version_id=version_id,
        external_identifier="TEST-0030", mapping_type="close", mapping_method="manual",
        proposed_at=NOW, proposed_by_user_id=author_id,
    )
    transition_concept_external_reference(
        session, reference.id, target_status=from_status, occurred_at=NOW,
        reviewed_by_user_id=author_id,
        verification_basis="human_review" if from_status == "verified" else None,
    )
    session.flush()
    assert reference.mapping_status == from_status

    with pytest.raises(ValueError, match="cannot move from"):
        transition_concept_external_reference(
            session, reference.id, target_status=to_status, occurred_at=NOW,
            reviewed_by_user_id=author_id,
            verification_basis="human_review" if to_status == "verified" else None,
        )


def test_a_withdrawn_release_accepts_no_new_mappings(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Withdrawn release target")
    session.flush()
    set_external_scheme_version_status(
        session, version_id, target_status="withdrawn", occurred_at=NOW
    )
    session.flush()
    with pytest.raises(ValueError, match="accepts no new mappings"):
        propose_concept_external_reference(
            session, semantic_concept_id=concept_id, scheme_version_id=version_id,
            external_identifier="TEST-0031", mapping_type="close", mapping_method="manual",
            proposed_at=NOW, proposed_by_user_id=author_id,
        )


def test_a_deprecated_release_keeps_its_mappings_usable(session, author_id):
    """Section 3.4: an older release is superseded, not made untrue. What it
    said stays a valid historical assertion."""
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Superseded release")
    session.flush()
    reference = propose_concept_external_reference(
        session, semantic_concept_id=concept_id, scheme_version_id=version_id,
        external_identifier="TEST-0032", mapping_type="close", mapping_method="manual",
        proposed_at=NOW, proposed_by_user_id=author_id,
    )
    session.flush()
    set_external_scheme_version_status(
        session, version_id, target_status="deprecated", occurred_at=NOW
    )
    session.flush()
    transition_concept_external_reference(
        session, reference.id, target_status="verified", occurred_at=NOW,
        reviewed_by_user_id=author_id, verification_basis="human_review",
    )
    session.flush()
    assert reference.mapping_status == "verified"


def test_a_withdrawn_concept_accepts_no_new_mappings(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Concept to withdraw")
    session.execute(
        text("UPDATE semantic_concepts SET status='withdrawn' WHERE id=:id"), {"id": concept_id}
    )
    session.flush()
    session.expire_all()
    with pytest.raises(ValueError, match="accepts no new mappings"):
        propose_concept_external_reference(
            session, semantic_concept_id=concept_id, scheme_version_id=version_id,
            external_identifier="TEST-0033", mapping_type="close", mapping_method="manual",
            proposed_at=NOW, proposed_by_user_id=author_id,
        )


def test_a_withdrawn_scheme_accepts_no_new_releases(session):
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"GONE-{uuid.uuid4().hex[:8].upper()}",
        title="Retracted scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
    )
    session.flush()
    set_external_scheme_status(session, scheme.id, target_status="withdrawn", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="accepts no new versions"):
        register_external_scheme_version(
            session, scheme_id=scheme.id, version_label="1.0", registered_at=NOW
        )
    with pytest.raises(ValueError, match="cannot move from"):
        set_external_scheme_status(session, scheme.id, target_status="active", occurred_at=NOW)


def test_proposing_against_a_missing_concept_or_release_is_refused(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Real mapping concept")
    session.flush()
    with pytest.raises(LookupError, match="semantic concept not found"):
        propose_concept_external_reference(
            session, semantic_concept_id=uuid.uuid4(), scheme_version_id=version_id,
            external_identifier="TEST-0034", mapping_type="close", mapping_method="manual",
            proposed_at=NOW,
        )
    with pytest.raises(LookupError, match="external scheme version not found"):
        propose_concept_external_reference(
            session, semantic_concept_id=concept_id, scheme_version_id=uuid.uuid4(),
            external_identifier="TEST-0034", mapping_type="close", mapping_method="manual",
            proposed_at=NOW,
        )


# --------------------------------------------------------------------------
# Read services
# --------------------------------------------------------------------------


def test_listing_puts_the_exact_mapping_first_and_filters(session, author_id):
    version_id = _version(session, author_id)
    concept_id = _concept(session, author_id, "Mapping ordering")
    session.flush()
    for index, mapping_type in enumerate(("related", "broader", "exact")):
        propose_concept_external_reference(
            session, semantic_concept_id=concept_id, scheme_version_id=version_id,
            external_identifier=f"TEST-004{index}", mapping_type=mapping_type,
            mapping_method="manual", proposed_at=NOW, proposed_by_user_id=author_id,
        )
    session.flush()

    listed = list_concept_external_references(session, concept_id)
    assert [reference.mapping_type for reference in listed][0] == "exact"
    assert len(listed) == 3
    assert len(list_concept_external_references(session, concept_id, mapping_type="broader")) == 1

    with pytest.raises(ValueError):
        list_concept_external_references(session, concept_id, mapping_status="not_a_status")
    with pytest.raises(ValueError):
        list_concept_external_references(session, concept_id, mapping_type="not_a_type")


def test_releases_list_newest_first_with_undated_releases_last(session, author_id):
    scheme = register_external_semantic_scheme(
        session,
        scheme_code=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        title="Release ordering scheme",
        issuing_body="SymGov test fixture",
        registered_at=NOW,
    )
    session.flush()
    for label, release_date in (
        ("1.0", date(2024, 1, 1)),
        ("2.0", date(2026, 1, 1)),
        ("draft", None),
    ):
        register_external_scheme_version(
            session, scheme_id=scheme.id, version_label=label,
            registered_at=NOW, release_date=release_date,
        )
    session.flush()
    assert [
        version.version_label for version in list_external_scheme_versions(session, scheme.id)
    ] == ["2.0", "1.0", "draft"]

    with pytest.raises(ValueError):
        list_external_scheme_versions(session, scheme.id, status="not_a_status")


def test_scheme_lookup_is_case_insensitive_on_the_code(session):
    assert get_external_semantic_scheme(session, "cfihos-rdl") is not None
    assert get_external_semantic_scheme(session, "NOT-A-SCHEME") is None
    assert len(list_external_semantic_schemes(session, status="withdrawn")) == 0


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_all_three_tables_and_keeps_the_earlier_packages():
    with _database("symgov-schemes-down") as (engine, url, _raw):
        _alembic(url, "upgrade", SCHEME_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name IN ('external_semantic_schemes',"
                    "'external_semantic_scheme_versions','concept_external_references')"
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name IN ('semantic_concepts','semantic_concept_revisions',"
                    "'symbol_semantic_assignments')"
                )
            ).scalar_one() == 3
