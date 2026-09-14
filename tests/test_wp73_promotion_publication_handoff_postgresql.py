"""Stage 7 WP7.3 regression: promotion request -> opened ReviewCase -> the
*existing, unmodified* `POST /workspace/review-cases/{id}/decisions`
endpoint -> `execute_publication_handoff`'s new organization-symbol-
promotion branch -> real `published_pages`/`pack_entries` rows and a
`visibility='public'` governed symbol, exercised end-to-end over real HTTP
against a disposable PostgreSQL container.

Real Postgres is required (not the SQLite fixture WP7.2's route test
uses): `ensure_catalog_symbol_id`'s `nextval('catalog_symbol_id_seq')`,
the deferred `trg_published_pages_validate_catalog_publication`/
`trg_pack_entries_validate_catalog_publication` constraint triggers, and
`active_public_symbol_projections`'s join semantics are all Postgres-only.

Proves, per the programme plan §13 and the Stage 7 plan's Q1/Q4 decisions:
- A reviewer holding the existing global `admin`/`reviewer` role -- not an
  organization-scoped role -- opens and decides the review case (FR-PUB-003:
  "use the existing Symgov review model").
- Acceptance sets `visibility='public'` on the *same* governed-symbol row
  (no second governed symbol is ever created) and publishes the *exact*
  organization-approved revision, not a newly derived one.
- The promoted symbol becomes visible through `active_public_symbol_projections`
  (the real reader-facing gate WP7.1 hardened) once accepted.
- The promotion request's own state closes to `accepted`, decision-logged.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

# `resolve_eligible_organization_memberships` (organization_authorization.py)
# requires the organization's normalized_code to be in the configured pilot
# allowlist AND the organization to be genuinely active/entitled -- unlike
# the service-layer-only Postgres tests' `_organization`/`_membership`
# helpers (which create an inactive org with a random code, fine when a
# test constructs `AuthenticatedUser` by hand and never logs in for real),
# this file drives a real `/auth/login`, so it needs a real eligible
# organization.
ORGANIZATION_CODE = "acme"


def _add_membership(Session, user_id, *, base_role="user", capabilities=()):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = session.query(Organization).filter(Organization.normalized_code == ORGANIZATION_CODE).one_or_none()
        if organization is None:
            organization = Organization(
                id=uuid.uuid4(),
                code=ORGANIZATION_CODE.upper(),
                normalized_code=ORGANIZATION_CODE,
                display_name="ACME Organization",
                name_key="acme-organization",
                entitlement_status="active",
                is_active=True,
                is_protected=False,
                fallback_icon_svg="<svg/>",
                created_at=now,
                updated_at=now,
            )
            session.add(organization)
            session.flush()
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
                id=uuid.uuid4(), membership_id=membership.id, base_role=base_role, is_active=True,
                assigned_at=now, revoked_at=None,
            )
        )
        for capability in capabilities:
            session.add(
                OrganizationMemberCapability(
                    id=uuid.uuid4(), membership_id=membership.id, capability=capability, is_active=True,
                    granted_at=now,
                )
            )
        session.commit()
        return organization.id


def _create_user_with_global_roles(Session, *, email: str, display_name: str, roles: list[str]):
    """`upsert_user`'s global-role assignment (`auth.py`) only applies when
    the user's subscription tier is 'plus' -- a brand-new user defaults to
    'free', so passing `roles=` on first creation is silently dropped.
    Upgrade the tier first, then re-apply the roles."""
    with Session() as session:
        user = upsert_user(session, email=email, display_name=display_name, roles=[], pin="1234", must_change_pin=False)
        session.commit()
        user_id = user.id
    with Session() as session:
        subscription = session.get(UserSubscription, user_id)
        subscription.tier = "plus"
        # ck_user_subscriptions_tier_expiry: 'plus' requires either
        # is_protected or a non-null expires_on.
        subscription.expires_on = datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 1)
        session.commit()
    with Session() as session:
        upsert_user(session, email=email, display_name=display_name, roles=roles, pin="1234", must_change_pin=False)
        session.commit()
    return user_id

# Moved from 20260904_0043 to head by SM-P0-08. This file drives the real
# organization-promotion path, and section 9.2's publication gate now sits on
# it -- reading `symbol_semantic_assignments`, `symbol_revision_classifications`,
# `rights_records`, `asset_transformations` and `publication_gate_*`, none of
# which exist at 20260904_0043. An upgrade target, not a head assertion: it is
# here so the fixture has the schema the code under test reads, and it must
# track head for as long as that stays true.
NEW_MIGRATION_HEAD = "20260911_0057"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp73_database():
    with _database("symgov-wp73") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            # Production runs migrations as symgov_app, so it owns every
            # table outright; this disposable rehearsal runs migrations as
            # postgres, so the equivalent access is granted explicitly here
            # (mirrors test_organization_symbol_postgresql.py's own
            # stage5_database fixture).
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT ON catalog_symbol_identifiers TO symgov_app",
                "GRANT USAGE, SELECT ON SEQUENCE catalog_symbol_id_seq TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_cases TO symgov_app",
                "GRANT SELECT, INSERT ON human_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_case_actions TO symgov_app",
                "GRANT SELECT, INSERT ON publication_approval_targets TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT ON active_public_symbol_projections TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _client(engine):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        organization_pilot_codes=("acme",),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), TestingSessionLocal


def test_promotion_request_publishes_the_existing_symbol_through_the_real_workspace_decision_endpoint(wp73_database):
    engine, _, _ = wp73_database

    admin_client, Session = _client(engine)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        admin_user = upsert_user(
            session, email="wp73admin@example.test", display_name="WP7.3 Admin", roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
        admin_user_id = admin_user.id
    _add_membership(Session, admin_user_id, base_role="admin", capabilities=("contributor", "symbol_reviewer"))

    login = admin_client.post("/api/v1/auth/login", json={"email": "wp73admin@example.test", "pin": "1234"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["session"]["mode"] == "organization"

    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": "WP7.3 Fire Hydrant", "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]

    submit_review_response = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={}
    )
    assert submit_review_response.status_code == 200, submit_review_response.text
    review_submission_id = submit_review_response.json()["id"]

    decide_review_response = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{review_submission_id}/decision",
        json={"decision": "approved"},
    )
    assert decide_review_response.status_code == 200, decide_review_response.text

    submit_promotion_response = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful outside our organization.", "sharingAcknowledgment": True},
    )
    assert submit_promotion_response.status_code == 200, submit_promotion_response.text
    promotion_request_id = submit_promotion_response.json()["id"]
    assert submit_promotion_response.json()["status"] == "submitted"

    reviewer_client, _ = _client(engine)
    _create_user_with_global_roles(
        Session, email="wp73reviewer@example.test", display_name="WP7.3 Reviewer", roles=["reviewer"]
    )

    reviewer_login = reviewer_client.post("/api/v1/auth/login", json={"email": "wp73reviewer@example.test", "pin": "1234"})
    assert reviewer_login.status_code == 200, reviewer_login.text
    assert reviewer_login.json()["user"]["session"]["mode"] == "personal"

    open_review_response = reviewer_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review"
    )
    assert open_review_response.status_code == 200, open_review_response.text
    opened = open_review_response.json()
    assert opened["status"] == "triage"
    review_case_id = opened["reviewCaseId"]
    assert review_case_id

    decision_response = reviewer_client.post(
        f"/api/v1/workspace/review-cases/{review_case_id}/decisions",
        json={"decisionCode": "approve"},
    )
    assert decision_response.status_code == 200, decision_response.text
    decision_body = decision_response.json()
    assert decision_body["currentStage"] == "published"
    assert decision_body["closedAt"] is not None

    # --- Verify the actual database effects, not just the HTTP response ---
    with engine.begin() as connection:
        symbol_row = connection.execute(
            text("SELECT visibility, current_revision_id, catalog_symbol_id FROM governed_symbols WHERE id=:id"),
            {"id": symbol_id},
        ).one()
        assert symbol_row.visibility == "public"
        assert str(symbol_row.current_revision_id) == revision_id
        assert symbol_row.catalog_symbol_id is not None

        revision_state = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id=:id"), {"id": revision_id}
        ).scalar_one()
        assert revision_state == "published"

        promotion_row = connection.execute(
            text("SELECT status, closed_at, review_case_id FROM promotion_requests WHERE id=:id"),
            {"id": promotion_request_id},
        ).one()
        assert promotion_row.status == "accepted"
        assert promotion_row.closed_at is not None
        assert str(promotion_row.review_case_id) == review_case_id

        page_and_entry = connection.execute(
            text(
                "SELECT pp.publication_state AS page_state, pe.publication_state AS entry_state, pack.status AS pack_status "
                "FROM published_pages pp "
                "JOIN pack_entries pe ON pe.published_page_id = pp.id "
                "JOIN publication_packs pack ON pack.id = pp.pack_id "
                "WHERE pp.current_symbol_revision_id=:revision"
            ),
            {"revision": revision_id},
        ).one()
        assert page_and_entry.page_state == "active"
        assert page_and_entry.entry_state == "active"
        assert page_and_entry.pack_status == "published"

        projected = connection.execute(
            text("SELECT 1 FROM active_public_symbol_projections WHERE governed_symbol_id=:id"),
            {"id": symbol_id},
        ).first()
        assert projected is not None, "promoted symbol must be visible through the real public reader gate"

        decisions_logged = connection.execute(
            text("SELECT decision_code FROM promotion_request_decisions WHERE promotion_request_id=:id ORDER BY created_at"),
            {"id": promotion_request_id},
        ).scalars().all()
        assert decisions_logged == ["triage", "accepted"]


def test_only_admin_or_reviewer_role_can_open_a_promotion_review_case(wp73_database):
    engine, _, _ = wp73_database
    admin_client, Session = _client(engine)
    with Session() as session:
        admin_user = upsert_user(
            session, email="wp73admin2@example.test", display_name="Admin2", roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
        admin_user_id = admin_user.id
    _add_membership(Session, admin_user_id, base_role="admin", capabilities=("contributor", "symbol_reviewer"))

    admin_client.post("/api/v1/auth/login", json={"email": "wp73admin2@example.test", "pin": "1234"})
    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": "WP7.3 Valve", "category": "process", "discipline": "mechanical", "summary": "A valve."},
    )
    symbol_id, revision_id = create_response.json()["id"], create_response.json()["currentRevisionId"]
    submit_review = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "x", "sharingAcknowledgment": True},
    )
    promotion_request_id = submit_promotion.json()["id"]

    # An unprivileged personal-mode user (no admin/reviewer role) cannot open the review case.
    other_client, _ = _client(engine)
    with Session() as session:
        upsert_user(
            session, email="wp73nobody@example.test", display_name="Nobody", roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
    other_client.post("/api/v1/auth/login", json={"email": "wp73nobody@example.test", "pin": "1234"})
    forbidden_response = other_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review"
    )
    assert forbidden_response.status_code == 403


# --- SM-P1-01 WP1.0: the organization-promotion classification dual-write ---
#
# `apply_classification_mapping` had exactly two production call sites, both
# in `publication_handoff.py` (the Rupert/Daisy intake path). The organization
# promotion path enforced section 9.2's publication gate but never proposed a
# structured classification, so a symbol promoted out of an organization
# arrived in the public catalogue with free-text `category`/`discipline` only
# and no `symbol_revision_classifications` row -- invisible to the M5
# catalogue assignment read that is switched on in production, and absent from
# any semantic review queue. Specification section 12.1 phase M4; a P0 defect
# against it, repaired here.
#
# These run over the real HTTP promotion journey rather than calling the
# handoff directly, because the defect was in which production caller invokes
# the mapper, not in the mapper.


def _promote_organization_symbol(engine, *, suffix, name, category, discipline):
    """Drive one organization symbol from draft to accepted public promotion.

    Returns `(symbol_id, revision_id)`. Each call uses its own admin and
    reviewer emails: the database fixture is module-scoped, and the deciding
    reviewer must not be a member of the submitting organization.
    """
    admin_email = f"wp10admin-{suffix}@example.test"
    reviewer_email = f"wp10reviewer-{suffix}@example.test"

    admin_client, Session = _client(engine)
    with Session() as session:
        admin_user = upsert_user(
            session,
            email=admin_email,
            display_name=f"WP1.0 Admin {suffix}",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        session.commit()
        admin_user_id = admin_user.id
    _add_membership(Session, admin_user_id, base_role="admin", capabilities=("contributor", "symbol_reviewer"))

    login = admin_client.post("/api/v1/auth/login", json={"email": admin_email, "pin": "1234"})
    assert login.status_code == 200, login.text

    create_response = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": category, "discipline": discipline, "summary": "A WP1.0 fixture symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    symbol_id = create_response.json()["id"]
    revision_id = create_response.json()["currentRevisionId"]

    submit_review = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={}
    )
    assert submit_review.status_code == 200, submit_review.text
    decide_review = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit_review.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide_review.status_code == 200, decide_review.text

    submit_promotion = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful outside our organization.", "sharingAcknowledgment": True},
    )
    assert submit_promotion.status_code == 200, submit_promotion.text
    promotion_request_id = submit_promotion.json()["id"]

    reviewer_client, _ = _client(engine)
    _create_user_with_global_roles(
        Session, email=reviewer_email, display_name=f"WP1.0 Reviewer {suffix}", roles=["reviewer"]
    )
    reviewer_login = reviewer_client.post("/api/v1/auth/login", json={"email": reviewer_email, "pin": "1234"})
    assert reviewer_login.status_code == 200, reviewer_login.text

    open_review = reviewer_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promotion_request_id}/open-review"
    )
    assert open_review.status_code == 200, open_review.text
    review_case_id = open_review.json()["reviewCaseId"]

    decision_response = reviewer_client.post(
        f"/api/v1/workspace/review-cases/{review_case_id}/decisions",
        json={"decisionCode": "approve"},
    )
    assert decision_response.status_code == 200, decision_response.text
    assert decision_response.json()["currentStage"] == "published"
    return symbol_id, revision_id


def _classification_assignments(engine, revision_id):
    with engine.begin() as connection:
        return connection.execute(
            text(
                "SELECT cs.scheme_code, cn.node_code, cn.preferred_label, a.assignment_role, "
                "       a.status, a.method, a.evidence_json->>'match_basis' AS match_basis, "
                "       a.evidence_json->>'raw_value' AS raw_value, a.proposed_by_user_id "
                "FROM symbol_revision_classifications a "
                "JOIN classification_nodes cn ON cn.id = a.classification_node_id "
                "JOIN classification_schemes cs ON cs.id = a.classification_scheme_id "
                "WHERE a.symbol_revision_id = :revision "
                "ORDER BY cs.scheme_code"
            ),
            {"revision": revision_id},
        ).all()


def _legacy_columns(engine, symbol_id):
    with engine.begin() as connection:
        return connection.execute(
            text("SELECT category, discipline FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).one()


def test_organization_promotion_proposes_structured_classification_assignments(wp73_database):
    """A promoted organization symbol must reach the public catalogue with
    SM-P0-07's structured assignments, not free text alone.

    `mechanical` matches the seeded `Mechanical` discipline node exactly;
    `valve` reaches `Valves` through the trailing-S rule. Both are display-
    eligible bases, so SM-P0-09's dual-write may also canonicalize the legacy
    columns -- proved separately below for the coarse basis that may not.
    """
    engine, _, _ = wp73_database
    symbol_id, revision_id = _promote_organization_symbol(
        engine, suffix="exact", name="WP1.0 Ball Valve", category="valve", discipline="mechanical"
    )

    rows = _classification_assignments(engine, revision_id)
    by_scheme = {row.scheme_code: row for row in rows}
    assert set(by_scheme) == {"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"}, rows

    discipline_row = by_scheme["ENGINEERING-DISCIPLINE"]
    assert discipline_row.node_code == "MECHANICAL"
    assert discipline_row.preferred_label == "Mechanical"
    assert discipline_row.match_basis == "exact"
    assert discipline_row.raw_value == "mechanical"

    category_row = by_scheme["SYMBOL-CATEGORY-FAMILY"]
    assert category_row.node_code == "VALVES"
    assert category_row.preferred_label == "Valves"
    assert category_row.match_basis == "plural_variant"
    assert category_row.raw_value == "valve"

    for row in rows:
        # Section 12.3: promotion proposes, it does not verify. A human
        # decision through the semantic review queue is what verifies.
        assert row.status == "proposed"
        assert row.assignment_role == "primary"
        assert row.method == "source_mapping"
        # Attributed to the deciding reviewer, never to a service user.
        assert row.proposed_by_user_id is not None

    # SM-P0-09's dual-write: a display-eligible basis canonicalizes the legacy
    # display columns from the assignment the promotion has just proposed.
    legacy = _legacy_columns(engine, symbol_id)
    assert legacy.discipline == "Mechanical"
    assert legacy.category == "Valves"


def test_organization_promotion_keeps_a_coarse_taxonomy_match_out_of_the_legacy_columns(wp73_database):
    """`legacy_taxonomy` is in `NON_DISPLAY_MATCH_BASES` for a measured
    reason (SM-P0-09, commit 56677a9): the browse taxonomy maps were built for
    bucketing, and using them to rewrite a durable column destroyed
    distinctions operators had recorded. The assignment is still proposed --
    it is real structure -- but the operator's own `civil` stays in the
    column. The repaired promotion path must honour the same rule the intake
    path does.
    """
    engine, _, _ = wp73_database
    symbol_id, revision_id = _promote_organization_symbol(
        engine, suffix="coarse", name="WP1.0 Retaining Wall", category="fire", discipline="civil"
    )

    rows = _classification_assignments(engine, revision_id)
    by_scheme = {row.scheme_code: row for row in rows}

    # `civil` reaches `Civil / Structural` only through the browse taxonomy.
    assert by_scheme["ENGINEERING-DISCIPLINE"].node_code == "CIVIL_STRUCTURAL"
    assert by_scheme["ENGINEERING-DISCIPLINE"].match_basis == "legacy_taxonomy"
    # `fire` is a discipline short form, not a category one: no category node.
    assert "SYMBOL-CATEGORY-FAMILY" not in by_scheme, rows

    legacy = _legacy_columns(engine, symbol_id)
    assert legacy.discipline == "civil", "a coarse match must not rewrite the display column"
    assert legacy.category == "fire", "an unmatched facet must not clear the display column"


def test_a_classification_mapping_failure_still_promotes_the_symbol(wp73_database, monkeypatch):
    """SM-P0-07's rule, which the repaired path inherits: the classification
    proposal is wrapped whole as well as per item, so even a total failure in
    the mapper records itself in the payload where a reviewer can see it
    rather than refusing a promotion a human has already approved. The
    publication gate is the thing that refuses; this is not.
    """
    engine, _, _ = wp73_database

    def _explode(*args, **kwargs):
        raise RuntimeError("deliberate WP1.0 mapper failure")

    monkeypatch.setattr("symgov_backend.publication_handoff.apply_classification_mapping", _explode)

    symbol_id, revision_id = _promote_organization_symbol(
        engine, suffix="failure", name="WP1.0 Broken Mapper", category="valve", discipline="mechanical"
    )

    with engine.begin() as connection:
        symbol_row = connection.execute(
            text("SELECT visibility, catalog_symbol_id FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).one()
        assert symbol_row.visibility == "public"
        assert symbol_row.catalog_symbol_id is not None

        report = connection.execute(
            text("SELECT payload_json->'classification_mapping' FROM symbol_revisions WHERE id=:id"),
            {"id": revision_id},
        ).scalar_one()

    assert report is not None, "the failure must be recorded, not swallowed"
    assert report["status"] == "failed"
    assert "deliberate WP1.0 mapper failure" in report["error"]
    assert not _classification_assignments(engine, revision_id)
