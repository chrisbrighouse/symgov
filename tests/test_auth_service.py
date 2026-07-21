from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.auth import (
    authenticate_user,
    create_user_session,
    current_user_from_token,
    upsert_user,
    user_roles,
)
from symgov_backend.models import SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.subscriptions import upgrade_to_plus


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__):
        table.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_upsert_user_creates_unique_user_with_additive_roles_and_default_pin():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(
            session,
            email="Chris.Brighouse@Hotmail.co.uk",
            display_name="Alfi",
            roles=["admin", "submitter", "reviewer"],
        )
        session.commit()

        assert user.email == "chris.brighouse@hotmail.co.uk"
        assert user.display_name == "Alfi"
        assert user.pin_hash.startswith("pbkdf2_sha256$")
        assert user.must_change_pin is True
        assert user.is_active is True
        assert user_roles(session, user.id) == ("admin", "reviewer", "submitter")


def test_upsert_user_does_not_attach_privileged_roles_to_free_user():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(
            session,
            email="integrator@symgov.local",
            display_name="Integrator",
            roles=["integrator"],
        )
        session.commit()

        assert user_roles(session, user.id) == ()


def test_authenticate_user_accepts_email_case_insensitively_and_rejects_wrong_pin():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="chris.brighouse@hotmail.co.uk", display_name="Alfi", roles=["admin"], pin="4590")
        session.commit()

        assert authenticate_user(session, email="CHRIS.BRIGHOUSE@HOTMAIL.CO.UK", pin="4590").id == user.id
        assert authenticate_user(session, email="chris.brighouse@hotmail.co.uk", pin="1234") is None


def test_authenticate_user_rejects_inactive_user():
    Session = session_factory()
    with Session() as session:
        upsert_user(session, email="chris.brighouse@hotmail.co.uk", display_name="Alfi", roles=["admin"], pin="4590")
        user = session.query(User).one()
        user.is_active = False
        session.commit()

        assert authenticate_user(session, email="chris.brighouse@hotmail.co.uk", pin="4590") is None


def test_create_user_session_returns_raw_token_once_and_me_lookup_uses_hash():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="chris.brighouse@hotmail.co.uk", display_name="Alfi", roles=["admin", "reviewer"], pin="4590")
        raw_token = create_user_session(session, user=user)
        session.commit()

        stored_session = session.query(UserSession).one()
        assert raw_token
        assert raw_token not in stored_session.token_hash

        current = current_user_from_token(session, raw_token)
        assert current is not None
        assert current.email == "chris.brighouse@hotmail.co.uk"
        assert current.display_name == "Alfi"
        assert current.roles == ("admin", "reviewer")


def test_current_user_from_token_ignores_expired_session():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="chris.brighouse@hotmail.co.uk", display_name="Alfi", roles=["admin"], pin="4590")
        raw_token = create_user_session(session, user=user, ttl_hours=-1)
        session.commit()

        assert current_user_from_token(session, raw_token, now=datetime.now(timezone.utc)) is None


def test_free_user_has_no_effective_privileged_roles_and_subscription_metadata():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="free@example.com", display_name="Free", roles=["reviewer"], pin="4590")
        raw_token = create_user_session(session, user=user)
        session.commit()

        current = current_user_from_token(session, raw_token)

        assert current is not None
        assert current.roles == ()
        assert current.subscription_tier == "free"
        assert current.subscription_expires_on is None
        assert user_roles(session, user.id) == ()


def test_active_plus_user_receives_roles_and_expired_plus_is_reconciled():
    Session = session_factory()
    with Session() as session:
        user = upsert_user(session, email="plus@example.com", display_name="Plus", roles=[], pin="4590")
        upgrade_to_plus(session, user, months=1, as_of=date(2026, 1, 31))
        session.add(UserRole(user_id=user.id, role="reviewer", created_at=datetime.now(timezone.utc)))
        raw_token = create_user_session(session, user=user)
        session.commit()

        active = current_user_from_token(session, raw_token, now=datetime(2026, 2, 27, tzinfo=timezone.utc))
        assert active is not None
        assert active.roles == ("reviewer",)
        assert active.subscription_tier == "plus"
        assert active.subscription_expires_on == date(2026, 2, 28)

        expired = current_user_from_token(session, raw_token, now=datetime(2026, 2, 28, tzinfo=timezone.utc))
        assert expired is not None
        assert expired.roles == ()
        assert expired.subscription_tier == "free"
        assert user_roles(session, user.id) == ()
