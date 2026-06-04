"""RS line (stock / benchmark ratio) and the "blue dot" leadership signal.

The RS line is ``stock_close / benchmark_close``. A "blue dot" (DeepVue/O'Neil
leadership signal) fires when the RS line makes a new trailing-``lookback`` high
**before** price does — the RS line is at a new high while price is not.

These helpers produce *series* for chart overlay. The single-point flags used by
the per-scan Setup Engine field are computed in ``readiness.py`` directly from the
same ``technicals.at_new_high`` primitive.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.patterns.technicals import rolling_at_new_high

DEFAULT_LOOKBACK = 252


def _aligned_ratio(stock_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    stock_close = stock_close.astype(float)
    aligned_benchmark = benchmark_close.astype(float).reindex(stock_close.index)
    return stock_close / aligned_benchmark.replace(0.0, np.nan)


def compute_rs_line(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    normalize: bool = True,
) -> pd.Series:
    """RS line aligned to the stock's index.

    When ``normalize`` is True the series is scaled to start at 1.0 for display
    (a positive monotonic transform; it does not affect new-high detection).
    """
    rs = _aligned_ratio(stock_close, benchmark_close)
    if normalize:
        valid = rs.dropna()
        if not valid.empty and valid.iloc[0] != 0:
            rs = rs / valid.iloc[0]
    return rs


def blue_dot_series(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    lookback: int = DEFAULT_LOOKBACK,
) -> pd.Series:
    """Per-date boolean: RS line at a new high while price is not."""
    rs = _aligned_ratio(stock_close, benchmark_close)
    frame = pd.DataFrame({"rs": rs, "price": stock_close.astype(float)}).dropna()
    if frame.empty:
        return pd.Series([], dtype=bool)
    rs_new_high = rolling_at_new_high(frame["rs"], window=lookback)
    price_new_high = rolling_at_new_high(frame["price"], window=lookback)
    return rs_new_high & (~price_new_high)
