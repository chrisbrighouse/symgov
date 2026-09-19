from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from .models import (
    Attachment,
    IntakeRecord,
    PublishedPreviewAuthorization,
    ReviewSplitItem,
    SymbolRevision,
    ValidationReport,
)
from .published_catalog import choose_published_preview_asset


LEGACY_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "symgov/runtime-legacy-id")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _normalize_sha256(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        return None
    return candidate


def _coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return uuid.uuid5(LEGACY_ID_NAMESPACE, str(value))


def _lineage_payload(revision: SymbolRevision) -> dict:
    payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
    lineage = payload.get("lineage")
    return lineage if isinstance(lineage, dict) else {}


def _matches_persisted_validation_report_split_lineage(
    session: Session,
    *,
    lineage: dict,
    attachment: Attachment,
) -> bool:
    split_item_id = _coerce_uuid(lineage.get("review_split_item_id"))
    review_case_id = _coerce_uuid(lineage.get("parent_sheet_review_case_id"))
    reviewed_object_key = str(lineage.get("reviewed_attachment_object_key") or "").strip()
    attachment_object_key = str(attachment.object_key or "").strip()
    if split_item_id is None or review_case_id is None or not reviewed_object_key:
        return False
    if not attachment_object_key or attachment_object_key != reviewed_object_key:
        return False

    split_item = session.get(ReviewSplitItem, split_item_id)
    if split_item is None:
        return False

    split_item_object_key = str(split_item.attachment_object_key or "").strip()
    return (
        split_item.review_case_id == review_case_id
        and bool(split_item_object_key)
        and split_item_object_key == reviewed_object_key
    )


def _is_trusted_preview_lineage(session: Session, *, revision: SymbolRevision, attachment: Attachment) -> bool:
    if attachment.parent_type == "symbol_revision" and attachment.parent_id == revision.id:
        return True

    lineage = _lineage_payload(revision)

    if attachment.parent_type == "validation_report":
        attachment_report = session.get(ValidationReport, attachment.parent_id)
        if attachment_report is None:
            return False

        validation_id = _coerce_uuid(lineage.get("validation_report_id"))
        if validation_id is not None and validation_id == attachment.parent_id:
            return True

        return _matches_persisted_validation_report_split_lineage(
            session,
            lineage=lineage,
            attachment=attachment,
        )

    if attachment.parent_type == "external_submission_batch":
        intake_id = _coerce_uuid(lineage.get("intake_record_id"))
        if intake_id is None:
            return False
        intake = session.get(IntakeRecord, intake_id)
        if intake is None:
            return False

        normalized = intake.normalized_submission_json if isinstance(intake.normalized_submission_json, dict) else {}
        batch_id = _coerce_uuid(normalized.get("submission_batch_id"))
        if batch_id is None or batch_id != attachment.parent_id:
            return False

        attachment_ids = {
            str(value).strip()
            for value in (normalized.get("attachment_ids") or [])
            if str(value).strip()
        }
        attachment_keys: set[str] = set()
        if intake.raw_object_key:
            attachment_keys.add(str(intake.raw_object_key).strip())
        for key in ("raw_object_key", "origin_object_key", "source_object_key"):
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                attachment_keys.add(value.strip())

        return str(attachment.id) in attachment_ids or str(attachment.object_key).strip() in attachment_keys

    return False


@dataclass(frozen=True)
class PreviewAuthorizationResult:
    status: str
    object_key: str | None = None
    reason: str | None = None


def ensure_preview_authorization(
    session: Session,
    *,
    revision: SymbolRevision,
    source: str,
    created_at: datetime | None = None,
) -> PreviewAuthorizationResult:
    payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
    preview_asset = choose_published_preview_asset(payload)
    object_key = str((preview_asset or {}).get("object_key") or "").strip()
    if not object_key:
        return PreviewAuthorizationResult(status="no_preview")

    attachment = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
    if attachment is None:
        return PreviewAuthorizationResult(status="missing_attachment", object_key=object_key)

    digest = _normalize_sha256(attachment.sha256)
    if digest is None:
        return PreviewAuthorizationResult(status="missing_sha256", object_key=object_key)

    try:
        size_bytes = int(attachment.size_bytes)
    except (TypeError, ValueError):
        return PreviewAuthorizationResult(status="invalid_size", object_key=object_key)
    if size_bytes < 0:
        return PreviewAuthorizationResult(status="invalid_size", object_key=object_key)

    if not _is_trusted_preview_lineage(session, revision=revision, attachment=attachment):
        return PreviewAuthorizationResult(status="untrusted_lineage", object_key=object_key)

    existing_for_object = (
        session.query(PublishedPreviewAuthorization)
        .filter(PublishedPreviewAuthorization.object_key == object_key)
        .one_or_none()
    )
    if existing_for_object is not None and existing_for_object.symbol_revision_id != revision.id:
        return PreviewAuthorizationResult(status="conflicting_object_key", object_key=object_key)

    existing = (
        session.query(PublishedPreviewAuthorization)
        .filter(
            PublishedPreviewAuthorization.symbol_revision_id == revision.id,
            PublishedPreviewAuthorization.object_key == object_key,
        )
        .one_or_none()
    )
    if existing is not None:
        if (
            existing.attachment_id != attachment.id
            or existing.attachment_sha256 != digest
            or int(existing.attachment_size_bytes) != size_bytes
            or existing.attachment_content_type != attachment.content_type
            or existing.attachment_parent_type != attachment.parent_type
            or existing.attachment_parent_id != attachment.parent_id
        ):
            return PreviewAuthorizationResult(status="immutable_mismatch", object_key=object_key)
        return PreviewAuthorizationResult(status="unchanged", object_key=object_key)

    now = created_at or _utc_now()
    session.add(
        PublishedPreviewAuthorization(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"symgov:preview-auth:{revision.id}:{object_key}"),
            symbol_revision_id=revision.id,
            attachment_id=attachment.id,
            object_key=object_key,
            attachment_parent_type=attachment.parent_type,
            attachment_parent_id=attachment.parent_id,
            attachment_content_type=attachment.content_type,
            attachment_size_bytes=size_bytes,
            attachment_sha256=digest,
            source=source,
            created_at=now,
        )
    )
    session.flush()
    return PreviewAuthorizationResult(status="created", object_key=object_key)


