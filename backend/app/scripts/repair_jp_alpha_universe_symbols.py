"""Repair legacy zero-prefixed JP symbols when a source-backed alpha code exists.

JP alpha-code listings such as ``335A.T`` must not be represented as
``0335.T``. The repair is intentionally conservative: it only renames a
zero-prefixed numeric JP symbol when the supplied/current source data contains
exactly one matching three-digit alpha-code candidate.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

_JP_ALPHA_SYMBOL_RE = re.compile(r"^([1-9][0-9]{2})([A-Z])\.T$")
_ZERO_PREFIXED_JP_SYMBOL_RE = re.compile(r"^0[0-9]{3,4}\.T$")


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


@dataclass(frozen=True)
class SymbolColumnRepairTarget:
    model: type
    conflict_fields: tuple[str, ...] | None = None

    @property
    def table_name(self) -> str:
        return str(self.model.__tablename__)


def _default_official_jp_symbols(source_service: Any | None = None) -> list[str]:
    from app.services.official_market_universe_source_service import (
        OfficialMarketUniverseSourceService,
    )

    service = source_service or OfficialMarketUniverseSourceService()
    snapshot = service.fetch_market_snapshot("JP")
    symbols: list[str] = []
    for row in snapshot.rows:
        raw_symbol = _normalize_symbol(row.get("symbol"))
        if not raw_symbol:
            continue
        symbols.append(raw_symbol if raw_symbol.endswith(".T") else f"{raw_symbol}.T")
    return symbols


def _candidate_symbols_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        normalized_fields = {field.strip().lower(): field for field in reader.fieldnames}
        symbol_field = (
            normalized_fields.get("symbol")
            or normalized_fields.get("local_code")
            or normalized_fields.get("ticker")
            or reader.fieldnames[0]
        )
        symbols: list[str] = []
        for row in reader:
            raw_symbol = _normalize_symbol(row.get(symbol_field))
            if not raw_symbol:
                continue
            symbols.append(raw_symbol if raw_symbol.endswith(".T") else f"{raw_symbol}.T")
        return symbols


def build_jp_alpha_symbol_aliases(
    candidate_symbols: Iterable[str],
) -> dict[str, str]:
    """Return unambiguous ``0335.T -> 335A.T`` aliases from source candidates."""
    targets_by_alias: dict[str, set[str]] = {}
    for symbol in candidate_symbols:
        normalized = _normalize_symbol(symbol)
        match = _JP_ALPHA_SYMBOL_RE.fullmatch(normalized)
        if match is None:
            continue
        digits, _suffix = match.groups()
        alias = f"0{digits}.T"
        targets_by_alias.setdefault(alias, set()).add(normalized)

    return {
        alias: next(iter(targets))
        for alias, targets in targets_by_alias.items()
        if len(targets) == 1
    }


def _iter_zero_prefixed_jp_symbols(db: Session) -> list[str]:
    from app.models.stock_universe import StockUniverse

    rows = (
        db.query(StockUniverse.symbol)
        .filter(StockUniverse.market == "JP")
        .order_by(StockUniverse.symbol.asc())
        .all()
    )
    return [
        _normalize_symbol(symbol)
        for symbol, in rows
        if _ZERO_PREFIXED_JP_SYMBOL_RE.fullmatch(_normalize_symbol(symbol))
    ]


def _replace_symbol_in_sequence(value: object, old_symbol: str, new_symbol: str) -> object:
    if not isinstance(value, list):
        return value
    changed = False
    updated: list[object] = []
    for item in value:
        if _normalize_symbol(item) == old_symbol:
            updated.append(new_symbol)
            changed = True
        else:
            updated.append(item)
    return updated if changed else value


def _symbol_column_targets() -> tuple[SymbolColumnRepairTarget, ...]:
    from app.models.industry import IBDIndustryGroup
    from app.models.institutional_ownership import InstitutionalOwnershipHistory
    from app.models.market_scan import ScanWatchlist
    from app.models.provider_snapshot import ProviderSnapshotRow
    from app.models.scan_result import ScanResult
    from app.models.stock import (
        StockFundamental,
        StockIndustry,
        StockPrice,
        StockTechnical,
    )
    from app.models.stock_universe import (
        StockUniverse,
        StockUniverseIndexMembership,
        StockUniverseStatusEvent,
    )
    from app.models.theme import ThemeConstituent
    from app.models.ticker_validation import TickerValidationLog
    from app.models.user_watchlist import WatchlistItem
    from app.models.watchlist import Watchlist

    return (
        SymbolColumnRepairTarget(StockUniverse, conflict_fields=()),
        SymbolColumnRepairTarget(StockUniverseStatusEvent),
        SymbolColumnRepairTarget(
            StockUniverseIndexMembership,
            conflict_fields=("index_name",),
        ),
        SymbolColumnRepairTarget(StockPrice, conflict_fields=("date",)),
        SymbolColumnRepairTarget(StockFundamental, conflict_fields=()),
        SymbolColumnRepairTarget(StockTechnical, conflict_fields=()),
        SymbolColumnRepairTarget(StockIndustry, conflict_fields=()),
        SymbolColumnRepairTarget(IBDIndustryGroup, conflict_fields=()),
        SymbolColumnRepairTarget(ProviderSnapshotRow, conflict_fields=("run_id",)),
        SymbolColumnRepairTarget(ScanResult),
        SymbolColumnRepairTarget(ThemeConstituent, conflict_fields=("theme_cluster_id",)),
        SymbolColumnRepairTarget(Watchlist, conflict_fields=()),
        SymbolColumnRepairTarget(WatchlistItem, conflict_fields=("watchlist_id",)),
        SymbolColumnRepairTarget(ScanWatchlist, conflict_fields=("list_name",)),
        SymbolColumnRepairTarget(TickerValidationLog),
        SymbolColumnRepairTarget(InstitutionalOwnershipHistory),
    )


def _target_has_symbol_conflict(
    db: Session,
    target: SymbolColumnRepairTarget,
    old_symbol: str,
    new_symbol: str,
) -> bool:
    if target.conflict_fields is None:
        return False

    symbol_column = getattr(target.model, "symbol")
    old_rows = db.scalars(
        select(target.model).where(symbol_column == old_symbol)
    ).all()
    if not old_rows:
        return False

    if not target.conflict_fields:
        return (
            db.query(target.model)
            .filter(symbol_column == new_symbol)
            .first()
            is not None
        )

    for old_row in old_rows:
        filters = [symbol_column == new_symbol]
        for field_name in target.conflict_fields:
            filters.append(
                getattr(target.model, field_name) == getattr(old_row, field_name)
            )
        if db.query(target.model).filter(*filters).first() is not None:
            return True
    return False


def _symbol_column_conflict_table(
    db: Session,
    old_symbol: str,
    new_symbol: str,
) -> str | None:
    for target in _symbol_column_targets():
        if _target_has_symbol_conflict(db, target, old_symbol, new_symbol):
            return target.table_name
    return None


def _update_symbol_columns(db: Session, old_symbol: str, new_symbol: str) -> dict[str, int]:
    table_counts: dict[str, int] = {}
    for target in _symbol_column_targets():
        table = target.model.__table__
        result = db.execute(
            update(table)
            .where(table.c.symbol == old_symbol)
            .values(symbol=new_symbol)
        )
        if result.rowcount:
            table_counts[table.name] = int(result.rowcount)
    return table_counts


def _update_symbol_json_arrays(
    db: Session,
    old_symbol: str,
    new_symbol: str,
) -> dict[str, int]:
    from app.models.scan_result import Scan
    from app.models.theme import ThemeAlert, ThemeMention

    specs = (
        (Scan, "universe_symbols"),
        (ThemeMention, "tickers"),
        (ThemeAlert, "related_tickers"),
    )
    updates: dict[str, int] = {}
    for model, attr_name in specs:
        changed = 0
        for row in db.scalars(select(model)).all():
            current = getattr(row, attr_name)
            updated = _replace_symbol_in_sequence(current, old_symbol, new_symbol)
            if updated is current:
                continue
            setattr(row, attr_name, updated)
            changed += 1
        if changed:
            updates[f"{model.__tablename__}.{attr_name}"] = changed
    return updates


def repair_jp_alpha_universe_symbols(
    db: Session,
    *,
    candidate_symbols: Iterable[str] | None = None,
    candidate_csv: Path | None = None,
    candidate_source_service: Any | None = None,
    dry_run: bool = True,
) -> dict[str, object]:
    """Rename existing zero-prefixed JP rows when a unique alpha candidate exists."""
    from app.models.stock_universe import StockUniverse

    if candidate_symbols is not None:
        candidates = list(candidate_symbols)
    elif candidate_csv is not None:
        candidates = []
    else:
        candidates = _default_official_jp_symbols(candidate_source_service)
    if candidate_csv is not None:
        candidates.extend(_candidate_symbols_from_csv(candidate_csv))
    aliases = build_jp_alpha_symbol_aliases(candidates)

    planned: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    table_updates: dict[str, int] = {}
    json_updates: dict[str, int] = {}

    for old_symbol in _iter_zero_prefixed_jp_symbols(db):
        new_symbol = aliases.get(old_symbol)
        if new_symbol is None:
            skipped.append({"symbol": old_symbol, "reason": "no_unique_alpha_candidate"})
            continue
        conflict_table = _symbol_column_conflict_table(db, old_symbol, new_symbol)
        if conflict_table is not None:
            skipped.append(
                {
                    "symbol": old_symbol,
                    "reason": f"target_conflict:{conflict_table}",
                    "target": new_symbol,
                }
            )
            continue

        planned.append({"from": old_symbol, "to": new_symbol})
        if dry_run:
            continue

        counts = _update_symbol_columns(db, old_symbol, new_symbol)
        for table_name, count in counts.items():
            table_updates[table_name] = table_updates.get(table_name, 0) + count
        for key, count in _update_symbol_json_arrays(db, old_symbol, new_symbol).items():
            json_updates[key] = json_updates.get(key, 0) + count

        row = db.query(StockUniverse).filter(StockUniverse.symbol == new_symbol).one_or_none()
        if row is not None:
            row.local_code = new_symbol[:-2]
            row.exchange = row.exchange or "XTKS"

    if not dry_run:
        db.commit()

    return {
        "dry_run": dry_run,
        "aliases": len(aliases),
        "planned": len(planned),
        "renamed": 0 if dry_run else len(planned),
        "skipped": skipped,
        "repairs": planned,
        "table_updates": table_updates,
        "json_updates": json_updates,
        "scan_universe_updates": json_updates.get("scans.universe_symbols", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes")
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        help="optional source CSV with symbol/local_code/ticker column",
    )
    args = parser.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        stats = repair_jp_alpha_universe_symbols(
            db,
            candidate_csv=args.candidate_csv,
            dry_run=not args.apply,
        )
        print(stats)
    finally:
        db.close()


if __name__ == "__main__":
    main()
