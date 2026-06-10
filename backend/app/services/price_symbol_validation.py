"""Compatibility exports for provider price-symbol support policy."""

from __future__ import annotations

from app.domain.providers.price_symbol_support import (
    YAHOO_UNSUPPORTED_DERIVATIVE_PRICE_SYMBOL_ERROR,
    YAHOO_UNSUPPORTED_PREFIXES,
    YAHOO_UNSUPPORTED_SUFFIXES,
    YAHOO_ZERO_PREFIXED_JP_SYMBOL_ERROR,
    is_derivative_style_yahoo_symbol,
    is_unsupported_yahoo_price_symbol,
    is_zero_prefixed_jp_local_code,
    split_supported_price_symbols,
    yahoo_price_no_data_error_for_symbol,
)

__all__ = [
    "YAHOO_UNSUPPORTED_DERIVATIVE_PRICE_SYMBOL_ERROR",
    "YAHOO_UNSUPPORTED_PREFIXES",
    "YAHOO_UNSUPPORTED_SUFFIXES",
    "YAHOO_ZERO_PREFIXED_JP_SYMBOL_ERROR",
    "is_derivative_style_yahoo_symbol",
    "is_unsupported_yahoo_price_symbol",
    "is_zero_prefixed_jp_local_code",
    "split_supported_price_symbols",
    "yahoo_price_no_data_error_for_symbol",
]
