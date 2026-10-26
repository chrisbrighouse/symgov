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
from .catalog_categories import canonical_catalog_category, catalog_category_names
from .catalog_facets import CATALOG_DISCIPLINE_ORDER, canonical_discipline
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import (
    Attachment,
    AuditEvent,
    GovernedSymbol,
    OrganizationSymbolReviewDecision,
    OrganizationSymbolReviewSubmission,
    SymbolRevision,
    User,
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


def record_organization_symbol_audit(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    action: str,
    symbol: GovernedSymbol,
    revision_id: uuid.UUID | None = None,
    submission_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    """One `AuditEvent` per organization-private workflow transition (spec FR18)."""
    payload: dict = {"organization_id": str(symbol.owner_organization_id)}
    if revision_id is not None:
        payload["symbol_revision_id"] = str(revision_id)
    if submission_id is not None:
        payload["submission_id"] = str(submission_id)
    if details:
        payload.update(details)
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="governed_symbol",
            entity_id=symbol.id,
            action=action,
            actor_id=uuid.UUID(current_user.id),
            payload_json=payload,
            created_at=_utc_now(),
        )
    )


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
    category: str,
    discipline: str,
    summary: str,
    description: str | None,
    aliases: list[str],
    keywords: list[str],
) -> dict:
    return {
        "name": name,
        "category": category,
        "discipline": discipline,
        "summary": summary,
        "description": description or summary,
        "aliases": aliases,
        "keywords": keywords,
        "assets": [],
    }


def _clean_discipline(discipline: str) -> str:
    clean = canonical_discipline(_clean_text(discipline, field="discipline", max_length=128))
    if clean is None:
        # Only the Catalog's standard names are stored (X-04).
        raise OrganizationSymbolDraftError(
            f"Unknown discipline '{discipline.strip()}'. Choose one of: {', '.join(CATALOG_DISCIPLINE_ORDER)}."
        )
    return clean


def _clean_category(session: Session, category: str, *, current: str | None = None) -> str:
    """One of the Catalog's categories: the built-in names plus reviewer additions."""
    text = _clean_text(category, field="category", max_length=128)
    clean = canonical_catalog_category(session, text)
    if clean is not None:
        return clean
    if current is not None and text == current:
        # A draft saved before the list was enforced keeps its value until
        # someone changes it.
        return text
    raise OrganizationSymbolDraftError(
        f"Unknown category '{category.strip()}'. Choose one of: {', '.join(catalog_category_names(session))}."
    )


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
    clean_category = _clean_category(session, category)
    clean_discipline = _clean_discipline(discipline)
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
            category=clean_category,
            discipline=clean_discipline,
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
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.draft_created", symbol=symbol, revision_id=revision.id
    )
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


def _can_edit_revision(current_user: AuthenticatedUser, symbol: GovernedSymbol, revision: SymbolRevision) -> bool:
    actor = uuid.UUID(current_user.id)
    return (
        current_user.organization_base_role == "admin"
        or symbol.owner_id == actor
        or revision.author_id == actor
    )


def _workflow_revision(symbol: GovernedSymbol, revision: SymbolRevision) -> None:
    """Only the symbol's current revision, or its pending one, is in play."""
    if revision.id not in (symbol.current_revision_id, symbol.pending_revision_id):
        raise OrganizationSymbolDraftError("This revision has been superseded.")


def get_editable_draft_revision(
    session: Session, current_user: AuthenticatedUser, symbol_id: uuid.UUID, revision_id: uuid.UUID
) -> tuple[GovernedSymbol, SymbolRevision]:
    symbol, revision = get_draft_revision(session, current_user, symbol_id, revision_id)
    if not _can_edit_revision(current_user, symbol, revision):
        raise OrganizationSymbolDraftNotVisible()
    _workflow_revision(symbol, revision)
    if revision.lifecycle_state != "draft":
        raise OrganizationSymbolDraftError("Only a draft revision can be changed.")
    return symbol, revision


