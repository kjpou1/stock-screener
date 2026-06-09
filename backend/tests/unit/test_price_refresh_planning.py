from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from app.models.stock import StockPrice


def _calendar(day: date):
    return SimpleNamespace(last_completed_trading_day=lambda _market: day)


def _seed(payload):
    from app.services.price_refresh_planning import GitHubSeedOutcome

    return GitHubSeedOutcome.from_mapping(payload)


def _planning_input(**overrides):
    from app.services.price_history_coverage import PriceHistoryCoverage
    from app.services.price_refresh_planning import PriceRefreshPlanningInput

    values = {
        "all_symbols": ("0700.HK",),
        "mode": "bootstrap",
        "effective_market": "HK",
        "target_as_of": date(2026, 6, 8),
        "coverage": PriceHistoryCoverage(stale=("0700.HK",)),
    }
    values.update(overrides)
    return PriceRefreshPlanningInput(**values)


def test_price_history_coverage_splits_fresh_stale_and_no_history(universe_session):
    from app.services.price_history_coverage import classify_price_history

    universe_session.add_all(
        [
            StockPrice(symbol="0700.HK", date=date(2026, 6, 5), close=100),
            StockPrice(symbol="0005.HK", date=date(2026, 6, 8), close=50),
        ]
    )
    universe_session.commit()

    coverage = classify_price_history(
        universe_session,
        symbols=["0700.HK", "0005.HK", "9999.HK"],
        as_of_date=date(2026, 6, 8),
    )

    assert coverage.fresh == ("0005.HK",)
    assert coverage.stale == ("0700.HK",)
    assert coverage.no_history == ("9999.HK",)


def test_bootstrap_plan_uses_stale_top_up_and_full_bootstrap_for_no_history(universe_session):
    from app.services.price_history_coverage import PriceHistoryCoverage
    from app.services.price_refresh_planning import (
        GitHubSeedOutcome,
        PriceRefreshJobKind,
        PriceRefreshSource,
        NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
        STALE_PRICE_TOP_UP_PERIOD,
        plan_price_refresh_from_input,
    )

    plan = plan_price_refresh_from_input(_planning_input(
        all_symbols=["0700.HK", "9999.HK"],
        github_seed=_seed({
            "status": "success",
            "as_of_date": "2026-06-05",
            "source_revision": "daily_prices_hk:20260605090000",
            "stale_reason": "behind expected session",
        }),
        coverage=PriceHistoryCoverage(stale=("0700.HK",), no_history=("9999.HK",)),
    ))

    assert plan.source is PriceRefreshSource.GITHUB_AND_LIVE
    assert plan.github_seed_used is True
    assert isinstance(plan.github_seed, GitHubSeedOutcome)
    assert plan.github_seed.status.value == "success"
    assert plan.symbols == ("0700.HK", "9999.HK")
    assert [(job.kind, job.symbols, job.period) for job in plan.jobs] == [
        (PriceRefreshJobKind.STALE, ("0700.HK",), STALE_PRICE_TOP_UP_PERIOD),
        (PriceRefreshJobKind.NO_HISTORY, ("9999.HK",), NO_HISTORY_PRICE_BOOTSTRAP_PERIOD),
    ]


def test_full_mode_stays_full_even_when_github_sync_result_is_available(universe_session):
    from app.services.price_refresh_planning import (
        NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
        PriceRefreshJobKind,
        PriceRefreshSource,
        plan_price_refresh_from_input,
    )

    plan = plan_price_refresh_from_input(_planning_input(
        all_symbols=["0700.HK", "9999.HK"],
        mode="full",
        github_seed=_seed({"status": "success", "as_of_date": "2026-06-08"}),
        coverage=None,
    ))

    assert plan.source is PriceRefreshSource.LIVE
    assert plan.github_seed_used is False
    assert [(job.kind, job.symbols, job.period) for job in plan.jobs] == [
        (PriceRefreshJobKind.FULL, ("0700.HK", "9999.HK"), NO_HISTORY_PRICE_BOOTSTRAP_PERIOD)
    ]


def test_current_github_bundle_classifies_history_without_a_second_missing_symbol_api(universe_session):
    from app.services.price_history_coverage import PriceHistoryCoverage
    from app.services.price_refresh_planning import (
        NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
        PriceRefreshJobKind,
        PriceRefreshSource,
        STALE_PRICE_TOP_UP_PERIOD,
        plan_price_refresh_from_input,
    )

    plan = plan_price_refresh_from_input(_planning_input(
        all_symbols=["0700.HK", "0005.HK", "9999.HK"],
        github_seed=_seed({
            "status": "success",
            "as_of_date": "2026-06-08",
            "source_revision": "daily_prices_hk:20260608090000",
        }),
        coverage=PriceHistoryCoverage(
            fresh=("0005.HK",),
            stale=("0700.HK",),
            no_history=("9999.HK",),
        ),
    ))

    assert plan.source is PriceRefreshSource.GITHUB_AND_LIVE
    assert plan.github_seed_used is True
    assert [(job.kind, job.symbols, job.period) for job in plan.jobs] == [
        (PriceRefreshJobKind.STALE, ("0700.HK",), STALE_PRICE_TOP_UP_PERIOD),
        (PriceRefreshJobKind.NO_HISTORY, ("9999.HK",), NO_HISTORY_PRICE_BOOTSTRAP_PERIOD),
    ]


