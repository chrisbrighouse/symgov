from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..models import (
    AgentDefinition,
    AgentQueueItem,
    Attachment,
    AuditEvent,
    ClassificationRecord,
    HumanReviewDecision,
    IntakeRecord,
    ProvenanceAssessment,
    ReviewCase,
    ReviewCaseAction,
    ValidationReport,
)
from ..publication_handoff import execute_publication_handoff
from ..runtime import download_object_bytes
from ..schemas import (
    WorkspaceAgentQueueItemListResponse,
    WorkspaceAgentQueueItemResponse,
    WorkspaceDaisyAssignmentProposalResponse,
    WorkspaceDaisyEvidenceRequestResponse,
    WorkspaceDaisyReportListResponse,
    WorkspaceDaisyReportResponse,
    WorkspaceDaisyStageTransitionResponse,
    WorkspaceHumanReviewDecisionSummary,
    WorkspaceReviewActionResponse,
    WorkspaceReviewCaseListResponse,
    WorkspaceReviewCaseResponse,
    WorkspaceReviewChildResponse,
    WorkspaceReviewDecisionRequest,
    WorkspaceReviewDecisionResponse,
)
from ..settings import get_settings


router = APIRouter(prefix="/workspace", tags=["workspace"])
legacy_router = APIRouter(tags=["workspace"])
DAISY_RUNTIME_REPORT_ROOT = Path("/data/.openclaw/workspaces/daisy/runtime/review_coordination_reports")
DECISION_TRANSITIONS = {
    "approve": {
        "to_stage": "ready_for_publication_handoff",
        "action_code": "prepare_publication_handoff",
        "target_agent_slug": "rupert",
        "target_stage": "publication_staging",
        "close": False,
    },
    "reject": {
        "to_stage": "rejected",
        "action_code": "close_as_rejected",
        "target_agent_slug": None,
        "target_stage": None,
        "close": True,
    },
    "request_changes": {
        "to_stage": "changes_requested",
        "action_code": "request_changes",
        "target_agent_slug": None,
        "target_stage": "review_follow_up",
        "close": False,
    },
    "more_evidence": {
        "to_stage": "waiting_for_evidence",
        "action_code": "request_contributor_evidence",
        "target_agent_slug": None,
        "target_stage": "evidence_collection",
        "close": False,
    },
    "rename_classify": {
        "to_stage": "classification_change_requested",
        "action_code": "request_classification_update",
        "target_agent_slug": "libby",
        "target_stage": "classification_review",
        "close": False,
    },
    "duplicate": {
        "to_stage": "duplicate_closed",
        "action_code": "close_as_duplicate",
        "target_agent_slug": None,
        "target_stage": None,
        "close": True,
    },
    "deleted": {
        "to_stage": "deleted_closed",
        "action_code": "delete_proposed_child",
        "target_agent_slug": None,
        "target_stage": None,
        "close": True,
    },
    "defer": {
        "to_stage": "deferred",
        "action_code": "defer_review_case",
        "target_agent_slug": None,
        "target_stage": "deferred",
        "close": False,
    },
    "child_actions_submitted": {
        "to_stage": "review_actions_recorded",
        "action_code": "record_child_review_actions",
        "target_agent_slug": None,
        "target_stage": "review_follow_up",
        "close": False,
    },
}
CHILD_ACTION_CODES = set(DECISION_TRANSITIONS)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_review_case_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Review case not found.") from exc


def build_review_summary(validation_report: ValidationReport, child_count: int, source_file_name: str) -> str:
    detail = (validation_report.report_json or {}).get("decision") or validation_report.validation_status
    if child_count:
        return f"{source_file_name} produced {child_count} proposed child symbols and remains in review ({detail})."
    return f"{source_file_name} remains in review after technical validation ({detail})."


def build_review_notes(validation_report: ValidationReport, child_count: int) -> list[str]:
    normalized = validation_report.normalized_payload_json or {}
    ocr = normalized.get("ocr_label_summary") or {}
    notes = [f"Review {child_count} proposed raster split crops before publication." if child_count else "Review raster validation outcome before publication."]
    if ocr.get("available"):
        notes.append(f"OCR labels assigned to {ocr.get('assigned_count', 0)} proposed child symbols.")
    if validation_report.defect_count:
        notes.append(f"Validation recorded {validation_report.defect_count} defects that may require changes.")
    return notes


