"""Live fetch execution helpers for planned price refresh jobs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from .price_refresh_planning import PriceRefreshJob


logger = logging.getLogger(__name__)

MappingResult = Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class PriceRefreshBatchOutcome:
    batch_number: int
    total_batches: int
    job: PriceRefreshJob
    symbols: tuple[str, ...]
    price_data_by_symbol: Mapping[str, Any]
    successes: tuple[str, ...]
    failures: tuple[str, ...]
    failure_details: Mapping[str, str]
    refreshed_by_market: Counter[str] = field(default_factory=Counter)
    failed_by_market: Counter[str] = field(default_factory=Counter)

    @property
    def refreshed(self) -> int:
        return len(self.successes)

    @property
    def failed(self) -> int:
        return len(self.failures)


@dataclass(frozen=True)
class PriceRefreshExecutionSummary:
    refreshed: int
    failed: int
    failed_symbols: list[str]
    refreshed_by_market: Counter[str] = field(default_factory=Counter)
    failed_by_market: Counter[str] = field(default_factory=Counter)
    processed: int = 0


@dataclass
class PriceRefreshExecutionAccumulator:
    refreshed: int = 0
    failed: int = 0
    processed: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    refreshed_by_market: Counter[str] = field(default_factory=Counter)
    failed_by_market: Counter[str] = field(default_factory=Counter)

    def add(self, batch: PriceRefreshBatchOutcome) -> None:
        self.processed += len(batch.symbols)
        self.refreshed += batch.refreshed
        self.failed += batch.failed
        self.failed_symbols.extend(batch.failures)
        self.refreshed_by_market.update(batch.refreshed_by_market)
        self.failed_by_market.update(batch.failed_by_market)

    def summary(self) -> PriceRefreshExecutionSummary:
        return PriceRefreshExecutionSummary(
            refreshed=self.refreshed,
            failed=self.failed,
            failed_symbols=list(self.failed_symbols),
            refreshed_by_market=Counter(self.refreshed_by_market),
            failed_by_market=Counter(self.failed_by_market),
            processed=self.processed,
        )


def _total_batches(jobs: Sequence[PriceRefreshJob], batch_size: int) -> int:
    return sum(
        (len(job.symbols) + batch_size - 1) // batch_size
        for job in jobs
    )


def _result_for_symbol(
    batch_results: MappingResult,
    symbol: str,
) -> Mapping[str, Any] | None:
    if symbol in batch_results:
        return batch_results[symbol]
    normalized_symbol = str(symbol).upper()
    for result_symbol, result in batch_results.items():
        if str(result_symbol).upper() == normalized_symbol:
            return result
    return None


def _record_failure(
    *,
    symbol: str,
    reason: str,
    failures: list[str],
    failed_by_market: Counter[str],
    failure_details: dict[str, str],
    market_for_symbol: Callable[[str], str],
) -> None:
    failures.append(symbol)
    failed_by_market[market_for_symbol(symbol)] += 1
    failure_details[symbol] = reason


def _classify_batch_results(
    *,
    symbols: Sequence[str],
    batch_results: MappingResult,
    market_for_symbol: Callable[[str], str],
) -> tuple[
    dict[str, Any],
    tuple[str, ...],
    tuple[str, ...],
    dict[str, str],
    Counter[str],
    Counter[str],
]:
    price_data_by_symbol: dict[str, Any] = {}
    successes: list[str] = []
    failures: list[str] = []
    failure_details: dict[str, str] = {}
    refreshed_by_market: Counter[str] = Counter()
    failed_by_market: Counter[str] = Counter()

    for symbol in symbols:
        data = _result_for_symbol(batch_results, symbol)
        if data is None:
            _record_failure(
                symbol=symbol,
                reason="No data returned",
                failures=failures,
                failed_by_market=failed_by_market,
                failure_details=failure_details,
                market_for_symbol=market_for_symbol,
            )
            continue
        if not data.get("has_error") and data.get("price_data") is not None:
            price_df = data["price_data"]
            if not price_df.empty:
                price_data_by_symbol[symbol] = price_df
                successes.append(symbol)
                refreshed_by_market[market_for_symbol(symbol)] += 1
                continue
            _record_failure(
                symbol=symbol,
                reason="Empty data returned",
                failures=failures,
                failed_by_market=failed_by_market,
                failure_details=failure_details,
                market_for_symbol=market_for_symbol,
            )
            continue
        _record_failure(
            symbol=symbol,
            reason=str(data.get("error", "Unknown error")),
            failures=failures,
            failed_by_market=failed_by_market,
            failure_details=failure_details,
            market_for_symbol=market_for_symbol,
        )

    return (
        price_data_by_symbol,
        tuple(successes),
        tuple(failures),
        failure_details,
        refreshed_by_market,
        failed_by_market,
    )


def iter_price_refresh_batches(
    *,
    jobs: Sequence[PriceRefreshJob],
    batch_size: int,
    market: str | None,
    fetch_batch: Callable[..., MappingResult],
    market_for_symbol: Callable[[str], str],
    raise_if_transient_database_error: Callable[[Exception], None],
) -> Iterator[PriceRefreshBatchOutcome]:
    total_batches = _total_batches(jobs, batch_size)
    batch_number = 0
    for job in jobs:
        job_symbols = tuple(job.symbols)
        for batch_start in range(0, len(job_symbols), batch_size):
            batch_symbols = job_symbols[batch_start:batch_start + batch_size]
            batch_number += 1
            logger.info(
                "Batch %d/%d: Fetching %d symbols (%s, period=%s)",
                batch_number,
                total_batches,
                len(batch_symbols),
                job.kind.value,
                job.period,
            )

            try:
                batch_results = fetch_batch(
                    batch_symbols,
                    period=job.period,
                    market=market,
                )
                (
                    price_data_by_symbol,
                    successes,
                    failures,
                    failure_details,
                    refreshed_by_market,
                    failed_by_market,
                ) = _classify_batch_results(
                    symbols=batch_symbols,
                    batch_results=batch_results,
                    market_for_symbol=market_for_symbol,
                )
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                raise_if_transient_database_error(exc)
                logger.error("Batch %d error: %s", batch_number, exc)
                price_data_by_symbol = {}
                successes = ()
                failures = batch_symbols
                failure_details = {symbol: str(exc) for symbol in batch_symbols}
                refreshed_by_market = Counter()
                failed_by_market = Counter(
                    market_for_symbol(symbol) for symbol in batch_symbols
                )

            yield PriceRefreshBatchOutcome(
                batch_number=batch_number,
                total_batches=total_batches,
                job=job,
                symbols=batch_symbols,
                price_data_by_symbol=price_data_by_symbol,
                successes=successes,
                failures=failures,
                failure_details=failure_details,
                refreshed_by_market=refreshed_by_market,
                failed_by_market=failed_by_market,
            )


def summarize_price_refresh_batches(
    batches: Sequence[PriceRefreshBatchOutcome],
) -> PriceRefreshExecutionSummary:
    accumulator = PriceRefreshExecutionAccumulator()
    for batch in batches:
        accumulator.add(batch)
    return accumulator.summary()
