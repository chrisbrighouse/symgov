"""Stage 11 WP11.3 — the complete two-organization adversarial fixture and
route-policy/tenant-isolation matrix (programme plan §17 "Integrated
verification" items 1-2), against a real disposable PostgreSQL container.

Stages 1-10 already built extensive per-stage tenant-isolation coverage
(organization admin API, org symbol drafts/review, projects, promotion
requests, demotion, catalog organization context from four angles, product
usage/contribution isolation, agent configuration/findings, and Stage 11's
own WP11.1 Symbol Set item/palette/builder-search isolation tests). This
module does not re-prove what those already cover -- see
`docs/plans/2026-09-06-stage11-wp11.3-route-policy-tenant-isolation-matrix.md`
for the full route-by-route audit of that existing coverage and the
resulting gap list. This file:

1. Provides one canonical, reusable two-organization adversarial fixture
   with every principal type named in §17 item 1 (personal, Organization
   User/Admin/reviewer-capability per organization, Platform Admin,
   an inactive organization membership, and an API-key principal) --
   consolidating what every prior stage's test file rebuilt ad hoc.
2. Closes the specific gaps the route-policy audit found: proving Symbol
   Set item mutation stays Organization-Admin-only even under the WP11.1
   widened eligibility path (a non-admin `symbol_reviewer`-capability actor
   can browse but never write, own-organization symbol or not) -- WP11.1
   itself only exercised the widened path with an Organization Admin actor;
   Platform Admin's platform-level authority not leaking into org-scoped
   session routes for organizations Platform Admin does not belong to;
   an inactive organization membership being rejected at the real HTTP
   login/session layer (not just the service layer some Stage 5/9 tests
   already cover); and the API-key principal being confined to the public
   Catalog -- genuinely proven by exercising the real API-key-gated
   `/catalog/symbols` route, not merely by an organization-scoped route
   401ing a session-less client for an unrelated, structural reason (see
   Gap 5's corrected framing below, added after WP11.6's independent
   review caught the original test's misleading claim).

Every unauthorized cross-tenant access attempt must 404 (not 403),
consistent with the precedent Stage 10's own tests set
(`test_organization_findings_are_not_visible_to_a_different_organizations_admin`).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp53_organization_symbol_drafts import _actor  # noqa: E402
from test_wp74_symbol_demotion_postgresql import (  # noqa: E402
    _add_membership,
    _create_user_with_global_roles,
    _make_platform_admin,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.catalog_api_auth import hash_api_key  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import CatalogApiKey, Organization, OrganizationMembership, OrganizationRoleAssignment  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import decide_submission  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260908_0046"


@pytest.fixture(scope="module")
def stage11_database():
    with _database("symgov-stage11-wp11-3") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


def _unique_code(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def _unique_set_code(prefix: str) -> str:
    """Symbol Set codes must match `^[A-Z0-9][A-Z0-9-]{0,31}$` -- uppercase
    only, unlike organization codes."""
    return f"{prefix}{uuid.uuid4().hex[:6]}".upper()


def _client(engine, *, pilot_codes):
    app = create_app()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_admin_enabled=True,
        platform_admin_enabled=True,
        symbol_sets_enabled=True,
        organization_symbols_enabled=True,
        organization_pilot_codes=tuple(pilot_codes),
    )

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), SessionLocal


def _login(client, email, *, expected_status=200):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == expected_status, response.text
    return response


def _new_user(Session, label: str) -> tuple[uuid.UUID, str]:
    suffix = uuid.uuid4().hex[:8]
    email = f"wp113{label}-{suffix}@example.test"
    user_id = _create_user_with_global_roles(Session, email=email, display_name=f"WP11.3 {label} {suffix}", roles=[])
    return user_id, email


def _inactive_membership(Session, user_id, *, code):
    """A same-shape membership to `_add_membership`, but seeded directly at
    `status='inactive'` -- this organization once admitted the user, but
    the membership itself, not just a role, is now inactive."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        organization = session.query(Organization).filter(Organization.normalized_code == code).one()
        membership = OrganizationMembership(
            id=uuid.uuid4(), organization_id=organization.id, user_id=user_id,
            status="inactive", activated_at=now, deactivated_at=now, created_at=now, updated_at=now,
        )
        session.add(membership)
        session.flush()
        session.add(OrganizationRoleAssignment(
            id=uuid.uuid4(), membership_id=membership.id, base_role="user", is_active=True,
            assigned_at=now, revoked_at=None,
        ))


