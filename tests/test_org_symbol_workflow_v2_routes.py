"""Unified submission & review WP2 route layer: the new endpoints are absent
while `organization_symbol_workflow_v2_enabled` is off, and with it on the read
model and the edit/withdraw/history round trip work through the real app.

Trigger and constraint behaviour is proven against PostgreSQL in
`test_org_symbol_workflow_v2_postgresql.py`; this file uses the in-memory
SQLite client from `test_wp55_organization_symbols_api.py`.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.settings import get_settings  # noqa: E402
from test_wp55_organization_symbols_api import _add_membership, _build_client, _login  # noqa: E402

DRAFT = {"name": "Fire hydrant", "category": "fire", "discipline": "civil", "summary": "A fire hydrant symbol."}


def _client(*, workflow_v2: bool, base_role: str = "user"):
    client, Session, user_id = _build_client()
    settings = client.app.dependency_overrides[get_settings]()
    client.app.dependency_overrides[get_settings] = lambda: replace(
        settings, organization_symbol_workflow_v2_enabled=workflow_v2
    )
    _add_membership(Session, user_id, "acme", base_role=base_role, capabilities=("contributor", "symbol_reviewer"))
    assert _login(client).status_code == 200
    draft = client.post("/api/v1/organization-symbols", json=DRAFT).json()
    return client, draft["id"], draft["currentRevisionId"]


def test_the_new_endpoints_are_absent_while_the_flag_is_off():
    client, symbol_id, revision_id = _client(workflow_v2=False)
    base = f"/api/v1/organization-symbols/{symbol_id}"
    assert client.patch(f"{base}/revisions/{revision_id}", json={"name": "x"}).status_code == 404
    assert client.delete(f"{base}/revisions/{revision_id}/assets/{revision_id}").status_code == 404
    assert client.post(f"{base}/review-submissions/{revision_id}/withdraw").status_code == 404
    assert client.get(f"{base}/history").status_code == 404
    assert client.delete(f"{base}/pending-revision").status_code == 404
    assert client.get("/api/v1/organization-symbols/review-policy").status_code == 404
    assert client.get("/api/v1/auth/me").json()["user"]["capabilities"]["organizationSymbolWorkflowV2Enabled"] is False


def test_edit_withdraw_and_history_round_trip_with_the_flag_on():
    client, symbol_id, revision_id = _client(workflow_v2=True)
    base = f"/api/v1/organization-symbols/{symbol_id}"
    assert client.get("/api/v1/auth/me").json()["user"]["capabilities"]["organizationSymbolWorkflowV2Enabled"] is True

    edited = client.patch(f"{base}/revisions/{revision_id}", json={"name": "Pillar hydrant", "keywords": ["hydrant"]})
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["canonicalName"] == "Pillar hydrant"
    assert body["currentRevision"]["keywords"] == ["hydrant"]
    assert body["currentRevision"]["category"] == "fire"
    assert body["pendingRevision"] is None

    unknown = client.patch(f"{base}/revisions/{revision_id}", json={"discipline": "Astrology"})
    assert unknown.status_code == 400

    submission_id = client.post(f"{base}/revisions/{revision_id}/submit", json={"rationale": "Ready."}).json()["id"]
    assert client.patch(f"{base}/revisions/{revision_id}", json={"name": "Late"}).status_code == 400

    withdrawn = client.post(f"{base}/review-submissions/{submission_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert (withdrawn.json()["status"], withdrawn.json()["closureReason"]) == ("closed", "withdrawn")
    assert client.get(base).json()["currentRevision"]["lifecycleState"] == "draft"

    history = client.get(f"{base}/history")
    assert history.status_code == 200
    [entry] = history.json()["items"]
    assert (entry["outcome"], entry["submissionRationale"], entry["submittedByName"]) == ("withdrawn", "Ready.", "Member")


def test_every_member_reads_the_review_policy_and_only_a_stepped_up_admin_changes_it():
    member, _, _ = _client(workflow_v2=True)
    policy = member.get("/api/v1/organization-symbols/review-policy")
    assert policy.status_code == 200, policy.text
    assert (policy.json()["allowSelfApproval"], policy.json()["updatedAt"]) == (True, None)
    assert member.put("/api/v1/organization-symbols/review-policy", json={"allowSelfApproval": False}).status_code == 403

    admin, _, _ = _client(workflow_v2=True, base_role="admin")
    assert admin.put("/api/v1/organization-symbols/review-policy", json={"allowSelfApproval": False}).status_code == 403
    admin.post("/api/v1/auth/reauthenticate", json={"pin": "1234"})
    changed = admin.put("/api/v1/organization-symbols/review-policy", json={"allowSelfApproval": False})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["allowSelfApproval"], changed.json()["updatedByName"]) == (False, "Member")
    assert admin.get("/api/v1/organization-symbols/review-policy").json()["allowSelfApproval"] is False
