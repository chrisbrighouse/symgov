from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser, upsert_user
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.routes.ed import require_ed_pilot
from symgov_backend.models import (
    AuthLoginAttemptEvent,
    AuthLoginThrottleBucket,
    AuthThrottleRecoveryEvent,
    SubscriptionEvent,
    User,
    UserRole,
    UserSession,
    UserSubscription,
)
from symgov_backend.subscriptions import upgrade_to_plus


SAFE_HEADERS = {"origin": "http://testserver"}

CANONICAL_OPERATION_FORMS = {
    "create": ("create", "creates", "creating", "created", "created"),
    "add": ("add", "adds", "adding", "added", "added"),
    "edit": ("edit", "edits", "editing", "edited", "edited"),
    "update": ("update", "updates", "updating", "updated", "updated"),
    "modify": ("modify", "modifies", "modifying", "modified", "modified"),
    "change": ("change", "changes", "changing", "changed", "changed"),
    "delete": ("delete", "deletes", "deleting", "deleted", "deleted"),
    "remove": ("remove", "removes", "removing", "removed", "removed"),
    "reset": ("reset", "resets", "resetting", "reset", "reset"),
    "destroy": ("destroy", "destroys", "destroying", "destroyed", "destroyed"),
    "approve": ("approve", "approves", "approving", "approved", "approved"),
    "publish": ("publish", "publishes", "publishing", "published", "published"),
    "withdraw": ("withdraw", "withdraws", "withdrawing", "withdrew", "withdrawn"),
    "govern": ("govern", "governs", "governing", "governed", "governed"),
    "send": ("send", "sends", "sending", "sent", "sent"),
    "cause": ("cause", "causes", "causing", "caused", "caused"),
    "navigate": ("navigate", "navigates", "navigating", "navigated", "navigated"),
    "select": ("select", "selects", "selecting", "selected", "selected"),
    "save": ("save", "saves", "saving", "saved", "saved"),
    "initiate": ("initiate", "initiates", "initiating", "initiated", "initiated"),
    "administer": (
        "administer",
        "administers",
        "administering",
        "administered",
        "administered",
    ),
}

OPERATION_REQUESTS = [
    "Create a Symgov project.",
    "Please add a Symgov user.",
    "Start editing this Symgov symbol set.",
    "Please ensure Ed updates my Symgov profile.",
    "Please have this Symgov classification modified.",
    "Change my Symgov role to admin.",
    "Delete the Symgov symbol set.",
    "Please remove this Symgov project.",
    "Please have the Symgov security setting reset.",
    "Start destroying this Symgov record.",
    "Approve this Symgov standard.",
    "Please have this Symgov standard published.",
    "Please ensure Ed withdraws this Symgov standard.",
    "Please govern this Symgov approval action.",
    "Send an external Symgov message.",
    "Please cause an external Symgov side effect.",
    "Navigate to the Symgov project.",
    "Please have this Symgov symbol set selected.",
    "Save this Symgov preference.",
    "Please initiate the Symgov workflow.",
    "Please administer the Symgov users and roles.",
    "Start publishing this Symgov standard.",
]

PROVIDER_ACTION_CLAIMS = [
    f"I {forms[3]} the Symgov record for you."
    for forms in CANONICAL_OPERATION_FORMS.values()
] + [
    "I did delete the Symgov symbol set.",
    "I do delete the Symgov symbol set.",
    "I already did delete the Symgov symbol set.",
    "I did already delete the Symgov symbol set.",
    "I already have deleted the Symgov symbol set.",
    "I have already deleted the Symgov symbol set.",
    "Ed has successfully published the Symgov standard.",
    "The Symgov symbol set was deleted.",
    "The Symgov roles were already changed.",
    "The Symgov symbol set is deleted.",
    "The Symgov roles are changed.",
    "The Symgov record has been removed.",
    "The Symgov workflow had just been initiated.",
    "I am publishing the Symgov standard.",
    "Ed is sending an external Symgov message.",
    "The Symgov symbol set is being deleted.",
]

