from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.scan_result import Scan, ScanResult
from app.models.stock import StockPrice
from app.models.stock_universe import StockUniverse, UNIVERSE_STATUS_ACTIVE
from app.models.theme import ThemeAlert, ThemeMention
from app.scripts.repair_jp_alpha_universe_symbols import (
    build_jp_alpha_symbol_aliases,
    repair_jp_alpha_universe_symbols,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_build_jp_alpha_symbol_aliases_omits_ambiguous_suffixes():
    aliases = build_jp_alpha_symbol_aliases(
        ["335A.T", "335B.T", "352A.T", "7203.T"]
    )

    assert aliases == {"0352.T": "352A.T"}


def test_repair_jp_alpha_universe_symbols_renames_symbol_keyed_rows():
    session = _session()
    session.add(
        StockUniverse(
            symbol="0335.T",
            name="Mirairo Inc.",
            market="JP",
            exchange="XTKS",
            local_code="0335",
            is_active=True,
            status=UNIVERSE_STATUS_ACTIVE,
        )
    )
    session.add(StockPrice(symbol="0335.T", date=date(2026, 6, 8), close=363.0))
    session.add(
        Scan(
            scan_id="scan-jp",
            universe_type="custom",
            universe_market="JP",
            universe_symbols=["0335.T", "7203.T"],
        )
    )
    session.add(ScanResult(scan_id="scan-jp", symbol="0335.T", composite_score=90))
    session.add(
        ThemeMention(
            source_type="news",
            raw_theme="IPOs",
            canonical_theme="IPOs",
            pipeline="technical",
            tickers=["0335.T", "7203.T"],
            ticker_count=2,
        )
    )
    session.add(
        ThemeAlert(
            alert_type="new_constituent",
            title="JP IPO",
            related_tickers=["0335.T"],
        )
    )
    session.commit()

    stats = repair_jp_alpha_universe_symbols(
        session,
        candidate_symbols=["335A.T"],
        dry_run=False,
    )

    assert stats["renamed"] == 1
    assert stats["dry_run"] is False
    assert session.query(StockUniverse).filter_by(symbol="0335.T").count() == 0
    repaired = session.query(StockUniverse).filter_by(symbol="335A.T").one()
    assert repaired.local_code == "335A"
    assert session.query(StockPrice).filter_by(symbol="335A.T").count() == 1
    assert session.query(ScanResult).filter_by(symbol="335A.T").count() == 1
    assert session.query(Scan).filter_by(scan_id="scan-jp").one().universe_symbols == [
        "335A.T",
        "7203.T",
    ]
    assert session.query(ThemeMention).one().tickers == ["335A.T", "7203.T"]
    assert session.query(ThemeAlert).one().related_tickers == ["335A.T"]
