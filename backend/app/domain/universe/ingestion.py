"""Shared canonical row models for official-source Universe ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from typing import TypeAlias

from ..markets.catalog import get_market_catalog
from .listing_tiers import listing_tier_registry


ACTIVE_UNIVERSE_STATUS = "active"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class UniverseLifecycleMetadata:
    """Mutable-row lifecycle state that maps to StockUniverse hot-path fields."""

    status: str = ACTIVE_UNIVERSE_STATUS
    is_active: bool = True
    status_reason: str | None = None
    first_seen_at: datetime | None = None
    last_seen_in_source_at: datetime | None = None
    deactivated_at: datetime | None = None
    consecutive_fetch_failures: int = 0

    def __post_init__(self) -> None:
        status = _required_text(self.status, "status").lower()
        reason = _optional_text(self.status_reason, "status_reason")
        failures = int(self.consecutive_fetch_failures or 0)
        if failures < 0:
            raise ValueError("consecutive_fetch_failures must be non-negative")
        if status == ACTIVE_UNIVERSE_STATUS and not self.is_active:
            raise ValueError("active status requires is_active=True")
        if status != ACTIVE_UNIVERSE_STATUS and self.is_active:
            raise ValueError("inactive status requires is_active=False")
        if self.is_active and self.deactivated_at is not None:
            raise ValueError("active lifecycle metadata must not set deactivated_at")

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "status_reason", reason)
        object.__setattr__(
            self,
            "first_seen_at",
            _optional_datetime(self.first_seen_at, "first_seen_at"),
        )
        object.__setattr__(
            self,
            "last_seen_in_source_at",
            _optional_datetime(
                self.last_seen_in_source_at,
                "last_seen_in_source_at",
            ),
        )
        object.__setattr__(
            self,
            "deactivated_at",
            _optional_datetime(self.deactivated_at, "deactivated_at"),
        )
        object.__setattr__(self, "consecutive_fetch_failures", failures)

    @classmethod
    def active(cls) -> "UniverseLifecycleMetadata":
        return cls(status=ACTIVE_UNIVERSE_STATUS, is_active=True)

    @classmethod
    def inactive(
        cls,
        *,
        status: str,
        reason: str | None = None,
        deactivated_at: datetime | None = None,
    ) -> "UniverseLifecycleMetadata":
        normalized_status = _required_text(status, "status").lower()
        if normalized_status == ACTIVE_UNIVERSE_STATUS:
            raise ValueError("inactive lifecycle metadata requires a non-active status")
        return cls(
            status=normalized_status,
            is_active=False,
            status_reason=reason,
            deactivated_at=deactivated_at,
        )


@dataclass(frozen=True, slots=True)
class UniverseSourceProvenance:
    """Source lineage for one canonical Universe row."""

    source_name: str
    snapshot_id: str
    source_symbol: str = ""
    source_row_number: int | None = None
    snapshot_as_of: date | str | None = None
    source_metadata: JsonObject = field(default_factory=dict)
    lineage_hash: str | None = None
    row_hash: str | None = None

    def __post_init__(self) -> None:
        source_name = _required_text(self.source_name, "source_name").lower()
        source_name = source_name.replace("-", "_")
        snapshot_id = _required_text(self.snapshot_id, "snapshot_id")
        source_symbol = _optional_text(self.source_symbol, "source_symbol") or ""
        row_number = self.source_row_number
        if row_number is not None:
            row_number = int(row_number)
            if row_number <= 0:
                raise ValueError("source_row_number must be positive when provided")

        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_symbol", source_symbol)
        object.__setattr__(self, "source_row_number", row_number)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(
            self,
            "snapshot_as_of",
            _optional_snapshot_as_of(self.snapshot_as_of),
        )
        object.__setattr__(
            self,
            "source_metadata",
            _json_object(self.source_metadata, "source_metadata"),
        )
        object.__setattr__(
            self,
            "lineage_hash",
            _optional_text(self.lineage_hash, "lineage_hash"),
        )
        object.__setattr__(self, "row_hash", _optional_text(self.row_hash, "row_hash"))


@dataclass(frozen=True, slots=True)
class CanonicalUniverseRow:
    """Canonical official-source Universe row before persistence."""

    symbol: str
    name: str
    market: str
    mic: str
    local_code: str
    currency: str | None
    timezone: str | None
    provenance: UniverseSourceProvenance
    listing_tier: str | None = None
    sector: str = ""
    industry: str = ""
    market_cap: float | None = None
    lifecycle: UniverseLifecycleMetadata = field(
        default_factory=UniverseLifecycleMetadata.active
    )

    def __post_init__(self) -> None:
        symbol = _required_text(self.symbol, "symbol").upper()
        market = _required_text(self.market, "market").upper()
        mic = _required_text(self.mic, "mic").upper()
        local_code = _required_text(self.local_code, "local_code").upper()

        market_entry = get_market_catalog().get(market)
        if mic not in market_entry.mics:
            supported = ", ".join(market_entry.mics)
            raise ValueError(
                f"Unsupported MIC {mic!r} for market {market}. Supported: {supported}"
            )

        mic_facts = market_entry.mic_facts_for(mic)
        currency = _optional_text(self.currency, "currency")
        currency = currency.upper() if currency else mic_facts.default_currency
        if currency not in market_entry.supported_currencies:
            supported = ", ".join(market_entry.supported_currencies)
            raise ValueError(
                f"Unsupported currency {currency!r} for market {market}. "
                f"Supported: {supported}"
            )

        timezone = _optional_text(self.timezone, "timezone") or mic_facts.timezone
        listing_tier = self._normalize_listing_tier(market, mic, self.listing_tier)
        market_cap = float(self.market_cap) if self.market_cap is not None else None

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", _optional_text(self.name, "name") or "")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "local_code", local_code)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "listing_tier", listing_tier)
        object.__setattr__(self, "sector", _optional_text(self.sector, "sector") or "")
        object.__setattr__(
            self,
            "industry",
            _optional_text(self.industry, "industry") or "",
        )
        object.__setattr__(self, "market_cap", market_cap)

    @property
    def active_identity_key(self) -> tuple[str, str, str] | None:
        if not self.lifecycle.is_active:
            return None
        return (self.market, self.mic, self.local_code)

    @staticmethod
    def _normalize_listing_tier(
        market: str,
        mic: str,
        listing_tier: str | None,
    ) -> str | None:
        raw_tier = _optional_text(listing_tier, "listing_tier")
        if raw_tier is None:
            return None
        normalized_tier = listing_tier_registry.normalize(
            market,
            raw_tier,
            mic=mic,
        )
        if normalized_tier is None:
            raise ValueError(
                f"Unsupported listing_tier {listing_tier!r} for {market}/{mic}"
            )
        return normalized_tier


@dataclass(frozen=True, slots=True)
class RejectedUniverseRow:
    """Rejected source row captured by canonicalization or ingestion."""

    source_row_number: int | None
    source_symbol: str
    reason: str
    source_name: str | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        row_number = self.source_row_number
        if row_number is not None:
            row_number = int(row_number)
            if row_number <= 0:
                raise ValueError("source_row_number must be positive when provided")

        object.__setattr__(self, "source_row_number", row_number)
        object.__setattr__(
            self,
            "source_symbol",
            _optional_text(self.source_symbol, "source_symbol") or "",
        )
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))
        object.__setattr__(
            self,
            "source_name",
            _optional_text(self.source_name, "source_name"),
        )
        object.__setattr__(
            self,
            "snapshot_id",
            _optional_text(self.snapshot_id, "snapshot_id"),
        )


class DuplicateActiveUniverseRowError(ValueError):
    """Raised when active rows collide on canonical Market/MIC/local_code."""

    def __init__(
        self,
        identity_key: tuple[str, str, str],
        first: CanonicalUniverseRow,
        second: CanonicalUniverseRow,
    ) -> None:
        self.identity_key = identity_key
        self.symbols = (first.symbol, second.symbol)
        market, mic, local_code = identity_key
        super().__init__(
            "Duplicate active Universe identity "
            f"{market}/{mic}/{local_code} for symbols "
            f"{first.symbol!r} and {second.symbol!r}"
        )


@dataclass(frozen=True, slots=True)
class CanonicalUniverseIngestionResult:
    """Canonical ingestion output with active identity invariants enforced."""

    canonical_rows: tuple[CanonicalUniverseRow, ...] = ()
    rejected_rows: tuple[RejectedUniverseRow, ...] = ()

    def __post_init__(self) -> None:
        canonical_rows = tuple(self.canonical_rows or ())
        rejected_rows = tuple(self.rejected_rows or ())

        seen: dict[tuple[str, str, str], CanonicalUniverseRow] = {}
        for row in canonical_rows:
            key = row.active_identity_key
            if key is None:
                continue
            previous = seen.get(key)
            if previous is not None:
                raise DuplicateActiveUniverseRowError(key, previous, row)
            seen[key] = row

        object.__setattr__(self, "canonical_rows", canonical_rows)
        object.__setattr__(self, "rejected_rows", rejected_rows)

    @property
    def accepted_count(self) -> int:
        return len(self.canonical_rows)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_rows)


def _required_text(value: str | None, field_name: str) -> str:
    normalized = _optional_text(value, field_name)
    if normalized is None:
        raise ValueError(f"{field_name} must be provided")
    return normalized


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = value.strip()
    return normalized or None


def _optional_datetime(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    return value


def _optional_snapshot_as_of(value: date | str | None) -> date | str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("snapshot_as_of must be a date or ISO date string")
    if isinstance(value, date):
        return value
    return _optional_text(value, "snapshot_as_of")


def _json_object(value: JsonObject | None, field_name: str) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")

    normalized: JsonObject = {}
    for key, child in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        normalized[key] = _json_value(child, f"{field_name}.{key}")
    return normalized


def _json_value(value: object, field_path: str) -> JsonValue:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{field_path} must be finite JSON number")
        return value
    if isinstance(value, list):
        return [_json_value(child, f"{field_path}[]") for child in value]
    if isinstance(value, dict):
        return _json_object(value, field_path)
    raise ValueError(f"{field_path} must be JSON-compatible")
