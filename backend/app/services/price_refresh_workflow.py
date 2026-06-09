"""Application workflow for smart market price refreshes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from ..config import settings
from ..services.price_refresh_activity import (
    CeleryTaskLike,
    PriceRefreshActivityReporter,
    PriceRefreshFinalization,
    PriceRefreshOutcome,
)
from ..services.price_refresh_live_runner import (
    LivePriceRefreshRunner,
    PriceRefreshRetryScheduler,
)
from ..services.price_refresh_execution import PriceRefreshExecutionSummary
from ..services.price_refresh_planning import (
    GitHubSeedOutcome,
    LIVE_TOP_UP_MODES,
    PriceRefreshJob,
    PriceRefreshMode,
    PriceRefreshPlan,
    PriceRefreshSource,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceRefreshMarketGateway:
    normalize_market: Callable[[str], str]
    market_tag: Callable[[str | None], str]
    log_extra: Callable[[str | None], Mapping[str, Any]]
    get_eastern_now: Callable[[], Any]
    is_trading_day: Callable[[Any], bool]
    format_market_status: Callable[[], str]
    is_market_enabled_now: Callable[[str], bool]


@dataclass(frozen=True)
class PriceRefreshWorkflowDependencies:
    session_factory: Callable[[], Any]
    price_cache_factory: Callable[[], Any]
    bulk_fetcher_factory: Callable[[], Any]
    warm_benchmarks: Callable[..., Mapping[str, Any]]
    plan_price_refresh: Callable[..., PriceRefreshPlan]
    daily_price_bundle_service_factory: Callable[[], Any]
    market_calendar_service_factory: Callable[[], Any]
    activity_reporter: PriceRefreshActivityReporter
    live_runner: LivePriceRefreshRunner
    retry_scheduler: PriceRefreshRetryScheduler
    market_gateway: PriceRefreshMarketGateway
    raise_if_transient_database_error: Callable[[Exception], None]
    safe_rollback: Callable[[Any], None]
    time_window_bypass_enabled: Callable[[], bool] = lambda: False


@dataclass(frozen=True)
class PriceRefreshPreparation:
    all_symbols: list[str]
    symbol_markets: dict[str, str]
    github_seed: GitHubSeedOutcome | None
    refresh_plan: PriceRefreshPlan
    refresh_source: PriceRefreshSource
    symbols: list[str]
    live_refresh_jobs: list[PriceRefreshJob]


@dataclass
class PriceRefreshProgressState:
    refreshed: int = 0
    processed: int = 0
    failed: int = 0
    total: int = 0

    def update_from_summary(self, summary: PriceRefreshExecutionSummary) -> None:
        self.refreshed = summary.refreshed
        self.processed = summary.processed
        self.failed = summary.failed


class PriceRefreshWorkflow:
    def __init__(self, dependencies: PriceRefreshWorkflowDependencies) -> None:
        self._deps = dependencies

    def run(
        self,
        *,
        task: CeleryTaskLike,
        mode: PriceRefreshMode | str = PriceRefreshMode.AUTO,
        market: str | None = None,
        activity_lifecycle: str | None = None,
    ) -> dict[str, Any]:
        parsed_mode = PriceRefreshMode.parse(mode)
        gateway = self._deps.market_gateway
        effective_market = (
            gateway.normalize_market(market) if market is not None else "US"
        )
        activity_lifecycle = activity_lifecycle or "daily_refresh"
        log_extra = gateway.log_extra(market)

        logger.info("=" * 80)
        logger.info(
            "TASK: Smart Cache Refresh %s (mode=%s)",
            gateway.market_tag(market),
            parsed_mode.value,
            extra=log_extra,
        )
        logger.info("Market status: %s", gateway.format_market_status(), extra=log_extra)
        logger.info("Timestamp: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), extra=log_extra)
        logger.info("=" * 80)

        if market is not None and not gateway.is_market_enabled_now(
            gateway.normalize_market(market)
        ):
            logger.info("Skipping smart refresh for disabled market %s", market, extra=log_extra)
            return {
                "status": "skipped",
                "reason": f"market {effective_market} is disabled in local runtime preferences",
                "market": effective_market,
                "mode": parsed_mode.value,
                "timestamp": datetime.now().isoformat(),
            }

        if self._should_reject_full_refresh(parsed_mode, task, market):
            now_et = gateway.get_eastern_now()
            return {
                "skipped": True,
                "reason": f"Outside refresh window (weekday={now_et.weekday()}, hour={now_et.hour})",
                "mode": parsed_mode.value,
                "timestamp": datetime.now().isoformat(),
            }

        if parsed_mode is PriceRefreshMode.AUTO:
            today = gateway.get_eastern_now().date()
            if not gateway.is_trading_day(today):
                logger.info("Skipping smart refresh (auto) - %s is not a trading day", today)
                return {
                    "skipped": True,
                    "reason": "Not a trading day",
                    "date": today.isoformat(),
                    "mode": parsed_mode.value,
                }

        price_cache = self._deps.price_cache_factory()
        db = self._deps.session_factory()
        progress = PriceRefreshProgressState()

        try:
            self._deps.activity_reporter.start_prices(
                db,
                task=task,
                market=effective_market,
                lifecycle=activity_lifecycle,
                message="Refreshing market prices",
            )
            preparation = self._prepare_refresh(
                db,
                price_cache,
                task=task,
                mode=parsed_mode,
                market=market,
                effective_market=effective_market,
                activity_lifecycle=activity_lifecycle,
                log_extra=log_extra,
            )

            no_live_result = self._complete_without_live_fetch(
                db,
                price_cache,
                task=task,
                mode=parsed_mode,
                market=market,
                effective_market=effective_market,
                activity_lifecycle=activity_lifecycle,
                preparation=preparation,
            )
            if no_live_result is not None:
                return no_live_result

            outcome = self._execute_live_refresh(
                db,
                price_cache,
                task=task,
                mode=parsed_mode,
                market=market,
                effective_market=effective_market,
                activity_lifecycle=activity_lifecycle,
                preparation=preparation,
                progress=progress,
            )
            return outcome.to_task_result()

        except SoftTimeLimitExceeded:
            logger.error("Soft time limit exceeded in smart_refresh_cache", exc_info=True)
            self._deps.safe_rollback(db)
            self._deps.activity_reporter.record_failure(
                db,
                price_cache,
                task=task,
                market=market,
                effective_market=effective_market,
                lifecycle=activity_lifecycle,
                refreshed=progress.refreshed,
                total=progress.total,
                current=progress.processed,
                message="Soft time limit exceeded",
            )
            raise
        except Exception as exc:
            self._deps.raise_if_transient_database_error(exc)
            logger.error("Error in smart_refresh_cache task: %s", exc, exc_info=True)
            self._deps.safe_rollback(db)
            self._deps.activity_reporter.record_failure(
                db,
                price_cache,
                task=task,
                market=market,
                effective_market=effective_market,
                lifecycle=activity_lifecycle,
                refreshed=progress.refreshed,
                total=progress.total,
                current=progress.processed,
                message=str(exc),
            )
            return {
                "status": "failed",
                "error": str(exc),
                "refreshed": progress.refreshed,
                "failed": progress.failed,
                "mode": parsed_mode.value,
                "timestamp": datetime.now().isoformat(),
            }
        finally:
            db.close()

    def _prepare_refresh(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        mode: PriceRefreshMode,
        market: str | None,
        effective_market: str,
        activity_lifecycle: str,
        log_extra: Mapping[str, Any],
    ) -> PriceRefreshPreparation:
        gateway = self._deps.market_gateway
        logger.info("[1/3] Warming market benchmarks...")
        benchmark_result = self._deps.warm_benchmarks(market=market)
        if benchmark_result.get("error"):
            logger.error("Benchmark warmup failed: %s", benchmark_result.get("error"))

        logger.info("[2/3] Determining symbols to refresh (mode=%s)...", mode.value)
        all_symbols, symbol_markets = self._load_active_symbol_universe(
            db,
            market=market,
            effective_market=effective_market,
        )

        def symbols_needing_auto_refresh(candidate_symbols: Sequence[str]) -> Sequence[str]:
            logger.info(
                "Auto refresh: %d active symbols (full universe, market cap order) %s",
                len(candidate_symbols),
                gateway.market_tag(market),
                extra=log_extra,
            )
            refresh_symbols = price_cache.get_symbols_needing_refresh(
                list(candidate_symbols),
                max_age_hours=settings.refresh_skip_hours,
            )
            skipped = len(candidate_symbols) - len(refresh_symbols)
            if skipped > 0:
                logger.info(
                    "Skipping %d recently-refreshed symbols (fresh within %sh)",
                    skipped,
                    settings.refresh_skip_hours,
                )
            return refresh_symbols

        github_seed = None
        if mode in LIVE_TOP_UP_MODES and all_symbols and market is not None:
            github_seed = GitHubSeedOutcome.from_mapping(
                self._deps.daily_price_bundle_service_factory().sync_from_github(
                    db,
                    market=effective_market,
                    allow_stale=True,
                )
            )

        refresh_plan = self._deps.plan_price_refresh(
            db,
            all_symbols=all_symbols,
            mode=mode,
            effective_market=effective_market,
            market_calendar_service=self._deps.market_calendar_service_factory(),
            github_seed=github_seed,
            recently_refreshed_filter=(
                symbols_needing_auto_refresh
                if mode is PriceRefreshMode.AUTO
                else None
            ),
        )
        preparation = PriceRefreshPreparation(
            all_symbols=all_symbols,
            symbol_markets=symbol_markets,
            github_seed=refresh_plan.github_seed,
            refresh_plan=refresh_plan,
            refresh_source=refresh_plan.source,
            symbols=list(refresh_plan.symbols),
            live_refresh_jobs=list(refresh_plan.jobs),
        )
        self._publish_github_seed_log(
            github_seed=preparation.github_seed,
            refresh_plan=refresh_plan,
            effective_market=effective_market,
            all_symbols=all_symbols,
            activity_lifecycle=activity_lifecycle,
            db=db,
            task=task,
            log_extra=log_extra,
        )
        self._log_live_symbol_plan(
            refresh_plan=refresh_plan,
            refresh_source=preparation.refresh_source,
            symbols=preparation.symbols,
            mode=mode,
            market=market,
            effective_market=effective_market,
            log_extra=log_extra,
        )
        return preparation

    def _complete_without_live_fetch(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        mode: PriceRefreshMode,
        market: str | None,
        effective_market: str,
        activity_lifecycle: str,
        preparation: PriceRefreshPreparation,
    ) -> dict[str, Any] | None:
        if preparation.refresh_source is PriceRefreshSource.GITHUB and not preparation.symbols:
            message = (
                preparation.refresh_plan.completion_message
                or "GitHub daily price bundle is current - no live fetch needed"
            )
            trading_day = self._completion_trading_day(
                preparation.github_seed,
                effective_market,
            )
            outcome = PriceRefreshOutcome(
                status="completed",
                source=PriceRefreshSource.GITHUB,
                mode=mode,
                message=message,
                github_seed=preparation.github_seed,
            )
            finalization = PriceRefreshFinalization(
                metadata_status="completed",
                metadata_refreshed=len(preparation.all_symbols),
                metadata_total=len(preparation.all_symbols),
                activity_current=len(preparation.all_symbols) if preparation.all_symbols else 0,
                activity_total=len(preparation.all_symbols) if preparation.all_symbols else 0,
                message=message,
                market_success_rates={effective_market: (trading_day, 1.0)},
            )
            self._deps.activity_reporter.finalize_success(
                db,
                price_cache,
                task=task,
                market=market,
                effective_market=effective_market,
                lifecycle=activity_lifecycle,
                finalization=finalization,
            )
            return outcome.to_task_result()

        if preparation.symbols:
            return None

        message = self._empty_refresh_message(preparation.refresh_plan, mode)
        outcome = PriceRefreshOutcome(
            status="completed",
            source=preparation.refresh_source,
            mode=mode,
            message=message,
            github_seed=preparation.github_seed,
        )
        finalization = PriceRefreshFinalization(
            metadata_status="completed",
            metadata_refreshed=0,
            metadata_total=0,
            activity_current=0,
            activity_total=0,
            message=message,
            heartbeat_status=None,
        )
        self._deps.activity_reporter.finalize_success(
            db,
            price_cache,
            task=task,
            market=market,
            effective_market=effective_market,
            lifecycle=activity_lifecycle,
            finalization=finalization,
        )
        return outcome.to_task_result()

    def _execute_live_refresh(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        mode: PriceRefreshMode,
        market: str | None,
        effective_market: str,
        activity_lifecycle: str,
        preparation: PriceRefreshPreparation,
        progress: PriceRefreshProgressState,
    ) -> PriceRefreshOutcome:
        total = len(preparation.symbols)
        progress.total = total
        symbol_market_totals = Counter(
            preparation.symbol_markets.get(str(symbol).upper(), effective_market)
            for symbol in preparation.symbols
        )
        bulk_fetcher = self._deps.bulk_fetcher_factory()

        self._deps.activity_reporter.publish_progress(
            db,
            price_cache,
            task=task,
            market=market,
            effective_market=effective_market,
            lifecycle=activity_lifecycle,
            current=0,
            total=total,
            percent=0,
            message="Refreshing market prices",
            refreshed=0,
            failed=0,
        )

        logger.info("[3/3] Fetching %d symbols...", total)
        execution_result = self._deps.live_runner.run(
            task=task,
            bulk_fetcher=bulk_fetcher,
            price_cache=price_cache,
            db=db,
            jobs=preparation.live_refresh_jobs,
            total=total,
            batch_size=100,
            market=market,
            effective_market=effective_market,
            activity_lifecycle=activity_lifecycle,
            symbol_markets=preparation.symbol_markets,
            activity_reporter=self._deps.activity_reporter,
            progress_recorder=progress.update_from_summary,
        )
        progress.processed = execution_result.processed
        progress.refreshed = execution_result.refreshed
        progress.failed = execution_result.failed

        success_rate = progress.refreshed / total if total > 0 else 0
        status = "completed" if success_rate >= 0.95 else "partial"
        market_success_rates = self._market_success_rates(
            symbol_market_totals=symbol_market_totals,
            refreshed_by_market=execution_result.refreshed_by_market,
        )

        self._deps.retry_scheduler.schedule(
            execution_result.failed_symbols,
            effective_market=effective_market,
            symbol_markets=preparation.symbol_markets,
            activity_lifecycle=activity_lifecycle,
        )

        logger.info("=" * 80)
        logger.info("Smart refresh completed (%s mode):", mode.value)
        logger.info("  Refreshed: %s", progress.refreshed)
        logger.info("  Failed: %s", progress.failed)
        logger.info("  Total: %s", total)
        if execution_result.failed_symbols:
            logger.info("  Failed symbols: %s...", execution_result.failed_symbols[:10])
        logger.info("=" * 80)

        finalization = PriceRefreshFinalization(
            metadata_status=status,
            metadata_refreshed=progress.refreshed,
            metadata_total=total,
            activity_current=total,
            activity_total=total,
            message=f"Price refresh {status}",
            market_success_rates=market_success_rates,
        )
        self._deps.activity_reporter.finalize_success(
            db,
            price_cache,
            task=task,
            market=market,
            effective_market=effective_market,
            lifecycle=activity_lifecycle,
            finalization=finalization,
        )

        return PriceRefreshOutcome(
            status=status,
            source=(
                PriceRefreshSource.GITHUB_AND_LIVE
                if preparation.refresh_plan.used_github_seed
                else PriceRefreshSource.LIVE
            ),
            mode=mode,
            refreshed=progress.refreshed,
            failed=progress.failed,
            total=total,
            failed_symbols=execution_result.failed_symbols,
            github_seed=preparation.github_seed,
        )

    def _market_success_rates(
        self,
        *,
        symbol_market_totals: Mapping[str, int],
        refreshed_by_market: Mapping[str, int],
    ) -> dict[str, tuple[Any, float]]:
        market_success_rates = {}
        for refresh_market, market_total in symbol_market_totals.items():
            market_success_rate = (
                refreshed_by_market[refresh_market] / market_total
                if market_total > 0
                else 0
            )
            if market_success_rate >= 0.95:
                market_success_rates[refresh_market] = (
                    self._deps.market_calendar_service_factory().last_completed_trading_day(
                        refresh_market
                    ),
                    market_success_rate,
                )
        return market_success_rates

    def _should_reject_full_refresh(
        self,
        mode: PriceRefreshMode,
        task: CeleryTaskLike,
        market: str | None,
    ) -> bool:
        if mode is not PriceRefreshMode.FULL or market is not None:
            return False
        is_manual = (
            self._deps.time_window_bypass_enabled()
            or (
                getattr(getattr(task, "request", None), "headers", None)
                and task.request.headers.get("origin") == "manual"
            )
        )
        if is_manual:
            return False
        now_et = self._deps.market_gateway.get_eastern_now()
        weekday = now_et.weekday()
        hour = now_et.hour
        in_weekday_window = weekday < 5 and 16 <= hour < 24
        in_sunday_window = weekday == 6 and 1 <= hour < 6
        if in_weekday_window or in_sunday_window:
            return False
        logger.warning(
            "Rejecting Beat-scheduled full refresh outside time window "
            "(weekday=%s, hour=%s). Likely a catchup storm.",
            weekday,
            hour,
        )
        return True

    def _load_active_symbol_universe(
        self,
        db,
        *,
        market: str | None,
        effective_market: str,
    ) -> tuple[list[str], dict[str, str]]:
        from ..models.stock_universe import StockUniverse

        query = db.query(StockUniverse.symbol, StockUniverse.market).filter(
            StockUniverse.is_active == True
        )
        if market is not None:
            query = query.filter(
                StockUniverse.market == self._deps.market_gateway.normalize_market(market)
            )
        query = query.order_by(StockUniverse.market_cap.desc().nullslast())
        universe_rows = query.all()
        all_symbols = [row.symbol for row in universe_rows]
        symbol_markets = {
            str(row.symbol).upper(): self._deps.market_gateway.normalize_market(
                getattr(row, "market", None) or effective_market
            )
            for row in universe_rows
        }
        return all_symbols, symbol_markets

    def _publish_github_seed_log(
        self,
        *,
        github_seed: GitHubSeedOutcome | None,
        refresh_plan: PriceRefreshPlan,
        effective_market: str,
        all_symbols: Sequence[str],
        activity_lifecycle: str,
        db,
        task: CeleryTaskLike,
        log_extra: Mapping[str, Any],
    ) -> None:
        if github_seed and github_seed.stale_reason:
            logger.info(
                "GitHub daily price bundle for %s imported with stale manifest: %s",
                effective_market,
                github_seed.stale_reason,
                extra=log_extra,
            )
        if github_seed and not refresh_plan.used_github_seed:
            reason = github_seed.reason or github_seed.error
            logger.warning(
                "GitHub daily price bundle not used for %s (status=%s, reason=%s, stale_reason=%s); "
                "using live refresh policy",
                effective_market,
                github_seed.status_value,
                reason,
                github_seed.stale_reason,
                extra=log_extra,
            )
            self._deps.activity_reporter.publish_github_seed_fallback(
                db,
                task=task,
                market=effective_market,
                lifecycle=activity_lifecycle,
                total=len(all_symbols),
                status_value=github_seed.status_value,
            )

    def _log_live_symbol_plan(
        self,
        *,
        refresh_plan: PriceRefreshPlan,
        refresh_source: PriceRefreshSource,
        symbols: Sequence[str],
        mode: PriceRefreshMode,
        market: str | None,
        effective_market: str,
        log_extra: Mapping[str, Any],
    ) -> None:
        if not symbols:
            return
        if refresh_source is PriceRefreshSource.GITHUB_AND_LIVE:
            logger.info(
                "GitHub daily price bundle synced for %s; live refresh will top up %d symbols",
                effective_market,
                len(symbols),
                extra=log_extra,
            )
        elif mode is PriceRefreshMode.FULL:
            logger.info(
                "Full refresh: %d symbols (market cap order) %s",
                len(symbols),
                self._deps.market_gateway.market_tag(market),
                extra=log_extra,
            )
        elif mode in {PriceRefreshMode.BOOTSTRAP, PriceRefreshMode.DELTA}:
            logger.info(
                "Delta refresh: %d symbols %s",
                len(symbols),
                self._deps.market_gateway.market_tag(market),
                extra=log_extra,
            )

    def _completion_trading_day(
        self,
        github_seed: GitHubSeedOutcome | None,
        effective_market: str,
    ):
        if github_seed and github_seed.as_of_date is not None:
            return github_seed.as_of_date
        return self._deps.market_calendar_service_factory().last_completed_trading_day(
            effective_market
        )

    @staticmethod
    def _empty_refresh_message(
        refresh_plan: PriceRefreshPlan,
        mode: PriceRefreshMode,
    ) -> str:
        if refresh_plan.completion_message:
            return refresh_plan.completion_message
        if mode is PriceRefreshMode.AUTO:
            return "All symbols recently refreshed - nothing to do"
        if mode in {PriceRefreshMode.BOOTSTRAP, PriceRefreshMode.DELTA}:
            return "All symbols already fresh - no live fetch needed"
        return "No active symbols found in universe"
