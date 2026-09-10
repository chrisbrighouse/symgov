"""Migration and service rehearsal for SM-P0-04 against a real PostgreSQL server.

Why this file exists: the rules that make a classification trustworthy are
storage-level, and none of them can be proven by reading the migration text.

* A node's parent, and an assignment's node, must belong to the scheme the row
  names. Both are composite foreign keys onto
  `classification_nodes (id, scheme_id)`.
* At most one verified primary *per scheme* per target, and one live
  assignment per (target, node), are partial unique indexes.
* Specification section 12.3 -- a backfilled row is never verified -- is a
  check constraint, and PostgreSQL must refuse the row.
* The seeded facet orders must actually land, in the order
  `catalog_taxonomy.py` lists them. That ordering is what the catalogue UI
  renders today.

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
    find_classified_targets,
    list_concept_classifications,
    list_symbol_revision_classifications,
    propose_concept_classification,
    propose_symbol_revision_classification,
    transition_concept_classification,
    transition_symbol_revision_classification,
    verified_primary_concept_classification,
    verified_primary_symbol_classification,
)
from symgov_backend.classification_schemes import (  # noqa: E402
    SEED_CLASSIFICATION_SCHEMES,
    add_classification_node,
    classification_node_labels,
    classification_node_seed_id,
    classification_scheme_seed_id,
    derive_classification_node_code,
    get_classification_scheme,
    list_classification_nodes,
    list_classification_schemes,
    register_classification_scheme,
    seed_classification_schemes,
    set_classification_node_status,
    set_classification_scheme_status,
)
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    ClassificationScheme,
    ConceptClassificationAssignment,
    SymbolRevisionClassificationAssignment,
)
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402

CLASSIFICATION_REVISION = "20260909_0051"
PREVIOUS_REVISION = "20260909_0050"

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

CLASSIFICATION_TABLES = (
    "classification_schemes",
    "classification_nodes",
    "concept_classification_assignments",
    "symbol_revision_classifications",
)

CLASSIFICATION_MODELS = (
    ClassificationScheme,
    ClassificationNode,
    ConceptClassificationAssignment,
    SymbolRevisionClassificationAssignment,
)

EARLIER_SEMANTIC_TABLES = (
    "semantic_concepts",
    "semantic_concept_revisions",
    "symbol_semantic_assignments",
    "external_semantic_schemes",
    "external_semantic_scheme_versions",
    "concept_external_references",
)


@pytest.fixture(scope="module")
def classification_database():
    with _database("symgov-classification") as (engine, url, raw_url):
        _alembic(url, "upgrade", CLASSIFICATION_REVISION)
        yield engine, url


@pytest.fixture(scope="module")
def author_id(classification_database) -> uuid.UUID:
    engine, _ = classification_database
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": identifier, "email": "classification-author@example.test", "now": NOW},
        )
    return identifier


@pytest.fixture()
def session(classification_database) -> Session:
    engine, _ = classification_database
    with Session(engine) as active_session:
        yield active_session
        active_session.rollback()


def _seeded_node(session: Session, scheme_code: str, label: str) -> uuid.UUID:
    return classification_node_seed_id(scheme_code, derive_classification_node_code(label))


def _scheme(session: Session, author: uuid.UUID, *, status: str = "active"):
    """Register a throwaway scheme."""
    scheme = register_classification_scheme(
        session,
        scheme_code=f"TEST-{uuid.uuid4().hex[:10].upper()}",
        name="Illustrative test scheme",
        registered_at=NOW,
        status=status,
        created_by_user_id=author,
    )
    session.flush()
    return scheme


def _node(session: Session, scheme_id: uuid.UUID, label: str, **kwargs):
    node = add_classification_node(
        session, scheme_id=scheme_id, preferred_label=label, added_at=NOW,
        status=kwargs.pop("status", "active"), **kwargs,
    )
    session.flush()
    return node


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


def _symbol_revision(session: Session, author: uuid.UUID) -> uuid.UUID:
    """Seed a draft governed symbol revision.

    Deliberately draft: 20260826_0031's publication invariant would otherwise
    demand a canonical catalog identifier, which is irrelevant here.
    """
    symbol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    slug = f"classification-symbol-{uuid.uuid4().hex[:10]}"
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
    session.flush()
    return revision_id


# --------------------------------------------------------------------------
# Migration shape
# --------------------------------------------------------------------------


def test_upgrade_creates_all_four_tables(classification_database):
    engine, _ = classification_database
    with engine.begin() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name = ANY(:names)"
                ),
                {"names": list(CLASSIFICATION_TABLES)},
            ).scalars()
        )
    assert tables == set(CLASSIFICATION_TABLES)


def test_upgrade_left_the_earlier_semantic_packages_untouched(classification_database):
    engine, _ = classification_database
    with engine.begin() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name = ANY(:names)"
            ),
            {"names": list(EARLIER_SEMANTIC_TABLES)},
        ).scalar_one()
    assert count == len(EARLIER_SEMANTIC_TABLES)


def test_every_constraint_name_survived_the_63_character_limit(classification_database):
    """A name PostgreSQL had to truncate would come back shortened or
    hash-suffixed. Both failure modes named in migration 20260909_0051's
    docstring show up here."""
    engine, _ = classification_database
    with engine.begin() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = ANY(:names)"
                ),
                {"names": list(CLASSIFICATION_TABLES)},
            ).scalars()
        )
    for expected in (
        "fk_symbol_revision_classifications_classification_node_id",
        "fk_symbol_revision_classifications_symbol_revision_id",
        "fk_concept_classification_assignments_classification_node_id",
        "fk_classification_nodes_parent_node_id_scheme_id",
        "ck_symbol_revision_classifications_backfill_not_verified",
        "ck_concept_classification_assignments_backfill_not_verified",
        "uq_classification_nodes_id_scheme_id",
    ):
        assert expected in names, sorted(names)
    assert all(len(name) <= 63 for name in names)
    for table in CLASSIFICATION_TABLES:
        assert not any(name.startswith(f"ck_{table}_ck_") for name in names)


def test_the_new_tables_are_not_recorded_as_pre_existing_name_drift():
    """The repair guard from SM-P0-03 is allowed to shrink, never grow."""
    from test_semantic_constraint_name_repair_postgresql import PRE_EXISTING_NAME_DRIFT

    assert PRE_EXISTING_NAME_DRIFT.isdisjoint(set(CLASSIFICATION_TABLES))


@pytest.mark.parametrize("model", CLASSIFICATION_MODELS, ids=lambda m: m.__tablename__)
def test_the_new_tables_carry_exactly_the_orm_constraint_names(classification_database, model):
    """The parity guard SM-P0-03 added, applied to these four tables at the
    revision that creates them. Comparing against the names SQLAlchemy would
    *emit* matters: `constraint.name` in the metadata is the pre-truncation
    name, so a naive comparison would report drift on every long name."""
    from test_semantic_constraint_name_repair_postgresql import (
        _database_names,
        _expected_names,
    )

    engine, _ = classification_database
    with engine.begin() as connection:
        actual = _database_names(connection).get(model.__tablename__, set())
    assert actual == _expected_names(model.__table__)


# --------------------------------------------------------------------------
# Seed (section 12.1 phase M1)
# --------------------------------------------------------------------------


def test_the_migration_seeded_exactly_the_three_schemes(classification_database):
    engine, _ = classification_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, scheme_code, name, scope, version_label, status, description "
                "FROM classification_schemes ORDER BY scheme_code"
            )
        ).all()
        assignments = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM concept_classification_assignments) "
                "+ (SELECT count(*) FROM symbol_revision_classifications)"
            )
        ).scalar_one()

    seeded = {row.scheme_code: row for row in rows}
    assert set(seeded) == {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY", "USE-CASE"}
    for definition in SEED_CLASSIFICATION_SCHEMES:
        row = seeded[definition["scheme_code"]]
        assert row.id == classification_scheme_seed_id(definition["scheme_code"])
        assert row.name == definition["name"]
        assert row.description == definition["description"]
        assert row.scope == "platform"
        assert row.version_label == "1"
        assert row.status == "active"
    # Phase M1 seeds vocabulary only. Backfilling assignments is phase M2.
    assert assignments == 0


@pytest.mark.parametrize(
    ("scheme_code", "expected_labels"),
    [
        ("ENGINEERING-DISCIPLINE", CATALOG_DISCIPLINE_ORDER),
        ("SYMBOL-CATEGORY-FAMILY", CATALOG_CATEGORY_ORDER),
        ("USE-CASE", CATALOG_USE_CASE_ORDER),
    ],
)
def test_sort_order_round_trips_the_catalogue_list_order(
    classification_database, scheme_code, expected_labels
):
    """This ordering is what the catalogue UI renders today, so losing it
    would be a visible regression."""
    engine, _ = classification_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT n.node_code, n.preferred_label, n.sort_order, n.parent_node_id, n.status "
                "FROM classification_nodes n JOIN classification_schemes s ON s.id = n.scheme_id "
                "WHERE s.scheme_code = :code ORDER BY n.sort_order"
            ),
            {"code": scheme_code},
        ).all()

    assert [row.preferred_label for row in rows] == list(expected_labels)
    assert [row.sort_order for row in rows] == [
        (index + 1) * 10 for index in range(len(expected_labels))
    ]
    for row in rows:
        assert row.node_code == derive_classification_node_code(row.preferred_label)
        # The hard-coded orders are flat lists; no hierarchy is invented.
        assert row.parent_node_id is None
        assert row.status == "active"


def test_the_seed_identifiers_are_the_deterministic_urn_hashes(classification_database):
    engine, _ = classification_database
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT s.scheme_code, n.node_code, n.id FROM classification_nodes n "
                "JOIN classification_schemes s ON s.id = n.scheme_id"
            )
        ).all()
    assert len(rows) == 37
    for row in rows:
        assert row.id == classification_node_seed_id(row.scheme_code, row.node_code)


def test_the_service_read_returns_the_catalogue_facet_lists(session):
    """The read that replaces the hard-coded constants once SM-P0-05 lands."""
    assert classification_node_labels(session, "ENGINEERING-DISCIPLINE") == CATALOG_DISCIPLINE_ORDER
    assert classification_node_labels(session, "SYMBOL-CATEGORY-FAMILY") == CATALOG_CATEGORY_ORDER
    assert classification_node_labels(session, "use-case") == CATALOG_USE_CASE_ORDER
    with pytest.raises(LookupError):
        classification_node_labels(session, "NOT-A-SCHEME")


def test_seeding_again_is_idempotent(session):
    before_schemes = len(list_classification_schemes(session))
    seeded = seed_classification_schemes(session, seeded_at=NOW)
    session.flush()
    assert len(seeded) == 3
    assert len(list_classification_schemes(session)) == before_schemes
    for definition in SEED_CLASSIFICATION_SCHEMES:
        scheme = get_classification_scheme(session, definition["scheme_code"])
        assert scheme.id == classification_scheme_seed_id(definition["scheme_code"])
        assert [
            node.preferred_label for node in list_classification_nodes(session, scheme.id)
        ] == list(definition["labels"])


# --------------------------------------------------------------------------
# Scheme and node storage guarantees
# --------------------------------------------------------------------------


def test_scheme_codes_are_unique(session, author_id):
    code = f"UNIQ-{uuid.uuid4().hex[:8].upper()}"
    for _ in range(2):
        register_classification_scheme(
            session, scheme_code=code, name="Duplicate scheme", registered_at=NOW
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_a_node_code_is_unique_within_its_scheme(session, author_id):
    scheme = _scheme(session, author_id)
    _node(session, scheme.id, "Valves")
    add_classification_node(
        session, scheme_id=scheme.id, preferred_label="Valves", added_at=NOW, status="active"
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_the_same_node_code_may_exist_in_two_schemes(session, author_id):
    """Uniqueness is per scheme: "Electrical" is a legitimate discipline and a
    legitimate category."""
    first, second = _scheme(session, author_id), _scheme(session, author_id)
    _node(session, first.id, "Electrical")
    _node(session, second.id, "Electrical")
    session.flush()
    assert len(list_classification_nodes(session, first.id)) == 1
    assert len(list_classification_nodes(session, second.id)) == 1


def test_database_refuses_a_node_that_is_its_own_parent(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Self parent")
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text("UPDATE classification_nodes SET parent_node_id = id WHERE id = :id"),
            {"id": node.id},
        )
        session.flush()


def test_database_refuses_a_parent_in_another_scheme(session, author_id):
    """The composite foreign key onto (id, scheme_id) is what makes this a
    storage guarantee rather than a service-layer convention."""
    first, second = _scheme(session, author_id), _scheme(session, author_id)
    parent = _node(session, first.id, "Foreign parent")
    child = _node(session, second.id, "Adopted child")
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text("UPDATE classification_nodes SET parent_node_id = :parent WHERE id = :id"),
            {"parent": parent.id, "id": child.id},
        )
        session.flush()


def test_the_service_refuses_a_parent_in_another_scheme(session, author_id):
    first, second = _scheme(session, author_id), _scheme(session, author_id)
    parent = _node(session, first.id, "Other scheme parent")
    with pytest.raises(ValueError, match="same scheme"):
        add_classification_node(
            session,
            scheme_id=second.id,
            preferred_label="Cross scheme child",
            parent_node_id=parent.id,
            added_at=NOW,
        )


def test_a_node_accepts_a_parent_inside_its_own_scheme(session, author_id):
    scheme = _scheme(session, author_id)
    parent = _node(session, scheme.id, "Valves")
    child = _node(session, scheme.id, "Gate valves", parent_node_id=parent.id)
    session.flush()
    assert child.parent_node_id == parent.id
    assert [node.id for node in list_classification_nodes(session, scheme.id, top_level_only=True)] == [
        parent.id
    ]
    assert [
        node.id for node in list_classification_nodes(session, scheme.id, parent_node_id=parent.id)
    ] == [child.id]


def test_the_service_refuses_a_parent_chain_that_closes_a_cycle(session, author_id):
    """No check constraint can see a two-hop cycle, so the service walks to
    the root before accepting a parent."""
    scheme = _scheme(session, author_id)
    grandparent = _node(session, scheme.id, "Level one")
    parent = _node(session, scheme.id, "Level two", parent_node_id=grandparent.id)
    session.execute(
        text("UPDATE classification_nodes SET parent_node_id = :parent WHERE id = :id"),
        {"parent": parent.id, "id": grandparent.id},
    )
    session.flush()
    session.expire_all()
    with pytest.raises(ValueError, match="cycle"):
        add_classification_node(
            session,
            scheme_id=scheme.id,
            preferred_label="Level three",
            parent_node_id=parent.id,
            added_at=NOW,
        )


def test_a_node_with_children_cannot_be_deleted(session, author_id):
    scheme = _scheme(session, author_id)
    parent = _node(session, scheme.id, "Retained parent")
    _node(session, scheme.id, "Retained child", parent_node_id=parent.id)
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text("DELETE FROM classification_nodes WHERE id = :id"), {"id": parent.id}
        )
        session.flush()


def test_a_scheme_carrying_nodes_cannot_be_deleted(session, author_id):
    scheme = _scheme(session, author_id)
    _node(session, scheme.id, "Retained node")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text("DELETE FROM classification_schemes WHERE id = :id"), {"id": scheme.id}
        )
        session.flush()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("scheme_code", "not upper case"),
        ("scheme_code", "TRAILING-"),
        ("scope", "organization"),
        ("status", "archived"),
        ("version_label", "   "),
    ],
)
def test_database_enforces_the_scheme_grammar_scope_and_status(session, column, value):
    row = {
        "id": uuid.uuid4(),
        "scheme_code": f"OK-{uuid.uuid4().hex[:8].upper()}",
        "scope": "platform",
        "status": "active",
        "version_label": "1",
        "now": NOW,
    }
    row[column] = value
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO classification_schemes (id,scheme_code,name,scope,version_label,"
                "status,created_at,updated_at) "
                "VALUES (:id,:scheme_code,'Name',:scope,:version_label,:status,:now,:now)"
            ),
            row,
        )
        session.flush()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("node_code", "not upper case"),
        ("node_code", "TRAILING_"),
        ("preferred_label", "   "),
        ("sort_order", -1),
        ("status", "archived"),
    ],
)
def test_database_enforces_the_node_grammar_order_and_status(
    session, author_id, column, value
):
    scheme = _scheme(session, author_id)
    row = {
        "id": uuid.uuid4(),
        "scheme_id": scheme.id,
        "node_code": f"OK_{uuid.uuid4().hex[:8].upper()}",
        "preferred_label": "Label",
        "sort_order": 10,
        "status": "active",
        "now": NOW,
    }
    row[column] = value
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO classification_nodes (id,scheme_id,node_code,preferred_label,"
                "sort_order,status,created_at,updated_at) "
                "VALUES (:id,:scheme_id,:node_code,:preferred_label,:sort_order,:status,:now,:now)"
            ),
            row,
        )
        session.flush()


def test_a_withdrawn_scheme_accepts_no_new_nodes(session, author_id):
    scheme = _scheme(session, author_id)
    set_classification_scheme_status(session, scheme.id, target_status="withdrawn", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="accepts no new nodes"):
        add_classification_node(
            session, scheme_id=scheme.id, preferred_label="Too late", added_at=NOW
        )
    with pytest.raises(ValueError, match="cannot move from"):
        set_classification_scheme_status(
            session, scheme.id, target_status="active", occurred_at=NOW
        )


def test_appending_a_node_without_a_sort_order_lands_after_the_last(session, author_id):
    scheme = _scheme(session, author_id)
    _node(session, scheme.id, "First", sort_order=10)
    _node(session, scheme.id, "Second", sort_order=20)
    appended = _node(session, scheme.id, "Third")
    session.flush()
    assert appended.sort_order == 30
    assert [node.preferred_label for node in list_classification_nodes(session, scheme.id)] == [
        "First",
        "Second",
        "Third",
    ]


def test_a_duplicate_sort_order_still_orders_deterministically(session, author_id):
    """`sort_order` is deliberately not unique, so a node can be inserted
    between two existing ones. `preferred_label` breaks the tie."""
    scheme = _scheme(session, author_id)
    _node(session, scheme.id, "Beta", sort_order=10)
    _node(session, scheme.id, "Alpha", sort_order=10)
    session.flush()
    assert [node.preferred_label for node in list_classification_nodes(session, scheme.id)] == [
        "Alpha",
        "Beta",
    ]


# --------------------------------------------------------------------------
# Assignment storage guarantees
# --------------------------------------------------------------------------


def test_database_refuses_an_assignment_whose_scheme_disagrees_with_its_node(
    session, author_id
):
    """Both halves of the composite foreign key must line up, which is what
    stops `classification_scheme_id` being a lie about the node."""
    first, second = _scheme(session, author_id), _scheme(session, author_id)
    node = _node(session, first.id, "Honest node")
    concept_id = _concept(session, author_id, "Mismatched scheme")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_classification_assignments (id,semantic_concept_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:node,:scheme,'primary','proposed','manual',:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "concept": concept_id,
                "node": node.id,
                "scheme": second.id,
                "now": NOW,
            },
        )
        session.flush()


def test_database_refuses_an_assignment_to_a_node_that_does_not_exist(session, author_id):
    concept_id = _concept(session, author_id, "Dangling node")
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_classification_assignments (id,semantic_concept_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:node,:scheme,'primary','proposed','manual',:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "concept": concept_id,
                "node": uuid.uuid4(),
                "scheme": uuid.uuid4(),
                "now": NOW,
            },
        )
        session.flush()


def test_a_node_carrying_assignments_cannot_be_deleted(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Assigned node")
    concept_id = _concept(session, author_id, "Assignment holder")
    propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="primary",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(text("DELETE FROM classification_nodes WHERE id = :id"), {"id": node.id})
        session.flush()


def test_the_service_fills_the_scheme_in_from_the_node(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Derived scheme")
    concept_id = _concept(session, author_id, "Scheme derivation")
    assignment = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="secondary",
        method="rule",
        proposed_at=NOW,
        confidence=0.75,
        evidence={"rule": "illustrative"},
    )
    session.flush()
    assert assignment.classification_scheme_id == scheme.id
    assert assignment.status == "proposed"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("assignment_role", "proposed"),
        ("assignment_role", "not_a_role"),
        ("status", "not_a_status"),
        ("method", "imported"),
        ("method", "not_a_method"),
    ],
)
def test_database_enforces_the_concept_assignment_vocabularies(
    session, author_id, column, value
):
    """`proposed` as a role and `imported` as a method are both rejected: the
    first is a governance status, the second belongs to
    `concept_external_references`."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Vocabulary node")
    concept_id = _concept(session, author_id, f"Vocabulary {column} {value}")
    session.flush()
    row = {
        "id": uuid.uuid4(),
        "concept": concept_id,
        "node": node.id,
        "scheme": scheme.id,
        "assignment_role": "secondary",
        "status": "proposed",
        "method": "manual",
        "now": NOW,
    }
    row[column] = value
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO concept_classification_assignments (id,semantic_concept_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "created_at,updated_at) "
                "VALUES (:id,:concept,:node,:scheme,:assignment_role,:status,:method,:now,:now)"
            ),
            row,
        )
        session.flush()


