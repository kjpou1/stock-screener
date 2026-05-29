"""Runtime Universe option payloads derived from canonical Universe definitions."""

from __future__ import annotations

from dataclasses import asdict

from ..domain.markets.catalog import CATALOG_VERSION, get_market_catalog
from ..domain.markets.mic_aliases import mic_alias_registry
from ..domain.universe.indexes import index_registry
from ..domain.universe.listing_tiers import listing_tier_registry
from ..schemas.universe import UniverseDefinition, UniverseType


def _market_universe(
    market: str,
    *,
    mic: str | None = None,
    listing_tier: str | None = None,
) -> UniverseDefinition:
    return UniverseDefinition(
        type=UniverseType.MARKET,
        market=market,
        mic=mic,
        listing_tier=listing_tier,
    )


def _index_universe(index: str) -> UniverseDefinition:
    return UniverseDefinition(type=UniverseType.INDEX, index=index)


def _selection(label: str, universe_def: UniverseDefinition) -> dict[str, object]:
    return {
        "value": universe_def.key(),
        "label": label,
        "universe_def": universe_def,
    }


def build_runtime_universe_options_payload(
    *,
    enabled_markets: list[str],
) -> dict[str, object]:
    """Build stable Universe choices without live scan-readiness state."""
    catalog = get_market_catalog()
    enabled = {market.strip().upper() for market in enabled_markets}
    markets: list[dict[str, object]] = []
    for code in catalog.supported_market_codes():
        entry = catalog.get(code)
        mic_aliases_by_mic: dict[str, list[str]] = {mic: [] for mic in entry.mics}
        mic_alias_options: list[dict[str, object]] = []
        for alias in mic_alias_registry.aliases(code):
            resolved = mic_alias_registry.resolve(code, alias)
            if resolved is None or resolved.alias == resolved.mic:
                continue
            universe_def = _market_universe(code, mic=resolved.mic)
            mic_aliases_by_mic.setdefault(resolved.mic, []).append(resolved.alias)
            mic_alias_options.append(
                {
                    **_selection(resolved.alias, universe_def),
                    "alias": resolved.alias,
                    "mic": resolved.mic,
                }
            )

        mics = [
            {
                **_selection(
                    facts.mic,
                    _market_universe(code, mic=facts.mic),
                ),
                "mic": facts.mic,
                "aliases": mic_aliases_by_mic.get(facts.mic, []),
            }
            for facts in entry.mic_facts
        ]
        indexes = [
            {
                **_selection(
                    definition.label,
                    _index_universe(definition.key),
                ),
                "key": definition.key,
                "aliases": list(definition.aliases),
            }
            for definition in index_registry.definitions(code)
        ]
        listing_tiers = [
            {
                **_selection(
                    definition.label,
                    _market_universe(
                        code,
                        mic=definition.mic,
                        listing_tier=definition.key,
                    ),
                ),
                "key": definition.key,
                "mic": definition.mic,
                "aliases": list(definition.aliases),
            }
            for definition in listing_tier_registry.definitions(code)
        ]

        markets.append(
            {
                "code": code,
                "label": entry.label,
                "enabled": code in enabled,
                "capabilities": asdict(entry.capabilities),
                "market": _selection(
                    f"All {entry.label}",
                    _market_universe(code),
                ),
                "mics": mics,
                "mic_aliases": mic_alias_options,
                "indexes": indexes,
                "listing_tiers": listing_tiers,
            }
        )
    return {
        "version": CATALOG_VERSION,
        "supported_markets": catalog.supported_market_codes(),
        "enabled_markets": enabled_markets,
        "markets": markets,
    }
