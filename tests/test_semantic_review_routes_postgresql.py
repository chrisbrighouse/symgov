"""PostgreSQL cover for SM-P1-01 WP1.2: the semantic review API end to end.

Real Postgres, not the portable route fixture. Three kinds of fact need a
migrated server and cannot be established in `test_semantic_review_routes.py`:

* **The queue queries execute at all.** WP1.1's concept display name is a
  correlated subquery whose `ORDER BY` references the outer
  `semantic_concepts` row; SQLite will not resolve that outer reference. The
  portable file stubs the five queue reads for exactly this reason and says
  so.
* **Section 14.2's boundary on real joined rows.** `publication_gate` records
  that the private/public boundary was untouched there because it exposed no
  read route. WP1.2 is the first read route over these tables, so this file
  is where the boundary is actually proved: an organisation-private symbol
  must be invisible to a platform-scoped queue, and a decision route on
  another organisation's row must answer `404` rather than `403`, because a
  caller can tell those two apart.
* **The database's own refusals.** `ck_symbol_revision_classifications_
  backfill_not_verified` (section 12.3) is a CHECK constraint. The portable
  fixture drops PostgreSQL-dialect CHECKs to create its tables at all, so a
  test that a backfilled row cannot be verified is only meaningful here.

The seeded schemes and nodes these tests look up are rows migration
`20260909_0051` inserts, so they exist only in a migrated database.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.catalog_symbol_ids import ensure_catalog_symbol_id  # noqa: E402
from symgov_backend.classification_assignments import (  # noqa: E402
    propose_concept_classification,
    propose_symbol_revision_classification,
)
from symgov_backend.classification_schemes import (  # noqa: E402
    add_classification_node,
    register_classification_scheme,
)
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    ClassificationNode,
    ClassificationScheme,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
    GovernedSymbol,
    Organization,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
    User,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

# Must track head: this API reads tables SM-P0-04 added and the ORM is one
# global object that always reflects head.
# Moved to head for SM-P1-02 WP2.1. `20260915_0058` is what admits ICS's
# dotted node codes -- below it the grammar check refuses `13.220` outright --
# so a fixture pinned under that revision cannot hold the vocabulary this
# module now has to exercise. Same move nine fixtures made in SM-P0-08.
MIGRATION_HEAD = "20260917_0060"

V1 = "/api/v1"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def review_api_database():
    with _database("symgov-semantic-review-api") as (engine, url, raw_url):
        _alembic(url, "upgrade", MIGRATION_HEAD)
        yield engine


def _client(engine, *, semantic_review_enabled=True):
    app = create_app()
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        organization_symbols_enabled=True,
        platform_admin_enabled=True,
        semantic_review_enabled=semantic_review_enabled,
    )

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), Session


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _user(Session, *, email, roles=()):
    """Create a user, granting global roles the way the product does.

    A role assignment needs an active `plus` subscription, so a single
    `upsert_user(roles=[...])` on a fresh user silently stores none -- the
    two-step shape `test_wp74_symbol_demotion_postgresql` uses for the same
    reason.
    """
    with Session() as session:
        user = upsert_user(
            session, email=email, display_name=email, roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
        user_id = user.id
    if roles:
        with Session() as session:
            subscription = session.get(UserSubscription, user_id)
            subscription.tier = "plus"
            subscription.expires_on = _now().date().replace(year=_now().year + 1)
            session.commit()
        with Session() as session:
            upsert_user(
                session, email=email, display_name=email, roles=list(roles),
                pin="1234", must_change_pin=False,
            )
            session.commit()
    return user_id


def _organization(session, *, code, now, active=False):
    """Active only where the caller needs a session bound to it.

    `enforce_active_organization_admin_minimum` makes an *active*
    organization require an active Organization Administrator, so an
    organization that merely owns a symbol is built suspended -- and an
    organisation that has lost its admin must not start leaking its private
    symbols either.
    """
    organization = Organization(
        id=uuid.uuid4(),
        code=code.upper(),
        normalized_code=code,
        display_name=f"{code.upper()} Organization",
        name_key=f"{code}-organization",
        entitlement_status="active" if active else "suspended",
        is_active=active,
        is_protected=False,
        fallback_icon_svg="<svg/>",
        created_at=now,
        updated_at=now,
    )
    session.add(organization)
    session.flush()
    return organization


def _bind_member(session, *, organization, user_id, now, base_role="admin"):
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=organization.id,
        user_id=user_id,
        status="active",
        activated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(membership)
    session.flush()
    session.add(
        OrganizationRoleAssignment(
            id=uuid.uuid4(), membership_id=membership.id, base_role=base_role,
            is_active=True, assigned_at=now,
        )
    )
    session.flush()
    return membership


def _platform_admin(Session, *, email):
    """Seed a real Platform Administrator.

    Platform Admin is not a bare role assignment: it is an active
    `platform_admin` assignment *plus* an `admin` base role in the `symgov`
    organization itself (`organization_authorization`). The organization
    carries `ck_organizations_reserved_identity`, so its lowercase code and
    protected flag must be right at INSERT, and
    `enforce_platform_admin_eligibility` requires the assignment and the
    membership to land in the same transaction.
    """
    user_id = _user(Session, email=email)
    now = _now()
    with Session() as session:
        organization = session.execute(
            select(Organization).where(Organization.normalized_code == "symgov")
        ).scalar_one_or_none()
        if organization is None:
            organization = Organization(
                id=uuid.uuid4(),
                code="symgov",
                normalized_code="symgov",
                display_name="Symgov",
                name_key="symgov-organization",
                entitlement_status="active",
                is_active=True,
                is_protected=True,
                fallback_icon_svg="<svg/>",
                created_at=now,
                updated_at=now,
            )
            session.add(organization)
            session.flush()
        _bind_member(session, organization=organization, user_id=user_id, now=now, base_role="admin")
        session.add(
            PlatformRoleAssignment(
                id=uuid.uuid4(), user_id=user_id, role="platform_admin",
                is_active=True, assigned_at=now,
            )
        )
        session.commit()
    return user_id


def _symbol(session, *, owner_id, now, canonical_name, allocate_catalog_id=False,
            visibility="public", owner_organization_id=None, lifecycle_state="published"):
    """One governed symbol and its current revision.

    Order matters: `validate_catalog_symbol_publication_invariant` refuses a
    `published` revision whose symbol has no canonical catalog identifier, so
    the identifier is allocated before the revision exists.
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


def _node(session, *, scheme_code, node_code):
    return session.execute(
        select(ClassificationNode)
        .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
        .where(ClassificationScheme.scheme_code == scheme_code, ClassificationNode.node_code == node_code)
    ).scalar_one()


