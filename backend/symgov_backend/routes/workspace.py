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
    ReviewSplitItem,
    ReviewSymbolProperty,
    SourcePackage,
    SymbolRevision,
    ValidationReport,
)
from ..publication_handoff import execute_publication_handoff
from ..review_followup_handoff import execute_review_followup_handoff
from ..runtime import coerce_uuid, download_object_bytes
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
    WorkspaceReviewSymbolPropertiesResponse,
    WorkspaceReviewSymbolPropertiesUpdateRequest,
    WorkspaceSplitReviewProcessItemResponse,
    WorkspaceSplitReviewProcessRequest,
    WorkspaceSplitReviewProcessResponse,
)
from ..settings import get_settings


router = APIRouter(prefix="/workspace", tags=["workspace"])
legacy_router = APIRouter(tags=["workspace"])
DAISY_RUNTIME_REPORT_ROOT = Path("/data/.openclaw/workspaces/daisy/runtime/review_coordination_reports")
OPEN_SPLIT_ITEM_STATUSES = ("awaiting_decision", "returned_for_review")
DECISION_TRANSITIONS = {
    "approve": {
        "to_stage": "ready_for_publication_handoff",
        "action_code": "prepare_publication_handoff",
        "target_agent_slug": "rupert",
        "target_stage": "publication_staging",
        "close": False,
    },
    "reject": {
        "to_stage": "libby_disposition_review",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "disposition_review",
        "close": False,
    },
    "request_changes": {
        "to_stage": "changes_requested",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "review_follow_up",
        "close": False,
    },
    "more_evidence": {
        "to_stage": "waiting_for_evidence",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "evidence_collection",
        "close": False,
    },
    "rename_classify": {
        "to_stage": "classification_change_requested",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "classification_review",
        "close": False,
    },
    "duplicate": {
        "to_stage": "libby_duplicate_review",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "duplicate_resolution",
        "close": False,
    },
    "deleted": {
        "to_stage": "libby_deletion_review",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "deletion_review",
        "close": False,
    },
    "defer": {
        "to_stage": "deferred",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "deferred",
        "close": False,
    },
    "child_actions_submitted": {
        "to_stage": "review_actions_recorded",
        "action_code": "route_review_follow_up_to_libby",
        "target_agent_slug": "libby",
        "target_stage": "review_follow_up",
        "close": False,
    },
}
CHILD_ACTION_ALIASES = {
    "approved": "approve",
    "rejected": "reject",
}
CHILD_ACTION_CODES = set(DECISION_TRANSITIONS) | set(CHILD_ACTION_ALIASES)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_review_case_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Review case not found.") from exc


def normalize_child_action(action: str) -> str:
    value = action.strip()
    return CHILD_ACTION_ALIASES.get(value, value)


def intake_id_for_review_case(
    session: Session,
    review_case: ReviewCase,
) -> uuid.UUID | None:
    if review_case.source_entity_type == "validation_report":
        validation_report = session.get(ValidationReport, review_case.source_entity_id)
        if validation_report is not None and validation_report.source_type == "intake_record":
            return validation_report.source_id
        return None
    if review_case.source_entity_type == "provenance_assessment":
        provenance_assessment = session.get(ProvenanceAssessment, review_case.source_entity_id)
        return provenance_assessment.intake_record_id if provenance_assessment is not None else None
    return None


def open_split_review_intake_ids(session: Session) -> set[uuid.UUID]:
    rows = session.execute(
        select(ReviewCase, ValidationReport)
        .join(
            ValidationReport,
            and_(
                ReviewCase.source_entity_type == "validation_report",
                ReviewCase.source_entity_id == ValidationReport.id,
            ),
        )
        .where(ReviewCase.closed_at.is_(None))
        .where(ReviewCase.current_stage == "raster_split_review")
        .where(ValidationReport.source_type == "intake_record")
    ).all()
    return {validation_report.source_id for _, validation_report in rows}


def suppress_parent_sheet_review(
    review_case: ReviewCase,
    intake_record_id: uuid.UUID | None,
    split_intake_ids: set[uuid.UUID],
) -> bool:
    return (
        review_case.source_entity_type == "provenance_assessment"
        and review_case.current_stage == "classification_review"
        and intake_record_id in split_intake_ids
    )


def close_parent_sheet_reviews_for_split(
    session: Session,
    *,
    split_review_case: ReviewCase,
    closed_at: datetime,
) -> list[str]:
    intake_record_id = intake_id_for_review_case(session, split_review_case)
    if intake_record_id is None:
        return []

    closed_ids = []
    rows = session.execute(
        select(ReviewCase, ProvenanceAssessment)
        .join(
            ProvenanceAssessment,
            and_(
                ReviewCase.source_entity_type == "provenance_assessment",
                ReviewCase.source_entity_id == ProvenanceAssessment.id,
            ),
        )
        .where(ReviewCase.closed_at.is_(None))
        .where(ReviewCase.current_stage == "classification_review")
        .where(ProvenanceAssessment.intake_record_id == intake_record_id)
    ).all()
    for review_case, _ in rows:
        review_case.current_stage = "superseded_by_raster_split"
        review_case.closed_at = closed_at
        closed_ids.append(str(review_case.id))
    return closed_ids


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


