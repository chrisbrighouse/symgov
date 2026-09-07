"""Transaction-safe persistence for the authoritative sanitized LLM usage ledger."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Mapping
import uuid

from sqlalchemy import case, func, or_, select
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
_MAX_BREAKDOWN_ROWS = 100


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
    """Calculate half-open UTC bounds for period aggregation."""
    from datetime import timedelta
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    elif anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    else:
        anchor = anchor.astimezone(timezone.utc)

    if period == "day":
        start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "week":
        start_day = anchor.date() - timedelta(days=anchor.weekday())
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    elif period == "month":
        start = datetime(anchor.year, anchor.month, 1, tzinfo=timezone.utc)
        if anchor.month == 12:
            next_month = datetime(anchor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(anchor.year, anchor.month + 1, 1, tzinfo=timezone.utc)
        end = next_month
    elif period == "mtd":
        start = datetime(anchor.year, anchor.month, 1, tzinfo=timezone.utc)
        end = anchor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        raise ValueError(f"Unsupported period: {period}")

    return start, end


def _numeric(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _breakdown_rows(rows: list[Mapping[str, Any]], *, null_label: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {key: _numeric(value) for key, value in dict(row).items()}
        if null_label is not None and item.get("label") is None:
            item["label"] = null_label
        result.append(item)
    return sorted(result, key=lambda item: (-int(item.get("attempts") or 0), str(item.get("label") or item.get("provider") or ""), str(item.get("model") or "")))


def aggregate_llm_usage(
    session: Any,
    start: datetime,
    end: datetime,
    *,
    environment: str,
) -> dict[str, Any]:
    """Return bounded SQL aggregates for the authoritative ledger."""
    row = LLMUsageEvent
    bounded = (
        row.occurred_at_utc >= start,
        row.occurred_at_utc < end,
        row.environment == environment,
    )
    successful = case((row.status == "succeeded", 1), else_=0)
    failed = case((row.status != "succeeded", 1), else_=0)
    unknown_cost = case((row.provider_reported_cost_usd.is_(None) & row.calculated_cost_usd.is_(None), 1), else_=0)
    summary_statement = select(
        func.count().label("attempts"), func.sum(successful).label("successful"),
        func.sum(failed).label("failed"), func.coalesce(func.sum(row.latency_ms), 0).label("latency_ms"),
        func.coalesce(func.sum(row.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(row.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(row.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(row.cache_write_input_tokens), 0).label("cache_write_input_tokens"),
        func.coalesce(func.sum(row.reasoning_tokens), 0).label("reasoning_tokens"),
        func.sum(row.provider_reported_cost_usd).label("provider_reported_cost_usd"),
        func.sum(row.calculated_cost_usd).label("calculated_cost_usd"),
        func.sum(func.coalesce(row.provider_reported_cost_usd, row.calculated_cost_usd)).label("effective_cost_usd"),
        func.sum(unknown_cost).label("unknown_cost_attempts"),
        func.sum(case((row.input_tokens.is_(None), 1), else_=0)).label("unknown_input_token_attempts"),
        func.sum(case((row.output_tokens.is_(None), 1), else_=0)).label("unknown_output_token_attempts"),
        func.sum(case((row.attempt_number > 1, 1), else_=0)).label("retry_attempts"),
    ).where(*bounded)
    summary = dict(session.execute(summary_statement).mappings().one())

    provider_statement = select(
        row.provider.label("provider"), row.resolved_model.label("model"), func.count().label("attempts"),
        func.sum(successful).label("successful"), func.sum(failed).label("failed"),
        func.coalesce(func.sum(row.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(row.output_tokens), 0).label("output_tokens"),
        func.sum(func.coalesce(row.provider_reported_cost_usd, row.calculated_cost_usd)).label("effective_cost_usd"),
    ).where(*bounded).group_by(row.provider, row.resolved_model).order_by(
        func.count().desc(), row.provider.asc(), row.resolved_model.asc()
    ).limit(_MAX_BREAKDOWN_ROWS + 1)

    def grouped(column: Any):
        return select(column.label("label"), func.count().label("attempts")).where(*bounded).group_by(
            column
        ).order_by(func.count().desc(), column.asc()).limit(_MAX_BREAKDOWN_ROWS + 1)

    provider_rows = list(session.execute(provider_statement).mappings().all())
    use_case_rows = list(session.execute(grouped(row.use_case)).mappings().all())
    agent_rows = list(session.execute(grouped(row.agent_slug)).mappings().all())
    status_rows = list(session.execute(grouped(row.status)).mappings().all())
    truncated_breakdowns = []
    bounded_rows = []
    for label, rows in (
        ("provider/model", provider_rows),
        ("use case", use_case_rows),
        ("agent", agent_rows),
        ("status", status_rows),
    ):
        if len(rows) > _MAX_BREAKDOWN_ROWS:
            truncated_breakdowns.append(label)
        bounded_rows.append(rows[:_MAX_BREAKDOWN_ROWS])
    provider_rows, use_case_rows, agent_rows, status_rows = bounded_rows
    totals = {
        "attempts": int(summary.get("attempts") or 0),
        "successful": int(summary.get("successful") or 0),
        "failed": int(summary.get("failed") or 0),
        "latencyMs": int(summary.get("latency_ms") or 0),
        "inputTokens": int(summary.get("input_tokens") or 0),
        "outputTokens": int(summary.get("output_tokens") or 0),
        "cachedInputTokens": int(summary.get("cached_input_tokens") or 0),
        "cacheWriteInputTokens": int(summary.get("cache_write_input_tokens") or 0),
        "reasoningTokens": int(summary.get("reasoning_tokens") or 0),
        "effectiveCostUsd": _numeric(summary.get("effective_cost_usd")),
        "providerReportedCostUsd": _numeric(summary.get("provider_reported_cost_usd")),
        "calculatedCostUsd": _numeric(summary.get("calculated_cost_usd")),
        "unknownCostAttempts": int(summary.get("unknown_cost_attempts") or 0),
        "unknownInputTokenAttempts": int(summary.get("unknown_input_token_attempts") or 0),
        "unknownOutputTokenAttempts": int(summary.get("unknown_output_token_attempts") or 0),
        "retryAttempts": int(summary.get("retry_attempts") or 0),
    }
    warnings = []
    if summary.get("unknown_input_token_attempts") or summary.get("unknown_output_token_attempts"):
        warnings.append("Some attempts have unknown token values; displayed token totals include known values only.")
    if totals["unknownCostAttempts"]:
        warnings.append("Some attempts have unknown cost; effective cost includes known values only.")
    if truncated_breakdowns:
        warnings.append(
            "Breakdown rows were truncated to the safe limit for: "
            + ", ".join(truncated_breakdowns)
            + "."
        )
    return {
        "totals": totals,
        "breakdowns": {
            "byProviderModel": _breakdown_rows(provider_rows),
            "byUseCase": _breakdown_rows(use_case_rows),
            "byAgent": _breakdown_rows(agent_rows, null_label="unassigned"),
            "byStatus": _breakdown_rows(status_rows),
        },
        "warnings": warnings,
    }


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