def _login(client, email):
    response = client.post(f"{V1}/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response


@pytest.fixture(scope="module")
def seeded(review_api_database):
    """A public symbol carrying a backfilled classification, an ACME-private
    symbol carrying one, and an OTHER-private symbol carrying one.

    The three together are what section 14.2 is about: an ACME reviewer must
    see the first two and never learn that the third exists.
    """
    engine = review_api_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = _now()

    reviewer_id = _user(Session, email="reviewer@example.test", roles=("reviewer",))

    with Session() as session:
        owner = upsert_user(
            session, email="owner@example.test", display_name="Owner", roles=[], pin="1234", must_change_pin=False
        )
        session.flush()
        owner_id = uuid.UUID(owner.id) if isinstance(owner.id, str) else owner.id

        acme = _organization(session, code="acme", now=now, active=True)
        other = _organization(session, code="other", now=now, active=False)

        # ACME is active, so it must retain an active Organization
        # Administrator at every commit; the reviewer is it.
        _bind_member(session, organization=acme, user_id=reviewer_id, now=now, base_role="admin")

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

        discipline = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code="MECHANICAL")

        public_symbol, public_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Public Ball Valve",
            allocate_catalog_id=True,
        )
        acme_symbol, acme_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="ACME Private Valve",
            visibility="organization_private", owner_organization_id=acme.id, lifecycle_state="draft",
        )
        other_symbol, other_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="OTHER Private Valve",
            visibility="organization_private", owner_organization_id=other.id, lifecycle_state="draft",
        )

        backfilled = propose_symbol_revision_classification(
            session,
            symbol_revision_id=public_revision.id,
            classification_node_id=discipline.id,
            assignment_role="primary",
            method="legacy_backfill",
            proposed_at=now,
        )
        acme_assignment = propose_symbol_revision_classification(
            session,
            symbol_revision_id=acme_revision.id,
            classification_node_id=discipline.id,
            assignment_role="primary",
            method="legacy_backfill",
            proposed_at=now,
        )
        other_assignment = propose_symbol_revision_classification(
            session,
            symbol_revision_id=other_revision.id,
            classification_node_id=discipline.id,
            assignment_role="primary",
            method="legacy_backfill",
            proposed_at=now,
        )
        # SM-P1-02 WP2.1. A miniature ICS, under the real scheme code, with
        # real ICS codes at all three of its real levels. The production
        # vocabulary is 1381 nodes imported by `ics_taxonomy`; importing it
        # here would cost minutes to prove a rule that four nodes prove
        # exactly as well, and the rule under test is depth, not breadth.
        #
        # Seeded `active` deliberately: the shipped import creates every node
        # `draft`, and this fixture stands for the state *after* the
        # governance activation decision D3 calls for. `13.220.20` is the node
        # decision D4 says no reviewer may assign, and `13.240` is the one the
        # new service guard must refuse whatever its depth -- it sits at group
        # level, which D4 allows.
        ics = register_classification_scheme(
            session,
            scheme_code="ISO-ICS-7",
            name="International Classification for Standards (ICS)",
            registered_at=now,
            status="active",
        )
        session.flush()
        ics_field = add_classification_node(
            session, scheme_id=ics.id, node_code="13", status="active", added_at=now,
            preferred_label="Environment. Health protection. Safety",
        )
        session.flush()
        ics_group = add_classification_node(
            session, scheme_id=ics.id, node_code="13.220", status="active", added_at=now,
            preferred_label="Protection against fire", parent_node_id=ics_field.id,
        )
        session.flush()
        ics_subgroup = add_classification_node(
            session, scheme_id=ics.id, node_code="13.220.20", status="active", added_at=now,
            preferred_label="Fire protection", parent_node_id=ics_group.id,
        )
        ics_draft_group = add_classification_node(
            session, scheme_id=ics.id, node_code="13.240", status="draft", added_at=now,
            preferred_label="Protection against excessive pressure", parent_node_id=ics_field.id,
        )
        session.flush()

        session.commit()

        return {
            "ics_field_node_id": ics_field.id,
            "ics_group_node_id": ics_group.id,
            "ics_subgroup_node_id": ics_subgroup.id,
            "ics_draft_group_node_id": ics_draft_group.id,
            "acme_organization_id": acme.id,
            "public_revision_id": public_revision.id,
            "public_symbol_id": public_symbol.id,
            "acme_revision_id": acme_revision.id,
            "other_revision_id": other_revision.id,
            "backfilled_assignment_id": backfilled.id,
            "acme_assignment_id": acme_assignment.id,
            "other_assignment_id": other_assignment.id,
            "discipline_node_id": discipline.id,
            "scheme_version_id": scheme_version.id,
            "owner_id": owner_id,
        }


@pytest.fixture
def reviewer_client(review_api_database, seeded):
    client, Session = _client(review_api_database)
    _login(client, "reviewer@example.test")
    return client, Session


def test_the_queue_renders_the_human_readable_catalog_identifier(reviewer_client, seeded):
    """`CLAUDE.md`: human-readable symbol IDs stay prominent in compact UI.

    `S-1`, allocated by the real allocator, not the governed symbol's
    UUID -- which remains available as a transport key and nothing more.
    """
    client, _Session = reviewer_client

    response = client.get(f"{V1}/semantic-review/queues/symbol-classifications")

    assert response.status_code == 200, response.text
    rows = {row["assignmentId"]: row for row in response.json()["items"]}
    public_row = rows[str(seeded["backfilled_assignment_id"])]

    assert public_row["symbol"]["catalogSymbolId"] == "S-1"
    assert public_row["symbol"]["canonicalName"] == "Public Ball Valve"
    assert public_row["schemeCode"] == "ENGINEERING-DISCIPLINE"
    assert public_row["nodeCode"] == "MECHANICAL"


def test_the_queue_warns_that_a_backfilled_row_can_never_be_verified(reviewer_client, seeded):
    """Section 12.3, and the whole production review population.

    All 132 rows SM-P0-10 backfilled are `proposed`/`legacy_backfill`, so a
    queue that offered a generic approve control would offer one the database
    refuses on every row it was built for.
    """
    client, _Session = reviewer_client

    rows = {
        row["assignmentId"]: row
        for row in client.get(f"{V1}/semantic-review/queues/symbol-classifications").json()["items"]
    }
    capabilities = rows[str(seeded["backfilled_assignment_id"])]["capabilities"]

    assert capabilities["canVerify"] is False
    assert capabilities["canReject"] is True
    assert capabilities["mustRepropose"] is True
    assert "legacy_backfill" in capabilities["blockedReason"]