def _organization_id(Session, code) -> uuid.UUID:
    with Session() as session:
        return session.query(Organization).filter(Organization.normalized_code == code).one().id


def _api_key(Session) -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    token = f"wp113-{uuid.uuid4().hex}"
    with Session() as session:
        session.add(CatalogApiKey(
            id=uuid.uuid4(), customer_name="Stage11 WP11.3", integration_name="adversarial-fixture",
            key_prefix=token[:12], key_hash=hash_api_key(token), scopes_json=["catalog.read"],
            status="active", created_at=now, updated_at=now,
        ))
        session.commit()
    return token


@pytest.fixture()
def adversarial(stage11_database):
    engine, _, _ = stage11_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    code_a, code_b = _unique_code("wp113a"), _unique_code("wp113b")

    personal_id, personal_email = _new_user(Session, "personal")

    # The active-organization-admin-minimum trigger requires each new
    # organization to be created with its first Admin membership -- add the
    # Admin before any other membership on that same organization.
    org_a_admin_id, org_a_admin_email = _new_user(Session, "orgaadmin")
    _add_membership(Session, org_a_admin_id, code=code_a, base_role="admin")

    org_a_user_id, org_a_user_email = _new_user(Session, "orgauser")
    _add_membership(Session, org_a_user_id, code=code_a, base_role="user")

    org_a_reviewer_id, org_a_reviewer_email = _new_user(Session, "orgareviewer")
    _add_membership(Session, org_a_reviewer_id, code=code_a, base_role="user", capabilities=("contributor", "symbol_reviewer"))

    org_b_admin_id, org_b_admin_email = _new_user(Session, "orgbadmin")
    _add_membership(Session, org_b_admin_id, code=code_b, base_role="admin")

    org_b_user_id, org_b_user_email = _new_user(Session, "orgbuser")
    _add_membership(Session, org_b_user_id, code=code_b, base_role="user")

    org_b_reviewer_id, org_b_reviewer_email = _new_user(Session, "orgbreviewer")
    _add_membership(Session, org_b_reviewer_id, code=code_b, base_role="user", capabilities=("contributor", "symbol_reviewer"))

    platform_admin_id, platform_admin_email = _new_user(Session, "platformadmin")
    _make_platform_admin(Session, platform_admin_id)

    org_a_inactive_id, org_a_inactive_email = _new_user(Session, "orgainactive")
    _inactive_membership(Session, org_a_inactive_id, code=code_a)

    api_key_token = _api_key(Session)

    pilot_codes = (code_a, code_b, "symgov")
    clients = {}
    for name, email in (
        ("org_a_user", org_a_user_email),
        ("org_a_admin", org_a_admin_email),
        ("org_a_reviewer", org_a_reviewer_email),
        ("org_b_user", org_b_user_email),
        ("org_b_admin", org_b_admin_email),
        ("org_b_reviewer", org_b_reviewer_email),
        ("platform_admin", platform_admin_email),
    ):
        client, _ = _client(engine, pilot_codes=pilot_codes)
        _login(client, email)
        clients[name] = client

    personal_client, _ = _client(engine, pilot_codes=pilot_codes)
    _login(personal_client, personal_email)
    clients["personal"] = personal_client

    api_client, _ = _client(engine, pilot_codes=pilot_codes)
    api_client.headers["Authorization"] = f"Bearer {api_key_token}"
    clients["api_key"] = api_client

    inactive_client, _ = _client(engine, pilot_codes=pilot_codes)
    clients["org_a_inactive"] = inactive_client

    from types import SimpleNamespace

    return SimpleNamespace(
        engine=engine, Session=Session,
        code_a=code_a, code_b=code_b,
        org_a_id=_organization_id(Session, code_a), org_b_id=_organization_id(Session, code_b),
        clients=clients,
        org_a_reviewer_id=org_a_reviewer_id, org_b_reviewer_id=org_b_reviewer_id,
        org_a_inactive_email=org_a_inactive_email,
    )


def _create_active_set(client, code):
    created = client.post("/api/v1/org/me/symbol-sets", json={"code": code, "name": code})
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    activated = client.patch(f"/api/v1/org/me/symbol-sets/{set_id}", json={"status": "active"})
    assert activated.status_code == 200, activated.text
    return set_id


