"""Side-effect runner for live price refresh batches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import settings
from .price_refresh_activity import CeleryTaskLike, PriceRefreshActivityReporter, task_id
from .price_refresh_execution import (
    PriceRefreshExecutionAccumulator,
    PriceRefreshExecutionSummary,
    iter_price_refresh_batches,
)
from .price_refresh_planning import PriceRefreshJob


@dataclass(frozen=True)
class LivePriceRefreshRunnerDependencies:
    fetch_with_backoff: Callable[..., Mapping[str, Mapping[str, Any]]]
    track_symbol_failures: Callable[..., None]
    rate_limiter_factory: Callable[[], Any]
    data_fetch_lock_factory: Callable[[], Any]
    raise_if_transient_database_error: Callable[[Exception], None]


class LivePriceRefreshRunner:
    def __init__(self, dependencies: LivePriceRefreshRunnerDependencies) -> None:
        self._deps = dependencies

    def run(
        self,
        *,
        task: CeleryTaskLike,
        bulk_fetcher: Any,
        price_cache: Any,
        db: Any,
        jobs: Sequence[PriceRefreshJob],
        total: int,
        batch_size: int,
        market: str | None,
        effective_market: str,
        activity_lifecycle: str,
        symbol_markets: Mapping[str, str],
        activity_reporter: PriceRefreshActivityReporter,
        progress_recorder: Callable[[PriceRefreshExecutionSummary], None] | None = None,
    ) -> PriceRefreshExecutionSummary:
        accumulator = PriceRefreshExecutionAccumulator()

        def market_for_symbol(symbol: str) -> str:
            return symbol_markets.get(str(symbol).upper(), effective_market)

        def fetch_batch(symbols: Sequence[str], *, period: str, market: str | None):
            return self._deps.fetch_with_backoff(
                bulk_fetcher,
                list(symbols),
                period=period,
                market=market,
            )

        for batch in iter_price_refresh_batches(
            jobs=jobs,
            batch_size=batch_size,
            market=market,
            fetch_batch=fetch_batch,
            market_for_symbol=market_for_symbol,
            raise_if_transient_database_error=self._deps.raise_if_transient_database_error,
        ):
            if batch.price_data_by_symbol:
                price_cache.store_batch_in_cache(
                    dict(batch.price_data_by_symbol),
                    also_store_db=True,
                )
            self._deps.track_symbol_failures(
                price_cache,
                list(batch.successes),
                list(batch.failures),
                db,
                    failure_details=dict(batch.failure_details),
                )

            accumulator.add(batch)
            summary = accumulator.summary()
            if progress_recorder is not None:
                progress_recorder(summary)
            percent = (summary.processed / total) * 100 if total else 100.0
            activity_reporter.publish_progress(
                db,
                price_cache,
                task=task,
                market=market,
                effective_market=effective_market,
                lifecycle=activity_lifecycle,
                current=summary.processed,
                total=total,
                percent=percent,
                message=f"Batch {batch.batch_number}/{batch.total_batches} · refreshing prices",
                refreshed=summary.refreshed,
                failed=summary.failed,
            )
            self._extend_lock(task, market=market)
            if summary.processed < total:
                self._wait_between_batches(market)

        return accumulator.summary()

    def _extend_lock(self, task: CeleryTaskLike, *, market: str | None) -> None:
        self._deps.data_fetch_lock_factory().extend_lock(
            task_id(task) or "unknown",
            300,
            market=market,
        )

    def _wait_between_batches(self, market: str | None) -> None:
        rate_limiter = self._deps.rate_limiter_factory()
        if market is not None:
            rate_limiter.wait_for_market("yfinance:batch", market)
            return
        rate_limiter.wait(
            "yfinance:batch",
            min_interval_s=settings.yfinance_batch_rate_limit_interval,
        )


@dataclass(frozen=True)
class PriceRefreshRetryScheduler:
    schedule_failed_symbol_retry: Callable[..., None]

    def schedule(
        self,
        failed_symbols: Sequence[str],
        *,
        effective_market: str,
        symbol_markets: Mapping[str, str],
        activity_lifecycle: str,
    ) -> None:
        if not failed_symbols:
            return
        failed_symbols_by_market: dict[str, list[str]] = {}
        for symbol in failed_symbols:
            failed_symbols_by_market.setdefault(
                symbol_markets.get(str(symbol).upper(), effective_market),
                [],
            ).append(symbol)
        for retry_market, retry_symbols in failed_symbols_by_market.items():
            kwargs = {
                "symbols": retry_symbols,
                "market": retry_market,
                "attempt": 1,
            }
            if activity_lifecycle == "bootstrap":
                kwargs["countdown"] = 30
            self.schedule_failed_symbol_retry(**kwargs)