def test_verifying_a_backfilled_row_is_refused_by_the_service(reviewer_client, seeded):
    client, Session = reviewer_client
    assignment_id = seeded["backfilled_assignment_id"]

    response = client.post(
        f"{V1}/semantic-review/symbol-classifications/{assignment_id}/decision",
        json={"targetStatus": "verified"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "validation_error"
    with Session() as session:
        assert session.get(SymbolRevisionClassificationAssignment, assignment_id).status == "proposed"


def test_an_organisation_private_symbol_is_invisible_to_a_platform_scoped_queue(review_api_database, seeded):
    """Section 16.1: no organisation-private symbol existence is revealed by
    public semantic endpoints.

    A personal-mode session scopes to `None`, which is public-only -- not
    "everything", and not "public plus whatever the caller happens to own".
    """
    client, Session = _client(review_api_database)
    _user(Session, email="personal@example.test", roles=("reviewer",))
    _login(client, "personal@example.test")

    body = client.get(f"{V1}/semantic-review/queues/symbol-classifications").json()
    seen = {row["assignmentId"] for row in body["items"]}

    assert str(seeded["backfilled_assignment_id"]) in seen
    assert str(seeded["acme_assignment_id"]) not in seen
    assert str(seeded["other_assignment_id"]) not in seen
    assert not any(row["symbol"]["visibility"] != "public" for row in body["items"])


def test_an_organisation_reviewer_sees_its_own_private_symbols_and_no_others(reviewer_client, seeded):
    client, _Session = reviewer_client

    seen = {
        row["assignmentId"]
        for row in client.get(f"{V1}/semantic-review/queues/symbol-classifications").json()["items"]
    }

    assert str(seeded["backfilled_assignment_id"]) in seen
    assert str(seeded["acme_assignment_id"]) in seen
    assert str(seeded["other_assignment_id"]) not in seen


def test_another_organisations_row_is_absent_rather_than_forbidden(reviewer_client, seeded):
    """404, not 403 -- the difference is the disclosure.

    A `403` would confirm that the row exists and belongs to someone else,
    which is precisely the private-symbol existence section 14.2 forbids
    revealing. The same substitution applies to the revision read.
    """
    client, _Session = reviewer_client

    decision = client.post(
        f"{V1}/semantic-review/symbol-classifications/{seeded['other_assignment_id']}/decision",
        json={"targetStatus": "rejected"},
    )
    detail = client.get(f"{V1}/semantic-review/symbol-revisions/{seeded['other_revision_id']}")

    assert decision.status_code == 404, decision.text
    assert decision.json()["error"] == "not_found"
    assert detail.status_code == 404, detail.text


def test_a_reviewer_rejects_a_backfilled_row_then_proposes_afresh(reviewer_client, seeded):
    """The one journey the whole production population actually needs.

    `mustRepropose` tells a reviewer to reject and propose again with a real
    method; this proves the API can carry that instruction out, and that the
    replacement is attributed to the reviewer rather than to a service user.
    """
    client, Session = reviewer_client
    revision_id = seeded["acme_revision_id"]
    assignment_id = seeded["acme_assignment_id"]

    rejected = client.post(
        f"{V1}/semantic-review/symbol-classifications/{assignment_id}/decision",
        json={"targetStatus": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text

    proposed = client.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
        json={
            "classificationNodeId": str(seeded["discipline_node_id"]),
            "assignmentRole": "primary",
            "method": "manual",
            "evidence": {"reviewedBecause": "legacy backfill rejected"},
        },
    )
    assert proposed.status_code == 201, proposed.text

    replacement = [
        row for row in proposed.json()["classificationAssignments"] if row["method"] == "manual"
    ]
    assert len(replacement) == 1
    assert replacement[0]["status"] == "proposed"
    assert replacement[0]["capabilities"]["canVerify"] is True
    assert replacement[0]["capabilities"]["mustRepropose"] is False

    with Session() as session:
        stored = session.get(SymbolRevisionClassificationAssignment, uuid.UUID(replacement[0]["assignmentId"]))
        reviewer_id = session.execute(
            select(SymbolRevisionClassificationAssignment.proposed_by_user_id).where(
                SymbolRevisionClassificationAssignment.id == stored.id
            )
        ).scalar_one()
        assert reviewer_id is not None, "a reviewer's proposal must name the reviewer"
        assert session.get(SymbolRevisionClassificationAssignment, assignment_id).status == "rejected"


def test_a_read_only_scheme_refuses_a_reviewers_proposal(reviewer_client, seeded):
    """Decision Q6: `REPRESENTATION-TYPE` is read-only in v1.

    The decision is written about the UI, but a control the API still honours
    is not read-only. Existing assignments stay visible; nothing creates one.
    """
    client, Session = reviewer_client
    with Session() as session:
        node = _node(session, scheme_code="REPRESENTATION-TYPE", node_code=session.execute(
            select(ClassificationNode.node_code)
            .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
            .where(ClassificationScheme.scheme_code == "REPRESENTATION-TYPE")
            .limit(1)
        ).scalar_one())
        node_id = node.id

    response = client.post(
        f"{V1}/semantic-review/symbol-revisions/{seeded['acme_revision_id']}/classifications",
        json={"classificationNodeId": str(node_id), "assignmentRole": "primary", "method": "manual"},
    )

    assert response.status_code == 422, response.text
    assert "read-only" in response.text


def test_concept_lifecycle_is_platform_level_and_a_reviewer_cannot_enter_it(reviewer_client):
    client, _Session = reviewer_client

    response = client.post(
        f"{V1}/semantic-review/concepts",
        json={"conceptKind": "physical_equipment", "preferredName": "Ball Valve", "definition": "A valve."},
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Platform Admin privileges are required."


def test_a_platform_admin_creates_a_concept_and_publishes_its_revision(review_api_database, seeded):
    """The first writer `semantic_concepts` has ever had in this codebase.

    Plan section 1.2 measured zero production importers, so no concept row was
    ever created and every symbol-to-concept assignment was structurally
    unreachable. This is what makes the semantic identity dimension of the
    section 9.2 gate reachable at all.
    """
    client, Session = _client(review_api_database)
    _platform_admin(Session, email="platform@example.test")
    _login(client, "platform@example.test")

    created = client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Ball Valve",
            "definition": "A quarter-turn valve using a perforated ball.",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["conceptCode"].startswith("SGC-")
    assert body["lifecycleState"] == "draft"
    assert body["conceptStatus"] == "draft"

    for target in ("review", "approved", "published"):
        moved = client.post(
            f"{V1}/semantic-review/concept-revisions/{body['revisionId']}/transition",
            json={"targetState": target},
        )
        assert moved.status_code == 200, moved.text

    assert moved.json()["lifecycleState"] == "published"
    assert moved.json()["conceptStatus"] == "active"
    assert moved.json()["conceptCode"] == body["conceptCode"], "a rename never moves the code (section 16.2)"


def test_a_reviewer_assigns_a_concept_to_a_revision_and_verifies_it(review_api_database, seeded):
    """Section 16.1: a symbol revision can be assigned one verified primary
    SymGov concept.

    Both halves matter. The assignment did not exist because nothing could
    create a concept; the verification did not exist because
    `transition_symbol_semantic_assignment` had no production caller.
    """
    platform_client, Session = _client(review_api_database)
    _platform_admin(Session, email="platform2@example.test")
    _login(platform_client, "platform2@example.test")

    concept = platform_client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Gate Valve",
            "definition": "A valve opened by lifting a gate.",
        },
    ).json()

    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")
    revision_id = seeded["public_revision_id"]

    proposed = reviewer.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/semantic-assignments",
        json={
            "semanticConceptId": concept["semanticConceptId"],
            "assignmentRole": "primary",
            "method": "manual",
            "confidence": 0.9,
        },
    )
    assert proposed.status_code == 201, proposed.text
    row = [
        item
        for item in proposed.json()["semanticAssignments"]
        if item["concept"]["conceptCode"] == concept["conceptCode"]
    ]
    assert len(row) == 1
    assert row[0]["status"] == "proposed", "section 8.4: every assertion starts proposed"
    assignment_id = row[0]["assignmentId"]

    verified = reviewer.post(
        f"{V1}/semantic-review/semantic-assignments/{assignment_id}/decision",
        json={"targetStatus": "verified"},
    )
    assert verified.status_code == 200, verified.text

    with Session() as session:
        stored = session.get(SymbolSemanticAssignment, uuid.UUID(assignment_id))
        reviewer_user_id = session.execute(
            select(User.id).where(User.email == "reviewer@example.test")
        ).scalar_one()

        assert stored.status == "verified"
        # Attributed to the authenticated actor, never to a service user --
        # both halves of the decision, so the section 14.4 audit trail names
        # a person at each step.
        assert str(stored.proposed_by_user_id) == str(reviewer_user_id)
        assert str(stored.reviewed_by_user_id) == str(reviewer_user_id)


def test_the_revision_detail_returns_the_whole_governed_state(reviewer_client, seeded):
    client, _Session = reviewer_client

    response = client.get(f"{V1}/semantic-review/symbol-revisions/{seeded['public_revision_id']}")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["symbol"]["catalogSymbolId"] == "S-1"
    assert body["lifecycleState"] == "published"
    assert any(row["method"] == "legacy_backfill" for row in body["classificationAssignments"])
    assert isinstance(body["semanticAssignments"], list)
    assert isinstance(body["rightsRecords"], list)


def test_the_whole_surface_is_absent_when_the_flag_is_off(review_api_database, seeded):
    """The flag ships default-off and is never activated by this package.

    404 rather than 403, so a disabled feature does not advertise itself.
    """
    client, _Session = _client(review_api_database, semantic_review_enabled=False)
    _login(client, "reviewer@example.test")

    response = client.get(f"{V1}/semantic-review/queues/symbol-classifications")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


def test_an_over_large_page_is_refused_rather_than_silently_clamped(reviewer_client):
    """WP1.1's rule, carried to the contract boundary.

    A caller asking for 5000 rows has a defect or a misunderstanding, and
    silently returning 200 of them hides both.
    """
    client, _Session = reviewer_client

    response = client.get(f"{V1}/semantic-review/queues/symbol-classifications", params={"limit": 5000})

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "validation_error"


def test_an_external_mapping_is_proposed_then_rejected_and_stays_in_the_response(
    review_api_database, seeded
):
    """Section 16.2: no external mapping without a scheme version.

    The rejected row must come back in the response. Listing only the open
    queue would drop the row the caller had just decided, which is the one
    thing they need to see -- and section 14.4 keeps the decision history with
    the governed data rather than hiding it once it leaves `proposed`.
    """
    platform_client, Session = _client(review_api_database)
    _platform_admin(Session, email="platform3@example.test")
    _login(platform_client, "platform3@example.test")

    concept = platform_client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Check Valve",
            "definition": "A valve permitting flow in one direction.",
        },
    ).json()

    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")

    proposed = reviewer.post(
        f"{V1}/semantic-review/concepts/{concept['semanticConceptId']}/external-mappings",
        json={
            "schemeVersionId": str(seeded["scheme_version_id"]),
            "externalIdentifier": "CFIHOS-1234",
            "mappingType": "exact",
            "mappingMethod": "manual",
            "externalLabel": "Check valve",
        },
    )
    assert proposed.status_code == 201, proposed.text
    assert len(proposed.json()["items"]) == 1
    mapping = proposed.json()["items"][0]
    assert mapping["status"] == "proposed"
    assert mapping["schemeCode"] == "CFIHOS"
    assert mapping["schemeVersionLabel"] == "1.5"

    rejected = reviewer.post(
        f"{V1}/semantic-review/external-mappings/{mapping['referenceId']}/decision",
        json={"targetStatus": "rejected"},
    )

    assert rejected.status_code == 200, rejected.text
    returned = {row["referenceId"]: row for row in rejected.json()["items"]}
    assert mapping["referenceId"] in returned, "the row just decided must come back"
    assert returned[mapping["referenceId"]]["status"] == "rejected"


