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
    this half of the search exists to serve that action. Also gated on
    `settings.organization_symbols_enabled` (WP6.6 audit fix), matching
    `effective_palette.py`'s equivalent gate on the same flag — this
    surface is reachable behind only `symbol_sets_enabled` (see
    `app.py`'s `stage4_route_guard`), so without this check any residual
    organization-private data from a previous enablement window would
    stay visible here even after the feature was turned back off.

The two halves are disjoint by the same structural argument as
`effective_palette.py`: `GovernedSymbol.visibility` is immutable after
creation and a `public`/`organization_private` row can never satisfy both
predicates, so no de-duplication step is needed here (unlike the palette
union, which explicitly guards it defensively).
"""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .catalog_search import catalog_symbol_filters
from .catalog_workbench import load_catalog_clipboard
from .models import GovernedSymbol, OrganizationMemberCapability
from .project_service import get_principal
from .published_catalog import PUBLISHED_SYMBOLS_SQL
from .symbol_eligibility import eligible_organization_private_symbols
from .symbol_identity import governed_symbol_human_readable_id

PUBLIC_SEARCH_ROW_LIMIT = 500


def _has_organization_wide_toggle_authority(session: Session, principal) -> bool:
    if principal.is_admin:
        return True
    return session.query(OrganizationMemberCapability).filter(
        OrganizationMemberCapability.membership_id == principal.membership.id,
        OrganizationMemberCapability.capability == "symbol_reviewer",
        OrganizationMemberCapability.is_active.is_(True),
    ).first() is not None


def _search_public_symbols(
    session: Session,
    *,
    query_text: str | None,
    category: str | None = None,
    discipline: str | None = None,
    format_: str | None = None,
) -> list[dict]:
    filters, params, _ = catalog_symbol_filters(
        q=query_text, discipline=discipline, category=category, use_case=None,
        format_=format_, pack=None, symbol_family=None, has_preview=None, updated_since=None,
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
    return _public_entries(rows)


def _public_entries(rows) -> list[dict]:
    seen: set[str] = set()
    entries = []
    for row in rows:
        if row.symbol_id in seen:
            continue
        seen.add(row.symbol_id)
        entries.append({
            "governedSymbolId": uuid.UUID(row.symbol_id),
            "catalogSymbolId": row.catalog_symbol_id,
            "displayId": row.catalog_symbol_id,
            "source": "public",
            "canonicalName": row.canonical_name,
            "category": row.category,
            "discipline": row.discipline,
            "slug": row.slug,
            "organizationWide": None,
            "currentRevisionId": uuid.UUID(row.symbol_revision_id),
        })
    return entries


def _organization_entry(session: Session, governed) -> dict:
    return {
        "governedSymbolId": governed.id,
        "catalogSymbolId": governed.catalog_symbol_id,
        "displayId": governed_symbol_human_readable_id(session, governed),
        "source": "organization",
        "canonicalName": governed.canonical_name,
        "category": governed.category,
        "discipline": governed.discipline,
        "slug": governed.slug,
        "organizationWide": bool(governed.organization_wide),
        "currentRevisionId": governed.current_revision_id,
    }


def _search_organization_symbols(
    session: Session,
    organization_id: uuid.UUID,
    *,
    query_text: str | None,
    category: str | None = None,
    discipline: str | None = None,
    format_: str | None = None,
) -> list[dict]:
    rows = eligible_organization_private_symbols(
        session,
        organization_id,
        query_text=query_text,
        format_=format_,
    )
    if category:
        category_key = category.casefold()
        rows = [row for row in rows if category_key in (row.category or "").casefold()]
    if discipline:
        discipline_key = discipline.casefold()
        rows = [row for row in rows if discipline_key in (row.discipline or "").casefold()]
    return [_organization_entry(session, governed) for governed in rows]


def search_symbol_set_builder(
    session: Session,
    request,
    settings,
    *,
    query_text: str | None,
    page: int,
    page_size: int,
    category: str | None = None,
    discipline: str | None = None,
    format_: str | None = None,
):
    principal = get_principal(session, request, settings)

    entries = _search_public_symbols(
        session,
        query_text=query_text,
        category=category,
        discipline=discipline,
        format_=format_,
    )
    if settings.organization_symbols_enabled and _has_organization_wide_toggle_authority(session, principal):
        entries.extend(_search_organization_symbols(
            session,
            principal.organization.id,
            query_text=query_text,
            category=category,
            discipline=discipline,
            format_=format_,
        ))

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


def _public_symbols_by_slug(session: Session, slugs: list[str]) -> list[dict]:
    query = text(PUBLISHED_SYMBOLS_SQL + " AND gs.slug IN :slugs ORDER BY gs.id").bindparams(
        bindparam("slugs", expanding=True),
    )
    return _public_entries(session.execute(query, {"slugs": slugs}).all())


def _organization_symbols_by_slug(session: Session, organization_id: uuid.UUID, slugs: list[str]) -> list[dict]:
    symbol_ids = [row.id for row in session.query(GovernedSymbol.id).filter(GovernedSymbol.slug.in_(slugs)).all()]
    if not symbol_ids:
        return []
    rows = eligible_organization_private_symbols(session, organization_id, symbol_ids=symbol_ids)
    return [_organization_entry(session, governed) for governed in rows]


def symbol_set_builder_clipboard(session: Session, request, settings):
    """The session's Catalog clipboard, resolved for the Builder.

    Read-only: the Catalog page writes the clipboard whole, so a Builder
    write would be undone by any Catalog page still open. Clipboard items
    carry the Catalog slug (globally unique, `uq_governed_symbols_slug`),
    and each is resolved through the same two halves as Builder search, so
    an item is addable only if search could have returned it. The rest
    come back as `unavailable`, in clipboard order like the addable items.
    """
    principal = get_principal(session, request, settings)
    clipboard = load_catalog_clipboard(session, principal.user.id, principal.organization.id)
    slugs = [str(item.get("id") or "") for item in clipboard if item.get("id")]

    resolved: dict[str, dict] = {}
    if slugs:
        for entry in _public_symbols_by_slug(session, slugs):
            resolved[entry["slug"]] = entry
        if settings.organization_symbols_enabled and _has_organization_wide_toggle_authority(session, principal):
            for entry in _organization_symbols_by_slug(session, principal.organization.id, slugs):
                resolved.setdefault(entry["slug"], entry)

    items, unavailable = [], []
    for item in clipboard:
        slug = str(item.get("id") or "")
        if slug in resolved:
            items.append(resolved[slug])
        elif slug:
            unavailable.append({
                "slug": slug,
                "displayId": item.get("displayName") or None,
                "name": item.get("name") or None,
            })

    return principal, {"items": items, "unavailable": unavailable, "total": len(clipboard)}
