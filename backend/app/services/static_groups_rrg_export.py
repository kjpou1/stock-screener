"""Static-site RRG payload builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.domain.markets.catalog import get_market_catalog
from app.infra.db.models.feature_store import FeatureRun, StockFeatureDaily
from app.models.industry import IBDGroupRank, IBDIndustryGroup
from app.models.stock import StockFundamental
from app.models.stock_universe import StockUniverse
from app.services.market_group_ranking_service import get_market_group_ranking_service
from app.services.market_taxonomy_service import get_market_taxonomy_service
from app.services.rrg_history_provider import build_rrg_history_provider
from app.services.rrg_service import RRGService
from app.wiring.bootstrap import get_group_rank_service


class StaticGroupsRRGUnavailableError(RuntimeError):
    """Raised when static RRG cannot be exported from the current DB."""

    def __init__(self, *, section: str, reason: str) -> None:
        self.section = section
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class StaticGroupsRRGPayloadBuilder:
    """Build the offline RRG bundle consumed by static group-ranking pages."""

    schema_version: str
    rrg_service: RRGService
    market_catalog: Any = field(default_factory=get_market_catalog)

    @classmethod
    def from_runtime_services(
        cls,
        *,
        schema_version: str,
    ) -> "StaticGroupsRRGPayloadBuilder":
        return cls(
            schema_version=schema_version,
            rrg_service=RRGService(
                history_provider=build_rrg_history_provider(
                    group_rank_service=get_group_rank_service(),
                    market_group_ranking_service=get_market_group_ranking_service(),
                ),
                taxonomy_service=get_market_taxonomy_service(),
            ),
        )

    def build(
        self,
        *,
        db: Any,
        generated_at: str,
        expected_as_of_date: date,
        market: str,
    ) -> dict[str, Any]:
        normalized_market = str(market or "US").strip().upper()
        self._preflight_tables(db, normalized_market)

        try:
            scopes = self.rrg_service.get_rrg_scopes(
                db,
                market=normalized_market,
                scopes=("groups", "sectors"),
            )
        except SQLAlchemyError as exc:
            if not _is_missing_table_sql_error(exc):
                raise
            raise StaticGroupsRRGUnavailableError(
                section=f"{normalized_market} rrg",
                reason="RRG source tables are unavailable for this export database.",
            ) from exc

        groups_rrg = scopes["groups"]
        sectors_rrg = scopes["sectors"]
        available_scopes = [
            scope for scope in ("groups", "sectors")
            if scopes.get(scope, {}).get("groups")
        ]

        if not groups_rrg.get("groups"):
            raise StaticGroupsRRGUnavailableError(
                section=f"{normalized_market} rrg",
                reason=(
                    "No RRG data could be computed (group-rank history is too "
                    "short or absent for this market)."
                ),
            )

        return {
            "schema_version": self.schema_version,
            "generated_at": generated_at,
            "available": True,
            "market": normalized_market,
            "as_of_date": groups_rrg.get("date") or expected_as_of_date.isoformat(),
            "available_scopes": available_scopes,
            "payload": {
                "groups": groups_rrg,
                "sectors": sectors_rrg,
            },
        }

    def _preflight_tables(self, db: Any, market: str) -> None:
        missing_tables = _missing_required_tables(
            db,
            self._required_table_names(market),
        )
        if missing_tables:
            raise StaticGroupsRRGUnavailableError(
                section=f"{market} rrg",
                reason=(
                    "RRG source tables are unavailable for this export database: "
                    f"{', '.join(missing_tables)}."
                ),
            )

    def _required_table_names(self, market: str) -> tuple[str, ...]:
        if market == "US":
            table_names = {IBDGroupRank.__tablename__}
            if "sectors" in self.market_catalog.rrg_scopes_for_market(market):
                table_names.update(
                    {
                        IBDIndustryGroup.__tablename__,
                        StockUniverse.__tablename__,
                    }
                )
            return tuple(sorted(table_names))
        return tuple(
            sorted(
                {
                    FeatureRun.__tablename__,
                    StockFeatureDaily.__tablename__,
                    StockFundamental.__tablename__,
                    StockUniverse.__tablename__,
                }
            )
        )


def _missing_required_tables(db: Any, table_names: tuple[str, ...]) -> list[str]:
    bind = db.get_bind() if callable(getattr(db, "get_bind", None)) else db
    inspector = inspect(bind)
    return [
        table_name for table_name in table_names
        if not inspector.has_table(table_name)
    ]


def _is_missing_table_sql_error(exc: SQLAlchemyError) -> bool:
    parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    message = " ".join(parts).lower()
    return any(
        marker in message
        for marker in (
            "no such table",
            "does not exist",
            "undefined table",
            "unknown table",
        )
    )


__all__ = [
    "StaticGroupsRRGUnavailableError",
    "StaticGroupsRRGPayloadBuilder",
]
