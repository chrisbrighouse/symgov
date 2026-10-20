"""The calibration arithmetic and the automatic-approval switch (WP-H6)."""

from __future__ import annotations

import pytest

from symgov_backend.hannah_calibration import THRESHOLDS, recommend, sweep
from symgov_backend.hannah_photos import auto_approve_min_score


def test_thresholds_start_at_the_proposal_threshold():
    assert THRESHOLDS[0] == 0.35 and THRESHOLDS[-1] == 0.95
    assert all(later > earlier for earlier, later in zip(THRESHOLDS, THRESHOLDS[1:]))


def test_the_sweep_counts_what_each_threshold_would_approve():
    pairs = [(0.9, True), (0.8, True), (0.75, False), (0.6, True), (None, True), (None, False)]
    rows = {row["threshold"]: row for row in sweep(pairs, thresholds=(0.5, 0.7, 0.8, 0.95))}
    assert rows[0.5] == {"threshold": 0.5, "would_approve": 4, "steward_rejected": 1, "steward_approved_caught": 3, "recall": 0.75}
    assert rows[0.7]["steward_rejected"] == 1
    assert rows[0.8] == {"threshold": 0.8, "would_approve": 2, "steward_rejected": 0, "steward_approved_caught": 2, "recall": 0.5}
    assert rows[0.95]["would_approve"] == 0


def test_the_recommendation_is_the_lowest_threshold_with_no_rejected_approval():
    pairs = [(0.9, True), (0.8, True), (0.75, False), (0.6, True)]
    picked = recommend(sweep(pairs, thresholds=(0.5, 0.7, 0.8, 0.95)))
    assert picked["threshold"] == 0.8 and picked["enough_evidence"] is False
    assert recommend(sweep([(0.9, False)], thresholds=(0.5, 0.95))) is None
    assert recommend(sweep([], thresholds=(0.5,))) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("  ", None), ("high", None), ("0.35", None), ("0.2", None), ("1.5", None), ("0.8", 0.8), ("1", 1.0)],
)
def test_automatic_approval_is_off_unless_a_sensible_threshold_is_set(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SYMGOV_HANNAH_AUTO_APPROVE_MIN_SCORE", raising=False)
    else:
        monkeypatch.setenv("SYMGOV_HANNAH_AUTO_APPROVE_MIN_SCORE", value)
    assert auto_approve_min_score() == expected
