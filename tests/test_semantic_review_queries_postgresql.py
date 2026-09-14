"""PostgreSQL cover for SM-P1-01 WP1.1: the cross-target semantic review queue.

Real Postgres, not the SQLite service fixture: the queue joins
`symbol_revision_classifications` to `classification_nodes`,
`classification_schemes`, `symbol_revisions` and `governed_symbols`, reads
`evidence_json` as JSONB, and its tenant predicate is the thing section 14.2
turns on. The seeded schemes and nodes this file looks up are rows migration
`20260909_0051` inserts, so they exist only in a migrated database.

The decision-capability rules themselves are pure and are proved DB-free in
`test_semantic_review_queries.py`.
"""

from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.catalog_symbol_ids import ensure_catalog_symbol_id  # noqa: E402
from symgov_backend.classification_assignments import (  # noqa: E402
    propose_concept_classification,
    propose_symbol_revision_classification,
)
from symgov_backend.concept_external_references import (  # noqa: E402
    propose_concept_external_reference,
)
from symgov_backend.rights_provenance import propose_rights_record  # noqa: E402
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    propose_symbol_semantic_assignment,
)
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    ClassificationScheme,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
    GovernedSymbol,
    Organization,
    SemanticConcept,
    SymbolRevision,
    User,
)
from symgov_backend.semantic_review import (  # noqa: E402
    MAX_QUEUE_LIMIT,
    list_open_concept_classifications,
    list_open_concept_external_references,
    list_open_rights_records,
    list_open_symbol_revision_classifications,
    list_open_symbol_semantic_assignments,
)

# Must track head: the queue reads tables SM-P0-04 added and the ORM is one
# global object that always reflects head.
MIGRATION_HEAD = "20260911_0057"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def review_queue_database():
    with _database("symgov-semantic-review") as (engine, url, raw_url):
        _alembic(url, "upgrade", MIGRATION_HEAD)
        yield engine


def _node(session, *, scheme_code: str, node_code: str) -> ClassificationNode:
    return session.execute(
        select(ClassificationNode)
        .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
        .where(ClassificationScheme.scheme_code == scheme_code, ClassificationNode.node_code == node_code)
    ).scalar_one()


def _organization(session, *, code: str, now) -> Organization:
    # Deliberately inactive: `enforce_active_organization_admin_minimum` makes
    # an *active* organization require an active Organization Administrator,
    # and this file is not testing membership. The queue's tenant predicate
    # reads `governed_symbols.owner_organization_id` only, so the
    # organization's own lifecycle state is irrelevant to what it proves --
    # and an organisation that has lost its admin must not start leaking its
    # private symbols either.
    organization = Organization(
        id=uuid.uuid4(),
        code=code.upper(),
        normalized_code=code,
        display_name=f"{code.upper()} Organization",
        name_key=f"{code}-organization",
        entitlement_status="suspended",
        is_active=False,
        is_protected=False,
        fallback_icon_svg="<svg/>",
        created_at=now,
        updated_at=now,
    )
    session.add(organization)
    session.flush()
    return organization


def _symbol(session, *, owner_id, now, canonical_name, allocate_catalog_id=False,
            visibility="public", owner_organization_id=None,
            lifecycle_state="published") -> tuple[GovernedSymbol, SymbolRevision]:
    """Build one governed symbol and its current revision.

    Order matters: `validate_catalog_symbol_publication_invariant` refuses a
    `published` revision whose symbol has no canonical catalog identifier, so
    the identifier is allocated before the revision exists rather than after.
    """
    symbol = GovernedSymbol(
        id=uuid.uuid4(),
        slug=f"sym-{uuid.uuid4().hex[:12]}",
        canonical_name=canonical_name,
        category="valve",
        discipline="mechanical",
        owner_id=owner_id,
        owner_organization_id=owner_organization_id,
        visibility=visibility,
        organization_wide=False,
        current_revision_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(symbol)
    session.flush()

    if allocate_catalog_id:
        # The real allocator, not a hand-written row: `catalog_symbol_id` is a
        # FK into `catalog_symbol_identifiers`, which has required columns of
        # its own, and the queue must surface this identifier rather than a
        # UUID (`CLAUDE.md`).
        ensure_catalog_symbol_id(session, symbol.id, allocated_at=now)
        session.flush()

    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label="1",
        lifecycle_state=lifecycle_state,
        payload_json={},
        author_id=owner_id,
        created_at=now,
    )
    session.add(revision)
    session.flush()
    symbol.current_revision_id = revision.id
    session.flush()
    return symbol, revision


