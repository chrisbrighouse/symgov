from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    OrganizationMembership,
    OrganizationRoleAssignment,
    OrganizationSubscription,
    UserRole,
)
from symgov_backend.organization_subscriptions import set_organization_subscription  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402
from symgov_backend.subscriptions import today_utc, upgrade_to_plus  # noqa: E402
from test_platform_organizations_api import (  # noqa: E402
    _build_client,
    _login_and_step_up,
    _seed_commercial_org,
    _seed_symgov_org_with_platform_admin,
)

ADMIN = "platform-admin@example.test"
REASON = "Contracted submitter seat for this team."


def _seed_member(Session, org_id, email="member@example.test", *, status="active"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        user = upsert_user(session, email=email, display_name=email.split("@")[0], roles=[], pin="1234", must_change_pin=False)
        session.commit()
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=org_id, user_id=user.id, status=status,
            activated_at=now, created_at=now, updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(OrganizationRoleAssignment(
            id=uuid.uuid4(), membership_id=membership.id, base_role="user", is_active=True, assigned_at=now,
        ))
        session.commit()
        return membership.id, user.id


def _settings(*, plan_roles_enabled=True):
    return SymgovAPISettings(
        organizations_enabled=True, platform_admin_enabled=True, organization_admin_enabled=False,
        symbol_sets_enabled=False, organization_symbols_enabled=False, organization_agents_enabled=False,
        organization_plan_roles_enabled=plan_roles_enabled,
    )


def _setup(*, plan=True, plan_roles_enabled=True):
    client, Session, admin_id, _, _ = _build_client()
    client.app.dependency_overrides[get_settings] = lambda: _settings(plan_roles_enabled=plan_roles_enabled)
    symgov_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    if plan:
        with Session() as session:
            set_organization_subscription(session, acme_id, seat_limit=25, months=12)
            session.commit()
    membership_id, member_id = _seed_member(Session, acme_id)
    _login_and_step_up(client, ADMIN, symgov_id)
    return client, Session, symgov_id, acme_id, membership_id, member_id


def _url(org_id, membership_id, role, suffix=""):
    return f"/api/v1/platform/organizations/{org_id}/members/{membership_id}/roles/{role}{suffix}"


def _member_roles_in_org_session(client, org_id, email="member@example.test"):
    """Sign in as the member, bind to the organization, and read what /auth/me says."""
    member = TestClient(client.app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    member.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    member.post("/api/v1/auth/organizations/select", json={"organizationId": str(org_id)})
    body = member.get("/api/v1/auth/me").json()
    return body["user"]["roles"], body["user"]["subscription"]["tier"]


def test_a_free_member_of_a_subscribed_organization_has_no_roles_until_one_is_assigned():
    client, _, _, acme_id, _, _ = _setup()
    assert _member_roles_in_org_session(client, acme_id) == ([], "free")


def test_assigned_role_applies_in_the_organization_session_and_personal_tier_is_unchanged():
    client, Session, _, acme_id, membership_id, member_id = _setup()
    response = client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    assert response.status_code == 200
    assert [(i["email"], i["role"]) for i in response.json()["items"]] == [("member@example.test", "submitter")]
    assert response.json()["rolesEnabled"] is True
    assert response.json()["assignableRoles"] == ["integrator", "reviewer", "submitter"]
    roles, tier = _member_roles_in_org_session(client, acme_id)
    assert roles == ["submitter"]
    assert tier == "free"  # the personal plan is untouched; only the session's roles change
    with Session() as session:
        assert session.query(UserRole).filter_by(user_id=member_id).count() == 0


def test_roles_stop_when_the_plan_lapses_and_return_on_renewal():
    client, Session, _, acme_id, membership_id, _ = _setup()
    client.put(_url(acme_id, membership_id, "reviewer"), json={"reason": REASON})
    assert _member_roles_in_org_session(client, acme_id)[0] == ["reviewer"]

    with Session() as session:
        row = session.get(OrganizationSubscription, acme_id)
        row.started_on = today_utc() - timedelta(days=400)
        row.expires_on = today_utc() - timedelta(days=35)
        session.commit()
    assert _member_roles_in_org_session(client, acme_id)[0] == []

    client.post(
        f"/api/v1/platform/organizations/{acme_id}/subscription/renew", json={"months": 12, "reason": "Renewed after lapse."}
    )
    assert _member_roles_in_org_session(client, acme_id)[0] == ["reviewer"]


def test_revoking_a_role_removes_it_and_records_who_and_why():
    client, Session, _, acme_id, membership_id, _ = _setup()
    client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    client.put(_url(acme_id, membership_id, "integrator"), json={"reason": REASON})
    response = client.post(_url(acme_id, membership_id, "submitter", "/revoke"), json={"reason": "Moved to another team."})
    assert response.status_code == 200
    assert [i["role"] for i in response.json()["items"]] == ["integrator"]
    assert _member_roles_in_org_session(client, acme_id)[0] == ["integrator"]
    from symgov_backend.models import OrganizationMemberRole
    with Session() as session:
        revoked = session.query(OrganizationMemberRole).filter_by(role="submitter").one()
        assert (revoked.is_active, revoked.revoke_reason) == (False, "Moved to another team.")
        assert revoked.revoked_by_user_id is not None and revoked.assign_reason == REASON


def test_personal_plus_and_organization_roles_combine_in_the_organization_session():
    client, Session, _, acme_id, membership_id, member_id = _setup()
    with Session() as session:
        from symgov_backend.models import User
        user = session.get(User, member_id)
        upgrade_to_plus(session, user, months=12)
        session.add(UserRole(user_id=member_id, role="reviewer", created_at=datetime.now(timezone.utc)))
        session.commit()
    client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    roles, tier = _member_roles_in_org_session(client, acme_id)
    assert (roles, tier) == (["reviewer", "submitter"], "plus")


def test_the_role_applies_only_in_its_own_organization():
    from symgov_backend.auth import effective_roles
    from symgov_backend.models import User, UserSubscription
    from symgov_backend.organization_authorization import EligibleOrganizationMembership

    client, Session, _, acme_id, membership_id, member_id = _setup()
    client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    other_org = _seed_commercial_org(Session, code="BETA")
    other_membership, _ = _seed_member(Session, other_org, email="other@example.test")
    with Session() as session:
        set_organization_subscription(session, other_org, seat_limit=5, months=12)
        user = session.get(User, member_id)
        subscription = session.get(UserSubscription, member_id) or None
        from symgov_backend.subscriptions import ensure_subscription
        subscription = ensure_subscription(session, user)

        def context(org, membership):
            return EligibleOrganizationMembership(
                membership_id=membership, organization_id=org, code="X", display_name="X", base_role="user",
                capabilities=(), is_platform_admin=False,
            )

        assert effective_roles(session, user, subscription, context(acme_id, membership_id), _settings()) == ("submitter",)
        assert effective_roles(session, user, subscription, context(other_org, other_membership), _settings()) == ()
        assert effective_roles(session, user, subscription, None, _settings()) == ()


def test_granting_is_checked_idempotent_and_step_up_protected():
    client, Session, symgov_id, acme_id, membership_id, _ = _setup()
    url = _url(acme_id, membership_id, "submitter")
    assert client.put(url, json={"reason": REASON}).status_code == 200
    assert client.put(url, json={"reason": REASON}).status_code == 200
    assert len(client.get(f"/api/v1/platform/organizations/{acme_id}/member-roles").json()["items"]) == 1

    for role in ("admin", "nonsense"):
        refused = client.put(_url(acme_id, membership_id, role), json={"reason": REASON})
        assert refused.status_code == 400 and "Unknown organization role" in refused.json()["detail"]
    assert client.put(url, json={"reason": "short"}).status_code == 422
    assert client.put(_url(acme_id, uuid.uuid4(), "submitter"), json={"reason": REASON}).status_code == 400
    assert client.put(_url(acme_id, "not-a-uuid", "submitter"), json={"reason": REASON}).status_code == 404
    assert client.post(_url(acme_id, membership_id, "reviewer", "/revoke"), json={"reason": REASON}).status_code == 400

    inactive_membership, _ = _seed_member(Session, acme_id, email="gone@example.test", status="inactive")
    gone = client.put(_url(acme_id, inactive_membership, "submitter"), json={"reason": REASON})
    assert gone.status_code == 400 and "active membership" in gone.json()["detail"]


def test_an_organization_without_a_plan_cannot_assign_roles_and_symgov_never_can():
    client, _, symgov_id, acme_id, membership_id, _ = _setup(plan=False)
    refused = client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    assert refused.status_code == 400 and "no subscription" in refused.json()["detail"]
    protected = client.put(_url(symgov_id, membership_id, "submitter"), json={"reason": REASON})
    assert protected.status_code == 400


def test_assigning_requires_step_up_and_a_platform_admin():
    client, Session, admin_id_org, acme_id, membership_id, _ = _setup()
    fresh, Session2, *_ = _build_client()
    assert fresh.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON}).status_code == 401

    from test_platform_organizations_api import _login_and_select_org
    client2, Session2, admin2, _, _ = _build_client()
    symgov2 = _seed_symgov_org_with_platform_admin(Session2, admin2)
    acme2 = _seed_commercial_org(Session2, code="ACME")
    with Session2() as session:
        set_organization_subscription(session, acme2, seat_limit=5, months=12)
        session.commit()
    membership2, _ = _seed_member(Session2, acme2)
    _login_and_select_org(client2, ADMIN, symgov2)  # signed in, no step-up
    assert client2.put(_url(acme2, membership2, "submitter"), json={"reason": REASON}).status_code == 403


def test_while_the_flag_is_off_assigned_roles_have_no_effect_and_the_tables_are_not_read():
    client, Session, _, acme_id, membership_id, _ = _setup(plan_roles_enabled=False)
    granted = client.put(_url(acme_id, membership_id, "submitter"), json={"reason": REASON})
    assert granted.status_code == 200
    assert granted.json()["rolesEnabled"] is False  # recorded, not yet in effect
    assert _member_roles_in_org_session(client, acme_id) == ([], "free")

    from symgov_backend.auth import effective_roles
    from symgov_backend.models import User
    from symgov_backend.subscriptions import ensure_subscription
    from unittest.mock import patch

    with Session() as session, patch("symgov_backend.auth.organization_plan_roles") as lookup:
        user = session.query(User).filter_by(email="member@example.test").one()
        assert effective_roles(session, user, ensure_subscription(session, user), object(), _settings(plan_roles_enabled=False)) == ()
        lookup.assert_not_called()
