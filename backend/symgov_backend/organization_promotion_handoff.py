"""Stage 7 WP7.3 -- the narrow transition point where an accepted
`PromotionRequest`'s review-case decision hands off to the existing public
publication write path (`published_pages`/`pack_entries`), without routing
through Rupert/duplicate-detection/raster-split (all specific to the
drawing-intake pipeline an organization-symbol promotion has no need for)
and without resolving/creating a governed symbol by slug (per programme plan
§13 task 4/5: reuse the exact existing governed-symbol UUID and
organization-approved revision UUID; never create a second governed
symbol).

Dispatched from `publication_handoff.execute_publication_handoff` via a
single early branch on `review_case.source_entity_type ==
"organization_symbol_promotion"` -- kept in its own module rather than
threaded through the drawing-intake pipeline's own helper functions
(`ensure_approved_symbol_revision`, `approved_revisions_for_decision`,
`build_pack_metadata`, `run_rupert`, ...), because those derive
symbol/revision identity from intake/classification/provenance context an
organization-promotion `ReviewCase` never has (`load_review_context`
returns an all-None context for an unrecognized `source_entity_type`) and
would otherwise silently create a *duplicate phantom governed symbol*
keyed by the review case's own UUID as a fallback slug -- confirmed by
direct inspection of `ensure_approved_symbol_revision`'s slug-resolution
path before this module was written, not assumed. `run_rupert` additionally
spawns a real external subprocess; an already-approved organization symbol
has no image/duplicate-detection processing need for it.

`ensure_publication_approval_target`/`build_publication_approval_revision_targets`
(runtime.py) are likewise not reused as-is: they require every asset object
key in a revision's `payload_json` to resolve to an `Attachment` row with
`parent_type="symbol_revision"`, a shape `organization_symbol_drafts.py`'s
WP5.3 asset pipeline never creates (it stores asset metadata directly in
`payload_json["assets"]`, no `Attachment` row). This module builds its own
immutable `PublicationApprovalTarget` snapshot from the organization
revision's own payload directly, writing to the same table so promotion
still gets the same tamper-evident approval evidence the drawing-intake
pipeline gets -- just computed correctly for how organization-symbol
assets are actually stored.

`PublicationPack.pack_code`/`PublishedPage.page_code` are keyed off the
promotion request's own UUID, not the symbol's slug, so a symbol that is
later demoted and re-promoted through a fresh `PromotionRequest` gets a
fresh pack/page rather than colliding with the (still globally unique)
prior one -- consistent with the spec's "never reactivate older
projections during re-promotion" requirement WP7.4/WP7.6 will need to hold.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .catalog_symbol_ids import ensure_catalog_symbol_id
from .models import (
    AuditEvent,
    GovernedSymbol,
    HumanReviewDecision,
    PackEntry,
    PromotionRequest,
    PromotionRequestDecision,
    PublicationApprovalTarget,
    PublicationPack,
    PublishedPage,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
)
from .product_usage_events import record_governance_usage_event

OPEN_PROMOTION_STATUSES = ("submitted", "triage", "in_review", "changes_requested")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _organization_promotion_revision_target(revision: SymbolRevision) -> dict[str, Any]:
    payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
    assets = [
        {
            "objectKey": asset.get("object_key"),
            "sha256": asset.get("sha256"),
            "filename": asset.get("filename"),
            "contentType": asset.get("content_type"),
        }
        for asset in (payload.get("assets") or [])
        if isinstance(asset, dict)
    ]
    return {
        "symbolRevisionId": str(revision.id),
        "symbolId": str(revision.symbol_id),
        "revisionLabel": revision.revision_label,
        "assets": assets,
    }


def _ensure_organization_promotion_approval_target(
    session: Session, *, review_decision: HumanReviewDecision, revision: SymbolRevision, created_at: datetime
) -> PublicationApprovalTarget:
    """Mirrors `runtime.ensure_publication_approval_target`'s idempotent
    upsert-or-verify contract exactly (same table, same immutability
    guarantee), with a revision-target shape suited to how organization
    symbol assets are actually stored."""
    revision_targets = [_organization_promotion_revision_target(revision)]
    content_sha256 = _canonical_json_sha256(revision_targets)
    existing = (
        session.query(PublicationApprovalTarget)
        .filter_by(review_decision_id=review_decision.id)
        .one_or_none()
    )
    if existing is not None:
        if (
            existing.review_case_id != review_decision.review_case_id
            or existing.revision_targets_json != revision_targets
            or existing.content_sha256 != content_sha256
        ):
            raise RuntimeError(
                "Existing immutable publication approval target does not match approved content identity."
            )
        return existing
    target = PublicationApprovalTarget(
        id=uuid.uuid4(),
        review_decision_id=review_decision.id,
        review_case_id=review_decision.review_case_id,
        revision_targets_json=revision_targets,
        content_sha256=content_sha256,
        created_at=created_at,
    )
    session.add(target)
    session.flush()
    return target


def execute_organization_promotion_handoff(
    session: Session,
    *,
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    action: ReviewCaseAction,
    approval_actor: dict[str, Any],
    close_review_case: bool = True,
    commit_transaction: bool = True,
) -> dict[str, Any]:
    now = _utc_now()

    def _fail(detail: str) -> dict[str, Any]:
        action.action_status = "failed"
        action.completed_at = now
        action.action_payload_json = {**(action.action_payload_json or {}), "error": detail}
        if commit_transaction:
            session.commit()
        else:
            session.flush()
        return {"status": "failed", "detail": detail}

    promotion_request = session.get(PromotionRequest, review_case.source_entity_id, with_for_update=True)
    if promotion_request is None or promotion_request.review_case_id != review_case.id:
        return _fail("Promotion request not found or does not match this review case.")
    if promotion_request.status not in OPEN_PROMOTION_STATUSES:
        return _fail("Promotion request is not in an open state.")

    # Lock the governed-symbol row -- the same shared serialization boundary
    # `symbol_set_service.py`'s set-item writers and `submit_promotion_request`
    # already take (Stage 7 plan §1.4) -- before re-verifying eligibility.
    symbol = session.get(GovernedSymbol, promotion_request.governed_symbol_id, with_for_update=True)
    if (
        symbol is None
        or symbol.owner_organization_id != promotion_request.organization_id
        or symbol.visibility != "organization_private"
        or symbol.current_revision_id != promotion_request.symbol_revision_id
    ):
        # Never resolve/republish by mutable slug (§13 task 4): if the
        # symbol's current revision has moved on since submission (e.g. a
        # fresh draft revision was started), this exact snapshot is stale
        # and must not be silently republished under a different revision.
        return _fail(
            "The symbol's current revision no longer matches the revision this promotion request approved; "
            "a fresh promotion request against the current revision is required."
        )

    revision = session.get(SymbolRevision, promotion_request.symbol_revision_id)
    if revision is None or revision.symbol_id != symbol.id:
        return _fail("The approved revision could not be found.")

    approval_target = _ensure_organization_promotion_approval_target(
        session, review_decision=decision, revision=revision, created_at=now
    )

    # visibility must flip to 'public' before allocating a catalog symbol
    # ID: `ck_governed_symbols_catalog_symbol_visibility_barrier` requires
    # catalog_symbol_id is null OR visibility='public', and
    # ensure_catalog_symbol_id flushes as soon as it sets catalog_symbol_id.
    revision.lifecycle_state = "published"
    symbol.visibility = "public"
    symbol.updated_at = now

    ensure_catalog_symbol_id(session, symbol.id, allocated_at=now)

    pack = PublicationPack(
        id=uuid.uuid4(),
        pack_code=f"ORG-PROMOTION-{promotion_request.id}",
        title=symbol.canonical_name,
        audience="public",
        effective_date=now.date(),
        status="published",
        created_at=now,
        updated_at=now,
    )
    session.add(pack)
    session.flush()

    page = PublishedPage(
        id=uuid.uuid4(),
        page_code=f"ORG-PAGE-{promotion_request.id}",
        title=symbol.canonical_name,
        pack_id=pack.id,
        current_symbol_revision_id=revision.id,
        effective_date=now.date(),
        publication_state="active",
        created_at=now,
        updated_at=now,
    )
    session.add(page)
    session.flush()

    entry = PackEntry(
        id=uuid.uuid4(),
        pack_id=pack.id,
        symbol_revision_id=revision.id,
        published_page_id=page.id,
        sort_order=1,
        publication_state="active",
        created_at=now,
    )
    session.add(entry)

    from_status = promotion_request.status
    promotion_request.status = "accepted"
    promotion_request.closed_at = now
    promotion_request.updated_at = now

    session.add(
        PromotionRequestDecision(
            id=uuid.uuid4(),
            promotion_request_id=promotion_request.id,
            decision_code="accepted",
            from_status=from_status,
            to_status="accepted",
            decided_by_user_id=decision.decided_by,
            decider_name=decision.decider_name,
            decider_role=decision.decider_role,
            note=decision.decision_note,
            created_at=now,
        )
    )

    action.action_status = "completed"
    action.completed_at = now
    action.action_payload_json = {
        **(action.action_payload_json or {}),
        "approval_target_id": str(approval_target.id),
        "approval_content_sha256": approval_target.content_sha256,
        "published_page_id": str(page.id),
        "publication_pack_id": str(pack.id),
        "governed_symbol_id": str(symbol.id),
        "approval_actor": approval_actor,
    }

    if close_review_case:
        review_case.current_stage = "published"
        review_case.closed_at = now

    session.add(
        AuditEvent(
            entity_type="governed_symbol",
            entity_id=symbol.id,
            action="promotion_request.accepted",
            actor_id=decision.decided_by,
            payload_json={
                "promotionRequestId": str(promotion_request.id),
                "reviewCaseId": str(review_case.id),
                "reviewDecisionId": str(decision.id),
                "symbolRevisionId": str(revision.id),
                "publishedPageId": str(page.id),
                "publicationPackId": str(pack.id),
            },
            created_at=now,
        )
    )
    record_governance_usage_event(
        session,
        event_type="publication_decided",
        user_id=decision.decided_by,
        organization_id=promotion_request.organization_id,
        governed_symbol_id=symbol.id,
        symbol_revision_id=revision.id,
        symbol_source="public",
        occurred_at=now,
    )

    if commit_transaction:
        session.commit()
    else:
        session.flush()

    return {
        "status": "completed",
        "governed_symbol_id": str(symbol.id),
        "published_page_id": str(page.id),
        "publication_pack_id": str(pack.id),
    }
