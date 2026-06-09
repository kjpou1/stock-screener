"""Price refresh planning for GitHub-seeded and live market refreshes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from app.domain.providers.price_symbol_support import split_supported_price_symbols

from .price_history_coverage import PriceHistoryCoverage


STALE_PRICE_TOP_UP_PERIOD = "7d"
NO_HISTORY_PRICE_BOOTSTRAP_PERIOD = "2y"


class PriceRefreshMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    BOOTSTRAP = "bootstrap"
    DELTA = "delta"

    @classmethod
    def parse(cls, value: "PriceRefreshMode | str") -> "PriceRefreshMode":
        if isinstance(value, cls):
            return value
        return cls(str(value))


class PriceRefreshJobKind(str, Enum):
    AUTO = "auto"
    FULL = "full"
    STALE = "stale"
    NO_HISTORY = "no_history"


class PriceRefreshSource(str, Enum):
    LIVE = "live"
    GITHUB = "github"
    GITHUB_AND_LIVE = "github+live"


class GitHubSeedStatus(str, Enum):
    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"
    MISSING = "missing"
    ERROR = "error"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "GitHubSeedStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


LIVE_TOP_UP_MODES = frozenset({PriceRefreshMode.BOOTSTRAP, PriceRefreshMode.DELTA})
GITHUB_SYNC_SUCCESS_STATUSES = frozenset({
    GitHubSeedStatus.SUCCESS,
    GitHubSeedStatus.UP_TO_DATE,
})


@dataclass(frozen=True)
class GitHubSeedOutcome:
    status: GitHubSeedStatus
    raw_status: str | None = None
    as_of_date: date | None = None
    source_revision: str | None = None
    reason: str | None = None
    error: str | None = None
    stale_reason: str | None = None

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any] | None
    ) -> "GitHubSeedOutcome | None":
        if not payload:
            return None
        raw_status = str(payload.get("status")) if payload.get("status") is not None else None
        return cls(
            status=GitHubSeedStatus.parse(raw_status),
            raw_status=raw_status,
            as_of_date=_parse_bundle_date(payload.get("as_of_date")),
            source_revision=(
                str(payload["source_revision"])
                if payload.get("source_revision") is not None
                else None
            ),
            reason=str(payload["reason"]) if payload.get("reason") is not None else None,
            error=str(payload["error"]) if payload.get("error") is not None else None,
            stale_reason=(
                str(payload["stale_reason"])
                if payload.get("stale_reason") is not None
                else None
            ),
        )

    @property
    def status_value(self) -> str:
        return self.raw_status or self.status.value

    @property
    def is_success(self) -> bool:
        return self.status in GITHUB_SYNC_SUCCESS_STATUSES

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status_value}
        if self.as_of_date is not None:
            payload["as_of_date"] = self.as_of_date.isoformat()
        if self.source_revision is not None:
            payload["source_revision"] = self.source_revision
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.error is not None:
            payload["error"] = self.error
        if self.stale_reason is not None:
            payload["stale_reason"] = self.stale_reason
        return payload


@dataclass(frozen=True)
class PriceRefreshJob:
    kind: PriceRefreshJobKind
    symbols: tuple[str, ...]
    period: str


@dataclass(frozen=True)
class PriceRefreshPlan:
    symbols: tuple[str, ...]
    jobs: tuple[PriceRefreshJob, ...] = ()
    all_symbols: tuple[str, ...] = ()
    unsupported_symbols: tuple[str, ...] = ()
    symbol_markets: Mapping[str, str] = field(default_factory=dict)
    github_seed: GitHubSeedOutcome | None = None
    github_seed_used: bool = False
    completion_message: str | None = None

    @property
    def source(self) -> PriceRefreshSource:
        if self.github_seed_used:
            return PriceRefreshSource.GITHUB_AND_LIVE if self.jobs else PriceRefreshSource.GITHUB
        return PriceRefreshSource.LIVE

    @property
    def used_github_seed(self) -> bool:
        return self.github_seed_used

    @property
    def live_refresh_jobs(self) -> tuple[PriceRefreshJob, ...]:
        return self.jobs


@dataclass(frozen=True)
class PriceRefreshPlanningInput:
    all_symbols: Sequence[str]
    mode: PriceRefreshMode | str
    effective_market: str
    symbol_markets: Mapping[str, str] = field(default_factory=dict)
    github_seed: GitHubSeedOutcome | None = None
    coverage: PriceHistoryCoverage | None = None
    target_as_of: date | None = None
    auto_refresh_symbols: Sequence[str] | None = None


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(symbol).upper() for symbol in symbols)


def _split_supported_sequence(
    symbols: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    supported, unsupported = split_supported_price_symbols(list(_normalize_symbols(symbols)))
    return tuple(supported), tuple(unsupported)


def _parse_bundle_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def build_top_up_jobs(coverage: PriceHistoryCoverage) -> tuple[PriceRefreshJob, ...]:
    jobs: list[PriceRefreshJob] = []
    if coverage.stale:
        jobs.append(
            PriceRefreshJob(
                kind=PriceRefreshJobKind.STALE,
                symbols=coverage.stale,
                period=STALE_PRICE_TOP_UP_PERIOD,
            )
        )
    if coverage.no_history:
        jobs.append(
            PriceRefreshJob(
                kind=PriceRefreshJobKind.NO_HISTORY,
                symbols=coverage.no_history,
                period=NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
            )
        )
    return tuple(jobs)


def _filter_supported_jobs(
    jobs: Sequence[PriceRefreshJob],
) -> tuple[tuple[PriceRefreshJob, ...], tuple[str, ...]]:
    filtered_jobs: list[PriceRefreshJob] = []
    unsupported_symbols: list[str] = []
    for job in jobs:
        supported, unsupported = _split_supported_sequence(job.symbols)
        unsupported_symbols.extend(unsupported)
        if supported:
            filtered_jobs.append(replace(job, symbols=supported))
    return tuple(filtered_jobs), tuple(unsupported_symbols)


def _symbols_from_jobs(jobs: Sequence[PriceRefreshJob]) -> tuple[str, ...]:
    return tuple(symbol for job in jobs for symbol in job.symbols)


def _plan_live_full(symbols: tuple[str, ...]) -> PriceRefreshPlan:
    supported, unsupported = _split_supported_sequence(symbols)
    jobs = (
        PriceRefreshJob(
            kind=PriceRefreshJobKind.FULL,
            symbols=supported,
            period=NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
        ),
    ) if supported else ()
    return PriceRefreshPlan(symbols=supported, jobs=jobs, unsupported_symbols=unsupported)


def _plan_live_auto(symbols: tuple[str, ...]) -> PriceRefreshPlan:
    supported, unsupported = _split_supported_sequence(symbols)
    jobs = (
        PriceRefreshJob(
            kind=PriceRefreshJobKind.AUTO,
            symbols=supported,
            period=NO_HISTORY_PRICE_BOOTSTRAP_PERIOD,
        ),
    ) if supported else ()
    return PriceRefreshPlan(symbols=supported, jobs=jobs, unsupported_symbols=unsupported)


def _plan_live_top_up(
    coverage: PriceHistoryCoverage,
    *,
    github_seed: GitHubSeedOutcome | None = None,
) -> PriceRefreshPlan:
    jobs, unsupported = _filter_supported_jobs(build_top_up_jobs(coverage))
    return PriceRefreshPlan(
        symbols=_symbols_from_jobs(jobs),
        jobs=jobs,
        github_seed=github_seed,
        unsupported_symbols=unsupported,
    )


def _plan_github_top_up(
    coverage: PriceHistoryCoverage,
    *,
    github_seed: GitHubSeedOutcome,
    target_as_of: date | None,
) -> PriceRefreshPlan:
    github_as_of = github_seed.as_of_date
    jobs, unsupported = _filter_supported_jobs(build_top_up_jobs(coverage))
    live_symbols = _symbols_from_jobs(jobs)
    completion_message = None
    if not live_symbols:
        completion_message = (
            "GitHub daily price bundle is current - no live fetch needed"
            if github_as_of == target_as_of
            else "All symbols already fresh - no live fetch needed"
        )
    return PriceRefreshPlan(
        symbols=live_symbols,
        jobs=jobs,
        github_seed=github_seed,
        github_seed_used=True,
        unsupported_symbols=unsupported,
        completion_message=completion_message,
    )


def plan_price_refresh_from_input(
    planning_input: PriceRefreshPlanningInput,
) -> PriceRefreshPlan:
    """Plan price-fetch work from precomputed, side-effect-free inputs."""
    parsed_mode = PriceRefreshMode.parse(planning_input.mode)
    normalized_symbols = _normalize_symbols(planning_input.all_symbols)
    if not normalized_symbols:
        return PriceRefreshPlan(
            symbols=(),
            jobs=(),
            completion_message="No active symbols found in universe",
        )

    if parsed_mode is PriceRefreshMode.AUTO:
        plan = _plan_live_auto(
            _normalize_symbols(planning_input.auto_refresh_symbols)
            if planning_input.auto_refresh_symbols is not None
            else normalized_symbols
        )
    elif parsed_mode is PriceRefreshMode.FULL:
        plan = _plan_live_full(normalized_symbols)
    elif planning_input.github_seed and planning_input.github_seed.is_success:
        plan = _plan_github_top_up(
            _require_coverage(planning_input),
            github_seed=planning_input.github_seed,
            target_as_of=planning_input.target_as_of,
        )
    else:
        plan = _plan_live_top_up(
            _require_coverage(planning_input),
            github_seed=planning_input.github_seed,
        )

    return replace(
        plan,
        all_symbols=normalized_symbols,
        symbol_markets=dict(planning_input.symbol_markets),
    )


def _require_coverage(planning_input: PriceRefreshPlanningInput) -> PriceHistoryCoverage:
    if planning_input.coverage is None:
        raise ValueError("price refresh top-up planning requires precomputed coverage")
    return planning_input.coverage
