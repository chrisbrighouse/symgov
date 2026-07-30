from datetime import datetime, timezone
import pytest
from symgov_backend.services.llm_usage_ledger import (
    calculate_period_utc_bounds,
    reconcile_invoice_summary,
)


def test_calculate_period_utc_bounds_day():
    anchor = datetime(2026, 7, 30, 15, 30, 0, tzinfo=timezone.utc)
    start, end = calculate_period_utc_bounds("day", anchor=anchor)
    assert start == datetime(2026, 7, 30, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_calculate_period_utc_bounds_mtd():
    anchor = datetime(2026, 7, 30, 15, 30, 0, tzinfo=timezone.utc)
    start, end = calculate_period_utc_bounds("mtd", anchor=anchor)
    assert start == datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_reconcile_invoice_summary_threshold():
    res1 = reconcile_invoice_summary(total_effective_cost_usd=100.0, invoice_cost_usd=104.0)
    assert res1["requiresInvestigation"] is False

    res2 = reconcile_invoice_summary(total_effective_cost_usd=100.0, invoice_cost_usd=106.0)
    assert res2["requiresInvestigation"] is True
