from datetime import datetime, timezone
from types import SimpleNamespace

from symgov_backend.routes.workspace import build_daisy_report_item


def _case(closed_at):
    return SimpleNamespace(current_stage="retired_historical_cleanup", escalation_level="low", closed_at=closed_at)


PAYLOAD = {"id": "r1", "queue_item_id": "q1", "review_case_id": "c1", "coordination_status": "proposed"}


def test_closed_case_reports_closed_at():
    item = build_daisy_report_item(PAYLOAD, _case(datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)))
    assert item.closedAt is not None and item.closedAt.startswith("2026-10-08T09:30:00")


def test_open_or_missing_case_has_no_closed_at():
    assert build_daisy_report_item(PAYLOAD, _case(None)).closedAt is None
    assert build_daisy_report_item(PAYLOAD, None).closedAt is None