def test_proposing_a_live_mapping_again_is_refused_rather_than_faulting(
    review_api_database, seeded
):
    """The 2026-09-14 amendment's carried defect, closed 2026-09-17.

    `uq_concept_external_references_active_mapping` is unique on (concept,
    release, external identifier) while the status is `proposed` or
    `verified`. The route used to reach the database only at `session.commit()`
    -- outside every exception handler -- so this collision escaped as a 500.
    It is now the same refusal `propose_symbol_classification` gives for its
    own index, and the reviewer is told which field collided.

    The rejected row afterwards proves the fix did not become a blanket
    refusal: once the original leaves the index, the identifier is proposable
    again, which is the remedy the message points at.
    """
    platform_client, Session = _client(review_api_database)
    _platform_admin(Session, email="platform5@example.test")
    _login(platform_client, "platform5@example.test")
    concept = platform_client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Gate Valve",
            "definition": "A valve opening by lifting a gate.",
        },
    ).json()

    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")
    body = {
        "schemeVersionId": str(seeded["scheme_version_id"]),
        "externalIdentifier": "CFIHOS-4321",
        "mappingType": "exact",
        "mappingMethod": "manual",
    }
    first = reviewer.post(
        f"{V1}/semantic-review/concepts/{concept['semanticConceptId']}/external-mappings",
        json=body,
    )
    assert first.status_code == 201, first.text
    mapping = first.json()["items"][0]

    collision = reviewer.post(
        f"{V1}/semantic-review/concepts/{concept['semanticConceptId']}/external-mappings",
        json=body,
    )

    assert collision.status_code == 422, collision.text
    assert collision.json()["error"] == "validation_error"
    # The envelope's `detail` is a constant; the message lives in the issues.
    issues = collision.json()["issues"]
    assert any("already has a live mapping" in issue["msg"] for issue in issues), issues
    assert any(issue["loc"][-1] == "externalIdentifier" for issue in issues), issues

    # The refusal rolled back cleanly: exactly one row, still proposed.
    listed = reviewer.post(
        f"{V1}/semantic-review/external-mappings/{mapping['referenceId']}/decision",
        json={"targetStatus": "rejected"},
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()["items"]
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "rejected"

    # Out of the partial index, so the identifier is available again.
    afresh = reviewer.post(
        f"{V1}/semantic-review/concepts/{concept['semanticConceptId']}/external-mappings",
        json=body,
    )
    assert afresh.status_code == 201, afresh.text
    assert {row["status"] for row in afresh.json()["items"]} == {"rejected", "proposed"}


def _concept_with_classification(
    review_api_database, *, email, node_code="MECHANICAL", method="manual", role="primary"
):
    """A concept carrying one classification assignment, seeded by the service.

    There is deliberately no propose *route* for a concept classification:
    WP3.1 closes the decision half of section 4.3 item 9 only. The writer is
    the industry-axis plan's WP2.2, whose shape depends on decision D1, so
    seeding through `propose_concept_classification` is what a caller of this
    route will actually be deciding on.
    """
    platform_client, Session = _client(review_api_database)
    _platform_admin(Session, email=email)
    _login(platform_client, email)
    concept = platform_client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Globe Valve",
            "definition": "A valve regulating flow with a movable plug.",
        },
    ).json()
    concept_id = uuid.UUID(concept["semanticConceptId"])

    with Session() as session:
        node = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code=node_code)
        assignment = propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=node.id,
            assignment_role=role,
            method=method,
            proposed_at=_now(),
        )
        session.commit()
        return concept_id, assignment.id, node.id