@pytest.fixture(scope="module")
def seeded(review_queue_database):
    """One public symbol with a backfilled proposal, one public symbol with an
    ordinary proposal, and one organisation-private symbol with a proposal
    that must never appear in a platform-public queue."""
    engine = review_queue_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with Session() as session:
        user = upsert_user(
            session, email="queue@example.test", display_name="Queue", roles=[], pin="1234", must_change_pin=False
        )
        session.flush()
        owner_id = uuid.UUID(user.id) if isinstance(user.id, str) else user.id

        organization = _organization(session, code="acme", now=now)

        discipline_node = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code="MECHANICAL")
        category_node = _node(session, scheme_code="SYMBOL-CATEGORY-FAMILY", node_code="VALVES")

        public_backfilled, backfilled_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Legacy Ball Valve",
            allocate_catalog_id=True,
        )
        public_ordinary, ordinary_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Mapped Gate Valve",
            allocate_catalog_id=True,
        )
        private_symbol, private_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Private Draft Valve",
            visibility="organization_private", owner_organization_id=organization.id,
            # An organisation-private symbol is not published, and the
            # catalog-publication invariant would refuse it if it were.
            lifecycle_state="draft",
        )

        # Oldest first here, so "newest first" ordering is falsifiable.
        propose_symbol_revision_classification(
            session,
            symbol_revision_id=backfilled_revision.id,
            classification_node_id=discipline_node.id,
            assignment_role="primary",
            method="legacy_backfill",
            proposed_at=now - timedelta(hours=2),
            proposed_by_user_id=owner_id,
            evidence={"match_basis": "legacy_taxonomy"},
        )
        propose_symbol_revision_classification(
            session,
            symbol_revision_id=ordinary_revision.id,
            classification_node_id=category_node.id,
            assignment_role="primary",
            method="source_mapping",
            proposed_at=now - timedelta(hours=1),
            proposed_by_user_id=owner_id,
            evidence={"match_basis": "exact"},
        )
        propose_symbol_revision_classification(
            session,
            symbol_revision_id=private_revision.id,
            classification_node_id=discipline_node.id,
            assignment_role="primary",
            method="manual",
            proposed_at=now,
            proposed_by_user_id=owner_id,
            evidence={},
        )
        session.commit()

        return {
            "Session": Session,
            "organization_id": organization.id,
            "public_backfilled_revision": backfilled_revision.id,
            "public_ordinary_revision": ordinary_revision.id,
            "private_revision": private_revision.id,
        }


def test_the_platform_queue_returns_open_proposals_newest_first(seeded):
    with seeded["Session"]() as session:
        rows = list_open_symbol_revision_classifications(session)

    assert [row.symbol.canonical_name for row in rows] == ["Mapped Gate Valve", "Legacy Ball Valve"]
    assert all(row.status == "proposed" for row in rows)


def test_the_platform_queue_never_reveals_an_organisation_private_symbol(seeded):
    """Specification section 14.2 and the section 16.1 criterion "no
    organisation-private symbol existence is revealed by public semantic
    endpoints". The private symbol's assignment is the newest of the three,
    so a missing tenant predicate fails this loudly."""
    with seeded["Session"]() as session:
        rows = list_open_symbol_revision_classifications(session)

    assert "Private Draft Valve" not in [row.symbol.canonical_name for row in rows]
    assert seeded["private_revision"] not in [row.symbol_revision_id for row in rows]


