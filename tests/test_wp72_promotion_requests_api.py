"""Stage 7 WP7.2 regression: the `promotion-requests` HTTP route layer
(submission, listing, detail, withdrawal) on top of the dedicated
`PromotionRequest`/`PromotionRequestDecision` model.

Exercised through the real FastAPI app with an in-memory SQLite database,
following the pattern `tests/test_wp55_organization_symbols_api.py`
established -- the DB-level idempotency (unique partial index) and
immutability triggers are Postgres-only and are exercised separately by
`tests/test_wp72_promotion_requests_postgresql.py` against a real
disposable container, mirroring how WP5.4's split works
(`test_wp54_organization_symbol_review.py` for Postgres-only invariants,
`test_wp55_organization_symbols_api.py` for route/schema wiring).
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
    ProductUsageEvent,
    PromotionRequest,
    PromotionRequestDecision,
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
        ProductUsageEvent.__table__,
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
        PromotionRequest.__table__,
        PromotionRequestDecision.__table__,
    ):
        # Postgres-only CHECK constraints (btrim/char_length/~) and the
        # unique-partial-index idempotency guard can't be parsed/enforced
        # by SQLite; the real enforcement is proven against Postgres by
        # test_wp72_promotion_requests_postgresql.py. This test only needs
        # the table shape and the route/service wiring. Postgres-only
        # `'...'::jsonb` server defaults (SQLite has no cast syntax) are
        # likewise stripped -- every row this test writes sets its JSON
        # column explicitly, so no default is ever relied on. Other server
        # defaults (e.g. Organization.locale) are left alone since SQLite
        # can parse a plain string literal default fine.
        original_constraints = table.constraints
        original_defaults = {}
        # `postgresql_where` on a partial unique index is a Postgres-only
        # dialect kwarg -- SQLite silently ignores the WHERE clause and
        # creates a full (non-partial) unique index instead, which is
        # *stricter* than the real Postgres behavior this test isn't
        # trying to prove (test_wp72_promotion_requests_postgresql.py
        # proves the real partial-index semantics). Drop such indexes here
        # so a withdrawn/closed row doesn't spuriously block a fresh one.
        partial_indexes = [index for index in table.indexes if "postgresql_where" in index.kwargs]
        original_indexes = set(table.indexes)
        try:
            table.constraints = {
                item for item in original_constraints if not isinstance(item, CheckConstraint)
            }
            for index in partial_indexes:
                table.indexes.discard(index)
            for column in table.columns:
                if column.server_default is not None and "::jsonb" in str(column.server_default.arg):
                    original_defaults[column.name] = column.server_default
                    column.server_default = None
            table.create(engine)
        finally:
            table.constraints = original_constraints
            table.indexes.update(original_indexes)
            for column_name, default in original_defaults.items():
                table.columns[column_name].server_default = default


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
        organization_pilot_codes=("acme", "other"),
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


def _login(client, email="member@example.test"):
    return client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})


def _create_approved_symbol(client):
    """Create a draft, submit it for organization review, and approve it --
    the exact precondition `submit_promotion_request` requires (FR-PUB-001).
    Reuses the real HTTP pipeline WP5.3/WP5.4 already built and this file's
    sibling test already exercises."""
    create_response = client.post(
        "/api/v1/organization-symbols",
        json={"name": "Fire hydrant", "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."},
    )
    assert create_response.status_code == 200, create_response.text
    draft = create_response.json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]

    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={}
    )
    assert submit_response.status_code == 200, submit_response.text
    submission_id = submit_response.json()["id"]

    decision_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submission_id}/decision",
        json={"decision": "approved"},
    )
    assert decision_response.status_code == 200, decision_response.text
    return symbol_id


def test_organization_admin_can_submit_a_promotion_request_for_an_approved_symbol():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful across the industry.", "sharingAcknowledgment": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["governedSymbolId"] == symbol_id
    assert body["status"] == "submitted"
    assert body["sharingAcknowledgment"] is True
    assert body["closedAt"] is None


def test_submission_without_sharing_acknowledgment_is_rejected():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": False},
    )
    assert response.status_code == 400


def test_non_admin_member_cannot_submit_a_promotion_request():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="user", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": True},
    )
    assert response.status_code == 403


def test_submission_for_an_unapproved_revision_is_rejected():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)

    create_response = client.post(
        "/api/v1/organization-symbols",
        json={"name": "Draft-only", "category": "fire", "discipline": "civil", "summary": "Still a draft."},
    )
    symbol_id = create_response.json()["id"]

    response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Not ready.", "sharingAcknowledgment": True},
    )
    assert response.status_code == 400


def test_organization_admin_can_withdraw_a_pending_promotion_request():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": True},
    )
    request_id = submit_response.json()["id"]

    withdraw_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw",
        json={"note": "No longer needed."},
    )
    assert withdraw_response.status_code == 200, withdraw_response.text
    body = withdraw_response.json()
    assert body["status"] == "withdrawn"
    assert body["closedAt"] is not None


def test_withdrawing_an_already_withdrawn_request_conflicts():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": True},
    )
    request_id = submit_response.json()["id"]
    client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw", json={})

    second_withdraw = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw", json={}
    )
    assert second_withdraw.status_code == 409


def test_submitting_a_second_active_request_after_withdrawal_succeeds():
    """The DB-level unique-partial-index guard only blocks a second *open*
    request; withdrawing the first must restore eligibility (mirrors
    OrganizationSymbolReviewSubmission's own active/closed pattern -- the
    SQLite fixture in this file drops CHECK constraints, so this exercises
    the service-layer status check, not the Postgres unique index itself,
    which test_wp72_promotion_requests_postgresql.py proves separately)."""
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)

    first = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "First attempt.", "sharingAcknowledgment": True},
    )
    request_id = first.json()["id"]
    client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw", json={})

    second = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Second attempt.", "sharingAcknowledgment": True},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] != request_id


def test_list_and_get_scope_to_the_owning_organization():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)
    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": True},
    )
    request_id = submit_response.json()["id"]

    list_response = client.get(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [request_id]

    get_response = client.get(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == request_id


def test_cross_organization_admin_cannot_see_or_withdraw_the_request():
    client, Session, user_id = _build_client()
    _add_membership(Session, user_id, "acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client)
    symbol_id = _create_approved_symbol(client)
    submit_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Widely useful.", "sharingAcknowledgment": True},
    )
    request_id = submit_response.json()["id"]
    client.post("/api/v1/auth/logout")

    with Session() as session:
        other_user = upsert_user(
            session, email="other@example.test", display_name="Other", roles=[], pin="1234", must_change_pin=False
        )
        session.commit()
        other_user_id = other_user.id
    _add_membership(Session, other_user_id, "other", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(client, email="other@example.test")

    get_response = client.get(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}")
    assert get_response.status_code == 404

    withdraw_response = client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{request_id}/withdraw", json={}
    )
    assert withdraw_response.status_code == 404
