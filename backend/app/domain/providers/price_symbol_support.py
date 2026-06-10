"""Provider price-symbol support policy.

These helpers describe symbols that should never be sent to a price provider.
They intentionally do not normalize invalid symbols into a different ticker:
for JP, stripping a leading zero can resolve a different Yahoo instrument.
"""

from __future__ import annotations


YAHOO_ZERO_PREFIXED_JP_SYMBOL_ERROR = (
    "JP local code is zero-prefixed; no price data expected from Yahoo. "
    "Do not strip the leading zero because that can resolve a different security."
)
YAHOO_UNSUPPORTED_DERIVATIVE_PRICE_SYMBOL_ERROR = (
    "Derivative-style symbol is not expected to have Yahoo price history"
)
YAHOO_UNSUPPORTED_SUFFIXES = ("U", "UN", "UNT", "UNIT", "R", "RT")
YAHOO_UNSUPPORTED_PREFIXES = ("W", "WS", "WT")


def is_zero_prefixed_jp_local_code(local_code: str | None) -> bool:
    token = str(local_code or "").strip().upper()
    if not token:
        return False
    numeric_part = token[:-1] if token[-1].isalpha() else token
    return numeric_part.startswith("0") and numeric_part.isdigit()


def _jp_local_code_from_yahoo_symbol(symbol: str | None) -> str | None:
    normalized = str(symbol or "").strip().upper()
    if normalized.endswith(".T"):
        return normalized[:-2]
    if normalized.endswith(".JP"):
        return normalized[:-3]
    return None


def is_derivative_style_yahoo_symbol(symbol: str | None) -> bool:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    for delimiter in ("-", ".", "/"):
        if delimiter not in normalized:
            continue
        suffix = normalized.rsplit(delimiter, 1)[1]
        if suffix in YAHOO_UNSUPPORTED_SUFFIXES:
            return True
        if any(suffix.startswith(prefix) for prefix in YAHOO_UNSUPPORTED_PREFIXES):
            return True
    return False


def yahoo_price_no_data_error_for_symbol(symbol: str | None) -> str | None:
    local_code = _jp_local_code_from_yahoo_symbol(symbol)
    if local_code is not None and is_zero_prefixed_jp_local_code(local_code):
        return YAHOO_ZERO_PREFIXED_JP_SYMBOL_ERROR
    if is_derivative_style_yahoo_symbol(symbol):
        return YAHOO_UNSUPPORTED_DERIVATIVE_PRICE_SYMBOL_ERROR
    return None


def is_unsupported_yahoo_price_symbol(symbol: str) -> bool:
    return yahoo_price_no_data_error_for_symbol(symbol) is not None


def split_supported_price_symbols(
    symbols: list[str] | tuple[str, ...],
) -> tuple[list[str], list[str]]:
    supported: list[str] = []
    unsupported: list[str] = []
    for symbol in symbols:
        if is_unsupported_yahoo_price_symbol(symbol):
            unsupported.append(symbol)
        else:
            supported.append(symbol)
    return supported, unsupported
