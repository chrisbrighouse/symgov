"""Stage 9 WP9.4 -- aggregate rollup refresh and organization usage-summary
read model for `ProductUsageEvent`.

Two responsibilities, matching this stage's own two-tier split between raw
events (WP9.1-9.3) and their indefinitely-retained aggregate (WP9.4):

- `refresh_product_usage_rollups` re-aggregates raw `product_usage_events`
  rows into `product_usage_daily_rollups`, one row per
  (organization, event_type, day). Deliberately not wired to any
  scheduler/cron here (Chris confirmed a periodic-batch design over a
  synchronous-incremental one during WP9.4 planning): invoking this on a
  recurring cadence in a live environment is a deployment/operations
  decision requiring its own separate explicit approval, mirroring
  `product_usage_retention.purge_expired_product_usage_events`'s own
  precedent. A production rollout must run this refresh before that purge
  ever deletes rows it hasn't yet aggregated -- an ordering constraint to
  document at deployment time, not an urgent risk today since neither job
  is scheduled yet.
- `get_organization_usage_summary` reads only the rollup table (never raw
  events) so dashboard history survives the 90-day raw-row purge, and
  enforces the confirmed 3-distinct-user minimum aggregation threshold
  (Stage 9 plan §4 Q7) by suppressing (omitting) any day-cell whose
  `distinct_user_count` falls below it -- an Organization Admin or Platform
  Admin dashboard must never reveal a real behavioral count contributed by
  fewer than `MINIMUM_AGGREGATION_DISTINCT_USERS` real people.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .models import ProductUsageDailyRollup, ProductUsageEvent

MINIMUM_AGGREGATION_DISTINCT_USERS = 3
DEFAULT_USAGE_SUMMARY_WINDOW_DAYS = 30


def refresh_product_usage_rollups(session: Session, *, since: datetime | None = None, now: datetime | None = None) -> int:
    """Re-aggregate raw `product_usage_events` rows into
    `product_usage_daily_rollups`. Only organization-scoped rows
    (`organization_id is not null`) are rolled up -- see
    `ProductUsageDailyRollup`'s own docstring for why. `since` optionally
    bounds which raw rows are re-aggregated (omit it to re-aggregate every
    row currently in the table, which is idempotent and always correct,
    just more work); the caller is responsible for committing the session.
    Returns the number of rollup cells touched (inserted or updated).
    """
    reference = now or datetime.now(timezone.utc)
    query = (
        session.query(
            ProductUsageEvent.organization_id,
            ProductUsageEvent.event_type,
            func.date(ProductUsageEvent.occurred_at).label("occurred_on"),
            func.count().label("event_count"),
            func.count(func.distinct(ProductUsageEvent.user_id)).label("distinct_user_count"),
        )
        .filter(ProductUsageEvent.organization_id.is_not(None))
        .group_by(ProductUsageEvent.organization_id, ProductUsageEvent.event_type, func.date(ProductUsageEvent.occurred_at))
    )
    if since is not None:
        query = query.filter(ProductUsageEvent.occurred_at >= since)

    touched = 0
    for organization_id, event_type, occurred_on, event_count, distinct_user_count in query.all():
        session.execute(
            text(
                "INSERT INTO product_usage_daily_rollups "
                "(id, organization_id, event_type, occurred_on, event_count, distinct_user_count, updated_at) "
                "VALUES (:id, :organization_id, :event_type, :occurred_on, :event_count, :distinct_user_count, :updated_at) "
                "ON CONFLICT (organization_id, event_type, occurred_on) DO UPDATE SET "
                "event_count = EXCLUDED.event_count, "
                "distinct_user_count = EXCLUDED.distinct_user_count, "
                "updated_at = EXCLUDED.updated_at"
            ),
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "event_type": event_type,
                "occurred_on": occurred_on,
                "event_count": event_count,
                "distinct_user_count": distinct_user_count,
                "updated_at": reference,
            },
        )
        touched += 1
    return touched


def get_organization_usage_summary(
    session: Session,
    organization_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
) -> dict:
    """Read-model for an Organization Admin/Platform Admin usage dashboard.

    Returns a dict shaped directly as the API's camelCase JSON response
    (mirroring `project_dict`/`set_dict`'s own convention of building the
    response shape in the service layer). Any day-cell whose
    `distinct_user_count < MINIMUM_AGGREGATION_DISTINCT_USERS` is omitted
    from `days` and counted only in `suppressedDayCount` -- its exact
    event/user counts are never returned, since revealing them would defeat
    the point of the threshold for a cell that thin.
    """
    until = until or datetime.now(timezone.utc).date()
    since = since or (until - timedelta(days=DEFAULT_USAGE_SUMMARY_WINDOW_DAYS - 1))

    rows = (
        session.query(ProductUsageDailyRollup)
        .filter(
            ProductUsageDailyRollup.organization_id == organization_id,
            ProductUsageDailyRollup.occurred_on >= since,
            ProductUsageDailyRollup.occurred_on <= until,
        )
        .order_by(ProductUsageDailyRollup.event_type, ProductUsageDailyRollup.occurred_on)
        .all()
    )

    by_event_type: dict[str, dict] = {}
    for row in rows:
        bucket = by_event_type.setdefault(
            row.event_type, {"days": [], "suppressedDayCount": 0, "totalEventCount": 0}
        )
        if row.distinct_user_count < MINIMUM_AGGREGATION_DISTINCT_USERS:
            bucket["suppressedDayCount"] += 1
            continue
        bucket["days"].append(
            {"date": row.occurred_on, "eventCount": row.event_count, "distinctUserCount": row.distinct_user_count}
        )
        bucket["totalEventCount"] += row.event_count

    return {
        "organizationId": str(organization_id),
        "since": since,
        "until": until,
        "eventTypes": [
            {"eventType": event_type, **data} for event_type, data in sorted(by_event_type.items())
        ],
    }
