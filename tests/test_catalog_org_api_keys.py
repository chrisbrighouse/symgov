"""Organization Catalog API keys (WP-A): issue, claim, revoke, and what ends them.

The service is exercised on SQLite sessions; the routes through the same
organization-bound test client the Symbol Set routes use.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import CheckConstraint, JSON

from catalog_key_fakes import HOLDER_ID  # noqa: F401  (keeps the helper module importable from here)
from symgov_backend.catalog_api_auth import authenticate_catalog_api_key
from symgov_backend.catalog_api_keys import CatalogApiKeyError, CatalogApiKeyNotFoundError
from symgov_backend.catalog_org_api_keys import (
    CatalogOrganizationKeyAlreadyClaimedError,
    CatalogOrganizationKeyNotAllowedError,
    MAX_OPEN_KEYS_PER_HOLDER,
    claim_organization_catalog_api_key,
    issue_organization_catalog_api_key,
    list_holder_catalog_api_keys,
    list_organization_catalog_api_keys,
    revoke_organization_catalog_api_key,
    revoke_organization_keys_for,
)
from symgov_backend.dependencies import get_db_session
from symgov_backend.models import AuditEvent, CatalogApiKey, CatalogApiKeyHolder, CatalogApiRateWindow, OrganizationMembership, User
from symgov_backend.settings import get_settings

from test_catalog_api_key_policies import _membership, _organization, _session_factory, _user  # noqa: F401
from test_projects_api import _stage4_client

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    # AuditEvent is JSONB; the policies fixtures leave it out, so add it here.
    factory = _session_factory()
    table = AuditEvent.__table__
    original = table.constraints
    types = {c.name: c.type for c in table.columns}
    defaults = {c.name: c.server_default for c in table.columns}
    try:
        for column in table.columns:
            if column.type.__class__.__name__ == "JSONB":
                column.type = JSON()
                column.server_default = None
        table.create(factory.kw["bind"])
    finally:
        table.constraints = original
        for column in table.columns:
            column.type = types[column.name]
            column.server_default = defaults[column.name]
    with factory() as value:
        yield value


def _setup(session):
    organization = _organization(session)
    admin, holder = _user(session), _user(session)
    _membership(session, organization, admin)
    _membership(session, organization, holder)
    return organization, admin, holder


def _issue(session, organization, admin, holder, **overrides):
    values = dict(
        organization_id=organization.id, issuer_user_id=admin.id, holder_user_id=holder.id,
        integration_name="CAD plugin", scopes=["catalog.read"], now=NOW,
    )
    values.update(overrides)
    return issue_organization_catalog_api_key(session, **values)


# --- issue -----------------------------------------------------------------------


def test_issuing_discloses_no_secret_and_the_key_cannot_be_used_before_the_claim(session):
    organization, admin, holder = _setup(session)
    key = _issue(session, organization, admin, holder)
    assert key["state"] == "pending_claim"
    assert key["keyPrefix"] is None
    assert "rawKey" not in key
    assert key["holderUserId"] == str(holder.id)
    stored = session.get(CatalogApiKey, uuid.UUID(key["keyId"]))
    assert stored.key_prefix == "pending"
    assert authenticate_catalog_api_key(session, "pending", now=NOW) is None
    audit = session.query(AuditEvent).filter(AuditEvent.action == "catalog_api_key.issued").one()
    assert audit.actor_id == admin.id and audit.payload_json["actor_type"] == "organization_admin"
    assert "key_hash" not in audit.payload_json


def test_a_holder_must_be_an_active_member_and_the_organization_must_allow_keys(session):
    organization, admin, holder = _setup(session)
    outsider = _user(session)
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        _issue(session, organization, admin, outsider)
    inactive = _user(session, active=False)
    _membership(session, organization, inactive)
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        _issue(session, organization, admin, inactive)
    suspended = _organization(session, entitlement="suspended")
    _membership(session, suspended, holder)
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        _issue(session, suspended, admin, holder, organization_id=suspended.id)


def test_issue_validates_scopes_limits_and_origins(session):
    organization, admin, holder = _setup(session)
    with pytest.raises(CatalogApiKeyError):
        _issue(session, organization, admin, holder, scopes=["catalog.nonsense"])
    with pytest.raises(CatalogApiKeyError):
        _issue(session, organization, admin, holder, rate_limit_per_minute=601)
    with pytest.raises(CatalogApiKeyError):
        _issue(session, organization, admin, holder, rate_limit_per_minute=0)
    with pytest.raises(CatalogApiKeyError):
        _issue(session, organization, admin, holder, allowed_origins=["https://partner.example/path"])
    with pytest.raises(CatalogApiKeyError):
        _issue(session, organization, admin, holder, expires_at=NOW - timedelta(days=1))
    key = _issue(session, organization, admin, holder, rate_limit_per_minute=300, allowed_origins=["https://partner.example/", "https://partner.example"])
    assert key["rateLimitPerMinute"] == 300 and key["allowedOrigins"] == ["https://partner.example"]


def test_a_member_may_hold_only_so_many_open_keys(session):
    organization, admin, holder = _setup(session)
    for index in range(MAX_OPEN_KEYS_PER_HOLDER):
        _issue(session, organization, admin, holder, integration_name=f"plugin {index}")
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        _issue(session, organization, admin, holder, integration_name="one too many")


# --- claim -------------------------------------------------------------------------


def test_the_holder_claims_the_key_and_sees_the_secret_once(session):
    organization, admin, holder = _setup(session)
    issued = _issue(session, organization, admin, holder)
    key_id = uuid.UUID(issued["keyId"])
    key, raw = claim_organization_catalog_api_key(session, api_key_id=key_id, user_id=holder.id, now=NOW)
    assert raw.startswith("symgov_live_") and key["state"] == "active" and raw.startswith(key["keyPrefix"])
    context = authenticate_catalog_api_key(session, raw, now=NOW)
    assert context.key_kind == "organization" and context.organization_id == str(organization.id)
    assert context.holder_user_id == str(holder.id) and context.scopes == ("catalog.read",)
    with pytest.raises(CatalogOrganizationKeyAlreadyClaimedError):
        claim_organization_catalog_api_key(session, api_key_id=key_id, user_id=holder.id, now=NOW)
    assert raw not in repr(list_holder_catalog_api_keys(session, holder.id))
    assert raw not in repr(list_organization_catalog_api_keys(session, organization.id))
    claimed = session.query(AuditEvent).filter(AuditEvent.action == "catalog_api_key.claimed").one()
    assert raw not in repr(claimed.payload_json)


def test_nobody_but_the_holder_can_claim(session):
    organization, admin, holder = _setup(session)
    issued = _issue(session, organization, admin, holder)
    with pytest.raises(CatalogApiKeyNotFoundError):
        claim_organization_catalog_api_key(session, api_key_id=uuid.UUID(issued["keyId"]), user_id=admin.id, now=NOW)
    with pytest.raises(CatalogApiKeyNotFoundError):
        claim_organization_catalog_api_key(session, api_key_id=uuid.uuid4(), user_id=holder.id, now=NOW)


def test_a_key_cannot_be_claimed_after_revocation_or_loss_of_membership(session):
    organization, admin, holder = _setup(session)
    revoked = _issue(session, organization, admin, holder)
    revoke_organization_catalog_api_key(session, organization_id=organization.id, api_key_id=uuid.UUID(revoked["keyId"]), actor_id=admin.id, now=NOW)
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        claim_organization_catalog_api_key(session, api_key_id=uuid.UUID(revoked["keyId"]), user_id=holder.id, now=NOW)
    lapsed = _issue(session, organization, admin, holder, integration_name="second")
    session.query(OrganizationMembership).filter(OrganizationMembership.user_id == holder.id).update({"status": "inactive"})
    with pytest.raises(CatalogOrganizationKeyNotAllowedError):
        claim_organization_catalog_api_key(session, api_key_id=uuid.UUID(lapsed["keyId"]), user_id=holder.id, now=NOW)


# --- revoke ------------------------------------------------------------------------


def _claimed(session, organization, admin, holder, **overrides):
    issued = _issue(session, organization, admin, holder, **overrides)
    key, raw = claim_organization_catalog_api_key(session, api_key_id=uuid.UUID(issued["keyId"]), user_id=holder.id, now=NOW)
    return uuid.UUID(key["keyId"]), raw


def test_an_admin_revokes_a_key_and_it_stops_working(session):
    organization, admin, holder = _setup(session)
    key_id, raw = _claimed(session, organization, admin, holder)
    revoked = revoke_organization_catalog_api_key(session, organization_id=organization.id, api_key_id=key_id, actor_id=admin.id, now=NOW)
    assert revoked["state"] == "revoked" and revoked["revokeReason"] == "revoked_by_admin"
    assert authenticate_catalog_api_key(session, raw, now=NOW) is None
    # Revoking again is harmless.
    revoke_organization_catalog_api_key(session, organization_id=organization.id, api_key_id=key_id, actor_id=admin.id, now=NOW)


def test_an_admin_cannot_revoke_another_organizations_key(session):
    organization, admin, holder = _setup(session)
    key_id, raw = _claimed(session, organization, admin, holder)
    other = _organization(session)
    with pytest.raises(CatalogApiKeyNotFoundError):
        revoke_organization_catalog_api_key(session, organization_id=other.id, api_key_id=key_id, actor_id=admin.id, now=NOW)
    assert authenticate_catalog_api_key(session, raw, now=NOW) is not None


def test_losing_the_membership_revokes_for_good_even_if_the_member_returns(session):
    organization, admin, holder = _setup(session)
    _, raw = _claimed(session, organization, admin, holder)
    other = _organization(session)
    _membership(session, other, holder)
    _, other_raw = _claimed(session, other, admin, holder, organization_id=other.id)
    count = revoke_organization_keys_for(session, reason="membership_deactivated", user_id=holder.id, organization_id=organization.id, now=NOW)
    assert count == 1
    session.query(OrganizationMembership).filter(OrganizationMembership.user_id == holder.id).update({"status": "active"})
    assert authenticate_catalog_api_key(session, raw, now=NOW) is None
    assert authenticate_catalog_api_key(session, other_raw, now=NOW) is not None
    holder_row = session.query(CatalogApiKeyHolder).filter(CatalogApiKeyHolder.organization_id == organization.id).one()
    assert holder_row.revoke_reason == "membership_deactivated"
    assert revoke_organization_keys_for(session, reason="membership_deactivated", user_id=holder.id, organization_id=organization.id, now=NOW) == 0


def test_suspending_the_organization_revokes_every_key_of_it(session):
    organization, admin, holder = _setup(session)
    _, raw = _claimed(session, organization, admin, holder)
    assert revoke_organization_keys_for(session, reason="organization_suspended", organization_id=organization.id, now=NOW) == 1
    assert authenticate_catalog_api_key(session, raw, now=NOW) is None
    audit = session.query(AuditEvent).filter(AuditEvent.action == "catalog_api_key.revoked").one()
    assert audit.payload_json["reason"] == "organization_suspended"


def test_a_personal_key_is_not_touched_by_organization_revocation(session):
    organization, admin, holder = _setup(session)
    from test_catalog_api_key_policies import _key

    _, raw_holder = _key(session, holder=holder, token="personal-token")
    assert revoke_organization_keys_for(session, reason="user_deactivated", user_id=holder.id, now=NOW) == 0
    assert authenticate_catalog_api_key(session, "personal-token", now=NOW) is not None


def test_listings_show_the_right_people_the_right_keys(session):
    organization, admin, holder = _setup(session)
    pending = _issue(session, organization, admin, holder, integration_name="waiting")
    claimed_id, _ = _claimed(session, organization, admin, holder, integration_name="done")
    admin_view = {item["integrationName"]: item for item in list_organization_catalog_api_keys(session, organization.id)}
    assert admin_view["waiting"]["state"] == "pending_claim" and admin_view["done"]["state"] == "active"
    assert admin_view["done"]["holderEmail"] == holder.email
    mine = {item["integrationName"]: item for item in list_holder_catalog_api_keys(session, holder.id)}
    assert set(mine) == {"waiting", "done"} and mine["done"]["organizationName"] == organization.display_name
    assert list_holder_catalog_api_keys(session, admin.id) == []
    assert pending["keyId"] != str(claimed_id)


# --- routes ------------------------------------------------------------------------


def _client(*, enabled=True):
    client, Session = _stage4_client(role="admin")
    from symgov_backend.models import AuditEvent as Audit  # noqa: F401

    settings = replace(client._stage4_settings, organization_api_keys_enabled=enabled)
    client.app.dependency_overrides[get_settings] = lambda: settings
    from symgov_backend.models import SymbolUsageEvent

    for model in (CatalogApiKey, CatalogApiKeyHolder, CatalogApiRateWindow, SymbolUsageEvent):
        table = model.__table__
        original = table.constraints
        types = {c.name: c.type for c in table.columns}
        defaults = {c.name: c.server_default for c in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item for item in original
                if not (isinstance(item, CheckConstraint) and any(t in str(item.sqltext) for t in ("~", "jsonb", "char_length", "::", "interval")))
            }
            table.create(Session.kw["bind"], checkfirst=True)
        finally:
            table.constraints = original
            for column in table.columns:
                column.type = types[column.name]
                column.server_default = defaults[column.name]
    return client, Session


def _step_up(client):
    assert client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"}).status_code == 200


def _member_id(Session):
    with Session() as session:
        return session.query(User).first().id


def test_the_admin_routes_are_off_unless_the_flag_is_on():
    client, _ = _client(enabled=False)
    assert client.get("/api/v1/org/me/api-keys").status_code == 404
    assert client.get("/api/v1/profile/api-keys").status_code == 404


def test_admin_issues_then_holder_claims_through_the_routes():
    client, Session = _client()
    me = str(_member_id(Session))
    _step_up(client)
    issued = client.post("/api/v1/org/me/api-keys", json={"holderUserId": me, "integrationName": "Revit add-in", "scopes": ["catalog.read"], "rateLimitPerMinute": 90})
    assert issued.status_code == 201, issued.text
    key_id = issued.json()["key"]["keyId"]
    assert "rawKey" not in issued.text

    listed = client.get("/api/v1/org/me/api-keys").json()
    assert [item["keyId"] for item in listed["items"]] == [key_id] and listed["defaults"]["rateLimitPerMinute"] == 120

    mine = client.get("/api/v1/profile/api-keys").json()["items"]
    assert mine[0]["state"] == "pending_claim"
    claim = client.post(f"/api/v1/profile/api-keys/{key_id}/claim")
    assert claim.status_code == 200 and claim.json()["rawKey"].startswith("symgov_live_")
    again = client.post(f"/api/v1/profile/api-keys/{key_id}/claim")
    assert again.status_code == 409 and "symgov_live_" not in again.text

    revoked = client.delete(f"/api/v1/org/me/api-keys/{key_id}")
    assert revoked.status_code == 200 and revoked.json()["key"]["state"] == "revoked"
    assert client.delete(f"/api/v1/org/me/api-keys/{uuid.uuid4()}").status_code == 404


def test_issuing_and_revoking_need_a_recent_pin_step_up():
    client, Session = _client()
    me = str(_member_id(Session))
    body = {"holderUserId": me, "integrationName": "Revit add-in"}
    assert client.post("/api/v1/org/me/api-keys", json=body).status_code == 403
    assert client.delete(f"/api/v1/org/me/api-keys/{uuid.uuid4()}").status_code == 403
    assert client.get("/api/v1/org/me/api-keys").status_code == 200
    _step_up(client)
    assert client.post("/api/v1/org/me/api-keys", json=body).status_code == 201


def test_the_routes_refuse_bad_requests():
    client, Session = _client()
    me = str(_member_id(Session))
    _step_up(client)
    assert client.post("/api/v1/org/me/api-keys", json={"holderUserId": str(uuid.uuid4()), "integrationName": "x"}).status_code == 409
    assert client.post("/api/v1/org/me/api-keys", json={"holderUserId": me, "integrationName": "x", "rateLimitPerMinute": 9999}).status_code == 422
    assert client.post("/api/v1/org/me/api-keys", json={"holderUserId": me, "integrationName": "x", "surprise": 1}).status_code == 422
    assert client.post(f"/api/v1/profile/api-keys/{uuid.uuid4()}/claim").status_code == 404


def test_a_plain_member_cannot_administer_keys():
    client, Session = _stage4_client(role="user")
    settings = replace(client._stage4_settings, organization_api_keys_enabled=True)
    client.app.dependency_overrides[get_settings] = lambda: settings
    assert client.get("/api/v1/org/me/api-keys").status_code == 403
