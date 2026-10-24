"""Unified submission & review WP3: a Platform Admin reads and changes any
organization's review policy, behind step-up and the workflow v2 flag."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.models import OrganizationReviewPolicy  # noqa: E402
from symgov_backend.settings import get_settings  # noqa: E402
from test_platform_organizations_api import (  # noqa: E402
    _build_client,
    _login_and_select_org,
    _seed_commercial_org,
    _seed_symgov_org_with_platform_admin,
    _step_up,
)


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    # AuditEvent's JSONB default cannot be created on SQLite; PostgreSQL tests cover the audit row.
    with patch("symgov_backend.organization_review_policy._emit_audit") as emitted:
        yield emitted


def _world(*, workflow_v2=True):
    client, Session, admin_id, _, _ = _build_client()
    OrganizationReviewPolicy.__table__.create(Session.kw["bind"])
    settings = client.app.dependency_overrides[get_settings]()
    client.app.dependency_overrides[get_settings] = lambda: replace(
        settings, organization_symbol_workflow_v2_enabled=workflow_v2
    )
    symgov_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    acme_id = _seed_commercial_org(Session, code="ACME")
    return client, symgov_id, acme_id


def _url(org_id):
    return f"/api/v1/platform/organizations/{org_id}/review-policy"


def test_a_platform_admin_changes_an_organizations_policy_after_step_up(_stub_emit_audit):
    client, symgov_id, acme_id = _world()
    _login_and_select_org(client, "platform-admin@example.test", symgov_id)
    assert client.get(_url(acme_id)).json()["allowSelfApproval"] is True
    assert client.put(_url(acme_id), json={"allowSelfApproval": False}).status_code == 403

    _step_up(client)
    changed = client.put(_url(acme_id), json={"allowSelfApproval": False})
    assert changed.status_code == 200, changed.text
    assert changed.json()["allowSelfApproval"] is False
    assert client.get(_url(acme_id)).json()["allowSelfApproval"] is False
    assert _stub_emit_audit.call_args.kwargs["action"] == "org_review_policy.updated"
    assert _stub_emit_audit.call_args.kwargs["payload"]["effective_authority"] == "platform_admin"


def test_the_policy_routes_are_absent_while_the_flag_is_off_and_refused_to_others():
    client, symgov_id, acme_id = _world(workflow_v2=False)
    _login_and_select_org(client, "platform-admin@example.test", symgov_id)
    assert client.get(_url(acme_id)).status_code == 404

    client, _, acme_id = _world()
    client.post("/api/v1/auth/login", json={"email": "plain@example.test", "pin": "1234"})
    assert client.get(_url(acme_id)).status_code == 403
