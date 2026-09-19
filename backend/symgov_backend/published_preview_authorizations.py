from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from .models import Attachment, PublishedPreviewAuthorization, SymbolRevision
from .published_catalog import choose_published_preview_asset


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _normalize_sha256(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        return None
    return candidate


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
        else:
            failures.append(
                {
                    "symbol_revision_id": str(revision.id),
                    "status": status,
                    "object_key": result.object_key or "",
                    "reason": result.reason or "",
                }
            )
    if apply:
        session.commit()
    else:
        session.rollback()
    return {**counters, "failures": failures, "applied": bool(apply)}
