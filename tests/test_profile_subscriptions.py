from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import EmailOutbox, SubscriptionEvent, User, UserRole, UserSession, UserSubscription
from symgov_backend.settings import SymgovAPISettings, get_settings


def build_client(*, admin_email="chris.brighouse@hotmail.co.uk"):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (User.__table__, UserRole.__table__, UserSession.__table__, UserSubscription.__table__, SubscriptionEvent.__table__, EmailOutbox.__table__):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        upsert_user(session, email="customer@example.com", display_name="Customer", roles=[], pin="4590")
        upsert_user(session, email="chris.brighouse@hotmail.co.uk", display_name="Chris", roles=["admin"], pin="4590")
        session.commit()
    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: SymgovAPISettings(subscription_admin_email=admin_email)
    return TestClient(app), Session


def login(client, email="customer@example.com"):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "4590"})
    assert response.status_code == 200


def test_profile_requires_authentication_before_database_work():
    response = TestClient(create_app()).get("/api/v1/profile")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


def test_profile_returns_own_identity_subscription_and_server_plan():
    client, _ = build_client()
    login(client)

    response = client.get("/api/v1/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "customer@example.com"
    assert payload["user"]["displayName"] == "Customer"
    assert payload["user"]["roles"] == []
    assert payload["user"]["subscription"]["tier"] == "free"
    assert payload["user"]["subscription"]["expiresOn"] is None
    assert payload["plan"]["currency"] == "GBP"
    assert payload["plan"]["annualPricePence"] == 5000
    assert payload["plan"]["minimumYears"] == 1
    assert payload["plan"]["maximumYears"] == 5
    assert payload["plan"]["paymentRequired"] is False
    assert [option["years"] for option in payload["plan"]["upgradeOptions"]] == [1, 2, 3, 4, 5]
    assert [option["totalPricePence"] for option in payload["plan"]["upgradeOptions"]] == [5000, 10000, 15000, 20000, 25000]


def test_self_service_upgrade_is_immediate_uses_whole_years_and_grants_no_roles(monkeypatch):
    from symgov_backend import subscriptions

    monkeypatch.setattr(subscriptions, "today_utc", lambda: date(2028, 2, 29))
    client, Session = build_client()
    login(client)

    response = client.post("/api/v1/profile/subscription/upgrade", json={"years": 2, "confirmed": True})

    assert response.status_code == 200
    assert response.json()["user"]["subscription"] == {
        "tier": "plus",
        "startedOn": "2028-02-29",
        "expiresOn": "2030-02-28",
        "isActive": True,
        "isProtected": False,
    }
    assert response.json()["user"]["roles"] == []
    assert response.json()["notificationStatus"] == "queued"
    with Session() as session:
        event = session.query(SubscriptionEvent).filter(SubscriptionEvent.action == "upgraded").one()
        assert event.actor_id == event.user_id
        assert event.origin == "self_service"
        outbox = session.query(__import__("symgov_backend.models", fromlist=["EmailOutbox"]).EmailOutbox).all()
        assert sorted(item.to_email for item in outbox) == ["chris.brighouse@hotmail.co.uk", "customer@example.com"]


def test_upgrade_rejects_invalid_years_missing_confirmation_and_repeat():
    client, _ = build_client()
    login(client)

    for years in (0, 6, 1.5, "2years"):
        response = client.post("/api/v1/profile/subscription/upgrade", json={"years": years, "confirmed": True})
        assert response.status_code == 422

    assert client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": False}).status_code == 400
    assert client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": True}).status_code == 200
    repeated = client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": True})
    assert repeated.status_code == 409


def test_profile_mutations_require_json_and_reject_cross_origin_requests():
    client, _ = build_client()
    login(client)

    malformed = client.post(
        "/api/v1/profile/subscription/upgrade",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 400
    assert client.post("/api/v1/profile/subscription/upgrade", content="years=1").status_code == 415
    cross_origin = client.post(
        "/api/v1/profile/subscription/upgrade",
        json={"years": 1, "confirmed": True},
        headers={"Origin": "https://attacker.example"},
    )
    assert cross_origin.status_code == 403


def test_customer_and_admin_notifications_can_share_an_address():
    client, Session = build_client(admin_email="customer@example.com")
    login(client)

    response = client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": True})

    assert response.status_code == 200
    with Session() as session:
        rows = session.query(EmailOutbox).all()
        assert len(rows) == 2
        assert {row.recipient_kind for row in rows} == {"customer", "admin"}
        assert {row.to_email for row in rows} == {"customer@example.com"}


def test_immediate_downgrade_removes_roles_and_queues_notifications():
    client, Session = build_client()
    login(client)
    assert client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": True}).status_code == 200
    with Session() as session:
        customer = session.query(User).filter(User.email == "customer@example.com").one()
        session.add(UserRole(user_id=customer.id, role="submitter", created_at=customer.created_at))
        session.commit()

    response = client.post("/api/v1/profile/subscription/downgrade", json={"confirmed": True})

    assert response.status_code == 200
    assert response.json()["user"]["subscription"]["tier"] == "free"
    assert response.json()["user"]["roles"] == []
    assert response.json()["notificationStatus"] == "queued"
    with Session() as session:
        event = session.query(SubscriptionEvent).filter(SubscriptionEvent.action == "cancelled").one()
        assert event.origin == "self_service"
        EmailOutbox = __import__("symgov_backend.models", fromlist=["EmailOutbox"]).EmailOutbox
        assert session.query(EmailOutbox).count() == 4


def test_downgrade_requires_confirmation_active_plus_and_rejects_protected_owner():
    client, _ = build_client()
    login(client)
    assert client.post("/api/v1/profile/subscription/downgrade", json={"confirmed": False}).status_code == 400
    assert client.post("/api/v1/profile/subscription/downgrade", json={"confirmed": True}).status_code == 409

    client.cookies.clear()
    login(client, "chris.brighouse@hotmail.co.uk")
    response = client.post("/api/v1/profile/subscription/downgrade", json={"confirmed": True})
    assert response.status_code == 403
    assert "protected" in response.json()["detail"].lower()
    upgrade = client.post("/api/v1/profile/subscription/upgrade", json={"years": 1, "confirmed": True})
    assert upgrade.status_code == 409
