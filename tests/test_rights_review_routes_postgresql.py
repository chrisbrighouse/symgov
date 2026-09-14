"""PostgreSQL cover for SM-P1-01 WP1.3: rights record governance over HTTP.

The rules this file proves are not route policy -- they are the governance
contract `transition_rights_record` enforces and the database backs, and they
only mean anything against a migrated server:

* **Section 8.4.** An `ai_assisted` determination cannot approve itself. This
  is `ck_rights_records_approved_not_ai_determined` as well as a service
  check, and it is the rule that makes WP1.3 necessary at all:
  `publication_gate.propose_intake_rights_record` writes `ai_assisted` and is
  the only other creator of a `RightsRecord` in the codebase, so every rights
  record production holds is permanently unapprovable.
* **Section 7.12's triple.** Approving records who, when and why. A named
  decider is required unconditionally -- there is no controlled-system rights
  decision -- and `decided_by_user_id` is the one foreign key in the semantic
  model with `ON DELETE RESTRICT`, because the approver must outlive the
  approval.
* **The licence and disposition rules.** A permissive disposition can only be
  approved on a status that supports it, and `licensed` or `restricted`
  cannot be approved without a reference to the terms.
* **Succession.** Approving a record retires whichever was approved before it
  for the same subject, so one approved record per subject is upheld by
  succession rather than by refusing the new decision.
* **Section 14.2.** A symbol-subject record is tenant-scoped; a package- or
  standard-subject record is a platform-level assertion about a licence and is
  reachable at every scope, because withholding it would hide the very records
  section 9.2's rights dimension needs approved.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_semantic_review_routes_postgresql import (  # noqa: E402
    MIGRATION_HEAD,
    V1,
    _client,
    _login,
    _organization,
    _bind_member,
    _platform_admin,
    _symbol,
    _user,
)
from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.models import RightsRecord  # noqa: E402
from symgov_backend.source_package_acquisition import register_source_package  # noqa: E402

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def rights_database():
    with _database("symgov-rights-review-api") as (engine, url, raw_url):
        _alembic(url, "upgrade", MIGRATION_HEAD)
        yield engine


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(scope="module")
def seeded(rights_database):
    """A public revision, an ACME-private revision, an OTHER-private revision,
    and one source package whose rights are a platform-level assertion."""
    engine = rights_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = _now()

    reviewer_id = _user(Session, email="rightsreviewer@example.test", roles=("reviewer",))

    with Session() as session:
        owner = upsert_user(
            session, email="rightsowner@example.test", display_name="Owner",
            roles=[], pin="1234", must_change_pin=False,
        )
        session.flush()
        owner_id = uuid.UUID(owner.id) if isinstance(owner.id, str) else owner.id

        acme = _organization(session, code="acme", now=now, active=True)
        other = _organization(session, code="other", now=now, active=False)
        _bind_member(session, organization=acme, user_id=reviewer_id, now=now, base_role="admin")

        _public, public_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Rights Public Valve",
            allocate_catalog_id=True,
        )
        _acme, acme_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Rights ACME Valve",
            visibility="organization_private", owner_organization_id=acme.id, lifecycle_state="draft",
        )
        _other, other_revision = _symbol(
            session, owner_id=owner_id, now=now, canonical_name="Rights OTHER Valve",
            visibility="organization_private", owner_organization_id=other.id, lifecycle_state="draft",
        )

        package = register_source_package(
            session,
            package_code="EXAMPLE-LIB-2026-1",
            title="Example Authoritative Library",
            registered_at=now,
            provider="Example Provider",
            release_version="2026.1",
            acquisition_method="licensed_download",
            acquired_at=now,
            licence_reference="CONTRACT-RIGHTS-001",
        )
        session.flush()
        session.commit()

        return {
            "public_revision_id": public_revision.id,
            "acme_revision_id": acme_revision.id,
            "other_revision_id": other_revision.id,
            "source_package_id": package.id,
            "reviewer_id": reviewer_id,
        }


@pytest.fixture
def reviewer_client(rights_database, seeded):
    client, Session = _client(rights_database)
    _login(client, "rightsreviewer@example.test")
    return client, Session


def _propose(client, **body):
    return client.post(f"{V1}/semantic-review/rights-records", json=body)


def _decide(client, record_id, **body):
    return client.post(f"{V1}/semantic-review/rights-records/{record_id}/decision", json=body)


def test_a_reviewer_proposes_and_approves_a_permissive_record(reviewer_client, seeded):
    """The journey the whole package exists for.

    Section 7.12's triple lands on the row: who took the decision, when, and
    why. The proposal is attributed to the reviewer too, so the record names a
    person at both ends.
    """
    client, Session = reviewer_client

    proposed = _propose(
        client,
        disposition="distribute",
        determinationMethod="licence_document",
        rightsStatus="licensed",
        symbolRevisionId=str(seeded["public_revision_id"]),
        licenceReference="CONTRACT-RIGHTS-001",
    )
    assert proposed.status_code == 201, proposed.text
    body = proposed.json()

    assert body["status"] == "proposed", "section 8.4: a determination starts proposed"
    assert body["capabilities"]["canVerify"] is True
    assert body["capabilities"]["mustRepropose"] is False
    record_id = body["recordId"]

    approved = _decide(
        client,
        record_id,
        targetStatus="approved",
        decisionReason="Distribution is permitted under the recorded contract.",
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    with Session() as session:
        stored = session.get(RightsRecord, uuid.UUID(record_id))
        assert stored.decision_status == "approved"
        assert str(stored.decided_by_user_id) == str(seeded["reviewer_id"])
        assert str(stored.proposed_by_user_id) == str(seeded["reviewer_id"])
        assert stored.decided_at is not None
        assert stored.decision_reason == "Distribution is permitted under the recorded contract."


def test_an_ai_assisted_determination_cannot_approve_itself(reviewer_client, seeded):
    """Section 8.4 -- and the reason this package is not optional.

    `propose_intake_rights_record` writes exactly this record on every
    promotion, and it is the only rights record production has. If it could
    be approved here, WP1.3 would be a convenience; because it cannot, a
    reviewer's own proposal is the only way the section 9.2 gate's rights
    dimension is ever satisfied.
    """
    client, _Session = reviewer_client

    proposed = _propose(
        client,
        disposition="distribute",
        determinationMethod="ai_assisted",
        rightsStatus="open",
        symbolRevisionId=str(seeded["public_revision_id"]),
    )
    assert proposed.status_code == 201, proposed.text
    body = proposed.json()

    assert body["capabilities"]["canVerify"] is False
    assert body["capabilities"]["mustRepropose"] is True
    assert "ai_assisted" in body["capabilities"]["blockedReason"]

    refused = _decide(
        client, body["recordId"], targetStatus="approved", decisionReason="Looks fine.",
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"] == "validation_error"
    assert "ai_assisted" in refused.text


def test_approving_without_a_reason_is_refused(reviewer_client, seeded):
    """Section 7.12 asks for the "why" of an approval specifically."""
    client, _Session = reviewer_client
    record_id = _propose(
        client,
        disposition="display",
        determinationMethod="manual",
        rightsStatus="open",
        symbolRevisionId=str(seeded["public_revision_id"]),
    ).json()["recordId"]

    refused = _decide(client, record_id, targetStatus="approved")

    assert refused.status_code == 422, refused.text
    assert "reason" in refused.text


def test_a_licence_backed_status_cannot_be_approved_without_a_reference(reviewer_client, seeded):
    client, _Session = reviewer_client
    record_id = _propose(
        client,
        disposition="distribute",
        determinationMethod="licence_document",
        rightsStatus="licensed",
        symbolRevisionId=str(seeded["public_revision_id"]),
    ).json()["recordId"]

    refused = _decide(
        client, record_id, targetStatus="approved", decisionReason="Contract found.",
    )

    assert refused.status_code == 422, refused.text
    assert "licence reference" in refused.text


def test_the_decision_may_establish_the_status_and_licence_it_was_proposed_without(
    reviewer_client, seeded
):
    """A record proposed as `unknown` becomes `licensed` when the contract is
    found -- the decision is often what establishes both."""
    client, Session = reviewer_client
    record_id = _propose(
        client,
        disposition="distribute",
        determinationMethod="licence_document",
        symbolRevisionId=str(seeded["public_revision_id"]),
    ).json()["recordId"]

    approved = _decide(
        client,
        record_id,
        targetStatus="approved",
        decisionReason="Contract located in the provider portal.",
        rightsStatus="licensed",
        licenceReference="CONTRACT-RIGHTS-002",
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["rightsStatus"] == "licensed"
    assert approved.json()["licenceReference"] == "CONTRACT-RIGHTS-002"


def test_a_permissive_disposition_is_refused_on_a_status_that_cannot_support_it(
    reviewer_client, seeded
):
    client, _Session = reviewer_client
    record_id = _propose(
        client,
        disposition="distribute",
        determinationMethod="manual",
        rightsStatus="prohibited",
        symbolRevisionId=str(seeded["public_revision_id"]),
    ).json()["recordId"]

    refused = _decide(
        client, record_id, targetStatus="approved", decisionReason="Trying anyway.",
    )

    assert refused.status_code == 422, refused.text
    assert "prohibited" in refused.text


def test_retiring_a_record_carries_no_decider(reviewer_client, seeded):
    """Retirement is a succession, not a judgement about the work.

    `transition_rights_record` refuses a decider on anything but an approval
    or a rejection, so the route passes the actor only where it is a
    decision -- a detail that would otherwise turn every retirement into a
    422.
    """
    client, Session = reviewer_client
    record_id = _propose(
        client,
        disposition="metadata_only",
        determinationMethod="manual",
        symbolRevisionId=str(seeded["acme_revision_id"]),
    ).json()["recordId"]

    retired = _decide(client, record_id, targetStatus="retired")

    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired"
    with Session() as session:
        stored = session.get(RightsRecord, uuid.UUID(record_id))
        assert stored.decided_by_user_id is None
        assert stored.decided_at is None


def test_approving_a_second_record_retires_the_first_for_the_same_subject(reviewer_client, seeded):
    client, Session = reviewer_client
    revision_id = str(seeded["acme_revision_id"])

    first = _propose(
        client, disposition="display", determinationMethod="manual",
        rightsStatus="open", symbolRevisionId=revision_id,
    ).json()["recordId"]
    _decide(client, first, targetStatus="approved", decisionReason="First determination.")

    second = _propose(
        client, disposition="distribute", determinationMethod="manual",
        rightsStatus="open", symbolRevisionId=revision_id,
    ).json()["recordId"]
    approved = _decide(
        client, second, targetStatus="approved", decisionReason="Revised after legal review.",
    )

    assert approved.status_code == 200, approved.text
    with Session() as session:
        assert session.get(RightsRecord, uuid.UUID(first)).decision_status == "retired"
        assert session.get(RightsRecord, uuid.UUID(second)).decision_status == "approved"


def test_the_approver_cannot_be_deleted_while_the_approval_stands(rights_database, seeded):
    """`ON DELETE RESTRICT` -- the one such key in the semantic model.

    Section 7.12 wants the "who" of an approval to remain answerable, so the
    approver must outlive the approval.
    """
    Session = _sessionmaker(rights_database)
    approver_id = uuid.uuid4()
    now = _now()
    with Session() as session:
        # Its own subject: only one record per subject may be approved, and
        # other tests in this module approve records against the shared
        # fixture revisions. Reusing one would fail on that rule at commit,
        # before the foreign key under test was ever exercised.
        _symbol_row, isolated_revision = _symbol(
            session,
            owner_id=seeded["reviewer_id"],
            now=now,
            canonical_name=f"Approver Key Valve {approver_id.hex[:6]}",
            visibility="organization_private",
            lifecycle_state="draft",
        )
        session.flush()
        # A bare user row, never logged in. Deleting a user who *has* logged
        # in trips the unrelated append-only guard on
        # `auth_login_attempt_events` first, which would make this test pass
        # for the wrong reason -- it is the rights foreign key under test, not
        # the authentication audit trigger.
        session.execute(
            text(
                "INSERT INTO users (id,email,display_name,pin_hash,pin_set_at,"
                "must_change_pin,is_active,created_at,updated_at) "
                "VALUES (:id,:email,:email,'test',:now,false,true,:now,:now)"
            ),
            {"id": approver_id, "email": f"approver-{approver_id.hex[:8]}@example.test", "now": now},
        )
        session.add(
            RightsRecord(
                id=uuid.uuid4(),
                symbol_revision_id=isolated_revision.id,
                rights_status="open",
                disposition="display",
                determination_method="manual",
                decision_status="approved",
                decided_by_user_id=approver_id,
                decided_at=now,
                decision_reason="Display is permitted.",
                evidence_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    with Session() as session:
        with pytest.raises(IntegrityError):
            session.execute(text("DELETE FROM users WHERE id = :id"), {"id": approver_id})
            session.flush()
        session.rollback()


def test_a_package_subject_record_is_platform_level_and_needs_no_tenant(rights_database, seeded):
    """Section 9.2's rights dimension reads package records as well.

    A record whose subject is a source package names no symbol and has no
    tenant, so a personal-mode session -- which is public-only for symbols --
    must still be able to propose and decide one.
    """
    client, _Session = _client(rights_database)
    _user(_sessionmaker(rights_database), email="rightspersonal@example.test", roles=("reviewer",))
    _login(client, "rightspersonal@example.test")

    proposed = _propose(
        client,
        disposition="distribute",
        determinationMethod="licence_document",
        rightsStatus="licensed",
        sourcePackageId=str(seeded["source_package_id"]),
        licenceReference="CONTRACT-RIGHTS-001",
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["subjectKind"] == "source_package"
    assert proposed.json()["symbol"] is None

    approved = _decide(
        client,
        proposed.json()["recordId"],
        targetStatus="approved",
        decisionReason="The library licence permits redistribution.",
    )

    assert approved.status_code == 200, approved.text


def _sessionmaker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_another_organisations_revision_is_absent_rather_than_forbidden(reviewer_client, seeded):
    """404, not 403, on both routes.

    A 403 would confirm that the revision exists and belongs to someone else,
    which is the private-symbol existence section 14.2 forbids revealing --
    and proposing rights over it would be a write into another tenant.
    """
    client, Session = reviewer_client

    proposed = _propose(
        client,
        disposition="display",
        determinationMethod="manual",
        symbolRevisionId=str(seeded["other_revision_id"]),
    )

    assert proposed.status_code == 404, proposed.text
    assert proposed.json()["error"] == "not_found"

    with Session() as session:
        leaked = session.execute(
            select(RightsRecord).where(RightsRecord.symbol_revision_id == seeded["other_revision_id"])
        ).scalars().all()
        assert leaked == [], "a refused proposal must write nothing"


def test_a_decision_on_another_organisations_record_is_absent_rather_than_forbidden(
    rights_database, seeded
):
    client, Session = _client(rights_database)
    _platform_admin(_sessionmaker(rights_database), email="rightsplatform@example.test")
    _login(client, "rightsplatform@example.test")

    # Seeded directly: the point is the decision route's scope check, and a
    # record on another organisation's revision cannot be created through the
    # propose route at all -- which the previous test pins.
    with _sessionmaker(rights_database)() as session:
        record = RightsRecord(
            id=uuid.uuid4(),
            symbol_revision_id=seeded["other_revision_id"],
            rights_status="open",
            disposition="display",
            determination_method="manual",
            decision_status="proposed",
            evidence_json={},
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(record)
        session.commit()
        record_id = record.id

    reviewer, _Session = _client(rights_database)
    _login(reviewer, "rightsreviewer@example.test")

    refused = _decide(reviewer, record_id, targetStatus="rejected", decisionReason="No.")

    assert refused.status_code == 404, refused.text
    with Session() as session:
        assert session.get(RightsRecord, record_id).decision_status == "proposed"


def test_the_rights_queue_shows_the_reviewers_own_proposal(reviewer_client, seeded):
    """The queue WP1.2 exposed now has something in it a reviewer can act on.

    Before WP1.3 every row it could show was `ai_assisted` and therefore
    `mustRepropose`, with no route to repropose through.
    """
    client, _Session = reviewer_client
    record_id = _propose(
        client,
        disposition="display",
        determinationMethod="manual",
        rightsStatus="open",
        symbolRevisionId=str(seeded["acme_revision_id"]),
    ).json()["recordId"]

    queue = client.get(f"{V1}/semantic-review/queues/rights-records", params={"limit": 200})

    assert queue.status_code == 200, queue.text
    rows = {row["recordId"]: row for row in queue.json()["items"]}
    assert record_id in rows
    assert rows[record_id]["capabilities"]["canVerify"] is True