def test_current_github_bundle_accepts_datetime_as_of_date(universe_session):
    from app.services.price_history_coverage import PriceHistoryCoverage
    from app.services.price_refresh_planning import PriceRefreshSource, plan_price_refresh_from_input

    plan = plan_price_refresh_from_input(_planning_input(
        all_symbols=["0700.HK"],
        github_seed=_seed({
            "status": "success",
            "as_of_date": datetime(2026, 6, 8, 9, 0),
            "source_revision": "daily_prices_hk:20260608090000",
        }),
        coverage=PriceHistoryCoverage(fresh=("0700.HK",)),
    ))

    assert plan.source is PriceRefreshSource.GITHUB
    assert plan.github_seed_used is True
    assert plan.completion_message == "GitHub daily price bundle is current - no live fetch needed"


def test_failed_github_sync_is_live_top_up_not_github_live(universe_session):
    from app.services.price_refresh_planning import (
        PriceRefreshJobKind,
        PriceRefreshSource,
        plan_price_refresh_from_input,
    )

    plan = plan_price_refresh_from_input(_planning_input(
        all_symbols=["0700.HK"],
        mode="delta",
        github_seed=_seed({"status": "missing", "reason": "not found"}),
    ))

    assert plan.source is PriceRefreshSource.LIVE
    assert plan.github_seed_used is False
    assert [(job.kind, job.symbols, job.period) for job in plan.jobs] == [
        (PriceRefreshJobKind.STALE, ("0700.HK",), "7d"),
    ]


def test_github_seed_and_plan_do_not_expose_mapping_compatibility_surface():
    from app.services.price_refresh_planning import (
        GitHubSeedOutcome,
        GitHubSeedStatus,
        PriceRefreshPlan,
    )

    seed = GitHubSeedOutcome(status=GitHubSeedStatus.SUCCESS)
    plan = PriceRefreshPlan(symbols=(), github_seed=seed)

    assert not hasattr(seed, "get")
    assert "__getitem__" not in GitHubSeedOutcome.__dict__
    assert "github_sync" not in PriceRefreshPlan.__dict__


def test_price_refresh_plan_can_be_built_from_precomputed_inputs_without_database():
    from app.services.price_history_coverage import PriceHistoryCoverage
    from app.services.price_refresh_planning import (
        PriceRefreshJobKind,
        PriceRefreshPlanningInput,
        PriceRefreshSource,
        plan_price_refresh_from_input,
    )

    plan = plan_price_refresh_from_input(
        PriceRefreshPlanningInput(
            all_symbols=("0700.HK", "0005.HK", "9999.HK"),
            mode="bootstrap",
            effective_market="HK",
            github_seed=_seed({
                "status": "success",
                "as_of_date": "2026-06-08",
                "source_revision": "daily_prices_hk:20260608090000",
            }),
            coverage=PriceHistoryCoverage(
                fresh=("0005.HK",),
                stale=("0700.HK",),
                no_history=("9999.HK",),
            ),
        )
    )

    assert plan.source is PriceRefreshSource.GITHUB_AND_LIVE
    assert plan.github_seed_used is True
    assert [(job.kind, job.symbols, job.period) for job in plan.jobs] == [
        (PriceRefreshJobKind.STALE, ("0700.HK",), "7d"),
        (PriceRefreshJobKind.NO_HISTORY, ("9999.HK",), "2y"),
    ]


def test_build_market_price_refresh_plan_owns_universe_and_github_seed(universe_session):
    from app.models.stock_universe import StockUniverse
    from app.services.price_refresh_plan_builder import build_market_price_refresh_plan
    from app.services.price_refresh_planning import (
        PriceRefreshSource,
    )

    universe_session.add_all(
        [
            StockUniverse(symbol="0700.HK", market="HK", market_cap=500),
            StockUniverse(symbol="0005.HK", market="HK", market_cap=100),
            StockUniverse(symbol="7203.T", market="JP", market_cap=300),
        ]
    )
    universe_session.add(StockPrice(symbol="0700.HK", date=date(2026, 6, 8), close=100))
    universe_session.commit()

    sync_calls = []

    plan = build_market_price_refresh_plan(
        universe_session,
        mode="bootstrap",
        market="hk",
        effective_market="HK",
        normalize_market=lambda market: str(market).upper(),
        market_calendar_service=_calendar(date(2026, 6, 8)),
        sync_github_seed=lambda db, *, market, allow_stale: (
            sync_calls.append((db, market, allow_stale))
            or {"status": "success", "as_of_date": "2026-06-08"}
        ),
    )

    assert sync_calls == [(universe_session, "HK", True)]
    assert plan.all_symbols == ("0700.HK", "0005.HK")
    assert plan.symbol_markets == {"0700.HK": "HK", "0005.HK": "HK"}
    assert plan.source is PriceRefreshSource.GITHUB_AND_LIVE
    assert plan.symbols == ("0005.HK",)
