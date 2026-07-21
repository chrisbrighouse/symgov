import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import authenticate_user
from symgov_backend.management import bootstrap_first_user
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.subscriptions import PROTECTED_OWNER_EMAIL


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def roles_for(session, user):
    return sorted(role for (role,) in session.query(UserRole.role).filter(UserRole.user_id == user.id).all())


def test_bootstrap_first_user_creates_protected_owner_with_perpetual_plus_admin():
    session = make_session()

    user = bootstrap_first_user(session, email=PROTECTED_OWNER_EMAIL, pin="4590")
    subscription = session.get(UserSubscription, user.id)

    assert user.display_name == "Chris Brighouse"
    assert user.email == PROTECTED_OWNER_EMAIL
    assert user.must_change_pin is True
    assert user.is_active is True
    assert roles_for(session, user) == ["admin"]
    assert subscription.tier == "plus"
    assert subscription.expires_on is None
    assert subscription.is_protected is True
    assert authenticate_user(session, email=PROTECTED_OWNER_EMAIL.upper(), pin="4590") is not None


def test_bootstrap_first_user_is_idempotent():
    session = make_session()

    first = bootstrap_first_user(session, email=PROTECTED_OWNER_EMAIL, pin="4590")
    second = bootstrap_first_user(session, email=PROTECTED_OWNER_EMAIL.upper(), pin="4590")

    assert second.id == first.id
    assert session.query(User).count() == 1
    assert roles_for(session, second) == ["admin"]


def test_bootstrap_rejects_a_different_default_admin_account():
    session = make_session()

    with pytest.raises(ValueError, match="protected owner"):
        bootstrap_first_user(session, email="alfi@example.com", pin="4590")
