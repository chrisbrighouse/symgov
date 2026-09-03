"""Stage 8 WP8.1/WP8.2 -- the organization-wide private-symbol Catalog list
query and detail resolver.

Extends `routes/published.py`'s Catalog list (`GET /published/symbols`) to
additionally surface an organization-bound session's own organization-wide
private symbols, merged into the same list as public results via a
`source` discriminator (`"public"` | `"organization_private"`), per the
Stage 8 plan (`docs/plans/2026-09-03-symbol-set-management-stage8-implementation-plan.md`,
§1.2/§1.4/§4 Q2/Q3). WP8.2 adds the matching single-symbol detail resolver
(`resolve_organization_wide_catalog_symbol`), by raw governed-symbol UUID
scoped to the caller's own active organization, per §1.6 -- additive to,
and never modifying, `catalog_symbol_resolution.resolve_catalog_symbol`,
which structurally cannot resolve a private symbol at all (no
`catalog_symbol_id`) and must stay exactly as Stage 7's audit proved it.

Organization-private symbols never have a `PublishedPage`/`PackEntry`/
`catalog_symbol_id` (only Stage 7's promotion pipeline ever creates those),
so this is a separate, additive query -- not a WHERE-clause change to
`PUBLISHED_SYMBOLS_SQL`. The predicate mirrors `effective_palette.py`'s
`_organization_wide_entries` exactly: owner organization,
`visibility='organization_private'`, `organization_wide=true`. Per the
Stage 8 plan §4 Q2, this is deliberately narrower than the decision
addendum's I-09 "set-only private symbol" category -- that category was
never implemented (`symbol_set_service.py` structurally rejects adding any
non-public symbol to a Symbol Set) and is treated as superseded.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import GovernedSymbol, SymbolRevision


def list_organization_wide_catalog_symbols(
    session: Session,
    organization_id: uuid.UUID,
    *,
    query: str | None = None,
) -> list[tuple[GovernedSymbol, SymbolRevision | None]]:
    """Return the caller's active organization's browsable private symbols.

    Ordered by canonical name for a stable, deterministic merge with the
    public result set -- the frontend re-sorts client-side regardless
    (`StandardsPage`, per the Stage 8 plan §1.8).
    """
    filters = [
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
        GovernedSymbol.organization_wide.is_(True),
    ]
    symbol_query = session.query(GovernedSymbol).filter(*filters)
    if query:
        like = f"%{query}%"
        symbol_query = symbol_query.filter(
            or_(
                GovernedSymbol.slug.ilike(like),
                GovernedSymbol.canonical_name.ilike(like),
                GovernedSymbol.category.ilike(like),
                GovernedSymbol.discipline.ilike(like),
            )
        )
    symbols = symbol_query.order_by(GovernedSymbol.canonical_name, GovernedSymbol.id).all()

    revision_ids = [gs.current_revision_id for gs in symbols if gs.current_revision_id is not None]
    revisions_by_id: dict[uuid.UUID, SymbolRevision] = {}
    if revision_ids:
        revisions_by_id = {
            revision.id: revision
            for revision in session.query(SymbolRevision).filter(SymbolRevision.id.in_(revision_ids)).all()
        }
    return [(gs, revisions_by_id.get(gs.current_revision_id)) for gs in symbols]


def resolve_organization_wide_catalog_symbol(
    session: Session,
    symbol_ref: str,
    organization_id: uuid.UUID,
) -> tuple[GovernedSymbol, SymbolRevision | None] | None:
    """Resolve a Catalog detail/preview lookup for the caller's own
    organization-wide private symbol, by raw governed-symbol UUID only --
    not slug, not any alias scheme -- per the Stage 8 plan §1.6. Scope
    matches `list_organization_wide_catalog_symbols` exactly:
    `organization_wide=true` only (§4 Q2), owned by the given organization.
    Returns `None` (never raises) for a non-UUID `symbol_ref`, an unknown
    id, a symbol not owned by this organization, or one that is not
    organization-wide -- the caller decides what a miss means (WP8.2's
    callers fall back to a 404, exactly as the public resolution path
    already does for its own misses).
    """
    try:
        symbol_id = uuid.UUID(symbol_ref)
    except (TypeError, ValueError):
        return None
    symbol = session.query(GovernedSymbol).filter(
        GovernedSymbol.id == symbol_id,
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
        GovernedSymbol.organization_wide.is_(True),
    ).one_or_none()
    if symbol is None:
        return None
    revision = (
        session.get(SymbolRevision, symbol.current_revision_id)
        if symbol.current_revision_id is not None
        else None
    )
    return symbol, revision
