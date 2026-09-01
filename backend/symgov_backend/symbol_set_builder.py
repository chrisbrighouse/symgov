"""Stage 6 WP6.2 — Symbol Set Builder search.

Per the Stage 6 plan (`docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md`,
WP6.2) and programme plan §12 task 7: "Add Symbol Set Builder search over
Public Catalog plus authorized organization symbols, with server-side
authorization and no client-only filtering."

This is deliberately a *search* endpoint, not the palette (`effective_palette.py`)
and not the item-mutation endpoint (unchanged per Chris's 2026-09-01
decision: item mutation stays on the existing full-replace
`PUT /org/me/symbol-sets/{setId}/items`). It answers "what could I add to
a Set, or mark organization-wide" — the union it returns is:

  - `source="public"` — currently public-eligible governed symbols
    (addable as a `SymbolSetItem` via the existing full-replace
    endpoint), reusing `PUBLISHED_SYMBOLS_SQL` rather than restating the
    join.
  - `source="organization"` — the caller's own organization's *approved*
    organization-private symbols (candidates for the `organization_wide`
    toggle, WP6.3) — never another organization's, and never a
    draft/rejected/unapproved revision. Visible only to an Organization
    Admin or an active `symbol_reviewer`, mirroring who is authorized to
    actually decide the toggle (WP6.3's authorization decision), since
    this half of the search exists to serve that action.

The two halves are disjoint by the same structural argument as
`effective_palette.py`: `GovernedSymbol.visibility` is immutable after
creation and a `public`/`organization_private` row can never satisfy both
predicates, so no de-duplication step is needed here (unlike the palette
union, which explicitly guards it defensively).
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

from .catalog_search import catalog_symbol_filters
from .models import GovernedSymbol, OrganizationMemberCapability, OrganizationSymbolReviewDecision, OrganizationSymbolReviewSubmission, SymbolRevision
from .project_service import get_principal
from .published_catalog import PUBLISHED_SYMBOLS_SQL

PUBLIC_SEARCH_ROW_LIMIT = 500


def _has_organization_wide_toggle_authority(session: Session, principal) -> bool:
    if principal.is_admin:
        return True
    return session.query(OrganizationMemberCapability).filter(
        OrganizationMemberCapability.membership_id == principal.membership.id,
        OrganizationMemberCapability.capability == "symbol_reviewer",
        OrganizationMemberCapability.is_active.is_(True),
    ).first() is not None


def _search_public_symbols(session: Session, *, query_text: str | None) -> list[dict]:
    filters, params, _ = catalog_symbol_filters(
        q=query_text, discipline=None, category=None, use_case=None,
        format_=None, pack=None, symbol_family=None, has_preview=None, updated_since=None,
    )
    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    params["limit"] = PUBLIC_SEARCH_ROW_LIMIT
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL + where_extension +
            " ORDER BY gs.canonical_name, gs.id LIMIT :limit"
        ),
        params,
    ).all()
    seen: set[str] = set()
    entries = []
    for row in rows:
        if row.symbol_id in seen:
            continue
        seen.add(row.symbol_id)
        entries.append({
            "governedSymbolId": uuid.UUID(row.symbol_id),
            "source": "public",
            "canonicalName": row.canonical_name,
            "category": row.category,
            "discipline": row.discipline,
            "slug": row.slug,
            "organizationWide": None,
            "currentRevisionId": uuid.UUID(row.symbol_revision_id),
        })
    return entries


def _search_organization_symbols(session: Session, organization_id: uuid.UUID, *, query_text: str | None) -> list[dict]:
    query = session.query(GovernedSymbol).join(
        SymbolRevision,
        and_(SymbolRevision.id == GovernedSymbol.current_revision_id, SymbolRevision.symbol_id == GovernedSymbol.id),
    ).join(
        OrganizationSymbolReviewDecision,
        and_(
            OrganizationSymbolReviewDecision.organization_id == GovernedSymbol.owner_organization_id,
            OrganizationSymbolReviewDecision.governed_symbol_id == GovernedSymbol.id,
            OrganizationSymbolReviewDecision.symbol_revision_id == SymbolRevision.id,
            OrganizationSymbolReviewDecision.decision == "approved",
        ),
    ).join(
        OrganizationSymbolReviewSubmission,
        and_(
            OrganizationSymbolReviewSubmission.id == OrganizationSymbolReviewDecision.submission_id,
            OrganizationSymbolReviewSubmission.status == "closed",
        ),
    ).filter(
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
    )
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(or_(
            GovernedSymbol.canonical_name.ilike(like),
            GovernedSymbol.category.ilike(like),
            GovernedSymbol.discipline.ilike(like),
            GovernedSymbol.slug.ilike(like),
        ))
    rows = query.order_by(GovernedSymbol.canonical_name, GovernedSymbol.id).all()
    return [
        {
            "governedSymbolId": governed.id,
            "source": "organization",
            "canonicalName": governed.canonical_name,
            "category": governed.category,
            "discipline": governed.discipline,
            "slug": governed.slug,
            "organizationWide": bool(governed.organization_wide),
            "currentRevisionId": governed.current_revision_id,
        }
        for governed in rows
    ]


def search_symbol_set_builder(
    session: Session,
    request,
    settings,
    *,
    query_text: str | None,
    page: int,
    page_size: int,
):
    principal = get_principal(session, request, settings)

    entries = _search_public_symbols(session, query_text=query_text)
    if _has_organization_wide_toggle_authority(session, principal):
        entries.extend(_search_organization_symbols(session, principal.organization.id, query_text=query_text))

    entries.sort(key=lambda entry: (entry["canonicalName"], str(entry["governedSymbolId"])))

    total = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start:start + page_size]

    return principal, {
        "items": page_entries,
        "page": page,
        "pageSize": page_size,
        "total": total,
    }