def test_a_symbol_revision_classification_has_no_inherited_role(session, author_id):
    """Section 7.8 lists no `inherited`: a revision inherits nothing, its
    concept does."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Inheritance node")
    revision_id = _symbol_revision(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_revision_classifications (id,symbol_revision_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "created_at,updated_at) "
                "VALUES (:id,:revision,:node,:scheme,'inherited','proposed','manual',:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "revision": revision_id,
                "node": node.id,
                "scheme": scheme.id,
                "now": NOW,
            },
        )
        session.flush()


@pytest.mark.parametrize("confidence", ["-0.0001", "1.0001"])
def test_database_enforces_the_confidence_range(session, author_id, confidence):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Confidence node")
    revision_id = _symbol_revision(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_revision_classifications (id,symbol_revision_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "confidence,created_at,updated_at) "
                "VALUES (:id,:revision,:node,:scheme,'secondary','proposed','ai_assisted',"
                ":confidence,:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "revision": revision_id,
                "node": node.id,
                "scheme": scheme.id,
                "confidence": confidence,
                "now": NOW,
            },
        )
        session.flush()


def test_database_refuses_evidence_that_is_not_an_object(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Evidence node")
    revision_id = _symbol_revision(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_revision_classifications (id,symbol_revision_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "evidence_json,created_at,updated_at) "
                "VALUES (:id,:revision,:node,:scheme,'secondary','proposed','manual',"
                "'[]'::jsonb,:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "revision": revision_id,
                "node": node.id,
                "scheme": scheme.id,
                "now": NOW,
            },
        )
        session.flush()


def test_database_refuses_a_verified_row_with_no_decision_time(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Undated decision")
    revision_id = _symbol_revision(session, author_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO symbol_revision_classifications (id,symbol_revision_id,"
                "classification_node_id,classification_scheme_id,assignment_role,status,method,"
                "created_at,updated_at) "
                "VALUES (:id,:revision,:node,:scheme,'primary','verified','manual',:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "revision": revision_id,
                "node": node.id,
                "scheme": scheme.id,
                "now": NOW,
            },
        )
        session.flush()


# --------------------------------------------------------------------------
# Section 12.3: a backfilled row is never verified
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "target_column", "target"),
    [
        ("concept_classification_assignments", "semantic_concept_id", "concept"),
        ("symbol_revision_classifications", "symbol_revision_id", "revision"),
    ],
)
def test_database_refuses_a_verified_backfilled_row(
    session, author_id, table, target_column, target
):
    """Specification section 12.3: existing published symbols must not be
    silently reclassified as verified."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, f"Backfill {table}")
    target_id = (
        _concept(session, author_id, f"Backfill {table}")
        if target == "concept"
        else _symbol_revision(session, author_id)
    )
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                f"INSERT INTO {table} (id,{target_column},classification_node_id,"
                "classification_scheme_id,assignment_role,status,method,reviewed_at,"
                "created_at,updated_at) "
                "VALUES (:id,:target,:node,:scheme,'primary','verified','legacy_backfill',"
                ":now,:now,:now)"
            ),
            {
                "id": uuid.uuid4(),
                "target": target_id,
                "node": node.id,
                "scheme": scheme.id,
                "now": NOW,
            },
        )
        session.flush()


