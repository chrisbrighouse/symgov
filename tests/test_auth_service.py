from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from symgov_backend.auth import (
    authenticate_user,
    create_user_session,
    current_user_from_token,
    upsert_user,
    user_roles,
)
from symgov_backend.models import User, UserRole, UserSession


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    for table in (User.__table__, UserRole.__table__, UserSession.__table__):
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
