"""Terminal completion helpers for market price refreshes."""

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
    PriceRefreshMode,
    PriceRefreshPlan,
    PriceRefreshSource,
)


@dataclass(frozen=True)
class PriceRefreshTerminalCompletion:
    outcome: PriceRefreshOutcome
    finalization: PriceRefreshFinalization


def build_terminal_completion(
    *,
    mode: PriceRefreshMode,
    effective_market: str,
    plan: PriceRefreshPlan,
    last_completed_trading_day: Callable[[str], Any],
) -> PriceRefreshTerminalCompletion | None:
    if plan.symbols:
        return None
    if plan.source is PriceRefreshSource.GITHUB:
        message = (
            plan.completion_message
            or "GitHub daily price bundle is current - no live fetch needed"
        )
        symbol_count = len(plan.all_symbols)
        trading_day = _completion_trading_day(
            plan.github_seed,
            effective_market,
            last_completed_trading_day=last_completed_trading_day,
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
        message = _empty_refresh_message(plan, mode)
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
            source=plan.source,
            mode=mode,
            message=message,
            github_seed=plan.github_seed,
        ),
        finalization=finalization,
    )


def _completion_trading_day(
    github_seed: GitHubSeedOutcome | None,
    effective_market: str,
    *,
    last_completed_trading_day: Callable[[str], Any],
) -> Any:
    if github_seed and github_seed.as_of_date is not None:
        return github_seed.as_of_date
    return last_completed_trading_day(effective_market)


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
