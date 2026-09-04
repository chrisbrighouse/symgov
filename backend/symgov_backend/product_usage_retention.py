"""Stage 9 WP9.1 -- retention purge for `product_usage_events`.

Raw `ProductUsageEvent` rows are retained for 90 days (Stage 9 plan §4 Q7,
confirmed with Chris); rolled-up aggregate counts, built by a later work
package (WP9.4), are retained indefinitely and are unaffected by this purge.
This module owns only the raw-row deletion -- it does not compute, read, or
persist any aggregate itself.

Deliberately not wired to any scheduler/cron here: invoking this on a
recurring cadence in a live environment is a deployment/operations decision
requiring its own separate explicit approval, per this repository's
prohibition on live mutations without that approval. This module is a
tested, callable unit only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import ProductUsageEvent

PRODUCT_USAGE_EVENT_RETENTION_DAYS = 90


def purge_expired_product_usage_events(session: Session, *, now: datetime | None = None) -> int:
    """Delete `product_usage_events` rows older than the retention window.

    Returns the number of rows deleted. The caller is responsible for
    committing the session -- this function only issues the DELETE.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=PRODUCT_USAGE_EVENT_RETENTION_DAYS)
    result = session.execute(delete(ProductUsageEvent).where(ProductUsageEvent.occurred_at < cutoff))
    return result.rowcount or 0