def package_display_id(session: Session, intake_record: IntakeRecord | None) -> str | None:
    if intake_record is None:
        return None
    normalized = intake_record.normalized_submission_json or {}
    code = str(normalized.get("source_package_code") or normalized.get("workspace_display_name") or "").strip().upper()
    if code:
        return code
    if intake_record.source_package_id:
        package = session.get(SourcePackage, intake_record.source_package_id)
        if package is not None:
            return package.package_code
    return None


def split_item_display_parts(split_item: ReviewSplitItem) -> tuple[str | None, int | None, str | None]:
    payload = split_item.payload_json or {}
    package_id = payload.get("package_display_id") if isinstance(payload, dict) else None
    sequence = payload.get("package_symbol_sequence") if isinstance(payload, dict) else None
    try:
        sequence = int(sequence) if sequence is not None else None
    except (TypeError, ValueError):
        sequence = None
    display_name = f"{package_id}-{sequence}" if package_id and sequence is not None else None
    return package_id, sequence, display_name


def queue_item_display_parts(session: Session, queue_item: AgentQueueItem) -> tuple[str | None, int | None, str | None]:
    payload = queue_item.payload_json or {}
    if isinstance(payload, dict):
        direct_display_name = payload.get("display_name") or payload.get("workspace_display_name") or payload.get("displayName")
        direct_package_id = payload.get("package_display_id") or payload.get("packageDisplayId")
        direct_sequence = payload.get("package_symbol_sequence") or payload.get("packageSymbolSequence")
        if direct_display_name:
            return direct_package_id, direct_sequence, direct_display_name

    review_case_id = payload.get("review_case_id") if isinstance(payload, dict) else None
    child_decisions = payload.get("child_decisions") if isinstance(payload, dict) else None
    child_decision = child_decisions[0] if isinstance(child_decisions, list) and child_decisions else None
    if child_decision is None and isinstance(payload, dict):
        report = payload.get("libby_follow_up_report")
        if isinstance(report, dict):
            report_child_decisions = report.get("child_decisions")
            child_decision = (
                report_child_decisions[0]
                if isinstance(report_child_decisions, list) and report_child_decisions
                else None
            )
            vlad_result = report.get("vlad_result")
            if child_decision is None and isinstance(vlad_result, dict):
                metadata = vlad_result.get("normalized_technical_metadata")
                requested_changes = metadata.get("requested_changes") if isinstance(metadata, dict) else None
                nested_child_decisions = (
                    requested_changes.get("child_decisions") if isinstance(requested_changes, dict) else None
                )
                child_decision = (
                    nested_child_decisions[0]
                    if isinstance(nested_child_decisions, list) and nested_child_decisions
                    else None
                )
    if child_decision is None and isinstance(payload, dict):
        decision_id = payload.get("review_decision_id") or (str(queue_item.source_id) if queue_item.source_type == "review_decision" else None)
        if decision_id:
            decision = session.get(HumanReviewDecision, coerce_uuid(decision_id))
            decision_payload = decision.decision_payload_json if decision is not None else {}
            if isinstance(decision_payload, dict):
                review_case_id = review_case_id or decision_payload.get("review_case_id")
                decision_children = decision_payload.get("child_decisions")
                child_decision = decision_children[0] if isinstance(decision_children, list) and decision_children else None

    if isinstance(child_decision, dict) and review_case_id:
        child_keys = {
            str(value)
            for value in (
                child_decision.get("childId"),
                child_decision.get("proposedSymbolId"),
                child_decision.get("fileName"),
            )
            if value
        }
        if child_keys:
            split_item = (
                session.query(ReviewSplitItem)
                .filter(ReviewSplitItem.review_case_id == coerce_uuid(review_case_id))
                .filter(
                    (ReviewSplitItem.child_key.in_(child_keys))
                    | (ReviewSplitItem.proposed_symbol_id.in_(child_keys))
                    | (ReviewSplitItem.file_name.in_(child_keys))
                )
                .one_or_none()
            )
            if split_item is not None:
                return split_item_display_parts(split_item)

    revision_ids = payload.get("symbol_revision_ids") if isinstance(payload, dict) else None
    if isinstance(revision_ids, list) and len(revision_ids) == 1:
        revision = session.get(SymbolRevision, coerce_uuid(revision_ids[0]))
        revision_payload = revision.payload_json if revision is not None else {}
        if isinstance(revision_payload, dict):
            display_name = revision_payload.get("display_name") or revision_payload.get("workspace_display_name")
            package_id = revision_payload.get("package_display_id")
            sequence = revision_payload.get("package_symbol_sequence")
            if display_name:
                return package_id, sequence, display_name

    intake_record = None
    intake_record_id = payload.get("intake_record_id") if isinstance(payload, dict) else None
    if intake_record_id:
        intake_record = session.get(IntakeRecord, coerce_uuid(intake_record_id))
    if intake_record is None:
        intake_record = session.query(IntakeRecord).filter(IntakeRecord.queue_item_id == queue_item.id).one_or_none()
    if intake_record is None and isinstance(payload, dict):
        object_key = payload.get("raw_object_key") or payload.get("origin_object_key")
        if object_key:
            intake_record = session.query(IntakeRecord).filter(IntakeRecord.raw_object_key == object_key).one_or_none()

    package_id = package_display_id(session, intake_record)
    return package_id, None, package_id


