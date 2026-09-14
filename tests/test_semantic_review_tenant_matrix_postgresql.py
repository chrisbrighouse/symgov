"""The section 14.2 tenant-isolation sweep (SM-P1-01 WP1.6).

**A sweep, not a sample.** Three existing PostgreSQL files prove the
private/public boundary on a handful of routes -- the platform-scoped queue
never reveals a private symbol, an organisation reviewer sees its own and no
others, another organisation's row is absent rather than forbidden. Each of
them names the routes it probes, so none can fail when a *new* symbol-scoped
route arrives without the predicate. This file probes every symbol-scoped
route on the router with a foreign row, and
`test_the_sweep_probes_every_tenant_scoped_route` pins that "every" against
the router's own decorators by way of `test_semantic_review_tenant_matrix`'s
`TENANT_SCOPED` table. Adding an eleventh symbol-scoped route fails this file
until it is probed.

**Three principals, because the interesting one is the privileged one.**
The sweep runs as an ACME reviewer, as a personal-mode session, and as a
**Platform Administrator** -- whose `active_organization_id` is the `symgov`
organisation, not ACME and not OTHER. `_scope` reads that field and nothing
else, so the most privileged principal in the product is scoped exactly like
any other. "Platform Admin cannot see another organisation's private symbol"
is the assertion section 14.2 actually needs, and it is the one a reader is
most likely to assume is false.

**Why here and not in the portable partition.** The portable route fixture
creates its tables by dropping the PostgreSQL-dialect CHECK constraints and
stubs the five queue reads, because WP1.1's concept display name is a
correlated subquery SQLite will not resolve. A tenant assertion written there
would pass without ever exercising the predicate. The seam is only real
against a migrated server with genuine joined rows.

**404 and never 403, and never content.** The two statuses are
distinguishable, and the difference is exactly the private-symbol existence
section 14.2 forbids revealing. Every probe asserts the status, the error
envelope, and that the foreign symbol's canonical name and slug appear
nowhere in the response body.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_semantic_review_routes_postgresql import (  # noqa: E402
    MIGRATION_HEAD,
    V1,
    _bind_member,
    _client,
    _login,
    _node,
    _organization,
    _platform_admin,
    _symbol,
    _user,
)
from test_semantic_review_tenant_matrix import TENANT_SCOPED  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.classification_assignments import (  # noqa: E402
    propose_symbol_revision_classification,
)
from symgov_backend.models import (  # noqa: E402
    RightsRecord,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
)
from symgov_backend.semantic_concepts import create_semantic_concept  # noqa: E402
from symgov_backend.symbol_semantic_assignments import (  # noqa: E402
    propose_symbol_semantic_assignment,
)

psycopg = pytest.importorskip("psycopg")

PREFIX = f"{V1}/semantic-review"


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(scope="module")
def matrix_database():
    with _database("symgov-tenant-matrix") as (engine, url, raw_url):
        _alembic(url, "upgrade", MIGRATION_HEAD)
        yield engine


@pytest.fixture(scope="module")
def seeded(matrix_database):
    """One OTHER-private symbol carrying one row of every governed kind.

    The sweep needs a foreign row for each symbol-scoped route, and the rows
    are seeded through the services rather than through the API precisely
    because the API refuses to create them -- which is what the sweep is
    proving. OTHER is built suspended: an organisation that has lost its
    Organization Administrator must not start leaking its private symbols
    either.
    """
    engine = matrix_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = _now()

    reviewer_id = _user(Session, email="matrixreviewer@example.test", roles=("reviewer",))
    personal_id = _user(Session, email="matrixpersonal@example.test", roles=("reviewer",))

    # The Platform Administrator probe needs the *most privileged principal
    # that can reach these routes at all*, which is not a bare Platform
    # Administrator. Decision Q2 puts the symbol-scoped routes behind the
    # global `admin`/`reviewer` role, and `platform_admin` is a different
    # axis -- a platform role assignment plus an `admin` base role inside the
    # `symgov` organisation. Seeding only that produces a 403 "Insufficient
    # role" on every route here, which is the authorization boundary and says
    # nothing whatever about section 14.2: an unauthorised caller gets the
    # same 403 for a row that does not exist. Granting the global role too is
    # what makes the refusal a *tenant* refusal, and it is the harder case.
    platform_id = _platform_admin(Session, email="matrixplatform@example.test")
    _user(Session, email="matrixplatform@example.test", roles=("reviewer",))

    with Session() as session:
        owner = upsert_user(
            session, email="matrixowner@example.test", display_name="Owner",
            roles=[], pin="1234", must_change_pin=False,
        )
        session.flush()
        owner_id = uuid.UUID(owner.id) if isinstance(owner.id, str) else owner.id

        acme = _organization(session, code="acme", now=now, active=True)
        other = _organization(session, code="other", now=now, active=False)
        _bind_member(session, organization=acme, user_id=reviewer_id, now=now, base_role="admin")

        discipline = _node(session, scheme_code="ENGINEERING-DISCIPLINE", node_code="MECHANICAL")

        _public_symbol, public_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Matrix Public Valve",
            allocate_catalog_id=True,
        )
        acme_symbol, acme_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Matrix ACME Valve",
            visibility="organization_private", owner_organization_id=acme.id,
            lifecycle_state="draft",
        )
        other_symbol, other_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Matrix OTHER Secret Valve",
            visibility="organization_private", owner_organization_id=other.id,
            lifecycle_state="draft",
        )

        concept, _revision = create_semantic_concept(
            session,
            concept_kind="physical_equipment",
            preferred_name="Matrix Ball Valve",
            definition="A quarter-turn valve used by the tenant matrix.",
            created_by_user_id=owner_id,
            created_at=now,
        )
        session.flush()

        acme_classification = propose_symbol_revision_classification(
            session, symbol_revision_id=acme_revision.id,
            classification_node_id=discipline.id, assignment_role="primary",
            method="legacy_backfill", proposed_at=now,
        )
        other_classification = propose_symbol_revision_classification(
            session, symbol_revision_id=other_revision.id,
            classification_node_id=discipline.id, assignment_role="primary",
            method="legacy_backfill", proposed_at=now,
        )
        other_semantic = propose_symbol_semantic_assignment(
            session, symbol_revision_id=other_revision.id,
            semantic_concept_id=concept.id, assignment_role="primary",
            method="manual", proposed_at=now, proposed_by_user_id=owner_id,
        )
        other_rights = RightsRecord(
            id=uuid.uuid4(),
            symbol_revision_id=other_revision.id,
            rights_status="open",
            disposition="display",
            determination_method="manual",
            decision_status="proposed",
            evidence_json={},
            created_at=now,
            updated_at=now,
        )
        session.add(other_rights)
        session.commit()

        return {
            "acme_organization_id": acme.id,
            "other_organization_id": other.id,
            "reviewer_id": reviewer_id,
            "personal_id": personal_id,
            "platform_id": platform_id,
            "public_revision_id": public_revision.id,
            "acme_symbol_id": acme_symbol.id,
            "acme_revision_id": acme_revision.id,
            "acme_classification_id": acme_classification.id,
            "other_symbol_id": other_symbol.id,
            "other_symbol_slug": other_symbol.slug,
            "other_symbol_name": other_symbol.canonical_name,
            "other_revision_id": other_revision.id,
            "other_classification_id": other_classification.id,
            "other_semantic_assignment_id": other_semantic.id,
            "other_rights_record_id": other_rights.id,
            "concept_id": concept.id,
            "discipline_node_id": discipline.id,
        }


# ---------------------------------------------------------------------------
# The probes
#
# Each names the route it covers by (method, path template), which is the key
# `TENANT_SCOPED` uses -- so the coverage test can compare the two directly
# rather than trusting a hand-kept count.
# ---------------------------------------------------------------------------

FOREIGN_ROW_PROBES = (
    (
        "GET",
        "/semantic-review/symbol-revisions/{symbol_revision_id}",
        lambda s: f"{PREFIX}/symbol-revisions/{s['other_revision_id']}",
        lambda s: None,
    ),
    (
        "POST",
        "/semantic-review/symbol-revisions/{symbol_revision_id}/semantic-assignments",
        lambda s: f"{PREFIX}/symbol-revisions/{s['other_revision_id']}/semantic-assignments",
        lambda s: {
            "semanticConceptId": str(s["concept_id"]),
            "assignmentRole": "primary",
            "method": "manual",
        },
    ),
    (
        "POST",
        "/semantic-review/semantic-assignments/{assignment_id}/decision",
        lambda s: f"{PREFIX}/semantic-assignments/{s['other_semantic_assignment_id']}/decision",
        lambda s: {"targetStatus": "verified"},
    ),
    (
        "POST",
        "/semantic-review/symbol-revisions/{symbol_revision_id}/classifications",
        lambda s: f"{PREFIX}/symbol-revisions/{s['other_revision_id']}/classifications",
        lambda s: {
            "classificationNodeId": str(s["discipline_node_id"]),
            "assignmentRole": "primary",
            "method": "manual",
        },
    ),
    (
        "POST",
        "/semantic-review/symbol-classifications/{assignment_id}/decision",
        lambda s: f"{PREFIX}/symbol-classifications/{s['other_classification_id']}/decision",
        lambda s: {"targetStatus": "rejected"},
    ),
    (
        "POST",
        "/semantic-review/rights-records",
        lambda s: f"{PREFIX}/rights-records",
        lambda s: {
            "disposition": "display",
            "determinationMethod": "manual",
            "symbolRevisionId": str(s["other_revision_id"]),
        },
    ),
    (
        "POST",
        "/semantic-review/rights-records/{record_id}/decision",
        lambda s: f"{PREFIX}/rights-records/{s['other_rights_record_id']}/decision",
        lambda s: {"targetStatus": "rejected", "decisionReason": "Not mine to decide."},
    ),
)

# The three queues that resolve symbol-scoped rows. A queue cannot answer 404
# for a row it simply does not list, so the assertion is absence from the
# listing rather than a status code.
QUEUE_PROBES = (
    (
        "GET",
        "/semantic-review/queues/symbol-classifications",
        "assignmentId",
        "other_classification_id",
    ),
    (
        "GET",
        "/semantic-review/queues/symbol-semantic-assignments",
        "assignmentId",
        "other_semantic_assignment_id",
    ),
    (
        "GET",
        "/semantic-review/queues/rights-records",
        "recordId",
        "other_rights_record_id",
    ),
)

# The unscoped reads whose responses name no symbol at all. The fourth
# unscoped read, the WP1.5 classification preview, *does* name a symbol -- it
# forecasts one -- but it resolves a `ReviewCase`, which carries no
# organisation; its own foreign-case refusal is pinned by
# `test_a_split_item_of_another_case_is_reported_absent` in
# `test_semantic_review_routes_postgresql.py`, and is not re-probed here.
SYMBOL_FREE_UNSCOPED_READS = (
    "/semantic-review/queues/concept-classifications",
    "/semantic-review/queues/concept-external-mappings",
    "/semantic-review/classification-schemes",
)

PRINCIPALS = (
    ("acme_reviewer", "matrixreviewer@example.test"),
    # A Platform Administrator who also holds the global `reviewer` role, so
    # the refusal it meets is the tenant predicate rather than decision Q2's
    # role boundary. Its session binds to the `symgov` organisation, which is
    # neither ACME nor OTHER.
    ("platform_admin", "matrixplatform@example.test"),
    ("personal_session", "matrixpersonal@example.test"),
)


def _as(engine, email):
    client, Session = _client(engine)
    _login(client, email)
    return client, Session


def _assert_no_foreign_content(response, seeded):
    body = response.text
    assert seeded["other_symbol_name"] not in body
    assert seeded["other_symbol_slug"] not in body
    assert str(seeded["other_symbol_id"]) not in body


@pytest.mark.parametrize("principal,email", PRINCIPALS)
@pytest.mark.parametrize("method,template,url,payload", FOREIGN_ROW_PROBES)
def test_a_foreign_symbol_scoped_row_is_absent_not_forbidden(
    matrix_database, seeded, principal, email, method, template, url, payload
):
    """404, never 403, never content -- on every symbol-scoped route.

    `platform_admin` is the probe that carries the argument: its session is
    bound to the `symgov` organisation, so `_scope` returns `symgov` and the
    OTHER-private row is as invisible to it as to anybody else. Widening the
    predicate for a role would change the boundary rather than apply it.
    """
    client, _Session = _as(matrix_database, email)

    body = payload(seeded)
    response = client.request(method, url(seeded), json=body if body is not None else None)

    assert response.status_code == 404, f"{principal} {method} {template} -> {response.status_code}"
    assert response.json()["error"] == "not_found"
    assert response.status_code != 403, "403 confirms the row exists (section 14.2)"
    _assert_no_foreign_content(response, seeded)


@pytest.mark.parametrize("principal,email", PRINCIPALS)
@pytest.mark.parametrize("method,template,id_field,seed_key", QUEUE_PROBES)
def test_no_symbol_scoped_queue_lists_a_foreign_private_row(
    matrix_database, seeded, principal, email, method, template, id_field, seed_key
):
    client, _Session = _as(matrix_database, email)

    response = client.get(f"{V1}{template}", params={"limit": 200})

    assert response.status_code == 200, response.text
    seen = {row[id_field] for row in response.json()["items"]}
    assert str(seeded[seed_key]) not in seen, f"{principal} saw a foreign row on {template}"
    _assert_no_foreign_content(response, seeded)


def test_the_platform_administrator_probe_is_bound_to_its_own_organisation(
    matrix_database, seeded
):
    """Otherwise the sweep's hardest case is weaker than it looks.

    If the Platform Administrator's session were personal-mode, `_scope`
    would return `None` and every refusal above would be the public-only
    path -- true, but a different and much easier assertion than "an
    organisation-bound privileged session cannot cross into another
    organisation". This pins that the session really is bound, to `symgov`.
    """
    client, _Session = _as(matrix_database, "matrixplatform@example.test")

    me = client.get(f"{V1}/auth/me")

    assert me.status_code == 200, me.text
    user = me.json()["user"]

    assert user["isPlatformAdmin"] is True, "the probe must actually be a Platform Administrator"
    assert "reviewer" in user["roles"], "and must hold the global role decision Q2 requires"
    assert user["session"]["mode"] == "organization"
    assert user["organization"]["code"].lower() == "symgov"
    assert str(user["session"]["activeOrganizationId"]) not in {
        str(seeded["acme_organization_id"]),
        str(seeded["other_organization_id"]),
    }


def test_a_bare_platform_administrator_meets_the_role_boundary_not_the_tenant_one(
    matrix_database, seeded
):
    """Measured, and recorded so a reader does not misread the 403.

    Decision Q2 puts the symbol-scoped routes behind the global
    `admin`/`reviewer` role. `platform_admin` is a different axis, so a
    Platform Administrator holding no global role is refused 403 before the
    tenant predicate is ever reached. That is not a section 14.2 disclosure:
    the same 403 comes back for a row that does not exist, so it separates
    nothing. It is why the sweep's platform principal holds the global role
    as well.
    """
    _platform_admin(
        sessionmaker(bind=matrix_database, autoflush=False, expire_on_commit=False),
        email="matrixbareplatform@example.test",
    )
    client, _Session = _as(matrix_database, "matrixbareplatform@example.test")

    foreign = client.get(f"{PREFIX}/symbol-revisions/{seeded['other_revision_id']}")
    nonexistent = client.get(f"{PREFIX}/symbol-revisions/{uuid.uuid4()}")

    assert foreign.status_code == 403, foreign.text
    assert nonexistent.status_code == 403, "the same 403 for a row that does not exist"
    _assert_no_foreign_content(foreign, seeded)


def test_the_sweep_probes_every_tenant_scoped_route(seeded):
    """What makes this a sweep rather than a sample.

    `TENANT_SCOPED` is derived against the router's own decorators in
    `test_semantic_review_tenant_matrix`, so an eleventh symbol-scoped route
    must be classified there and then probed here. Neither half can be
    satisfied by editing the other.
    """
    probed = {(method, template) for method, template, _url, _payload in FOREIGN_ROW_PROBES}
    probed |= {(method, template) for method, template, _f, _k in QUEUE_PROBES}

    assert probed == set(TENANT_SCOPED)


def test_an_organisation_reviewer_still_reaches_its_own_private_rows(matrix_database, seeded):
    """The sweep proves refusal; this proves the refusal is not universal.

    A predicate that refused everything would pass every assertion above and
    be useless. ACME's own private symbol and its own classification row
    remain reachable to the ACME reviewer.
    """
    client, _Session = _as(matrix_database, "matrixreviewer@example.test")

    detail = client.get(f"{PREFIX}/symbol-revisions/{seeded['acme_revision_id']}")
    queue = client.get(f"{PREFIX}/queues/symbol-classifications", params={"limit": 200})

    assert detail.status_code == 200, detail.text
    assert detail.json()["symbol"]["canonicalName"] == "Matrix ACME Valve"
    seen = {row["assignmentId"] for row in queue.json()["items"]}
    assert str(seeded["acme_classification_id"]) in seen


@pytest.mark.parametrize("principal,email", PRINCIPALS)
@pytest.mark.parametrize("template", SYMBOL_FREE_UNSCOPED_READS)
def test_an_unscoped_read_exposes_no_private_symbol_identity(
    matrix_database, seeded, principal, email, template
):
    """The closed list, checked rather than asserted.

    `test_semantic_review_tenant_matrix` records *why* each of these carries
    no tenant predicate; this checks the premise the argument rests on -- that
    the response names no symbol, so there is no private existence for the
    missing predicate to have leaked.
    """
    client, _Session = _as(matrix_database, email)

    response = client.get(f"{V1}{template}", params={"limit": 200})

    assert response.status_code == 200, response.text
    _assert_no_foreign_content(response, seeded)
    assert "canonicalName" not in response.text, f"{template} names a symbol after all"


def test_a_refused_probe_writes_nothing(matrix_database, seeded):
    """A 404 that had already written would leak by side effect.

    Every probe in the sweep is run once more here, as the ACME reviewer, and
    the OTHER revision's three governed row counts and three statuses are
    compared before and after. A refusal that created a proposal, or moved a
    decision, would be invisible to a status-code assertion.
    """
    client, Session = _as(matrix_database, "matrixreviewer@example.test")
    revision_id = seeded["other_revision_id"]

    def snapshot():
        with Session() as session:
            classifications = session.execute(
                select(SymbolRevisionClassificationAssignment)
                .where(SymbolRevisionClassificationAssignment.symbol_revision_id == revision_id)
            ).scalars().all()
            semantics = session.execute(
                select(SymbolSemanticAssignment)
                .where(SymbolSemanticAssignment.symbol_revision_id == revision_id)
            ).scalars().all()
            rights = session.execute(
                select(RightsRecord).where(RightsRecord.symbol_revision_id == revision_id)
            ).scalars().all()
            return (
                sorted((str(row.id), row.status) for row in classifications),
                sorted((str(row.id), row.status) for row in semantics),
                sorted((str(row.id), row.decision_status) for row in rights),
            )

    before = snapshot()

    for method, _template, url, payload in FOREIGN_ROW_PROBES:
        body = payload(seeded)
        response = client.request(method, url(seeded), json=body if body is not None else None)
        assert response.status_code == 404, response.text

    assert snapshot() == before
