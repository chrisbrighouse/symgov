"""Stage 9 WP9.1 regression: the `product_usage_events` schema, its frozen
`event_type` vocabulary and cross-column constraints, immutability, and the
90-day retention purge, against a real disposable PostgreSQL container.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.1/§4 Q1/Q5/Q7):
- Only the browse-facing core event-type subset exists in v1's
  `CheckConstraint` vocabulary; governance-lifecycle event types are
  deliberately not present yet (WP9.2's own job to add, additively).
- `format` is required exactly when `event_type = 'symbol_downloaded'`,
  `favourite_action` exactly when `event_type = 'favorite_changed'`, and
  `context_resolution_basis` exactly when `event_type` is one of
  `'context_resolved'`/`'set_selected'` -- never otherwise.
- Rows are immutable once inserted (an `UPDATE` trigger blocks it), but
  `DELETE` remains permitted -- unlike `LLMUsageEvent`'s own fully
  append-only trigger -- because the confirmed 90-day raw-row retention
  purge depends on being able to delete expired rows.
- `purge_expired_product_usage_events` deletes only rows older than the
  90-day window, leaving newer rows and any explicitly-passed `now`
  untouched.

This package adds no emission wiring and no endpoint -- rows are inserted
directly via the ORM in these tests, exactly as WP9.2/WP9.3 will later do
from real mutation/browse code paths.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.models import ProductUsageEvent  # noqa: E402
from symgov_backend.product_usage_retention import purge_expired_product_usage_events  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0040"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp91_database():
    with _database("symgov-wp91") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


@pytest.fixture()
def wp91_session(wp91_database):
    engine, _, _ = wp91_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _seed_user_and_organization(Session):
    """A bare `Organization` insert violates the active-admin-minimum
    trigger, so this reuses `_add_membership` (base_role='admin') exactly
    as the other Stage 7/8 Postgres test fixtures do."""
    user_id = _create_user_with_global_roles(Session, email=f"wp91-{uuid.uuid4().hex[:8]}@example.test", display_name=f"WP9.1 User {uuid.uuid4().hex[:8]}", roles=[])
    organization_id = _add_membership(Session, user_id, code=f"wp91{uuid.uuid4().hex[:6]}", base_role="admin")
    return user_id, organization_id


def test_valid_core_event_rows_insert_successfully(wp91_session):
    user_id, organization_id = _seed_user_and_organization(wp91_session)
    now = datetime.now(timezone.utc)

    rows = [
        ProductUsageEvent(event_type="personal_session_started", occurred_at=now, session_mode="personal", user_id=user_id),
        ProductUsageEvent(event_type="organization_selected", occurred_at=now, session_mode="organization", user_id=user_id, organization_id=organization_id),
        ProductUsageEvent(event_type="context_resolved", occurred_at=now, session_mode="organization", user_id=user_id, organization_id=organization_id, context_resolution_basis="project_default"),
        ProductUsageEvent(event_type="set_selected", occurred_at=now, session_mode="organization", user_id=user_id, organization_id=organization_id, context_resolution_basis="explicit"),
        ProductUsageEvent(event_type="symbol_previewed", occurred_at=now, session_mode="personal", user_id=user_id, symbol_source="public"),
        ProductUsageEvent(event_type="symbol_downloaded", occurred_at=now, session_mode="personal", user_id=user_id, symbol_source="public", format="dxf"),
        ProductUsageEvent(event_type="favorite_changed", occurred_at=now, session_mode="personal", user_id=user_id, favourite_action="added"),
    ]
    with wp91_session() as session:
        session.add_all(rows)
        session.commit()

    with wp91_session() as session:
        count = session.query(ProductUsageEvent).filter(ProductUsageEvent.user_id == user_id).count()
    assert count == 7


def test_unknown_event_type_is_rejected(wp91_session):
    user_id, _ = _seed_user_and_organization(wp91_session)
    with wp91_session() as session:
        session.add(ProductUsageEvent(event_type="bogus_event", occurred_at=datetime.now(timezone.utc), session_mode="personal", user_id=user_id))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(event_type="symbol_previewed", session_mode="personal", format="dxf"),  # format on a non-download event
        dict(event_type="symbol_downloaded", session_mode="personal"),  # missing format on a download event
        dict(event_type="symbol_previewed", session_mode="personal", favourite_action="added"),  # favourite_action off-event
        dict(event_type="favorite_changed", session_mode="personal"),  # missing favourite_action
        dict(event_type="symbol_previewed", session_mode="personal", context_resolution_basis="explicit"),  # context basis off-event
        dict(event_type="context_resolved", session_mode="organization"),  # missing context basis
        dict(event_type="personal_session_started", session_mode="not_a_real_mode"),  # bad session_mode
        dict(event_type="symbol_previewed", session_mode="personal", symbol_source="not_a_real_source"),  # bad symbol_source
        dict(event_type="organization_selected", session_mode="organization"),  # organization mode missing organization_id
    ],
)
def test_cross_column_and_enum_constraints_reject_invalid_combinations(wp91_session, kwargs):
    user_id, _ = _seed_user_and_organization(wp91_session)
    with wp91_session() as session:
        session.add(ProductUsageEvent(occurred_at=datetime.now(timezone.utc), user_id=user_id, **kwargs))
        with pytest.raises(IntegrityError):
            session.commit()


def test_personal_mode_with_organization_id_is_rejected(wp91_session):
    user_id, organization_id = _seed_user_and_organization(wp91_session)
    with wp91_session() as session:
        session.add(
            ProductUsageEvent(
                event_type="personal_session_started",
                occurred_at=datetime.now(timezone.utc),
                session_mode="personal",
                user_id=user_id,
                organization_id=organization_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_rows_are_immutable_once_inserted(wp91_session):
    user_id, _ = _seed_user_and_organization(wp91_session)
    row_id = uuid.uuid4()
    with wp91_session() as session:
        session.add(ProductUsageEvent(id=row_id, event_type="personal_session_started", occurred_at=datetime.now(timezone.utc), session_mode="personal", user_id=user_id))
        session.commit()

    with wp91_session() as session:
        with pytest.raises(ProgrammingError) as excinfo:
            session.execute(text("UPDATE product_usage_events SET session_mode = 'organization' WHERE id = :id"), {"id": row_id})
            session.commit()
        assert "immutable" in str(excinfo.value).lower()


def test_purge_deletes_only_rows_older_than_the_retention_window(wp91_session):
    user_id, _ = _seed_user_and_organization(wp91_session)
    reference_now = datetime.now(timezone.utc)
    old_row_id = uuid.uuid4()
    fresh_row_id = uuid.uuid4()

    with wp91_session() as session:
        session.add_all(
            [
                ProductUsageEvent(
                    id=old_row_id,
                    event_type="personal_session_started",
                    occurred_at=reference_now - timedelta(days=100),
                    session_mode="personal",
                    user_id=user_id,
                ),
                ProductUsageEvent(
                    id=fresh_row_id,
                    event_type="personal_session_started",
                    occurred_at=reference_now - timedelta(days=1),
                    session_mode="personal",
                    user_id=user_id,
                ),
            ]
        )
        session.commit()

    with wp91_session() as session:
        deleted_count = purge_expired_product_usage_events(session, now=reference_now)
        session.commit()
    assert deleted_count == 1

    with wp91_session() as session:
        remaining_ids = {row.id for row in session.query(ProductUsageEvent).filter(ProductUsageEvent.user_id == user_id).all()}
    assert remaining_ids == {fresh_row_id}