def build_provenance_notes(provenance_assessment: ProvenanceAssessment) -> list[str]:
    notes = []
    if provenance_assessment.rights_status:
        notes.append(f"Tracy rights status: {provenance_assessment.rights_status}.")
    source_refs = ((provenance_assessment.evidence_json or {}).get("standards_source_refs") or [])
    if source_refs:
        notes.append(f"Upstream source refs captured: {len(source_refs)}.")
    else:
        notes.append("No upstream source refs were captured.")
    declaration = ((provenance_assessment.evidence_json or {}).get("declaration_excerpt") or "").strip()
    if declaration:
        notes.append(f"Declaration excerpt: {declaration}")
    return notes


def build_preview_url(review_case_id: str, object_key: str | None) -> str | None:
    if not object_key:
        return None
    return f"/api/v1/workspace/review-cases/{review_case_id}/children/preview?object_key={quote(object_key, safe='')}"


def build_source_preview_url(review_case_id: str, object_key: str | None) -> str | None:
    if not object_key:
        return None
    return f"/api/v1/workspace/review-cases/{review_case_id}/source/preview"


def resolve_source_object_key(validation_report: ValidationReport) -> str | None:
    normalized = validation_report.normalized_payload_json or {}
    return (
        normalized.get("raw_object_key")
        or normalized.get("origin_object_key")
        or normalized.get("object_key")
        or (normalized.get("single_symbol_candidate") or {}).get("raw_object_key")
        or (validation_report.report_json or {}).get("raw_object_key")
        or (validation_report.report_json or {}).get("origin_object_key")
    )


def resolve_source_object_key_from_intake(intake_record: IntakeRecord) -> str | None:
    normalized = intake_record.normalized_submission_json or {}
    return intake_record.raw_object_key or normalized.get("raw_object_key") or normalized.get("origin_object_key")


def build_children(validation_report: ValidationReport, review_case_id: str, source_file_name: str) -> list[WorkspaceReviewChildResponse]:
    normalized = validation_report.normalized_payload_json or {}
    manifest = normalized.get("derivative_manifest") or {}
    children = []
    for child in manifest.get("children") or []:
        object_key = child.get("attachment_object_key")
        proposed_symbol_id = str(child.get("proposed_symbol_id") or child.get("file_name") or "UNSPECIFIED")
        file_name = str(child.get("file_name") or "child.png")
        children.append(
            WorkspaceReviewChildResponse(
                id=str(object_key or f"{proposed_symbol_id}:{file_name}"),
                proposedSymbolId=proposed_symbol_id,
                proposedSymbolName=str(child.get("proposed_symbol_name") or file_name or "Unnamed child"),
                fileName=file_name,
                parentFileName=source_file_name,
                nameSource=child.get("name_source"),
                attachmentObjectKey=object_key,
                previewUrl=build_preview_url(review_case_id, object_key),
            )
        )
    return children


def resolve_source_file_name(validation_report: ValidationReport) -> str:
    normalized = validation_report.normalized_payload_json or {}
    return (
        normalized.get("file_name")
        or (normalized.get("split_plan_summary") or {}).get("source_file_name")
        or "Submitted PNG"
    )


def resolve_source_file_name_from_intake(intake_record: IntakeRecord) -> str:
    normalized = intake_record.normalized_submission_json or {}
    raw_input_path = normalized.get("raw_input_path")
    if raw_input_path:
        return Path(str(raw_input_path)).name
    raw_object_key = intake_record.raw_object_key or normalized.get("raw_object_key")
    if raw_object_key:
        return Path(str(raw_object_key)).name
    return str(normalized.get("candidate_symbol_id") or "Submitted symbol")


def load_current_classification(
    session: Session,
    *,
    review_case_id: str,
    provenance_assessment_id: str | None = None,
) -> ClassificationRecord | None:
    query = session.query(ClassificationRecord).filter(ClassificationRecord.status == "current")
    if provenance_assessment_id:
        query = query.filter(
            (ClassificationRecord.review_case_id == review_case_id)
            | (ClassificationRecord.provenance_assessment_id == provenance_assessment_id)
        )
    else:
        query = query.filter(ClassificationRecord.review_case_id == review_case_id)
    return query.order_by(ClassificationRecord.created_at.desc()).first()