def test_a_backfilled_proposal_is_allowed_and_stays_proposed(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Legacy category")
    revision_id = _symbol_revision(session, author_id)
    assignment = propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision_id,
        classification_node_id=node.id,
        assignment_role="primary",
        method="legacy_backfill",
        proposed_at=NOW,
        evidence={"legacy_category": "Valves"},
    )
    session.flush()
    assert assignment.status == "proposed"
    with pytest.raises(ValueError, match="propose it afresh"):
        transition_symbol_revision_classification(
            session,
            assignment.id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )


# --------------------------------------------------------------------------
# One verified primary per scheme
# --------------------------------------------------------------------------


def test_a_concept_may_hold_one_verified_primary_in_each_scheme(session, author_id):
    """A primary discipline and a primary category are both legitimate."""
    discipline = get_classification_scheme(session, "ENGINEERING-DISCIPLINE")
    category = get_classification_scheme(session, "SYMBOL-CATEGORY-FAMILY")
    concept_id = _concept(session, author_id, "Two primaries")
    for scheme, label in ((discipline, "Piping / P&ID"), (category, "Valves")):
        assignment = propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=_seeded_node(session, scheme.scheme_code, label),
            assignment_role="primary",
            method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        session.flush()
        transition_concept_classification(
            session,
            assignment.id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
        session.flush()

    assert verified_primary_concept_classification(session, concept_id, discipline.id) is not None
    assert verified_primary_concept_classification(session, concept_id, category.id) is not None


def test_verifying_a_second_primary_retires_the_first_in_the_same_scheme(session, author_id):
    """Supersession rather than refusal -- the shape SM-P0-02 and SM-P0-03
    already use."""
    category = get_classification_scheme(session, "SYMBOL-CATEGORY-FAMILY")
    concept_id = _concept(session, author_id, "Primary succession")
    assignments = []
    for label in ("Valves", "Actuators"):
        assignment = propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=_seeded_node(session, category.scheme_code, label),
            assignment_role="primary",
            method="manual",
            proposed_at=NOW,
            proposed_by_user_id=author_id,
        )
        assignments.append(assignment)
    session.flush()

    for assignment in assignments:
        transition_concept_classification(
            session,
            assignment.id,
            target_status="verified",
            occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )
        session.flush()

    assert assignments[0].status == "retired"
    assert assignments[1].status == "verified"
    assert verified_primary_concept_classification(session, concept_id, category.id).id == (
        assignments[1].id
    )


