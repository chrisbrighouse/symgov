from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from symgov_backend import auth
from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    GovernedSymbol,
    HumanReviewDecision,
    PublicationApprovalTarget,
    PublicationJob,
    ReviewCase,
    ReviewCaseAction,
    ReviewSplitItem,
    SymbolRevision,
)
from symgov_backend import publication_handoff, review_followup_handoff
from symgov_backend import runtime
from symgov_backend.routes import workspace as workspace_routes
from symgov_backend.schemas import (
    WorkspaceReviewDecisionRequest,
    WorkspaceReviewSymbolPropertiesUpdateRequest,
    WorkspaceRightsReviewDecisionRequest,
    WorkspaceSplitReviewProcessRequest,
)


def authenticated_user(*roles: str, user_id: str | None = None, display_name: str = "Ada Reviewer") -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id or "11111111-1111-1111-1111-111111111111",
        email="ada@example.test",
        display_name=display_name,
        roles=tuple(roles),
        must_change_pin=False,
    )


@pytest.mark.parametrize(
    ("roles", "expected_role"),
    [
        (("reviewer",), "reviewer"),
        (("admin",), "admin"),
        (("admin", "reviewer"), "reviewer"),
    ],
)
def test_review_operation_actor_uses_session_uuid_name_and_deterministic_role(roles, expected_role):
    actor = auth.derive_review_operation_actor(authenticated_user(*roles))

    assert str(actor.id) == "11111111-1111-1111-1111-111111111111"
    assert actor.display_name == "Ada Reviewer"
    assert actor.effective_role == expected_role
    assert actor.roles == tuple(sorted(roles))


@pytest.mark.parametrize(
    "user",
    [
        authenticated_user(),
        authenticated_user("submitter"),
        authenticated_user("reviewer", user_id="not-a-uuid"),
        authenticated_user("reviewer", display_name="   "),
    ],
)
def test_review_operation_actor_fails_closed_for_invalid_session_actor(user):
    with pytest.raises(ValueError):
        auth.derive_review_operation_actor(user)


@pytest.mark.parametrize(
    ("request_type", "payload"),
    [
        (
            WorkspaceReviewDecisionRequest,
            {"decisionCode": "approve", "deciderName": "Impersonated reviewer"},
        ),
        (
            WorkspaceReviewDecisionRequest,
            {"decisionCode": "approve", "deciderRole": "admin"},
        ),
        (
            WorkspaceRightsReviewDecisionRequest,
            {"decisionCode": "clear_rights", "deciderName": "Impersonated reviewer", "deciderRole": "admin"},
        ),
        (
            WorkspaceSplitReviewProcessRequest,
            {"deciderName": "Impersonated reviewer", "childDecisions": []},
        ),
        (
            WorkspaceReviewSymbolPropertiesUpdateRequest,
            {"name": "Pump", "updatedBy": "Impersonated reviewer"},
        ),
    ],
)
def test_human_mutation_request_models_reject_direct_identity_spoofing(request_type, payload):
    with pytest.raises(ValidationError, match="identity fields are session-authoritative"):
        request_type.model_validate(payload)


@pytest.mark.parametrize(
    ("request_type", "payload"),
    [
        (
            WorkspaceReviewDecisionRequest,
            {"request": {"decisionCode": "approve", "deciderName": "Impersonated reviewer"}},
        ),
        (
            WorkspaceRightsReviewDecisionRequest,
            {"request": {"decisionCode": "clear_rights", "deciderRole": "admin"}},
        ),
        (
            WorkspaceSplitReviewProcessRequest,
            {"request": {"deciderName": "Impersonated reviewer", "childDecisions": []}},
        ),
        (
            WorkspaceReviewSymbolPropertiesUpdateRequest,
            {"request": {"name": "Pump", "updatedBy": "Impersonated reviewer"}},
        ),
    ],
)
def test_human_mutation_request_models_reject_wrapped_identity_spoofing(request_type, payload):
    with pytest.raises(ValidationError, match="identity fields are session-authoritative"):
        request_type.model_validate(payload)


@pytest.mark.parametrize(
    ("method", "v1_path", "legacy_path", "payload"),
    [
        (
            "post",
            "/api/v1/workspace/review-cases/11111111-1111-1111-1111-111111111111/decisions",
            "/api/workspace/review-cases/11111111-1111-1111-1111-111111111111/decisions",
            {"decisionCode": "approve", "deciderName": "Impersonated reviewer"},
        ),
        (
            "post",
            "/api/v1/workspace/review-cases/11111111-1111-1111-1111-111111111111/decisions",
            "/api/workspace/review-cases/11111111-1111-1111-1111-111111111111/decisions",
            {"decisionCode": "approve", "deciderRole": "admin"},
        ),
        (
            "post",
            "/api/v1/workspace/rights-review-cases/11111111-1111-1111-1111-111111111111/decisions",
            "/api/workspace/rights-review-cases/11111111-1111-1111-1111-111111111111/decisions",
            {"decisionCode": "clear_rights", "deciderName": "Impersonated reviewer"},
        ),
        (
            "post",
            "/api/v1/workspace/rights-review-cases/11111111-1111-1111-1111-111111111111/decisions",
            "/api/workspace/rights-review-cases/11111111-1111-1111-1111-111111111111/decisions",
            {"decisionCode": "clear_rights", "deciderRole": "admin"},
        ),
        (
            "post",
            "/api/v1/workspace/review-cases/11111111-1111-1111-1111-111111111111/split-items/process-decisions",
            "/api/workspace/review-cases/11111111-1111-1111-1111-111111111111/split-items/process-decisions",
            {"deciderName": "Impersonated reviewer", "childDecisions": []},
        ),
        (
            "post",
            "/api/v1/workspace/review-cases/11111111-1111-1111-1111-111111111111/split-items/process-decisions",
            "/api/workspace/review-cases/11111111-1111-1111-1111-111111111111/split-items/process-decisions",
            {"deciderRole": "admin", "childDecisions": []},
        ),
        (
            "patch",
            "/api/v1/workspace/review-cases/11111111-1111-1111-1111-111111111111/symbol-properties",
            "/api/workspace/review-cases/11111111-1111-1111-1111-111111111111/symbol-properties",
            {"name": "Pump", "updatedBy": "Impersonated reviewer"},
        ),
    ],
)
def test_real_v1_routes_reject_direct_and_wrapped_spoofing_without_handler_side_effects(
    method, v1_path, legacy_path, payload
):
    app = create_app()

    class NoMutationSession:
        def get(self, *_args, **_kwargs):
            raise AssertionError("handler reached persistence")

    app.dependency_overrides[get_db_session] = lambda: NoMutationSession()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user("reviewer")
    client = TestClient(app)

    assert getattr(client, method)(v1_path, json=payload).status_code == 422
    assert getattr(client, method)(v1_path, json={"request": payload}).status_code == 422
    assert getattr(client, method)(legacy_path, json=payload).status_code == 404
    assert getattr(client, method)(legacy_path, json={"request": payload}).status_code == 404


