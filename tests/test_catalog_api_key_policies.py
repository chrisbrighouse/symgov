"""Key holders and key policies (WP-A): who a key may authenticate as, which
origins it may be used from, and its per-minute budget.

SQLite stands in for the database here; the upsert behind the limiter and the
migration have their own PostgreSQL tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import CheckConstraint, JSON, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from symgov_backend.catalog_api_auth import (
    CatalogApiKeyPolicyError,
    IntegrationAuthContext,
    apply_key_policies,
    authenticate_catalog_api_key,
    effective_rate_limit,
    enforce_key_policies,
    hash_api_key,
)
from symgov_backend.models import (
    CatalogApiKey,
    CatalogApiKeyHolder,
    CatalogApiRateWindow,
    Organization,
    OrganizationMembership,
    User,
)
from symgov_backend.settings import SymgovAPISettings

NOW = datetime(2026, 10, 16, 12, 0, 30, tzinfo=timezone.utc)
TOKEN = "symgov_live_policy-test-token"


def _session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Organization, OrganizationMembership, CatalogApiKey, CatalogApiKeyHolder, CatalogApiRateWindow):
        table = model.__table__
        constraints = table.constraints
        types = {c.name: c.type for c in table.columns}
        defaults = {c.name: c.server_default for c in table.columns}
        try:
            for column in table.columns:
                if column.type.__class__.__name__ == "JSONB":
                    column.type = JSON()
                    column.server_default = None
            table.constraints = {
                item for item in constraints
                if not (isinstance(item, CheckConstraint) and any(t in str(item.sqltext) for t in ("~", "interval", "jsonb", "char_length", "::")))
            }
            table.create(engine)
        finally:
            table.constraints = constraints
            for column in table.columns:
                column.type = types[column.name]
                column.server_default = defaults[column.name]
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def session():
    with _session_factory()() as value:
        yield value


def _user(session, *, active=True):
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.test", display_name=f"U{uuid.uuid4().hex[:6]}", pin_hash="x", pin_set_at=NOW, is_active=active, created_at=NOW, updated_at=NOW)
    session.add(user)
    session.flush()
    return user


def _organization(session, *, active=True, entitlement="active"):
    suffix = uuid.uuid4().hex[:8]
    organization = Organization(
        id=uuid.uuid4(), code=f"ACME-{suffix.upper()}", normalized_code=f"acme-{suffix}", display_name=f"Acme {suffix}", name_key=uuid.uuid4().hex,
        entitlement_status=entitlement, is_active=active, is_protected=False, fallback_icon_svg="<svg/>",
        created_at=NOW, updated_at=NOW,
    )
    session.add(organization)
    session.flush()
    return organization


def _membership(session, organization, user, status="active"):
    session.add(OrganizationMembership(
        id=uuid.uuid4(), organization_id=organization.id, user_id=user.id, status=status, created_at=NOW, updated_at=NOW,
    ))
    session.flush()


def _key(session, *, kind="personal", holder=None, organization=None, claimed=True, origins=(), rate=None, revoke_reason=None, token=TOKEN, with_holder=True):
    holder = holder or _user(session)
    key = CatalogApiKey(
        id=uuid.uuid4(), customer_name="Acme", integration_name="CAD", key_prefix="symgov_live_policy", key_hash=hash_api_key(token if token != TOKEN or not session.query(CatalogApiKey).count() else uuid.uuid4().hex),
        scopes_json=["catalog.read"], status="active", allowed_origins_json=list(origins), rate_limit_per_minute=rate,
        created_by=holder.id, created_at=NOW, updated_at=NOW,
    )
    session.add(key)
    session.flush()
    if with_holder:
        session.add(CatalogApiKeyHolder(
            api_key_id=key.id, key_kind=kind, holder_user_id=holder.id,
            organization_id=organization.id if organization else None,
            issued_by_user_id=None, claimed_at=NOW if claimed else None, revoke_reason=revoke_reason,
        ))
    session.flush()
    return key, holder


def _auth(session):
    return authenticate_catalog_api_key(session, TOKEN, now=NOW)


def _request(origin=None):
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _context(**overrides):
    values = dict(api_key_id=str(uuid.uuid4()), customer_name="Acme", integration_name="CAD", scopes=("catalog.read",), key_prefix="symgov_live_x")
    values.update(overrides)
    return IntegrationAuthContext(**values)


# --- who a key authenticates as ------------------------------------------------


def test_personal_key_authenticates_as_its_holder(session):
    key, holder = _key(session)
    context = _auth(session)
    assert context.key_kind == "personal"
    assert context.holder_user_id == str(holder.id)
    assert context.organization_id is None


def test_deactivated_holder_key_stops_working(session):
    _key(session, holder=_user(session, active=False))
    assert _auth(session) is None


def test_key_without_a_holder_row_never_authenticates(session):
    _key(session, with_holder=False)
    assert _auth(session) is None


def test_organization_key_needs_claim_membership_and_active_organization(session):
    organization = _organization(session)
    holder = _user(session)
    _membership(session, organization, holder)
    _key(session, kind="organization", holder=holder, organization=organization)
    context = _auth(session)
    assert context.key_kind == "organization" and context.organization_id == str(organization.id)


@pytest.mark.parametrize("case", ["unclaimed", "membership_inactive", "no_membership", "organization_inactive", "suspended", "revoked"])
def test_organization_key_is_refused_when_any_condition_lapses(session, case):
    organization = _organization(session, active=case != "organization_inactive", entitlement="suspended" if case == "suspended" else "active")
    holder = _user(session)
    if case != "no_membership":
        _membership(session, organization, holder, status="inactive" if case == "membership_inactive" else "active")
    _key(
        session, kind="organization", holder=holder, organization=organization,
        claimed=case != "unclaimed", revoke_reason="membership_deactivated" if case == "revoked" else None,
    )
    assert _auth(session) is None


# --- origins -------------------------------------------------------------------


def test_request_without_origin_is_not_origin_checked(session):
    enforce_key_policies(session, _request(), _context(), SymgovAPISettings(), now=NOW)


def test_foreign_origin_is_refused_unless_the_key_lists_it(session):
    settings = SymgovAPISettings()
    with pytest.raises(CatalogApiKeyPolicyError) as refused:
        enforce_key_policies(session, _request("https://partner.example"), _context(), settings, now=NOW)
    assert refused.value.status_code == 403
    enforce_key_policies(
        session, _request("https://partner.example"), _context(allowed_origins=("https://partner.example",)), settings, now=NOW,
    )


def test_symgov_own_origin_is_always_permitted(session):
    enforce_key_policies(session, _request("http://testserver"), _context(), SymgovAPISettings(), now=NOW)


def test_listed_origin_must_match_scheme_and_port(session):
    context = _context(allowed_origins=("https://partner.example",))
    with pytest.raises(CatalogApiKeyPolicyError):
        enforce_key_policies(session, _request("http://partner.example"), context, SymgovAPISettings(), now=NOW)
    with pytest.raises(CatalogApiKeyPolicyError):
        enforce_key_policies(session, _request("https://partner.example:8443"), context, SymgovAPISettings(), now=NOW)


# --- rate limits ---------------------------------------------------------------


def _key_id(session):
    key, _ = _key(session)
    return str(key.id)


def test_defaults_are_60_personal_120_organization_and_capped_at_600():
    settings = SymgovAPISettings()
    assert effective_rate_limit(_context(), settings) == 60
    assert effective_rate_limit(_context(key_kind="organization"), settings) == 120
    assert effective_rate_limit(_context(rate_limit_per_minute=200), settings) == 200
    assert effective_rate_limit(_context(rate_limit_per_minute=5000), settings) == 600
    assert effective_rate_limit(_context(rate_limit_per_minute=0), settings) == 60


def test_the_request_over_the_limit_is_refused_with_retry_headers(session):
    context = _context(api_key_id=_key_id(session), rate_limit_per_minute=3)
    settings = SymgovAPISettings()
    for _ in range(3):
        enforce_key_policies(session, _request(), context, settings, now=NOW)
    with pytest.raises(CatalogApiKeyPolicyError) as refused:
        enforce_key_policies(session, _request(), context, settings, now=NOW)
    error = refused.value
    assert error.status_code == 429
    assert error.headers["Retry-After"] == "30"
    assert error.headers["X-RateLimit-Limit"] == "3"
    assert error.headers["X-RateLimit-Remaining"] == "0"


def test_the_next_minute_starts_a_new_window_and_old_windows_are_dropped(session):
    context = _context(api_key_id=_key_id(session), rate_limit_per_minute=1)
    settings = SymgovAPISettings()
    enforce_key_policies(session, _request(), context, settings, now=NOW)
    with pytest.raises(CatalogApiKeyPolicyError):
        enforce_key_policies(session, _request(), context, settings, now=NOW)
    enforce_key_policies(session, _request(), context, settings, now=NOW + timedelta(minutes=1))
    enforce_key_policies(session, _request(), context, settings, now=NOW + timedelta(hours=2))
    remaining = session.query(CatalogApiRateWindow).count()
    assert remaining == 1


def test_keys_have_separate_budgets(session):
    settings = SymgovAPISettings()
    first = _context(api_key_id=_key_id(session), rate_limit_per_minute=1)
    second = _context(api_key_id=_key_id(session), rate_limit_per_minute=1)
    enforce_key_policies(session, _request(), first, settings, now=NOW)
    enforce_key_policies(session, _request(), second, settings, now=NOW)


def test_apply_key_policies_turns_a_refusal_into_http_with_headers_and_keeps_the_count(session):
    context = _context(api_key_id=_key_id(session), rate_limit_per_minute=1)
    settings = SymgovAPISettings()
    apply_key_policies(session, _request(), context, settings)
    with pytest.raises(HTTPException) as refused:
        apply_key_policies(session, _request(), context, settings)
    assert refused.value.status_code == 429
    assert refused.value.headers["Retry-After"]
    forbidden = _context(api_key_id=context.api_key_id)
    with pytest.raises(HTTPException) as origin:
        apply_key_policies(session, _request("https://elsewhere.example"), forbidden, settings)
    assert origin.value.status_code == 403