def test_database_refuses_two_verified_primaries_in_one_scheme(session, author_id):
    """The service supersedes; the partial unique index is what makes a
    direct write fail too."""
    category = get_classification_scheme(session, "SYMBOL-CATEGORY-FAMILY")
    revision_id = _symbol_revision(session, author_id)
    session.flush()
    insert = text(
        "INSERT INTO symbol_revision_classifications (id,symbol_revision_id,"
        "classification_node_id,classification_scheme_id,assignment_role,status,method,"
        "reviewed_at,created_at,updated_at) "
        "VALUES (:id,:revision,:node,:scheme,'primary','verified','manual',:now,:now,:now)"
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        for label in ("Valves", "Pumps"):
            session.execute(
                insert,
                {
                    "id": uuid.uuid4(),
                    "revision": revision_id,
                    "node": _seeded_node(session, category.scheme_code, label),
                    "scheme": category.id,
                    "now": NOW,
                },
            )
        session.flush()


def test_two_proposed_primaries_may_compete_for_review(session, author_id):
    """Proposals are deliberately unconstrained so competing candidates can sit
    side by side."""
    category = get_classification_scheme(session, "SYMBOL-CATEGORY-FAMILY")
    revision_id = _symbol_revision(session, author_id)
    for label in ("Valves", "Pumps"):
        propose_symbol_revision_classification(
            session,
            symbol_revision_id=revision_id,
            classification_node_id=_seeded_node(session, category.scheme_code, label),
            assignment_role="primary",
            method="ai_assisted",
            proposed_at=NOW,
            confidence=0.99,
        )
    session.flush()
    assert len(list_symbol_revision_classifications(session, revision_id)) == 2
    assert verified_primary_symbol_classification(session, revision_id, category.id) is None


def test_one_live_assignment_per_target_and_node(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Repeated node")
    concept_id = _concept(session, author_id, "Repeated assignment")
    for role in ("primary", "secondary"):
        propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=node.id,
            assignment_role=role,
            method="manual",
            proposed_at=NOW,
        )
    with pytest.raises((IntegrityError, DBAPIError)):
        session.flush()


def test_a_rejected_assignment_leaves_room_for_a_replacement(session, author_id):
    """Rejected and retired rows stay out of the live index, so an
    assignment's governance history survives its successor."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Second attempt")
    concept_id = _concept(session, author_id, "Rejected then replaced")
    first = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="secondary",
        method="ai_assisted",
        proposed_at=NOW,
    )
    session.flush()
    transition_concept_classification(
        session, first.id, target_status="rejected", occurred_at=NOW,
        reviewed_by_user_id=author_id,
    )
    session.flush()
    second = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="secondary",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    assert first.status == "rejected"
    assert first.reviewed_at == NOW
    assert second.status == "proposed"
    with pytest.raises(ValueError, match="cannot move from"):
        transition_concept_classification(
            session, first.id, target_status="verified", occurred_at=NOW,
            reviewed_by_user_id=author_id,
        )


# --------------------------------------------------------------------------
# Review policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["manual", "ai_assisted"])
def test_verifying_a_manual_or_ai_assisted_assignment_requires_a_reviewer(
    session, author_id, method
):
    """Specification section 8.4: confidence is not a governance state, and an
    unattributed verification of a machine proposal is exactly what principle
    P-07 rules out."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, f"Reviewer needed {method}")
    concept_id = _concept(session, author_id, f"Reviewer needed {method}")
    assignment = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="primary",
        method=method,
        proposed_at=NOW,
        confidence=0.99,
    )
    session.flush()
    with pytest.raises(ValueError, match="requires a reviewer"):
        transition_concept_classification(
            session, assignment.id, target_status="verified", occurred_at=NOW
        )