def test_a_concept_classification_can_be_verified_and_comes_back_decided(
    review_api_database, seeded
):
    """Section 4.3 item 9, closed 2026-09-17 (WP3.1).

    `transition_concept_classification` existed and was tested from the start;
    what was missing was any way to reach it, so the queue was read-only and
    a governance state the model defines was unreachable in production.
    """
    _concept_id, assignment_id, _node_id = _concept_with_classification(
        review_api_database, email="platform6@example.test"
    )
    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")

    response = reviewer.post(
        f"{V1}/semantic-review/concept-classifications/{assignment_id}/decision",
        json={"targetStatus": "verified"},
    )

    assert response.status_code == 200, response.text
    rows = {row["assignmentId"]: row for row in response.json()["items"]}
    assert rows[str(assignment_id)]["status"] == "verified"
    assert rows[str(assignment_id)]["schemeCode"] == "ENGINEERING-DISCIPLINE"
    assert rows[str(assignment_id)]["nodeCode"] == "MECHANICAL"
    # The queue lists only `proposed` rows, which is why the response is the
    # concept's whole classification state rather than a queue page.
    queued = reviewer.get(f"{V1}/semantic-review/queues/concept-classifications").json()
    assert str(assignment_id) not in {row["assignmentId"] for row in queued["items"]}


def test_verifying_a_primary_retires_the_primary_verified_before_it(
    review_api_database, seeded
):
    """Succession, not refusal -- the rule `transition_concept_classification`
    documents and SM-P0-02/-03 already use. The response has to carry both
    rows for a reviewer to see that the first one moved.
    """
    concept_id, first_id, _node_id = _concept_with_classification(
        review_api_database, email="platform7@example.test"
    )
    reviewer, Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")
    assert (
        reviewer.post(
            f"{V1}/semantic-review/concept-classifications/{first_id}/decision",
            json={"targetStatus": "verified"},
        ).status_code
        == 200
    )
    with Session() as session:
        node = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code="PROCESS")
        second = propose_concept_classification(
            session,
            semantic_concept_id=concept_id,
            classification_node_id=node.id,
            assignment_role="primary",
            method="manual",
            proposed_at=_now(),
        )
        session.commit()
        second_id = second.id

    response = reviewer.post(
        f"{V1}/semantic-review/concept-classifications/{second_id}/decision",
        json={"targetStatus": "verified"},
    )

    assert response.status_code == 200, response.text
    rows = {row["assignmentId"]: row["status"] for row in response.json()["items"]}
    assert rows[str(second_id)] == "verified"
    assert rows[str(first_id)] == "retired"


