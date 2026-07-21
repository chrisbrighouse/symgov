from __future__ import annotations

import io
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError:  # pragma: no cover - production image-processing dependency
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment]
from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import (
    AgentDefinition,
    AgentQueueItem,
    AuditEvent,
    ClassificationRecord,
    GovernedSymbol,
    HumanReviewDecision,
    IntakeRecord,
    ProvenanceAssessment,
    ReviewCase,
    ReviewCaseAction,
    ReviewSplitItem,
    ReviewSymbolProperty,
    SymbolRevision,
    User,
    ValidationReport,
)
from .service_users import enforce_noninteractive_service_account, new_service_pin_hash
from .settings import get_settings
from .runtime import coerce_uuid, download_object_bytes, slugify_public_code


RUPERT_RUNNER = Path("/data/.openclaw/workspaces/rupert/run_rupert_publication.py")
RUPERT_RUNTIME_ROOT = Path("/data/.openclaw/workspaces/rupert/runtime")
SYMGOV_DB_ENV_FILE = Path("/data/.openclaw/workspace/symgov/.env.backend.database")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_publication_service_user(session: Session) -> User:
    service_email = "symgov-publication-service@symgov.local"
    row = session.query(User).filter(text("lower(email) = :email")).params(email=service_email).one_or_none()
    if row is None:
        now = utc_now()
        row = User(
            id=coerce_uuid("user:symgov-publication-service"),
            email=service_email,
            display_name="SymGov Publication Service",
            pin_hash=new_service_pin_hash(),
            pin_set_at=now,
            must_change_pin=False,
            is_active=False,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
    return enforce_noninteractive_service_account(session, row, now=utc_now())


def text_value(*values: Any, fallback: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return fallback


def list_value(value: Any) -> list:
    return value if isinstance(value, list) else []


def source_package_display_id(intake_record: IntakeRecord | None) -> str | None:
    if intake_record is None:
        return None
    normalized = intake_record.normalized_submission_json or {}
    package_id = str(normalized.get("source_package_code") or normalized.get("workspace_display_name") or "").strip().upper()
    return package_id or None


def split_item_display_metadata(split_item: ReviewSplitItem | None, fallback_index: int | None = None) -> dict[str, Any]:
    if split_item is None:
        return {}
    payload = split_item.payload_json or {}
    package_id = payload.get("package_display_id") if isinstance(payload, dict) else None
    sequence = payload.get("package_symbol_sequence") if isinstance(payload, dict) else None
    try:
        sequence = int(sequence) if sequence is not None else fallback_index
    except (TypeError, ValueError):
        sequence = fallback_index
    display_name = f"{package_id}-{sequence}" if package_id and sequence is not None else None
    return {
        "display_name": display_name,
        "package_display_id": package_id,
        "package_symbol_sequence": sequence,
    }


def load_review_symbol_properties(
    session: Session,
    *,
    review_case: ReviewCase,
    split_item: ReviewSplitItem | None = None,
) -> ReviewSymbolProperty | None:
    symbol_record_key = split_item.child_key if split_item is not None else str(review_case.id)
    return (
        session.query(ReviewSymbolProperty)
        .filter(ReviewSymbolProperty.review_case_id == review_case.id)
        .filter(ReviewSymbolProperty.symbol_record_key == symbol_record_key)
        .one_or_none()
    )


def candidate_id_from_intake(intake_record: IntakeRecord | None) -> str:
    if intake_record is None:
        return ""
    normalized = intake_record.normalized_submission_json or {}
    return text_value(normalized.get("candidate_symbol_id"), normalized.get("candidate_title"))


def source_file_from_intake(intake_record: IntakeRecord | None) -> str:
    if intake_record is None:
        return ""
    normalized = intake_record.normalized_submission_json or {}
    for key in ("original_filename", "origin_file_name", "raw_input_path", "raw_object_key"):
        value = normalized.get(key) or (intake_record.raw_object_key if key == "raw_object_key" else None)
        if value:
            return Path(str(value)).name
    return ""


def load_review_context(session: Session, review_case: ReviewCase) -> dict[str, Any]:
    validation_report = None
    provenance_assessment = None
    intake_record = None

    if review_case.source_entity_type == "validation_report":
        validation_report = session.get(ValidationReport, review_case.source_entity_id)
        if validation_report is not None and validation_report.source_type == "intake_record":
            intake_record = session.get(IntakeRecord, validation_report.source_id)
    elif review_case.source_entity_type == "provenance_assessment":
        provenance_assessment = session.get(ProvenanceAssessment, review_case.source_entity_id)
        if provenance_assessment is not None:
            intake_record = session.get(IntakeRecord, provenance_assessment.intake_record_id)

    classification_query = session.query(ClassificationRecord).filter(
        ClassificationRecord.status == "current",
        ClassificationRecord.review_case_id == review_case.id,
    )
    if provenance_assessment is not None:
        classification_query = classification_query.union(
            session.query(ClassificationRecord).filter(
                ClassificationRecord.status == "current",
                ClassificationRecord.provenance_assessment_id == provenance_assessment.id,
            )
        )
    classification_record = classification_query.order_by(ClassificationRecord.created_at.desc()).first()

    return {
        "validation_report": validation_report,
        "provenance_assessment": provenance_assessment,
        "intake_record": intake_record,
        "classification_record": classification_record,
    }


def derive_symbol_slug(context: dict[str, Any], review_case: ReviewCase) -> str:
    classification = context["classification_record"]
    intake = context["intake_record"]
    source_file = source_file_from_intake(intake)
    raw_key = (
        text_value(classification.symbol_key if classification else None)
        or candidate_id_from_intake(intake)
        or Path(source_file).stem
        or str(review_case.id)
    )
    return slugify_public_code(raw_key)


def derive_symbol_name(context: dict[str, Any], slug: str) -> str:
    classification = context["classification_record"]
    intake = context["intake_record"]
    aliases = list_value(classification.aliases_json if classification else None)
    normalized = intake.normalized_submission_json if intake is not None else {}
    title = text_value(
        normalized.get("candidate_title") if normalized else None,
        classification.symbol_family.replace("_", " ").replace("-", " ").title() if classification and classification.symbol_family else None,
        aliases[0] if aliases else None,
        fallback=slug.replace("-", " ").title(),
    )
    return title


def ensure_approved_symbol_revision(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
) -> SymbolRevision:
    context = load_review_context(session, review_case)
    classification = context["classification_record"]
    intake = context["intake_record"]
    provenance = context["provenance_assessment"]
    validation = context["validation_report"]
    service_user = ensure_publication_service_user(session)
    now = utc_now()

    slug = derive_symbol_slug(context, review_case)
    symbol_properties = load_review_symbol_properties(session, review_case=review_case)
    canonical_name = text_value(symbol_properties.name if symbol_properties else None, derive_symbol_name(context, slug))
    category = text_value(
        symbol_properties.category if symbol_properties else None,
        classification.category if classification else None,
        fallback="symbol",
    )
    discipline = text_value(
        symbol_properties.discipline if symbol_properties else None,
        classification.discipline if classification else None,
        fallback="general",
    )

    symbol = session.query(GovernedSymbol).filter_by(slug=slug).one_or_none()
    if symbol is None:
        symbol = GovernedSymbol(
            id=coerce_uuid(f"governed-symbol:{slug}"),
            slug=slug,
            canonical_name=canonical_name,
            category=category,
            discipline=discipline,
            owner_id=service_user.id,
            current_revision_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(symbol)
        session.flush()
    else:
        symbol.canonical_name = canonical_name
        symbol.category = category
        symbol.discipline = discipline
        symbol.updated_at = now

    revision_label = f"review-{decision.created_at.date().isoformat()}-{str(decision.id)[:8]}"
    revision = session.query(SymbolRevision).filter_by(symbol_id=symbol.id, revision_label=revision_label).one_or_none()
    if revision is None:
        revision = SymbolRevision(
            id=coerce_uuid(f"symbol-revision:{decision.id}"),
            symbol_id=symbol.id,
            revision_label=revision_label,
            lifecycle_state="approved",
            payload_json={},
            rationale=None,
            author_id=service_user.id,
            created_at=now,
        )
        session.add(revision)

    source_file = source_file_from_intake(intake)
    source_object_key = text_value(
        classification.origin_object_key if classification else None,
        intake.raw_object_key if intake else None,
    )
    aliases = list_value(classification.aliases_json if classification else None)
    keywords = list_value(classification.search_terms_json if classification else None)
    source_refs = list_value(classification.source_refs_json if classification else None)
    display_name = source_package_display_id(intake)

    revision.lifecycle_state = "approved"
    revision.payload_json = {
        "name": canonical_name,
        "summary": text_value(
            symbol_properties.description if symbol_properties else None,
            classification.review_summary if classification else None,
            provenance.summary if provenance else None,
            fallback=canonical_name,
        ),
        "description": text_value(
            symbol_properties.description if symbol_properties else None,
            decision.decision_note,
            classification.review_summary if classification else None,
            fallback=canonical_name,
        ),
        "aliases": aliases,
        "keywords": keywords,
        "source_refs": source_refs,
        "source_file": source_file,
        "source_object_key": source_object_key,
        "review_case_id": str(review_case.id),
        "review_decision_id": str(decision.id),
        "classification_record_id": str(classification.id) if classification else None,
        "review_symbol_properties_id": str(symbol_properties.id) if symbol_properties else None,
        "display_name": display_name,
        "package_display_id": display_name,
        "package_symbol_sequence": None,
        "classification": {
            "status": classification.classification_status if classification else None,
            "confidence": float(classification.confidence) if classification else None,
            "libby_approved": classification.libby_approved if classification else None,
            "discipline": classification.discipline if classification else discipline,
            "category": classification.category if classification else category,
            "symbol_family": classification.symbol_family if classification else None,
            "process_category": classification.process_category if classification else None,
            "parent_equipment_class": classification.parent_equipment_class if classification else None,
            "source_classification": classification.source_classification if classification else None,
        },
        "lineage": {
            "intake_record_id": str(intake.id) if intake else None,
            "validation_report_id": str(validation.id) if validation else None,
            "provenance_assessment_id": str(provenance.id) if provenance else None,
        },
    }
    revision.rationale = text_value(decision.decision_note, fallback="Approved during Daisy-coordinated human review.")
    session.flush()
    symbol.current_revision_id = revision.id
    symbol.updated_at = now
    session.flush()
    return revision


def is_raster_split_review(context: dict[str, Any], review_case: ReviewCase) -> bool:
    validation = context["validation_report"]
    if review_case.current_stage == "raster_split_review":
        return True
    normalized = validation.normalized_payload_json if validation is not None else {}
    return bool((normalized or {}).get("derivative_manifest", {}).get("children"))


def approved_child_decisions(decision: HumanReviewDecision) -> list[dict[str, Any]]:
    payload = decision.decision_payload_json or {}
    children = payload.get("child_decisions") if isinstance(payload, dict) else None
    if not isinstance(children, list):
        return []
    return [
        child
        for child in children
        if isinstance(child, dict) and child.get("action") in {"approve", "approved"}
    ]


def child_lookup_by_id(validation_report: ValidationReport | None) -> dict[str, dict[str, Any]]:
    normalized = validation_report.normalized_payload_json if validation_report is not None else {}
    children = ((normalized or {}).get("derivative_manifest") or {}).get("children") or []
    lookup = {}
    for child in children:
        if not isinstance(child, dict):
            continue
        keys = [
            child.get("attachment_object_key"),
            child.get("proposed_symbol_id"),
            child.get("file_name"),
        ]
        for key in keys:
            if key:
                lookup[str(key)] = child
    return lookup


def reviewed_split_item_for_child(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    child_decision: dict[str, Any],
    child_manifest: dict[str, Any] | None,
) -> ReviewSplitItem | None:
    payload = decision.decision_payload_json or {}
    split_child_item_id = payload.get("split_child_item_id") if isinstance(payload, dict) else None
    if split_child_item_id:
        try:
            row = session.get(ReviewSplitItem, uuid.UUID(str(split_child_item_id)))
            if row is not None and row.review_case_id == review_case.id:
                return row
        except ValueError:
            pass

    child_keys = [
        child_decision.get("childId"),
        child_decision.get("proposedSymbolId"),
        child_decision.get("fileName"),
    ]
    if child_manifest:
        child_keys.extend(
            [
                child_manifest.get("child_key"),
                child_manifest.get("proposed_symbol_id"),
                child_manifest.get("file_name"),
                child_manifest.get("attachment_object_key"),
            ]
        )
    lookup_keys = {str(key) for key in child_keys if key}
    if lookup_keys:
        row = (
            session.query(ReviewSplitItem)
            .filter(ReviewSplitItem.review_case_id == review_case.id)
            .filter(
                (ReviewSplitItem.child_key.in_(lookup_keys))
                | (ReviewSplitItem.proposed_symbol_id.in_(lookup_keys))
                | (ReviewSplitItem.file_name.in_(lookup_keys))
                | (ReviewSplitItem.attachment_object_key.in_(lookup_keys))
            )
            .one_or_none()
        )
        if row is not None:
            return row
    if split_child_item_id:
        raise RuntimeError(
            f"Could not resolve reviewed split item {split_child_item_id} "
            f"for publication decision {decision.id}."
        )
    return None


def ensure_approved_child_symbol_revision(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    child_decision: dict[str, Any],
    child_manifest: dict[str, Any] | None,
    index: int,
) -> SymbolRevision:
    context = load_review_context(session, review_case)
    validation = context["validation_report"]
    intake = context["intake_record"]
    service_user = ensure_publication_service_user(session)
    now = utc_now()

    proposed_id = text_value(
        child_decision.get("proposedSymbolId"),
        child_manifest.get("proposed_symbol_id") if child_manifest else None,
        child_decision.get("childId"),
        fallback=f"{review_case.id}-child-{index + 1}",
    )
    slug = slugify_public_code(proposed_id)
    canonical_name = text_value(
        child_decision.get("proposedSymbolName"),
        child_manifest.get("proposed_symbol_name") if child_manifest else None,
        fallback=slug.replace("-", " ").title(),
    )
    reviewed_split_item = reviewed_split_item_for_child(
        session,
        review_case=review_case,
        decision=decision,
        child_decision=child_decision,
        child_manifest=child_manifest,
    )
    symbol_properties = load_review_symbol_properties(
        session,
        review_case=review_case,
        split_item=reviewed_split_item,
    )
    canonical_name = text_value(symbol_properties.name if symbol_properties else None, canonical_name)
    category = text_value(symbol_properties.category if symbol_properties else None, fallback="symbol")
    discipline = text_value(symbol_properties.discipline if symbol_properties else None, fallback="general")

    symbol = session.query(GovernedSymbol).filter_by(slug=slug).one_or_none()
    if symbol is None:
        symbol = GovernedSymbol(
            id=coerce_uuid(f"governed-symbol:{slug}"),
            slug=slug,
            canonical_name=canonical_name,
            category=category,
            discipline=discipline,
            owner_id=service_user.id,
            current_revision_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(symbol)
        session.flush()
    else:
        symbol.canonical_name = canonical_name
        symbol.category = category
        symbol.discipline = discipline
        symbol.updated_at = now

    revision_label = f"review-{decision.created_at.date().isoformat()}-{str(decision.id)[:8]}-{index + 1:02d}"
    revision = session.query(SymbolRevision).filter_by(symbol_id=symbol.id, revision_label=revision_label).one_or_none()
    if revision is None:
        revision = SymbolRevision(
            id=coerce_uuid(f"symbol-revision:{decision.id}:{proposed_id}"),
            symbol_id=symbol.id,
            revision_label=revision_label,
            lifecycle_state="approved",
            payload_json={},
            rationale=None,
            author_id=service_user.id,
            created_at=now,
        )
        session.add(revision)

    source_file = source_file_from_intake(intake)
    display_metadata = split_item_display_metadata(reviewed_split_item, fallback_index=index + 1)
    child_object_key = text_value(
        reviewed_split_item.attachment_object_key if reviewed_split_item else None,
        child_manifest.get("attachment_object_key") if child_manifest else None,
        child_decision.get("childId"),
    )
    revision.lifecycle_state = "approved"
    revision.payload_json = {
        "name": canonical_name,
        "summary": text_value(symbol_properties.description if symbol_properties else None, child_decision.get("note"), fallback=canonical_name),
        "description": text_value(
            symbol_properties.description if symbol_properties else None,
            decision.decision_note,
            child_decision.get("note"),
            fallback=canonical_name,
        ),
        "aliases": [canonical_name] if canonical_name != proposed_id else [],
        "keywords": [proposed_id, "raster split child"],
        "source_refs": [],
        "source_file": source_file,
        "source_object_key": child_object_key,
        "review_case_id": str(review_case.id),
        "review_decision_id": str(decision.id),
        "classification_record_id": None,
        "review_symbol_properties_id": str(symbol_properties.id) if symbol_properties else None,
        "display_name": display_metadata.get("display_name"),
        "package_display_id": display_metadata.get("package_display_id"),
        "package_symbol_sequence": display_metadata.get("package_symbol_sequence"),
        "classification": {
            "status": "human_approved_split_child",
            "confidence": None,
            "libby_approved": None,
            "discipline": discipline,
            "category": category,
            "symbol_family": None,
            "process_category": None,
            "parent_equipment_class": None,
            "source_classification": "raster_split_review",
        },
        "lineage": {
            "intake_record_id": str(intake.id) if intake else None,
            "validation_report_id": str(validation.id) if validation else None,
            "provenance_assessment_id": None,
            "parent_sheet_review_case_id": str(review_case.id),
            "child_id": child_decision.get("childId"),
            "child_file_name": child_manifest.get("file_name") if child_manifest else None,
            "child_bbox": child_manifest.get("bbox") if child_manifest else None,
            "review_split_item_id": str(reviewed_split_item.id) if reviewed_split_item else None,
            "reviewed_attachment_object_key": reviewed_split_item.attachment_object_key if reviewed_split_item else None,
        },
    }
    revision.rationale = text_value(
        decision.decision_note,
        child_decision.get("note"),
        fallback="Approved as an extracted child symbol during raster split review.",
    )
    session.flush()
    symbol.current_revision_id = revision.id
    symbol.updated_at = now
    session.flush()
    return revision


def approved_revisions_for_decision(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    context: dict[str, Any],
) -> list[SymbolRevision]:
    if not is_raster_split_review(context, review_case):
        return [ensure_approved_symbol_revision(session, review_case=review_case, decision=decision)]

    child_decisions = approved_child_decisions(decision)
    if not child_decisions:
        raise RuntimeError("Raster split publication requires at least one approved child symbol.")

    child_lookup = child_lookup_by_id(context["validation_report"])
    revisions = []
    for index, child_decision in enumerate(child_decisions):
        child_manifest = child_lookup.get(str(child_decision.get("childId"))) or child_lookup.get(
            str(child_decision.get("proposedSymbolId"))
        )
        revisions.append(
            ensure_approved_child_symbol_revision(
                session,
                review_case=review_case,
                decision=decision,
                child_decision=child_decision,
                child_manifest=child_manifest,
                index=index,
            )
        )
    return revisions


def revision_display_ids(revisions: list[SymbolRevision]) -> list[str]:
    values = []
    for revision in revisions:
        payload = revision.payload_json or {}
        if not isinstance(payload, dict):
            continue
        display_name = text_value(payload.get("display_name"), payload.get("workspace_display_name"))
        if display_name:
            values.append(display_name)
    return values


def mark_split_items_published_for_revisions(
    session: Session,
    *,
    revisions: list[SymbolRevision],
    downstream_queue_item_id: str,
    completed_at: datetime,
) -> list[str]:
    """Close split-review rows that Rupert has successfully published.

    Split children are moved to `queued_rupert` as soon as a reviewer approves them.  Rupert's
    publication handoff creates the governed symbol and publication records, so the split child
    must be moved to `published` in the same transaction; otherwise the Workspace keeps showing
    stale publication work even though Rupert completed successfully.
    """
    published_split_item_ids: list[str] = []
    for revision in revisions:
        payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
        lineage = payload.get("lineage") if isinstance(payload, dict) else None
        split_item_id = lineage.get("review_split_item_id") if isinstance(lineage, dict) else None
        if not split_item_id:
            continue
        try:
            split_uuid = uuid.UUID(str(split_item_id))
        except ValueError:
            continue
        split_item = session.get(ReviewSplitItem, split_uuid)
        if split_item is None:
            continue
        split_item.status = "published"
        split_item.downstream_agent_slug = "rupert"
        split_item.downstream_queue_item_id = downstream_queue_item_id
        split_item.processed_at = completed_at
        split_item.updated_at = completed_at
        published_split_item_ids.append(str(split_item.id))
    return published_split_item_ids


def mark_split_item_duplicate_pending_for_decision(
    session: Session,
    *,
    decision: HumanReviewDecision,
    downstream_queue_item_id: str,
    updated_at: datetime,
) -> str | None:
    """Move a split child out of `queued_rupert` when publication is blocked by duplicate gate."""
    payload = decision.decision_payload_json if isinstance(decision.decision_payload_json, dict) else {}
    split_item_id = payload.get("split_child_item_id")
    if not split_item_id:
        return None
    try:
        split_uuid = uuid.UUID(str(split_item_id))
    except ValueError:
        return None
    split_item = session.get(ReviewSplitItem, split_uuid)
    if split_item is None:
        return None
    split_item.status = "duplicate_pending"
    split_item.downstream_agent_slug = "libby"
    split_item.downstream_queue_item_id = downstream_queue_item_id
    split_item.updated_at = updated_at
    return str(split_item.id)


def publication_duplicate_override_for_decision(decision: HumanReviewDecision) -> dict[str, Any] | None:
    """Return a human false-duplicate override recorded by Daisy/SME review, if present."""
    payload = decision.decision_payload_json if isinstance(decision.decision_payload_json, dict) else {}
    override = payload.get("duplicate_gate_override") if isinstance(payload, dict) else None
    if not isinstance(override, dict):
        return None
    outcome = str(override.get("outcome") or "").strip()
    if outcome != "false_duplicate":
        return None
    return {
        "outcome": outcome,
        "reason": str(override.get("reason") or "Human reviewer confirmed this is not a duplicate.").strip(),
        "review_split_item_id": override.get("review_split_item_id"),
        "reviewed_by": override.get("reviewed_by"),
        "reviewed_at": override.get("reviewed_at"),
        "source": "daisy_duplicate_exception_review",
    }


def build_pack_metadata(context: dict[str, Any], review_case: ReviewCase) -> tuple[str, str]:
    source_file = source_file_from_intake(context["intake_record"])
    source_stem = slugify_public_code(Path(source_file).stem) if source_file else str(review_case.id)[:8]
    pack_code = f"source-pack-{source_stem}"
    pack_title = f"Source file pack: {source_file}" if source_file else f"Review pack {str(review_case.id)[:8]}"
    return pack_code, pack_title


def write_rupert_queue_item(queue_item: dict[str, Any]) -> Path:
    queue_dir = RUPERT_RUNTIME_ROOT / "agent_queue_items"
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_path = queue_dir / f"{queue_item['id']}.json"
    with queue_path.open("w", encoding="utf-8") as handle:
        json.dump(queue_item, handle, indent=2)
        handle.write("\n")
    return queue_path


def run_rupert(queue_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(RUPERT_RUNNER),
        "--queue-item",
        str(queue_path),
        "--runtime-root",
        str(RUPERT_RUNTIME_ROOT),
        "--persist-db",
    ]
    if SYMGOV_DB_ENV_FILE.exists():
        command.extend(["--db-env-file", str(SYMGOV_DB_ENV_FILE)])
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Rupert publication failed.").strip())
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Rupert returned invalid JSON: {completed.stdout}") from exc


def _canonical_grayscale_pixels(payload: bytes, *, size: tuple[int, int]) -> list[int]:
    if Image is None:
        raise RuntimeError("Pillow is required for graphical duplicate detection.")
    with Image.open(io.BytesIO(payload)) as image:
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGBA", image.size, "WHITE")
            background.alpha_composite(image.convert("RGBA"))
            image = background.convert("RGB")
        gray = image.convert("L").resize(size)
        return list(gray.getdata())


def _image_dhash_hex(payload: bytes) -> str:
    pixels = _canonical_grayscale_pixels(payload, size=(9, 8))
    bits: list[int] = []
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits.append(1 if pixels[base + col] > pixels[base + col + 1] else 0)
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return f"{value:016x}"


def _pixel_difference_score(left_payload: bytes, right_payload: bytes) -> float:
    left_pixels = _canonical_grayscale_pixels(left_payload, size=(128, 128))
    right_pixels = _canonical_grayscale_pixels(right_payload, size=(128, 128))
    total = sum(abs(left - right) for left, right in zip(left_pixels, right_pixels))
    return total / (255.0 * len(left_pixels))


def _hamming_distance_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _revision_source_object_key(revision: SymbolRevision) -> str | None:
    payload = revision.payload_json or {}
    if not isinstance(payload, dict):
        return None
    value = payload.get("source_object_key")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def detect_graphical_duplicates(
    session: Session,
    *,
    revisions: list[SymbolRevision],
    distance_threshold: int = 4,
    pixel_difference_threshold: float = 0.08,
) -> list[dict[str, Any]]:
    settings = get_settings()
    storage_env = str(settings.storage_env_file)
    published_rows = (
        session.query(SymbolRevision, GovernedSymbol)
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
        .filter(SymbolRevision.lifecycle_state == "published")
        .all()
    )
    published_hashes: list[dict[str, Any]] = []
    for row_revision, row_symbol in published_rows:
        object_key = _revision_source_object_key(row_revision)
        if not object_key:
            continue
        try:
            payload = download_object_bytes(object_key=object_key, env_file=storage_env)
            payload_bytes = payload["payload"]
            hash_hex = _image_dhash_hex(payload_bytes)
        except Exception:
            continue
        published_hashes.append(
            {
                "revision_id": str(row_revision.id),
                "symbol_slug": row_symbol.slug,
                "object_key": object_key,
                "dhash": hash_hex,
                "payload": payload_bytes,
            }
        )

    matches: list[dict[str, Any]] = []
    for revision in revisions:
        candidate_key = _revision_source_object_key(revision)
        if not candidate_key:
            continue
        try:
            candidate_payload = download_object_bytes(object_key=candidate_key, env_file=storage_env)
            candidate_payload_bytes = candidate_payload["payload"]
            candidate_hash = _image_dhash_hex(candidate_payload_bytes)
        except (FileNotFoundError, RuntimeError, UnidentifiedImageError, OSError):
            continue
        for published in published_hashes:
            if published["revision_id"] == str(revision.id):
                continue
            distance = _hamming_distance_hex(candidate_hash, published["dhash"])
            if distance > distance_threshold:
                continue
            pixel_difference = _pixel_difference_score(candidate_payload_bytes, published["payload"])
            if pixel_difference <= pixel_difference_threshold:
                matches.append(
                    {
                        "candidate_revision_id": str(revision.id),
                        "candidate_object_key": candidate_key,
                        "candidate_dhash": candidate_hash,
                        "matched_revision_id": published["revision_id"],
                        "matched_symbol_slug": published["symbol_slug"],
                        "matched_object_key": published["object_key"],
                        "matched_dhash": published["dhash"],
                        "hamming_distance": distance,
                        "distance_threshold": distance_threshold,
                        "pixel_difference": round(pixel_difference, 6),
                        "pixel_difference_threshold": pixel_difference_threshold,
                    }
                )
    return matches


def queue_libby_duplicate_followup(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    action: ReviewCaseAction,
    duplicates: list[dict[str, Any]],
) -> dict[str, Any]:
    now = utc_now()
    libby_definition = session.query(AgentDefinition).filter_by(slug="libby").one_or_none()
    if libby_definition is None:
        raise RuntimeError("Libby agent definition not found while queueing duplicate follow-up.")

    first = duplicates[0]
    reviewer_message = (
        "Graphical duplicate suspected before publication: "
        f"candidate revision {first['candidate_revision_id']} visually matches published symbol "
        f"{first['matched_symbol_slug']} (dHash distance {first['hamming_distance']} <= {first['distance_threshold']}; "
        f"pixel difference {first['pixel_difference']} <= {first['pixel_difference_threshold']})."
    )
    payload = {
        "task_type": "publication_duplicate_detected",
        "review_case_id": str(review_case.id),
        "review_decision_id": str(decision.id),
        "current_stage": "duplicate_resolution",
        "decision_code": "duplicate",
        "decision_summary": "Automatic publication duplicate gate detected a graphical duplicate.",
        "decision_note": reviewer_message,
        "case_comment": reviewer_message,
        "origin": "rupert_publication_gate",
        "libby_follow_up_type": "duplicate_resolution",
        "duplicate_evidence": duplicates,
    }
    queue_id = f"aqi-libby-duplicate-{str(decision.id)[:8]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    queue_item = {
        "id": queue_id,
        "agent_id": "libby",
        "source_type": "review_decision",
        "source_id": str(decision.id),
        "status": "queued",
        "priority": "high",
        "payload_json": payload,
        "confidence": None,
        "escalation_reason": "graphical_duplicate_detected",
        "created_at": isoformat_utc(now),
        "started_at": None,
        "completed_at": None,
    }

    db_queue_item = session.get(AgentQueueItem, coerce_uuid(queue_id))
    if db_queue_item is None:
        db_queue_item = AgentQueueItem(id=coerce_uuid(queue_id))
        session.add(db_queue_item)
    db_queue_item.agent_id = libby_definition.id
    db_queue_item.source_type = "review_decision"
    db_queue_item.source_id = coerce_uuid(str(decision.id))
    db_queue_item.status = "queued"
    db_queue_item.priority = "high"
    db_queue_item.payload_json = payload
    db_queue_item.confidence = None
    db_queue_item.escalation_reason = "graphical_duplicate_detected"
    db_queue_item.created_at = now
    db_queue_item.started_at = None
    db_queue_item.completed_at = None

    review_case.current_stage = "duplicate_resolution"
    review_case.closed_at = None
    duplicate_split_item_id = mark_split_item_duplicate_pending_for_decision(
        session,
        decision=decision,
        downstream_queue_item_id=queue_id,
        updated_at=now,
    )

    action.action_status = "completed"
    action.completed_at = now
    action.action_payload_json = {
        **(action.action_payload_json or {}),
        "libby_queue_item_id": queue_id,
        "publication_blocked_reason": "graphical_duplicate_detected",
        "duplicate_split_item_id": duplicate_split_item_id,
        "duplicate_gate": {
            "status": "detected",
            "reviewer_message": reviewer_message,
            "duplicate_evidence": duplicates,
            "libby_queue_item_id": queue_id,
        },
    }

    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="review_case",
            entity_id=review_case.id,
            action="publication_duplicate_detected",
            actor_id=None,
            payload_json={
                "decision_id": str(decision.id),
                "review_case_action_id": str(action.id),
                "libby_queue_item_id": queue_id,
                "duplicate_split_item_id": duplicate_split_item_id,
                "duplicate_evidence": duplicates,
                "reviewer_message": reviewer_message,
            },
            created_at=now,
        )
    )
    session.commit()

    queue_file = Path("/data/.openclaw/workspaces/libby/runtime") / "agent_queue_items" / f"{queue_id}.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("w", encoding="utf-8") as handle:
        json.dump(queue_item, handle, indent=2)
        handle.write("\n")
    return {"status": "duplicate_detected", "libby_queue_item_id": queue_id, "reviewer_message": reviewer_message}


def execute_publication_handoff(
    session: Session,
    *,
    review_case_id: uuid.UUID,
    decision_id: uuid.UUID,
    close_review_case: bool = True,
) -> dict[str, Any]:
    action = (
        session.query(ReviewCaseAction)
        .filter(
            ReviewCaseAction.review_case_id == review_case_id,
            ReviewCaseAction.decision_id == decision_id,
            ReviewCaseAction.action_code == "prepare_publication_handoff",
            ReviewCaseAction.target_agent_slug == "rupert",
        )
        .order_by(ReviewCaseAction.created_at.desc())
        .first()
    )
    if action is None:
        return {"status": "skipped", "detail": "No Rupert publication handoff action was found."}

    review_case = session.get(ReviewCase, review_case_id)
    decision = session.get(HumanReviewDecision, decision_id)
    rupert_definition = session.query(AgentDefinition).filter_by(slug="rupert").one_or_none()
    if review_case is None or decision is None or rupert_definition is None:
        action.action_status = "failed"
        action.action_payload_json = {
            **(action.action_payload_json or {}),
            "error": "Missing review case, decision, or Rupert agent definition.",
        }
        action.completed_at = utc_now()
        session.commit()
        return {"status": "failed", "detail": action.action_payload_json["error"]}

    now = utc_now()
    action.action_status = "running"
    action.started_at = now
    session.commit()

    try:
        context = load_review_context(session, review_case)
        revisions = approved_revisions_for_decision(
            session,
            review_case=review_case,
            decision=decision,
            context=context,
        )
        duplicate_override = publication_duplicate_override_for_decision(decision)
        duplicate_matches = [] if duplicate_override else detect_graphical_duplicates(session, revisions=revisions)
        if duplicate_matches:
            return queue_libby_duplicate_followup(
                session,
                review_case=review_case,
                decision=decision,
                action=action,
                duplicates=duplicate_matches,
            )
        revision_ids = [str(revision.id) for revision in revisions]
        display_ids = revision_display_ids(revisions)
        pack_code, pack_title = build_pack_metadata(context, review_case)
        queue_id = f"aqi-rupert-review-{str(decision.id)[:8]}-{now.strftime('%Y%m%dT%H%M%SZ')}"
        queue_item = {
            "id": queue_id,
            "agent_id": "rupert",
            "source_type": "review_decision",
            "source_id": str(decision.id),
            "status": "queued",
            "priority": "medium",
            "payload_json": {
                "review_case_id": str(review_case.id),
                "review_decision_id": str(decision.id),
                "human_decision": "approve",
                "human_approved": True,
                "symbol_revision_ids": revision_ids,
                "symbol_display_ids": display_ids,
                "display_name": display_ids[0] if len(display_ids) == 1 else None,
                "release_target": "standards-current",
                "publication_pack_code": pack_code,
                "publication_pack_title": pack_title,
                "effective_date": now.date().isoformat(),
                "standards_visibility": "public",
                "release_area": None,
                "source_review_case_summary": decision.decision_summary,
                "duplicate_gate_override": duplicate_override,
            },
            "confidence": None,
            "escalation_reason": None,
            "created_at": isoformat_utc(now),
            "started_at": None,
            "completed_at": None,
        }
        action.action_payload_json = {
            **(action.action_payload_json or {}),
            "symbol_revision_ids": revision_ids,
            "rupert_queue_item_id": queue_id,
            "publication_pack_code": pack_code,
            "duplicate_gate_override": duplicate_override,
        }
        session.commit()

        queue_path = write_rupert_queue_item(queue_item)
        result = run_rupert(queue_path)
        completed_at = utc_now()
        published_split_item_ids = mark_split_items_published_for_revisions(
            session,
            revisions=revisions,
            downstream_queue_item_id=queue_id,
            completed_at=completed_at,
        )
        action.action_status = "completed"
        action.completed_at = completed_at
        action.action_payload_json = {
            **(action.action_payload_json or {}),
            "rupert_result": result,
            "published_symbol_revision_ids": revision_ids,
            "published_split_item_ids": published_split_item_ids,
        }
        if close_review_case:
            review_case.current_stage = "published"
            review_case.closed_at = completed_at
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                entity_type="review_case",
                entity_id=review_case.id,
                action="publication_handoff_completed",
                actor_id=None,
                payload_json={
                    "decision_id": str(decision.id),
                    "review_case_action_id": str(action.id),
                    "symbol_revision_ids": revision_ids,
                    "rupert_queue_item_id": queue_id,
                    "publication_pack_code": pack_code,
                },
                created_at=completed_at,
            )
        )
        session.commit()
        return {"status": "completed", "rupert_queue_item_id": queue_id, "symbol_revision_ids": revision_ids}
    except Exception as exc:
        failed_at = utc_now()
        session.rollback()
        action = session.get(ReviewCaseAction, action.id)
        if action is not None:
            action.action_status = "failed"
            action.completed_at = failed_at
            action.action_payload_json = {
                **(action.action_payload_json or {}),
                "error": str(exc),
            }
        session.commit()
        return {"status": "failed", "detail": str(exc)}
