"""Spec section 10.1: Ed opens only to named pilot organizations.

The gate is an organization-code allowlist read from
SYMGOV_ED_PILOT_ORGANIZATION_CODES. Unset or empty means Ed is absent for
everyone, so shipping this code changes nothing until an operator names an
organization.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.settings import SymgovAPISettings, get_settings


SAFE_HEADERS = {"origin": "http://testserver"}
PROMPT = {"prompt": "Explain Symgov projects"}


def _user(*, organization_code: str | None) -> AuthenticatedUser:
    organization_id = str(uuid.uuid4()) if organization_code else None
    return AuthenticatedUser(
        id=str(uuid.uuid4()),
        email="ed-pilot@example.invalid",
        display_name="Ed Pilot",
        roles=("reviewer",),
        must_change_pin=False,
        subscription_tier="plus",
        session_mode="organization" if organization_code else "personal",
        active_organization_id=organization_id,
        organization_code=organization_code,
        organization_display_name="Pilot Org" if organization_code else None,
        organization_base_role="user" if organization_code else None,
    )


@pytest.fixture
def provider(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(ed_orchestration, "_USER_RATE_LIMITS", {})
    monkeypatch.setattr(ed_orchestration, "_ORG_RATE_LIMITS", {})
    mock = MagicMock(
        return_value={
            "provider": "openrouter",
            "model": "openai/gpt-5-mini",
            "outputText": '{"status":"answered","answer":"Ed answer","tool_calls":[]}',
            "latencyMs": 10,
            "usage": {"total_tokens": 7},
        }
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", mock)
    return mock


def _client(user: AuthenticatedUser, pilot_codes: tuple[str, ...]) -> TestClient:
    app = create_app()
    settings = SymgovAPISettings(ed_pilot_organization_codes=pilot_codes)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    return TestClient(app, headers=SAFE_HEADERS)


def test_default_settings_leave_ed_absent(monkeypatch):
    monkeypatch.delenv("SYMGOV_ED_PILOT_ORGANIZATION_CODES", raising=False)

    assert SymgovAPISettings().ed_pilot_organization_codes == ()


def test_pilot_codes_are_read_per_instance_and_normalized(monkeypatch):
    monkeypatch.setenv("SYMGOV_ED_PILOT_ORGANIZATION_CODES", " ACME, bsco ,,Crwl ")

    assert SymgovAPISettings().ed_pilot_organization_codes == ("acme", "bsco", "crwl")


def test_empty_allowlist_is_404_and_never_reaches_the_provider(provider):
    response = _client(_user(organization_code="ACME"), ()).post("/api/v1/ed/chat", json=PROMPT)

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    provider.assert_not_called()


def test_listed_organization_reaches_ed_case_insensitively(provider):
    response = _client(_user(organization_code="ACME"), ("acme",)).post(
        "/api/v1/ed/chat", json=PROMPT
    )

    assert response.status_code == 200
    provider.assert_called_once()


def test_unlisted_organization_is_404(provider):
    response = _client(_user(organization_code="BSCO"), ("acme",)).post(
        "/api/v1/ed/chat", json=PROMPT
    )

    assert response.status_code == 404
    provider.assert_not_called()


def test_personal_session_is_outside_every_pilot(provider):
    response = _client(_user(organization_code=None), ("acme",)).post(
        "/api/v1/ed/chat", json=PROMPT
    )

    assert response.status_code == 404
    provider.assert_not_called()


def test_gate_runs_before_body_validation(provider):
    """An unlisted caller learns nothing from a malformed body."""
    response = _client(_user(organization_code="BSCO"), ("acme",)).post(
        "/api/v1/ed/chat", json={"prompt": "", "unexpected": True}
    )

    assert response.status_code == 404


def test_gate_does_not_count_against_the_rate_limit(provider):
    from symgov_backend.services import ed_orchestration

    user = _user(organization_code="BSCO")
    client = _client(user, ("acme",))
    for _ in range(3):
        assert client.post("/api/v1/ed/chat", json=PROMPT).status_code == 404

    assert user.id not in ed_orchestration._USER_RATE_LIMITS


@pytest.mark.parametrize(
    ("organization_code", "pilot_codes", "expected"),
    [("ACME", ("acme",), True), ("BSCO", ("acme",), False), (None, ("acme",), False), ("ACME", (), False)],
)
def test_the_session_tells_the_ui_whether_ed_is_open(organization_code, pilot_codes, expected):
    """Stage 5 shows the Ed entry only when the same gate would let the request through."""
    from symgov_backend.routes.auth import auth_user_response

    response = auth_user_response(
        _user(organization_code=organization_code),
        SymgovAPISettings(ed_pilot_organization_codes=pilot_codes),
    )

    assert response.capabilities["edEnabled"] is expected


def test_a_credential_change_session_is_outside_the_pilot():
    from dataclasses import replace

    from symgov_backend.routes.ed import ed_pilot_allows

    user = replace(_user(organization_code="ACME"), session_purpose="credential_change")

    assert ed_pilot_allows(user, SymgovAPISettings(ed_pilot_organization_codes=("acme",))) is False
