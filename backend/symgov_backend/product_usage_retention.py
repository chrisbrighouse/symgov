"""Stage 9 WP9.1/WP9.4 -- retention purge for `product_usage_events`.

Raw `ProductUsageEvent` rows are retained for 90 days (Stage 9 plan §4 Q7,
confirmed with Chris); rolled-up aggregate counts in
`product_usage_daily_rollups` (WP9.4) are retained indefinitely and are
unaffected by this purge. This module owns only the raw-row deletion -- it
does not compute or persist any aggregate itself.

`refresh_product_usage_rollups` (WP9.4) is deliberately not wired to any
scheduler either, so a production rollout must run it before this purge --
otherwise this purge would delete raw rows a rollup cell never captured.
Rather than rely purely on documented operational ordering, this purge
additionally refuses in code to delete any organization-scoped row whose
own (organization, event_type, day) rollup cell does not yet exist --
`personal`-mode rows (`organization_id is null`) have no rollup to protect
(WP9.4 only rolls up organization-scoped activity) and purge on schedule as
before. This does not remove the ordering requirement -- refresh still must
run regularly enough that cells exist by the time rows age out -- but it
turns a silent, unrecoverable data-loss failure mode into rows that simply
stay past 90 days until refreshed, which is the safer failure direction.

Deliberately not wired to any scheduler/cron here: invoking this on a
recurring cadence in a live environment is a deployment/operations decision
requiring its own separate explicit approval, per this repository's
prohibition on live mutations without that approval. This module is a
tested, callable unit only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, or_
from sqlalchemy.orm import Session

from .models import ProductUsageDailyRollup, ProductUsageEvent

PRODUCT_USAGE_EVENT_RETENTION_DAYS = 90


def purge_expired_product_usage_events(session: Session, *, now: datetime | None = None) -> int:
    """Delete `product_usage_events` rows older than the retention window,
    except organization-scoped rows not yet reflected in a
    `product_usage_daily_rollups` cell (see module docstring).

    Returns the number of rows deleted. The caller is responsible for
    committing the session -- this function only issues the DELETE.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=PRODUCT_USAGE_EVENT_RETENTION_DAYS)
    rolled_up = exists().where(
        ProductUsageDailyRollup.organization_id == ProductUsageEvent.organization_id,
        ProductUsageDailyRollup.event_type == ProductUsageEvent.event_type,
        ProductUsageDailyRollup.occurred_on == func.date(ProductUsageEvent.occurred_at),
    )
    result = session.execute(
        delete(ProductUsageEvent).where(
            ProductUsageEvent.occurred_at < cutoff,
            or_(ProductUsageEvent.organization_id.is_(None), rolled_up),
        )
    )
    return result.rowcount or 0
