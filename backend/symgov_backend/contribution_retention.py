"""Stage 9 WP9.5 -- retention purge for `contribution_events`.

Mirrors `product_usage_retention.purge_expired_product_usage_events`'s own
90-day window (Q7 covers both tables explicitly). Needs no equivalent
"not yet reflected in a rollup" ordering guard: the badge/lifetime-total
side effects a `contribution_events` row can trigger are written
synchronously, in the same transaction as the row itself
(`contribution_events.record_contribution_awarded`/
`reverse_contributions_for_symbol`), so by the time this purge ever runs,
any badge or counter increment a row would produce already exists --
there is no ordering hazard like WP9.4's unscheduled rollup-refresh design
had. A reversal row whose original award row this purge deletes simply
keeps a stale (no longer resolvable) `reversed_event_id` -- deliberately
not a real foreign key, see `ContributionEvent`'s own docstring -- so this
purge needs no special handling for that case either.

Deliberately not wired to any scheduler/cron here, mirroring
`product_usage_retention`'s own precedent: invoking this on a recurring
cadence in a live environment is a deployment/operations decision
requiring its own separate explicit approval. This module is a tested,
callable unit only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import ContributionEvent

CONTRIBUTION_EVENT_RETENTION_DAYS = 90


def purge_expired_contribution_events(session: Session, *, now: datetime | None = None) -> int:
    """Delete `contribution_events` rows older than the retention window.

    Returns the number of rows deleted. The caller is responsible for
    committing the session -- this function only issues the DELETE.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=CONTRIBUTION_EVENT_RETENTION_DAYS)
    result = session.execute(delete(ContributionEvent).where(ContributionEvent.occurred_at < cutoff))
    return result.rowcount or 0