def split_child_key(child: dict, proposed_symbol_id: str, file_name: str) -> str:
    return str(child.get("attachment_object_key") or proposed_symbol_id or file_name)


def ensure_split_items(
    session: Session,
    *,
    review_case: ReviewCase,
    validation_report: ValidationReport,
    source_file_name: str,
) -> list[ReviewSplitItem]:
    normalized = validation_report.normalized_payload_json or {}
    manifest = normalized.get("derivative_manifest") or {}
    intake_record = (
        session.get(IntakeRecord, validation_report.source_id)
        if validation_report.source_type == "intake_record"
        else None
    )
    package_id = package_display_id(session, intake_record)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    items = []
    for index, child in enumerate(manifest.get("children") or [], start=1):
        object_key = child.get("attachment_object_key")
        proposed_symbol_id = str(child.get("proposed_symbol_id") or child.get("file_name") or "UNSPECIFIED")
        file_name = str(child.get("file_name") or "child.png")
        child_key = split_child_key(child, proposed_symbol_id, file_name)
        child_payload = {
            **child,
            "package_display_id": package_id,
            "package_symbol_sequence": index,
            "workspace_display_name": f"{package_id}-{index}" if package_id else None,
        }
        item_id = coerce_uuid(f"review-split-item:{review_case.id}:{child_key}")
        item = session.get(ReviewSplitItem, item_id)
        if item is None:
            item = ReviewSplitItem(
                id=item_id,
                review_case_id=review_case.id,
                child_key=child_key,
                proposed_symbol_id=proposed_symbol_id,
                proposed_symbol_name=str(child.get("proposed_symbol_name") or file_name or "Unnamed child"),
                file_name=file_name,
                parent_file_name=source_file_name,
                name_source=child.get("name_source"),
                attachment_object_key=object_key,
                status="awaiting_decision",
                payload_json=child_payload,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
        else:
            existing_payload = item.payload_json or {}
            existing_payload = {
                **existing_payload,
                "package_display_id": existing_payload.get("package_display_id") or package_id,
                "package_symbol_sequence": existing_payload.get("package_symbol_sequence") or index,
                "workspace_display_name": existing_payload.get("workspace_display_name")
                or (f"{package_id}-{index}" if package_id else None),
            }
            item.proposed_symbol_id = proposed_symbol_id
            item.proposed_symbol_name = str(child.get("proposed_symbol_name") or file_name or "Unnamed child")
            item.parent_file_name = source_file_name
            item.name_source = child.get("name_source")
            edited_asset = existing_payload.get("vlad_edited_asset") if isinstance(existing_payload, dict) else None
            if isinstance(edited_asset, dict) and edited_asset.get("attachment_object_key"):
                item.file_name = edited_asset.get("file_name") or Path(edited_asset["attachment_object_key"]).name
                item.attachment_object_key = edited_asset["attachment_object_key"]
            elif isinstance(edited_asset, dict) and edited_asset.get("object_key"):
                item.file_name = edited_asset.get("file_name") or Path(edited_asset["object_key"]).name
                item.attachment_object_key = edited_asset["object_key"]
            elif item.status == "awaiting_decision":
                item.file_name = file_name
                item.attachment_object_key = object_key
            elif not item.attachment_object_key:
                item.attachment_object_key = object_key
            item.payload_json = {**child_payload, **existing_payload}
            item.updated_at = now
        items.append(item)
    if items:
        session.flush()
    return items


def build_children(
    session: Session,
    review_case: ReviewCase,
    validation_report: ValidationReport,
    review_case_id: str,
    source_file_name: str,
) -> list[WorkspaceReviewChildResponse]:
    split_items = ensure_split_items(
        session,
        review_case=review_case,
        validation_report=validation_report,
        source_file_name=source_file_name,
    )
    open_statuses = {"awaiting_decision", "returned_for_review"}
    if split_items:
        children = []
        for item in split_items:
            if item.status not in open_statuses:
                continue
            item_package_id, item_sequence, item_display_name = split_item_display_parts(item)
            children.append(
                WorkspaceReviewChildResponse(
                    id=item.child_key,
                    proposedSymbolId=item.proposed_symbol_id,
                    proposedSymbolName=item.proposed_symbol_name,
                    displayName=item_display_name,
                    packageDisplayId=item_package_id,
                    packageSymbolSequence=item_sequence,
                    fileName=item.file_name,
                    parentFileName=item.parent_file_name,
                    nameSource=item.name_source,
                    attachmentObjectKey=item.attachment_object_key,
                    previewUrl=build_preview_url(review_case_id, item.attachment_object_key),
                    reviewStatus=item.status,
                    latestAction=item.latest_action,
                    latestNote=item.latest_note,
                    latestDetails=item.latest_details,
                    processedAt=isoformat_utc(item.processed_at) if item.processed_at else None,
                    downstreamAgentSlug=item.downstream_agent_slug,
                    downstreamQueueItemId=item.downstream_queue_item_id,
                )
            )
        return children

    normalized = validation_report.normalized_payload_json or {}
    manifest = normalized.get("derivative_manifest") or {}
    children = []
    package_id = package_display_id(
        session,
        session.get(IntakeRecord, validation_report.source_id) if validation_report.source_type == "intake_record" else None,
    )
    for index, child in enumerate(manifest.get("children") or [], start=1):
        object_key = child.get("attachment_object_key")
        proposed_symbol_id = str(child.get("proposed_symbol_id") or child.get("file_name") or "UNSPECIFIED")
        file_name = str(child.get("file_name") or "child.png")
        child_key = split_child_key(child, proposed_symbol_id, file_name)
        children.append(
            WorkspaceReviewChildResponse(
                id=child_key,
                proposedSymbolId=proposed_symbol_id,
                proposedSymbolName=str(child.get("proposed_symbol_name") or file_name or "Unnamed child"),
                displayName=f"{package_id}-{index}" if package_id else None,
                packageDisplayId=package_id,
                packageSymbolSequence=index,
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


def compact_text(value: str | None, limit: int) -> str:
    text_value = str(value or "").strip()
    return text_value[:limit]


def build_symbol_properties_response(properties: ReviewSymbolProperty) -> WorkspaceReviewSymbolPropertiesResponse:
    return WorkspaceReviewSymbolPropertiesResponse(
        id=str(properties.id),
        reviewCaseId=str(properties.review_case_id),
        splitItemId=str(properties.review_split_item_id) if properties.review_split_item_id else None,
        symbolRecordKey=properties.symbol_record_key,
        name=properties.name,
        description=properties.description or "",
        category=properties.category,
        discipline=properties.discipline,
        source=properties.source,
        updatedBy=properties.updated_by,
        updatedAt=isoformat_utc(properties.updated_at),
    )


def get_or_create_symbol_properties(
    session: Session,
    *,
    review_case: ReviewCase,
    split_item: ReviewSplitItem | None = None,
    classification_record: ClassificationRecord | None = None,
    default_name: str,
    default_description: str,
) -> ReviewSymbolProperty:
    symbol_record_key = split_item.child_key if split_item is not None else str(review_case.id)
    query = session.query(ReviewSymbolProperty).filter(
        ReviewSymbolProperty.review_case_id == review_case.id,
        ReviewSymbolProperty.symbol_record_key == symbol_record_key,
    )
    properties = query.one_or_none()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if properties is None:
        properties = ReviewSymbolProperty(
            id=coerce_uuid(f"review-symbol-properties:{review_case.id}:{symbol_record_key}"),
            review_case_id=review_case.id,
            review_split_item_id=split_item.id if split_item is not None else None,
            symbol_record_key=symbol_record_key,
            name=compact_text(default_name, 50) or compact_text(symbol_record_key, 50),
            description=compact_text(default_description, 256),
            category=compact_text(classification_record.category if classification_record else None, 80) or None,
            discipline=compact_text(classification_record.discipline if classification_record else None, 80) or None,
            source="agent_initial",
            updated_by=None,
            created_at=now,
            updated_at=now,
        )
        session.add(properties)
        session.flush()
        return properties

    if split_item is not None and properties.review_split_item_id is None:
        properties.review_split_item_id = split_item.id
    if classification_record is not None:
        if not properties.category:
            properties.category = compact_text(classification_record.category, 80) or None
        if not properties.discipline:
            properties.discipline = compact_text(classification_record.discipline, 80) or None
    session.add(properties)
    return properties


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
    session: Session,
    review_case: ReviewCase,
    validation_report: ValidationReport,
    intake_record: IntakeRecord,
    classification_record: ClassificationRecord | None = None,
) -> WorkspaceReviewCaseResponse:
    source_file_name = resolve_source_file_name(validation_report)
    source_object_key = resolve_source_object_key(validation_report)
    children = build_children(session, review_case, validation_report, str(review_case.id), source_file_name)
    package_id = package_display_id(session, intake_record)
    primary_symbol_id = children[0].proposedSymbolId if children else str(
        (intake_record.normalized_submission_json or {}).get("candidate_symbol_id") or "PNG-REVIEW"
    )
    opened_at = review_case.opened_at if isinstance(review_case.opened_at, datetime) else datetime.now(timezone.utc)
    due = opened_at + timedelta(days=2)
    payload = {
        "id": str(review_case.id),
        "symbolId": primary_symbol_id,
        "displayName": package_id,
        "packageDisplayId": package_id,
        "packageSymbolSequence": None,
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
        "symbolProperties": build_symbol_properties_response(
            get_or_create_symbol_properties(
                session,
                review_case=review_case,
                classification_record=classification_record,
                default_name=package_id or primary_symbol_id,
                default_description=build_review_summary(validation_report, len(children), source_file_name),
            )
        ),
        "children": children,
    }
    return apply_classification_fields(payload, classification_record)


def build_split_item_workspace_item(
    session: Session,
    review_case: ReviewCase,
    validation_report: ValidationReport,
    intake_record: IntakeRecord,
    split_item: ReviewSplitItem,
    classification_record: ClassificationRecord | None = None,
) -> WorkspaceReviewCaseResponse:
    source_file_name = resolve_source_file_name(validation_report)
    source_object_key = resolve_source_object_key(validation_report)
    opened_at = split_item.updated_at if isinstance(split_item.updated_at, datetime) else review_case.opened_at
    due = opened_at + timedelta(days=2)
    preview_url = build_preview_url(str(review_case.id), split_item.attachment_object_key)
    item_package_id, item_sequence, item_display_name = split_item_display_parts(split_item)
    child = WorkspaceReviewChildResponse(
        id=split_item.child_key,
        proposedSymbolId=split_item.proposed_symbol_id,
        proposedSymbolName=split_item.proposed_symbol_name,
        displayName=item_display_name,
        packageDisplayId=item_package_id,
        packageSymbolSequence=item_sequence,
        fileName=split_item.file_name,
        parentFileName=split_item.parent_file_name,
        nameSource=split_item.name_source,
        attachmentObjectKey=split_item.attachment_object_key,
        previewUrl=preview_url,
        reviewStatus=split_item.status,
        latestAction=split_item.latest_action,
        latestNote=split_item.latest_note,
        latestDetails=split_item.latest_details,
        processedAt=isoformat_utc(split_item.processed_at) if split_item.processed_at else None,
        downstreamAgentSlug=split_item.downstream_agent_slug,
        downstreamQueueItemId=split_item.downstream_queue_item_id,
    )
    symbol_properties = get_or_create_symbol_properties(
        session,
        review_case=review_case,
        split_item=split_item,
        classification_record=classification_record,
        default_name=split_item.proposed_symbol_name,
        default_description=f"{split_item.proposed_symbol_name} from {source_file_name}.",
    )
    payload = {
        "id": str(split_item.id),
        "reviewItemType": "split_item",
        "parentReviewCaseId": str(review_case.id),
        "splitItemId": str(split_item.id),
        "splitChildKey": split_item.child_key,
        "splitChildStatus": split_item.status,
        "symbolId": split_item.proposed_symbol_id,
        "displayName": item_display_name,
        "packageDisplayId": item_package_id,
        "packageSymbolSequence": item_sequence,
        "title": f"Review split symbol {split_item.proposed_symbol_id}",
        "owner": "Unassigned",
        "due": due.date().isoformat(),
        "priority": review_case.escalation_level.title(),
        "risk": "Medium" if review_case.escalation_level.lower() == "medium" else review_case.escalation_level.title(),
        "pages": 1,
        "packs": 0,
        "status": split_item.status.replace("_", " ").title(),
        "summary": f"{split_item.proposed_symbol_name} from {source_file_name} is awaiting its own human review decision.",
        "clarifications": build_review_notes(validation_report, 1),
        "currentStage": split_item.status,
        "escalationLevel": review_case.escalation_level,
        "openedAt": isoformat_utc(opened_at),
        "validationStatus": validation_report.validation_status,
        "defectCount": validation_report.defect_count,
        "sourceFileName": source_file_name,
        "sourceObjectKey": source_object_key,
        "sourcePreviewUrl": preview_url,
        "intakeRecordId": str(intake_record.id),
        "childCount": 1,
        "symbolProperties": build_symbol_properties_response(symbol_properties),
        "children": [child],
    }
    return apply_classification_fields(payload, classification_record)


def build_provenance_workspace_item(
    session: Session,
    review_case: ReviewCase,
    provenance_assessment: ProvenanceAssessment,
    intake_record: IntakeRecord,
    classification_record: ClassificationRecord | None = None,
) -> WorkspaceReviewCaseResponse:
    source_file_name = resolve_source_file_name_from_intake(intake_record)
    source_object_key = resolve_source_object_key_from_intake(intake_record)
    normalized = intake_record.normalized_submission_json or {}
    package_id = package_display_id(session, intake_record)
    opened_at = review_case.opened_at if isinstance(review_case.opened_at, datetime) else datetime.now(timezone.utc)
    due = opened_at + timedelta(days=2)
    symbol_id = str(normalized.get("candidate_symbol_id") or intake_record.id)
    summary = classification_record.review_summary if classification_record else provenance_assessment.summary
    payload = {
        "id": str(review_case.id),
        "symbolId": symbol_id,
        "displayName": package_id,
        "packageDisplayId": package_id,
        "packageSymbolSequence": None,
        "title": f"Review classification for {source_file_name}",
        "owner": "Unassigned",
        "due": due.date().isoformat(),
        "priority": review_case.escalation_level.title(),
        "risk": "Medium" if review_case.escalation_level.lower() == "medium" else review_case.escalation_level.title(),
        "pages": 1,
        "packs": 0,
        "status": review_case.current_stage.replace("_", " ").title(),
        "summary": summary,
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
        "symbolProperties": build_symbol_properties_response(
            get_or_create_symbol_properties(
                session,
                review_case=review_case,
                classification_record=classification_record,
                default_name=package_id or str(normalized.get("candidate_title") or symbol_id),
                default_description=summary,
            )
        ),
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

    items = []
    for queue_item, definition in rows:
        package_id, package_sequence, display_name = queue_item_display_parts(session, queue_item)
        items.append(WorkspaceAgentQueueItemResponse(
            id=str(queue_item.id),
            agentId=definition.slug,
            agentName=definition.display_name,
            queueFamily=definition.queue_family,
            sourceType=queue_item.source_type,
            sourceId=str(queue_item.source_id),
            displayName=display_name,
            packageDisplayId=package_id,
            packageSymbolSequence=package_sequence,
            status=queue_item.status,
            priority=queue_item.priority,
            payload=queue_item.payload_json or {},
            confidence=float(queue_item.confidence) if queue_item.confidence is not None else None,
            escalationReason=queue_item.escalation_reason,
            createdAt=isoformat_utc(queue_item.created_at),
            startedAt=isoformat_utc(queue_item.started_at) if queue_item.started_at else None,
            completedAt=isoformat_utc(queue_item.completed_at) if queue_item.completed_at else None,
        ))

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
    split_intake_ids = open_split_review_intake_ids(session)

    items = []
    emitted_split_item_ids: set[str] = set()
    for review_case in review_cases:
        if review_case.source_entity_type == "validation_report":
            validation_report = session.get(ValidationReport, review_case.source_entity_id)
            if validation_report is None or validation_report.source_type != "intake_record":
                continue
            intake_record = session.get(IntakeRecord, validation_report.source_id)
            if intake_record is None:
                continue
            classification_record = load_current_classification(session, review_case_id=str(review_case.id))
            source_file_name = resolve_source_file_name(validation_report)
            split_items = ensure_split_items(
                session,
                review_case=review_case,
                validation_report=validation_report,
                source_file_name=source_file_name,
            )
            open_split_items = [item for item in split_items if item.status in OPEN_SPLIT_ITEM_STATUSES]
            if open_split_items:
                for split_item in open_split_items:
                    emitted_split_item_ids.add(str(split_item.id))
                    items.append(
                        attach_latest_decision(
                            session,
                            build_split_item_workspace_item(
                                session,
                                review_case,
                                validation_report,
                                intake_record,
                                split_item,
                                classification_record,
                            ),
                        )
                    )
                continue
            items.append(
                attach_latest_decision(
                    session,
                    build_validation_workspace_item(session, review_case, validation_report, intake_record, classification_record),
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
            if suppress_parent_sheet_review(review_case, intake_record.id, split_intake_ids):
                continue
            classification_record = load_current_classification(
                session,
                review_case_id=str(review_case.id),
                provenance_assessment_id=str(provenance_assessment.id),
            )
            items.append(
                attach_latest_decision(
                    session,
                    build_provenance_workspace_item(session, review_case, provenance_assessment, intake_record, classification_record),
                )
            )

    split_rows = session.execute(
        select(ReviewSplitItem, ReviewCase, ValidationReport)
        .join(ReviewCase, ReviewCase.id == ReviewSplitItem.review_case_id)
        .join(
            ValidationReport,
            and_(
                ReviewCase.source_entity_type == "validation_report",
                ReviewCase.source_entity_id == ValidationReport.id,
            ),
        )
        .where(ReviewSplitItem.status.in_(OPEN_SPLIT_ITEM_STATUSES))
        .order_by(ReviewSplitItem.updated_at.desc())
    ).all()
    for split_item, review_case, validation_report in split_rows:
        if str(split_item.id) in emitted_split_item_ids or validation_report.source_type != "intake_record":
            continue
        intake_record = session.get(IntakeRecord, validation_report.source_id)
        if intake_record is None:
            continue
        classification_record = load_current_classification(session, review_case_id=str(review_case.id))
        emitted_split_item_ids.add(str(split_item.id))
        items.append(
            attach_latest_decision(
                session,
                build_split_item_workspace_item(
                    session,
                    review_case,
                    validation_report,
                    intake_record,
                    split_item,
                    classification_record,
                ),
            )
        )

    session.commit()
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


@router.patch(
    "/review-cases/{review_case_id}/symbol-properties",
    response_model=WorkspaceReviewSymbolPropertiesResponse,
    responses={404: {"description": "Review case not found"}, 422: {"description": "Invalid symbol properties"}},
)
def update_workspace_review_symbol_properties(
    review_case_id: str,
    request: WorkspaceReviewSymbolPropertiesUpdateRequest,
    session: Session = Depends(get_db_session),
) -> WorkspaceReviewSymbolPropertiesResponse:
    parsed_case_id = parse_review_case_id(review_case_id)
    review_case = session.get(ReviewCase, parsed_case_id)
    if review_case is None:
        raise HTTPException(status_code=404, detail="Review case not found.")

    split_item = None
    if request.splitItemId:
        try:
            split_item_id = uuid.UUID(request.splitItemId)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid split item id.") from exc
        split_item = session.get(ReviewSplitItem, split_item_id)
        if split_item is None or split_item.review_case_id != review_case.id:
            raise HTTPException(status_code=404, detail="Split review item not found.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    properties = get_or_create_symbol_properties(
        session,
        review_case=review_case,
        split_item=split_item,
        default_name=request.name,
        default_description=request.description,
    )
    previous_payload = {
        "name": properties.name,
        "description": properties.description,
        "category": properties.category,
        "discipline": properties.discipline,
    }
    properties.name = request.name
    properties.description = request.description
    properties.category = request.category or None
    properties.discipline = request.discipline or None
    properties.source = "reviewer"
    properties.updated_by = request.updatedBy or "Human"
    properties.updated_at = now
    session.add(properties)
    session.add(
        AuditEvent(
            entity_type="review_symbol_property",
            entity_id=properties.id,
            action="review_symbol_properties_updated",
            actor_id=None,
            payload_json={
                "review_case_id": str(review_case.id),
                "review_split_item_id": str(split_item.id) if split_item is not None else None,
                "previous": previous_payload,
                "updated": {
                    "name": properties.name,
                    "description": properties.description,
                    "category": properties.category,
                    "discipline": properties.discipline,
                },
            },
            created_at=now,
        )
    )
    session.commit()
    return build_symbol_properties_response(properties)


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
        {child.action.strip() for child in request.childDecisions if child.action.strip() and child.action.strip() not in CHILD_ACTION_CODES}
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
    child_decisions = []
    for child in request.childDecisions:
        if not child.action.strip() or child.action.strip() == "pending":
            continue
        child_payload = child.model_dump()
        child_payload["originalAction"] = child_payload["action"]
        child_payload["action"] = normalize_child_action(child.action)
        child_decisions.append(child_payload)
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
        child_target_agent_slug = child_transition["target_agent_slug"]
        child_target_stage = child_transition["target_stage"]
        child_action_code = f"child_{child_transition['action_code']}"
        if decision_code != "approve":
            child_target_agent_slug = "libby"
            child_target_stage = "review_follow_up"
            child_action_code = f"child_review_feedback_{child['action']}"
        actions.append(
            create_review_action(
                review_case=review_case,
                decision=decision,
                action_code=child_action_code,
                action_status="pending",
                payload=child,
                target_agent_slug=child_target_agent_slug,
                target_stage=child_target_stage,
            )
        )

    for action in actions:
        session.add(action)

    review_case.current_stage = to_stage
    if transition["close"]:
        review_case.closed_at = now
    suppressed_parent_review_ids = []
    if decision_code == "approve" and from_stage == "raster_split_review":
        suppressed_parent_review_ids = close_parent_sheet_reviews_for_split(
            session,
            split_review_case=review_case,
            closed_at=now,
        )

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
                "suppressed_parent_review_ids": suppressed_parent_review_ids,
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
    else:
        execute_review_followup_handoff(
            session,
            review_case_id=review_case.id,
            decision_id=decision.id,
        )
    if decision_code == "approve" or transition["target_agent_slug"] == "libby":
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


@router.post(
    "/review-cases/{review_case_id}/split-items/process-decisions",
    response_model=WorkspaceSplitReviewProcessResponse,
    responses={404: {"description": "Review case not found"}, 422: {"description": "Invalid split decision"}},
)
def process_workspace_split_review_decisions(
    review_case_id: str,
    request: WorkspaceSplitReviewProcessRequest,
    session: Session = Depends(get_db_session),
) -> WorkspaceSplitReviewProcessResponse:
    parsed_case_id = parse_review_case_id(review_case_id)
    row = session.execute(
        select(ReviewCase, ValidationReport)
        .join(
            ValidationReport,
            and_(
                ReviewCase.source_entity_type == "validation_report",
                ReviewCase.source_entity_id == ValidationReport.id,
            ),
        )
        .where(ReviewCase.id == parsed_case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Raster split review case not found.")

    review_case, validation_report = row
    normalized = validation_report.normalized_payload_json or {}
    if review_case.current_stage != "raster_split_review" and not ((normalized.get("derivative_manifest") or {}).get("children")):
        raise HTTPException(status_code=422, detail="Review case does not contain split child items.")

    source_file_name = resolve_source_file_name(validation_report)
    split_items = ensure_split_items(
        session,
        review_case=review_case,
        validation_report=validation_report,
        source_file_name=source_file_name,
    )
    item_lookup = {}
    for item in split_items:
        item_lookup[item.child_key] = item
        item_lookup[item.proposed_symbol_id] = item
        item_lookup[item.file_name] = item

    child_decisions = []
    for child in request.childDecisions:
        raw_action = child.action.strip()
        if not raw_action or raw_action == "pending":
            continue
        normalized_action = normalize_child_action(raw_action)
        if normalized_action not in DECISION_TRANSITIONS:
            raise HTTPException(status_code=422, detail=f"Unsupported child action: {raw_action}.")
        item = item_lookup.get(child.childId) or item_lookup.get(child.proposedSymbolId or "")
        if item is None:
            raise HTTPException(status_code=422, detail=f"Unknown split child item: {child.childId}.")
        if item.status not in {"awaiting_decision", "returned_for_review"}:
            continue
        payload = child.model_dump()
        payload["originalAction"] = payload["action"]
        payload["action"] = normalized_action
        payload["childId"] = item.child_key
        payload["proposedSymbolId"] = payload.get("proposedSymbolId") or item.proposed_symbol_id
        payload["proposedSymbolName"] = payload.get("proposedSymbolName") or item.proposed_symbol_name
        child_decisions.append((item, payload))

    if not child_decisions:
        raise HTTPException(status_code=422, detail="No pending split child decisions were provided.")

    total_open_before = len([item for item in split_items if item.status in {"awaiting_decision", "returned_for_review"}])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    processed_items: list[WorkspaceSplitReviewProcessItemResponse] = []

    for item, child_payload in child_decisions:
        action_code = child_payload["action"]
        transition = DECISION_TRANSITIONS[action_code]
        is_approval = action_code == "approve"
        target_agent_slug = "rupert" if is_approval else "libby"
        target_stage = "publication_staging" if is_approval else transition["target_stage"]
        followup_action_code = "prepare_publication_handoff" if is_approval else "route_review_follow_up_to_libby"
        decision = HumanReviewDecision(
            review_case_id=review_case.id,
            decision_code=action_code,
            decision_summary=f"{request.deciderName} processed {action_code.replace('_', ' ')} for {item.proposed_symbol_id}.",
            decision_note=child_payload.get("note") or child_payload.get("details") or None,
            decided_by=None,
            decider_name=request.deciderName,
            decider_role=request.deciderRole,
            from_stage=review_case.current_stage,
            to_stage="ready_for_publication_handoff" if is_approval else transition["to_stage"],
            decision_payload_json={
                "child_decisions": [child_payload],
                "case_comment": request.caseComment,
                "review_case_id": str(review_case.id),
                "split_child_key": item.child_key,
                "split_child_item_id": str(item.id),
            },
            created_at=now,
        )
        session.add(decision)
        session.flush()
        action = create_review_action(
            review_case=review_case,
            decision=decision,
            action_code=followup_action_code,
            action_status="pending",
            payload={
                "decision_code": action_code,
                "decision_note": decision.decision_note,
                "split_child_key": item.child_key,
                "split_child_item_id": str(item.id),
                "child_decision": child_payload,
            },
            target_agent_slug=target_agent_slug,
            target_stage=target_stage,
        )
        session.add(action)
        session.add(
            AuditEvent(
                entity_type="review_split_item",
                entity_id=item.id,
                action="split_child_review_decision_recorded",
                actor_id=None,
                payload_json={
                    "review_case_id": str(review_case.id),
                    "decision_id": str(decision.id),
                    "decision_code": action_code,
                    "target_agent_slug": target_agent_slug,
                },
                created_at=now,
            )
        )
        item.latest_action = action_code
        item.latest_note = child_payload.get("note") or ""
        item.latest_details = child_payload.get("details") or ""
        item.latest_decision_id = decision.id
        item.latest_action_id = action.id
        item.downstream_agent_slug = target_agent_slug
        item.updated_at = now
        session.flush()
        session.commit()

        if is_approval:
            execute_publication_handoff(
                session,
                review_case_id=review_case.id,
                decision_id=decision.id,
                close_review_case=False,
            )
        else:
            execute_review_followup_handoff(
                session,
                review_case_id=review_case.id,
                decision_id=decision.id,
            )

        refreshed_action = (
            session.query(ReviewCaseAction)
            .filter(ReviewCaseAction.decision_id == decision.id)
            .order_by(ReviewCaseAction.created_at.asc())
            .first()
        )
        item = session.get(ReviewSplitItem, item.id)
        downstream_queue_item_id = None
        if refreshed_action is not None:
            action_payload = refreshed_action.action_payload_json or {}
            downstream_queue_item_id = action_payload.get("rupert_queue_item_id") or action_payload.get("libby_queue_item_id")
        if item is not None:
            item.status = "queued_rupert" if is_approval else "queued_libby"
            item.downstream_queue_item_id = downstream_queue_item_id
            item.processed_at = datetime.now(timezone.utc).replace(microsecond=0)
            item.updated_at = item.processed_at
            session.add(item)
        session.commit()

        processed_items.append(
            WorkspaceSplitReviewProcessItemResponse(
                childId=child_payload["childId"],
                action=action_code,
                status=item.status if item is not None else ("queued_rupert" if is_approval else "queued_libby"),
                targetAgentSlug=target_agent_slug,
                downstreamQueueItemId=downstream_queue_item_id,
                decisionId=str(decision.id),
            )
        )

    remaining_open_count = (
        session.query(ReviewSplitItem)
        .filter(ReviewSplitItem.review_case_id == review_case.id)
        .filter(ReviewSplitItem.status.in_(("awaiting_decision", "returned_for_review")))
        .count()
    )
    if remaining_open_count == 0:
        review_case = session.get(ReviewCase, review_case.id)
        if review_case is not None:
            closed_at = datetime.now(timezone.utc).replace(microsecond=0)
            review_case.current_stage = "split_children_processed"
            review_case.closed_at = closed_at
            session.add(
                AuditEvent(
                    entity_type="review_case",
                    entity_id=review_case.id,
                    action="split_children_processed",
                    actor_id=None,
                    payload_json={"processed_count": len(processed_items)},
                    created_at=closed_at,
                )
            )
            session.commit()

    review_case = session.get(ReviewCase, parsed_case_id)
    return WorkspaceSplitReviewProcessResponse(
        reviewCaseId=str(parsed_case_id),
        processedCount=len(processed_items),
        skippedPendingCount=max(total_open_before - len(processed_items), 0),
        remainingOpenCount=remaining_open_count,
        items=processed_items,
        currentStage=review_case.current_stage if review_case is not None else "unknown",
        closedAt=isoformat_utc(review_case.closed_at) if review_case is not None and review_case.closed_at else None,
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
    children = build_children(session, review_case, validation_report, str(review_case.id), source_file_name)
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