def update_draft(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    revision_id: uuid.UUID,
    name: str | None = None,
    category: str | None = None,
    discipline: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
    keywords: list[str] | None = None,
) -> tuple[GovernedSymbol, SymbolRevision]:
    """Edit a draft revision's details; omitted fields keep their value.

    A symbol's own name, category and discipline are what readers show, so
    they change here only while this draft is the symbol's only version. For
    a pending revision of a published symbol they are held in the revision
    and applied on approval.
    """
    symbol, revision = get_editable_draft_revision(session, current_user, symbol_id, revision_id)
    payload_json = dict(revision.payload_json or {})
    changed: list[str] = []

    def put(key: str, value) -> None:
        if payload_json.get(key) != value:
            payload_json[key] = value
            changed.append(key)

    if name is not None:
        put("name", _clean_text(name, field="name", max_length=256))
    if category is not None:
        put("category", _clean_category(session, category, current=payload_json.get("category")))
    if discipline is not None:
        put("discipline", _clean_discipline(discipline))
    if summary is not None:
        put("summary", _clean_text(summary, field="summary", max_length=2000))
    if description is not None:
        put("description", _clean_text(description, field="description", required=False, max_length=4000)
            or payload_json.get("summary"))
    if aliases is not None:
        put("aliases", _clean_string_list(aliases, field="aliases"))
    if keywords is not None:
        put("keywords", _clean_string_list(keywords, field="keywords"))
    if not changed:
        return symbol, revision

    revision.payload_json = payload_json
    now = _utc_now()
    if revision.id == symbol.current_revision_id:
        symbol.canonical_name = payload_json.get("name") or symbol.canonical_name
        symbol.category = payload_json.get("category") or symbol.category
        symbol.discipline = payload_json.get("discipline") or symbol.discipline
    symbol.updated_at = now
    session.flush()
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.draft_updated", symbol=symbol, revision_id=revision.id,
        details={"fields": sorted(changed)},
    )
    return symbol, revision


def remove_asset(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    revision_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> None:
    """Take an asset off a draft revision. The stored object and its
    `Attachment` row are kept, so the audit trail can still name it."""
    symbol, revision = get_editable_draft_revision(session, current_user, symbol_id, revision_id)
    payload_json = dict(revision.payload_json or {})
    assets = list(payload_json.get("assets") or [])
    remaining = [asset for asset in assets if str(asset.get("attachment_id")) != str(attachment_id)]
    if len(remaining) == len(assets):
        raise OrganizationSymbolDraftNotVisible()
    removed = next(asset for asset in assets if str(asset.get("attachment_id")) == str(attachment_id))
    payload_json["assets"] = remaining
    revision.payload_json = payload_json
    symbol.updated_at = _utc_now()
    session.flush()
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.asset_removed", symbol=symbol, revision_id=revision.id,
        details={"attachment_id": str(attachment_id), "filename": removed.get("filename")},
    )


def get_draft_asset(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    revision_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> Attachment:
    """The stored attachment behind one asset on a revision, for previewing.

    Visible to whoever can see the draft (its owner, Org Reviewers and Org
    Admins). The asset must still be listed on the revision, so a removed
    asset stops previewing, and the attachment row must belong to that
    revision under the same object key."""
    _symbol, revision = get_draft_revision(session, current_user, symbol_id, revision_id)
    asset = next(
        (item for item in (revision.payload_json or {}).get("assets") or []
         if str(item.get("attachment_id")) == str(attachment_id)),
        None,
    )
    if asset is None:
        raise OrganizationSymbolDraftNotVisible()
    attachment = session.get(Attachment, attachment_id)
    if (
        attachment is None
        or attachment.parent_type != "symbol_revision"
        or attachment.parent_id != revision.id
        or attachment.object_key != asset.get("object_key")
    ):
        raise OrganizationSymbolDraftNotVisible()
    return attachment


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
    if not _can_edit_revision(current_user, symbol, revision):
        raise OrganizationSymbolDraftNotVisible()
    if revision.lifecycle_state != "draft":
        raise OrganizationSymbolDraftError("Assets can only be attached while the revision is still a draft.")
    _workflow_revision(symbol, revision)
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
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.asset_added", symbol=symbol, revision_id=revision.id,
        details={"attachment_id": attachment["id"], "filename": clean_filename, "role": role},
    )

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
    if not _can_edit_revision(current_user, symbol, revision):
        raise OrganizationSymbolDraftNotVisible()
    if revision.lifecycle_state != "draft":
        raise OrganizationSymbolDraftError("Only a draft revision can be submitted for organization review.")
    _workflow_revision(symbol, revision)

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
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.submitted", symbol=symbol, revision_id=revision.id,
        submission_id=submission.id,
    )
    return submission


