"""History providers for Relative Rotation Graph inputs."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from typing import Any, Protocol, Sequence, Tuple

from sqlalchemy.exc import SQLAlchemyError


RRGHistoryResult = Tuple[
    str | None,
    dict[str, dict[str, Any]],
    dict[str, list[tuple[date, float, int]]],
]


class RRGHistoryProvider(Protocol):
    """Source RRG-ready group-ranking history for one market."""

    def get_all_groups_history(
        self,
        db: Any,
        *,
        market: str,
        days: int,
    ) -> RRGHistoryResult:
        """Return latest date, current ranking metadata, and daily RS series."""


class USGroupRankHistoryProvider:
    """Read US RRG history from persisted IBD group-rank rows."""

    def __init__(self, group_rank_service: Any) -> None:
        self._group_rank_service = group_rank_service

    def get_all_groups_history(
        self,
        db: Any,
        *,
        market: str,
        days: int,
    ) -> RRGHistoryResult:
        from datetime import date as _date

        from app.models.industry import IBDGroupRank

        current = self._group_rank_service.get_current_rankings(
            db,
            limit=197,
            market=market,
        )
        if not current:
            return None, {}, {}

        latest_date = current[0]["date"]
        meta = {row["industry_group"]: row for row in current}
        cutoff = _date.fromisoformat(latest_date) - timedelta(days=days)
        rows = (
            db.query(
                IBDGroupRank.industry_group,
                IBDGroupRank.date,
                IBDGroupRank.avg_rs_rating,
                IBDGroupRank.num_stocks,
            )
            .filter(IBDGroupRank.market == market, IBDGroupRank.date >= cutoff)
            .order_by(IBDGroupRank.industry_group, IBDGroupRank.date)
            .all()
        )
        return latest_date, meta, _collect_group_series(rows)


class CachedFeatureRunRRGHistoryProvider:
    """Materialize non-US RRG history from published feature-run snapshots."""

    def __init__(
        self,
        market_group_ranking_service: Any,
        *,
        max_cache_entries: int = 32,
    ) -> None:
        self._market_group_ranking_service = market_group_ranking_service
        self._max_cache_entries = max_cache_entries
        self._cache: OrderedDict[tuple[int, str, int, int], RRGHistoryResult] = OrderedDict()

    def get_all_groups_history(
        self,
        db: Any,
        *,
        market: str,
        days: int,
    ) -> RRGHistoryResult:
        normalized_market = str(market or "").strip().upper()
        latest_run = self._market_group_ranking_service._get_latest_published_run(
            db,
            market=normalized_market,
        )
        if latest_run is None:
            return None, {}, {}

        cache_key = (
            _db_bind_identity(db),
            normalized_market,
            int(days),
            int(latest_run.id),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        result = self._build_history_result(
            db,
            market=normalized_market,
            days=days,
            latest_run=latest_run,
        )
        self._cache[cache_key] = result
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_cache_entries:
            self._cache.popitem(last=False)
        return result

    def _build_history_result(
        self,
        db: Any,
        *,
        market: str,
        days: int,
        latest_run: Any,
    ) -> RRGHistoryResult:
        cutoff_date = latest_run.as_of_date - timedelta(days=days)
        market_runs = self._market_group_ranking_service._get_market_run_series(
            db,
            market=market,
            latest_run=latest_run,
            cutoff_date=cutoff_date,
            min_runs=0,
        )

        rankings_by_run: dict[int, list[dict[str, Any]]] = {}
        for run in market_runs:
            rows = self._market_group_ranking_service._load_run_rows(
                db,
                run.id,
                include_sparklines=False,
            )
            rankings_by_run[run.id] = (
                self._market_group_ranking_service.compute_group_rankings_from_rows(
                    rows,
                    ranking_date=run.as_of_date,
                )
            )

        latest_rankings = rankings_by_run.get(latest_run.id, [])
        meta = self._market_group_ranking_service._group_rank_map(latest_rankings)

        series: dict[str, list[tuple[date, float, int]]] = defaultdict(list)
        for run in reversed(market_runs):
            for ranking in rankings_by_run.get(run.id, []):
                group = ranking.get("industry_group")
                avg_rs = ranking.get("avg_rs_rating")
                if not group or avg_rs is None:
                    continue
                series[str(group)].append(
                    (
                        run.as_of_date,
                        float(avg_rs),
                        int(ranking.get("num_stocks") or 0),
                    )
                )

        return latest_run.as_of_date.isoformat(), meta, dict(series)


class MarketDispatchRRGHistoryProvider:
    """Dispatch to the market-appropriate RRG history provider."""

    def __init__(
        self,
        *,
        us_provider: RRGHistoryProvider,
        non_us_provider: RRGHistoryProvider,
        us_market: str = "US",
    ) -> None:
        self._us_provider = us_provider
        self._non_us_provider = non_us_provider
        self._us_market = us_market

    def get_all_groups_history(
        self,
        db: Any,
        *,
        market: str,
        days: int,
    ) -> RRGHistoryResult:
        provider = (
            self._us_provider
            if str(market or "").upper() == self._us_market
            else self._non_us_provider
        )
        return provider.get_all_groups_history(db, market=market, days=days)


def build_rrg_history_provider(
    *,
    group_rank_service: Any,
    market_group_ranking_service: Any,
) -> RRGHistoryProvider:
    return MarketDispatchRRGHistoryProvider(
        us_provider=USGroupRankHistoryProvider(group_rank_service),
        non_us_provider=CachedFeatureRunRRGHistoryProvider(market_group_ranking_service),
    )


def _db_bind_identity(db: Any) -> int:
    get_bind = getattr(db, "get_bind", None)
    if callable(get_bind):
        try:
            return id(get_bind())
        except SQLAlchemyError:
            return id(db)
    return id(db)


def _collect_group_series(
    rows: Sequence[Tuple[str, date, float, int | None]],
) -> dict[str, list[tuple[date, float, int]]]:
    series: dict[str, list[tuple[date, float, int]]] = defaultdict(list)
    for group, d, rs, ns in rows:
        series[group].append((d, float(rs), int(ns or 0)))
    return dict(series)


__all__ = [
    "CachedFeatureRunRRGHistoryProvider",
    "MarketDispatchRRGHistoryProvider",
    "RRGHistoryProvider",
    "RRGHistoryResult",
    "USGroupRankHistoryProvider",
    "build_rrg_history_provider",
]
