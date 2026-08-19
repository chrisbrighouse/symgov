"""Tests for the Organization Admin API (Stage 3, Slices 3A and icon upload)."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import upsert_user
from symgov_backend.dependencies import get_db_session
from symgov_backend.routes.organizations import get_icon_storage_bridge
from symgov_backend.models import (
    AuthLoginAttemptEvent,
    AuthLoginThrottleBucket,
    AuthOrganizationSelectionChallenge,
    AuthThrottleRecoveryEvent,
    Organization,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    PlatformRoleAssignment,
    SubscriptionEvent,
    User,
    UserRole,
    UserSession,
    UserSubscription,
)
from symgov_backend.settings import SymgovAPISettings, get_settings


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    """AuditEvent uses JSONB (PostgreSQL-only). Stub _emit_audit in all tests by default.
    Audit-specific tests override this by patching explicitly and capturing calls."""
    with patch("symgov_backend.organization_service._emit_audit"):
        yield


def _create_tables(engine) -> None:
    for table in (
        User.__table__,
        UserRole.__table__,
        Organization.__table__,
        OrganizationMembership.__table__,
        OrganizationRoleAssignment.__table__,
        OrganizationMemberCapability.__table__,
        PlatformRoleAssignment.__table__,
        UserSession.__table__,
        AuthOrganizationSelectionChallenge.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
    ):
        original = table.constraints
        try:
            table.constraints = {
                item
                for item in original
                if not (
                    isinstance(item, CheckConstraint)
                    and ("~" in str(item.sqltext) or "interval" in str(item.sqltext))
                )
            }
            table.create(engine)
        finally:
            table.constraints = original
    # Drop PostgreSQL-specific partial unique indexes that SQLite degrades to full unique indexes.
    # uq_org_role_active_membership: UNIQUE on membership_id WHERE is_active=true.
    # In SQLite it becomes UNIQUE on membership_id (all rows), blocking replace_membership_base_role.
    with engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_org_role_active_membership"))
        conn.execute(text("DROP INDEX IF EXISTS uq_org_capability_active_membership"))
        conn.commit()


def _build_engine_and_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _create_tables(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _build_client(*, pilots=(), org_admin_enabled=True, organizations_enabled=True):
    Session = _build_engine_and_session()
    with Session() as session:
        admin_user = upsert_user(
            session,
            email="admin@example.test",
            display_name="Org Admin",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        member_user = upsert_user(
            session,
            email="member@example.test",
            display_name="Org Member",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        other_user = upsert_user(
            session,
            email="other@example.test",
            display_name="Other User",
            roles=[],
            pin="1234",
            must_change_pin=False,
        )
        session.commit()
        admin_id = admin_user.id
        member_id = member_user.id
        other_id = other_user.id

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    settings = SymgovAPISettings(
        organizations_enabled=organizations_enabled,
        organization_admin_enabled=org_admin_enabled,
        symbol_sets_enabled=False,
        organization_symbols_enabled=False,
        organization_agents_enabled=False,
        organization_pilot_codes=pilots,
    )
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    fake_bridge = _FakeStorageBridge()
    app.dependency_overrides[get_icon_storage_bridge] = lambda: fake_bridge
    client = TestClient(app, headers={"origin": "http://testserver"}, raise_server_exceptions=False)
    client.fake_bridge = fake_bridge
    return client, Session, admin_id, member_id, other_id


class _FakeStorageBridge:
    """Stand-in for RuntimePersistenceBridge.upload_object_bytes in tests.

    Object storage is a real external S3-compatible service; tests must not
    perform network I/O. Uploaded bytes are captured so GET /org/me/icon tests
    can serve them back through a patched download_object_bytes.
    """

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def upload_object_bytes(self, *, object_key, payload, content_type, env_file=None):
        self.objects[object_key] = (payload, content_type)
        return {"object_key": object_key, "size_bytes": len(payload)}


def _make_png_bytes(width=64, height=64, color=(10, 20, 30)) -> bytes:
    image = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _add_org_with_members(Session, admin_id, member_id=None, *, code="ACME"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        org = Organization(
            id=uuid.uuid4(),
            code=code,
            normalized_code=code.lower(),
            display_name=f"{code} Corp",
            name_key=f"{code.lower()}-corp",
            legal_name=f"{code} Legal Ltd",
            legal_name_key=f"{code.lower()}-legal-ltd",
            entitlement_status="active",
            is_active=True,
            is_protected=False,
            fallback_icon_svg="<svg><text>A</text></svg>",
            created_at=now,
            updated_at=now,
        )
        admin_membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=admin_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([org, admin_membership])
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=admin_membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            )
        )
        member_membership_id = None
        if member_id:
            member_membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=org.id,
                user_id=member_id,
                status="active",
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(member_membership)
            session.flush()
            session.add(
                OrganizationRoleAssignment(
                    id=uuid.uuid4(),
                    membership_id=member_membership.id,
                    base_role="user",
                    is_active=True,
                    assigned_at=now,
                )
            )
            member_membership_id = member_membership.id
        session.commit()
        return org.id, admin_membership.id, member_membership_id


def _login_and_select_org(client, email, org_id):
    client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    client.post("/api/v1/auth/organizations/select", json={"organizationId": str(org_id)})


def _step_up(client):
    client.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})


def _patch_download_from_fake_bridge(client):
    def fake_download(*, object_key, env_file=None):
        payload, content_type = client.fake_bridge.objects[object_key]
        return {"payload": payload, "content_type": content_type, "object_key": object_key}

    return patch("symgov_backend.routes.organizations.download_object_bytes", side_effect=fake_download)


# --- Feature flag ---

def test_feature_flag_off_returns_404():
    client, Session, admin_id, _, _ = _build_client(
        pilots=("acme",), org_admin_enabled=False
    )
    _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", uuid.uuid4())
    response = client.get("/api/v1/org/me")
    assert response.status_code == 404


# --- GET /org/me ---

def test_get_org_detail_requires_org_session():
    # member_user has no org membership → login yields personal session
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    _add_org_with_members(Session, admin_id)  # admin_id added to ACME, member_id has no memberships
    client.post("/api/v1/auth/login", json={"email": "member@example.test", "pin": "1234"})

    response = client.get("/api/v1/org/me")

    assert response.status_code == 403


def test_get_org_detail_returns_current_org():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.get("/api/v1/org/me")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == "ACME"
    assert data["displayName"] == "ACME Corp"
    assert data["legalName"] == "ACME Legal Ltd"
    assert data["entitlementStatus"] == "active"
    assert data["isActive"] is True
    assert data["isProtected"] is False
    assert data["iconUrl"] == "/api/v1/org/me/icon"


def test_get_org_detail_unauthenticated_returns_401():
    client, _, _, _, _ = _build_client(pilots=("acme",))
    response = client.get("/api/v1/org/me")
    assert response.status_code == 401


# --- PATCH /org/me ---

def test_patch_org_requires_org_admin_role():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)
    _step_up(client)

    response = client.patch("/api/v1/org/me", json={"displayName": "New Name"})

    assert response.status_code == 403


def test_patch_org_requires_step_up():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.patch("/api/v1/org/me", json={"displayName": "New Name"})

    assert response.status_code == 403


def test_patch_org_updates_display_name():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.patch("/api/v1/org/me", json={"displayName": "Updated Corp Name"})

    assert response.status_code == 200
    assert response.json()["displayName"] == "Updated Corp Name"


def test_patch_org_audit_event_emitted():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        client.patch("/api/v1/org/me", json={"displayName": "Audited Corp"})

    assert any(e["action"] == "organization.updated" for e in emitted)


# --- GET /org/me/members ---

def test_list_members_returns_paginated_list():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.get("/api/v1/org/me/members")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    emails = {item["email"] for item in data["items"]}
    assert "admin@example.test" in emails
    assert "member@example.test" in emails


def test_list_members_pagination():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.get("/api/v1/org/me/members?pageSize=1")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1


def test_list_members_tenant_isolation():
    """Org member can only see their own org's members, not another org's."""
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme", "other"))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    other_org_id, _, _ = _add_org_with_members(Session, other_id, code="OTHER")
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.get("/api/v1/org/me/members")

    assert response.status_code == 200
    emails = {item["email"] for item in response.json()["items"]}
    assert "other@example.test" not in emails
    assert "admin@example.test" in emails


def test_list_members_accessible_to_org_user():
    """Non-admin org members can also list members."""
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)

    response = client.get("/api/v1/org/me/members")

    assert response.status_code == 200


# --- POST /org/me/members ---

def test_add_member_requires_org_admin():
    client, Session, admin_id, member_id, other_id = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)
    _step_up(client)

    response = client.post(
        "/api/v1/org/me/members",
        json={"userId": str(other_id), "baseRole": "user"},
    )

    assert response.status_code == 403


def test_add_member_requires_step_up():
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/members",
        json={"userId": str(other_id), "baseRole": "user"},
    )

    assert response.status_code == 403


def test_add_member_success():
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.post(
        "/api/v1/org/me/members",
        json={"userId": str(other_id), "baseRole": "user"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["userId"] == str(other_id)
    assert data["baseRole"] == "user"
    assert data["status"] == "active"


def test_add_member_duplicate_rejected():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.post(
        "/api/v1/org/me/members",
        json={"userId": str(member_id), "baseRole": "user"},
    )

    assert response.status_code == 400


def test_add_member_audit_event():
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        client.post("/api/v1/org/me/members", json={"userId": str(other_id), "baseRole": "user"})

    assert any(e["action"] == "membership.added" for e in emitted)


# --- PATCH /org/me/members/{membership_id} ---

def test_patch_member_role_requires_step_up():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.patch(
        f"/api/v1/org/me/members/{member_mid}",
        json={"baseRole": "admin"},
    )

    assert response.status_code == 403


def test_patch_member_promotes_to_admin():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.patch(
        f"/api/v1/org/me/members/{member_mid}",
        json={"baseRole": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["baseRole"] == "admin"


def test_patch_member_grants_capability():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.patch(
        f"/api/v1/org/me/members/{member_mid}",
        json={"grantCapability": "contributor"},
    )

    assert response.status_code == 200
    caps = [c["capability"] for c in response.json()["capabilities"]]
    assert "contributor" in caps


def test_patch_member_tenant_isolation():
    """Org admin cannot patch a member from a different org."""
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme", "other"))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    other_org_id, _, other_mid = _add_org_with_members(Session, other_id, code="OTHER")
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.patch(
        f"/api/v1/org/me/members/{other_mid}",
        json={"baseRole": "user"},
    )

    assert response.status_code == 404


def test_patch_member_last_admin_protection():
    """Cannot demote the last org admin."""
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, admin_mid, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.patch(
        f"/api/v1/org/me/members/{admin_mid}",
        json={"baseRole": "user"},
    )

    assert response.status_code == 400
    assert "last" in response.json()["detail"].lower()


def test_patch_member_role_audit_event():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        client.patch(f"/api/v1/org/me/members/{member_mid}", json={"baseRole": "admin"})

    assert any(e["action"] == "membership.base_role_replaced" for e in emitted)


# --- DELETE /org/me/members/{membership_id} ---

def test_delete_member_requires_step_up():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.delete(f"/api/v1/org/me/members/{member_mid}")

    assert response.status_code == 403


def test_delete_member_success():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.delete(f"/api/v1/org/me/members/{member_mid}")

    assert response.status_code == 204


def test_delete_last_admin_rejected():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, admin_mid, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.delete(f"/api/v1/org/me/members/{admin_mid}")

    assert response.status_code == 400


def test_delete_member_tenant_isolation():
    client, Session, admin_id, _, other_id = _build_client(pilots=("acme", "other"))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    other_org_id, other_mid, _ = _add_org_with_members(Session, other_id, code="OTHER")
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    response = client.delete(f"/api/v1/org/me/members/{other_mid}")

    assert response.status_code == 404


def test_delete_member_audit_event():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, member_mid = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    _step_up(client)

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        client.delete(f"/api/v1/org/me/members/{member_mid}")

    assert any(e["action"] == "membership.deactivated" for e in emitted)


# --- GET /org/me/icon ---

def test_get_icon_returns_svg():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.get("/api/v1/org/me/icon")

    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "<svg>" in response.text or "<svg" in response.text


def test_get_icon_requires_org_session():
    # member_user has no org membership → login yields personal session
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    _add_org_with_members(Session, admin_id)
    client.post("/api/v1/auth/login", json={"email": "member@example.test", "pin": "1234"})

    response = client.get("/api/v1/org/me/icon")

    assert response.status_code == 403


def test_get_icon_accessible_to_org_user():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)

    response = client.get("/api/v1/org/me/icon")

    assert response.status_code == 200


def _icon_upload_json(payload_bytes: bytes, content_type: str = "image/png") -> dict:
    import base64 as _base64

    return {"contentType": content_type, "contentBase64": _base64.b64encode(payload_bytes).decode("ascii")}


# --- POST /org/me/icon (custom icon upload) ---

def test_upload_icon_requires_org_admin():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)

    response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))

    assert response.status_code == 403


def test_upload_icon_feature_flag_off_returns_404():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",), org_admin_enabled=False)
    _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", uuid.uuid4())

    response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))

    assert response.status_code == 404


def test_upload_icon_rejects_svg():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/icon",
        json=_icon_upload_json(
            b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", content_type="image/svg+xml"
        ),
    )

    assert response.status_code == 422
    assert "SVG" in response.json()["detail"]


def test_upload_icon_rejects_oversized_base64_body():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    oversized = b"a" * (600 * 1024)
    response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(oversized))

    assert response.status_code == 413


def test_upload_icon_rejects_invalid_base64():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/icon",
        json={"contentType": "image/png", "contentBase64": "not-valid-base64!!"},
    )

    assert response.status_code == 400


def test_upload_icon_rejects_invalid_image_bytes():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(b"not a real png"))

    assert response.status_code == 422


def test_upload_icon_rejects_declared_type_mismatch():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/icon",
        json=_icon_upload_json(_make_png_bytes(), content_type="image/jpeg"),
    )

    assert response.status_code == 422


def test_upload_icon_rejects_dimensions_out_of_bounds():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/icon",
        json=_icon_upload_json(_make_png_bytes(width=8, height=8)),
    )

    assert response.status_code == 422


def test_upload_icon_success_serves_uploaded_bytes_and_sets_flag():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post(
        "/api/v1/org/me/icon",
        json=_icon_upload_json(_make_png_bytes(color=(200, 30, 40))),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["hasCustomIcon"] is True

    with _patch_download_from_fake_bridge(client):
        icon_response = client.get("/api/v1/org/me/icon")
    assert icon_response.status_code == 200
    assert icon_response.headers["content-type"] == "image/png"
    assert icon_response.content != b""


def test_upload_icon_is_rate_limited():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    first = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes(color=(1, 2, 3))))
    assert first.status_code == 200

    second = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes(color=(4, 5, 6))))
    assert second.status_code == 429


def test_upload_icon_blocked_for_protected_organization():
    client, Session, admin_id, _, _ = _build_client(pilots=("symgov",))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        org = Organization(
            id=uuid.uuid4(),
            code="symgov",
            normalized_code="symgov",
            display_name="Symgov Platform",
            name_key="symgov-platform",
            legal_name="Symgov Governance",
            legal_name_key="symgov-governance",
            entitlement_status="active",
            is_active=True,
            is_protected=True,
            fallback_icon_svg="<svg><text>S</text></svg>",
            created_at=now,
            updated_at=now,
        )
        membership = OrganizationMembership(
            id=uuid.uuid4(),
            organization_id=org.id,
            user_id=admin_id,
            status="active",
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([org, membership])
        session.flush()
        session.add(
            OrganizationRoleAssignment(
                id=uuid.uuid4(),
                membership_id=membership.id,
                base_role="admin",
                is_active=True,
                assigned_at=now,
            )
        )
        session.commit()
        org_id = org.id

    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))

    assert response.status_code == 400


def test_upload_icon_audit_event_emitted():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))

    assert response.status_code == 200
    assert any(e["action"] == "organization.icon_uploaded" for e in emitted)
    uploaded_event = next(e for e in emitted if e["action"] == "organization.icon_uploaded")
    assert "content_type" in uploaded_event["payload"]
    assert set(uploaded_event["payload"]) == {"content_type"}


# --- DELETE /org/me/icon ---

def test_remove_icon_without_custom_icon_returns_400():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    response = client.delete("/api/v1/org/me/icon")

    assert response.status_code == 400


def test_remove_icon_requires_org_admin():
    client, Session, admin_id, member_id, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id, member_id)
    _login_and_select_org(client, "member@example.test", org_id)

    response = client.delete("/api/v1/org/me/icon")

    assert response.status_code == 403


def test_remove_icon_reverts_to_fallback():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)

    upload_response = client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))
    assert upload_response.json()["hasCustomIcon"] is True

    delete_response = client.delete("/api/v1/org/me/icon")
    assert delete_response.status_code == 200
    assert delete_response.json()["hasCustomIcon"] is False

    icon_response = client.get("/api/v1/org/me/icon")
    assert icon_response.status_code == 200
    assert "image/svg+xml" in icon_response.headers["content-type"]


def test_remove_icon_audit_event_emitted():
    client, Session, admin_id, _, _ = _build_client(pilots=("acme",))
    org_id, _, _ = _add_org_with_members(Session, admin_id)
    _login_and_select_org(client, "admin@example.test", org_id)
    client.post("/api/v1/org/me/icon", json=_icon_upload_json(_make_png_bytes()))

    emitted = []
    with patch("symgov_backend.organization_service._emit_audit", side_effect=lambda *a, **kw: emitted.append(kw)):
        response = client.delete("/api/v1/org/me/icon")

    assert response.status_code == 200
    assert any(e["action"] == "organization.icon_removed" for e in emitted)
