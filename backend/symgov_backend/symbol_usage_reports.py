"""What the symbol usage ledger says, for an organization admin and for the platform.

An organization's page counts only that organization's own activity:
organization-bound sessions and that organization's keys (`scope_kind =
'organization'` and its `organization_id`). A personal key's or personal
session's activity is never counted here, however its user relates to the
organization. Users are named: this is a deliberate departure from the earlier
pseudonymous-analytics rule, on the owner's decision of 2026-10-13.

The platform's roll-up is built from `symbol_usage_daily_rollups` only: totals
by organization and by pack, no user identity, no search text.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import GovernedSymbol, Organization, Project, SymbolSet, SymbolUsageDailyRollup, SymbolUsageEvent, User

MAX_WINDOW_DAYS = 60
DEFAULT_WINDOW_DAYS = 30
TOP_LIMIT = 20

SYMBOL_EVENT_TYPES = ("symbol_viewed", "symbol_downloaded", "symbol_added_to_set", "symbol_added_to_clipboard", "symbol_api_fetched")


def _window(days: int | None, now: datetime | None) -> tuple[datetime, datetime, int]:
    days = max(1, min(int(days or DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
    end = now or datetime.now(timezone.utc)
    start = datetime.combine((end - timedelta(days=days - 1)).date(), datetime.min.time(), tzinfo=timezone.utc)
    return start, end, days


def _counts_by_type(rows) -> dict[str, int]:
    return {event_type: int(count) for event_type, count in rows}


def organization_usage(session: Session, organization_id: uuid.UUID, *, days: int | None = None, now: datetime | None = None) -> dict:
    start, end, days = _window(days, now)
    own = (
        SymbolUsageEvent.organization_id == organization_id,
        SymbolUsageEvent.scope_kind == "organization",
        SymbolUsageEvent.occurred_at >= start,
        SymbolUsageEvent.occurred_at <= end,
    )

    totals = _counts_by_type(
        session.query(SymbolUsageEvent.event_type, func.count()).filter(*own).group_by(SymbolUsageEvent.event_type).all()
    )

    by_symbol: dict[uuid.UUID, dict[str, int]] = {}
    for symbol_id, event_type, count in (
        session.query(SymbolUsageEvent.governed_symbol_id, SymbolUsageEvent.event_type, func.count())
        .filter(*own, SymbolUsageEvent.governed_symbol_id.is_not(None))
        .group_by(SymbolUsageEvent.governed_symbol_id, SymbolUsageEvent.event_type)
        .all()
    ):
        by_symbol.setdefault(symbol_id, {})[event_type] = int(count)
    ranked = sorted(by_symbol.items(), key=lambda item: (-sum(item[1].values()), str(item[0])))[:TOP_LIMIT]
    names = {
        row.id: row
        for row in session.query(GovernedSymbol).filter(GovernedSymbol.id.in_([symbol_id for symbol_id, _ in ranked] or [None])).all()
    }
    top_symbols = [
        {
            "symbolId": str(symbol_id),
            "displayId": (names[symbol_id].catalog_symbol_id or names[symbol_id].slug) if symbol_id in names else None,
            "name": names[symbol_id].canonical_name if symbol_id in names else None,
            "total": sum(counts.values()),
            "counts": counts,
        }
        for symbol_id, counts in ranked
    ]

    by_user: dict[uuid.UUID, dict[str, int]] = {}
    for user_id, event_type, count in (
        session.query(SymbolUsageEvent.user_id, SymbolUsageEvent.event_type, func.count())
        .filter(*own, SymbolUsageEvent.user_id.is_not(None))
        .group_by(SymbolUsageEvent.user_id, SymbolUsageEvent.event_type)
        .all()
    ):
        by_user.setdefault(user_id, {})[event_type] = int(count)
    people = {row.id: row for row in session.query(User).filter(User.id.in_(list(by_user) or [None])).all()}
    users = sorted(
        (
            {
                "userId": str(user_id),
                "displayName": people[user_id].display_name if user_id in people else None,
                "email": people[user_id].email if user_id in people else None,
                "total": sum(counts.values()),
                "counts": counts,
            }
            for user_id, counts in by_user.items()
        ),
        key=lambda item: (-item["total"], item["displayName"] or ""),
    )

    zero_results = [
        {"query": query, "searches": int(count)}
        for query, count in (
            session.query(SymbolUsageEvent.query_text, func.count())
            .filter(*own, SymbolUsageEvent.event_type == "catalog_searched", SymbolUsageEvent.result_count == 0, SymbolUsageEvent.query_text.is_not(None))
            .group_by(SymbolUsageEvent.query_text)
            .order_by(func.count().desc(), SymbolUsageEvent.query_text)
            .limit(TOP_LIMIT)
            .all()
        )
    ]

    project_rows = (
        session.query(SymbolUsageEvent.project_id, func.count())
        .filter(*own, SymbolUsageEvent.project_id.is_not(None))
        .group_by(SymbolUsageEvent.project_id)
        .all()
    )
    project_names = {row.id: row for row in session.query(Project).filter(Project.id.in_([pid for pid, _ in project_rows] or [None])).all()}
    set_rows = (
        session.query(SymbolUsageEvent.symbol_set_id, func.count())
        .filter(*own, SymbolUsageEvent.symbol_set_id.is_not(None))
        .group_by(SymbolUsageEvent.symbol_set_id)
        .all()
    )
    set_names = {row.id: row for row in session.query(SymbolSet).filter(SymbolSet.id.in_([sid for sid, _ in set_rows] or [None])).all()}

    channels = {
        channel: int(count)
        for channel, count in session.query(SymbolUsageEvent.channel, func.count()).filter(*own).group_by(SymbolUsageEvent.channel).all()
    }

    # The trend reads the rollups, so it still has days the raw rows no longer do.
    trend_rows = (
        session.query(SymbolUsageDailyRollup.occurred_on, SymbolUsageDailyRollup.event_type, func.sum(SymbolUsageDailyRollup.event_count))
        .filter(
            SymbolUsageDailyRollup.organization_id == organization_id,
            SymbolUsageDailyRollup.occurred_on >= start.date(),
            SymbolUsageDailyRollup.occurred_on <= end.date(),
        )
        .group_by(SymbolUsageDailyRollup.occurred_on, SymbolUsageDailyRollup.event_type)
        .order_by(SymbolUsageDailyRollup.occurred_on)
        .all()
    )
    trend: dict[str, dict[str, int]] = {}
    for occurred_on, event_type, count in trend_rows:
        trend.setdefault(str(occurred_on), {})[event_type] = int(count)

    return {
        "organizationId": str(organization_id),
        "since": start.date().isoformat(),
        "until": end.date().isoformat(),
        "days": days,
        "totals": totals,
        "channels": channels,
        "topSymbols": top_symbols,
        "users": users,
        "zeroResultSearches": zero_results,
        "projects": [
            {"projectId": str(pid), "code": project_names[pid].code if pid in project_names else None,
             "name": project_names[pid].name if pid in project_names else None, "events": int(count)}
            for pid, count in sorted(project_rows, key=lambda item: -item[1])
        ],
        "symbolSets": [
            {"symbolSetId": str(sid), "code": set_names[sid].code if sid in set_names else None,
             "name": set_names[sid].name if set_names.get(sid) else None, "events": int(count)}
            for sid, count in sorted(set_rows, key=lambda item: -item[1])
        ],
        "trend": [{"date": day, "counts": counts} for day, counts in sorted(trend.items())],
    }


def platform_usage_by_organization(session: Session, *, days: int | None = None, now: datetime | None = None) -> dict:
    """Totals per organization, and one line for all personal activity. No users, no search text."""
    start, end, days = _window(days, now)
    rows = (
        session.query(SymbolUsageDailyRollup.organization_id, SymbolUsageDailyRollup.event_type, SymbolUsageDailyRollup.channel, func.sum(SymbolUsageDailyRollup.event_count))
        .filter(SymbolUsageDailyRollup.occurred_on >= start.date(), SymbolUsageDailyRollup.occurred_on <= end.date())
        .group_by(SymbolUsageDailyRollup.organization_id, SymbolUsageDailyRollup.event_type, SymbolUsageDailyRollup.channel)
        .all()
    )
    grouped: dict[uuid.UUID | None, dict] = {}
    for organization_id, event_type, channel, count in rows:
        entry = grouped.setdefault(organization_id, {"counts": {}, "channels": {}, "total": 0})
        entry["counts"][event_type] = entry["counts"].get(event_type, 0) + int(count)
        entry["channels"][channel] = entry["channels"].get(channel, 0) + int(count)
        entry["total"] += int(count)
    orgs = {row.id: row for row in session.query(Organization).filter(Organization.id.in_([key for key in grouped if key] or [None])).all()}
    items = [
        {
            "organizationId": str(key) if key else None,
            "code": orgs[key].code if key in orgs else None,
            "displayName": orgs[key].display_name if key in orgs else ("Personal (no organization)" if key is None else None),
            **value,
        }
        for key, value in grouped.items()
    ]
    items.sort(key=lambda item: (-item["total"], item["displayName"] or ""))
    return {"since": start.date().isoformat(), "until": end.date().isoformat(), "days": days, "items": items}


def platform_usage_by_pack(session: Session, *, days: int | None = None, now: datetime | None = None) -> dict:
    start, end, days = _window(days, now)
    rows = (
        session.query(SymbolUsageDailyRollup.pack_code, SymbolUsageDailyRollup.event_type, func.sum(SymbolUsageDailyRollup.event_count))
        .filter(SymbolUsageDailyRollup.occurred_on >= start.date(), SymbolUsageDailyRollup.occurred_on <= end.date(), SymbolUsageDailyRollup.pack_code != "")
        .group_by(SymbolUsageDailyRollup.pack_code, SymbolUsageDailyRollup.event_type)
        .all()
    )
    grouped: dict[str, dict] = {}
    for pack_code, event_type, count in rows:
        entry = grouped.setdefault(pack_code, {"packCode": pack_code, "counts": {}, "total": 0})
        entry["counts"][event_type] = int(count)
        entry["total"] += int(count)
    return {
        "since": start.date().isoformat(), "until": end.date().isoformat(), "days": days,
        "items": sorted(grouped.values(), key=lambda item: (-item["total"], item["packCode"])),
    }