def resolve_authorized_preview_attachment(session: Session, *, revision_id: uuid.UUID, object_key: str) -> Attachment | None:
    auth = (
        session.query(PublishedPreviewAuthorization)
        .filter(
            PublishedPreviewAuthorization.symbol_revision_id == revision_id,
            PublishedPreviewAuthorization.object_key == object_key,
        )
        .one_or_none()
    )
    if auth is not None and not all(
        hasattr(auth, field)
        for field in (
            "attachment_id",
            "attachment_sha256",
            "attachment_size_bytes",
            "attachment_content_type",
            "attachment_parent_type",
            "attachment_parent_id",
        )
    ):
        auth = None
    if auth is not None:
        attachment = (
            session.query(Attachment)
            .filter(
                Attachment.id == auth.attachment_id,
                Attachment.object_key == object_key,
            )
            .one_or_none()
        )
        if attachment is None:
            return None
        digest = _normalize_sha256(attachment.sha256)
        if digest != auth.attachment_sha256:
            return None
        try:
            size_bytes = int(attachment.size_bytes)
        except (TypeError, ValueError):
            return None
        if (
            size_bytes != int(auth.attachment_size_bytes)
            or attachment.content_type != auth.attachment_content_type
            or attachment.parent_type != auth.attachment_parent_type
            or attachment.parent_id != auth.attachment_parent_id
        ):
            return None
        return attachment

    # Legacy fail-closed path while a database is awaiting backfill.
    return (
        session.query(Attachment)
        .filter(
            Attachment.object_key == object_key,
            Attachment.parent_type == "symbol_revision",
            Attachment.parent_id == revision_id,
        )
        .one_or_none()
    )


def backfill_published_preview_authorizations(session: Session, *, apply: bool) -> dict[str, object]:
    rows = (
        session.query(SymbolRevision)
        .filter(SymbolRevision.lifecycle_state == "published")
        .all()
    )
    counters = {
        "published_revisions": len(rows),
        "created": 0,
        "unchanged": 0,
        "no_preview": 0,
        "missing_attachment": 0,
        "missing_sha256": 0,
        "invalid_size": 0,
        "conflicting_object_key": 0,
        "immutable_mismatch": 0,
        "untrusted_lineage": 0,
    }
    failures: list[dict[str, str]] = []
    now = _utc_now()
    for revision in rows:
        result = ensure_preview_authorization(
            session,
            revision=revision,
            source="legacy_backfill",
            created_at=now,
        )
        status = result.status
        if status in counters:
            counters[status] += 1
        if status not in {"created", "unchanged", "no_preview"}:
            failures.append(
                {
                    "symbol_revision_id": str(revision.id),
                    "status": status,
                    "object_key": result.object_key or "",
                    "reason": result.reason or "",
                }
            )
    if apply and not failures:
        session.commit()
    else:
        session.rollback()
    return {**counters, "failures": failures, "applied": bool(apply and not failures)}