def test_a_backfilled_concept_classification_is_refused_as_the_queue_warned(
    review_api_database, seeded
):
    """Section 12.3, surfaced as this router's 422 rather than a fault.

    WP1.4's `mustRepropose` flag warns of exactly this before a reviewer
    tries; until WP3.1 there was no route for the warning to be about.
    """
    _concept_id, assignment_id, _node_id = _concept_with_classification(
        review_api_database, email="platform8@example.test", method="legacy_backfill"
    )
    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")

    response = reviewer.post(
        f"{V1}/semantic-review/concept-classifications/{assignment_id}/decision",
        json={"targetStatus": "verified"},
    )

    assert response.status_code == 422, response.text
    issues = response.json()["issues"]
    assert any("cannot be verified" in issue["msg"] for issue in issues), issues

    # Rejecting it is the remedy the message points at, and it is allowed.
    rejected = reviewer.post(
        f"{V1}/semantic-review/concept-classifications/{assignment_id}/decision",
        json={"targetStatus": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    rows = {row["assignmentId"]: row["status"] for row in rejected.json()["items"]}
    assert rows[str(assignment_id)] == "rejected"


def test_an_unknown_concept_classification_is_absent_not_forbidden(
    review_api_database, seeded
):
    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")

    response = reviewer.post(
        f"{V1}/semantic-review/concept-classifications/{uuid.uuid4()}/decision",
        json={"targetStatus": "verified"},
    )

    assert response.status_code == 404, response.text


def test_an_exact_mapping_cannot_be_verified_on_string_similarity(review_api_database, seeded):
    """Section 16.2, enforced by the service and surfaced as the 422 envelope.

    However high its confidence, an `exact` mapping asserted from string
    similarity is not evidence of sameness.
    """
    platform_client, Session = _client(review_api_database)
    _platform_admin(Session, email="platform4@example.test")
    _login(platform_client, "platform4@example.test")
    concept = platform_client.post(
        f"{V1}/semantic-review/concepts",
        json={
            "conceptKind": "physical_equipment",
            "preferredName": "Globe Valve",
            "definition": "A valve with a globular body.",
        },
    ).json()

    reviewer, _Session = _client(review_api_database)
    _login(reviewer, "reviewer@example.test")
    mapping = reviewer.post(
        f"{V1}/semantic-review/concepts/{concept['semanticConceptId']}/external-mappings",
        json={
            "schemeVersionId": str(seeded["scheme_version_id"]),
            "externalIdentifier": "CFIHOS-9999",
            "mappingType": "exact",
            "mappingMethod": "manual",
        },
    ).json()["items"][0]

    refused = reviewer.post(
        f"{V1}/semantic-review/external-mappings/{mapping['referenceId']}/decision",
        json={"targetStatus": "verified", "verificationBasis": "string_similarity"},
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"] == "validation_error"


# ---------------------------------------------------------------------------
# SM-P1-01 WP1.2 amendment (2026-09-14)
# ---------------------------------------------------------------------------


def test_the_queue_row_names_the_node_the_assignment_actually_holds(reviewer_client, seeded):
    client, _Session = reviewer_client

    response = client.get(f"{V1}/semantic-review/queues/symbol-classifications")
    assert response.status_code == 200, response.text

    rows = {row["assignmentId"]: row for row in response.json()["items"]}
    row = rows[str(seeded["backfilled_assignment_id"])]

    assert row["classificationNodeId"] == str(seeded["discipline_node_id"])
    assert row["nodeCode"] == "MECHANICAL"


def test_a_reviewer_discovers_the_assignable_nodes_over_http(reviewer_client):
    """The read that makes a re-proposal possible without inside knowledge."""
    client, _Session = reviewer_client

    response = client.get(f"{V1}/semantic-review/classification-schemes")
    assert response.status_code == 200, response.text

    schemes = {scheme["schemeCode"]: scheme for scheme in response.json()["items"]}

    assert set(schemes) == {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY", "ISO-ICS-7"}
    # Decision Q6: the three read-only schemes are not offered as choices.
    assert "USE-CASE" not in schemes
    assert "DOCUMENT-TYPE" not in schemes
    assert "REPRESENTATION-TYPE" not in schemes

    discipline_nodes = schemes["ENGINEERING-DISCIPLINE"]["nodes"]
    assert any(node["nodeCode"] == "MECHANICAL" for node in discipline_nodes)
    for node in discipline_nodes:
        assert node["nodeId"]
        assert node["nodeLabel"]


def test_the_repropose_journey_runs_on_nothing_but_what_the_api_returned(reviewer_client, seeded):
    """Section 12.3's remedy, carried out by a client with no fixture access.

    This is the amendment's whole point. The existing
    `..._rejects_a_backfilled_row_then_proposes_afresh` test takes the node id
    from the fixture; a real reviewer has only what the API hands them. Every
    identifier used below comes out of a response body.

    The order is forced by `uq_symbol_revision_classifications_active_node`,
    which is unique on (revision, node) while the status is `proposed` or
    `verified`: re-proposing the same node must follow the rejection, never
    precede it.
    """
    client, Session = reviewer_client
    # The public revision: visible at every scope, and its backfilled row
    # names MECHANICAL, so CIVIL_STRUCTURAL below is a genuinely different
    # node and the active-node index is not in play.
    revision_id = str(seeded["public_revision_id"])

    # Everything the reviewer knows comes from here.
    schemes = client.get(f"{V1}/semantic-review/classification-schemes").json()["items"]
    discipline = next(s for s in schemes if s["schemeCode"] == "ENGINEERING-DISCIPLINE")
    chosen_node = next(n for n in discipline["nodes"] if n["nodeCode"] == "CIVIL_STRUCTURAL")

    proposed = client.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
        json={
            "classificationNodeId": chosen_node["nodeId"],
            "assignmentRole": "primary",
            "method": "manual",
            "evidence": {"reviewedBecause": "the backfill mapped the wrong discipline"},
        },
    )
    assert proposed.status_code == 201, proposed.text

    replacement = [
        row for row in proposed.json()["classificationAssignments"]
        if row["classificationNodeId"] == chosen_node["nodeId"]
    ]
    assert len(replacement) == 1
    assert replacement[0]["status"] == "proposed"
    assert replacement[0]["method"] == "manual"
    assert replacement[0]["capabilities"]["canVerify"] is True


def test_reproposing_the_same_node_before_rejecting_is_refused_by_the_index(reviewer_client, seeded):
    """Why the UI does reject-then-propose and never the reverse.

    `uq_symbol_revision_classifications_active_node` holds while the original
    is still live, so the same node cannot be proposed twice. The reviewer is
    told, rather than silently given a duplicate.
    """
    client, _Session = reviewer_client
    revision_id = str(seeded["public_revision_id"])

    queue = client.get(f"{V1}/semantic-review/queues/symbol-classifications").json()["items"]
    row = next(r for r in queue if r["assignmentId"] == str(seeded["backfilled_assignment_id"]))
    assert row["status"] == "proposed"

    collision = client.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
        json={
            "classificationNodeId": row["classificationNodeId"],
            "assignmentRole": "primary",
            "method": "manual",
        },
    )
    assert collision.status_code == 422, collision.text


# --- WP1.5: the approval forecast, end to end ----------------------------
#
# Decision Q9 (2026-09-14). `test_classification_mapping_postgresql.py` proves
# that the forecast equals what the approval writes; what needs proving here
# is the *route* -- that it composes the writer's path over real seeded
# schemes, that a split child is forecast from its own classification record
# rather than the sheet's, and that reading it writes nothing.


def _seed_review_case(
    session,
    *,
    label,
    classification=None,
    reviewed_properties=None,
    split_children=(),
    with_package=True,
):
    """The smallest review case the forecast route will resolve.

    Deliberately not `execute_publication_handoff`'s full fixture: the route
    runs *before* any decision, so a decision, an approval target and a
    governed symbol are all absent by construction.
    """
    from symgov_backend.models import (
        AgentDefinition,
        AgentQueueItem,
        ClassificationRecord,
        IntakeRecord,
        ReviewCase,
        ReviewSplitItem,
        ReviewSymbolProperty,
        SourcePackage,
        ValidationReport,
    )

    # `category`, `discipline` and `confidence` are NOT NULL on
    # `classification_records`, so a fixture that omits them fails at INSERT
    # rather than exercising the route. The two text defaults are
    # `publication_handoff`'s own fallbacks.
    def _record(values):
        return {
            "category": "symbol",
            "discipline": "general",
            "confidence": Decimal("0.84"),
            **values,
        }

    now = _now()
    agent = AgentDefinition(
        id=uuid.uuid4(), slug=f"libby-{label}", display_name="Libby", role="classification",
        model="test", status="active", queue_family="classification",
        created_at=now, updated_at=now,
    )
    session.add(agent)
    session.flush()

    queue_item = AgentQueueItem(
        id=uuid.uuid4(), agent_id=agent.id, source_type="intake_record", source_id=uuid.uuid4(),
        status="completed", priority="normal", payload_json={}, created_at=now,
    )
    session.add(queue_item)
    session.flush()

    package = None
    if with_package:
        package = SourcePackage(
            id=uuid.uuid4(), package_code=f"{abs(hash(label)) % 0x10000:04X}",
            title=f"{label} submission", provider="contributor@example.test",
            package_type="submission_sheet", status="active", created_at=now, updated_at=now,
        )
        session.add(package)
        session.flush()

    intake = IntakeRecord(
        id=uuid.uuid4(), queue_item_id=queue_item.id, source_type="submission",
        source_ref=f"{label}-ref", submitter="contributor@example.test",
        submission_kind="single_symbol", intake_status="accepted", eligibility_status="eligible",
        source_package_id=package.id if package else None, raw_object_key=f"raw/{label}.svg",
        normalized_submission_json={"original_filename": f"{label}.svg", "candidate_title": label},
        routing_recommendation_json={}, report_json={}, created_at=now,
    )
    session.add(intake)
    session.flush()

    validation = ValidationReport(
        id=uuid.uuid4(), queue_item_id=queue_item.id, source_type="intake_record",
        source_id=intake.id, validation_status="pass", defect_count=0,
        normalized_payload_json={}, report_json={}, created_at=now,
    )
    session.add(validation)
    session.flush()

    review_case = ReviewCase(
        id=uuid.uuid4(), source_entity_type="validation_report", source_entity_id=validation.id,
        current_stage="classification_review", escalation_level="standard", opened_at=now,
    )
    session.add(review_case)
    session.flush()

    if classification is not None:
        session.add(
            ClassificationRecord(
                id=uuid.uuid4(), queue_item_id=queue_item.id, intake_record_id=intake.id,
                validation_report_id=validation.id, review_case_id=review_case.id,
                symbol_key=label, status="current", classification_status="provisional",
                source_id=intake.id, source_type="intake_record",
                libby_approved=False, created_at=now, updated_at=now, **_record(classification),
            )
        )

    if reviewed_properties is not None:
        session.add(
            ReviewSymbolProperty(
                id=uuid.uuid4(), review_case_id=review_case.id,
                symbol_record_key=str(review_case.id), name=label, description="",
                category=reviewed_properties.get("category"),
                discipline=reviewed_properties.get("discipline"),
                created_at=now, updated_at=now,
            )
        )

    split_items = []
    for index, child in enumerate(split_children):
        item = ReviewSplitItem(
            id=uuid.uuid4(), review_case_id=review_case.id, child_key=child["child_key"],
            proposed_symbol_id=child["child_key"].upper(), proposed_symbol_name=child["child_key"],
            file_name=f"{child['child_key']}.svg", parent_file_name=f"{label}.svg",
            attachment_object_key=f"raw/{child['child_key']}.svg", status="awaiting_decision",
            payload_json={"package_symbol_sequence": index + 1},
            created_at=now, updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(
            ClassificationRecord(
                id=uuid.uuid4(), queue_item_id=queue_item.id, review_case_id=None,
                parent_review_case_id=review_case.id, symbol_key=child["child_key"],
                symbol_region_index=index, status="current", classification_status="provisional",
                source_id=review_case.id, source_type="review_case",
                libby_approved=False, created_at=now, updated_at=now, **_record(child["classification"]),
            )
        )
        split_items.append(item)

    session.flush()
    return review_case, split_items


_SHEET_CLASSIFICATION = {
    "category": "symbol_sheet",
    "discipline": "Mechanical",
    "industry": "process_engineering",
    "symbol_family": "mixed_symbol_set",
    "standards_source": "https://example.test/isa-5-1",
    "library_provenance_class": "contributor_submission",
    "format": "svg",
}


def test_the_forecast_route_names_what_the_approval_will_propose(reviewer_client):
    client, Session = reviewer_client
    with Session() as session:
        review_case, _items = _seed_review_case(
            session,
            label="preview-basic",
            classification={
                "category": "Valves",
                "discipline": "Mechanical",
                "industry": "process_engineering",
                "symbol_family": "door",
                "library_provenance_class": "contributor_submission",
                "format": "svg",
            },
        )
        session.commit()
        review_case_id = review_case.id

    response = client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reviewCaseId"] == str(review_case_id)
    assert body["splitItemId"] is None
    assert body["method"] == "source_mapping"
    assert body["disciplineUsed"] == "Mechanical"

    proposed = {(item["schemeCode"], item["nodeCode"]) for item in body["willAssert"]}
    assert ("ENGINEERING-DISCIPLINE", "MECHANICAL") in proposed
    assert ("SYMBOL-CATEGORY-FAMILY", "VALVES") in proposed
    # Every entry names a real seeded node, alongside its readable label.
    for item in body["willAssert"]:
        assert item["nodeLabel"]
        assert uuid.UUID(item["classificationNodeId"])
        assert item["matchBasis"] in {"exact", "plural_variant", "legacy_taxonomy"}

    # Section 9.3's unseeded schemes are reported before the decision, which is
    # the half of decision Q9 an SME can still act on.
    gaps = {gap["field"]: gap for gap in body["willGap"]}
    assert gaps["industry"]["reason"] == "no_scheme"
    assert gaps["industry"]["rawValue"] == "process_engineering"
    assert gaps["format"]["reason"] == "carried_in_payload"


def test_the_forecast_route_follows_the_reviewed_property(reviewer_client):
    """A reviewer who corrects the discipline in place sees the correction
    forecast, not the record it overrode."""
    client, Session = reviewer_client
    with Session() as session:
        review_case, _items = _seed_review_case(
            session,
            label="preview-precedence",
            classification={"category": "Valves", "discipline": "Mechanical"},
            reviewed_properties={"category": "Pumps", "discipline": "Process"},
        )
        session.commit()
        review_case_id = review_case.id

    body = client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview"
    ).json()

    assert body["disciplineUsed"] == "Process"
    assert body["categoryUsed"] == "Pumps"
    primaries = {
        item["schemeCode"]: item["nodeLabel"]
        for item in body["willAssert"]
        if item["assignmentRole"] == "primary"
    }
    assert primaries["ENGINEERING-DISCIPLINE"] == "Process"
    assert primaries["SYMBOL-CATEGORY-FAMILY"] == "Pumps"


def test_a_split_child_is_forecast_from_its_own_record_not_the_sheets(reviewer_client):
    """The sheet's record holds `mixed_symbol_set`, which
    `load_child_classification_record` deliberately refuses to inherit. A
    forecast that used it would predict exactly what approval will not write.
    """
    client, Session = reviewer_client
    with Session() as session:
        review_case, items = _seed_review_case(
            session,
            label="preview-split",
            classification=_SHEET_CLASSIFICATION,
            split_children=[
                {"child_key": "child-1", "classification": {"category": "Pumps", "discipline": "Process", "symbol_family": "valve"}},
            ],
        )
        session.commit()
        review_case_id = review_case.id
        split_item_id = items[0].id

    sheet = client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview"
    ).json()
    child = client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview",
        params={"splitItemId": str(split_item_id)},
    ).json()

    assert child["splitItemId"] == str(split_item_id)
    assert child["disciplineUsed"] == "Process"
    assert sheet["disciplineUsed"] == "Mechanical"
    child_primaries = {
        item["schemeCode"]: item["nodeCode"]
        for item in child["willAssert"]
        if item["assignmentRole"] == "primary"
    }
    assert child_primaries["SYMBOL-CATEGORY-FAMILY"] == "PUMPS"
    # The sheet's own placeholder family is nowhere in the child's forecast.
    assert "mixed_symbol_set" not in json.dumps(child)


