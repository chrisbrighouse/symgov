"""Keeping the usage ledgers tidy: rollups, retention and expired rate windows.

One pass, in an order that cannot lose counts:

1. Re-aggregate the last few days of raw `symbol_usage_events` into
   `symbol_usage_daily_rollups`, and the same for the Stage 9
   `product_usage_events` into `product_usage_daily_rollups` (which nothing
   refreshed until now, so Organization usage dashboards read stale numbers).
   A day is re-aggregated whole, never partly, so re-running is harmless.
2. Delete raw symbol events older than 60 days, after rolling up exactly the
   days about to go (so a worker that was off for a week loses nothing), and
   Stage 9 events past their 90 days as that module already defines.
3. Delete rate windows of keys that stopped calling more than a day ago.

Rollups carry no user identity, which is why they outlive the raw rows and why
the platform roll-up may read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import uuid

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from .models import CatalogApiRateWindow, SymbolUsageDailyRollup, SymbolUsageEvent
from .product_usage_retention import purge_expired_product_usage_events
from .product_usage_rollups import refresh_product_usage_rollups

SYMBOL_USAGE_RETENTION_DAYS = 60
RECENT_DAYS_REAGGREGATED = 3


@dataclass(frozen=True)
class MaintenanceResult:
    rollup_cells: int = 0
    product_rollup_cells: int = 0
    purged_symbol_events: int = 0
    purged_product_events: int = 0
    purged_rate_windows: int = 0


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def refresh_symbol_usage_rollups(session: Session, *, since_day: date, now: datetime | None = None) -> int:
    """Rebuild the rollup cells for every day from `since_day` on. Returns the cells written."""
    reference = now or datetime.now(timezone.utc)
    start = _midnight(since_day)
    day = func.date(SymbolUsageEvent.occurred_at)
    pack = func.coalesce(SymbolUsageEvent.pack_code, "")
    rows = (
        session.query(
            day, SymbolUsageEvent.event_type, SymbolUsageEvent.channel, SymbolUsageEvent.scope_kind,
            SymbolUsageEvent.organization_id, pack, func.count(),
        )
        .filter(SymbolUsageEvent.occurred_at >= start)
        .group_by(day, SymbolUsageEvent.event_type, SymbolUsageEvent.channel, SymbolUsageEvent.scope_kind, SymbolUsageEvent.organization_id, pack)
        .all()
    )
    session.execute(delete(SymbolUsageDailyRollup).where(SymbolUsageDailyRollup.occurred_on >= since_day))
    for occurred_on, event_type, channel, scope_kind, organization_id, pack_code, count in rows:
        if isinstance(occurred_on, str):
            occurred_on = date.fromisoformat(occurred_on)
        session.add(SymbolUsageDailyRollup(
            id=uuid.uuid4(), occurred_on=occurred_on, event_type=event_type, channel=channel, scope_kind=scope_kind,
            organization_id=organization_id, pack_code=pack_code or "", event_count=count, updated_at=reference,
        ))
    session.flush()
    return len(rows)


def purge_symbol_usage_events(session: Session, *, now: datetime | None = None) -> int:
    """Delete raw events past retention, first rolling up the days that are about to go."""
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=SYMBOL_USAGE_RETENTION_DAYS)
    oldest = session.query(func.min(SymbolUsageEvent.occurred_at)).scalar()
    if oldest is None:
        return 0
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    if oldest >= cutoff:
        return 0
    # Whole days only: every day with a row older than the cutoff is rebuilt up to the cutoff day.
    refresh_symbol_usage_rollups(session, since_day=oldest.date(), now=reference)
    result = session.execute(delete(SymbolUsageEvent).where(SymbolUsageEvent.occurred_at < _midnight(cutoff.date())))
    return result.rowcount or 0


def run_maintenance(session: Session, *, now: datetime | None = None) -> MaintenanceResult:
    """One full pass. The caller commits."""
    reference = now or datetime.now(timezone.utc)
    since_day = (reference - timedelta(days=RECENT_DAYS_REAGGREGATED)).date()
    cells = refresh_symbol_usage_rollups(session, since_day=since_day, now=reference)
    product_cells = refresh_product_usage_rollups(session, since=_midnight(since_day), now=reference)
    purged = purge_symbol_usage_events(session, now=reference)
    purged_product = purge_expired_product_usage_events(session, now=reference)
    windows = session.execute(
        delete(CatalogApiRateWindow).where(CatalogApiRateWindow.window_start < reference - timedelta(days=1))
    ).rowcount or 0
    return MaintenanceResult(cells, product_cells, purged, purged_product, windows)