def test_an_organisation_scoped_queue_adds_only_its_own_private_symbols(seeded):
    with seeded["Session"]() as session:
        rows = list_open_symbol_revision_classifications(
            session, organization_id=seeded["organization_id"]
        )
        other_org_rows = list_open_symbol_revision_classifications(session, organization_id=uuid.uuid4())

    assert [row.symbol.canonical_name for row in rows] == [
        "Private Draft Valve",
        "Mapped Gate Valve",
        "Legacy Ball Valve",
    ]
    # A different organisation sees the public rows and nothing more.
    assert "Private Draft Valve" not in [row.symbol.canonical_name for row in other_org_rows]


def test_a_queue_row_carries_the_human_readable_symbol_identity(seeded):
    """`CLAUDE.md`: human-readable symbol IDs stay prominent and are never
    replaced by UUIDs in compact UI. The queue supplies them so the surface
    never has to resolve a UUID to show a row."""
    with seeded["Session"]() as session:
        rows = list_open_symbol_revision_classifications(session)

    ordinary = next(row for row in rows if row.symbol.canonical_name == "Mapped Gate Valve")
    # Allocated by the real sequence, so assert the canonical shape
    # (`catalog_symbol_ids.format_catalog_symbol_id`: "S-" + six digits)
    # rather than a value this fixture cannot pin.
    assert ordinary.symbol.catalog_symbol_id is not None
    assert re.fullmatch(r"S-\d{6}", ordinary.symbol.catalog_symbol_id)
    assert ordinary.scheme_code == "SYMBOL-CATEGORY-FAMILY"
    assert ordinary.node_code == "VALVES"
    assert ordinary.node_label == "Valves"
    assert ordinary.evidence == {"match_basis": "exact"}
    assert ordinary.proposed_at is not None


def test_a_backfilled_queue_row_offers_rejection_rather_than_approval(seeded):
    with seeded["Session"]() as session:
        rows = list_open_symbol_revision_classifications(session, method="legacy_backfill")

    assert len(rows) == 1
    row = rows[0]
    assert row.method == "legacy_backfill"
    assert row.capabilities.can_verify is False
    assert row.capabilities.can_reject is True
    assert row.capabilities.must_repropose is True


def test_the_queue_filters_by_method_status_and_scheme(seeded):
    with seeded["Session"]() as session:
        by_scheme = list_open_symbol_revision_classifications(
            session, classification_scheme_code="ENGINEERING-DISCIPLINE"
        )
        by_method = list_open_symbol_revision_classifications(session, method="source_mapping")
        verified = list_open_symbol_revision_classifications(session, status="verified")

    assert [row.symbol.canonical_name for row in by_scheme] == ["Legacy Ball Valve"]
    assert [row.symbol.canonical_name for row in by_method] == ["Mapped Gate Valve"]
    assert verified == []


def test_the_queue_rejects_a_filter_outside_the_frozen_vocabulary(seeded):
    with seeded["Session"]() as session:
        with pytest.raises(ValueError):
            list_open_symbol_revision_classifications(session, status="approved")
        with pytest.raises(ValueError):
            list_open_symbol_revision_classifications(session, method="imported")


def test_the_queue_pages_within_bounds(seeded):
    with seeded["Session"]() as session:
        first = list_open_symbol_revision_classifications(session, limit=1)
        second = list_open_symbol_revision_classifications(session, limit=1, offset=1)
        past_the_end = list_open_symbol_revision_classifications(session, limit=1, offset=99)

        with pytest.raises(ValueError):
            list_open_symbol_revision_classifications(session, limit=0)
        with pytest.raises(ValueError):
            list_open_symbol_revision_classifications(session, limit=MAX_QUEUE_LIMIT + 1)
        with pytest.raises(ValueError):
            list_open_symbol_revision_classifications(session, offset=-1)

    assert [row.symbol.canonical_name for row in first] == ["Mapped Gate Valve"]
    assert [row.symbol.canonical_name for row in second] == ["Legacy Ball Valve"]
    assert past_the_end == []