@pytest.mark.parametrize("method", ["source_mapping", "rule"])
def test_a_deterministic_assignment_may_be_verified_without_a_reviewer(
    session, author_id, method
):
    """Section 8.4's "deterministic authoritative-source verification"."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, f"Deterministic {method}")
    revision_id = _symbol_revision(session, author_id)
    assignment = propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision_id,
        classification_node_id=node.id,
        assignment_role="primary",
        method=method,
        proposed_at=NOW,
    )
    session.flush()
    transition_symbol_revision_classification(
        session, assignment.id, target_status="verified", occurred_at=NOW
    )
    session.flush()
    assert assignment.status == "verified"
    assert assignment.reviewed_at == NOW
    assert assignment.reviewed_by_user_id is None


def test_a_withdrawn_node_accepts_no_new_assignments(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Withdrawn node")
    concept_id = _concept(session, author_id, "Against a withdrawn node")
    set_classification_node_status(session, node.id, target_status="withdrawn", occurred_at=NOW)
    session.flush()
    with pytest.raises(ValueError, match="accepts no new assignments"):
        propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=node.id,
            assignment_role="primary",
            method="manual",
            proposed_at=NOW,
        )


def test_a_deprecated_node_keeps_its_existing_assignments(session, author_id):
    """A classification that was made stays made; retiring the node does not
    make it untrue."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Deprecated node")
    concept_id = _concept(session, author_id, "Deprecated node holder")
    assignment = propose_concept_classification(
        session,
        semantic_concept_id=concept_id,
        classification_node_id=node.id,
        assignment_role="secondary",
        method="manual",
        proposed_at=NOW,
        proposed_by_user_id=author_id,
    )
    session.flush()
    set_classification_node_status(session, node.id, target_status="deprecated", occurred_at=NOW)
    session.flush()
    assert len(list_concept_classifications(session, concept_id)) == 1
    transition_concept_classification(
        session, assignment.id, target_status="verified", occurred_at=NOW,
        reviewed_by_user_id=author_id,
    )
    session.flush()
    assert assignment.status == "verified"


