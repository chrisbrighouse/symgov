"""Emission of `SymbolUsageEvent` rows (WP-B).

Recording is best effort, as for the other usage ledgers: a bug here must never
break the request it rides on. Two entry points share one writer:

- `record_symbol_usage` adds the row inside the caller's transaction, under a
  savepoint, so a failure to write it cannot poison the caller's own work. The
  caller's commit persists it.
- `record_symbol_usage_best_effort` is for requests that have already done
  their work: it writes, commits, and swallows any failure.

Whose activity a row is follows from who did it. A signed-in session in an
organization, or one of that organization's keys, is the organization's. A
personal session or personal key is personal, whatever organization its user
belongs to elsewhere. Search text is stored only on an organization's rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy.orm import Session

from .catalog_usage import sanitize_usage_text
from .models import SymbolUsageEvent

EVENT_TYPES = frozenset({
    "symbol_viewed", "symbol_downloaded", "symbol_added_to_set",
    "symbol_added_to_clipboard", "symbol_api_fetched", "catalog_searched",
})
MAX_QUERY_TEXT = 200


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value  # type: ignore[return-value]
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def build_symbol_usage_event(
    *,
    event_type: str,
    channel: str,
    user_id: object = None,
    organization_id: object = None,
    api_key_id: object = None,
    key_kind: str | None = None,
    project_id: object = None,
    symbol_set_id: object = None,
    governed_symbol_id: object = None,
    symbol_revision_id: object = None,
    pack_code: str | None = None,
    format: str | None = None,
    query_text: object = None,
    result_count: int | None = None,
    occurred_at: datetime | None = None,
) -> SymbolUsageEvent:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown symbol usage event type: {event_type}")
    organization = _uuid_or_none(organization_id)
    if key_kind == "personal":
        organization = None
    scope_kind = "organization" if organization is not None else "personal"
    text = sanitize_usage_text(query_text, max_length=MAX_QUERY_TEXT) if scope_kind == "organization" else None
    return SymbolUsageEvent(
        id=uuid.uuid4(),
        occurred_at=occurred_at or datetime.now(timezone.utc),
        event_type=event_type,
        channel=channel,
        scope_kind=scope_kind,
        user_id=_uuid_or_none(user_id),
        organization_id=organization,
        api_key_id=_uuid_or_none(api_key_id),
        key_kind=key_kind,
        project_id=_uuid_or_none(project_id),
        symbol_set_id=_uuid_or_none(symbol_set_id),
        governed_symbol_id=_uuid_or_none(governed_symbol_id),
        symbol_revision_id=_uuid_or_none(symbol_revision_id),
        pack_code=pack_code,
        format=format,
        query_text=text[:MAX_QUERY_TEXT] if text else None,
        result_count=result_count,
    )


def user_activity(current_user: object) -> dict:
    """The who-and-where of a signed-in session, as the event's keyword arguments."""
    in_organization = getattr(current_user, "session_mode", None) == "organization" and getattr(current_user, "active_organization_id", None)
    return {
        "channel": "web",
        "user_id": getattr(current_user, "id", None),
        "organization_id": current_user.active_organization_id if in_organization else None,
    }


def key_activity(context: object) -> dict:
    """The who-and-where of an API key request."""
    return {
        "channel": "api",
        "user_id": getattr(context, "holder_user_id", None),
        "organization_id": getattr(context, "organization_id", None),
        "api_key_id": getattr(context, "api_key_id", None),
        "key_kind": getattr(context, "key_kind", None),
    }


def _recently_recorded(session: Session, event: SymbolUsageEvent, window_seconds: int) -> bool:
    query = session.query(SymbolUsageEvent.id).filter(
        SymbolUsageEvent.event_type == event.event_type,
        SymbolUsageEvent.channel == event.channel,
        SymbolUsageEvent.occurred_at >= event.occurred_at - timedelta(seconds=window_seconds),
        SymbolUsageEvent.user_id.is_(None) if event.user_id is None else SymbolUsageEvent.user_id == event.user_id,
        SymbolUsageEvent.governed_symbol_id.is_(None) if event.governed_symbol_id is None else SymbolUsageEvent.governed_symbol_id == event.governed_symbol_id,
    )
    if event.query_text is not None:
        query = query.filter(SymbolUsageEvent.query_text == event.query_text)
    if event.api_key_id is not None:
        query = query.filter(SymbolUsageEvent.api_key_id == event.api_key_id)
    return query.first() is not None


def record_symbol_usage(session: Session, *, dedupe_seconds: int = 0, **fields: object) -> bool:
    """Add one event under a savepoint. True if a row was added; never raises."""
    try:
        event = build_symbol_usage_event(**fields)  # type: ignore[arg-type]
        with session.begin_nested():
            if dedupe_seconds and _recently_recorded(session, event, dedupe_seconds):
                return False
            session.add(event)
            session.flush()
        return True
    except Exception:
        return False


def record_symbol_usage_best_effort(session: Session, **fields: object) -> bool:
    added = record_symbol_usage(session, **fields)
    try:
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return False
    return added


def resolve_symbol_refs(session: Session, refs: list[str]) -> dict[str, uuid.UUID]:
    """Governed symbol ids for human-readable refs (Catalog ID or slug), for the refs that exist."""
    from .models import GovernedSymbol

    wanted = {str(ref) for ref in refs if ref}
    if not wanted:
        return {}
    found: dict[str, uuid.UUID] = {}
    for symbol_id, catalog_id, slug in session.query(
        GovernedSymbol.id, GovernedSymbol.catalog_symbol_id, GovernedSymbol.slug
    ).filter((GovernedSymbol.catalog_symbol_id.in_(wanted)) | (GovernedSymbol.slug.in_(wanted))).all():
        for ref in (catalog_id, slug):
            if ref in wanted:
                found.setdefault(ref, symbol_id)
    for ref in wanted - set(found):
        parsed = _uuid_or_none(ref)
        if parsed is not None:
            found[ref] = parsed
    return found