# --------------------------------------------------------------------------
# The other four queues
#
# Two are symbol-scoped and therefore carry section 14.2's tenant predicate;
# two are concept-scoped and deliberately do not. Section 17 made concept
# governance platform-level, and a concept->scheme or concept->external-scheme
# assertion names no symbol at all, so there is no private existence for it to
# leak. Section 14.2's sentence is specifically about "the assignment from a
# private symbol revision to a concept" -- which is the symbol queue below.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_concepts(seeded):
    """A concept with one open classification, one open external mapping, one
    open assignment from a public symbol and one from the private symbol."""
    Session = seeded["Session"]
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with Session() as session:
        user = session.execute(select(User).where(User.email == "queue@example.test")).scalar_one()
        owner_id = user.id if isinstance(user.id, uuid.UUID) else uuid.UUID(user.id)

        concept, _revision = create_semantic_concept(
            session,
            concept_kind="physical_equipment",
            preferred_name="Ball Valve",
            definition="A quarter-turn valve using a perforated ball.",
            created_by_user_id=owner_id,
            created_at=now,
        )
        session.flush()

        scheme = ExternalSemanticScheme(
            id=uuid.uuid4(), scheme_code="CFIHOS", title="CFIHOS", issuing_body="IOGP",
            base_uri="https://example.test/cfihos", status="active",
            created_by_user_id=owner_id, created_at=now, updated_at=now,
        )
        session.add(scheme)
        session.flush()
        scheme_version = ExternalSemanticSchemeVersion(
            id=uuid.uuid4(), scheme_id=scheme.id, version_label="1.5", status="active",
            created_by_user_id=owner_id, created_at=now, updated_at=now,
        )
        session.add(scheme_version)
        session.flush()

        discipline_node = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code="MECHANICAL")

        propose_concept_classification(
            session,
            semantic_concept_id=concept.id,
            classification_node_id=discipline_node.id,
            assignment_role="primary",
            method="manual",
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        propose_concept_external_reference(
            session,
            semantic_concept_id=concept.id,
            scheme_version_id=scheme_version.id,
            external_identifier="CFIHOS-10000123",
            external_label="Ball valve",
            mapping_type="exact",
            mapping_method="manual",
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        propose_symbol_semantic_assignment(
            session,
            symbol_revision_id=seeded["public_ordinary_revision"],
            semantic_concept_id=concept.id,
            assignment_role="primary",
            method="manual",
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        propose_symbol_semantic_assignment(
            session,
            symbol_revision_id=seeded["private_revision"],
            semantic_concept_id=concept.id,
            assignment_role="primary",
            method="manual",
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        # `ai_assisted` is what `propose_intake_rights_record` writes, and it
        # is unapprovable by section 8.4.
        propose_rights_record(
            session,
            disposition="display",
            determination_method="ai_assisted",
            rights_status="open",
            symbol_revision_id=seeded["public_ordinary_revision"],
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        propose_rights_record(
            session,
            disposition="display",
            determination_method="manual",
            rights_status="open",
            symbol_revision_id=seeded["private_revision"],
            proposed_at=now,
            proposed_by_user_id=owner_id,
        )
        session.commit()
        return {"concept_id": concept.id, "concept_code": concept.concept_code}


def test_the_symbol_semantic_assignment_queue_is_tenant_scoped(seeded, seeded_concepts):
    """Section 14.2 names this case exactly: "the assignment from a private
    symbol revision to a concept remains tenant-scoped information"."""
    with seeded["Session"]() as session:
        platform = list_open_symbol_semantic_assignments(session)
        scoped = list_open_symbol_semantic_assignments(
            session, organization_id=seeded["organization_id"]
        )

    assert [row.symbol.canonical_name for row in platform] == ["Mapped Gate Valve"]
    assert sorted(row.symbol.canonical_name for row in scoped) == [
        "Mapped Gate Valve",
        "Private Draft Valve",
    ]
    assert platform[0].concept.concept_code == seeded_concepts["concept_code"]
    assert platform[0].concept.preferred_name == "Ball Valve"
    assert platform[0].capabilities.can_verify is True


def test_the_concept_classification_queue_returns_open_proposals(seeded, seeded_concepts):
    with seeded["Session"]() as session:
        rows = list_open_concept_classifications(session)

    assert len(rows) == 1
    assert rows[0].concept.concept_code == seeded_concepts["concept_code"]
    assert rows[0].scheme_code == "ENGINEERING-DISCIPLINE"
    assert rows[0].node_code == "MECHANICAL"
    assert rows[0].capabilities.can_verify is True


def test_the_external_mapping_queue_carries_the_scheme_release(seeded, seeded_concepts):
    """A mapping is meaningless without the release it was made against
    (section 16.2: "no external mapping is stored without an external scheme
    version"), so the queue surfaces the human-readable scheme code and
    version label rather than the version UUID."""
    with seeded["Session"]() as session:
        rows = list_open_concept_external_references(session)

    assert len(rows) == 1
    row = rows[0]
    assert row.concept.concept_code == seeded_concepts["concept_code"]
    assert row.scheme_code == "CFIHOS"
    assert row.scheme_version_label == "1.5"
    assert row.external_identifier == "CFIHOS-10000123"
    assert row.mapping_type == "exact"
    assert row.capabilities.can_verify is True


def test_the_rights_queue_is_tenant_scoped_and_flags_the_unapprovable(seeded, seeded_concepts):
    with seeded["Session"]() as session:
        platform = list_open_rights_records(session)
        scoped = list_open_rights_records(session, organization_id=seeded["organization_id"])

    assert [row.symbol.canonical_name for row in platform] == ["Mapped Gate Valve"]
    assert sorted(row.symbol.canonical_name for row in scoped) == [
        "Mapped Gate Valve",
        "Private Draft Valve",
    ]

    ai_assisted = platform[0]
    assert ai_assisted.determination_method == "ai_assisted"
    assert ai_assisted.capabilities.can_verify is False
    assert ai_assisted.capabilities.must_repropose is True


def test_every_queue_rejects_a_filter_outside_its_own_vocabulary(seeded, seeded_concepts):
    with seeded["Session"]() as session:
        with pytest.raises(ValueError):
            list_open_symbol_semantic_assignments(session, method="legacy_backfill")
        with pytest.raises(ValueError):
            list_open_concept_external_references(session, method="source_mapping")
        with pytest.raises(ValueError):
            list_open_rights_records(session, determination_method="rule")
        with pytest.raises(ValueError):
            list_open_concept_classifications(session, limit=0)


def test_a_draft_concept_still_shows_its_name_in_the_queue(seeded, seeded_concepts):
    """`current_revision_id` is set only when a revision is *published*
    (`transition_semantic_concept_revision`), and manual concept creation --
    which lands in this package -- starts every concept as `draft`. A queue
    keyed on the current revision would render a nameless row for exactly the
    concepts a reviewer was queued to act on, so the name falls back to the
    newest revision. Pinned because a later refactor to a plain join would
    reintroduce the blank silently.
    """
    with seeded["Session"]() as session:
        concept = session.get(SemanticConcept, seeded_concepts["concept_id"])
        assert concept.status == "draft"
        assert concept.current_revision_id is None, "fixture must exercise the fallback"

        rows = list_open_concept_classifications(session)

    assert rows[0].concept.preferred_name == "Ball Valve"
    assert rows[0].concept.concept_code == seeded_concepts["concept_code"]
