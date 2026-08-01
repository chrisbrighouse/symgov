from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from symgov_backend.services.llm_usage_ledger import (
    aggregate_llm_usage,
    calculate_period_utc_bounds,
    reconcile_invoice_summary,
)


def test_calculate_period_utc_bounds_day():
    anchor = datetime(2026, 7, 30, 15, 30, 0, tzinfo=timezone.utc)
    start, end = calculate_period_utc_bounds("day", anchor=anchor)
    assert start == datetime(2026, 7, 30, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 31, 0, 0, 0, tzinfo=timezone.utc)


def test_calculate_period_utc_bounds_mtd():
    anchor = datetime(2026, 7, 30, 15, 30, 0, tzinfo=timezone.utc)
    start, end = calculate_period_utc_bounds("mtd", anchor=anchor)
    assert start == datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 31, 0, 0, 0, tzinfo=timezone.utc)


def test_calendar_periods_use_complete_utc_week_and_month():
    anchor = datetime(2026, 7, 30, 23, 30, tzinfo=timezone.utc)

    week_start, week_end = calculate_period_utc_bounds("week", anchor=anchor)
    month_start, month_end = calculate_period_utc_bounds("month", anchor=anchor)

    assert week_start == datetime(2026, 7, 27, tzinfo=timezone.utc)
    assert week_end == datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert month_start == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert month_end == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_aggregate_usage_preserves_unknowns_retries_and_deterministic_breakdowns():
    summary = {
        "attempts": 3, "successful": 1, "failed": 2, "latency_ms": 60,
        "input_tokens": 12, "output_tokens": 7, "cached_input_tokens": 2,
        "cache_write_input_tokens": 1, "reasoning_tokens": 3,
        "provider_reported_cost_usd": Decimal("1.25"),
        "calculated_cost_usd": Decimal("0.50"),
        "effective_cost_usd": Decimal("1.75"), "unknown_cost_attempts": 1,
        "unknown_input_token_attempts": 1, "unknown_output_token_attempts": 1,
        "retry_attempts": 2,
    }
    provider_rows = [
        {"provider": "google", "model": "gemini", "attempts": 1, "successful": 1, "failed": 0,
         "input_tokens": 5, "output_tokens": 2, "effective_cost_usd": Decimal("0.50")},
        {"provider": "openrouter", "model": "alpha", "attempts": 2, "successful": 0, "failed": 2,
         "input_tokens": 7, "output_tokens": 5, "effective_cost_usd": Decimal("1.25")},
    ]
    session = MagicMock()
    results = [summary, provider_rows, [{"label": "workspace_chat", "attempts": 3}],
               [{"label": None, "attempts": 3}], [{"label": "failed", "attempts": 2}, {"label": "succeeded", "attempts": 1}]]
    session.execute.side_effect = [MagicMock(mappings=MagicMock(return_value=MagicMock(one=MagicMock(return_value=results[0]))))] + [
        MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))) for rows in results[1:]
    ]
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

    report = aggregate_llm_usage(session, start, end, environment="production")

    assert report["totals"] == {
        "attempts": 3, "successful": 1, "failed": 2, "latencyMs": 60,
        "inputTokens": 12, "outputTokens": 7, "cachedInputTokens": 2,
        "cacheWriteInputTokens": 1, "reasoningTokens": 3,
        "effectiveCostUsd": 1.75, "providerReportedCostUsd": 1.25,
        "calculatedCostUsd": 0.5, "unknownCostAttempts": 1,
        "unknownInputTokenAttempts": 1, "unknownOutputTokenAttempts": 1,
        "retryAttempts": 2,
    }
    assert [row["provider"] for row in report["breakdowns"]["byProviderModel"]] == ["openrouter", "google"]
    assert report["breakdowns"]["byAgent"] == [{"label": "unassigned", "attempts": 3}]
    assert report["breakdowns"]["byStatus"][0]["label"] == "failed"
    assert any("token" in warning.lower() for warning in report["warnings"])
    assert session.execute.call_count == 5
    for index, call in enumerate(session.execute.call_args_list):
        sql = str(call.args[0])
        assert "occurred_at_utc" in sql and ">=" in sql and "<" in sql
        assert "environment" in sql
        if index:
            assert "ORDER BY" in sql and "LIMIT" in sql


def test_aggregate_usage_truncates_oversized_breakdowns_with_warning():
    summary = {
        "attempts": 101, "successful": 101, "failed": 0, "latency_ms": 0,
        "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
        "cache_write_input_tokens": 0, "reasoning_tokens": 0,
        "provider_reported_cost_usd": None, "calculated_cost_usd": None,
        "effective_cost_usd": None, "unknown_cost_attempts": 101,
        "unknown_input_token_attempts": 0, "unknown_output_token_attempts": 0,
        "retry_attempts": 0,
    }
    provider_rows = [
        {"provider": "openrouter", "model": f"model-{index:03d}", "attempts": 1,
         "successful": 1, "failed": 0, "input_tokens": 0, "output_tokens": 0,
         "effective_cost_usd": None}
        for index in range(101)
    ]
    session = MagicMock()
    session.execute.side_effect = [
        MagicMock(mappings=MagicMock(return_value=MagicMock(one=MagicMock(return_value=summary)))),
        MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=provider_rows)))),
        *[
            MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
            for _ in range(3)
        ],
    ]

    report = aggregate_llm_usage(
        session,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
        environment="production",
    )

    assert len(report["breakdowns"]["byProviderModel"]) == 100
    assert any("truncated" in warning.lower() for warning in report["warnings"])


def test_reconcile_invoice_summary_threshold():
    res1 = reconcile_invoice_summary(total_effective_cost_usd=100.0, invoice_cost_usd=104.0)
    assert res1["requiresInvestigation"] is False

    res2 = reconcile_invoice_summary(total_effective_cost_usd=100.0, invoice_cost_usd=106.0)
    assert res2["requiresInvestigation"] is True
