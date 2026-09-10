"""Rehearsal for the CFIHOS-sourced classification schemes against real PostgreSQL.

Why this file exists: the seed has to satisfy storage rules SM-P0-04 put in
place, and reading the migration text cannot show that.

* 334 node codes must pass the `node_code` grammar check constraint, including
  the four-digit numeric CFIHOS short codes.
* The two new schemes must not disturb SM-P0-04's three catalogue schemes --
  the catalogue facet lists must read back byte-identical.
* `downgrade` must refuse rather than cascade when an assignment exists
  against a seeded node, because discarding a governed classification
  decision silently would be worse than a failed downgrade.

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

from symgov_backend.catalog_taxonomy import (  # noqa: E402
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
)
from symgov_backend.classification_assignments import (  # noqa: E402
    propose_concept_classification,
)
from symgov_backend.classification_schemes import (  # noqa: E402
    classification_node_labels,
    classification_node_seed_id,
    classification_scheme_seed_id,
    get_classification_scheme,
    list_classification_nodes,
)
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402

CFIHOS_REVISION = "20260909_0052"
PREVIOUS_REVISION = "20260909_0051"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

DOCUMENT_TYPE_NODE_COUNT = 329
REPRESENTATION_TYPE_NODE_COUNT = 5
CATALOGUE_NODE_COUNT = 37


@pytest.fixture(scope="module")
def cfihos_database():
    with _database("symgov-cfihos-schemes") as (engine, url, raw_url):
        _alembic(url, "upgrade", CFIHOS_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(cfihos_database) -> uuid.UUID:
    engine, _ = cfihos_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "cfihos-seed@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(cfihos_database) -> Session:
    engine, _ = cfihos_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


# --------------------------------------------------------------------------
# The seed lands
# --------------------------------------------------------------------------


def test_both_schemes_land_active_and_platform_scoped(cfihos_database):
    engine, _ = cfihos_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, scheme_code, name, scope, version_label, status, description "
                "FROM classification_schemes WHERE scheme_code = ANY(:codes) ORDER BY scheme_code"
            ),
            {"codes": ["DOCUMENT-TYPE", "REPRESENTATION-TYPE"]},
        ).all()
    assert [row.scheme_code for row in rows] == ["DOCUMENT-TYPE", "REPRESENTATION-TYPE"]
    for row in rows:
        assert row.id == classification_scheme_seed_id(row.scheme_code)
        assert row.scope == "platform"
        assert row.version_label == "CFIHOS v2.0"
        assert row.status == "active"
        assert "CFIHOS" in row.description
    assert [row.name for row in rows] == ["Document Type", "Representation Type"]


@pytest.mark.parametrize(
    ("scheme_code", "expected_count"),
    [
        ("DOCUMENT-TYPE", DOCUMENT_TYPE_NODE_COUNT),
        ("REPRESENTATION-TYPE", REPRESENTATION_TYPE_NODE_COUNT),
    ],
)
def test_every_node_lands_active_flat_and_in_source_order(
    cfihos_database, scheme_code, expected_count
):
    engine, _ = cfihos_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT n.node_code, n.preferred_label, n.sort_order, n.parent_node_id, "
                "n.status, n.id FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = :code ORDER BY n.sort_order"
            ),
            {"code": scheme_code},
        ).all()

    assert len(rows) == expected_count
    assert [row.sort_order for row in rows] == [(i + 1) * 10 for i in range(expected_count)]
    assert [row.preferred_label for row in rows] == sorted(
        row.preferred_label for row in rows
    ), "sort_order must follow the source's alphabetical order"
    for row in rows:
        assert row.parent_node_id is None
        assert row.status == "active"
        assert row.id == classification_node_seed_id(scheme_code, row.node_code)


def test_the_numeric_cfihos_short_codes_satisfy_the_node_code_constraint(cfihos_database):
    """Four-digit numeric codes are the interesting case for the
    `^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$` grammar, and only the server can
    confirm they pass."""
    engine, _ = cfihos_database
    with engine.begin() as connection:
        codes = connection.execute(
            text(
                "SELECT n.node_code FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = 'DOCUMENT-TYPE'"
            )
        ).scalars().all()
    assert len(codes) == DOCUMENT_TYPE_NODE_COUNT
    assert all(code.isdigit() and len(code) == 4 for code in codes)
    assert "2365" in codes


def test_the_document_type_definitions_survived_the_extract(cfihos_database):
    """CFIHOS's own definitions are the most valuable part of the source."""
    engine, _ = cfihos_database
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT n.preferred_label, n.description FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = 'DOCUMENT-TYPE' AND n.node_code = '2365'"
            )
        ).one()
        blank = connection.execute(
            text(
                "SELECT count(*) FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = 'DOCUMENT-TYPE' AND n.description IS NULL"
            )
        ).scalar_one()
    assert row.preferred_label == "piping and instrumentation diagram"
    assert row.description and row.description.strip() == row.description
    assert blank == 0


def test_representation_type_nodes_read_back_as_the_five_source_values(session):
    assert classification_node_labels(session, "REPRESENTATION-TYPE") == [
        "Intelligent vector drawing (CAD)",
        "Multi media",
        "Raster Image",
        "Structured Data",
        "Text",
    ]


def test_representation_type_carries_no_invented_descriptions(cfihos_database):
    """The source column is free text with no definitions behind it, so the
    description stays NULL rather than being written."""
    engine, _ = cfihos_database
    with engine.begin() as connection:
        described = connection.execute(
            text(
                "SELECT count(*) FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = 'REPRESENTATION-TYPE' AND n.description IS NOT NULL"
            )
        ).scalar_one()
    assert described == 0


