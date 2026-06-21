from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from symgov_backend.models import ProvenanceAssessment, ReviewCase
from symgov_backend.routes import workspace as workspace_routes


def test_rights_evidence_payload_extracts_tracy_report_problem_fields():
    assessment = SimpleNamespace(
        id=uuid4(),
        queue_item_id=uuid4(),
        rights_status="restricted",
        rights_disposition="review_required",
        processing_outcome="blocked",
        risk_level="high",
        confidence=0.82,
        summary="Tracy found restricted source material.",
        evidence_json={"source_url": "https://example.com/source", "source_notes": "Vendor page"},
        report_json={
            "defects": [{"code": "TRACY-RIGHTS-001", "message": "Restricted"}],
            "recommended_actions": ["Confirm licence before publication."],
            "source_context": {"domain": "example.com"},
        },
    )

    payload = workspace_routes.build_rights_evidence_payload(assessment)

    assert payload.provenanceAssessmentId == str(assessment.id)
    assert payload.tracyQueueItemId == str(assessment.queue_item_id)
    assert payload.rightsStatus == "restricted"
    assert payload.rightsDisposition == "review_required"
    assert payload.processingOutcome == "blocked"
    assert payload.riskLevel == "high"
    assert payload.defects == [{"code": "TRACY-RIGHTS-001", "message": "Restricted"}]
    assert payload.recommendedActions == ["Confirm licence before publication."]
    assert payload.sourceContext == {"domain": "example.com"}
    assert payload.evidence["source_url"] == "https://example.com/source"


def test_rights_decision_transition_codes_are_separate_from_generic_review_decisions():
    expected = {
        "clear_rights",
        "restrict_publication",
        "request_rights_evidence",
        "mark_conflict",
        "defer_rights",
    }

    assert expected.issubset(workspace_routes.RIGHTS_DECISION_TRANSITIONS)
    assert expected.isdisjoint(workspace_routes.DECISION_TRANSITIONS)


def test_rights_review_decision_rejects_non_rights_cases_before_mutation():
    review_case_id = uuid4()
    review_case = ReviewCase(
        id=review_case_id,
        source_entity_type="provenance_assessment",
        source_entity_id=uuid4(),
        current_stage="classification_review",
        escalation_level="high",
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )

    class FakeSession:
        def get(self, model, key):
            if model is ReviewCase and key == review_case_id:
                return review_case
            return None

    request = workspace_routes.WorkspaceRightsReviewDecisionRequest(
        decisionCode="clear_rights",
        evidenceNote="Rights cleared by reviewer.",
        deciderName="Daisy",
        deciderRole="rights_reviewer",
    )

    with pytest.raises(workspace_routes.HTTPException) as exc_info:
        workspace_routes.create_workspace_rights_review_decision(str(review_case_id), request, session=FakeSession())

    assert exc_info.value.status_code == 422
    assert "not a rights review" in exc_info.value.detail