def test_a_withdrawn_concept_accepts_no_new_classifications(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Withdrawn concept node")
    concept_id = _concept(session, author_id, "Concept to withdraw")
    session.execute(
        text("UPDATE semantic_concepts SET status='withdrawn' WHERE id=:id"), {"id": concept_id}
    )
    session.flush()
    session.expire_all()
    with pytest.raises(ValueError, match="accepts no new classifications"):
        propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=node.id,
            assignment_role="primary",
            method="manual",
            proposed_at=NOW,
        )


def test_proposing_against_a_missing_target_or_node_is_refused(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Real node")
    concept_id = _concept(session, author_id, "Real concept")
    session.flush()
    with pytest.raises(LookupError, match="semantic concept not found"):
        propose_concept_classification(
            session, semantic_concept_id=uuid.uuid4(), classification_node_id=node.id,
            assignment_role="primary", method="manual", proposed_at=NOW,
        )
    with pytest.raises(LookupError, match="classification node not found"):
        propose_concept_classification(
            session, semantic_concept_id=concept_id, classification_node_id=uuid.uuid4(),
            assignment_role="primary", method="manual", proposed_at=NOW,
        )
    with pytest.raises(LookupError, match="symbol revision not found"):
        propose_symbol_revision_classification(
            session, symbol_revision_id=uuid.uuid4(), classification_node_id=node.id,
            assignment_role="primary", method="manual", proposed_at=NOW,
        )


def test_invalid_roles_and_methods_are_refused_before_the_database(session, author_id):
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Service validation")
    concept_id = _concept(session, author_id, "Service validation")
    session.flush()
    with pytest.raises(ValueError, match="invalid classification assignment role"):
        propose_concept_classification(
            session, semantic_concept_id=concept_id, classification_node_id=node.id,
            assignment_role="proposed", method="manual", proposed_at=NOW,
        )
    with pytest.raises(ValueError, match="invalid classification assignment role"):
        propose_symbol_revision_classification(
            session, symbol_revision_id=_symbol_revision(session, author_id),
            classification_node_id=node.id, assignment_role="inherited", method="manual",
            proposed_at=NOW,
        )
    with pytest.raises(ValueError, match="invalid classification assignment method"):
        propose_concept_classification(
            session, semantic_concept_id=concept_id, classification_node_id=node.id,
            assignment_role="primary", method="imported", proposed_at=NOW,
        )


# --------------------------------------------------------------------------
# Read services
# --------------------------------------------------------------------------


def test_listing_puts_the_primary_first_and_filters(session, author_id):
    category = get_classification_scheme(session, "SYMBOL-CATEGORY-FAMILY")
    other = _scheme(session, author_id)
    other_node = _node(session, other.id, "Elsewhere")
    concept_id = _concept(session, author_id, "Listing order")
    for label, role in (("Valves", "secondary"), ("Pumps", "inherited"), ("Actuators", "primary")):
        propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=_seeded_node(session, category.scheme_code, label),
            assignment_role=role,
            method="manual",
            proposed_at=NOW,
        )
    propose_concept_classification(
        session, semantic_concept_id=concept_id, classification_node_id=other_node.id,
        assignment_role="secondary", method="manual", proposed_at=NOW,
    )
    session.flush()

    listed = list_concept_classifications(session, concept_id)
    assert len(listed) == 4
    assert listed[0].assignment_role == "primary"
    assert len(
        list_concept_classifications(session, concept_id, classification_scheme_id=category.id)
    ) == 3
    assert len(
        list_concept_classifications(session, concept_id, assignment_role="inherited")
    ) == 1
    assert len(list_concept_classifications(session, concept_id, status="verified")) == 0

    with pytest.raises(ValueError):
        list_concept_classifications(session, concept_id, status="not_a_status")
    with pytest.raises(ValueError):
        list_concept_classifications(session, concept_id, assignment_role="not_a_role")
    with pytest.raises(ValueError):
        list_symbol_revision_classifications(session, concept_id, assignment_role="inherited")