def apply_classification_fields(payload: dict, classification_record: ClassificationRecord | None) -> WorkspaceReviewCaseResponse:
    if classification_record is not None:
        payload.update(
            {
                "classificationStatus": classification_record.classification_status,
                "classificationConfidence": float(classification_record.confidence),
                "libbyApproved": classification_record.libby_approved,
                "engineeringDiscipline": classification_record.discipline,
                "format": classification_record.format,
                "industry": classification_record.industry,
                "symbolFamily": classification_record.symbol_family,
                "processCategory": classification_record.process_category,
                "parentEquipmentClass": classification_record.parent_equipment_class,
                "standardsSource": classification_record.standards_source,
                "libraryProvenanceClass": classification_record.library_provenance_class,
                "sourceClassification": classification_record.source_classification,
                "aliases": classification_record.aliases_json or [],
                "keywords": classification_record.search_terms_json or [],
                "sourceRefs": classification_record.source_refs_json or [],
                "classificationSummary": classification_record.review_summary,
            }
        )
    return WorkspaceReviewCaseResponse(**payload)


def build_decision_summary(decision: HumanReviewDecision | None) -> WorkspaceHumanReviewDecisionSummary | None:
    if decision is None:
        return None
    return WorkspaceHumanReviewDecisionSummary(
        id=str(decision.id),
        decisionCode=decision.decision_code,
        decisionSummary=decision.decision_summary,
        decisionNote=decision.decision_note,
        deciderName=decision.decider_name,
        deciderRole=decision.decider_role,
        fromStage=decision.from_stage,
        toStage=decision.to_stage,
        createdAt=isoformat_utc(decision.created_at),
    )


def load_latest_decision(session: Session, review_case_id: str) -> HumanReviewDecision | None:
    return (
        session.query(HumanReviewDecision)
        .filter(HumanReviewDecision.review_case_id == parse_review_case_id(review_case_id))
        .filter(HumanReviewDecision.superseded_at.is_(None))
        .order_by(HumanReviewDecision.created_at.desc())
        .first()
    )


def attach_latest_decision(session: Session, payload: WorkspaceReviewCaseResponse) -> WorkspaceReviewCaseResponse:
    latest_decision = load_latest_decision(session, payload.id)
    return payload.model_copy(update={"latestDecision": build_decision_summary(latest_decision)})


