"""WP5.3 — organization-private symbol draft/revision/intake/asset service.

Per the Stage 5 implementation plan (`docs/plans/2026-09-01-symbol-set-management-stage5-implementation-plan.md`,
§3) and the programme plan (`docs/2026-08-10-symbol-set-management-implementation-plan.md`, §11):

- An active Organization Admin, or an active member with the `contributor`
  capability, creates an owner-bound private draft in their active
  organization only.
- Draft/submitted metadata and assets are visible only to the creator,
  active Organization Admins, and active appointed Organization Reviewers
  (`symbol_reviewer` capability) of the owning organization — ordinary
  members cannot enumerate or infer them.
- Intake/asset validation is deterministic (reuses `validate_stored_image`
  from Stage 4's publication path); no LLM decides persistence authority.
- This module creates drafts and submits a revision to organization
  review; the reviewer-side approve/reject/request-changes decisions are
  WP5.4, not here.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import (
    GovernedSymbol,
    OrganizationSymbolReviewSubmission,
    SymbolRevision,
)
from .product_usage_events import record_governance_usage_event
from .runtime import RuntimePersistenceBridge


class OrganizationSymbolDraftError(ValueError):
    """Domain validation failure — the route maps this to HTTP 400."""


class OrganizationSymbolDraftNotVisible(LookupError):
    """The draft does not exist, or the actor is not authorized to see it.

    Deliberately does not distinguish "not found" from "not authorized" —
    per the plan, ordinary members must not be able to enumerate or infer
    private drafts by probing IDs. The route maps this to HTTP 404.
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _active_organization_id(current_user: AuthenticatedUser) -> uuid.UUID:
    if current_user.session_mode != "organization" or current_user.active_organization_id is None:
        raise OrganizationSymbolDraftError("An organization-bound session is required.")
    return uuid.UUID(current_user.active_organization_id)


def _is_authorized_contributor(current_user: AuthenticatedUser) -> bool:
    return current_user.organization_base_role == "admin" or "contributor" in current_user.organization_capabilities


def _can_view_all_org_drafts(current_user: AuthenticatedUser) -> bool:
    return current_user.organization_base_role == "admin" or "symbol_reviewer" in current_user.organization_capabilities


def _clean_text(value: str | None, *, field: str, required: bool = True, max_length: int = 2000) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise OrganizationSymbolDraftError(f"{field} is required.")
        return None
    if len(text) > max_length:
        raise OrganizationSymbolDraftError(f"{field} must be {max_length} characters or fewer.")
    return text


def _clean_string_list(values: list[str] | None, *, field: str, max_items: int = 25, max_item_length: int = 200) -> list[str]:
    if not values:
        return []
    if len(values) > max_items:
        raise OrganizationSymbolDraftError(f"{field} accepts at most {max_items} entries.")
    cleaned = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if len(text) > max_item_length:
            raise OrganizationSymbolDraftError(f"Each {field} entry must be {max_item_length} characters or fewer.")
        cleaned.append(text)
    return cleaned


def _revision_payload(
    *,
    name: str,
    summary: str,
    description: str | None,
    aliases: list[str],
    keywords: list[str],
) -> dict:
    return {
        "name": name,
        "summary": summary,
        "description": description or summary,
        "aliases": aliases,
        "keywords": keywords,
        "assets": [],
    }


def create_draft(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    name: str,
    category: str,
    discipline: str,
    summary: str,
    description: str | None = None,
    aliases: list[str] | None = None,
    keywords: list[str] | None = None,
) -> tuple[GovernedSymbol, SymbolRevision]:
    organization_id = _active_organization_id(current_user)
    if not _is_authorized_contributor(current_user):
        raise OrganizationSymbolDraftError(
            "Organization Admin privileges or the 'contributor' capability are required to create a draft."
        )

    clean_name = _clean_text(name, field="name", max_length=256)
    clean_category = _clean_text(category, field="category", max_length=128)
    clean_discipline = _clean_text(discipline, field="discipline", max_length=128)
    clean_summary = _clean_text(summary, field="summary", max_length=2000)
    clean_description = _clean_text(description, field="description", required=False, max_length=4000)
    clean_aliases = _clean_string_list(aliases, field="aliases")
    clean_keywords = _clean_string_list(keywords, field="keywords")

    now = _utc_now()
    owner_id = uuid.UUID(current_user.id)
    symbol_id = uuid.uuid4()
    slug = f"org-draft-{symbol_id}"

    symbol = GovernedSymbol(
        id=symbol_id,
        slug=slug,
        canonical_name=clean_name,
        category=clean_category,
        discipline=clean_discipline,
        owner_id=owner_id,
        owner_organization_id=organization_id,
        visibility="organization_private",
        organization_wide=False,
        current_revision_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(symbol)
    session.flush()

    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label=f"draft-{now.date().isoformat()}-{uuid.uuid4().hex[:8]}",
        lifecycle_state="draft",
        payload_json=_revision_payload(
            name=clean_name,
            summary=clean_summary,
            description=clean_description,
            aliases=clean_aliases,
            keywords=clean_keywords,
        ),
        rationale=None,
        author_id=owner_id,
        created_at=now,
    )
    session.add(revision)
    session.flush()

    symbol.current_revision_id = revision.id
    symbol.updated_at = now
    session.flush()
    return symbol, revision