def test_the_reverse_lookup_finds_both_kinds_of_target(session, author_id):
    """Specification section 14.3 indexes `classification_node_id` for this."""
    scheme = _scheme(session, author_id)
    node = _node(session, scheme.id, "Shared node")
    concept_id = _concept(session, author_id, "Reverse lookup concept")
    revision_id = _symbol_revision(session, author_id)
    propose_concept_classification(
        session, semantic_concept_id=concept_id, classification_node_id=node.id,
        assignment_role="primary", method="manual", proposed_at=NOW,
    )
    propose_symbol_revision_classification(
        session, symbol_revision_id=revision_id, classification_node_id=node.id,
        assignment_role="primary", method="manual", proposed_at=NOW,
    )
    session.flush()

    concepts, revisions = find_classified_targets(session, node.id)
    assert concepts == [concept_id]
    assert revisions == [revision_id]
    assert find_classified_targets(session, node.id, status="verified") == ([], [])
    with pytest.raises(ValueError):
        find_classified_targets(session, node.id, status="not_a_status")


def test_scheme_lookup_is_case_insensitive_on_the_code(session):
    assert get_classification_scheme(session, "use-case") is not None
    assert get_classification_scheme(session, "NOT-A-SCHEME") is None
    assert len(list_classification_schemes(session, status="active")) >= 3
    assert len(list_classification_schemes(session, status="withdrawn")) == 0
    with pytest.raises(ValueError):
        list_classification_schemes(session, status="not_a_status")
    with pytest.raises(ValueError):
        list_classification_nodes(session, uuid.uuid4(), status="not_a_status")


