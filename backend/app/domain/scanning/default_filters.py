"""Per-market default scan filters.

Shared by the static-site exporter and the Daily Snapshot service so both
surfaces apply the same default liquidity floor for a market.

Default minVolume thresholds are expressed in local-currency daily dollar
volume (avg_volume × current_price), not share count — the ``volume`` field
on each scan row is sourced from ``avg_dollar_volume`` in the local listing
currency. The US value preserves the historical USD 100M floor; non-US
values are sized to roughly USD 1M-equivalent so the full local universe is
visible by default, with users free to tighten via the filter panel.
"""

from __future__ import annotations

DEFAULT_SCAN_FILTERS_BY_MARKET: dict[str, dict[str, int | None]] = {
    "US": {"minVolume": 100_000_000},      # USD 100M
    "HK": {"minVolume":   8_000_000},      # ~USD 1M @ HKD 7.8
    "IN": {"minVolume":  80_000_000},      # ~USD 1M @ INR 83
    "JP": {"minVolume": 150_000_000},      # ~USD 1M @ JPY 150
    "KR": {"minVolume": 1_000_000_000},    # ~USD 750k @ KRW 1380
    "TW": {"minVolume":  30_000_000},      # ~USD 1M @ TWD 32
    "CN": {"minVolume":   7_000_000},      # ~USD 1M @ CNY 7.2
    "CA": {"minVolume":   1_400_000},      # ~USD 1M @ CAD 1.36
    "DE": {"minVolume":     900_000},      # ~USD 1M @ EUR 0.92
    "SG": {"minVolume":   1_300_000},      # ~USD 1M @ SGD 1.35
    "AU": {"minVolume":   1_500_000},      # ~USD 1M @ AUD 1.5
    "MY": {"minVolume":   4_500_000},      # ~USD 1M @ MYR 4.5
}
DEFAULT_SCAN_FILTERS_FALLBACK: dict[str, int | None] = {"minVolume": None}


def resolve_default_scan_filters(market: str | None) -> dict[str, int | None]:
    """Return the per-market default scan filters, or the no-op fallback."""
    code = (market or "").upper()
    return dict(DEFAULT_SCAN_FILTERS_BY_MARKET.get(code, DEFAULT_SCAN_FILTERS_FALLBACK))