def _visible_draft_query(session: Session, current_user: AuthenticatedUser):
    organization_id = _active_organization_id(current_user)
    query = select(GovernedSymbol).where(
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
    )
    if not _can_view_all_org_drafts(current_user):
        query = query.where(GovernedSymbol.owner_id == uuid.UUID(current_user.id))
    return query


def list_drafts(session: Session, current_user: AuthenticatedUser) -> list[GovernedSymbol]:
    query = _visible_draft_query(session, current_user).order_by(GovernedSymbol.updated_at.desc())
    return list(session.execute(query).scalars().all())


def get_draft(session: Session, current_user: AuthenticatedUser, symbol_id: uuid.UUID) -> GovernedSymbol:
    organization_id = _active_organization_id(current_user)
    symbol = session.get(GovernedSymbol, symbol_id)
    if (
        symbol is None
        or symbol.owner_organization_id != organization_id
        or symbol.visibility != "organization_private"
    ):
        raise OrganizationSymbolDraftNotVisible()
    if not _can_view_all_org_drafts(current_user) and symbol.owner_id != uuid.UUID(current_user.id):
        raise OrganizationSymbolDraftNotVisible()
    return symbol


def get_draft_revision(
    session: Session, current_user: AuthenticatedUser, symbol_id: uuid.UUID, revision_id: uuid.UUID
) -> tuple[GovernedSymbol, SymbolRevision]:
    symbol = get_draft(session, current_user, symbol_id)
    revision = session.get(SymbolRevision, revision_id)
    if revision is None or revision.symbol_id != symbol.id:
        raise OrganizationSymbolDraftNotVisible()
    return symbol, revision


@dataclass(frozen=True)
class DraftAssetUpload:
    id: uuid.UUID
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


def attach_asset(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    revision_id: uuid.UUID,
    filename: str,
    declared_content_type: str,
    payload: bytes,
    storage_env_file: str,
    role: str = "source",
) -> DraftAssetUpload:
    symbol, revision = get_draft_revision(session, current_user, symbol_id, revision_id)
    if symbol.owner_id != uuid.UUID(current_user.id) and current_user.organization_base_role != "admin":
        raise OrganizationSymbolDraftNotVisible()
    if revision.lifecycle_state != "draft":
        raise OrganizationSymbolDraftError("Assets can only be attached while the revision is still a draft.")
    clean_filename = _clean_text(filename, field="filename", max_length=256)
    if not payload:
        raise OrganizationSymbolDraftError("Uploaded file is empty.")

    try:
        media_type = validate_stored_image(payload, declared_content_type)
    except UnsafeImageContentError as exc:
        raise OrganizationSymbolDraftError(str(exc)) from exc

    digest = hashlib.sha256(payload).hexdigest()
    object_key = f"organization-symbols/{symbol.id}/{revision.id}/{uuid.uuid4().hex[:8]}-{clean_filename}"

    bridge = RuntimePersistenceBridge(env_file=storage_env_file)
    attachment = bridge.create_attachment(
        parent_type="symbol_revision",
        parent_id=revision.id,
        filename=clean_filename,
        object_key=object_key,
        content_type=media_type,
        size_bytes=len(payload),
        sha256=digest,
    )
    bridge.upload_object_bytes(
        object_key=object_key,
        payload=payload,
        content_type=media_type,
        env_file=storage_env_file,
    )

    payload_json = dict(revision.payload_json or {})
    assets = list(payload_json.get("assets") or [])
    assets.append(
        {
            "attachment_id": attachment["id"],
            "object_key": object_key,
            "filename": clean_filename,
            "content_type": media_type,
            "role": role,
            "sha256": digest,
            "size_bytes": len(payload),
        }
    )
    payload_json["assets"] = assets
    revision.payload_json = payload_json
    session.flush()

    return DraftAssetUpload(
        id=uuid.UUID(attachment["id"]),
        object_key=object_key,
        filename=clean_filename,
        content_type=media_type,
        size_bytes=len(payload),
        sha256=digest,
    )


def submit_for_review(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    revision_id: uuid.UUID,
    rationale: str | None = None,
) -> OrganizationSymbolReviewSubmission:
    symbol, revision = get_draft_revision(session, current_user, symbol_id, revision_id)
    if symbol.owner_id != uuid.UUID(current_user.id) and current_user.organization_base_role != "admin":
        raise OrganizationSymbolDraftNotVisible()
    if revision.lifecycle_state != "draft":
        raise OrganizationSymbolDraftError("Only a draft revision can be submitted for organization review.")

    clean_rationale = _clean_text(rationale, field="rationale", required=False, max_length=2000)

    now = _utc_now()
    submission = OrganizationSymbolReviewSubmission(
        id=uuid.uuid4(),
        organization_id=symbol.owner_organization_id,
        governed_symbol_id=symbol.id,
        symbol_revision_id=revision.id,
        submitted_by_user_id=uuid.UUID(current_user.id),
        submitted_at=now,
        rationale=clean_rationale,
    )
    session.add(submission)
    try:
        session.flush()
    except IntegrityError as exc:
        raise OrganizationSymbolDraftError(
            "This revision already has an active organization review submission."
        ) from exc

    revision.lifecycle_state = "review"
    record_governance_usage_event(
        session,
        event_type="organization_review_submitted",
        user_id=uuid.UUID(current_user.id),
        organization_id=symbol.owner_organization_id,
        governed_symbol_id=symbol.id,
        symbol_revision_id=revision.id,
        symbol_source="organization_private",
    )
    session.flush()
    return submission
