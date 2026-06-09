"""Shared price-provider failure classification."""

from __future__ import annotations

from enum import Enum
from typing import Any


class PriceFetchFailureKind(str, Enum):
    NO_PRICE_DATA = "no_price_data"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    CIRCUIT_OPEN = "circuit_open"


_RATE_LIMIT_INDICATORS = ("rate", "429", "too many", "limit", "throttl")
_NO_PRICE_DATA_INDICATORS = (
    "yfpricesmissingerror",
    "possibly delisted",
    "delisted",
    "no price data",
    "no data found",
    "symbol may be delisted",
    "no data after filtering",
)
_TRANSIENT_INDICATORS = (
    "empty",
    "batch download error",
    "missing from results",
    "symbol not in download results",
)


def normalize_price_fetch_failure_kind(value: Any) -> PriceFetchFailureKind | None:
    if isinstance(value, PriceFetchFailureKind):
        return value
    if value is None:
        return None
    try:
        return PriceFetchFailureKind(str(value))
    except ValueError:
        return None


def is_rate_limit_error(error: str | None) -> bool:
    lower = (error or "").lower()
    return any(indicator in lower for indicator in _RATE_LIMIT_INDICATORS)


def classify_price_fetch_error(error: str | None) -> PriceFetchFailureKind | None:
    lower = (error or "").lower()
    if not lower:
        return None
    if lower == PriceFetchFailureKind.CIRCUIT_OPEN.value:
        return PriceFetchFailureKind.CIRCUIT_OPEN
    if is_rate_limit_error(lower):
        return PriceFetchFailureKind.RATE_LIMIT
    if any(indicator in lower for indicator in _NO_PRICE_DATA_INDICATORS):
        return PriceFetchFailureKind.NO_PRICE_DATA
    if any(indicator in lower for indicator in _TRANSIENT_INDICATORS):
        return PriceFetchFailureKind.TRANSIENT
    return None


def is_no_data_price_failure(error: str | None) -> bool:
    return classify_price_fetch_error(error) is PriceFetchFailureKind.NO_PRICE_DATA


def is_retryable_price_failure_kind(
    kind: PriceFetchFailureKind | str | None,
) -> bool:
    normalized = normalize_price_fetch_failure_kind(kind)
    return normalized is not PriceFetchFailureKind.NO_PRICE_DATA


def is_retryable_price_failure(
    *,
    kind: PriceFetchFailureKind | str | None = None,
    error: str | None = None,
) -> bool:
    normalized = normalize_price_fetch_failure_kind(kind)
    if normalized is None:
        normalized = classify_price_fetch_error(error)
    return is_retryable_price_failure_kind(normalized)