# --------------------------------------------------------------------------
# Downgrade
# --------------------------------------------------------------------------


def test_downgrade_removes_all_four_tables_and_keeps_the_earlier_packages():
    with _database("symgov-classification-down") as (engine, url, _raw):
        _alembic(url, "upgrade", CLASSIFICATION_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name = ANY(:names)"
                ),
                {"names": list(CLASSIFICATION_TABLES)},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name = ANY(:names)"
                ),
                {"names": list(EARLIER_SEMANTIC_TABLES)},
            ).scalar_one() == len(EARLIER_SEMANTIC_TABLES)


def test_the_seed_survives_a_downgrade_and_re_upgrade():
    """`ON CONFLICT DO NOTHING` plus deterministic uuid5 identifiers means a
    re-upgrade re-seeds the same rows rather than a duplicate set."""
    with _database("symgov-classification-cycle") as (engine, url, _raw):
        _alembic(url, "upgrade", CLASSIFICATION_REVISION)
        _alembic(url, "downgrade", PREVIOUS_REVISION)
        _alembic(url, "upgrade", CLASSIFICATION_REVISION)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM classification_schemes")
            ).scalar_one() == 3
            assert connection.execute(
                text("SELECT count(*) FROM classification_nodes")
            ).scalar_one() == 37
            labels = connection.execute(
                text(
                    "SELECT n.preferred_label FROM classification_nodes n "
                    "JOIN classification_schemes s ON s.id = n.scheme_id "
                    "WHERE s.scheme_code = 'ENGINEERING-DISCIPLINE' ORDER BY n.sort_order"
                )
            ).scalars().all()
        assert labels == CATALOG_DISCIPLINE_ORDER
