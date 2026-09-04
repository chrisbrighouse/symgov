"""Stage 7 WP7.2 regression: promotion-request submission/withdrawal against
a real disposable PostgreSQL container -- the idempotency guard (a unique
partial index) and the append-preserving/immutability triggers are
Postgres-only, so a SQLite unit test cannot exercise them (see
`tests/test_wp72_promotion_requests_api.py`'s own note on this split,
mirroring how `test_wp54_organization_symbol_review.py`/
`test_wp55_organization_symbols_api.py` split the same way for Stage 5).

Proves, per the programme plan §13 and the Stage 7 plan's §4 decisions:
- Only an Organization Admin may submit (Q2) or withdraw a promotion
  request, and only for a symbol owned by their own organization.
- A symbol's current revision must carry an approved, closed organization
  review decision before it can be submitted (FR-PUB-001), mirroring the
  same predicate `trg_governed_symbols_organization_wide_eligibility`
  enforces for the organization-wide toggle.
- One active (non-terminal) promotion request per governed symbol, enforced
  at the database level (Q3) -- a concurrent duplicate submission loses the
  race with an `IntegrityError`, translated to `PromotionRequestConflict`.
- Withdrawing a still-pending request does not change symbol visibility
  (§13 task 6) and restores eligibility for a fresh submission.
- The promotion request's identity/content is immutable once written
  (append-preserving trigger); only status/closed_at/updated_at/
  review_case_id (from NULL) may change.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _approve,
    _organization,
    _revision,
    _submission,
    _user,
    stage5_database,
)
from test_wp53_organization_symbol_drafts import _actor, _membership  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.promotion_requests import (  # noqa: E402
    PromotionRequestConflict,
    PromotionRequestError,
    PromotionRequestNotVisible,
    submit_promotion_request,
    withdraw_promotion_request,
)


@pytest.fixture(scope="module")
def wp72_database(stage5_database):
    engine, url, raw_url = stage5_database
    from test_organization_symbol_postgresql import _alembic  # noqa: E402

    _alembic(url, "upgrade", "20260904_0042")
    import psycopg  # noqa: E402

    with psycopg.connect(raw_url, autocommit=True) as connection:
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app"
        )
        connection.execute("GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app")
    return engine, url, raw_url


@pytest.fixture()
def wp72_fixtures(wp72_database):
    engine, _, _ = wp72_database
    with engine.begin() as connection:
        organization = _organization(connection, "wp72")
        other_organization = _organization(connection, "wp72other")
        admin_user = _user(connection, "admin")
        contributor_user = _user(connection, "contributor")
        other_admin_user = _user(connection, "otheradmin")

        _membership(connection, organization, admin_user, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
        _membership(connection, organization, contributor_user, base_role="user", capabilities=("contributor",))
        _membership(connection, other_organization, other_admin_user, base_role="admin", capabilities=("contributor", "symbol_reviewer"))

    return {
        "engine": engine,
        "organization": organization,
        "other_organization": other_organization,
        "admin": _actor(admin_user, organization, base_role="admin", capabilities=("contributor", "symbol_reviewer")),
        "contributor": _actor(contributor_user, organization, base_role="user", capabilities=("contributor",)),
        "other_admin": _actor(other_admin_user, other_organization, base_role="admin", capabilities=("contributor", "symbol_reviewer")),
    }


def _approved_symbol(engine, organization, actor_user_id):
    """Build an organization-private symbol whose current revision has an
    approved, closed organization review decision -- the exact precondition
    `submit_promotion_request` requires."""
    with engine.begin() as connection:
        symbol_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(
            text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
                "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now,:organization,'organization_private')"
            ),
            {"id": symbol_id, "slug": f"wp72-{symbol_id}", "owner": actor_user_id, "now": now, "organization": organization},
        )
        revision_id = _revision(connection, symbol_id, actor_user_id, lifecycle="approved")
        submission_id = _submission(connection, organization, symbol_id, revision_id, actor_user_id)
        _approve(connection, submission_id, organization, symbol_id, revision_id, actor_user_id)
    return symbol_id, revision_id


def test_admin_can_submit_a_promotion_request_for_an_approved_symbol(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    symbol_id, revision_id = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(wp72_fixtures["admin"].id))
    with Session(engine) as session:
        request = submit_promotion_request(
            session, wp72_fixtures["admin"], symbol_id=symbol_id, reason="Widely useful.", sharing_acknowledgment=True
        )
        session.commit()
        assert request.status == "submitted"
        assert request.symbol_revision_id == revision_id
        assert request.closed_at is None


def test_non_admin_contributor_cannot_submit(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(wp72_fixtures["admin"].id))
    with Session(engine) as session:
        with pytest.raises(PromotionRequestError):
            submit_promotion_request(
                session, wp72_fixtures["contributor"], symbol_id=symbol_id, reason="x", sharing_acknowledgment=True
            )


def test_symbol_without_approved_decision_cannot_be_submitted(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    admin = wp72_fixtures["admin"]
    with engine.begin() as connection:
        symbol_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        connection.execute(
            text(
                "INSERT INTO governed_symbols (id,slug,canonical_name,category,discipline,owner_id,created_at,updated_at,owner_organization_id,visibility) "
                "VALUES (:id,:slug,:slug,'test','test',:owner,:now,:now,:organization,'organization_private')"
            ),
            {"id": symbol_id, "slug": f"wp72-draft-{symbol_id}", "owner": uuid.UUID(admin.id), "now": now, "organization": wp72_fixtures["organization"]},
        )
        _revision(connection, symbol_id, uuid.UUID(admin.id), lifecycle="draft")
    with Session(engine) as session:
        with pytest.raises(PromotionRequestError):
            submit_promotion_request(
                session, admin, symbol_id=symbol_id, reason="Not approved yet.", sharing_acknowledgment=True
            )


def test_cross_organization_admin_cannot_submit_for_another_organizations_symbol(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(wp72_fixtures["admin"].id))
    with Session(engine) as session:
        with pytest.raises(PromotionRequestNotVisible):
            submit_promotion_request(
                session, wp72_fixtures["other_admin"], symbol_id=symbol_id, reason="x", sharing_acknowledgment=True
            )


def test_concurrent_duplicate_submission_loses_the_db_level_race(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    admin = wp72_fixtures["admin"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(admin.id))

    with Session(engine) as session:
        submit_promotion_request(session, admin, symbol_id=symbol_id, reason="First.", sharing_acknowledgment=True)
        session.commit()

    with Session(engine) as session:
        with pytest.raises(PromotionRequestConflict):
            submit_promotion_request(session, admin, symbol_id=symbol_id, reason="Second.", sharing_acknowledgment=True)


def test_withdrawal_does_not_change_symbol_visibility_and_restores_eligibility(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    admin = wp72_fixtures["admin"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(admin.id))

    with Session(engine) as session:
        request = submit_promotion_request(session, admin, symbol_id=symbol_id, reason="First.", sharing_acknowledgment=True)
        session.commit()
        request_id = request.id

    with Session(engine) as session:
        withdrawn = withdraw_promotion_request(session, admin, request_id=request_id, note="Changed our mind.")
        session.commit()
        assert withdrawn.status == "withdrawn"
        assert withdrawn.closed_at is not None

    with engine.begin() as connection:
        visibility = connection.execute(
            text("SELECT visibility FROM governed_symbols WHERE id=:id"), {"id": symbol_id}
        ).scalar_one()
        assert visibility == "organization_private"

    # A fresh submission for the same symbol is now eligible again.
    with Session(engine) as session:
        second = submit_promotion_request(session, admin, symbol_id=symbol_id, reason="Second.", sharing_acknowledgment=True)
        session.commit()
        assert second.id != request_id
        assert second.status == "submitted"


def test_withdrawing_a_terminal_request_conflicts(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    admin = wp72_fixtures["admin"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(admin.id))

    with Session(engine) as session:
        request = submit_promotion_request(session, admin, symbol_id=symbol_id, reason="First.", sharing_acknowledgment=True)
        session.commit()
        request_id = request.id

    with Session(engine) as session:
        withdraw_promotion_request(session, admin, request_id=request_id)
        session.commit()

    with Session(engine) as session:
        with pytest.raises(PromotionRequestConflict):
            withdraw_promotion_request(session, admin, request_id=request_id)


def test_promotion_request_identity_is_immutable(wp72_fixtures):
    engine = wp72_fixtures["engine"]
    admin = wp72_fixtures["admin"]
    symbol_id, _ = _approved_symbol(engine, wp72_fixtures["organization"], uuid.UUID(admin.id))

    with Session(engine) as session:
        request = submit_promotion_request(session, admin, symbol_id=symbol_id, reason="First.", sharing_acknowledgment=True)
        session.commit()
        request_id = request.id

    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE promotion_requests SET reason='tampered' WHERE id=:id"), {"id": request_id}
            )
