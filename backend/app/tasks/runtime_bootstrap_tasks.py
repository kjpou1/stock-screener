"""Local-default bootstrap orchestration tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from celery import chain

from ..database import SessionLocal
from ..domain.bootstrap.plan import (
    BootstrapQueueKind,
    MarketBootstrapPlan,
    build_bootstrap_plan,
)
from ..services.market_activity_service import (
    mark_current_market_activity_failed,
    mark_market_activity_failed,
)
from ..tasks.market_queues import (
    data_fetch_queue_for_market,
    market_jobs_queue_for_market,
)
from ..celery_app import celery_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessFailure:
    stage_key: str
    activity_message: str


def _bootstrap_universe_name(market: str) -> str:
    return f"market:{market.upper()}"


def _queue_for_stage(stage) -> str:
    if stage.queue_kind == BootstrapQueueKind.DATA_FETCH:
        return data_fetch_queue_for_market(stage.kwargs["market"])
    if stage.queue_kind == BootstrapQueueKind.MARKET_JOBS:
        return market_jobs_queue_for_market(stage.kwargs["market"])
    raise ValueError(f"Unsupported bootstrap queue kind: {stage.queue_kind}")


def _build_market_bootstrap_signatures(market_plan: MarketBootstrapPlan) -> list:
    from app.interfaces.tasks.feature_store_tasks import build_daily_snapshot
    from app.tasks.breadth_tasks import calculate_daily_breadth_with_gapfill
    from app.tasks.cache_tasks import smart_refresh_cache
    from app.tasks.fundamentals_tasks import refresh_all_fundamentals
    from app.tasks.group_rank_tasks import calculate_daily_group_rankings_with_gapfill
    from app.tasks.industry_tasks import load_tracked_ibd_industry_groups
    from app.tasks.universe_tasks import (
        refresh_official_market_universe,
        refresh_stock_universe,
    )

    task_by_name = {
        "refresh_stock_universe": refresh_stock_universe,
        "refresh_official_market_universe": refresh_official_market_universe,
        "load_tracked_ibd_industry_groups": load_tracked_ibd_industry_groups,
        "smart_refresh_cache": smart_refresh_cache,
        "refresh_all_fundamentals": refresh_all_fundamentals,
        "calculate_daily_breadth_with_gapfill": calculate_daily_breadth_with_gapfill,
        "calculate_daily_group_rankings_with_gapfill": calculate_daily_group_rankings_with_gapfill,
        "build_daily_snapshot": build_daily_snapshot,
    }
    return [
        task_by_name[stage.task_name]
        .si(**stage.kwargs)
        .set(queue=_queue_for_stage(stage))
        for stage in market_plan.stages
    ]


def _apply_bootstrap_workflow(workflow, errback):
    try:
        return workflow.apply_async(link_error=errback)
    except TypeError:
        return workflow.apply_async()


def _readiness_failure(market_result) -> ReadinessFailure:
    if market_result is None or not market_result.core_ready:
        return ReadinessFailure(
            stage_key="core",
            activity_message="Bootstrap core data incomplete",
        )
    return ReadinessFailure(
        stage_key="scan",
        activity_message="Bootstrap scan did not publish",
    )


def _mark_missing_readiness_failures(db, readiness, markets: list[str]) -> tuple[list[str], list[str]]:
    core_missing_markets = []
    scan_missing_markets = []
    for market in markets:
        market_code = str(market).upper()
        market_result = readiness.market_results.get(market_code) or readiness.market_results.get(market)
        failure = _readiness_failure(market_result)
        if failure.stage_key == "core":
            core_missing_markets.append(market_code)
        else:
            scan_missing_markets.append(market_code)
        mark_market_activity_failed(
            db,
            market=market_code,
            stage_key=failure.stage_key,
            lifecycle="bootstrap",
            task_name="runtime_bootstrap",
            task_id=None,
            message=failure.activity_message,
        )
    return core_missing_markets, scan_missing_markets


def _format_missing_reason(core_missing_markets: list[str], scan_missing_markets: list[str]) -> str:
    failure_reasons = []
    if core_missing_markets:
        failure_reasons.append(
            "missing core market data for: " + ", ".join(core_missing_markets)
        )
    if scan_missing_markets:
        failure_reasons.append(
            "missing published auto scans for: " + ", ".join(scan_missing_markets)
        )
    return "; ".join(failure_reasons)


def queue_local_runtime_bootstrap(*, primary_market: str, enabled_markets: Iterable[str]) -> str:
    plan = build_bootstrap_plan(
        primary_market=primary_market,
        enabled_markets=enabled_markets,
    )
    primary = plan.primary_market
    enabled = list(plan.enabled_markets)

    market_plans_by_code = {market_plan.market: market_plan for market_plan in plan.market_plans}
    primary_plan = market_plans_by_code[primary]
    primary_completion = complete_local_runtime_bootstrap.si(
        primary_market=primary,
        enabled_markets=[primary],
    ).set(queue="celery")
    primary_errback = fail_local_runtime_bootstrap.s(
        primary_market=primary,
        enabled_markets=[primary],
    ).set(queue="celery")
    primary_task = _apply_bootstrap_workflow(
        chain(*_build_market_bootstrap_signatures(primary_plan), primary_completion),
        primary_errback,
    )

    for market_plan in plan.market_plans:
        if market_plan.market == primary:
            continue
        background_completion = complete_background_market_bootstrap.si(
            market=market_plan.market,
        ).set(queue="celery")
        background_errback = fail_background_market_bootstrap.s(
            market=market_plan.market,
        ).set(queue="celery")
        _apply_bootstrap_workflow(
            chain(*_build_market_bootstrap_signatures(market_plan), background_completion),
            background_errback,
        )

    logger.info(
        "Queued local runtime bootstrap",
        extra={
            "primary_market": primary,
            "enabled_markets": enabled,
            "task_id": primary_task.id,
        },
    )
    return primary_task.id


@celery_app.task(
    name="app.tasks.runtime_bootstrap_tasks.complete_local_runtime_bootstrap",
    queue="celery",
)
def complete_local_runtime_bootstrap(primary_market: str, enabled_markets: list[str]) -> dict:
    from ..services.bootstrap_readiness_service import BootstrapReadinessService
    from ..services.runtime_preferences_service import (
        get_runtime_preferences,
        set_bootstrap_state,
    )

    db = SessionLocal()
    try:
        prefs = get_runtime_preferences(db)
        readiness = BootstrapReadinessService().evaluate(
            db,
            enabled_markets=enabled_markets,
            bootstrap_started_at=prefs.bootstrap_started_at,
        )
        missing_markets = readiness.missing_markets
        core_missing_markets, scan_missing_markets = _mark_missing_readiness_failures(
            db,
            readiness,
            missing_markets,
        )
        primary_result = readiness.market_results.get(primary_market)
        primary_ready = bool(primary_result and primary_result.ready)
        if missing_markets:
            reason = _format_missing_reason(core_missing_markets, scan_missing_markets)
            if not primary_ready:
                set_bootstrap_state(db, "failed")
                return {
                    "status": "failed",
                    "primary_market": primary_market,
                    "enabled_markets": enabled_markets,
                    "reason": reason,
                }
            set_bootstrap_state(db, "ready")
            return {
                "status": "ready_with_background_failures",
                "primary_market": primary_market,
                "enabled_markets": enabled_markets,
                "background_failed_markets": [
                    market for market in missing_markets if market != primary_market
                ],
                "reason": reason,
            }
        set_bootstrap_state(db, "ready")
    finally:
        db.close()

    logger.info(
        "Completed local runtime bootstrap",
        extra={
            "primary_market": primary_market,
            "enabled_markets": enabled_markets,
        },
    )
    return {
        "status": "ready",
        "primary_market": primary_market,
        "enabled_markets": enabled_markets,
    }


@celery_app.task(
    name="app.tasks.runtime_bootstrap_tasks.complete_background_market_bootstrap",
    queue="celery",
)
def complete_background_market_bootstrap(market: str) -> dict:
    from ..services.bootstrap_readiness_service import BootstrapReadinessService
    from ..services.runtime_preferences_service import get_runtime_preferences

    db = SessionLocal()
    try:
        prefs = get_runtime_preferences(db)
        readiness = BootstrapReadinessService().evaluate(
            db,
            enabled_markets=[market],
            bootstrap_started_at=prefs.bootstrap_started_at,
        )
        market_result = readiness.market_results.get(str(market).upper())
        if market_result and market_result.ready:
            return {
                "status": "ready",
                "market": str(market).upper(),
            }
        missing_markets = readiness.missing_markets or [str(market).upper()]
        core_missing_markets, scan_missing_markets = _mark_missing_readiness_failures(
            db,
            readiness,
            missing_markets,
        )
        reason = _format_missing_reason(core_missing_markets, scan_missing_markets)
        if scan_missing_markets == [str(market).upper()] and not core_missing_markets:
            reason = "missing published auto scan"
        elif core_missing_markets == [str(market).upper()] and not scan_missing_markets:
            reason = "missing core market data"
        return {
            "status": "failed",
            "market": str(market).upper(),
            "reason": reason,
        }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.runtime_bootstrap_tasks.fail_local_runtime_bootstrap",
    queue="celery",
)
def fail_local_runtime_bootstrap(*args, primary_market: str, enabled_markets: list[str], **kwargs) -> dict:
    from ..services.runtime_preferences_service import set_bootstrap_state

    db = SessionLocal()
    try:
        set_bootstrap_state(db, "failed")
        for market in enabled_markets:
            mark_current_market_activity_failed(
                db,
                market=str(market).upper(),
                lifecycle="bootstrap",
                message="Bootstrap failed",
            )
    finally:
        db.close()

    logger.warning(
        "Marked local runtime bootstrap failed",
        extra={
            "primary_market": primary_market,
            "enabled_markets": enabled_markets,
            "callback_args": args,
            "callback_kwargs": kwargs,
        },
    )
    return {
        "status": "failed",
        "primary_market": primary_market,
        "enabled_markets": enabled_markets,
    }


@celery_app.task(
    name="app.tasks.runtime_bootstrap_tasks.fail_background_market_bootstrap",
    queue="celery",
)
def fail_background_market_bootstrap(*args, market: str, **kwargs) -> dict:
    db = SessionLocal()
    try:
        mark_current_market_activity_failed(
            db,
            market=str(market).upper(),
            lifecycle="bootstrap",
            message="Bootstrap failed",
        )
    finally:
        db.close()

    logger.warning(
        "Marked background market bootstrap failed",
        extra={
            "market": market,
            "callback_args": args,
            "callback_kwargs": kwargs,
        },
    )
    return {
        "status": "failed",
        "market": str(market).upper(),
    }
