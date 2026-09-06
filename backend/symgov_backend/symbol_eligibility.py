"""Stage 11 WP11.1 — same-organization approved-private-symbol eligibility.

Ported from the `symgov-stage6-fixes` worktree (`fix/stage6-review-remediation-20260902`)
per the Stage 11 kickoff/implementation plan §1.6 and §4's confirmed decision: loosen
Stage 6's public-only `SymbolSetItem` restriction so an approved same-organization
`organization_private` symbol may become a direct Symbol Set item, matching the
original programme-plan wording ("eligible active-set items (public/private) +
organization-wide", `docs/2026-08-10-symbol-set-management-implementation-plan.md:1072`).

Cross-organization access is never widened: every query below filters on the
caller's own `organization_id` and the approval/closed-submission chain for that
same organization, mirroring `symbol_set_builder._search_organization_symbols`'s
existing (Stage 6) cross-tenant-safe join shape.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Text, and_, cast, or_
from sqlalchemy.orm import Session

from .models import (
    GovernedSymbol,
    OrganizationSymbolReviewDecision,
    OrganizationSymbolReviewSubmission,
    SymbolRevision,
)
from .public_symbol_eligibility import current_public_symbols


def eligible_organization_private_symbols(
    session: Session,
    organization_id: uuid.UUID,
    *,
    symbol_ids: list[uuid.UUID] | None = None,
    organization_wide: bool | None = None,
    query_text: str | None = None,
    format_: str | None = None,
) -> list[GovernedSymbol]:
    """Return current, approved private symbols visible to one organization.

    The current revision and its exact closed approval are joined together so
    an older approval cannot make a newer draft/rejected revision visible.
    This is the single application-level predicate used by Symbol Set readers
    and writers; database triggers remain the final invariant for writes.
    """
    query = session.query(GovernedSymbol).join(
        SymbolRevision,
        and_(
            SymbolRevision.id == GovernedSymbol.current_revision_id,
            SymbolRevision.symbol_id == GovernedSymbol.id,
            SymbolRevision.lifecycle_state == "approved",
        ),
    ).join(
        OrganizationSymbolReviewDecision,
        and_(
            OrganizationSymbolReviewDecision.organization_id == organization_id,
            OrganizationSymbolReviewDecision.governed_symbol_id == GovernedSymbol.id,
            OrganizationSymbolReviewDecision.symbol_revision_id == SymbolRevision.id,
            OrganizationSymbolReviewDecision.decision == "approved",
        ),
    ).join(
        OrganizationSymbolReviewSubmission,
        and_(
            OrganizationSymbolReviewSubmission.id == OrganizationSymbolReviewDecision.submission_id,
            OrganizationSymbolReviewSubmission.organization_id == organization_id,
            OrganizationSymbolReviewSubmission.governed_symbol_id == GovernedSymbol.id,
            OrganizationSymbolReviewSubmission.symbol_revision_id == SymbolRevision.id,
            OrganizationSymbolReviewSubmission.status == "closed",
        ),
    ).filter(
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
    )
    if symbol_ids is not None:
        query = query.filter(GovernedSymbol.id.in_(symbol_ids))
    if organization_wide is not None:
        query = query.filter(GovernedSymbol.organization_wide.is_(organization_wide))
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(or_(
            GovernedSymbol.canonical_name.ilike(like),
            GovernedSymbol.category.ilike(like),
            GovernedSymbol.discipline.ilike(like),
            GovernedSymbol.slug.ilike(like),
        ))
    if format_:
        query = query.filter(cast(SymbolRevision.payload_json, Text).ilike(f"%{format_}%"))
    return query.order_by(GovernedSymbol.canonical_name, GovernedSymbol.id).all()


def current_symbol_revisions(
    session: Session,
    symbol_ids: list[uuid.UUID],
    organization_id: uuid.UUID,
    *,
    organization_symbols_enabled: bool,
    public_resolver=current_public_symbols,
) -> dict[uuid.UUID, uuid.UUID]:
    """Return the current eligible revision for each authorized symbol.

    Public eligibility is supplied by the caller so existing tests and callers
    can retain their public-catalog resolver. Organization-private eligibility
    is always evaluated by the shared approval/tenant predicate above, and is
    only ever widened to the caller's own organization.
    """
    if not symbol_ids:
        return {}
    eligible = dict(public_resolver(session, symbol_ids))
    if not organization_symbols_enabled:
        return eligible
    private_symbol_ids = [symbol_id for symbol_id, visibility in session.query(
        GovernedSymbol.id,
        GovernedSymbol.visibility,
    ).filter(GovernedSymbol.id.in_(symbol_ids)).all() if visibility == "organization_private"]
    if private_symbol_ids:
        for symbol in eligible_organization_private_symbols(session, organization_id, symbol_ids=private_symbol_ids):
            # Visibility is immutable in the product model. Keep public precedence
            # defensively if malformed historical data ever overlaps the halves.
            if symbol.current_revision_id is not None:
                eligible.setdefault(symbol.id, symbol.current_revision_id)
    return eligible