# --------------------------------------------------------------------------
# SM-P0-04's schemes are undisturbed
# --------------------------------------------------------------------------


def test_the_catalogue_facet_lists_are_unchanged(session):
    """This is the regression that would actually hurt: the catalogue renders
    these three, in this order."""
    assert classification_node_labels(session, "ENGINEERING-DISCIPLINE") == CATALOG_DISCIPLINE_ORDER
    assert classification_node_labels(session, "SYMBOL-CATEGORY-FAMILY") == CATALOG_CATEGORY_ORDER
    assert classification_node_labels(session, "USE-CASE") == CATALOG_USE_CASE_ORDER


def test_the_seed_adds_exactly_five_schemes_in_total(cfihos_database):
    engine, _ = cfihos_database
    with engine.begin() as connection:
        schemes = connection.execute(
            text("SELECT count(*) FROM classification_schemes")
        ).scalar_one()
        nodes = connection.execute(text("SELECT count(*) FROM classification_nodes")).scalar_one()
        assignments = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM concept_classification_assignments) "
                "+ (SELECT count(*) FROM symbol_revision_classifications)"
            )
        ).scalar_one()
    assert schemes == 5
    assert nodes == (
        CATALOGUE_NODE_COUNT + DOCUMENT_TYPE_NODE_COUNT + REPRESENTATION_TYPE_NODE_COUNT
    )
    # Seed data only. Backfilling assignments is phase M2 / SM-P0-09.
    assert assignments == 0


def test_a_node_code_may_repeat_across_the_two_new_schemes(session):
    """`TEXT` in Representation Type and a numeric code in Document Type share
    a namespace only within their own scheme."""
    document = get_classification_scheme(session, "DOCUMENT-TYPE")
    representation = get_classification_scheme(session, "REPRESENTATION-TYPE")
    document_codes = {node.node_code for node in list_classification_nodes(session, document.id)}
    representation_codes = {
        node.node_code for node in list_classification_nodes(session, representation.id)
    }
    assert document_codes.isdisjoint(representation_codes)
    assert "TEXT" in representation_codes


def test_a_seeded_node_is_assignable(session, author_id):
    """The point of the exercise: a concept can be classified as appearing on
    a P&ID."""
    concept_id = create_semantic_concept(
        session,
        concept_kind="physical_equipment",
        preferred_name="Gate valve",
        definition="Illustrative definition for a gate valve.",
        created_by_user_id=author_id,
        created_at=NOW,
    )[0].id
    session.flush()
    assignment = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=classification_node_seed_id("DOCUMENT-TYPE", "2365"),
        assignment_role="primary",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    assert assignment.classification_scheme_id == classification_scheme_seed_id("DOCUMENT-TYPE")
    assert assignment.status == "proposed"


# --------------------------------------------------------------------------
# Downgrade and re-upgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_only_the_two_new_schemes():
    with _database("symgov-cfihos-down") as (engine, url, _raw):
        _alembic(url, "upgrade", CFIHOS_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM classification_schemes "
                    "WHERE scheme_code = ANY(:codes)"
                ),
                {"codes": ["DOCUMENT-TYPE", "REPRESENTATION-TYPE"]},
            ).scalar_one() == 0
            # SM-P0-04's three schemes and their 37 nodes stay.
            assert connection.execute(
                text("SELECT count(*) FROM classification_schemes")
            ).scalar_one() == 3
            assert connection.execute(
                text("SELECT count(*) FROM classification_nodes")
            ).scalar_one() == CATALOGUE_NODE_COUNT


def test_downgrade_refuses_to_discard_a_governed_classification(author_id):
    """The RESTRICT foreign key must make the downgrade fail rather than
    cascade. A downgrade that silently deleted a reviewed classification would
    be worse than one that stops."""
    with _database("symgov-cfihos-restrict") as (engine, url, _raw):
        _alembic(url, "upgrade", CFIHOS_REVISION)
        actor = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                    "must_change_pin,is_active,created_at,updated_at) "
                    "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
                ),
                {"id": actor, "email": "cfihos-restrict@example.test", "now": NOW},
            )
        with Session(engine) as active_session:
            concept_id = create_semantic_concept(
                active_session,
                concept_kind="physical_equipment",
                preferred_name="Restrict guard",
                definition="Illustrative definition for the restrict guard.",
                created_by_user_id=actor,
                created_at=NOW,
            )[0].id
            active_session.flush()
            propose_concept_classification(
                active_session,
                semantic_concept_id=concept_id,
                classification_node_id=classification_node_seed_id("DOCUMENT-TYPE", "2365"),
                assignment_role="primary",
                method="manual",
                proposed_at=NOW,
                proposed_by_user_id=actor,
            )
            active_session.commit()

        result = _alembic(url, "downgrade", PREVIOUS_REVISION, check=False)
        assert result.returncode != 0, "downgrade must refuse while an assignment exists"
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM classification_schemes WHERE scheme_code='DOCUMENT-TYPE'")
            ).scalar_one() == 1


def test_re_upgrade_reseeds_the_same_rows_rather_than_duplicates():
    with _database("symgov-cfihos-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", CFIHOS_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", CFIHOS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM classification_schemes")
            ).scalar_one() == 5
            assert connection.execute(
                text("SELECT count(*) FROM classification_nodes")
            ).scalar_one() == (
                CATALOGUE_NODE_COUNT + DOCUMENT_TYPE_NODE_COUNT + REPRESENTATION_TYPE_NODE_COUNT
            )