def test_a_split_item_of_another_case_is_reported_absent(reviewer_client):
    client, Session = reviewer_client
    with Session() as session:
        first, items = _seed_review_case(
            session,
            label="preview-split-a",
            classification={"discipline": "Mechanical"},
            split_children=[{"child_key": "child-a", "classification": {"discipline": "Process"}}],
        )
        second, _none = _seed_review_case(
            session, label="preview-split-b", classification={"discipline": "Mechanical"}
        )
        session.commit()
        foreign_split_id = items[0].id
        second_id = second.id

    response = client.get(
        f"{V1}/semantic-review/review-cases/{second_id}/classification-preview",
        params={"splitItemId": str(foreign_split_id)},
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Review split item was not found."


def test_reading_the_forecast_route_writes_nothing(reviewer_client):
    client, Session = reviewer_client
    with Session() as session:
        review_case, _items = _seed_review_case(
            session,
            label="preview-readonly",
            classification={"category": "Valves", "discipline": "Mechanical", "standards_source": "ISA-5.1"},
        )
        session.commit()
        review_case_id = review_case.id

    with Session() as session:
        before = (
            session.query(SymbolRevisionClassificationAssignment).count(),
            session.query(SymbolRevision).count(),
        )

    assert client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview"
    ).status_code == 200

    with Session() as session:
        assert (
            session.query(SymbolRevisionClassificationAssignment).count(),
            session.query(SymbolRevision).count(),
        ) == before


