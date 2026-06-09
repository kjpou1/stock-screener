"""Executable action planning for market price refreshes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .price_refresh_activity import (
    PriceRefreshFinalization,
    PriceRefreshOutcome,
)
from .price_refresh_planning import (
    GitHubSeedOutcome,
    PriceRefreshJob,
    PriceRefreshMode,
    PriceRefreshPlan,
    PriceRefreshSource,
)


@dataclass(frozen=True)
class PriceRefreshPreparation:
    all_symbols: list[str]
    symbol_markets: dict[str, str]
    refresh_plan: PriceRefreshPlan

    @property
    def github_seed(self) -> GitHubSeedOutcome | None:
        return self.refresh_plan.github_seed

    @property
    def refresh_source(self) -> PriceRefreshSource:
        return self.refresh_plan.source

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.refresh_plan.symbols

    @property
    def live_refresh_jobs(self) -> tuple[PriceRefreshJob, ...]:
        return self.refresh_plan.jobs


@dataclass(frozen=True)
class PriceRefreshTerminalCompletion:
    outcome: PriceRefreshOutcome
    finalization: PriceRefreshFinalization


class PriceRefreshActionFactory:
    def __init__(
        self,
        *,
        last_completed_trading_day: Callable[[str], Any],
    ) -> None:
        self._last_completed_trading_day = last_completed_trading_day

    def build_terminal_completion(
        self,
        *,
        mode: PriceRefreshMode,
        effective_market: str,
        preparation: PriceRefreshPreparation,
    ) -> PriceRefreshTerminalCompletion | None:
        if preparation.symbols:
            return None

        return self._terminal_completion(
            mode=mode,
            effective_market=effective_market,
            preparation=preparation,
        )

    def _terminal_completion(
        self,
        *,
        mode: PriceRefreshMode,
        effective_market: str,
        preparation: PriceRefreshPreparation,
    ) -> PriceRefreshTerminalCompletion:
        if preparation.refresh_source is PriceRefreshSource.GITHUB:
            message = (
                preparation.refresh_plan.completion_message
                or "GitHub daily price bundle is current - no live fetch needed"
            )
            symbol_count = len(preparation.all_symbols)
            trading_day = self._completion_trading_day(
                preparation.github_seed,
                effective_market,
            )
            finalization = PriceRefreshFinalization(
                metadata_status="completed",
                metadata_refreshed=symbol_count,
                metadata_total=symbol_count,
                activity_current=symbol_count,
                activity_total=symbol_count,
                message=message,
                market_success_rates={effective_market: (trading_day, 1.0)},
            )
        else:
            message = _empty_refresh_message(preparation.refresh_plan, mode)
            finalization = PriceRefreshFinalization(
                metadata_status="completed",
                metadata_refreshed=0,
                metadata_total=0,
                activity_current=0,
                activity_total=0,
                message=message,
                heartbeat_status=None,
            )

        return PriceRefreshTerminalCompletion(
            outcome=PriceRefreshOutcome(
                status="completed",
                source=preparation.refresh_source,
                mode=mode,
                message=message,
                github_seed=preparation.github_seed,
            ),
            finalization=finalization,
        )

    def _completion_trading_day(
        self,
        github_seed: GitHubSeedOutcome | None,
        effective_market: str,
    ) -> Any:
        if github_seed and github_seed.as_of_date is not None:
            return github_seed.as_of_date
        return self._last_completed_trading_day(effective_market)


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
