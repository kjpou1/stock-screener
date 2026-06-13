from __future__ import annotations

from app.wiring.bootstrap import build_runtime_services


def test_runtime_services_reset_for_tests_clears_github_bootstrap_services():
    runtime = build_runtime_services()

    market_calendar = runtime.market_calendar_service()
    github_sync = runtime.github_release_sync_service()
    daily_price_bundle = runtime.daily_price_bundle_service()

    runtime.reset_for_tests()

    assert runtime._market_calendar_service is None
    assert runtime._github_release_sync_service is None
    assert runtime._daily_price_bundle_service is None

    assert runtime.market_calendar_service() is not market_calendar
    assert runtime.github_release_sync_service() is not github_sync
    assert runtime.daily_price_bundle_service() is not daily_price_bundle


def test_runtime_services_reuses_rrg_service_for_process_lifetime(monkeypatch):
    runtime = build_runtime_services()

    monkeypatch.setattr(runtime, "group_rank_service", lambda: object())
    monkeypatch.setattr(
        "app.services.market_group_ranking_service.get_market_group_ranking_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.market_taxonomy_service.get_market_taxonomy_service",
        lambda: object(),
    )

    rrg_service = runtime.rrg_service()

    assert runtime.rrg_service() is rrg_service

    runtime.reset_for_tests()

    assert runtime.rrg_service() is not rrg_service
