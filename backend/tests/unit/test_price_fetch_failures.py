from __future__ import annotations


def test_price_fetch_failure_classifier_separates_permanent_and_retryable_errors():
    from app.services.price_fetch_failures import (
        PriceFetchFailureKind,
        classify_price_fetch_error,
        is_retryable_price_failure,
    )

    assert (
        classify_price_fetch_error("possibly delisted; no price data found")
        is PriceFetchFailureKind.NO_PRICE_DATA
    )
    assert (
        classify_price_fetch_error("429 Too Many Requests")
        is PriceFetchFailureKind.RATE_LIMIT
    )
    assert (
        classify_price_fetch_error("yf.download returned empty")
        is PriceFetchFailureKind.TRANSIENT
    )
    assert not is_retryable_price_failure(error="delisted")
    assert is_retryable_price_failure(error="429 Too Many Requests")
