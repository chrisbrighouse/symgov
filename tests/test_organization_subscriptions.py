from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.models import (  # noqa: E402
    Organization,
    OrganizationMembership,
    OrganizationSubscription,
    OrganizationSubscriptionEvent,
)
from symgov_backend.organization_service import (  # noqa: E402
    add_organization_member,
    create_organization_with_initial_admin,
)
from symgov_backend.organization_subscriptions import (  # noqa: E402
    DEFAULT_SEAT_LIMIT,
    DEFAULT_TERM_MONTHS,
    assert_seat_available,
    seats_in_use,
    set_organization_subscription,
)
from test_organization_service import _seed_platform_admin_actor, _seed_user, _session_factory  # noqa: E402,F401

TODAY = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    with patch("symgov_backend.organization_service._emit_audit"):
        yield


@pytest.fixture
def Session():
    return _session_factory()


def _org(session, code="ACME") -> Organization:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    org = Organization(
        id=uuid.uuid4(), code=code, normalized_code=code.lower(), display_name=code, name_key=code.lower(),
        is_active=True, is_protected=False, fallback_icon_svg="<svg/>", created_at=now, updated_at=now,
    )
    session.add(org)
    session.flush()
    return org


def _add(session, org, actor, email):
    user = _seed_user(session, email=email)
    return add_organization_member(
        session, org.id, user_id=user.id, base_role="user", actor_user_id=actor.id, _bypass_admin_check=True,
    )


def test_unmetered_organization_has_no_seat_limit(Session):
    with Session() as session:
        actor = _seed_user(session, email="actor@example.test")
        org = _org(session)
        for index in range(5):
            _add(session, org, actor, f"u{index}@example.test")
        assert seats_in_use(session, org.id) == 5





@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"seat_limit": 0, "months": 12}, "Seat limit"),
        ({"seat_limit": True, "months": 12}, "Seat limit"),
        ({"seat_limit": 5}, "exactly one"),
        ({"seat_limit": 5, "months": 12, "expires_on": date(2027, 1, 1)}, "exactly one"),
        ({"seat_limit": 5, "months": 0}, "at least one month"),
        ({"seat_limit": 5, "expires_on": TODAY}, "in the future"),
    ],
)
def test_set_subscription_validates_input(Session, kwargs, message):
    with Session() as session:
        org = _org(session)
        with pytest.raises(ValueError, match=message):
            set_organization_subscription(session, org.id, as_of=TODAY, **kwargs)


def test_set_subscription_records_events_and_versions(Session):
    with Session() as session:
        actor = _seed_user(session, email="actor@example.test")
        org = _org(session)
        created = set_organization_subscription(
            session, org.id, seat_limit=10, months=12, actor_id=actor.id, reason="sale", as_of=TODAY,
        )
        assert (created.seat_limit, created.version, created.expires_on) == (10, 1, date(2027, 10, 4))
        updated = set_organization_subscription(
            session, org.id, seat_limit=20, expires_on=date(2028, 10, 4), actor_id=actor.id, as_of=TODAY,
        )
        assert (updated.seat_limit, updated.version, updated.started_on) == (20, 2, TODAY)
        events = session.query(OrganizationSubscriptionEvent).order_by(OrganizationSubscriptionEvent.created_at).all()
        assert sorted(e.action for e in events) == ["created", "updated"]
        update_event = next(e for e in events if e.action == "updated")
        assert (update_event.previous_seat_limit, update_event.new_seat_limit) == (10, 20)
        assert update_event.previous_expires_on == date(2027, 10, 4)


def test_protected_organization_cannot_be_metered(Session):
    with Session() as session:
        _seed_platform_admin_actor(session)
        org = session.query(Organization).filter(Organization.is_protected.is_(True)).one()
        with pytest.raises(ValueError, match="protected"):
            set_organization_subscription(session, org.id, seat_limit=5, months=12, as_of=TODAY)


def test_membership_changes_do_not_consult_the_seat_limit_yet(Session):
    with Session() as session:
        actor = _seed_user(session, email="actor@example.test")
        org = _org(session)
        set_organization_subscription(session, org.id, seat_limit=1, months=12, as_of=TODAY)
        for index in range(3):
            _add(session, org, actor, f"u{index}@example.test")
        assert seats_in_use(session, org.id) == 3
        # A limit below current usage is recorded, not refused.
        assert set_organization_subscription(session, org.id, seat_limit=2, months=12, as_of=TODAY).seat_limit == 2


def test_seat_helper_counts_active_and_invited_and_flags_expiry(Session):
    with Session() as session:
        actor = _seed_user(session, email="actor@example.test")
        org = _org(session)
        _add(session, org, actor, "a@example.test")
        invited_user = _seed_user(session, email="invited@example.test")
        now = datetime.now(timezone.utc)
        session.add(OrganizationMembership(
            id=uuid.uuid4(), organization_id=org.id, user_id=invited_user.id, status="invited",
            invited_at=now, created_at=now, updated_at=now,
        ))
        session.flush()
        assert seats_in_use(session, org.id) == 2
        set_organization_subscription(session, org.id, seat_limit=2, months=1, as_of=date(2026, 1, 4))
        with pytest.raises(ValueError, match="expired"):
            assert_seat_available(session, org.id, as_of=TODAY)
        set_organization_subscription(session, org.id, seat_limit=2, months=12, as_of=TODAY)
        with pytest.raises(ValueError, match="No seats available: 2 of 2"):
            assert_seat_available(session, org.id, as_of=TODAY)


def test_new_organization_starts_on_the_default_plan(Session):
    with Session() as session:
        platform = _seed_platform_admin_actor(session)
        admin = _seed_user(session, email="admin@example.test")
        result = create_organization_with_initial_admin(
            session, code="NEWCO", display_name="NewCo", initial_admin_user_id=admin.id, actor_user_id=platform.id,
        )
        subscription = session.get(OrganizationSubscription, result.organization.id)
        assert subscription.seat_limit == DEFAULT_SEAT_LIMIT == 25
        assert DEFAULT_TERM_MONTHS == 12
        assert subscription.expires_on > subscription.started_on
        event = session.query(OrganizationSubscriptionEvent).one()
        assert (event.action, event.actor_id) == ("created", platform.id)
