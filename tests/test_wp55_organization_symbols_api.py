"""WP5.5 regression: the `organization-symbols` HTTP route layer's read
model carries enough information for a frontend review queue to work.

`GET /organization-symbols` (list) and `GET /organization-symbols/{id}`
(detail) must expose, on `currentRevision`, whether a revision has an
active organization review submission — and if so, its submission id,
rationale, and submitted-at timestamp — because there is no separate
"list review submissions" endpoint; the frontend review queue is built by
filtering the drafts list for a populated `pendingSubmissionId`. Before
this change, `_draft_response`/`_revision_response` never carried the
submission at all, and the list endpoint never even loaded the current
revision, so the review queue could not have been built without this.

Exercised through the real FastAPI app (not by calling the service-layer
functions directly, which `tests/test_wp53_organization_symbol_drafts.py`
and `tests/test_wp54_organization_symbol_review.py` already cover against
a disposable Postgres container) so the route/schema wiring itself is
proven, using an in-memory SQLite database per the pattern in
`tests/test_organization_auth_context.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import (
    AuthLoginAttemptEvent,
    AuthLoginThrottleBucket,
    AuthOrganizationSelectionChallenge,
    AuthThrottleRecoveryEvent,
    GovernedSymbol,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    OrganizationSymbolReviewDecision,
    OrganizationSymbolReviewSubmission,
    PlatformRoleAssignment,
    SubscriptionEvent,
    SymbolRevision,
    User,
    UserRole,
    UserSession,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(element, compiler, **kw):
    # SymbolRevision.payload_json is a Postgres JSONB column; SQLite has
    # no JSONB type, so map it to the generic JSON type for this
    # in-memory route-shape test (the real JSONB behavior is exercised
    # against Postgres by test_wp53_organization_symbol_drafts.py).
    return "JSON"


def _create_tables(engine) -> None:
    for table in (
        User.__table__,
        UserRole.__table__,
        Organization.__table__,
        OrganizationMembership.__table__,
        OrganizationRoleAssignment.__table__,
        OrganizationMemberCapability.__table__,
        PlatformRoleAssignment.__table__,
        UserSession.__table__,
        AuthOrganizationSelectionChallenge.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
        GovernedSymbol.__table__,
        SymbolRevision.__table__,
        OrganizationSymbolReviewSubmission.__table__,
        OrganizationSymbolReviewDecision.__table__,
    ):
        # Several WP5.1 check constraints use Postgres-only functions
        # (btrim, char_length) or operators (~); SQLite can't parse them,
        # and the real enforcement is already proven against Postgres by
        # WP5.3/WP5.4. This test only needs the table shape.
        original_constraints = table.constraints
        try:
            table.constraints = {
                item for item in original_constraints if not isinstance(item, CheckConstraint)
            }
            table.create(engine)
        finally:
            table.constraints = original_constraints


def _build_client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        user = upsert_user(
            session, email="member@example.test", display_name="Member", roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
        user_id = user.id

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        organization_pilot_codes=("acme",),
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), Session, user_id


def _add_membership(Session, user_id, code, *, base_role="user", capabilities=()):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = Organization(
            id=uuid.uuid4(),
            code=code.upper(),
            normalized_code=code.lower(),
            display_name=f"{code.upper()} Organization",
            name_key=f"{code}-organization",
            entitlement_status="active",
            is_active=True,
            is_protected=False,
            fallback_icon_svg="<svg/>",
            created_at=now,
            updated_at=now,
        )
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=organization.id,
            user_id=user_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([organization, membership])
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


def _login(client):
    return client.post("/api/v1/auth/login", json={"email": "member@example.test", "pin": "1234"})


def test_list_and_detail_expose_pending_submission_only_once_submitted():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", capabilities=("contributor", "symbol_reviewer"))
    login = _login(client)
    assert login.status_code == 200
    assert login.json()["user"]["session"]["mode"] == "organization"

    create_response = client.post(
        "/api/v1/organization-symbols",
        json={
            "name": "Fire hydrant",
            "category": "fire",
            "discipline": "civil",
            "summary": "A fire hydrant symbol.",
        },
    )
    assert create_response.status_code == 200
    draft = create_response.json()
    symbol_id = draft["id"]
    revision_id = draft["currentRevisionId"]
    assert draft["currentRevision"]["pendingSubmissionId"] is None

    list_before = client.get("/api/v1/organization-symbols")
    assert list_before.status_code == 200
    item_before = next(item for item in list_before.json()["items"] if item["id"] == symbol_id)
    assert item_before["currentRevision"] is not None
    assert item_before["currentRevision"]["pendingSubmissionId"] is None

    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit",
        json={"rationale": "Ready for review."},
    )
    assert submit_response.status_code == 200
    submission_id = submit_response.json()["id"]

    detail_after = client.get(f"/api/v1/organization-symbols/{symbol_id}")
    assert detail_after.status_code == 200
    revision_after = detail_after.json()["currentRevision"]
    assert revision_after["pendingSubmissionId"] == submission_id
    assert revision_after["pendingSubmissionRationale"] == "Ready for review."
    assert revision_after["pendingSubmissionSubmittedAt"]

    list_after = client.get("/api/v1/organization-symbols")
    assert list_after.status_code == 200
    item_after = next(item for item in list_after.json()["items"] if item["id"] == symbol_id)
    assert item_after["currentRevision"]["pendingSubmissionId"] == submission_id


def test_decision_closes_submission_and_clears_pending_submission_from_the_read_model():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", capabilities=("contributor", "symbol_reviewer"))
    _login(client)

    create_response = client.post(
        "/api/v1/organization-symbols",
        json={"name": "Valve", "category": "process", "discipline": "mechanical", "summary": "A valve symbol."},
    )
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]
    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={}
    )
    submission_id = submit_response.json()["id"]

    decision_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submission_id}/decision",
        json={"decision": "approved"},
    )
    assert decision_response.status_code == 200

    detail_after = client.get(f"/api/v1/organization-symbols/{symbol_id}")
    revision_after = detail_after.json()["currentRevision"]
    assert revision_after["lifecycleState"] == "approved"
    assert revision_after["pendingSubmissionId"] is None