def load_daisy_report_payloads(review_case_id: str | None = None) -> list[dict]:
    if not DAISY_RUNTIME_REPORT_ROOT.exists():
        return []

    items: list[dict] = []
    for path in sorted(DAISY_RUNTIME_REPORT_ROOT.glob("*.json"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if review_case_id and str(payload.get("review_case_id") or "") != review_case_id:
            continue
        items.append(payload)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items


def build_daisy_report_item(report_payload: dict, review_case: ReviewCase | None) -> WorkspaceDaisyReportResponse:
    report_json = report_payload.get("report_json") or {}
    return WorkspaceDaisyReportResponse(
        id=str(report_payload.get("id") or "unknown"),
        queueItemId=str(report_payload.get("queue_item_id") or "unknown"),
        reviewCaseId=report_payload.get("review_case_id"),
        sourceType=report_payload.get("source_type"),
        sourceId=report_payload.get("source_id"),
        coordinationStatus=str(report_payload.get("coordination_status") or "unknown"),
        coordinationSummary=str(report_payload.get("coordination_summary") or ""),
        createdAt=str(report_payload.get("created_at") or ""),
        currentStage=review_case.current_stage if review_case is not None else None,
        escalationLevel=review_case.escalation_level if review_case is not None else None,
        decision=report_json.get("decision"),
        confidence=report_json.get("confidence"),
        escalationTarget=report_json.get("escalation_target"),
        defectCount=len(report_json.get("defects") or []),
        assignmentProposals=[
            WorkspaceDaisyAssignmentProposalResponse(
                proposalRank=int(item.get("proposal_rank") or 0),
                reviewer=str(item.get("reviewer") or "unknown"),
                role=str(item.get("role") or "unknown"),
                reason=str(item.get("reason") or ""),
            )
            for item in (report_payload.get("assignment_proposals") or [])
        ],
        stageTransitionProposals=[
            WorkspaceDaisyStageTransitionResponse(
                fromStage=str(item.get("from_stage") or ""),
                toStage=str(item.get("to_stage") or ""),
                action=str(item.get("action") or ""),
                reason=str(item.get("reason") or ""),
            )
            for item in (report_payload.get("stage_transition_proposals") or [])
        ],
        contributorEvidenceRequests=[
            WorkspaceDaisyEvidenceRequestResponse(
                requestType=str(item.get("request_type") or ""),
                detail=str(item.get("detail") or ""),
            )
            for item in (report_payload.get("contributor_evidence_requests") or [])
        ],
    )


def build_validation_workspace_item(
    review_case: ReviewCase,
    validation_report: ValidationReport,
    intake_record: IntakeRecord,
    classification_record: ClassificationRecord | None = None,
) -> WorkspaceReviewCaseResponse:
    source_file_name = resolve_source_file_name(validation_report)
    source_object_key = resolve_source_object_key(validation_report)
    children = build_children(validation_report, str(review_case.id), source_file_name)
    primary_symbol_id = children[0].proposedSymbolId if children else str(
        (intake_record.normalized_submission_json or {}).get("candidate_symbol_id") or "PNG-REVIEW"
    )
    opened_at = review_case.opened_at if isinstance(review_case.opened_at, datetime) else datetime.now(timezone.utc)
    due = opened_at + timedelta(days=2)
    payload = {
        "id": str(review_case.id),
        "symbolId": primary_symbol_id,
        "title": f"Review raster split proposal for {source_file_name}",
        "owner": "Unassigned",
        "due": due.date().isoformat(),
        "priority": review_case.escalation_level.title(),
        "risk": "Medium" if review_case.escalation_level.lower() == "medium" else review_case.escalation_level.title(),
        "pages": 1,
        "packs": 0,
        "status": review_case.current_stage.replace("_", " ").title(),
        "summary": build_review_summary(validation_report, len(children), source_file_name),
        "clarifications": build_review_notes(validation_report, len(children)),
        "currentStage": review_case.current_stage,
        "escalationLevel": review_case.escalation_level,
        "openedAt": isoformat_utc(opened_at),
        "validationStatus": validation_report.validation_status,
        "defectCount": validation_report.defect_count,
        "sourceFileName": source_file_name,
        "sourceObjectKey": source_object_key,
        "sourcePreviewUrl": build_source_preview_url(str(review_case.id), source_object_key),
        "intakeRecordId": str(intake_record.id),
        "childCount": len(children),
        "children": children,
    }
    return apply_classification_fields(payload, classification_record)


def build_provenance_workspace_item(
    review_case: ReviewCase,
    provenance_assessment: ProvenanceAssessment,
    intake_record: IntakeRecord,
    classification_record: ClassificationRecord | None = None,
) -> WorkspaceReviewCaseResponse:
    source_file_name = resolve_source_file_name_from_intake(intake_record)
    source_object_key = resolve_source_object_key_from_intake(intake_record)
    normalized = intake_record.normalized_submission_json or {}
    opened_at = review_case.opened_at if isinstance(review_case.opened_at, datetime) else datetime.now(timezone.utc)
    due = opened_at + timedelta(days=2)
    symbol_id = str(normalized.get("candidate_symbol_id") or intake_record.id)
    payload = {
        "id": str(review_case.id),
        "symbolId": symbol_id,
        "title": f"Review classification for {source_file_name}",
        "owner": "Unassigned",
        "due": due.date().isoformat(),
        "priority": review_case.escalation_level.title(),
        "risk": "Medium" if review_case.escalation_level.lower() == "medium" else review_case.escalation_level.title(),
        "pages": 1,
        "packs": 0,
        "status": review_case.current_stage.replace("_", " ").title(),
        "summary": classification_record.review_summary if classification_record else provenance_assessment.summary,
        "clarifications": build_provenance_notes(provenance_assessment),
        "currentStage": review_case.current_stage,
        "escalationLevel": review_case.escalation_level,
        "openedAt": isoformat_utc(opened_at),
        "validationStatus": "classification_pending",
        "defectCount": len(((provenance_assessment.report_json or {}).get("defects") or [])),
        "sourceFileName": source_file_name,
        "sourceObjectKey": source_object_key,
        "sourcePreviewUrl": build_source_preview_url(str(review_case.id), source_object_key),
        "intakeRecordId": str(intake_record.id),
        "childCount": 0,
        "children": [],
    }
    return apply_classification_fields(payload, classification_record)


@router.get(
    "/agent-queue-items",
    response_model=WorkspaceAgentQueueItemListResponse,
    responses={500: {"description": "Server error"}},
)
@legacy_router.get(
    "/workspace/agent-queue-items",
    response_model=WorkspaceAgentQueueItemListResponse,
    include_in_schema=False,
)
def list_workspace_agent_queue_items(
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> WorkspaceAgentQueueItemListResponse:
    rows = session.execute(
        select(AgentQueueItem, AgentDefinition)
        .join(AgentDefinition, AgentDefinition.id == AgentQueueItem.agent_id)
        .order_by(AgentQueueItem.created_at.desc())
        .limit(limit)
    ).all()

    items = [
        WorkspaceAgentQueueItemResponse(
            id=str(queue_item.id),
            agentId=definition.slug,
            agentName=definition.display_name,
            queueFamily=definition.queue_family,
            sourceType=queue_item.source_type,
            sourceId=str(queue_item.source_id),
            status=queue_item.status,
            priority=queue_item.priority,
            payload=queue_item.payload_json or {},
            confidence=float(queue_item.confidence) if queue_item.confidence is not None else None,
            escalationReason=queue_item.escalation_reason,
            createdAt=isoformat_utc(queue_item.created_at),
            startedAt=isoformat_utc(queue_item.started_at) if queue_item.started_at else None,
            completedAt=isoformat_utc(queue_item.completed_at) if queue_item.completed_at else None,
        )
        for queue_item, definition in rows
    ]

    return WorkspaceAgentQueueItemListResponse(items=items)


@router.get(
    "/review-cases",
    response_model=WorkspaceReviewCaseListResponse,
    responses={500: {"description": "Server error"}},
)
@legacy_router.get(
    "/workspace/review-cases",
    response_model=WorkspaceReviewCaseListResponse,
    include_in_schema=False,
)
def list_workspace_review_cases(
    session: Session = Depends(get_db_session),
) -> WorkspaceReviewCaseListResponse:
    review_cases = session.execute(
        select(ReviewCase)
        .where(ReviewCase.closed_at.is_(None))
        .order_by(ReviewCase.opened_at.desc())
    ).scalars().all()

    items = []
    for review_case in review_cases:
        if review_case.source_entity_type == "validation_report":
            validation_report = session.get(ValidationReport, review_case.source_entity_id)
            if validation_report is None or validation_report.source_type != "intake_record":
                continue
            intake_record = session.get(IntakeRecord, validation_report.source_id)
            if intake_record is None:
                continue
            classification_record = load_current_classification(session, review_case_id=str(review_case.id))
            items.append(
                attach_latest_decision(
                    session,
                    build_validation_workspace_item(review_case, validation_report, intake_record, classification_record),
                )
            )
            continue

        if review_case.source_entity_type == "provenance_assessment":
            provenance_assessment = session.get(ProvenanceAssessment, review_case.source_entity_id)
            if provenance_assessment is None:
                continue
            intake_record = session.get(IntakeRecord, provenance_assessment.intake_record_id)
            if intake_record is None:
                continue
            classification_record = load_current_classification(
                session,
                review_case_id=str(review_case.id),
                provenance_assessment_id=str(provenance_assessment.id),
            )
            items.append(
                attach_latest_decision(
                    session,
                    build_provenance_workspace_item(review_case, provenance_assessment, intake_record, classification_record),
                )
            )

    return WorkspaceReviewCaseListResponse(items=items)


def create_review_action(
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    action_code: str,
    action_status: str,
    payload: dict,
    target_agent_slug: str | None = None,
    target_stage: str | None = None,
) -> ReviewCaseAction:
    return ReviewCaseAction(
        review_case_id=review_case.id,
        decision_id=decision.id,
        action_code=action_code,
        action_status=action_status,
        target_agent_slug=target_agent_slug,
        target_stage=target_stage,
        action_payload_json=payload,
        created_by_type="human",
        created_by_id=decision.decided_by,
        created_at=decision.created_at,
    )


@router.post(
    "/review-cases/{review_case_id}/decisions",
    response_model=WorkspaceReviewDecisionResponse,
    responses={404: {"description": "Review case not found"}, 422: {"description": "Invalid decision"}},
)
def create_workspace_review_decision(
    review_case_id: str,
    request: WorkspaceReviewDecisionRequest,
    session: Session = Depends(get_db_session),
) -> WorkspaceReviewDecisionResponse:
    parsed_case_id = parse_review_case_id(review_case_id)
    review_case = session.get(ReviewCase, parsed_case_id)
    if review_case is None:
        raise HTTPException(status_code=404, detail="Review case not found.")

    decision_code = request.decisionCode.strip()
    if decision_code not in DECISION_TRANSITIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported review decision: {decision_code}.")

    invalid_child_actions = sorted(
        {
            child.action.strip()
            for child in request.childDecisions
            if child.action.strip() and child.action.strip() not in CHILD_ACTION_CODES
        }
    )
    if invalid_child_actions:
        raise HTTPException(status_code=422, detail=f"Unsupported child action(s): {', '.join(invalid_child_actions)}.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    previous_decisions = (
        session.query(HumanReviewDecision)
        .filter(HumanReviewDecision.review_case_id == review_case.id)
        .filter(HumanReviewDecision.superseded_at.is_(None))
        .all()
    )
    for previous in previous_decisions:
        previous.superseded_at = now

    transition = DECISION_TRANSITIONS[decision_code]
    from_stage = review_case.current_stage
    to_stage = transition["to_stage"]
    child_decisions = [
        child.model_dump()
        for child in request.childDecisions
        if child.action.strip() and child.action.strip() != "pending"
    ]
    decision_payload = {
        "child_decisions": child_decisions,
        "case_comment": request.caseComment,
        "review_case_id": str(review_case.id),
    }
    decision = HumanReviewDecision(
        review_case_id=review_case.id,
        decision_code=decision_code,
        decision_summary=f"{request.deciderName} recorded {decision_code.replace('_', ' ')}.",
        decision_note=request.decisionNote or None,
        decided_by=None,
        decider_name=request.deciderName,
        decider_role=request.deciderRole,
        from_stage=from_stage,
        to_stage=to_stage,
        decision_payload_json=decision_payload,
        created_at=now,
    )
    session.add(decision)
    session.flush()

    actions = [
        create_review_action(
            review_case=review_case,
            decision=decision,
            action_code=transition["action_code"],
            action_status="pending",
            payload={"decision_code": decision_code, "decision_note": request.decisionNote},
            target_agent_slug=transition["target_agent_slug"],
            target_stage=transition["target_stage"],
        )
    ]

    for child in child_decisions:
        child_transition = DECISION_TRANSITIONS[child["action"]]
        actions.append(
            create_review_action(
                review_case=review_case,
                decision=decision,
                action_code=f"child_{child_transition['action_code']}",
                action_status="pending",
                payload=child,
                target_agent_slug=child_transition["target_agent_slug"],
                target_stage=child_transition["target_stage"],
            )
        )

    for action in actions:
        session.add(action)

    review_case.current_stage = to_stage
    if transition["close"]:
        review_case.closed_at = now

    session.add(
        AuditEvent(
            entity_type="review_case",
            entity_id=review_case.id,
            action="human_review_decision_recorded",
            actor_id=None,
            payload_json={
                "decision_id": str(decision.id),
                "decision_code": decision_code,
                "from_stage": from_stage,
                "to_stage": to_stage,
                "child_decision_count": len(child_decisions),
            },
            created_at=now,
        )
    )
    session.commit()

    if decision_code == "approve":
        execute_publication_handoff(
            session,
            review_case_id=review_case.id,
            decision_id=decision.id,
        )
        refreshed_actions = (
            session.query(ReviewCaseAction)
            .filter(ReviewCaseAction.decision_id == decision.id)
            .order_by(ReviewCaseAction.created_at.asc())
            .all()
        )
        if refreshed_actions:
            actions = refreshed_actions
        session.refresh(review_case)

    return WorkspaceReviewDecisionResponse(
        reviewCaseId=str(review_case.id),
        decision=build_decision_summary(decision),
        actions=[
            WorkspaceReviewActionResponse(
                id=str(action.id),
                actionCode=action.action_code,
                actionStatus=action.action_status,
                targetAgentSlug=action.target_agent_slug,
                targetStage=action.target_stage,
                createdAt=isoformat_utc(action.created_at),
            )
            for action in actions
        ],
        currentStage=review_case.current_stage,
        closedAt=isoformat_utc(review_case.closed_at) if review_case.closed_at else None,
    )


@router.get(
    "/daisy/reports",
    response_model=WorkspaceDaisyReportListResponse,
    responses={500: {"description": "Server error"}},
)
@legacy_router.get(
    "/workspace/daisy/reports",
    response_model=WorkspaceDaisyReportListResponse,
    include_in_schema=False,
)
def list_workspace_daisy_reports(
    review_case_id: str | None = Query(default=None, min_length=1),
    session: Session = Depends(get_db_session),
) -> WorkspaceDaisyReportListResponse:
    report_payloads = load_daisy_report_payloads(review_case_id=review_case_id)
    review_case_ids = [payload.get("review_case_id") for payload in report_payloads if payload.get("review_case_id")]
    review_cases = {}
    if review_case_ids:
        rows = session.execute(select(ReviewCase).where(ReviewCase.id.in_(review_case_ids))).scalars().all()
        review_cases = {str(row.id): row for row in rows}

    items = [
        build_daisy_report_item(payload, review_cases.get(str(payload.get("review_case_id"))))
        for payload in report_payloads
    ]
    return WorkspaceDaisyReportListResponse(items=items)


@router.get("/review-cases/{review_case_id}/children/preview")
def get_workspace_review_child_preview(
    review_case_id: str,
    object_key: str = Query(min_length=1),
    session: Session = Depends(get_db_session),
) -> Response:
    row = session.execute(
        select(ReviewCase, ValidationReport)
        .join(
            ValidationReport,
            and_(
                ReviewCase.source_entity_type == "validation_report",
                ReviewCase.source_entity_id == ValidationReport.id,
            ),
        )
        .where(ReviewCase.id == review_case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Review case not found.")

    review_case, validation_report = row
    source_file_name = resolve_source_file_name(validation_report)
    children = build_children(validation_report, str(review_case.id), source_file_name)
    matching_child = next((child for child in children if child.attachmentObjectKey == object_key), None)
    if matching_child is None:
        raise HTTPException(status_code=404, detail="Review child preview not found.")

    attachment = session.execute(select(Attachment).where(Attachment.object_key == object_key)).scalar_one_or_none()
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)


@router.get("/review-cases/{review_case_id}/source/preview")
def get_workspace_review_source_preview(
    review_case_id: str,
    session: Session = Depends(get_db_session),
) -> Response:
    parsed_case_id = parse_review_case_id(review_case_id)
    review_case = session.get(ReviewCase, parsed_case_id)
    if review_case is None:
        raise HTTPException(status_code=404, detail="Review case not found.")

    object_key = None
    if review_case.source_entity_type == "validation_report":
        validation_report = session.get(ValidationReport, review_case.source_entity_id)
        object_key = resolve_source_object_key(validation_report) if validation_report is not None else None
    elif review_case.source_entity_type == "provenance_assessment":
        provenance_assessment = session.get(ProvenanceAssessment, review_case.source_entity_id)
        intake_record = session.get(IntakeRecord, provenance_assessment.intake_record_id) if provenance_assessment is not None else None
        object_key = resolve_source_object_key_from_intake(intake_record) if intake_record is not None else None

    if not object_key:
        raise HTTPException(status_code=404, detail="Review source preview not found.")

    attachment = session.execute(select(Attachment).where(Attachment.object_key == object_key)).scalar_one_or_none()
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)
