"""Stage 7 WP7.4 -- demotion eligibility, impact preview, and locked
transactional demotion (programme plan §13 tasks 8-10).

Eligibility (§13 task 8, FR-PUB-008/009):
- The symbol must currently be `visibility == 'public'`.
- Ownerless legacy public symbols (`owner_organization_id is None`) can
  never be demoted -- there is no owning organization to demote it back to
  (§13 task 9).
- No `SymbolSetItem` owned by a *different* organization may currently
  reference the governed symbol. Favorites, project use, previews,
  searches, views, downloads, and API reads never affect eligibility
  (§10.3 of the spec) -- they are reported by `preview_demotion` for
  impact only, using only real, already-tracked data
  (`CatalogFavourite`); no historical usage is fabricated, per `CLAUDE.md`.

Locking (§13 tasks 7-9, Stage 7 plan §1.4): `execute_demotion` takes the
exact same `session.get(GovernedSymbol, symbol_id, with_for_update=True)`
row lock `symbol_set_service.py`'s set-item writers and
`promotion_requests.submit_promotion_request` already take, so a
concurrent set-item add/remove and a demotion cannot race to produce a
private symbol still referenced by another organization's set. Re-verifies
eligibility *under* that lock (the preview is advisory only) before
touching any revision/page/entry/package row, and additionally locks every
`published` revision for the symbol, every `active` page/entry projection
for those revisions, and every affected package row before mutating any of
them -- so a concurrent read or writer of those rows serializes against
this transaction too.

Transactional write (§13 task 10): every `published` revision for the
governed-symbol UUID becomes `withdrawn`; every `active` page/entry
projection for those revisions becomes `retired` (actor/time/reason
recorded, per WP7.1's schema); each affected `PublicationPack` is retired
only if no `active` projection remains in it afterward (a multi-symbol
pack keeps its unrelated active projections and stays `published`).
`governed_symbol.visibility` flips to `organization_private` in the same
transaction. A pre-commit failure (an exception before the caller commits)
leaves every row unchanged, since nothing here commits on its own.

Known, explicitly flagged gap (not attempted here): §13 task 10's
"purge/invalidate application/CDN caches and short-link projections" refers
to production edge-cache/CDN infrastructure this repository has no
integration with at all (confirmed by an exhaustive grep for
cache/CDN/purge/invalidate/short-link machinery -- there is none to reuse,
and inventing a fake purge call would violate `CLAUDE.md`'s "do not invent
production metrics, workflow states, or backend contracts"). The DB
transaction alone already satisfies the acceptance bar that actually
matters for correctness -- `active_public_symbol_projections` excludes the
demoted symbol the instant this transaction commits, since visibility,
revision lifecycle, and page/entry state all flip together -- but a
previously-cached response at a real edge/CDN layer could still be stale
until that (currently nonexistent) infrastructure is purged. This module
only records a durable, auditable marker
(`governed_symbol.demotion_cache_purge_pending`) that a real purge
integration can later consume and resolve; it does not claim to have
purged anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .models import (
    AuditEvent,
    CatalogFavourite,
    CatalogSymbolIdentifier,
    GovernedSymbol,
    PackEntry,
    PublicationPack,
    PublishedPage,
    SymbolRevision,
    SymbolSet,
    SymbolSetItem,
)
from .contribution_events import reverse_contributions_for_symbol
from .product_usage_events import record_governance_usage_event


class DemotionError(ValueError):
    """Domain validation failure -- the route maps this to HTTP 400."""


class DemotionNotVisible(LookupError):
    """The governed symbol does not exist. The route maps this to 404."""


class DemotionIneligible(RuntimeError):
    """Structural ineligibility (not public, ownerless, or still
    referenced by another organization's Symbol Set). The route maps this
    to HTTP 409."""


class DemotionPreview:
    def __init__(self, *, symbol, eligible, reasons, blocking_organization_ids, favourites_count):
        self.symbol = symbol
        self.eligible = eligible
        self.reasons = reasons
        self.blocking_organization_ids = blocking_organization_ids
        self.favourites_count = favourites_count


class DemotionResult:
    def __init__(self, *, symbol, revision_ids, published_page_ids, pack_entry_ids, retired_pack_ids):
        self.symbol = symbol
        self.revision_ids = revision_ids
        self.published_page_ids = published_page_ids
        self.pack_entry_ids = pack_entry_ids
        self.retired_pack_ids = retired_pack_ids


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _require_platform_admin(current_user: AuthenticatedUser) -> None:
    """Defense-in-depth: the route also gates both actions behind the
    `require_platform_admin` dependency (and, for execution,
    `require_recent_step_up`), but this module must not rely solely on the
    route layer, mirroring every other service module this stage added."""
    if not current_user.is_platform_admin:
        raise DemotionError("Platform Admin privileges are required.")


def _clean_reason(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise DemotionError("A reason is required to demote a public symbol.")
    if len(cleaned) > 2000:
        raise DemotionError("reason must be 2000 characters or fewer.")
    return cleaned


def _blocking_organization_ids(session: Session, symbol: GovernedSymbol) -> list[uuid.UUID]:
    if symbol.owner_organization_id is None:
        return []
    rows = session.execute(
        select(SymbolSet.owner_organization_id)
        .join(SymbolSetItem, SymbolSetItem.symbol_set_id == SymbolSet.id)
        .where(
            SymbolSetItem.governed_symbol_id == symbol.id,
            SymbolSet.owner_organization_id != symbol.owner_organization_id,
        )
        .distinct()
    ).all()
    return [row[0] for row in rows]


def _eligibility(session: Session, symbol: GovernedSymbol) -> tuple[bool, list[str], list[uuid.UUID]]:
    reasons: list[str] = []
    if symbol.visibility != "public":
        reasons.append("The symbol is not currently public.")
    if symbol.owner_organization_id is None:
        reasons.append("Ownerless legacy public symbols cannot be demoted to organization-private.")
    blocking = _blocking_organization_ids(session, symbol) if symbol.owner_organization_id is not None else []
    if blocking:
        reasons.append(
            f"Referenced by {len(blocking)} Symbol Set(s) owned by a different organization; "
            "demotion is unavailable until all such references are removed."
        )
    return (not reasons, reasons, blocking)


def preview_demotion(session: Session, current_user: AuthenticatedUser, *, symbol_id: uuid.UUID) -> DemotionPreview:
    _require_platform_admin(current_user)
    symbol = session.get(GovernedSymbol, symbol_id)
    if symbol is None:
        raise DemotionNotVisible()

    eligible, reasons, blocking = _eligibility(session, symbol)
    favourites_count = session.execute(
        select(func.count()).select_from(CatalogFavourite).where(CatalogFavourite.symbol_id == symbol.id)
    ).scalar_one()

    return DemotionPreview(
        symbol=symbol,
        eligible=eligible,
        reasons=reasons,
        blocking_organization_ids=blocking,
        favourites_count=favourites_count,
    )


def execute_demotion(
    session: Session,
    current_user: AuthenticatedUser,
    *,
    symbol_id: uuid.UUID,
    reason: str,
) -> DemotionResult:
    _require_platform_admin(current_user)
    clean_reason = _clean_reason(reason)
    now = _utc_now()
    actor_id = uuid.UUID(current_user.id)

    # The shared governed-symbol-row lock (Stage 7 plan §1.4) -- the same
    # one symbol_set_service.py's set-item writers and
    # promotion_requests.submit_promotion_request already take.
    symbol = session.get(GovernedSymbol, symbol_id, with_for_update=True)
    if symbol is None:
        raise DemotionNotVisible()

    # Re-query eligibility *under* the lock; the preview is advisory only.
    eligible, reasons, _blocking = _eligibility(session, symbol)
    if not eligible:
        raise DemotionIneligible(" ".join(reasons))

    revisions = (
        session.execute(
            select(SymbolRevision)
            .where(SymbolRevision.symbol_id == symbol.id, SymbolRevision.lifecycle_state == "published")
            .with_for_update()
        )
        .scalars()
        .all()
    )
    revision_ids = [revision.id for revision in revisions]

    pages: list[PublishedPage] = []
    entries: list[PackEntry] = []
    if revision_ids:
        pages = (
            session.execute(
                select(PublishedPage)
                .where(
                    PublishedPage.current_symbol_revision_id.in_(revision_ids),
                    PublishedPage.publication_state == "active",
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        entries = (
            session.execute(
                select(PackEntry)
                .where(
                    PackEntry.symbol_revision_id.in_(revision_ids),
                    PackEntry.publication_state == "active",
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )

    affected_pack_ids = {entry.pack_id for entry in entries} | {page.pack_id for page in pages}
    packs: list[PublicationPack] = []
    if affected_pack_ids:
        packs = (
            session.execute(
                select(PublicationPack).where(PublicationPack.id.in_(affected_pack_ids)).with_for_update()
            )
            .scalars()
            .all()
        )

    for revision in revisions:
        revision.lifecycle_state = "withdrawn"
    for page in pages:
        page.publication_state = "retired"
        page.retired_by = actor_id
        page.retired_at = now
        page.retirement_reason = clean_reason
        page.updated_at = now
    for entry in entries:
        entry.publication_state = "retired"
        entry.retired_by = actor_id
        entry.retired_at = now
        entry.retirement_reason = clean_reason

    # ck_governed_symbols_catalog_symbol_visibility_barrier requires
    # catalog_symbol_id is null OR visibility='public' -- a promoted
    # symbol already carries a canonical catalog_symbol_id (allocated by
    # execute_organization_promotion_handoff via ensure_catalog_symbol_id),
    # so it must be released before the visibility flip. The registry row
    # itself is never deleted (history/identity is retained, per §13's
    # "never delete... revision history" and FR-SYM-011): its role moves
    # canonical -> historical_alias, keeping the governed_symbol_id link,
    # mirroring catalog_symbol_ids.correct_catalog_symbol_id's own
    # preserve_old_link=True convention. Re-promotion later allocates a
    # fresh identifier via the same ensure_catalog_symbol_id() call
    # (catalog_symbol_id is None again at that point).
    if symbol.catalog_symbol_id is not None:
        identifier_row = session.get(CatalogSymbolIdentifier, symbol.catalog_symbol_id, with_for_update=True)
        if identifier_row is not None and identifier_row.role == "canonical" and identifier_row.governed_symbol_id == symbol.id:
            identifier_row.role = "historical_alias"
            identifier_row.changed_at = now
            identifier_row.changed_by = actor_id
            identifier_row.change_reason = f"Demoted to organization-private: {clean_reason}"
        symbol.catalog_symbol_id = None

    symbol.visibility = "organization_private"
    symbol.updated_at = now

    # Flush so the "does any active projection remain in this pack" check
    # below sees this transaction's own retirements, not stale rows.
    session.flush()

    retired_pack_ids: list[uuid.UUID] = []
    for pack in packs:
        remaining_active = session.execute(
            select(func.count())
            .select_from(PackEntry)
            .where(PackEntry.pack_id == pack.id, PackEntry.publication_state == "active")
        ).scalar_one()
        if remaining_active == 0:
            pack.status = "retired"
            pack.updated_at = now
            retired_pack_ids.append(pack.id)

    session.add(
        AuditEvent(
            entity_type="governed_symbol",
            entity_id=symbol.id,
            action="governed_symbol.demoted",
            actor_id=actor_id,
            payload_json={
                "reason": clean_reason,
                "symbolRevisionIds": [str(revision_id) for revision_id in revision_ids],
                "publishedPageIds": [str(page.id) for page in pages],
                "packEntryIds": [str(entry.id) for entry in entries],
                "retiredPackIds": [str(pack_id) for pack_id in retired_pack_ids],
            },
            created_at=now,
        )
    )
    # See this module's docstring: no real CDN/edge-cache integration
    # exists in this repository yet. This marker is a durable, auditable
    # placeholder a future purge integration can consume and resolve --
    # it is not a claim that anything was actually purged.
    session.add(
        AuditEvent(
            entity_type="governed_symbol",
            entity_id=symbol.id,
            action="governed_symbol.demotion_cache_purge_pending",
            actor_id=actor_id,
            payload_json={"note": "No CDN/edge-cache integration is configured in this environment."},
            created_at=now,
        )
    )
    record_governance_usage_event(
        session,
        event_type="public_symbol_demoted",
        user_id=actor_id,
        organization_id=symbol.owner_organization_id,
        governed_symbol_id=symbol.id,
        symbol_source="organization_private",
        occurred_at=now,
    )
    # Stage 9 WP9.5, spec §12.2: "Demotion or invalidation may reverse
    # contribution events through append-only correction records." Does
    # not revoke any already-awarded badge -- see contribution_events.py's
    # own module docstring; a no-op if this symbol never had an active
    # accepted contribution (e.g. a legacy ownerless public symbol).
    reverse_contributions_for_symbol(
        session,
        governed_symbol_id=symbol.id,
        reason=clean_reason,
        occurred_at=now,
    )
    session.flush()

    return DemotionResult(
        symbol=symbol,
        revision_ids=revision_ids,
        published_page_ids=[page.id for page in pages],
        pack_entry_ids=[entry.id for entry in entries],
        retired_pack_ids=retired_pack_ids,
    )
