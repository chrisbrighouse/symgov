from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import hash_pin, user_roles, verify_pin
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSubscription
from symgov_backend.publication_handoff import ensure_publication_service_user
from symgov_backend.runtime import RuntimePersistenceBridge
from symgov_backend.services.published_feedback import get_or_create_ed_user


FIXED_NOW = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _published_feedback_user(session):
    return get_or_create_ed_user(session, now=FIXED_NOW)


def _publication_handoff_user(session):
    return ensure_publication_service_user(session)


def _runtime_publication_user(session):
    return RuntimePersistenceBridge.ensure_publication_service_user(object(), session)


@pytest.mark.parametrize(
    "ensure_user",
    [_published_feedback_user, _publication_handoff_user, _runtime_publication_user],
)
def test_automatic_service_users_are_noninteractive_free_accounts_and_repair_legacy_state(ensure_user):
    Session = session_factory()
    with Session() as session:
        user = ensure_user(session)
        subscription = session.get(UserSubscription, user.id)

        assert user.is_active is False
        assert verify_pin("4590", user.pin_hash) is False
        assert user_roles(session, user.id) == ()
        assert subscription is not None
        assert subscription.tier == "free"
        assert subscription.expires_on is None

        session.query(SubscriptionEvent).filter(SubscriptionEvent.user_id == user.id).delete(synchronize_session=False)
        session.query(UserSubscription).filter(UserSubscription.user_id == user.id).delete(synchronize_session=False)
        user.is_active = True
        user.pin_hash = hash_pin("4590")
        session.add(UserRole(user_id=user.id, role="admin", created_at=FIXED_NOW))
        session.flush()

        repaired = ensure_user(session)
        repaired_subscription = session.get(UserSubscription, repaired.id)

        assert repaired.id == user.id
        assert repaired.is_active is False
        assert verify_pin("4590", repaired.pin_hash) is False
        assert user_roles(session, repaired.id) == ()
        assert repaired_subscription is not None
        assert repaired_subscription.tier == "free"
        assert repaired_subscription.expires_on is None