def _approved_private_symbol(fixtures, actor_reviewer, *, name):
    """Builds a real approved, organization-private symbol via the actual
    Stage 5 service pipeline (mirrors `test_symbol_set_tenant_isolation.py`),
    so it is reachable exactly the way production would reach it."""
    SessionLocal = fixtures.Session
    with SessionLocal() as session:
        symbol, revision = create_draft(session, actor_reviewer, name=name, category="fire", discipline="fire-safety", summary="WP11.3 fixture symbol.")
        session.commit()
        submission = submit_for_review(session, actor_reviewer, symbol_id=symbol.id, revision_id=revision.id)
        session.commit()
        decide_submission(session, actor_reviewer, submission_id=submission.id, decision="approved")
        session.commit()
        return symbol.id


# --- Gap 1: the WP11.1 widened path is Organization-Admin-only to *write*
# (`replace_items` requires admin regardless of which symbols are eligible)
# -- a non-admin `symbol_reviewer`-capability actor can browse the
# organization half of Builder search (Stage 6, already tested) but must
# never be able to mutate Symbol Set items, own-organization symbol or not.
# This is the one authority nuance WP11.1 itself did not exercise with a
# non-admin actor. ---

def test_symbol_reviewer_non_admin_cannot_mutate_symbol_set_items_even_within_their_own_organization(adversarial):
    fixtures = adversarial
    admin_client = fixtures.clients["org_a_admin"]
    reviewer_client = fixtures.clients["org_a_reviewer"]
    actor_a_reviewer = _actor(fixtures.org_a_reviewer_id, fixtures.org_a_id, base_role="user", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _approved_private_symbol(fixtures, actor_a_reviewer, name="WP11.3 Reviewer Private Symbol")

    set_id = _create_active_set(admin_client, _unique_set_code("REVSET"))
    current = reviewer_client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items")
    assert current.status_code == 200, current.text
    response = reviewer_client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 0}], "etag": current.json()["etag"]},
    )
    assert response.status_code == 403, response.text

    admin_response = admin_client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 0}], "etag": current.json()["etag"]},
    )
    assert admin_response.status_code == 200, admin_response.text
    assert admin_response.json()["items"][0]["governedSymbolId"] == str(symbol_id)


def test_org_a_admin_cannot_add_a_different_organizations_approved_private_symbol(adversarial):
    fixtures = adversarial
    admin_client = fixtures.clients["org_a_admin"]
    actor_b_reviewer = _actor(fixtures.org_b_reviewer_id, fixtures.org_b_id, base_role="user", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _approved_private_symbol(fixtures, actor_b_reviewer, name="WP11.3 Org B Private Symbol")

    set_id = _create_active_set(admin_client, _unique_set_code("REVSETB"))
    current = admin_client.get(f"/api/v1/org/me/symbol-sets/{set_id}/items")
    response = admin_client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/items",
        json={"items": [{"governedSymbolId": str(symbol_id), "sortOrder": 0}], "etag": current.json()["etag"]},
    )
    assert response.status_code == 409, response.text


# --- Gap 2: cross-tenant list/detail/items/builder-search/palette 404s for
# a plain Organization User (not just Admin) across the two organizations. ---

@pytest.mark.parametrize("route_template", [
    "/api/v1/org/me/symbol-sets/{set_id}",
    "/api/v1/org/me/symbol-sets/{set_id}/items",
    "/api/v1/org/me/symbol-sets/{set_id}/projects",
])
def test_org_b_user_cannot_reach_org_as_symbol_set_by_id(adversarial, route_template):
    fixtures = adversarial
    set_id = _create_active_set(fixtures.clients["org_a_admin"], _unique_set_code("XORGSET"))
    response = fixtures.clients["org_b_user"].get(route_template.format(set_id=set_id))
    assert response.status_code == 404, response.text


# --- Gap 3: Platform Admin's platform-level authority must not leak into
# org-scoped session routes for an organization Platform Admin does not
# hold an organization membership in. Platform Admin's own session here is
# bound to the protected `symgov` organization, not org A/B. ---

