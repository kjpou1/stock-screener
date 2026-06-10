"""Bootstrap price-cache readiness over the canonical price-refresh universe."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.domain.markets.catalog import get_market_catalog
from app.domain.providers.price_symbol_support import split_supported_price_symbols
from app.services.bootstrap_cache_coverage import (
    BootstrapPriceCoverageReport,
    MISSING_SYMBOL_PREVIEW_LIMIT,
    evaluate_bootstrap_price_cache_coverage,
)
from app.services.cache.price_cache_warmup import (
    WarmupMetadataReadiness,
    evaluate_warmup_metadata,
)
from app.services.price_refresh_plan_builder import load_active_price_refresh_universe


@dataclass(frozen=True)
class BootstrapPriceReadinessReport(Mapping[str, Any]):
    market: str
    threshold: float
    price_coverage_date: date | str
    price_total_symbols: int
    price_covered_symbols: int
    price_missing_symbols: tuple[str, ...]
    unsupported_symbols: tuple[str, ...] = ()

    @classmethod
    def from_price_report(
        cls,
        price_report: BootstrapPriceCoverageReport,
        *,
        unsupported_symbols: tuple[str, ...],
    ) -> "BootstrapPriceReadinessReport":
        return cls(
            market=price_report.market,
            threshold=price_report.threshold,
            price_coverage_date=price_report.price_coverage_date,
            price_total_symbols=price_report.price_total_symbols,
            price_covered_symbols=price_report.price_covered_symbols,
            price_missing_symbols=price_report.price_missing_symbols,
            unsupported_symbols=unsupported_symbols,
        )

    @property
    def price_missing_symbol_count(self) -> int:
        return len(self.price_missing_symbols)

    @property
    def price_coverage_ratio(self) -> float:
        if self.price_total_symbols <= 0:
            return 0.0
        return self.price_covered_symbols / self.price_total_symbols

    @property
    def eligible(self) -> bool:
        return self.price_coverage_ratio >= self.threshold

    @property
    def mode(self) -> str:
        return "price_ready" if self.eligible else "waiting_for_prices"

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "threshold": self.threshold,
            "eligible": self.eligible,
            "mode": self.mode,
            "price_coverage_date": (
                self.price_coverage_date.isoformat()
                if isinstance(self.price_coverage_date, date)
                else str(self.price_coverage_date)
            ),
            "price_total_symbols": self.price_total_symbols,
            "price_covered_symbols": self.price_covered_symbols,
            "price_missing_symbols": self.price_missing_symbol_count,
            "price_coverage_ratio": self.price_coverage_ratio,
            "price_missing_symbols_preview": list(
                self.price_missing_symbols[:MISSING_SYMBOL_PREVIEW_LIMIT]
            ),
            "unsupported_skipped_count": len(self.unsupported_symbols),
            "unsupported_symbols_preview": list(
                self.unsupported_symbols[:MISSING_SYMBOL_PREVIEW_LIMIT]
            ),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(frozen=True)
class BootstrapPriceWarmupWaitDecision:
    market: str
    status: str
    warmup_metadata: dict | None
    warmup_readiness: WarmupMetadataReadiness
    coverage_report: BootstrapPriceReadinessReport

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def exhausted(self) -> bool:
        return self.status == "failed"

    @property
    def current(self) -> int:
        return self.coverage_report.price_covered_symbols

    @property
    def total(self) -> int:
        return self.coverage_report.price_total_symbols

    @property
    def progress_percent(self) -> float:
        return round(self.coverage_report.price_coverage_ratio * 100, 4)

    @property
    def failure_reason(self) -> str:
        return (
            f"Price cache coverage incomplete for {self.market}: "
            f"{self.current}/{self.total} "
            f"({self.coverage_report.price_coverage_ratio:.1%}, "
            f"threshold={self.coverage_report.threshold:.1%}, "
            f"missing={self.coverage_report.price_missing_symbol_count})"
        )

    @property
    def retry_message(self) -> str:
        return (
            f"Waiting for price cache coverage: {self.current}/{self.total} "
            f"({self.coverage_report.price_coverage_ratio:.1%}, "
            f"threshold={self.coverage_report.threshold:.1%}; "
            f"warmup={self.warmup_readiness.summary})"
        )

    def ready_payload(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "market": self.market,
            "warmup": self.warmup_metadata,
            "coverage": self.coverage_report.to_dict(),
        }


def _normalize_market(market: str | None) -> str:
    return get_market_catalog().get(market or "US").code


def load_bootstrap_price_symbols(
    db: Session,
    *,
    market: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return provider-eligible price symbols from the price-refresh universe."""
    market_code = _normalize_market(market)
    universe = load_active_price_refresh_universe(
        db,
        market=market_code,
        effective_market=market_code,
        normalize_market=_normalize_market,
    )
    supported_symbols, unsupported_symbols = split_supported_price_symbols(
        list(universe.symbols)
    )
    return tuple(supported_symbols), tuple(unsupported_symbols)


def evaluate_bootstrap_price_readiness(
    db: Session,
    *,
    market: str,
    as_of_date: date,
) -> BootstrapPriceReadinessReport:
    """Evaluate bootstrap price coverage using the same universe as price refresh."""
    market_code = _normalize_market(market)
    supported_symbols, unsupported_symbols = load_bootstrap_price_symbols(
        db,
        market=market_code,
    )
    report = evaluate_bootstrap_price_cache_coverage(
        db,
        market=market_code,
        symbols=supported_symbols,
        as_of_date=as_of_date,
    )
    return BootstrapPriceReadinessReport.from_price_report(
        report,
        unsupported_symbols=unsupported_symbols,
    )


def evaluate_bootstrap_price_warmup_wait(
    db: Session,
    *,
    market: str,
    as_of_date: date,
    warmup_metadata: dict | None,
    retries: int,
    max_retries: int,
) -> BootstrapPriceWarmupWaitDecision:
    market_code = _normalize_market(market)
    coverage_report = evaluate_bootstrap_price_readiness(
        db,
        market=market_code,
        as_of_date=as_of_date,
    )
    warmup_readiness = evaluate_warmup_metadata(
        warmup_metadata,
        context="bootstrap price run",
    )
    if coverage_report.eligible:
        status = "ready"
    elif retries >= max_retries:
        status = "failed"
    else:
        status = "waiting"
    return BootstrapPriceWarmupWaitDecision(
        market=market_code,
        status=status,
        warmup_metadata=warmup_metadata,
        warmup_readiness=warmup_readiness,
        coverage_report=coverage_report,
    )
