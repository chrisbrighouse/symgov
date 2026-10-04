from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.models import OrganizationMembership, OrganizationSubscription  # noqa: E402
from symgov_backend.organization_subscriptions import set_organization_subscription  # noqa: E402
from symgov_backend.subscriptions import add_calendar_months, today_utc  # noqa: E402
from test_platform_organizations_api import (  # noqa: E402
    _build_client,
    _login_and_select_org,
    _login_and_step_up,
    _seed_commercial_org,
    _seed_symgov_org_with_platform_admin,
)

ADMIN = "platform-admin@example.test"
REASON = "Annual renewal agreed with customer."


def _setup(*, step_up=True, plan=True, seat_limit=25, months=12):
    client, Session, admin_id, _, plain_id = _build_client()
    symgov_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    if plan:
        with Session() as session:
            set_organization_subscription(session, acme_id, seat_limit=seat_limit, months=months)
            session.commit()
    (_login_and_step_up if step_up else _login_and_select_org)(client, ADMIN, symgov_id)
    return client, Session, symgov_id, acme_id


def _url(org_id, suffix=""):
    return f"/api/v1/platform/organizations/{org_id}/subscription{suffix}"


def test_requires_authentication():
    client, *_ = _build_client()
    assert client.get(_url("00000000-0000-0000-0000-000000000000")).status_code == 401


def test_non_platform_admin_is_refused():
    client, Session, admin_id, _, plain_id = _build_client()
    symgov_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    client.post("/api/v1/auth/login", json={"email": "plain@example.test", "pin": "1234"})
    assert client.get(_url(acme_id)).status_code in (401, 403, 404)


def test_get_reports_plan_seats_and_status():
    client, _, _, acme_id = _setup()
    body = client.get(_url(acme_id)).json()
    assert (body["metered"], body["seatLimit"], body["seatsInUse"], body["status"]) == (True, 25, 0, "active")
    assert body["expiresOn"] == add_calendar_months(today_utc(), 12).isoformat()
    assert [e["action"] for e in body["events"]] == ["created"]


def test_get_for_an_organization_without_a_plan_is_unmetered():
    client, _, symgov_id, _ = _setup()
    body = client.get(_url(symgov_id)).json()
    assert (body["metered"], body["status"], body["seatLimit"]) == (False, "unmetered", None)


def test_unknown_organization_is_404():
    client, *_ = _setup()
    assert client.get(_url("00000000-0000-0000-0000-000000000000")).status_code == 404
    assert client.get(_url("not-a-uuid")).status_code == 404


def test_set_requires_step_up():
    client, _, _, acme_id = _setup(step_up=False)
    response = client.put(_url(acme_id), json={"seatLimit": 30, "months": 12, "reason": REASON})
    assert response.status_code == 403


def test_set_changes_seats_and_term_and_records_who_and_why():
    client, _, _, acme_id = _setup()
    response = client.put(_url(acme_id), json={"seatLimit": 40, "months": 24, "reason": REASON})
    assert response.status_code == 200
    body = response.json()
    assert (body["seatLimit"], body["version"], body["status"]) == (40, 2, "active")
    assert body["expiresOn"] == add_calendar_months(today_utc(), 24).isoformat()
    latest = body["events"][0]
    assert (latest["action"], latest["previousSeatLimit"], latest["newSeatLimit"]) == ("updated", 25, 40)
    assert (latest["actorEmail"], latest["reason"]) == (ADMIN, REASON)


def test_set_creates_a_plan_for_an_organization_without_one():
    client, _, _, acme_id = _setup(plan=False)
    body = client.put(_url(acme_id), json={"seatLimit": 5, "expiresOn": (today_utc() + timedelta(days=90)).isoformat(), "reason": REASON}).json()
    assert (body["metered"], body["seatLimit"], body["events"][0]["action"]) == (True, 5, "created")


def test_set_validates_the_request():
    client, _, _, acme_id = _setup()
    for payload in (
        {"seatLimit": 30, "months": 12, "reason": "short"},
        {"seatLimit": 30, "reason": REASON},
        {"seatLimit": 30, "months": 12, "expiresOn": "2030-01-01", "reason": REASON},
        {"seatLimit": 0, "months": 12, "reason": REASON},
        {"seatLimit": 30, "months": 0, "reason": REASON},
        {"seatLimit": 30, "months": 12, "reason": REASON, "unexpected": 1},
    ):
        assert client.put(_url(acme_id), json=payload).status_code == 422, payload


def test_set_refuses_a_past_expiry_and_a_limit_below_seats_in_use():
    client, Session, _, acme_id = _setup()
    past = client.put(_url(acme_id), json={"seatLimit": 30, "expiresOn": date(2020, 1, 1).isoformat(), "reason": REASON})
    assert past.status_code == 400 and "future" in past.json()["detail"]


def test_set_refuses_a_limit_below_seats_in_use():
    client, Session, _, acme_id = _setup()
    now = datetime.now(timezone.utc)
    with Session() as session:
        for index in range(2):
            user = upsert_user(
                session, email=f"m{index}@example.test", display_name=f"Member {index}",
                roles=[], pin="1234", must_change_pin=False,
            )
            session.add(OrganizationMembership(
                id=uuid.uuid4(), organization_id=acme_id, user_id=user.id, status="active",
                activated_at=now, created_at=now, updated_at=now,
            ))
        session.commit()
    response = client.put(_url(acme_id), json={"seatLimit": 1, "months": 12, "reason": REASON})
    assert response.status_code == 400
    assert "below the 2 seats in use" in response.json()["detail"]
    assert client.get(_url(acme_id)).json()["seatsInUse"] == 2


def test_renew_extends_a_running_plan_from_its_expiry_keeping_seats():
    client, _, _, acme_id = _setup(seat_limit=30, months=6)
    body = client.post(_url(acme_id, "/renew"), json={"months": 12, "reason": REASON}).json()
    assert body["seatLimit"] == 30
    assert body["expiresOn"] == add_calendar_months(add_calendar_months(today_utc(), 6), 12).isoformat()
    assert body["events"][0]["action"] == "updated"


def test_renew_restarts_a_lapsed_plan_from_today():
    client, Session, _, acme_id = _setup()
    with Session() as session:
        row = session.get(OrganizationSubscription, acme_id)
        row.started_on = today_utc() - timedelta(days=400)
        row.expires_on = today_utc() - timedelta(days=35)
        session.commit()
    assert client.get(_url(acme_id)).json()["status"] == "expired"
    body = client.post(_url(acme_id, "/renew"), json={"months": 12, "reason": REASON}).json()
    assert body["status"] == "active"
    assert body["expiresOn"] == add_calendar_months(today_utc(), 12).isoformat()
    assert body["startedOn"] == today_utc().isoformat()


def test_renew_needs_step_up_a_plan_and_a_valid_request():
    client, _, _, acme_id = _setup(step_up=False)
    assert client.post(_url(acme_id, "/renew"), json={"months": 12, "reason": REASON}).status_code == 403

    client, _, symgov_id, acme_id = _setup(plan=False)
    no_plan = client.post(_url(acme_id, "/renew"), json={"months": 12, "reason": REASON})
    assert no_plan.status_code == 400 and "no subscription" in no_plan.json()["detail"]
    assert client.post(_url(acme_id, "/renew"), json={"months": 0, "reason": REASON}).status_code == 422
    assert client.post(_url(acme_id, "/renew"), json={"months": 12, "reason": "short"}).status_code == 422