def test_platform_admin_org_scoped_session_cannot_reach_another_organizations_symbol_set(adversarial):
    fixtures = adversarial
    set_id = _create_active_set(fixtures.clients["org_a_admin"], _unique_set_code("PLATSET"))
    response = fixtures.clients["platform_admin"].get(f"/api/v1/org/me/symbol-sets/{set_id}")
    assert response.status_code == 404, response.text


def test_platform_admin_still_has_its_own_documented_cross_organization_platform_routes(adversarial):
    """Positive control: proves the denial above is really about org-scoped
    session routes, not that this Platform Admin session is broken -- the
    same principal must still reach the platform-level, cross-organization
    routes it is actually authorized for."""
    fixtures = adversarial
    response = fixtures.clients["platform_admin"].get(f"/api/v1/platform/organizations/{fixtures.org_a_id}/members")
    assert response.status_code == 200, response.text


# --- Gap 4: an inactive organization membership is rejected at the real
# HTTP login/session layer, not just proven at the service layer. ---

def test_inactive_organization_membership_falls_back_to_a_personal_session_and_cannot_reach_organization_a(adversarial):
    """An inactive membership is not an active one: login resolves to a
    zero-active-membership personal session rather than binding the user
    into organization A, and that personal session cannot reach any
    organization-scoped route for organization A."""
    fixtures = adversarial
    inactive_client = fixtures.clients["org_a_inactive"]
    login_response = _login(inactive_client, fixtures.org_a_inactive_email)
    body = login_response.json()
    assert body["user"]["session"]["mode"] == "personal"
    assert body["user"]["session"]["activeOrganizationId"] is None
    assert body["user"]["organization"] is None

    response = inactive_client.get("/api/v1/org/me/symbol-sets")
    assert response.status_code in (401, 403, 404), response.text


# --- Gap 5: the API-key principal is confined to the public Catalog and
# never reaches any organization-scoped route -- the acceptance-checklist
# claim "API-key Catalog behavior remains public-only unless separately
# specified" (programme plan line 1052), proven at the route layer rather
# than assumed.
#
# Correction from WP11.6's independent review: every organization-scoped
# route (`stage4_authorization.require_stage4_principal`, used by
# `/org/me/*`/`/organization-symbols/*`) authenticates *only* from
# `request.cookies["symgov_session"]` -- it never inspects an
# `Authorization` header at all, so `test_..._never_reaches_...` below
# passes for the structural reason that this session-only client never
# logged in, not because of any API-key-specific discrimination logic (it
# would 401 identically with the Authorization header removed entirely).
# The genuinely API-key-specific proof is the positive-control test:
# `/api/v1/catalog/symbols` is gated by
# `catalog_api_auth.require_catalog_scope`, which *does* look up the
# `CatalogApiKey` row by hashed bearer token -- confirmed directly (a
# request with no Authorization header 401s; one with a syntactically
# valid but unknown bearer token is rejected by the same lookup) before
# writing this test, not assumed from the route's name. ---

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/org/me"),
    ("GET", "/api/v1/org/me/symbol-sets"),
    ("GET", "/api/v1/org/me/projects"),
    ("GET", "/api/v1/organization-symbols"),
])
def test_a_session_less_api_key_client_never_reaches_any_organization_scoped_route(adversarial, method, path):
    """Structural, not API-key-specific: these routes authenticate only
    from the session cookie and never inspect the Authorization header at
    all, so an API-key client (which never logs in) is indistinguishable
    here from any other caller with no session. Real proof that the
    Catalog API-key mechanism itself is confined to the Catalog surface is
    the positive-control test below, which does exercise the CatalogApiKey
    row this fixture creates."""
    response = adversarial.clients["api_key"].request(method, path)
    assert response.status_code in (401, 403), response.text


def test_the_seeded_api_key_actually_authenticates_the_catalog_route_it_is_scoped_for(adversarial):
    """Genuinely exercises the `CatalogApiKey` row `_api_key()` creates:
    `/api/v1/catalog/symbols` is gated by
    `catalog_api_auth.require_catalog_scope`, which looks up the key by
    hashed bearer token -- confirmed to actually require a valid key
    (not merely open) by checking, outside this fixture, that the same
    route 401s with no Authorization header and is rejected with an
    unknown bearer token."""
    response = adversarial.clients["api_key"].get("/api/v1/catalog/symbols")
    assert response.status_code == 200, response.text
