from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.auth import authenticate_user
from symgov_backend.management import bootstrap_first_user
from symgov_backend.models import User, UserRole, UserSession


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, UserRole.__table__, UserSession.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def roles_for(session, user):
    return sorted(role for (role,) in session.query(UserRole.role).filter(UserRole.user_id == user.id).all())


def test_bootstrap_first_user_creates_alfi_with_all_initial_roles_and_forced_pin_change():
    session = make_session()

    user = bootstrap_first_user(session, email="alfi@example.com", pin="4590")

    assert user.display_name == "Alfi"
    assert user.email == "alfi@example.com"
    assert user.must_change_pin is True
    assert user.is_active is True
    assert roles_for(session, user) == ["admin", "reviewer", "submitter"]
    assert authenticate_user(session, email="ALFI@example.com", pin="4590") is not None


def test_bootstrap_first_user_is_idempotent_and_updates_roles():
    session = make_session()

    first = bootstrap_first_user(session, email="alfi@example.com", pin="4590")
    second = bootstrap_first_user(session, email="ALFI@example.com", pin="4590")

    assert second.id == first.id
    assert session.query(User).count() == 1
    assert roles_for(session, second) == ["admin", "reviewer", "submitter"]