def test_rights_review_decision_updates_corrected_problem_fields_on_provenance_assessment():
    review_case_id = uuid4()
    assessment_id = uuid4()
    review_case = ReviewCase(
        id=review_case_id,
        source_entity_type="provenance_assessment",
        source_entity_id=assessment_id,
        current_stage="provenance_rights_review",
        escalation_level="high",
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    assessment = ProvenanceAssessment(
        id=assessment_id,
        queue_item_id=uuid4(),
        intake_record_id=uuid4(),
        rights_status="restricted",
        rights_disposition="review_required",
        processing_outcome="blocked",
        risk_level="high",
        confidence=0.9,
        summary="Restricted before reviewer correction.",
        evidence_json={"source_url": "https://old.example/source"},
        report_json={},
        assessed_at=datetime.now(timezone.utc),
    )

    class EmptyQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        def get(self, model, key):
            if model is ReviewCase and key == review_case_id:
                return review_case
            if model is ProvenanceAssessment and key == assessment_id:
                return assessment
            return None

        def query(self, _model):
            return EmptyQuery()

        def add(self, item):
            self.added.append(item)

        def flush(self):
            return None

        def commit(self):
            self.committed = True

        def refresh(self, _item):
            return None

    session = FakeSession()
    request = workspace_routes.WorkspaceRightsReviewDecisionRequest(
        decisionCode="clear_rights",
        correctedRightsStatus="cleared",
        correctedRightsDisposition="cleared",
        correctedProcessingOutcome="pass",
        licenseLabel="Permission confirmed",
        sourceUrl="https://new.example/permission",
        evidenceNote="Reviewer checked the source and corrected Tracy's restricted status.",
        deciderName="Daisy",
        deciderRole="rights_reviewer",
    )

    response = workspace_routes.create_workspace_rights_review_decision(str(review_case_id), request, session=session)

    assert response.updatedRights["corrected_rights_status"] == "cleared"
    assert assessment.rights_status == "cleared"
    assert assessment.rights_disposition == "cleared"
    assert assessment.processing_outcome == "pass"
    assert assessment.evidence_json["license_label"] == "Permission confirmed"
    assert assessment.evidence_json["source_url"] == "https://new.example/permission"
    assert assessment.evidence_json["reviewer_rights_correction"]["decision_code"] == "clear_rights"
    assert session.committed is True


def test_rights_review_decision_closes_review_case_so_it_leaves_rights_queue():
    review_case_id = uuid4()
    assessment_id = uuid4()
    review_case = ReviewCase(
        id=review_case_id,
        source_entity_type="provenance_assessment",
        source_entity_id=assessment_id,
        current_stage="provenance_rights_review",
        escalation_level="high",
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    assessment = ProvenanceAssessment(
        id=assessment_id,
        queue_item_id=uuid4(),
        intake_record_id=uuid4(),
        rights_status="restricted",
        rights_disposition="review_required",
        processing_outcome="blocked",
        risk_level="high",
        confidence=0.9,
        summary="Restricted before reviewer correction.",
        evidence_json={},
        report_json={},
        assessed_at=datetime.now(timezone.utc),
    )

    class EmptyQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        def get(self, model, key):
            if model is ReviewCase and key == review_case_id:
                return review_case
            if model is ProvenanceAssessment and key == assessment_id:
                return assessment
            return None

        def query(self, _model):
            return EmptyQuery()

        def add(self, item):
            self.added.append(item)

        def flush(self):
            return None

        def commit(self):
            self.committed = True

        def refresh(self, _item):
            return None

    request = workspace_routes.WorkspaceRightsReviewDecisionRequest(
        decisionCode="clear_rights",
        correctedRightsStatus="cleared",
        correctedRightsDisposition="cleared",
        correctedProcessingOutcome="pass",
        evidenceNote="Rights cleared by reviewer.",
        deciderName="Daisy",
        deciderRole="rights_reviewer",
    )

    response = workspace_routes.create_workspace_rights_review_decision(str(review_case_id), request, session=FakeSession())

    assert response.currentStage == "rights_cleared"
    assert response.closedAt is not None
    assert review_case.closed_at is not None
    assert response.actions[0].actionCode == "rights_clearance_recorded"


def test_rights_review_decision_normalizes_legacy_ui_values_to_database_safe_values():
    review_case_id = uuid4()
    assessment_id = uuid4()
    review_case = ReviewCase(
        id=review_case_id,
        source_entity_type="provenance_assessment",
        source_entity_id=assessment_id,
        current_stage="provenance_rights_review",
        escalation_level="high",
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    assessment = ProvenanceAssessment(
        id=assessment_id,
        queue_item_id=uuid4(),
        intake_record_id=uuid4(),
        rights_status="restricted",
        rights_disposition="restricted",
        processing_outcome="review_required",
        risk_level="high",
        confidence=0.9,
        summary="Restricted before reviewer correction.",
        evidence_json={},
        report_json={},
        assessed_at=datetime.now(timezone.utc),
    )

    class EmptyQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

    class FakeSession:
        def get(self, model, key):
            if model is ReviewCase and key == review_case_id:
                return review_case
            if model is ProvenanceAssessment and key == assessment_id:
                return assessment
            return None

        def query(self, _model):
            return EmptyQuery()

        def add(self, _item):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, _item):
            return None

    request = workspace_routes.WorkspaceRightsReviewDecisionRequest(
        decisionCode="clear_rights",
        correctedRightsStatus="cleared",
        correctedRightsDisposition="rights_cleared",
        correctedProcessingOutcome="continue",
        evidenceNote="Rights cleared by reviewer.",
    )

    response = workspace_routes.create_workspace_rights_review_decision(str(review_case_id), request, session=FakeSession())

    assert assessment.rights_disposition == "cleared"
    assert assessment.processing_outcome == "pass"
    assert response.updatedRights["corrected_rights_disposition"] == "cleared"
    assert response.updatedRights["corrected_processing_outcome"] == "pass"
