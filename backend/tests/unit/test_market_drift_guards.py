"""Cross-layer drift guards for duplicated Market facts.

These tests intentionally document today's compatibility drift instead of
changing runtime behavior. Later harmonization tasks should remove the
exception dictionaries as each consumer moves behind Market Catalog.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.api.v1 import breadth, groups
from app.domain.markets import SUPPORTED_MARKET_CODES, market_registry
from app.domain.markets.catalog import get_market_catalog
from app.models.stock_universe import StockUniverse, UNIVERSE_STATUS_ACTIVE
from app.services import provider_routing_policy
from app.services.field_capability_registry import field_capability_registry
from app.services.security_master_service import SecurityMasterResolver
from app.tasks import market_queues


REPO_ROOT = Path(__file__).resolve().parents[2].parent

DOCUMENTED_CATALOG_MIC_CODES: dict[str, set[str]] = {
    "US": set(),
    "HK": {"XHKG"},
    "IN": {"XNSE", "XBOM"},
    "JP": {"XTKS"},
    "KR": {"XKRX"},
    "TW": {"XTAI"},
    "CN": {"XSHG", "XSHE", "XBSE"},
    "CA": {"XTSE", "XTNX"},
    "DE": {"XETR", "XFRA"},
    "SG": {"XSES"},
}

DOCUMENTED_CATALOG_ALIAS_CODES: dict[str, set[str]] = {
    "US": {"NYSE", "NASDAQ", "AMEX"},
    "HK": {"HKEX", "SEHK"},
    "IN": {"NSE", "BSE"},
    "JP": {"TSE", "JPX"},
    "KR": {"KOSPI", "KOSDAQ", "KRX"},
    "TW": {"TWSE", "TPEX"},
    "CN": {"SSE", "SZSE", "BJSE"},
    "CA": {"TSX", "TSXV"},
    "DE": {"XETRA", "FRA", "FWB"},
    "SG": {"SGX", "SES"},
}

DOCUMENTED_CATALOG_CANONICAL_MICS: dict[str, tuple[str, ...]] = {
    "US": ("XNYS", "XNAS", "XASE"),
    "HK": ("XHKG",),
    "IN": ("XNSE", "XBOM"),
    "JP": ("XTKS",),
    "KR": ("XKRX",),
    "TW": ("XTAI",),
    "CN": ("XSHG", "XSHE", "XBSE"),
    "CA": ("XTSE", "XTNX"),
    "DE": ("XETR", "XFRA"),
    "SG": ("XSES",),
}

DOCUMENTED_REGISTRY_ONLY_EXCHANGE_ALIASES: dict[str, set[str]] = {
    "US": {"XNYS", "XNAS", "XASE"},
    "CN": {"SHSE", "XBEI"},
}

DOCUMENTED_CATALOG_ONLY_EXCHANGE_ALIASES: dict[str, set[str]] = {}


def _catalog_codes() -> list[str]:
    return get_market_catalog().supported_market_codes()


def _catalog_market_codes_by_capability(capability: str) -> set[str]:
    catalog = get_market_catalog()
    return {
        code
        for code in catalog.supported_market_codes()
        if getattr(catalog.get(code).capabilities, capability)
    }


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _active_universe_currency_drift(db) -> list[dict[str, object]]:
    catalog = get_market_catalog()
    drift: list[dict[str, object]] = []
    rows = (
        db.query(StockUniverse.symbol, StockUniverse.market, StockUniverse.currency)
        .filter(StockUniverse.is_active.is_(True))
        .all()
    )
    for symbol, market, currency in rows:
        market_code = str(market or "").strip().upper()
        row_currency = str(currency or "").strip().upper()
        entry = catalog.get(market_code)
        if row_currency not in entry.supported_currencies:
            drift.append(
                {
                    "symbol": symbol,
                    "market": market_code,
                    "currency": row_currency,
                    "supported_currencies": entry.supported_currencies,
                }
            )
    return drift


def _active_universe_timezone_drift(db) -> list[dict[str, object]]:
    catalog = get_market_catalog()
    drift: list[dict[str, object]] = []
    rows = (
        db.query(
            StockUniverse.symbol,
            StockUniverse.market,
            StockUniverse.exchange,
            StockUniverse.timezone,
        )
        .filter(StockUniverse.is_active.is_(True))
        .all()
    )
    for symbol, market, mic, timezone in rows:
        market_code = str(market or "").strip().upper()
        row_mic = str(mic or "").strip().upper()
        expected_timezone = catalog.get(market_code).mic_facts_for(row_mic).timezone
        if timezone != expected_timezone:
            drift.append(
                {
                    "symbol": symbol,
                    "market": market_code,
                    "mic": row_mic,
                    "timezone": timezone,
                    "expected_timezone": expected_timezone,
                }
            )
    return drift


def _fallback_catalog_codes_from_frontend() -> list[str]:
    # Phase 0 only guards fallback code drift. A generated/structured frontend
    # catalog fixture belongs with the later frontend contract standardization
    # work, when fallback data is removed or generated from backend facts.
    runtime_context = REPO_ROOT / "frontend" / "src" / "contexts" / "RuntimeContext.jsx"
    source = runtime_context.read_text()
    fallback_catalog = source.split(
        "export const DEFAULT_MARKET_CATALOG = {", maxsplit=1
    )[1].split("const DEFAULT_SUPPORTED_MARKETS", maxsplit=1)[0]
    return re.findall(r"code:\s*'([A-Z]{2})'", fallback_catalog)


def test_supported_market_code_surfaces_match_catalog_codes() -> None:
    expected = set(_catalog_codes())

    assert SUPPORTED_MARKET_CODES == expected
    assert set(market_registry.supported_market_codes()) == expected
    assert set(market_queues.SUPPORTED_MARKETS) == expected
    assert set(provider_routing_policy.KNOWN_MARKETS) == expected
    assert set(provider_routing_policy.supported_markets()) == expected
    assert set(field_capability_registry.MARKET_ORDER) == expected


def test_runtime_order_surfaces_match_catalog_order() -> None:
    catalog_order = tuple(_catalog_codes())

    assert market_registry.supported_market_codes() == catalog_order
    assert market_queues.SUPPORTED_MARKETS == catalog_order


def test_catalog_exchange_codes_are_documented_as_mics_or_compatibility_aliases() -> None:
    catalog = get_market_catalog()

    for code in catalog.supported_market_codes():
        documented_codes = (
            DOCUMENTED_CATALOG_MIC_CODES.get(code, set())
            | DOCUMENTED_CATALOG_ALIAS_CODES.get(code, set())
        )
        assert set(catalog.get(code).exchanges) == documented_codes


def test_catalog_canonical_mics_are_documented_and_have_facts() -> None:
    catalog = get_market_catalog()

    for code in catalog.supported_market_codes():
        entry = catalog.get(code)
        assert entry.mics == DOCUMENTED_CATALOG_CANONICAL_MICS[code]
        assert entry.primary_mic == entry.mics[0]
        assert {facts.mic for facts in entry.mic_facts} == set(entry.mics)
        assert entry.default_currency in entry.supported_currencies


def test_active_universe_row_currencies_are_catalog_supported() -> None:
    db = _make_session()
    db.add_all(
        [
            StockUniverse(
                symbol="0700.HK",
                market="HK",
                exchange="XHKG",
                currency="HKD",
                timezone="Asia/Hong_Kong",
                is_active=True,
                status=UNIVERSE_STATUS_ACTIVE,
            ),
            StockUniverse(
                symbol="SAP.DE",
                market="DE",
                exchange="XETR",
                currency="EUR",
                timezone="Europe/Berlin",
                is_active=True,
                status=UNIVERSE_STATUS_ACTIVE,
            ),
        ]
    )
    db.commit()

    assert _active_universe_currency_drift(db) == []
    db.close()


def test_active_universe_currency_drift_reports_unsupported_currency() -> None:
    db = _make_session()
    db.add(
        StockUniverse(
            symbol="BAD.HK",
            market="HK",
            exchange="XHKG",
            currency="EUR",
            timezone="Asia/Hong_Kong",
            is_active=True,
            status=UNIVERSE_STATUS_ACTIVE,
        )
    )
    db.commit()

    assert _active_universe_currency_drift(db) == [
        {
            "symbol": "BAD.HK",
            "market": "HK",
            "currency": "EUR",
            "supported_currencies": ("HKD",),
        }
    ]
    db.close()


def test_active_universe_timezone_drift_reports_mic_mismatch() -> None:
    db = _make_session()
    db.add_all(
        [
            StockUniverse(
                symbol="0700.HK",
                market="HK",
                exchange="XHKG",
                currency="HKD",
                timezone="Asia/Hong_Kong",
                is_active=True,
                status=UNIVERSE_STATUS_ACTIVE,
            ),
            StockUniverse(
                symbol="BAD.SI",
                market="SG",
                exchange="XSES",
                currency="SGD",
                timezone="Asia/Hong_Kong",
                is_active=True,
                status=UNIVERSE_STATUS_ACTIVE,
            ),
        ]
    )
    db.commit()

    assert _active_universe_timezone_drift(db) == [
        {
            "symbol": "BAD.SI",
            "market": "SG",
            "mic": "XSES",
            "timezone": "Asia/Hong_Kong",
            "expected_timezone": "Asia/Singapore",
        }
    ]
    db.close()


def test_catalog_and_registry_exchange_alias_drift_is_documented() -> None:
    catalog = get_market_catalog()

    for code in catalog.supported_market_codes():
        catalog_exchanges = set(catalog.get(code).exchanges)
        registry_exchanges = set(market_registry.profile(code).exchanges)

        assert catalog_exchanges - registry_exchanges == (
            DOCUMENTED_CATALOG_ONLY_EXCHANGE_ALIASES.get(code, set())
        )
        assert registry_exchanges - catalog_exchanges == (
            DOCUMENTED_REGISTRY_ONLY_EXCHANGE_ALIASES.get(code, set())
        )


def test_bse_alias_ambiguity_is_documented_with_market_context() -> None:
    resolver = SecurityMasterResolver()

    india_bse = resolver.resolve_identity(symbol="500325", market="IN", exchange="BSE")
    china_bse = resolver.resolve_identity(symbol="920118", market="CN", exchange="BSE")

    assert india_bse.market == "IN"
    assert india_bse.canonical_symbol == "500325.BO"
    assert china_bse.market == "CN"
    assert china_bse.canonical_symbol == "920118.BJ"


def test_frontend_fallback_catalog_codes_match_backend_catalog_codes() -> None:
    assert _fallback_catalog_codes_from_frontend() == _catalog_codes()


def test_endpoint_capability_allowlists_match_catalog_capabilities() -> None:
    assert breadth.SUPPORTED_BREADTH_MARKETS == _catalog_market_codes_by_capability(
        "breadth"
    )
    assert groups.SUPPORTED_GROUP_MARKETS == _catalog_market_codes_by_capability(
        "group_rankings"
    )
