from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..dependencies import get_db_session
from ..models import Attachment, ClassificationRecord, IntakeRecord, ProvenanceAssessment, ReviewCase, ValidationReport
from ..runtime import download_object_bytes
from ..schemas import (
    WorkspaceDaisyAssignmentProposalResponse,
    WorkspaceDaisyEvidenceRequestResponse,
    WorkspaceDaisyReportListResponse,
    WorkspaceDaisyReportResponse,
    WorkspaceDaisyStageTransitionResponse,
    WorkspaceReviewCaseListResponse,
    WorkspaceReviewCaseResponse,
    WorkspaceReviewChildResponse,
)
from ..settings import get_settings


router = APIRouter(prefix="/workspace", tags=["workspace"])
legacy_router = APIRouter(tags=["workspace"])
DAISY_RUNTIME_REPORT_ROOT = Path("/data/.openclaw/workspaces/daisy/runtime/review_coordination_reports")


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        "intakeRecordId": str(intake_record.id),
        "childCount": 0,
        "children": [],
    }
    return apply_classification_fields(payload, classification_record)


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
        .where(ReviewCase.current_stage.in_(["raster_split_review", "provenance_review", "classification_review"]))
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
            items.append(build_validation_workspace_item(review_case, validation_report, intake_record, classification_record))
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
            items.append(build_provenance_workspace_item(review_case, provenance_assessment, intake_record, classification_record))

    return WorkspaceReviewCaseListResponse(items=items)


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