CAUSATIVE_AND_VOCATIVE_OPERATION_REQUESTS = [
    "Make Ed delete this Symgov symbol set.",
    "Have Ed publish this Symgov standard.",
    "Get Ed to send an external Symgov message.",
    "Let Ed change my Symgov role.",
    "Ed, delete this Symgov symbol set.",
    "Ed, please publish this Symgov standard.",
]

INTERVENING_ADVERB_ACTION_CLAIMS = [
    "I definitely did delete the Symgov symbol set.",
    "I did definitely delete the Symgov symbol set.",
    "I explicitly have deleted the Symgov symbol set.",
    "I have explicitly deleted the Symgov symbol set.",
    "The Symgov standard was automatically published.",
    "The Symgov message has securely been sent.",
    "Ed is actively publishing the Symgov standard.",
]


def _provider_result(
    *,
    answer: str = "Ed answer",
    status: str = "answered",
    tool_calls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "provider": "openrouter",
        "model": "openai/gpt-5-mini",
        "outputText": json.dumps(
            {
                "status": status,
                "answer": answer,
                "tool_calls": tool_calls or [],
            }
        ),
        "latencyMs": 10,
        "usage": {"total_tokens": 7},
    }


def _user(
    *,
    user_id: str | None = None,
    organization_id: str | None = None,
    roles: tuple[str, ...] = ("reviewer",),
    base_role: str | None = None,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id or str(uuid.uuid4()),
        email="ed-user@example.invalid",
        display_name="Ed User",
        roles=roles,
        must_change_pin=False,
        subscription_tier="plus",
        session_mode="organization" if organization_id else "personal",
        active_organization_id=organization_id,
        organization_code="ACME" if organization_id else None,
        organization_display_name="Acme Engineering" if organization_id else None,
        organization_base_role=base_role,
    )


def _override_client(user: AuthenticatedUser, session: object | None = None) -> TestClient:
    app = create_app()
    # The pilot gate has its own suite (test_ed_pilot_gate.py); these tests
    # exercise what sits behind it.
    app.dependency_overrides[require_ed_pilot] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: user
    db_session = session if session is not None else MagicMock()
    app.dependency_overrides[get_db_session] = lambda: db_session
    return TestClient(app, headers=SAFE_HEADERS)


def _real_login_client() -> TestClient:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        User.__table__,
        UserRole.__table__,
        UserSession.__table__,
        AuthLoginThrottleBucket.__table__,
        AuthLoginAttemptEvent.__table__,
        AuthThrottleRecoveryEvent.__table__,
        UserSubscription.__table__,
        SubscriptionEvent.__table__,
    ):
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        user = upsert_user(
            session,
            email="ed-real@example.invalid",
            display_name="Real Ed User",
            roles=("reviewer",),
            pin="4590",
            must_change_pin=False,
        )
        upgrade_to_plus(session, user, months=12)
        session.commit()

    app = create_app()
    app.dependency_overrides[require_ed_pilot] = lambda: None

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app, headers=SAFE_HEADERS)


