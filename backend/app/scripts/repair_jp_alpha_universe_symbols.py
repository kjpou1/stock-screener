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
from pathlib import Path
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

_JP_ALPHA_SYMBOL_RE = re.compile(r"^([1-9][0-9]{2})([A-Z])\.T$")
_ZERO_PREFIXED_JP_SYMBOL_RE = re.compile(r"^0[0-9]{3,4}\.T$")


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _default_kabutan_symbols() -> list[str]:
    from app.services.market_taxonomy_service import MarketTaxonomyService

    data_dir = MarketTaxonomyService._default_data_dir()
    path = data_dir / "kabutan_themes_en.csv"
    if not path.exists():
        return []

    symbols: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            market_label = _normalize_symbol(row.get("Market (EN)"))
            if market_label and not market_label.startswith(("TSE", "JPX", "XTKS")):
                continue
            raw_symbol = _normalize_symbol(row.get("Symbol"))
            if not raw_symbol:
                continue
            if raw_symbol.endswith(".T"):
                symbols.append(raw_symbol)
            else:
                symbols.append(f"{raw_symbol}.T")
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


def _update_symbol_columns(db: Session, old_symbol: str, new_symbol: str) -> dict[str, int]:
    from app import models  # noqa: F401 - registers ORM tables on Base.metadata
    from app.database import Base

    table_counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        if "symbol" not in table.c:
            continue
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
    dry_run: bool = True,
) -> dict[str, object]:
    """Rename existing zero-prefixed JP rows when a unique alpha candidate exists."""
    from app.models.stock_universe import StockUniverse

    candidates = list(candidate_symbols or _default_kabutan_symbols())
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
        if (
            db.query(StockUniverse.id)
            .filter(StockUniverse.symbol == new_symbol)
            .first()
            is not None
        ):
            skipped.append({"symbol": old_symbol, "reason": f"target_exists:{new_symbol}"})
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