@pytest.mark.parametrize(
    ("role", "display_name"),
    [("reviewer", "Ada Reviewer"), ("admin", "Alex Admin")],
)
def test_generic_review_decision_persists_authenticated_actor_across_decision_action_and_audit(
    monkeypatch, tmp_path, role, display_name
):
    review_case_id = uuid4()
    actor = authenticated_user(role, display_name=display_name)
    review_case = ReviewCase(
        id=review_case_id,
        source_entity_type="classification_record",
        source_entity_id=uuid4(),
        current_stage="classification_review",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )

    class Query:
        def __init__(self, session, model):
            self.session = session
            self.model = model

        def filter(self, *_args, **_kwargs):
            return self

        def filter_by(self, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return [item for item in self.session.added if isinstance(item, self.model)]

        def first(self):
            if self.model is ReviewCaseAction:
                return next(
                    (item for item in reversed(self.session.added) if isinstance(item, ReviewCaseAction)),
                    None,
                )
            return None

        def one_or_none(self):
            if self.model is AgentDefinition:
                return SimpleNamespace(id=uuid4(), slug="libby")
            return None

    class Session:
        def __init__(self):
            self.added = []

        def get(self, model, key):
            if model is ReviewCase and key == review_case_id:
                return review_case
            if model is HumanReviewDecision:
                return next(
                    (item for item in self.added if isinstance(item, HumanReviewDecision) and item.id == key),
                    None,
                )
            return None

        def query(self, model):
            return Query(self, model)

        def add(self, item):
            if hasattr(item, "id") and item.id is None:
                item.id = uuid4()
            self.added.append(item)

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, _item):
            return None

    session = Session()
    monkeypatch.setattr(review_followup_handoff, "LIBBY_RUNTIME_ROOT", tmp_path)

    response = workspace_routes.create_workspace_review_decision(
        str(review_case_id),
        WorkspaceReviewDecisionRequest(decisionCode="request_changes", decisionNote="Please correct the family."),
        current_user=actor,
        session=session,
    )

    decision = next(item for item in session.added if isinstance(item, HumanReviewDecision))
    action = next(item for item in session.added if isinstance(item, ReviewCaseAction))
    queue_item = next(item for item in session.added if isinstance(item, AgentQueueItem))
    decision_audit = next(
        item
        for item in session.added
        if isinstance(item, AuditEvent) and item.action == "human_review_decision_recorded"
    )
    handoff_audit = next(
        item
        for item in session.added
        if isinstance(item, AuditEvent) and item.action == "libby_review_followup_queued"
    )
    expected_actor = {
        "id": actor.id,
        "display_name": display_name,
        "effective_role": role,
    }
    assert decision.decided_by == uuid4().__class__(actor.id)
    assert decision.decider_name == display_name
    assert decision.decider_role == role
    assert decision.decision_summary.startswith(f"{display_name} recorded")
    assert action.created_by_id == decision.decided_by
    assert decision_audit.actor_id == decision.decided_by
    assert decision_audit.payload_json["actor"]["effective_role"] == role
    assert queue_item.payload_json["review_actor"] == expected_actor
    assert action.action_payload_json["review_actor"] == expected_actor
    assert handoff_audit.actor_id == decision.decided_by
    assert handoff_audit.payload_json["review_actor"] == expected_actor
    assert response.decision.deciderName == display_name


