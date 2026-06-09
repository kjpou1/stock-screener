"""Activity publication for smart price refresh workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .price_refresh_planning import (
    GitHubSeedOutcome,
    PriceRefreshMode,
    PriceRefreshSource,
)


class CeleryTaskLike(Protocol):
    name: str
    request: Any

    def update_state(self, *args, **kwargs) -> None:
        ...


def task_name(task: CeleryTaskLike) -> str:
    return getattr(task, "name", "smart_refresh_cache")


def task_id(task: CeleryTaskLike) -> str | None:
    return getattr(getattr(task, "request", None), "id", None)


@dataclass(frozen=True)
class PriceRefreshOutcome:
    status: str
    mode: PriceRefreshMode
    source: PriceRefreshSource
    message: str | None = None
    refreshed: int = 0
    failed: int = 0
    total: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    completed_at: datetime = field(default_factory=datetime.now)
    github_seed: GitHubSeedOutcome | None = None

    def to_task_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "source": self.source.value,
            "refreshed": self.refreshed,
            "failed": self.failed,
            "total": self.total,
            "mode": self.mode.value,
            "completed_at": self.completed_at.isoformat(),
        }
        if self.github_seed is not None:
            result["github_sync_status"] = self.github_seed.status_value
            result["source_revision"] = self.github_seed.source_revision
        if self.message is not None:
            result["message"] = self.message
        if self.failed_symbols:
            result["failed_symbols"] = self.failed_symbols[:20]
        return result


@dataclass(frozen=True)
class PriceRefreshFinalization:
    metadata_status: str
    metadata_refreshed: int
    metadata_total: int
    activity_current: int
    activity_total: int
    message: str
    heartbeat_status: str | None = "completed"
    market_success_rates: Mapping[str, tuple[Any, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceRefreshActivityDependencies:
    record_market_refresh_success: Callable[..., None]
    mark_market_activity_started: Callable[..., None]
    mark_market_activity_completed: Callable[..., None]
    mark_market_activity_progress_safely: Callable[..., None]
    mark_market_activity_failed_safely: Callable[..., None]


class PriceRefreshActivityReporter:
    def __init__(self, dependencies: PriceRefreshActivityDependencies) -> None:
        self._deps = dependencies

    def start_prices(
        self,
        db,
        *,
        task: CeleryTaskLike,
        market: str,
        lifecycle: str,
        message: str,
    ) -> None:
        self._deps.mark_market_activity_started(
            db,
            market=market,
            stage_key="prices",
            lifecycle=lifecycle,
            task_name=task_name(task),
            task_id=task_id(task),
            message=message,
        )

    def publish_github_seed_fallback(
        self,
        db,
        *,
        task: CeleryTaskLike,
        market: str,
        lifecycle: str,
        total: int,
        status_value: str,
    ) -> None:
        self._deps.mark_market_activity_progress_safely(
            db,
            market=market,
            stage_key="prices",
            lifecycle=lifecycle,
            task_name=task_name(task),
            task_id=task_id(task),
            current=0,
            total=total,
            percent=0,
            message=f"GitHub price bundle {status_value}; using live price refresh",
        )

    def publish_progress(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        market: str | None,
        effective_market: str,
        lifecycle: str,
        current: int,
        total: int,
        percent: float,
        message: str,
        refreshed: int,
        failed: int,
    ) -> None:
        task.update_state(
            state="PROGRESS",
            meta={
                "current": current,
                "total": total,
                "percent": percent,
                "refreshed": refreshed,
                "failed": failed,
            },
        )
        price_cache.update_warmup_heartbeat(current, total, percent, market=market)
        self._deps.mark_market_activity_progress_safely(
            db,
            market=effective_market,
            stage_key="prices",
            lifecycle=lifecycle,
            task_name=task_name(task),
            task_id=task_id(task),
            current=current,
            total=total,
            percent=round(percent, 1),
            message=message,
        )

    def finalize_success(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        market: str | None,
        effective_market: str,
        lifecycle: str,
        finalization: PriceRefreshFinalization,
    ) -> None:
        price_cache.save_warmup_metadata(
            finalization.metadata_status,
            finalization.metadata_refreshed,
            finalization.metadata_total,
            market=market,
        )
        if finalization.heartbeat_status is not None:
            price_cache.complete_warmup_heartbeat(
                finalization.heartbeat_status,
                market=market,
            )
        self._deps.mark_market_activity_completed(
            db,
            market=effective_market,
            stage_key="prices",
            lifecycle=lifecycle,
            task_name=task_name(task),
            task_id=task_id(task),
            current=finalization.activity_current,
            total=finalization.activity_total,
            message=finalization.message,
        )
        for refresh_market, (trading_day, success_rate) in finalization.market_success_rates.items():
            self._deps.record_market_refresh_success(
                db,
                market=refresh_market,
                trading_day=trading_day,
                success_rate=success_rate,
            )

    def record_failure(
        self,
        db,
        price_cache,
        *,
        task: CeleryTaskLike,
        market: str | None,
        effective_market: str,
        lifecycle: str,
        refreshed: int,
        total: int,
        current: int,
        message: str,
    ) -> None:
        price_cache.save_warmup_metadata(
            "failed",
            refreshed,
            total,
            message,
            market=market,
        )
        price_cache.complete_warmup_heartbeat("failed", market=market)
        self._deps.mark_market_activity_failed_safely(
            db,
            market=effective_market,
            stage_key="prices",
            lifecycle=lifecycle,
            task_name=task_name(task),
            task_id=task_id(task),
            current=current,
            total=total,
            message=message,
        )
