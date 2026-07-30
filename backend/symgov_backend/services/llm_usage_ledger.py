"""Transaction-safe persistence for the authoritative sanitized LLM usage ledger."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Mapping
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from ..models import LLMUsageEvent
from . import llm_telemetry

logger = logging.getLogger(__name__)

_UUID_FIELDS = frozenset(
    {
        "event_id", "queue_item_id", "agent_run_id", "review_case_id",
        "intake_record_id", "source_package_id", "symbol_id",
    }
)
_COST_FIELDS = frozenset({"provider_reported_cost_usd", "calculated_cost_usd"})
_JSON_FIELDS = frozenset({"other_usage_json", "metadata"})
_SAFE_CONFLICT_MESSAGE = "LLM usage event conflicts with an existing ledger record"
_MODEL_ATTRIBUTES = {"metadata": "metadata_json"}


class LLMUsageConflictError(RuntimeError):
    """A uniqueness collision whose sanitized ledger values disagree."""

    def __init__(self) -> None:
        super().__init__(_SAFE_CONFLICT_MESSAGE)


def _persistence_snapshot(event: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the validator's exact schema and convert database-native values."""
    snapshot: dict[str, Any] = {}
    for field in llm_telemetry._EVENT_KEYS:
        value = event[field]
        if field in _JSON_FIELDS:
            value = deepcopy(value)
        elif field in _UUID_FIELDS:
            value = uuid.UUID(value) if value is not None else None
        elif field in _COST_FIELDS:
            value = Decimal(value) if value is not None else None
        elif field == "occurred_at_utc":
            value = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        snapshot[field] = value
    return snapshot


def _duplicates(session: Any, values: Mapping[str, Any]) -> list[LLMUsageEvent]:
    statement = select(LLMUsageEvent).where(
        or_(
            LLMUsageEvent.event_id == values["event_id"],
            (
                (LLMUsageEvent.trace_id == values["trace_id"])
                & (LLMUsageEvent.observation_id == values["observation_id"])
            ),
        )
    )
    return list(session.execute(statement).scalars().all())


def _semantically_identical(row: LLMUsageEvent, values: Mapping[str, Any]) -> bool:
    return all(
        getattr(row, _MODEL_ATTRIBUTES.get(field, field)) == values[field]
        for field in llm_telemetry._EVENT_KEYS
    )


def _resolve_duplicate(rows: list[LLMUsageEvent], values: Mapping[str, Any]) -> LLMUsageEvent | None:
    if not rows:
        return None
    if len(rows) == 1 and _semantically_identical(rows[0], values):
        return rows[0]
    raise LLMUsageConflictError()


def persist_llm_usage_event(session: Any, event: Mapping[str, Any], trace_seed: str) -> LLMUsageEvent:
    """Validate, insert, and flush one event without committing the caller's transaction."""
    detached_event = deepcopy(dict(event))
    llm_telemetry.validate_event(detached_event, trace_seed=trace_seed)
    values = _persistence_snapshot(detached_event)

    existing = _resolve_duplicate(_duplicates(session, values), values)
    if existing is not None:
        return existing

    model_values = {_MODEL_ATTRIBUTES.get(field, field): value for field, value in values.items()}
    row = LLMUsageEvent(**model_values)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = _resolve_duplicate(_duplicates(session, values), values)
        if existing is not None:
            return existing
        raise LLMUsageConflictError() from None
    return row


def record_llm_usage_event_best_effort(
    session_factory: Any,
    event: Mapping[str, Any],
    trace_seed: str,
) -> bool:
    """Persist at a call boundary in an owned transaction and never fail the caller."""
    session = None
    try:
        session = session_factory()
        persist_llm_usage_event(session, event, trace_seed=trace_seed)
        session.commit()
        return True
    except Exception as exc:
        if session is not None:
            try:
                session.rollback()
            except Exception:
                pass
        logger.error(
            "LLM usage event persistence failed error_type=%s",
            type(exc).__name__,
        )
        return False
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def calculate_period_utc_bounds(period: str, anchor: datetime | None = None) -> tuple[datetime, datetime]:
    """Calculate UTC start and end bounds for period aggregation."""
    from datetime import timedelta
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    elif anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    else:
        anchor = anchor.astimezone(timezone.utc)

    if period == "day":
        start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        end = anchor.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period == "week":
        start_day = anchor.date() - timedelta(days=anchor.weekday())
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
        end = anchor.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period in ("month", "mtd"):
        start = datetime(anchor.year, anchor.month, 1, tzinfo=timezone.utc)
        end = anchor.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError(f"Unsupported period: {period}")

    return start, end


def reconcile_invoice_summary(
    total_effective_cost_usd: float,
    invoice_cost_usd: float,
) -> dict[str, Any]:
    """Reconcile calculated ledger total against an external invoice statement."""
    diff = abs(total_effective_cost_usd - invoice_cost_usd)
    requires_investigation = diff > 5.0
    return {
        "ledgerTotalUsd": round(total_effective_cost_usd, 4),
        "invoiceTotalUsd": round(invoice_cost_usd, 4),
        "differenceUsd": round(diff, 4),
        "requiresInvestigation": requires_investigation,
    }