def test_a_personal_mode_session_may_read_the_forecast(review_api_database, seeded):
    """No tenant predicate, and the reason is measured rather than assumed.

    Neither `IntakeRecord` nor `ClassificationRecord` carries an organisation,
    and the intake lane feeds the public catalog. Section 14.2 is about
    organisation-private *symbol existence*; a review case naming no symbol
    and no organisation gives it nothing to protect -- the same reasoning that
    left `GET /semantic-review/classification-schemes` unscoped.
    """
    client, Session = _client(review_api_database)
    _user(Session, email="personal-forecast@example.test", roles=("reviewer",))
    _login(client, "personal-forecast@example.test")

    with Session() as session:
        review_case, _items = _seed_review_case(
            session, label="preview-personal", classification={"discipline": "Mechanical"}
        )
        session.commit()
        review_case_id = review_case.id

    response = client.get(
        f"{V1}/semantic-review/review-cases/{review_case_id}/classification-preview"
    )

    assert response.status_code == 200, response.text
    assert response.json()["disciplineUsed"] == "Mechanical"


# --- SM-P1-02 WP2.1: ICS becomes assignable, bounded by decision D4 -------
#
# The scheme is reviewer-populated only. WP2.2 decides whether anything
# proposes into it automatically, and that turns on decision D1; nothing here
# anticipates it. What these tests fix is the boundary: which ICS nodes a
# reviewer may be offered, which they may assign, and the fact that the two
# answers are the same one.


def test_the_ics_picker_stops_at_group_level(reviewer_client, seeded):
    """Decision D4, proved on the read.

    ICS is 40 fields, 401 groups and 940 subgroups. A reviewer assigns at
    field level and may descend to group level; subgroup is never offered.
    The plan's gate names this test specifically, because widening the scheme
    allowlist alone would have returned all three levels.
    """
    client, _Session = reviewer_client

    schemes = {s["schemeCode"]: s for s in client.get(
        f"{V1}/semantic-review/classification-schemes"
    ).json()["items"]}

    codes = {node["nodeCode"] for node in schemes["ISO-ICS-7"]["nodes"]}

    assert "13" in codes, "the field level is assignable"
    assert "13.220" in codes, "the group level is assignable"
    assert "13.220.20" not in codes, "the subgroup level is never offered"
    # And the draft node is absent for a different reason than depth: it sits
    # at group level, which D4 allows, but no unactivated node is a choice.
    assert "13.240" not in codes


def test_a_reviewer_assigns_an_ics_group_and_is_refused_its_subgroup(reviewer_client, seeded):
    """The same rule on the write, which is the one that is actually a control.

    A picker that omits a node is not a refusal: a caller naming the
    identifier directly would otherwise reach a subgroup the UI never showed.
    """
    client, _Session = reviewer_client
    revision_id = str(seeded["public_revision_id"])

    allowed = client.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
        json={
            "classificationNodeId": str(seeded["ics_group_node_id"]),
            "assignmentRole": "primary",
            "method": "manual",
            "evidence": {"reviewedBecause": "the source standard is a fire protection standard"},
        },
    )
    assert allowed.status_code == 201, allowed.text

    refused = client.post(
        f"{V1}/semantic-review/symbol-revisions/{revision_id}/classifications",
        json={
            "classificationNodeId": str(seeded["ics_subgroup_node_id"]),
            "assignmentRole": "primary",
            "method": "manual",
        },
    )
    assert refused.status_code == 422, refused.text
    assert "more specific than a reviewer may assign" in refused.text


def test_a_draft_node_is_refused_however_a_caller_reaches_it(reviewer_client, seeded):
    """The hole SM-P1-02 WP2.1 found and closed.

    `CLOSED_NODE_STATUSES` held only `withdrawn`, so `draft` was assignable
    and only the picker's `status="active"` filter stood between a reviewer
    and an unactivated vocabulary. That filter is a read, not a control. The
    refusal now lives in `classification_assignments`, so it holds for every
    caller and for concept classifications too -- which have no propose route
    at all, and so could never have been protected by one.

    The node below is at group level, which decision D4 allows: this test
    fails for status alone, never for depth.
    """
    client, _Session = reviewer_client

    response = client.post(
        f"{V1}/semantic-review/symbol-revisions/{seeded['public_revision_id']}/classifications",
        json={
            "classificationNodeId": str(seeded["ics_draft_group_node_id"]),
            "assignmentRole": "primary",
            "method": "manual",
        },
    )

    assert response.status_code == 422, response.text
    assert "draft" in response.text
    assert "accepts no new assignments" in response.text
