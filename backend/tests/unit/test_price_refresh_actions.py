from __future__ import annotations

import pytest


def _preparation(*, refresh_plan, all_symbols=None):
    from app.services.price_refresh_actions import PriceRefreshPreparation

    return PriceRefreshPreparation(
        all_symbols=list(all_symbols or []),
        symbol_markets={},
        refresh_plan=refresh_plan,
    )


def test_price_refresh_action_factory_returns_no_completion_when_plan_has_symbols():
    from app.services.price_refresh_actions import PriceRefreshActionFactory
    from app.services.price_refresh_planning import PriceRefreshMode, PriceRefreshPlan

    factory = PriceRefreshActionFactory(
        last_completed_trading_day=lambda market: pytest.fail(
            f"unexpected calendar lookup for {market}"
        )
    )

    completion = factory.build_terminal_completion(
        mode=PriceRefreshMode.BOOTSTRAP,
        effective_market="JP",
        preparation=_preparation(
            refresh_plan=PriceRefreshPlan(symbols=("7203.T",)),
        ),
    )

    assert completion is None


def test_price_refresh_action_factory_returns_terminal_completion():
    from datetime import date

    from app.services.price_refresh_actions import PriceRefreshActionFactory
    from app.services.price_refresh_planning import (
        GitHubSeedOutcome,
        PriceRefreshMode,
        PriceRefreshPlan,
        PriceRefreshSource,
    )

    github_seed = GitHubSeedOutcome.from_mapping(
        {
            "status": "success",
            "as_of_date": "2026-06-08",
            "source_revision": "daily_prices_jp:20260608120000",
        }
    )
    assert github_seed is not None
    plan = PriceRefreshPlan(
        symbols=(),
        github_seed=github_seed,
        github_seed_used=True,
        completion_message="GitHub daily price bundle is current - no live fetch needed",
    )
    factory = PriceRefreshActionFactory(
        last_completed_trading_day=lambda market: date(2026, 6, 7)
    )

    completion = factory.build_terminal_completion(
        mode=PriceRefreshMode.BOOTSTRAP,
        effective_market="JP",
        preparation=_preparation(
            refresh_plan=plan,
            all_symbols=["7203.T", "9984.T"],
        ),
    )

    assert completion is not None
    assert completion.outcome.source is PriceRefreshSource.GITHUB
    assert completion.outcome.github_seed is github_seed
    assert completion.outcome.message == plan.completion_message
    assert completion.finalization.metadata_refreshed == 2
    assert completion.finalization.metadata_total == 2
    assert completion.finalization.market_success_rates == {
        "JP": (date(2026, 6, 8), 1.0),
    }