def test_split_non_approval_real_handoff_uses_admin_decision_actor(monkeypatch, tmp_path):
    review_case = ReviewCase(
        id=uuid4(),
        source_entity_type="validation_report",
        source_entity_id=uuid4(),
        current_stage="raster_split_review",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
    )
    validation_report = SimpleNamespace(
        id=review_case.source_entity_id,
        source_type="other",
        normalized_payload_json={"derivative_manifest": {"children": [{}]}},
    )
    split_item = ReviewSplitItem(
        id=uuid4(),
        review_case_id=review_case.id,
        child_key="child-1",
        proposed_symbol_id="P-1",
        proposed_symbol_name="Pump 1",
        file_name="pump-1.svg",
        status="awaiting_decision",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    actor = authenticated_user("admin", display_name="Alex Admin")

    class Result:
        def one_or_none(self):
            return review_case, validation_report

    class Query:
        def __init__(self, session, model):
            self.session = session
            self.model = model

        def filter(self, *_args, **_kwargs):
            return self

        def filter_by(self, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            if self.model is ReviewCaseAction:
                return next(
                    (item for item in reversed(self.session.added) if isinstance(item, ReviewCaseAction)),
                    None,
                )
            return None

        def one_or_none(self):
            if self.model is AgentDefinition:
                return SimpleNamespace(id=uuid4(), slug="libby")
            return None

        def count(self):
            return 0

    class Session:
        def __init__(self):
            self.added = []

        def execute(self, _statement):
            return Result()

        def query(self, model):
            return Query(self, model)

        def get(self, model, key):
            if model is ReviewCase and key == review_case.id:
                return review_case
            if model is ReviewSplitItem and key == split_item.id:
                return split_item
            if model is HumanReviewDecision:
                return next(
                    (item for item in self.added if isinstance(item, HumanReviewDecision) and item.id == key),
                    None,
                )
            if model.__name__ == "ValidationReport" and key == validation_report.id:
                return validation_report
            return None

        def add(self, item):
            if hasattr(item, "id") and item.id is None:
                item.id = uuid4()
            self.added.append(item)

        def flush(self):
            return None

        def commit(self):
            return None

    session = Session()
    monkeypatch.setattr(workspace_routes, "resolve_source_file_name", lambda _report: "source.png")
    monkeypatch.setattr(workspace_routes, "ensure_split_items", lambda *_args, **_kwargs: [split_item])
    monkeypatch.setattr(review_followup_handoff, "LIBBY_RUNTIME_ROOT", tmp_path)

    workspace_routes.process_workspace_split_review_decisions(
        str(review_case.id),
        WorkspaceSplitReviewProcessRequest(
            caseComment="Please correct the child.",
            childDecisions=[
                {"childId": "child-1", "action": "request_changes", "details": "Correct the family."},
            ],
        ),
        current_user=actor,
        session=session,
    )

    decision = next(item for item in session.added if isinstance(item, HumanReviewDecision))
    action = next(item for item in session.added if isinstance(item, ReviewCaseAction))
    queue_item = next(item for item in session.added if isinstance(item, AgentQueueItem))
    handoff_audit = next(
        item
        for item in session.added
        if isinstance(item, AuditEvent) and item.action == "libby_review_followup_queued"
    )
    expected_actor = {
        "id": actor.id,
        "display_name": "Alex Admin",
        "effective_role": "admin",
    }
    assert decision.decided_by == uuid4().__class__(actor.id)
    assert queue_item.payload_json["review_actor"] == expected_actor
    assert action.action_payload_json["review_actor"] == expected_actor
    assert handoff_audit.actor_id == decision.decided_by
    assert handoff_audit.payload_json["review_actor"] == expected_actor


@pytest.mark.parametrize(
    ("missing_field", "missing_value"),
    [("decided_by", None), ("decider_name", "   "), ("decider_role", "")],
)
def test_review_followup_missing_durable_actor_fails_before_queue_audit_or_action_mutation(
    monkeypatch, missing_field, missing_value
):
    review_case = ReviewCase(
        id=uuid4(),
        source_entity_type="classification_record",
        source_entity_id=uuid4(),
        current_stage="review_follow_up",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
    )
    decision = HumanReviewDecision(
        id=uuid4(),
        review_case_id=review_case.id,
        decision_code="request_changes",
        decision_summary="Ada requested changes.",
        decision_note="Correct the family.",
        decided_by=uuid4(),
        decider_name="Ada Reviewer",
        decider_role="reviewer",
        from_stage="classification_review",
        to_stage="review_follow_up",
        decision_payload_json={},
        created_at=datetime.now(timezone.utc),
    )
    setattr(decision, missing_field, missing_value)
    action = ReviewCaseAction(
        id=uuid4(),
        review_case_id=review_case.id,
        decision_id=decision.id,
        action_code="route_review_follow_up_to_libby",
        action_status="pending",
        target_agent_slug="libby",
        target_stage="review_follow_up",
        action_payload_json={"existing": "metadata"},
        created_by_type="human",
        created_by_id=decision.decided_by,
        created_at=datetime.now(timezone.utc),
    )

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            return action

    class Session:
        def __init__(self):
            self.added = []
            self.commits = 0

        def query(self, model):
            if model is AgentDefinition:
                raise AssertionError("Libby lookup occurred before durable actor validation")
            return Query()

        def get(self, model, key):
            if model is ReviewCase and key == review_case.id:
                return review_case
            if model is HumanReviewDecision and key == decision.id:
                return decision
            raise AssertionError(f"unexpected lookup before actor validation: {model}")

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

    session = Session()
    monkeypatch.setattr(
        review_followup_handoff,
        "write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("queue file written")),
    )

    with pytest.raises(RuntimeError, match="actor snapshot"):
        review_followup_handoff.execute_review_followup_handoff(
            session,
            review_case_id=review_case.id,
            decision_id=decision.id,
        )

    assert action.action_status == "pending"
    assert action.action_payload_json == {"existing": "metadata"}
    assert action.started_at is None
    assert action.completed_at is None
    assert session.added == []
    assert session.commits == 0


@pytest.mark.parametrize(
    ("role", "display_name"),
    [("reviewer", "Ada Reviewer"), ("admin", "Alex Admin")],
)
def test_property_update_uses_authenticated_actor_for_snapshot_feedback_and_audit(
    monkeypatch, role, display_name
):
    review_case = ReviewCase(
        id=uuid4(),
        source_entity_type="classification_record",
        source_entity_id=uuid4(),
        current_stage="classification_review",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
    )
    properties = SimpleNamespace(
        id=uuid4(),
        review_case_id=review_case.id,
        review_split_item_id=None,
        symbol_record_key="parent",
        name="Old pump",
        description="Old description",
        category="Equipment",
        discipline="Mechanical",
        format="svg",
        source="agent",
        updated_by="Libby",
        updated_at=datetime.now(timezone.utc),
    )
    feedback = []

    class Session:
        def __init__(self):
            self.added = []

        def get(self, model, key):
            return review_case if model is ReviewCase and key == review_case.id else None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            return None

    session = Session()
    monkeypatch.setattr(workspace_routes, "get_or_create_symbol_properties", lambda *_args, **_kwargs: properties)
    monkeypatch.setattr(workspace_routes, "remember_property_option", lambda _session, *, value, **_kwargs: value)
    monkeypatch.setattr(workspace_routes, "build_symbol_property_feedback_events", lambda **kwargs: [kwargs])
    monkeypatch.setattr(
        workspace_routes,
        "add_agent_feedback_events",
        lambda _session, events, **_kwargs: feedback.extend(events),
    )

    workspace_routes.update_workspace_review_symbol_properties(
        str(review_case.id),
        WorkspaceReviewSymbolPropertiesUpdateRequest(
            name="Session pump",
            description="Corrected",
            category="Equipment",
            discipline="Mechanical",
            format="svg",
        ),
        current_user=authenticated_user(role, display_name=display_name),
        session=session,
    )

    audit = next(item for item in session.added if isinstance(item, AuditEvent))
    assert properties.updated_by == display_name
    assert feedback[0]["reviewer_name"] == display_name
    assert feedback[0]["reviewer_role"] == role
    assert str(audit.actor_id) == "11111111-1111-1111-1111-111111111111"
    assert audit.payload_json["actor"]["effective_role"] == role


@pytest.mark.parametrize(
    ("role", "display_name"),
    [("reviewer", "Ada Reviewer"), ("admin", "Alex Admin")],
)
def test_split_child_decisions_freeze_one_session_actor_for_all_persistence(
    monkeypatch, role, display_name
):
    review_case = ReviewCase(
        id=uuid4(),
        source_entity_type="validation_report",
        source_entity_id=uuid4(),
        current_stage="raster_split_review",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
    )
    validation_report = SimpleNamespace(normalized_payload_json={"derivative_manifest": {"children": [{}, {}]}})
    split_items = [
        ReviewSplitItem(
            id=uuid4(),
            review_case_id=review_case.id,
            child_key=f"child-{index}",
            proposed_symbol_id=f"P-{index}",
            proposed_symbol_name=f"Pump {index}",
            file_name=f"pump-{index}.svg",
            status="duplicate_exception",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        for index in (1, 2)
    ]
    split_items_by_id = {item.id: item for item in split_items}
    feedback = []

    class Result:
        def one_or_none(self):
            return review_case, validation_report

    class Query:
        def __init__(self, session, model):
            self.session = session
            self.model = model

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            if self.model is ReviewCaseAction:
                return next((item for item in reversed(self.session.added) if isinstance(item, ReviewCaseAction)), None)
            return None

        def count(self):
            return 0

    class Session:
        def __init__(self):
            self.added = []

        def execute(self, _statement):
            return Result()

        def add(self, item):
            self.added.append(item)

        def flush(self):
            for item in self.added:
                if hasattr(item, "id") and item.id is None:
                    item.id = uuid4()

        def commit(self):
            return None

        def get(self, model, key):
            if model is ReviewCase and key == review_case.id:
                return review_case
            if model is ReviewSplitItem:
                return split_items_by_id.get(key)
            return None

        def query(self, model):
            return Query(self, model)

    session = Session()
    monkeypatch.setattr(workspace_routes, "resolve_source_file_name", lambda _report: "source.png")
    monkeypatch.setattr(workspace_routes, "ensure_split_items", lambda *_args, **_kwargs: split_items)
    monkeypatch.setattr(workspace_routes, "build_duplicate_decision_feedback_events", lambda **kwargs: [kwargs])
    monkeypatch.setattr(
        workspace_routes,
        "add_agent_feedback_events",
        lambda _session, events, **_kwargs: feedback.extend(events),
    )
    monkeypatch.setattr(workspace_routes, "execute_publication_handoff", lambda *_args, **_kwargs: {"status": "completed"})

    workspace_routes.process_workspace_split_review_decisions(
        str(review_case.id),
        WorkspaceSplitReviewProcessRequest(
            caseComment="False duplicate.",
            childDecisions=[
                {"childId": "child-1", "action": "approved", "details": "Verified first."},
                {"childId": "child-2", "action": "approved", "details": "Verified second."},
            ],
        ),
        current_user=authenticated_user(role, display_name=display_name),
        session=session,
    )

    decisions = [item for item in session.added if isinstance(item, HumanReviewDecision)]
    actions = [item for item in session.added if isinstance(item, ReviewCaseAction)]
    audits = [item for item in session.added if isinstance(item, AuditEvent)]
    actor_id = uuid4().__class__("11111111-1111-1111-1111-111111111111")
    assert len(decisions) == len(actions) == len(feedback) == 2
    assert all(decision.decided_by == actor_id for decision in decisions)
    assert all(decision.decider_name == display_name for decision in decisions)
    assert all(decision.decider_role == role for decision in decisions)
    assert all(decision.decision_summary.startswith(f"{display_name} processed") for decision in decisions)
    assert all(action.created_by_id == actor_id for action in actions)
    assert all(
        decision.decision_payload_json["duplicate_gate_override"]["reviewed_by"] == display_name
        for decision in decisions
    )
    assert all(event["reviewer_name"] == display_name for event in feedback)
    assert all(event["reviewer_role"] == role for event in feedback)
    assert audits and all(audit.actor_id == actor_id for audit in audits)
    assert all(audit.payload_json["actor"]["effective_role"] == role for audit in audits)


def test_historical_actor_null_decision_remains_readable_from_snapshots():
    decision = HumanReviewDecision(
        id=uuid4(),
        review_case_id=uuid4(),
        decision_code="approve",
        decision_summary="Historical reviewer approved.",
        decision_note=None,
        decided_by=None,
        decider_name="Historical Reviewer",
        decider_role="legacy_role",
        from_stage="review",
        to_stage="approved",
        decision_payload_json={},
        created_at=datetime.now(timezone.utc),
    )

    serialized = workspace_routes.build_decision_summary(decision)

    assert serialized.deciderName == "Historical Reviewer"
    assert serialized.deciderRole == "legacy_role"


def approved_decision(*, code: str = "approve", actor_id=None) -> HumanReviewDecision:
    review_case_id = uuid4()
    return HumanReviewDecision(
        id=uuid4(),
        review_case_id=review_case_id,
        decision_code=code,
        decision_summary="Ada Reviewer approved.",
        decision_note=None,
        decided_by=actor_id if actor_id is not None else uuid4(),
        decider_name="Ada Reviewer",
        decider_role="reviewer",
        from_stage="review",
        to_stage="ready_for_publication_handoff",
        decision_payload_json={"review_case_id": str(review_case_id)},
        created_at=datetime.now(timezone.utc),
    )


def test_publication_approval_snapshot_is_derived_from_durable_decision():
    decision = approved_decision()

    snapshot = publication_handoff.approval_actor_snapshot(decision)

    assert snapshot == {
        "id": str(decision.decided_by),
        "display_name": "Ada Reviewer",
        "effective_role": "reviewer",
    }


def test_execute_publication_handoff_queues_decision_actor_and_audits_human(monkeypatch):
    review_case = ReviewCase(
        id=uuid4(),
        source_entity_type="classification_record",
        source_entity_id=uuid4(),
        current_stage="ready_for_publication_handoff",
        escalation_level="medium",
        opened_at=datetime.now(timezone.utc),
    )
    decision = approved_decision()
    decision.review_case_id = review_case.id
    decision.decision_payload_json = {"review_case_id": str(review_case.id)}
    action = ReviewCaseAction(
        id=uuid4(),
        review_case_id=review_case.id,
        decision_id=decision.id,
        action_code="prepare_publication_handoff",
        action_status="pending",
        target_agent_slug="rupert",
        target_stage="publication_staging",
        action_payload_json={"decision_code": "approve"},
        created_by_type="human",
        created_by_id=decision.decided_by,
        created_at=datetime.now(timezone.utc),
    )
    rupert = SimpleNamespace(id=uuid4(), slug="rupert")
    revision = SimpleNamespace(id=uuid4(), symbol_id=uuid4(), revision_label="A", payload_json={})
    queued = []
    runner_paths = []

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args, **_kwargs):
            return self

        def filter_by(self, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            if self.model is ReviewCaseAction:
                return action
            if self.model is HumanReviewDecision:
                return decision
            return None

        def one_or_none(self):
            return rupert if self.model is AgentDefinition else None

    class Session:
        def __init__(self):
            self.added = []
            self.commits = 0

        def query(self, model):
            return Query(model)

        def get(self, model, key):
            if model is ReviewCase and key == review_case.id:
                return review_case
            if model is HumanReviewDecision and key == decision.id:
                return decision
            if model is ReviewCaseAction and key == action.id:
                return action
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("successful handoff must not roll back")

    session = Session()
    monkeypatch.setattr(publication_handoff, "load_review_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        publication_handoff,
        "approved_revisions_for_decision",
        lambda *_args, **_kwargs: [revision],
    )
    monkeypatch.setattr(publication_handoff, "detect_graphical_duplicates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(publication_handoff, "build_pack_metadata", lambda *_args, **_kwargs: ("pack", "Pack"))
    monkeypatch.setattr(
        publication_handoff,
        "ensure_publication_approval_target",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid4(), content_sha256="approval-sha256"),
    )

    def write_queue(queue_item):
        queued.append(queue_item)
        return SimpleNamespace(name="queue.json")

    def run_queue(queue_path):
        runner_paths.append(queue_path)
        return {"status": "completed"}

    monkeypatch.setattr(publication_handoff, "write_rupert_queue_item", write_queue)
    monkeypatch.setattr(publication_handoff, "run_rupert", run_queue)

    result = publication_handoff.execute_publication_handoff(
        session,
        review_case_id=review_case.id,
        decision_id=decision.id,
    )

    expected_actor = publication_handoff.approval_actor_snapshot(decision)
    audit = next(
        item
        for item in session.added
        if isinstance(item, AuditEvent) and item.action == "publication_handoff_completed"
    )
    assert result["status"] == "completed"
    assert len(queued) == len(runner_paths) == 1
    assert queued[0]["payload_json"]["approval_actor"] == expected_actor
    assert queued[0]["source_id"] == str(decision.id)
    assert audit.actor_id == decision.decided_by
    assert audit.payload_json["approval_actor"] == expected_actor


@pytest.mark.parametrize("code,actor_present", [("reject", True), ("approve", False)])
def test_publication_approval_snapshot_rejects_non_approval_and_historical_actor_null(code, actor_present):
    decision = approved_decision(code=code)
    if not actor_present:
        decision.decided_by = None

    with pytest.raises(RuntimeError):
        publication_handoff.approval_actor_snapshot(decision)


def test_runtime_resolves_human_approval_from_database_and_rejects_queue_actor_tampering():
    decision = approved_decision()

    class Session:
        def get(self, model, key):
            return decision if model is HumanReviewDecision and key == decision.id else None

    queue_item = {
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "payload_json": {
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "approval_actor": {
                "id": str(decision.decided_by),
                "display_name": decision.decider_name,
                "effective_role": decision.decider_role,
            },
        },
    }

    resolved, snapshot = runtime.resolve_durable_publication_approval(Session(), queue_item)
    assert resolved is decision
    assert snapshot["id"] == str(decision.decided_by)

    queue_item["payload_json"]["approval_actor"]["id"] = str(uuid4())
    with pytest.raises(RuntimeError, match="does not match durable review decision"):
        runtime.resolve_durable_publication_approval(Session(), queue_item)


@pytest.mark.parametrize(
    ("failure_case", "error_match"),
    [
        ("source_type", "originate from a review decision"),
        ("source_id", "decision identity is missing or inconsistent"),
        ("review_case_id", "review case does not match durable review decision"),
    ],
)
def test_runtime_rejects_queue_decision_and_case_identity_mismatch(failure_case, error_match):
    decision = approved_decision()

    class Session:
        def get(self, model, key):
            return decision if model is HumanReviewDecision and key == decision.id else None

    queue_item = {
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "payload_json": {
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "approval_actor": publication_handoff.approval_actor_snapshot(decision),
        },
    }
    if failure_case == "source_type":
        queue_item["source_type"] = "review_case"
    elif failure_case == "source_id":
        queue_item["source_id"] = str(uuid4())
    else:
        queue_item["payload_json"]["review_case_id"] = str(uuid4())

    with pytest.raises(RuntimeError, match=error_match):
        runtime.resolve_durable_publication_approval(Session(), queue_item)


@pytest.mark.parametrize(
    "mismatch",
    [
        "agent_id",
        "source_type",
        "source_id",
        "review_decision_id",
        "review_case_id",
        "symbol_revision_ids",
        "human_decision",
        "human_approved",
        "approval_actor",
        "approval_target_id",
        "approval_content_sha256",
    ],
)
def test_existing_durable_publication_queue_must_match_runtime_queue_authority(mismatch, monkeypatch):
    decision = approved_decision()
    revision_id = uuid4()
    agent_definition_id = uuid4()
    approval_actor = publication_handoff.approval_actor_snapshot(decision)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    queue_item_id = uuid4()
    approval_target_id = str(uuid4())
    approval_content_sha256 = "approval-sha256"
    queue_item = {
        "id": str(queue_item_id),
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "status": "completed",
        "priority": "medium",
        "payload_json": {
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "symbol_revision_ids": [str(revision_id)],
            "human_decision": "approve",
            "human_approved": True,
            "approval_actor": approval_actor,
            "approval_target_id": approval_target_id,
            "approval_content_sha256": approval_content_sha256,
        },
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    durable_payload = dict(queue_item["payload_json"])
    durable_payload["approval_actor"] = dict(approval_actor)
    durable_queue_item = SimpleNamespace(
        agent_id=agent_definition_id,
        source_type=queue_item["source_type"],
        source_id=decision.id,
        payload_json=durable_payload,
    )
    if mismatch == "agent_id":
        durable_queue_item.agent_id = uuid4()
    elif mismatch == "source_type":
        durable_queue_item.source_type = "review_case"
    elif mismatch == "source_id":
        durable_queue_item.source_id = uuid4()
    elif mismatch == "symbol_revision_ids":
        durable_payload[mismatch] = [str(uuid4())]
    elif mismatch == "human_decision":
        durable_payload[mismatch] = "reject"
    elif mismatch == "human_approved":
        durable_payload[mismatch] = False
    elif mismatch == "approval_actor":
        durable_payload[mismatch]["id"] = str(uuid4())
    else:
        durable_payload[mismatch] = str(uuid4())

    with pytest.raises(RuntimeError, match="(?i)durable queue item does not match runtime queue"):
        runtime.validate_existing_durable_publication_queue_item(
            durable_queue_item,
            queue_item,
            expected_agent_id=agent_definition_id,
        )

    revision = SimpleNamespace(
        id=revision_id,
        symbol_id=uuid4(),
        revision_label="A",
        payload_json={"review_decision_id": str(decision.id)},
    )
    monkeypatch.setattr(runtime, "resolve_durable_publication_revisions", lambda *_args, **_kwargs: [revision])
    agent_definition = SimpleNamespace(id=agent_definition_id, slug="rupert")

    class Query:
        def filter_by(self, **kwargs):
            assert kwargs == {"slug": "rupert"}
            return self

        def one_or_none(self):
            return agent_definition

    class RecordingSession:
        def __init__(self):
            self.write_calls = []

        def get(self, model, key):
            if model is HumanReviewDecision and key == decision.id:
                return decision
            if model is SymbolRevision and key == revision_id:
                return revision
            if model is AgentQueueItem and key == queue_item_id:
                return durable_queue_item
            raise AssertionError(f"unexpected durable lookup: {model} {key}")

        def query(self, model):
            assert model is AgentDefinition
            return Query()

        def add(self, item):
            self.write_calls.append(("add", item))
            raise AssertionError("publication row added before durable queue validation")

        def flush(self):
            self.write_calls.append(("flush", None))
            raise AssertionError("publication row flushed before durable queue validation")

        def execute(self, statement):
            self.write_calls.append(("execute", statement))
            raise AssertionError("published views refreshed before durable queue validation")

    session = RecordingSession()

    class RecordingBridge:
        _publication_pack_from_artifact = runtime.RuntimePersistenceBridge._publication_pack_from_artifact
        persist_publication_execution = runtime.RuntimePersistenceBridge.persist_publication_execution

        def __init__(self):
            self.service_user_calls = 0

        @contextmanager
        def session_scope(self):
            yield session

        def ensure_publication_service_user(self, _session):
            self.service_user_calls += 1
            raise AssertionError("service user resolved before durable queue validation")

    artifact_record = {
        "payload_json": {
            "decision": "stage",
            "staged_symbol_revisions": [str(revision_id)],
            "release_target": "standards-current",
            "publication_pack": {"pack_code": "blocked-pack", "effective_date": now[:10]},
        }
    }
    bridge = RecordingBridge()

    with pytest.raises(RuntimeError, match="(?i)durable queue item does not match runtime queue"):
        bridge.persist_publication_execution(queue_item, {}, artifact_record, {})

    assert session.write_calls == []
    assert bridge.service_user_calls == 0


def test_runtime_publication_persistence_attributes_governance_to_human_and_execution_to_service(monkeypatch):
    decision = approved_decision()
    revision = SimpleNamespace(
        id=uuid4(),
        symbol_id=uuid4(),
        revision_label="A",
        lifecycle_state="approved",
        payload_json={"review_decision_id": str(decision.id)},
    )
    symbol = SimpleNamespace(
        id=revision.symbol_id,
        catalog_symbol_id="S-000001",
        slug="pump",
        canonical_name="Pump",
        current_revision_id=None,
        updated_at=None,
    )
    rupert = SimpleNamespace(id=uuid4(), slug="rupert")
    service_user = SimpleNamespace(id=uuid4())

    class Query:
        def __init__(self, session, model):
            self.session = session
            self.model = model
            self.criteria = {}

        def filter_by(self, **kwargs):
            self.criteria.update(kwargs)
            return self

        def one_or_none(self):
            candidates = [item for item in self.session.rows if isinstance(item, self.model)]
            if self.model is AgentDefinition:
                candidates.append(rupert)
            for item in candidates:
                if all(getattr(item, key, None) == value for key, value in self.criteria.items()):
                    return item
            return None

    class Session:
        def __init__(self):
            self.rows = [decision]

        def get(self, model, key, **_kwargs):
            if model is HumanReviewDecision and key == decision.id:
                return decision
            if model is SymbolRevision and key == revision.id:
                return revision
            if model is GovernedSymbol and key == symbol.id:
                return symbol
            return next((item for item in self.rows if isinstance(item, model) and item.id == key), None)

        def query(self, model):
            return Query(self, model)

        def add(self, item):
            if hasattr(item, "id") and item.id is None:
                item.id = uuid4()
            if item not in self.rows:
                self.rows.append(item)

        def flush(self):
            for item in self.rows:
                if hasattr(item, "id") and item.id is None:
                    item.id = uuid4()

        def execute(self, _statement):
            return None

    session = Session()

    class Bridge:
        _publication_pack_from_artifact = runtime.RuntimePersistenceBridge._publication_pack_from_artifact
        generate_published_page_code = runtime.RuntimePersistenceBridge.generate_published_page_code
        persist_publication_execution = runtime.RuntimePersistenceBridge.persist_publication_execution

        @contextmanager
        def session_scope(self):
            yield session

        def ensure_publication_service_user(self, _session):
            queue_row = next(item for item in _session.rows if isinstance(item, AgentQueueItem))
            assert queue_row.agent_id is not None
            assert queue_row.source_type is not None
            assert queue_row.source_id is not None
            assert queue_row.status is not None
            assert queue_row.priority is not None
            assert queue_row.created_at is not None
            _session.flush()
            return service_user

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    queue_item = {
        "id": str(uuid4()),
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "status": "completed",
        "priority": "medium",
        "payload_json": {
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "human_decision": "approve",
            "human_approved": True,
            "approval_actor": publication_handoff.approval_actor_snapshot(decision),
            "symbol_revision_ids": [str(revision.id)],
        },
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    run_record = {
        "id": str(uuid4()),
        "model": "test",
        "prompt_version": "test",
        "tool_trace_json": {},
        "result_status": "completed",
        "started_at": now,
        "completed_at": now,
    }
    artifact_record = {
        "id": str(uuid4()),
        "artifact_type": "publication",
        "schema_version": "1",
        "created_at": now,
        "payload_json": {
            "decision": "stage",
            "staged_symbol_revisions": [str(revision.id)],
            "release_target": "standards-current",
            "publication_pack": {"pack_code": "test-pack", "title": "Test pack", "effective_date": now[:10]},
        },
    }
    report = {"id": str(uuid4())}

    bridge = Bridge()
    monkeypatch.setattr(runtime, "resolve_durable_publication_revisions", lambda *_args, **_kwargs: [revision])
    bridge.persist_publication_execution(queue_item, run_record, artifact_record, report)
    bridge.persist_publication_execution(queue_item, run_record, artifact_record, report)

    jobs = [item for item in session.rows if isinstance(item, PublicationJob)]
    audits = [item for item in session.rows if isinstance(item, AuditEvent)]
    assert len(jobs) == 1
    assert jobs[0].requested_by == decision.decided_by
    assert jobs[0].approved_by == decision.decided_by
    assert jobs[0].artifact_manifest_json["approval_actor"]["display_name"] == decision.decider_name
    assert jobs[0].artifact_manifest_json["execution_actor"] == {
        "id": str(service_user.id),
        "type": "service_user",
    }
    assert audits and all(item.actor_id == service_user.id for item in audits)
    assert all(item.actor_id != decision.decided_by for item in audits)
    assert all(item.payload_json["approval_actor"]["effective_role"] == "reviewer" for item in audits)
    assert all(item.payload_json["execution_actor"]["id"] == str(service_user.id) for item in audits)


@pytest.mark.parametrize("decision", [None, approved_decision(code="reject")])
def test_runtime_rejects_missing_or_non_approve_durable_decision(decision):
    decision_id = uuid4() if decision is None else decision.id

    class Session:
        def get(self, model, key):
            return decision if model is HumanReviewDecision and key == decision_id else None

    queue_item = {
        "source_type": "review_decision",
        "source_id": str(decision_id),
        "payload_json": {"review_decision_id": str(decision_id)},
    }

    with pytest.raises(RuntimeError):
        runtime.resolve_durable_publication_approval(Session(), queue_item)


@pytest.mark.parametrize(
    ("failure_case", "error_match"),
    [
        ("queue_actor_mismatch", "does not match durable review decision"),
        ("missing_decision", "does not exist"),
        ("non_approve_decision", "is not an approval"),
        ("actor_null_decision", "has no authenticated actor"),
    ],
)
def test_publication_persistence_rejects_invalid_durable_approval_before_any_write(
    failure_case, error_match
):
    decision = None if failure_case == "missing_decision" else approved_decision()
    if decision is not None and failure_case == "non_approve_decision":
        decision.decision_code = "reject"
    if decision is not None and failure_case == "actor_null_decision":
        decision.decided_by = None
    decision_id = uuid4() if decision is None else decision.id
    queued_actor = {
        "id": str(decision.decided_by) if decision is not None and decision.decided_by is not None else str(uuid4()),
        "display_name": decision.decider_name if decision is not None else "Missing Reviewer",
        "effective_role": decision.decider_role if decision is not None else "reviewer",
    }
    if failure_case == "queue_actor_mismatch":
        queued_actor["id"] = str(uuid4())

    class RecordingSession:
        def __init__(self):
            self.get_calls = []
            self.write_calls = []

        def get(self, model, key):
            self.get_calls.append((model, key))
            if model is HumanReviewDecision and key == decision_id:
                return decision
            raise AssertionError(f"unexpected lookup after approval validation: {model}")

        def query(self, model):
            self.write_calls.append(("query", model))
            raise AssertionError("publication query occurred before approval validation")

        def add(self, item):
            self.write_calls.append(("add", item))
            raise AssertionError("publication row was added before approval validation")

        def flush(self):
            self.write_calls.append(("flush", None))
            raise AssertionError("publication rows were flushed before approval validation")

        def execute(self, statement):
            self.write_calls.append(("execute", statement))
            raise AssertionError("published views were refreshed before approval validation")

        def refresh(self, item):
            self.write_calls.append(("refresh", item))
            raise AssertionError("publication row was refreshed before approval validation")

    session = RecordingSession()

    class RecordingBridge:
        _publication_pack_from_artifact = runtime.RuntimePersistenceBridge._publication_pack_from_artifact
        persist_publication_execution = runtime.RuntimePersistenceBridge.persist_publication_execution

        def __init__(self):
            self.service_user_calls = 0

        @contextmanager
        def session_scope(self):
            yield session

        def ensure_publication_service_user(self, _session):
            self.service_user_calls += 1
            raise AssertionError("service user resolved before approval validation")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    queue_item = {
        "id": str(uuid4()),
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": str(decision_id),
        "status": "completed",
        "priority": "medium",
        "payload_json": {
            "review_decision_id": str(decision_id),
            "review_case_id": str(decision.review_case_id) if decision is not None else str(uuid4()),
            "human_decision": "approve",
            "human_approved": True,
            "approval_actor": queued_actor,
        },
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    run_record = {
        "id": str(uuid4()),
        "model": "test",
        "prompt_version": "test",
        "tool_trace_json": {},
        "result_status": "completed",
        "started_at": now,
        "completed_at": now,
    }
    artifact_record = {
        "id": str(uuid4()),
        "artifact_type": "publication",
        "schema_version": "1",
        "created_at": now,
        "payload_json": {
            "decision": "stage",
            "staged_symbol_revisions": [str(uuid4())],
            "release_target": "standards-current",
            "publication_pack": {"pack_code": "blocked-pack", "effective_date": now[:10]},
        },
    }
    bridge = RecordingBridge()

    with pytest.raises(RuntimeError, match=error_match):
        bridge.persist_publication_execution(queue_item, run_record, artifact_record, {"id": str(uuid4())})

    assert session.get_calls == [(HumanReviewDecision, decision_id)]
    assert session.write_calls == []
    assert bridge.service_user_calls == 0


@pytest.mark.parametrize(
    ("failure_case", "error_match"),
    [
        ("substituted", "does not exactly match trusted publication handoff"),
        ("appended", "does not exactly match trusted publication handoff"),
        ("missing", "does not exactly match trusted publication handoff"),
        ("duplicate_artifact", "duplicate revision IDs"),
        ("duplicate_handoff", "duplicate revision IDs"),
        ("wrong_revision_decision", "content identity has changed"),
        ("missing_revision_decision", "content identity has changed"),
    ],
)
def test_publication_persistence_rejects_unapproved_revision_scope_before_any_write(
    failure_case, error_match
):
    decision = approved_decision()
    trusted_revision_id = uuid4()
    other_revision_id = uuid4()
    trusted_ids = [trusted_revision_id]
    staged_ids = [trusted_revision_id]
    revision_decision_id = decision.id

    if failure_case == "substituted":
        staged_ids = [other_revision_id]
    elif failure_case == "appended":
        staged_ids = [trusted_revision_id, other_revision_id]
    elif failure_case == "missing":
        trusted_ids = [trusted_revision_id, other_revision_id]
    elif failure_case == "duplicate_artifact":
        staged_ids = [trusted_revision_id, trusted_revision_id]
    elif failure_case == "duplicate_handoff":
        trusted_ids = [trusted_revision_id, trusted_revision_id]
        staged_ids = list(trusted_ids)
    elif failure_case == "wrong_revision_decision":
        revision_decision_id = uuid4()
    elif failure_case == "missing_revision_decision":
        revision_decision_id = None

    revision = SimpleNamespace(
        id=trusted_revision_id,
        symbol_id=uuid4(),
        revision_label="A",
        payload_json=(
            {"review_decision_id": str(revision_decision_id)}
            if revision_decision_id is not None
            else {}
        ),
    )
    other_revision = SimpleNamespace(
        id=other_revision_id,
        symbol_id=uuid4(),
        revision_label="B",
        payload_json={"review_decision_id": str(decision.id)},
    )
    revision_by_id = {
        trusted_revision_id: revision,
        other_revision_id: other_revision,
    }
    approved_target_by_id = {
        trusted_revision_id: SimpleNamespace(
            id=trusted_revision_id,
            symbol_id=revision.symbol_id,
            revision_label=revision.revision_label,
            payload_json={"review_decision_id": str(decision.id)},
        ),
        other_revision_id: SimpleNamespace(
            id=other_revision_id,
            symbol_id=other_revision.symbol_id,
            revision_label=other_revision.revision_label,
            payload_json={"review_decision_id": str(decision.id)},
        ),
    }
    target_revisions = [approved_target_by_id[revision_id] for revision_id in trusted_ids if revision_id in approved_target_by_id]
    revision_targets_json = runtime.build_publication_approval_revision_targets(None, target_revisions)
    approval_content_sha256 = runtime._canonical_json_sha256(revision_targets_json)
    approval_target = SimpleNamespace(
        id=uuid4(),
        review_decision_id=decision.id,
        review_case_id=decision.review_case_id,
        revision_targets_json=revision_targets_json,
        content_sha256=approval_content_sha256,
    )

    class PublicationApprovalTargetQuery:
        def filter_by(self, **kwargs):
            assert kwargs == {"review_decision_id": decision.id}
            return self

        def one_or_none(self):
            return approval_target

    class RecordingSession:
        def __init__(self):
            self.get_calls = []
            self.write_calls = []

        def get(self, model, key):
            self.get_calls.append((model, key))
            if model is HumanReviewDecision and key == decision.id:
                return decision
            if model is SymbolRevision and key == trusted_revision_id:
                return revision
            if model is SymbolRevision and key == other_revision_id:
                return other_revision
            raise AssertionError(f"unexpected durable lookup: {model} {key}")

        def query(self, model):
            if model is PublicationApprovalTarget:
                return PublicationApprovalTargetQuery()
            self.write_calls.append(("query", model))
            raise AssertionError("unexpected publication query before revision-scope validation")

        def add(self, item):
            self.write_calls.append(("add", item))
            raise AssertionError("publication row was added before revision-scope validation")

        def flush(self):
            self.write_calls.append(("flush", None))
            raise AssertionError("publication rows were flushed before revision-scope validation")

        def execute(self, statement):
            self.write_calls.append(("execute", statement))
            raise AssertionError("published views were refreshed before revision-scope validation")

        def refresh(self, item):
            self.write_calls.append(("refresh", item))
            raise AssertionError("publication row was refreshed before revision-scope validation")

    session = RecordingSession()

    class RecordingBridge:
        _publication_pack_from_artifact = runtime.RuntimePersistenceBridge._publication_pack_from_artifact
        persist_publication_execution = runtime.RuntimePersistenceBridge.persist_publication_execution

        def __init__(self):
            self.service_user_calls = 0

        @contextmanager
        def session_scope(self):
            yield session

        def ensure_publication_service_user(self, _session):
            self.service_user_calls += 1
            raise AssertionError("service user resolved before revision-scope validation")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    queue_item = {
        "id": str(uuid4()),
        "agent_id": "rupert",
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "status": "completed",
        "priority": "medium",
        "payload_json": {
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "human_decision": "approve",
            "human_approved": True,
            "approval_actor": publication_handoff.approval_actor_snapshot(decision),
            "approval_target_id": str(approval_target.id),
            "approval_content_sha256": approval_target.content_sha256,
            "symbol_revision_ids": [str(item) for item in trusted_ids],
        },
        "created_at": now,
        "started_at": now,
        "completed_at": now,
    }
    run_record = {
        "id": str(uuid4()),
        "model": "test",
        "prompt_version": "test",
        "tool_trace_json": {},
        "result_status": "completed",
        "started_at": now,
        "completed_at": now,
    }
    artifact_record = {
        "id": str(uuid4()),
        "artifact_type": "publication",
        "schema_version": "1",
        "created_at": now,
        "payload_json": {
            "decision": "stage",
            "staged_symbol_revisions": [str(item) for item in staged_ids],
            "approval_target_id": str(approval_target.id),
            "approval_content_sha256": approval_target.content_sha256,
            "release_target": "standards-current",
            "publication_pack": {"pack_code": "blocked-pack", "effective_date": now[:10]},
        },
    }
    bridge = RecordingBridge()

    with pytest.raises(RuntimeError, match=error_match):
        bridge.persist_publication_execution(
            queue_item,
            run_record,
            artifact_record,
            {"id": str(uuid4())},
        )

    assert session.get_calls[0] == (HumanReviewDecision, decision.id)
    assert session.write_calls == []
    assert bridge.service_user_calls == 0