def withdraw_submission(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    submission_id: uuid.UUID,
) -> OrganizationSymbolReviewSubmission:
    """The submitter takes a pending submission back; the revision returns to draft."""
    symbol = get_draft(session, current_user, symbol_id)
    submission = session.get(OrganizationSymbolReviewSubmission, submission_id, with_for_update=True)
    if submission is None or submission.governed_symbol_id != symbol.id:
        raise OrganizationSymbolDraftNotVisible()
    if submission.submitted_by_user_id != uuid.UUID(current_user.id):
        raise OrganizationSymbolDraftError("Only the person who submitted it can withdraw a submission.")
    if submission.status != "active":
        raise OrganizationSymbolDraftError("This submission has already been decided or withdrawn.")

    now = _utc_now()
    submission.status = "closed"
    submission.closed_at = now
    submission.closure_reason = "withdrawn"
    revision = session.get(SymbolRevision, submission.symbol_revision_id)
    if revision is not None and revision.lifecycle_state == "review":
        revision.lifecycle_state = "draft"
    symbol.updated_at = now
    session.flush()
    record_organization_symbol_audit(
        session, current_user, action="org_symbol.withdrawn", symbol=symbol,
        revision_id=submission.symbol_revision_id, submission_id=submission.id,
    )
    return submission


@dataclass(frozen=True)
class SubmissionHistoryEntry:
    submission: OrganizationSymbolReviewSubmission
    revision: SymbolRevision
    submitted_by_name: str | None
    decision: OrganizationSymbolReviewDecision | None
    decided_by_name: str | None

    @property
    def outcome(self) -> str:
        if self.submission.status == "active":
            return "active"
        if self.submission.closure_reason == "withdrawn":
            return "withdrawn"
        return self.decision.decision if self.decision is not None else "decided"


def submission_history(
    session: Session, current_user: AuthenticatedUser, symbol_id: uuid.UUID
) -> list[SubmissionHistoryEntry]:
    """Every submission of every revision of one symbol, newest first."""
    return history_for_symbol(session, get_draft(session, current_user, symbol_id))


def history_for_symbol(session: Session, symbol: GovernedSymbol) -> list[SubmissionHistoryEntry]:
    """`submission_history` without the caller check, for a caller that has
    already established it may see this symbol (the platform support view)."""
    submissions = session.execute(
        select(OrganizationSymbolReviewSubmission)
        .where(
            OrganizationSymbolReviewSubmission.governed_symbol_id == symbol.id,
            OrganizationSymbolReviewSubmission.organization_id == symbol.owner_organization_id,
        )
        .order_by(
            OrganizationSymbolReviewSubmission.submitted_at.desc(),
            OrganizationSymbolReviewSubmission.closed_at.desc().nulls_first(),
            OrganizationSymbolReviewSubmission.id,
        )
    ).scalars().all()
    decisions = {
        decision.submission_id: decision
        for decision in session.execute(
            select(OrganizationSymbolReviewDecision).where(
                OrganizationSymbolReviewDecision.governed_symbol_id == symbol.id
            )
        ).scalars()
    }
    user_ids = {item.submitted_by_user_id for item in submissions} | {d.decided_by_user_id for d in decisions.values()}
    names = {
        user.id: user.display_name
        for user in session.execute(select(User).where(User.id.in_(user_ids))).scalars()
    } if user_ids else {}
    entries = []
    for item in submissions:
        decision = decisions.get(item.id)
        entries.append(
            SubmissionHistoryEntry(
                submission=item,
                revision=session.get(SymbolRevision, item.symbol_revision_id),
                submitted_by_name=names.get(item.submitted_by_user_id),
                decision=decision,
                decided_by_name=names.get(decision.decided_by_user_id) if decision is not None else None,
            )
        )
    return entries
