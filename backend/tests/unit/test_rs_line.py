"""Tests for the RS-line series helpers (chart overlay + blue-dot signal)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.patterns.rs_line import blue_dot_series, compute_rs_line


def _series(values, start="2025-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series([float(v) for v in values], index=idx)


def test_compute_rs_line_returns_normalized_ratio():
    rs = compute_rs_line(_series([10.0, 20.0, 30.0]), _series([2.0, 4.0, 5.0]), normalize=True)

    # raw ratio = [5.0, 5.0, 6.0]; normalized to start 1.0 = [1.0, 1.0, 1.2]
    assert rs.tolist() == pytest.approx([1.0, 1.0, 1.2])


def test_compute_rs_line_aligns_benchmark_by_index():
    stock = _series([10.0, 20.0, 30.0])
    # benchmark carries an extra leading day; reindex must align by date.
    extra = pd.bdate_range("2025-01-01", periods=4)
    benchmark = pd.Series([99.0, 10.0, 10.0, 10.0], index=extra)

    rs = compute_rs_line(stock, benchmark, normalize=False)

    assert list(rs.index) == list(stock.index)
    assert rs.tolist() == pytest.approx([1.0, 2.0, 3.0])


def test_blue_dot_series_marks_only_leading_dates():
    # rs = [1.0, 1.0, 1.1667, 1.1368]; running price high is 110 at idx1.
    #  idx2: rs new high (1.1667) & price 105 < 110 -> blue dot
    #  idx3: rs 1.1368 < 1.1667 -> not an rs new high -> no blue dot
    series = blue_dot_series(_series([100.0, 110.0, 105.0, 108.0]), _series([100.0, 110.0, 90.0, 95.0]))

    assert series.tolist() == [False, False, True, False]


def test_blue_dot_series_false_when_price_also_at_new_high():
    # Both rs and price rise to new highs together -> not a blue dot.
    series = blue_dot_series(_series([100.0, 105.0, 110.0]), _series([100.0, 100.0, 100.0]))

    assert series.iloc[-1] is False or bool(series.iloc[-1]) is False


def test_blue_dot_series_empty_on_insufficient_data():
    assert blue_dot_series(_series([]), _series([])).empty