@pytest.fixture(autouse=True)
def _clear_rate_limits(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(ed_orchestration, "_USER_RATE_LIMITS", {})
    monkeypatch.setattr(ed_orchestration, "_ORG_RATE_LIMITS", {})


def test_plain_create_app_anonymous_is_401_before_provider_or_tool(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = TestClient(create_app(), headers=SAFE_HEADERS).post(
        "/api/v1/ed/chat",
        json={"prompt": "How does Symgov work?"},
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    provider.assert_not_called()
    tool.assert_not_called()


def test_ed_route_is_v1_only():
    response = TestClient(create_app(), headers=SAFE_HEADERS).post(
        "/api/ed/chat",
        json={"prompt": "How does Symgov work?"},
    )

    assert response.status_code == 404


def test_real_login_session_path_returns_private_success(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(return_value=_provider_result(answer="Symgov governs engineering symbols."))
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "resolve_model_for_feature", lambda feature: "openai/gpt-5-mini")
    client = _real_login_client()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "ed-real@example.invalid", "pin": "4590"},
    )
    assert login.status_code == 200

    response = client.post(
        "/api/v1/ed/chat",
        json={"prompt": "How does Symgov work?"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "answered"
    assert response.json()["readOnly"] is True
    provider.assert_called_once()


def test_request_contract_forbids_scope_and_bounds_question():
    client = _override_client(_user())

    scoped = client.post(
        "/api/v1/ed/chat",
        json={"prompt": "How does Symgov work?", "organizationId": str(uuid.uuid4())},
    )
    oversized = client.post(
        "/api/v1/ed/chat",
        json={"prompt": "x" * 1001},
    )

    assert scoped.status_code == 422
    assert scoped.headers["cache-control"] == "no-store, private"
    assert oversized.status_code == 422
    assert oversized.headers["cache-control"] == "no-store, private"


def test_obvious_mutation_is_refused_before_provider_tool_or_database_work(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    session = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)
    client = _override_client(_user(), session)

    response = client.post(
        "/api/v1/ed/chat",
        json={"prompt": "Delete the ACME symbol set now"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()
    assert session.mock_calls == []


def test_change_role_is_refused_before_provider_tool_or_database_work(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    session = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user(), session).post(
        "/api/v1/ed/chat",
        json={"prompt": "Change my Symgov role to admin"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()
    assert session.mock_calls == []


def test_operation_lexicon_has_all_audited_forms():
    from symgov_backend.services import ed_orchestration

    assert ed_orchestration._OPERATION_FORMS == CANONICAL_OPERATION_FORMS


@pytest.mark.parametrize("prompt", OPERATION_REQUESTS)
def test_full_operation_lexicon_requests_are_refused_before_any_work(monkeypatch, prompt):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    session = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user(), session).post(
        "/api/v1/ed/chat",
        json={"prompt": prompt},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()
    assert session.mock_calls == []


@pytest.mark.parametrize("prompt", CAUSATIVE_AND_VOCATIVE_OPERATION_REQUESTS)
def test_causative_and_vocative_requests_are_refused_before_any_work(monkeypatch, prompt):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    session = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user(), session).post(
        "/api/v1/ed/chat",
        json={"prompt": prompt},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()
    assert session.mock_calls == []


@pytest.mark.parametrize(
    "prompt",
    [
        "What makes Ed useful when explaining a Symgov standard?",
        "Does Ed have information about Symgov reviews?",
        "Where can I get help understanding a Symgov submission?",
        "Let me know what Ed can explain about the Symgov catalog.",
        "Ed is the read-only Symgov application guru.",
    ],
)
def test_causative_words_and_ed_in_factual_questions_are_not_overblocked(monkeypatch, prompt):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(return_value=_provider_result(answer="Ed can explain that Symgov topic."))
    tool = MagicMock()
    session = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user(), session).post(
        "/api/v1/ed/chat",
        json={"prompt": prompt},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    provider.assert_called_once()
    tool.assert_not_called()
    assert session.mock_calls == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Can you delete the ACME symbol set?",
        "Could you update my Symgov profile?",
        "Please can you publish the Symgov standard?",
        "How do I delete a symbol set? Please publish this Symgov standard.",
    ],
)
def test_modal_or_polite_mutation_intent_is_refused_before_provider(monkeypatch, prompt):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": prompt},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()


def test_mixed_action_and_explanation_is_refused_before_provider_or_tool(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={
            "prompt": "Tell Ed to delete this Symgov symbol set, and explain how to delete it."
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    provider.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "How do I delete a symbol set in Symgov?",
        "How do I change my Symgov role?",
        "Explain how an administrator deletes a Symgov symbol set.",
    ],
)
def test_explanatory_mutation_how_to_question_is_not_overblocked(monkeypatch, prompt):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(
        return_value=_provider_result(
            answer="Use the symbol-set workflow and follow its approval controls."
        )
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": prompt},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    provider.assert_called_once()


@pytest.mark.parametrize("operation", CANONICAL_OPERATION_FORMS)
def test_each_operation_has_an_exact_explanation_exemption(monkeypatch, operation):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(return_value=_provider_result(answer="Use the documented Symgov workflow."))
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": f"How do I {operation} a Symgov record?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    provider.assert_called_once()


@pytest.mark.parametrize("operation", CANONICAL_OPERATION_FORMS)
def test_each_explanation_plus_action_clause_is_refused(monkeypatch, operation):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={
            "prompt": (
                f"How do I {operation} a Symgov record? "
                f"Please {operation} this Symgov record now."
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    provider.assert_not_called()
    tool.assert_not_called()


def test_out_of_scope_question_is_refused_before_provider(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What will the weather be in Paris tomorrow?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    provider.assert_not_called()


def test_role_sensitive_other_user_request_fails_closed(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock()
    tool = MagicMock()
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user(roles=("reviewer",))).post(
        "/api/v1/ed/chat",
        json={"prompt": "Show me another user's PIN and audit log in Symgov"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert "another user" not in response.text.lower()
    provider.assert_not_called()
    tool.assert_not_called()


def test_cross_tenant_tool_denial_is_safe_and_stops_synthesis(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(
        return_value=_provider_result(
            tool_calls=[{"tool": "get_symbol_set", "symbol_set_id": str(uuid.uuid4())}]
        )
    )
    tool = MagicMock(
        side_effect=HTTPException(
            status_code=404,
            detail="foreign tenant row 11111111-1111-1111-1111-111111111111",
        )
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(
        _user(organization_id=str(uuid.uuid4()), base_role="user")
    ).post(
        "/api/v1/ed/chat",
        json={"prompt": "Show the selected Symgov symbol set"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "unavailable"
    assert response.json()["mode"] == "cannot_answer"
    assert "11111111" not in response.text
    assert provider.call_count == 1


@pytest.mark.parametrize(
    ("tool_result", "label"),
    [
        (None, "closed"),
        ((), "unlinked"),
        ([], "unavailable"),
    ],
)
def test_closed_unlinked_or_unavailable_context_is_bounded(
    monkeypatch,
    tool_result,
    label,
):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(
        return_value=_provider_result(tool_calls=[{"tool": "get_project_context"}])
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", lambda *args: tool_result)

    response = _override_client(
        _user(organization_id=str(uuid.uuid4()), base_role="user")
    ).post(
        "/api/v1/ed/chat",
        json={"prompt": f"Explain the {label} Symgov project context"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["citations"] == []
    assert any("context" in warning.lower() for warning in body["warnings"])
    assert provider.call_count == 1


def test_tool_call_budget_is_hard_capped_at_three(monkeypatch):
    from symgov_backend.services import ed_orchestration

    provider = MagicMock(
        return_value=_provider_result(
            tool_calls=[{"tool": "get_current_user_profile"} for _ in range(4)]
        )
    )
    tool = MagicMock(
        return_value={
            "display_name": "Ed User",
            "citation": {
                "source_kind": "live_record",
                "record_type": "user",
                "record_ref": "live:user:7c8c3128-4022-58e5-8d2f-13ab86fd8f6b",
                "as_of": "2026-09-28T12:00:00+00:00",
            },
        }
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What does my Symgov user profile show?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert tool.call_count == 3
    assert provider.call_count == 1


def test_safe_live_citation_uses_opaque_reference_not_hidden_record_id(monkeypatch):
    from symgov_backend.services import ed_orchestration

    hidden_id = "11111111-1111-1111-1111-111111111111"
    opaque_ref = "live:user:7c8c3128-4022-58e5-8d2f-13ab86fd8f6b"
    provider = MagicMock(
        side_effect=[
            _provider_result(tool_calls=[{"tool": "get_current_user_profile"}]),
            _provider_result(answer="Your current profile is available."),
        ]
    )
    tool = MagicMock(
        return_value={
            "id": hidden_id,
            "display_name": "Ed User",
            "citation": {
                "source_kind": "live_record",
                "record_type": "user",
                "record_ref": opaque_ref,
                "as_of": "2026-09-28T12:00:00+00:00",
            },
        }
    )
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "execute_ed_read_tool", tool)

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What does my Symgov profile show?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["citations"] == [
        {
            "sourceType": "live_record",
            "title": "Live user",
            "reference": opaque_ref,
            "asOf": "2026-09-28T12:00:00+00:00",
        }
    ]
    assert hidden_id not in response.text


def test_missing_evidence_has_explicit_approved_knowledge_warning(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(answer="Symgov is a governance application."),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What is the Symgov application?"},
    )

    assert response.status_code == 200
    assert response.json()["citations"] == []
    assert any("approved" in warning.lower() for warning in response.json()["warnings"])


@pytest.mark.parametrize(
    "unsafe_claim",
    PROVIDER_ACTION_CLAIMS,
)
def test_provider_completed_mutation_claim_is_refused_without_leaking_text(
    monkeypatch,
    unsafe_claim,
):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(answer=unsafe_claim),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What is the current ACME symbol set status in Symgov?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    assert unsafe_claim not in response.text


@pytest.mark.parametrize("unsafe_claim", INTERVENING_ADVERB_ACTION_CLAIMS)
def test_provider_intervening_adverb_claim_is_refused_without_leaking_text(
    monkeypatch,
    unsafe_claim,
):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(answer=unsafe_claim),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What is the current ACME symbol set status in Symgov?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"
    assert unsafe_claim not in response.text


@pytest.mark.parametrize("forms", CANONICAL_OPERATION_FORMS.values())
def test_adverb_claim_grammar_retains_canonical_operation_coverage(forms):
    from symgov_backend.services import ed_orchestration

    assert ed_orchestration._claims_operation(f"I definitely did {forms[0]} the record.")
    assert ed_orchestration._claims_operation(f"I have explicitly {forms[4]} the record.")
    assert ed_orchestration._claims_operation(f"Ed is actively {forms[2]} the record.")
    assert ed_orchestration._claims_operation(f"The record was automatically {forms[4]}.")


@pytest.mark.parametrize(
    "factual_answer",
    [
        "I did not delete the Symgov symbol set.",
        "I have never deleted the Symgov symbol set.",
        "The Symgov standard was not automatically published.",
        "The setting is currently unchanged.",
        "The project is currently selected.",
        "I did definitely explain the Symgov review; delete is a workflow action.",
        "The standard was automatically reviewed, and published is a status label.",
    ],
)
def test_negation_status_and_clause_boundaries_do_not_form_action_claims(
    monkeypatch,
    factual_answer,
):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(answer=factual_answer),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What does the current Symgov status show?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["answer"] == factual_answer


@pytest.mark.parametrize(
    "factual_answer",
    [
        "The symbol set is currently published.",
        "The active Symgov symbol set is currently selected.",
        "The Symgov project was previously saved as a preference.",
        "The Symgov role is currently changed.",
        "The Symgov role is currently unchanged.",
        "Historically, administrators approved Symgov standards after human review.",
        "The Symgov publication process requires a governance decision.",
    ],
)
def test_ordinary_factual_provider_answer_is_not_blocked(monkeypatch, factual_answer):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(answer=factual_answer),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "What does a published Symgov standard show?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["answer"] == factual_answer


def test_provider_refusal_is_structured_and_private(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(status="refusal", answer="I cannot answer that safely."),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "Explain Symgov security"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "refused"
    assert response.json()["mode"] == "blocked"


@pytest.mark.parametrize("error", [TimeoutError("secret timeout"), RuntimeError("provider secret")])
def test_provider_timeout_or_failure_returns_safe_private_response(monkeypatch, error):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        MagicMock(side_effect=error),
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "Explain Symgov projects"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "unavailable"
    assert "secret" not in response.text
    assert "provider" not in response.text.lower()


def test_malformed_provider_output_is_not_echoed(monkeypatch):
    from symgov_backend.services import ed_orchestration

    marker = "raw-malformed-secret"
    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: {"outputText": marker, "usage": {}},
    )

    response = _override_client(_user()).post(
        "/api/v1/ed/chat",
        json={"prompt": "Explain Symgov projects"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["status"] == "unavailable"
    assert marker not in response.text


def test_ed_guru_attribution_redaction_and_pseudonym_are_server_owned(monkeypatch):
    from symgov_backend.services import ed_orchestration

    secret = "sk-super-secret-value-1234567890"
    provider = MagicMock(return_value=_provider_result())
    monkeypatch.setattr(ed_orchestration, "request_llm_completion", provider)
    monkeypatch.setattr(ed_orchestration, "resolve_model_for_feature", lambda feature: "openai/gpt-5-mini")
    user = _user(user_id="22222222-2222-2222-2222-222222222222")

    response = _override_client(user).post(
        "/api/v1/ed/chat",
        json={"prompt": f"Explain Symgov security using token {secret}"},
    )

    assert response.status_code == 200
    kwargs = provider.call_args.kwargs
    assert kwargs["provider"] == "openrouter"
    assert kwargs["feature"] == "ed_guru"
    assert kwargs["use_case"] == "ed_guru"
    assert kwargs["service_name"] == "symgov-api"
    assert kwargs["prompt_version"] == "ed-guru-2026-09-28-v1"
    assert kwargs["timeout"] == 30
    assert kwargs["max_tokens"] == 800
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["initiator_pseudonym"] != user.id
    assert len(kwargs["initiator_pseudonym"]) == 64
    assert secret not in repr(kwargs)
    assert "[REDACTED]" in repr(kwargs["messages"])


def test_per_user_rate_limit_has_safe_private_429(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(ed_orchestration, "_USER_LIMIT", 2)
    monkeypatch.setattr(ed_orchestration, "_ORG_LIMIT", 100)
    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(),
    )
    client = _override_client(_user())

    assert client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov projects"}).status_code == 200
    assert client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov reviews"}).status_code == 200
    limited = client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov symbols"})

    assert limited.status_code == 429
    assert limited.headers["cache-control"] == "no-store, private"
    assert int(limited.headers["retry-after"]) >= 1
    assert "user" not in limited.text.lower()


def test_per_organization_rate_limit_spans_users(monkeypatch):
    from symgov_backend.services import ed_orchestration

    monkeypatch.setattr(ed_orchestration, "_USER_LIMIT", 100)
    monkeypatch.setattr(ed_orchestration, "_ORG_LIMIT", 2)
    monkeypatch.setattr(
        ed_orchestration,
        "request_llm_completion",
        lambda **kwargs: _provider_result(),
    )
    organization_id = str(uuid.uuid4())
    current = {"user": _user(organization_id=organization_id, base_role="user")}
    app = create_app()
    app.dependency_overrides[require_ed_pilot] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    client = TestClient(app, headers=SAFE_HEADERS)

    assert client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov projects"}).status_code == 200
    current["user"] = _user(organization_id=organization_id, base_role="user")
    assert client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov reviews"}).status_code == 200
    current["user"] = _user(organization_id=organization_id, base_role="user")
    limited = client.post("/api/v1/ed/chat", json={"prompt": "Explain Symgov symbols"})

    assert limited.status_code == 429
    assert limited.headers["cache-control"] == "no-store, private"
    assert "organization" not in limited.text.lower()
