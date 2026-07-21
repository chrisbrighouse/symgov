from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import upsert_user, user_roles
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.subscriptions import (
    PROTECTED_OWNER_EMAIL,
    add_calendar_months,
    adjust_plus_months,
    cancel_plus,
    ensure_subscription,
    reconcile_subscription,
    upgrade_to_plus,
)


def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_calendar_months_use_standard_duration_and_clamp_month_end():
    assert add_calendar_months(date(2026, 1, 3), 3) == date(2026, 4, 3)
    assert add_calendar_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_calendar_months(date(2026, 2, 28), 1, anchor_day=31) == date(2026, 3, 31)
    assert add_calendar_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_new_user_defaults_to_non_expiring_free_subscription(monkeypatch):
    monkeypatch.setattr("symgov_backend.subscriptions.today_utc", lambda: date(2026, 7, 20))
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="free@example.com", display_name="Free User", roles=[])
        subscription = ensure_subscription(session, user, as_of=date(2026, 7, 20))

        assert subscription.tier == "free"
        assert subscription.started_on == date(2026, 7, 20)
        assert subscription.expires_on is None
        assert subscription.is_protected is False


def test_upgrade_adjust_cancel_and_expiry_remove_roles_permanently():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="plus@example.com", display_name="Plus User", roles=[])
        subscription = upgrade_to_plus(session, user, months=3, as_of=date(2026, 1, 3))
        assert subscription.tier == "plus"
        assert subscription.started_on == date(2026, 1, 3)
        assert subscription.expires_on == date(2026, 4, 3)
        assert subscription.anchor_day == 3
        assert user_roles(session, user.id) == ()

        with pytest.raises(ValueError, match="already has an active Plus"):
            upgrade_to_plus(session, user, months=1, as_of=date(2026, 2, 3))

        session.add(UserRole(user_id=user.id, role="reviewer", created_at=user.created_at))
        session.flush()
        subscription = adjust_plus_months(session, user, months=-1, as_of=date(2026, 2, 1))
        assert subscription.expires_on == date(2026, 3, 3)

        with pytest.raises(ValueError, match="current month"):
            adjust_plus_months(session, user, months=-2, as_of=date(2026, 2, 1))

        reconcile_subscription(session, user, as_of=date(2026, 3, 3))
        assert subscription.tier == "free"
        assert subscription.expires_on is None
        assert user_roles(session, user.id) == ()
        assert [event.action for event in session.query(SubscriptionEvent).order_by(SubscriptionEvent.created_at).all()] == [
            "created",
            "upgraded",
            "adjusted",
            "expired",
        ]

        upgrade_to_plus(session, user, months=1, as_of=date(2026, 5, 10))
        session.add(UserRole(user_id=user.id, role="submitter", created_at=user.created_at))
        session.flush()
        cancel_plus(session, user, as_of=date(2026, 5, 11))
        assert subscription.tier == "free"
        assert user_roles(session, user.id) == ()


def test_protected_owner_is_perpetual_plus_admin_and_cannot_be_cancelled():
    Session = session_factory()
    with Session() as session:
        owner = upsert_user(session, email=PROTECTED_OWNER_EMAIL, display_name="Chris", roles=[])
        subscription = ensure_subscription(session, owner, as_of=date(2026, 7, 20))

        assert subscription.tier == "plus"
        assert subscription.expires_on is None
        assert subscription.is_protected is True
        assert user_roles(session, owner.id) == ("admin",)

        with pytest.raises(ValueError, match="protected owner"):
            cancel_plus(session, owner, as_of=date(2026, 7, 20))
