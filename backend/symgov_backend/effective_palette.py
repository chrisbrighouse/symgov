"""Stage 6 WP6.1 — the effective palette read model.

Per the Stage 6 plan (`docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md`,
WP6.1) and programme plan §12: the effective palette is the deterministic
union of

  (a) approved/eligible `SymbolSetItem` rows for the active Symbol Set, and
  (b) approved organization-owned `GovernedSymbol` rows in the caller's
      active organization where `organization_wide=true`,

de-duplicated by governed-symbol UUID. Public Catalog browsing remains an
independent, separately searchable scope — this module never widens what a
palette contains beyond what the active set actually references plus (b).

Active-set resolution reuses `symbol_context_service._resolved_set`
unchanged (project default / organization default / stored user
preference) and layers one additional, non-persisting tier on top: an
explicit `set_code` supplied on the palette request itself. That explicit
tier is request-scoped only — persisting an explicit choice remains the
job of `symbol_context_service.select_active_set`
(`PUT /org/me/symbol-context/active-set`).

Tenant isolation is structural, not just query-shaped: `SymbolSetItem` can
reference either a `visibility='public'` governed symbol, or an approved,
currently-eligible `visibility='organization_private'` symbol owned by the
*same* organization as the Symbol Set (Stage 11 WP11.1 widened this from
Stage 6's public-only restriction, per the original programme-plan wording
"eligible active-set items (public/private) + organization-wide" —
`docs/2026-08-10-symbol-set-management-implementation-plan.md:1072`).
Every eligibility check — `symbol_set_service.replace_items`/`list_items`/
`copy_set` and this module's `_set_entries`/`_organization_wide_entries` —
routes through `symbol_eligibility.current_symbol_revisions`/
`eligible_organization_private_symbols`, which always scope the private
half to the caller's own `organization_id`; a private symbol can never
become eligible for any other organization's Symbol Set or palette. An
`organization_wide=true` governed symbol is always
`visibility='organization_private'` scoped to exactly one
`owner_organization_id` (enforced by the `organization_wide_scope` CHECK
constraint and the `trg_governed_symbols_organization_wide_eligibility`
deferred trigger — see WP5.1/5.4). `GovernedSymbol.visibility` is never
reassigned after creation anywhere in this codebase, so a symbol can never
move between the public and private halves. The de-duplication step below
is still applied, per the spec's explicit "duplicate union paths"
requirement, as a defensive invariant rather than a currently reachable
case (a private symbol can appear via `_set_entries` and, if also
organization-wide, via `_organization_wide_entries`, so it is reachable
here specifically for that one case).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .models import GovernedSymbol, SymbolSet, SymbolSetItem
from .project_service import get_project, normalize_code
from .public_symbol_eligibility import current_public_symbols
from .symbol_context_service import _eligible_set, _resolved_set, symbol_set_summary
from .symbol_eligibility import current_symbol_revisions, eligible_organization_private_symbols
from .symbol_identity import governed_symbol_human_readable_id

ORGANIZATION_WIDE_GROUP = "Organization-wide"


def _explicit_set(session: Session, principal, project, set_code: str):
    _, normalized_code = normalize_code(set_code)
    symbol_set = session.query(SymbolSet).filter(
        SymbolSet.owner_organization_id == principal.organization.id,
        SymbolSet.normalized_code == normalized_code,
    ).one_or_none()
    if (
        symbol_set is None
        or symbol_set.status != "active"
        or _eligible_set(session, project.id, symbol_set.id) is None
    ):
        raise HTTPException(404, "Not found.")
    return symbol_set


def _set_entries(
    session: Session,
    symbol_set: SymbolSet | None,
    organization_id: uuid.UUID,
    *,
    organization_symbols_enabled: bool,
) -> list[dict]:
    if symbol_set is None:
        return []
    rows = session.query(SymbolSetItem, GovernedSymbol).join(
        GovernedSymbol, GovernedSymbol.id == SymbolSetItem.governed_symbol_id,
    ).filter(
        SymbolSetItem.symbol_set_id == symbol_set.id,
    ).order_by(SymbolSetItem.sort_order, SymbolSetItem.governed_symbol_id).all()
    eligible = current_symbol_revisions(
        session,
        [item.governed_symbol_id for item, _ in rows],
        organization_id,
        organization_symbols_enabled=organization_symbols_enabled,
        public_resolver=current_public_symbols,
    )
    entries = []
    for item, governed in rows:
        if item.governed_symbol_id not in eligible:
            # Kept in the underlying set for Builder diagnosis
            # (`symbol_set_service.list_items` still surfaces it as
            # `unavailable`); palette consumers receive only eligible
            # symbols, per spec task 6.
            continue
        entries.append({
            "governedSymbolId": item.governed_symbol_id,
            "catalogSymbolId": governed.catalog_symbol_id,
            "displayId": governed_symbol_human_readable_id(session, governed),
            "source": "set",
            "canonicalName": governed.canonical_name,
            "category": governed.category,
            "discipline": governed.discipline,
            "sortOrder": item.sort_order,
            "groupName": item.group_name,
            "displayLabel": item.display_label,
            "preferredFormat": item.preferred_format,
            "notes": item.notes,
            "provenance": item.provenance_json or {},
            "currentRevisionId": eligible[item.governed_symbol_id],
        })
    return entries


def _organization_wide_entries(session: Session, organization_id: uuid.UUID, *, start_sort_order: int) -> list[dict]:
    candidate_ids = [symbol_id for symbol_id, in session.query(
        GovernedSymbol.id,
    ).filter(
        GovernedSymbol.owner_organization_id == organization_id,
        GovernedSymbol.visibility == "organization_private",
        GovernedSymbol.organization_wide.is_(True),
    ).order_by(GovernedSymbol.canonical_name, GovernedSymbol.id).all()]
    if not candidate_ids:
        return []
    rows = eligible_organization_private_symbols(
        session,
        organization_id,
        symbol_ids=candidate_ids,
        organization_wide=True,
    )
    entries = []
    for offset, governed in enumerate(rows):
        entries.append({
            "governedSymbolId": governed.id,
            "catalogSymbolId": governed.catalog_symbol_id,
            "displayId": governed_symbol_human_readable_id(session, governed),
            "source": "organization_wide",
            "canonicalName": governed.canonical_name,
            "category": governed.category,
            "discipline": governed.discipline,
            "sortOrder": start_sort_order + offset,
            "groupName": ORGANIZATION_WIDE_GROUP,
            "displayLabel": None,
            "preferredFormat": None,
            "notes": None,
            "provenance": {},
            "currentRevisionId": governed.current_revision_id,
        })
    return entries


def effective_palette(
    session: Session,
    request: Request,
    settings,
    project_id: uuid.UUID,
    *,
    set_code: str | None = None,
    page: int,
    page_size: int,
):
    principal, project = get_project(session, request, settings, project_id)

    if set_code is not None:
        symbol_set = _explicit_set(session, principal, project, set_code)
        reason = "explicit"
    else:
        # Mirrors `symbol_context_service.get_context`'s default:
        # cleanup_stale=True. This can delete a stale
        # `UserProjectSetSelection` row on what is otherwise a read; the
        # route commits the resulting change, same as `GET
        # /org/me/symbol-context`.
        symbol_set, reason = _resolved_set(session, principal, project)

    organization_symbols_enabled = bool(getattr(settings, "organization_symbols_enabled", False))
    entries = _set_entries(
        session, symbol_set, principal.organization.id,
        organization_symbols_enabled=organization_symbols_enabled,
    )

    if organization_symbols_enabled:
        seen_ids = {entry["governedSymbolId"] for entry in entries}
        next_sort_order = max((entry["sortOrder"] for entry in entries), default=-1) + 1
        for entry in _organization_wide_entries(session, principal.organization.id, start_sort_order=next_sort_order):
            if entry["governedSymbolId"] in seen_ids:
                continue
            entries.append(entry)

    entries.sort(key=lambda entry: (entry["sortOrder"], str(entry["governedSymbolId"])))

    total = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start:start + page_size]

    return principal, {
        "activeSet": symbol_set_summary(symbol_set) if symbol_set is not None else None,
        "reason": reason,
        "items": page_entries,
        "page": page,
        "pageSize": page_size,
        "total": total,
    }
