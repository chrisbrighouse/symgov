"""Operator withdrawal of public symbols from the Catalog. Never runs migrations.

For a symbol that must leave the Catalog when there is no organization to
demote it to: a legacy ownerless public symbol, for example one published
without the licence terms its source requires. `symbol_demotion` refuses those,
because demotion hands the symbol back to its owning organization.

For each named symbol (`--symbol S-71` or `--symbol <slug>`, repeatable):

* every revision that is `published`, or still on its way there (`draft`,
  `review`, `approved`), becomes `withdrawn`, so it cannot be published again
  without a new revision;
* every `active` published page and pack entry for those revisions is
  `retired`, with actor, time and reason, and a pack left with no active
  entry is retired too;
* one `governed_symbol.withdrawn` audit event records the previous state of
  every row, which is the restore record.

The symbol row, its catalog identifier and its revision history are kept. A
withdrawn symbol's identifier resolves to nothing; the embedding worker drops
its vector on its next pass.

Dry-run is the default: it reports what would change and writes nothing. Pass
`--apply` with `--actor-id` and `--reason` to commit. Like the other operator
CLIs this is a trusted interface, not an API endpoint, and it owns its
transaction, so a failure leaves every row as it was.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import uuid

from sqlalchemy import create_engine, func, or_, select, text
from sqlalchemy.orm import Session

from .contribution_events import reverse_contributions_for_symbol
from .models import AuditEvent, GovernedSymbol, PackEntry, PublicationPack, PublishedPage, SymbolRevision

WITHDRAWABLE_STATES = ("draft", "review", "approved", "published")
WITHDRAWN_STATE = "withdrawn"
AUDIT_ACTION = "governed_symbol.withdrawn"


class ConfigurationError(ValueError):
    """A missing setting, safe to name. Never carries a credential value."""


class SymbolNotFound(LookupError):
    """A named symbol is not in the database. Nothing is changed."""


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    return create_engine(url, hide_parameters=True)


def plan_withdrawal(session: Session, references: list[str]) -> list[dict]:
    """What withdrawing each symbol touches, under row locks. Raises before any write if one is missing."""
    plans, missing = [], []
    for reference in references:
        symbol = session.execute(
            select(GovernedSymbol)
            .where(or_(GovernedSymbol.catalog_symbol_id == reference, GovernedSymbol.slug == reference))
            .with_for_update()
        ).scalar_one_or_none()
        if symbol is None:
            missing.append(reference)
            continue
        revisions = session.execute(
            select(SymbolRevision)
            .where(SymbolRevision.symbol_id == symbol.id, SymbolRevision.lifecycle_state.in_(WITHDRAWABLE_STATES))
            .order_by(SymbolRevision.created_at)
            .with_for_update()
        ).scalars().all()
        revision_ids = [revision.id for revision in revisions]
        pages = entries = []
        if revision_ids:
            pages = session.execute(
                select(PublishedPage)
                .where(PublishedPage.current_symbol_revision_id.in_(revision_ids), PublishedPage.publication_state == "active")
                .with_for_update()
            ).scalars().all()
            entries = session.execute(
                select(PackEntry)
                .where(PackEntry.symbol_revision_id.in_(revision_ids), PackEntry.publication_state == "active")
                .with_for_update()
            ).scalars().all()
        pack_ids = {page.pack_id for page in pages} | {entry.pack_id for entry in entries}
        packs = session.execute(
            select(PublicationPack).where(PublicationPack.id.in_(pack_ids)).with_for_update()
        ).scalars().all() if pack_ids else []
        plans.append({"symbol": symbol, "revisions": revisions, "pages": pages, "entries": entries, "packs": packs})
    if missing:
        raise SymbolNotFound(", ".join(missing))
    return plans


def apply_withdrawal(session: Session, plans: list[dict], *, actor_id: uuid.UUID, reason: str, now: datetime) -> None:
    for plan in plans:
        symbol = plan["symbol"]
        previous = {
            "revisions": {str(revision.id): revision.lifecycle_state for revision in plan["revisions"]},
            "publishedPageIds": [str(page.id) for page in plan["pages"]],
            "packEntryIds": [str(entry.id) for entry in plan["entries"]],
        }
        for revision in plan["revisions"]:
            revision.lifecycle_state = WITHDRAWN_STATE
        for row in (*plan["pages"], *plan["entries"]):
            row.publication_state = "retired"
            row.retired_by = actor_id
            row.retired_at = now
            row.retirement_reason = reason
        for page in plan["pages"]:
            page.updated_at = now
        symbol.updated_at = now
        session.flush()
        retired_packs = []
        for pack in plan["packs"]:
            remaining = session.execute(
                select(func.count()).select_from(PackEntry)
                .where(PackEntry.pack_id == pack.id, PackEntry.publication_state == "active")
            ).scalar_one()
            if remaining == 0 and pack.status != "retired":
                retired_packs.append({"id": str(pack.id), "previousStatus": pack.status})
                pack.status = "retired"
                pack.updated_at = now
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                entity_type="governed_symbol",
                entity_id=symbol.id,
                action=AUDIT_ACTION,
                actor_id=actor_id,
                payload_json={
                    "reason": reason,
                    "catalogSymbolId": symbol.catalog_symbol_id,
                    "slug": symbol.slug,
                    "previous": previous,
                    "retiredPacks": retired_packs,
                },
                created_at=now,
            )
        )
        # A no-op unless the symbol carries an accepted contribution, as for demotion.
        reverse_contributions_for_symbol(session, governed_symbol_id=symbol.id, reason=reason, occurred_at=now)
    session.flush()
    session.execute(text("SELECT refresh_published_symbol_views()"))


def _summary(plans: list[dict], mode: str) -> dict:
    return {
        "mode": mode,
        "symbols": [
            {
                "catalogSymbolId": plan["symbol"].catalog_symbol_id,
                "slug": plan["symbol"].slug,
                "name": plan["symbol"].canonical_name,
                "visibility": plan["symbol"].visibility,
                "revisions": {revision.revision_label: revision.lifecycle_state for revision in plan["revisions"]},
                "activePages": len(plan["pages"]),
                "activePackEntries": len(plan["entries"]),
                "packs": [pack.pack_code for pack in plan["packs"]],
            }
            for plan in plans
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", action="append", required=True, help="Catalog ID (S-71) or slug; repeatable")
    parser.add_argument("--apply", action="store_true", help="Commit; omit for a dry run")
    parser.add_argument("--actor-id", type=uuid.UUID, help="User id recorded on the retirements and audit event (required with --apply)")
    parser.add_argument("--reason", help="Why the symbols leave the Catalog (required with --apply)")
    args = parser.parse_args(argv)
    if args.apply and not (args.actor_id and args.reason and args.reason.strip()):
        parser.error("--apply requires --actor-id and --reason")

    try:
        engine = _engine()
        try:
            with Session(engine) as session, session.begin():
                plans = plan_withdrawal(session, args.symbol)
                # Summarise before applying: apply moves the states.
                summary = _summary(plans, "apply" if args.apply else "dry-run")
                if args.apply:
                    apply_withdrawal(
                        session, plans, actor_id=args.actor_id, reason=args.reason.strip(),
                        now=datetime.now(timezone.utc).replace(microsecond=0),
                    )
                else:
                    session.rollback()
            print(json.dumps(summary, indent=2))
            return 0
        finally:
            engine.dispose()
    except (ConfigurationError, SymbolNotFound) as exc:
        label = "symbol not found: " if isinstance(exc, SymbolNotFound) else ""
        print(f"Symbol withdrawal failed; nothing was changed: {label}{exc}.", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print("Symbol withdrawal failed; nothing was changed. Check the actor id and database readiness.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
