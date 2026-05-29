from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.universe.ingestion import (
    CanonicalUniverseIngestionResult,
    CanonicalUniverseRow,
    DuplicateActiveUniverseRowError,
    RejectedUniverseRow,
    UniverseLifecycleMetadata,
    UniverseSourceProvenance,
)


def _provenance(
    *,
    source_symbol: str = "00700",
    row_number: int = 12,
) -> UniverseSourceProvenance:
    return UniverseSourceProvenance(
        source_name="hkex_official",
        source_symbol=source_symbol,
        source_row_number=row_number,
        snapshot_id="hkex-listofsecurities-2026-05-28",
        snapshot_as_of="2026-05-28",
        source_metadata={"source_urls": ["https://example.test/hkex.xlsx"]},
        lineage_hash="lineage-hash",
        row_hash="row-hash",
    )


def test_canonical_universe_row_normalizes_identity_and_preserves_symbol() -> None:
    row = CanonicalUniverseRow(
        symbol=" 00700.hk ",
        name="Tencent Holdings",
        market=" hk ",
        mic=" xhkg ",
        local_code="00700",
        currency=" hkd ",
        timezone="Asia/Hong_Kong",
        listing_tier="Main Board",
        sector="Communication Services",
        industry="Internet Content",
        market_cap=3_000_000_000_000.0,
        lifecycle=UniverseLifecycleMetadata.active(),
        provenance=_provenance(),
    )

    assert row.symbol == "00700.HK"
    assert row.market == "HK"
    assert row.mic == "XHKG"
    assert row.currency == "HKD"
    assert row.local_code == "00700"
    assert row.listing_tier == "main_board"
    assert row.lifecycle.is_active is True
    assert row.provenance.source_name == "hkex_official"
    assert row.active_identity_key == ("HK", "XHKG", "00700")


def test_canonical_universe_row_defaults_mic_facts_when_currency_timezone_absent() -> None:
    first_seen_at = datetime(2026, 5, 28, 8, 30, tzinfo=UTC)
    row = CanonicalUniverseRow(
        symbol="D05.SI",
        name="DBS Group",
        market="SG",
        mic="XSES",
        local_code="D05",
        currency=None,
        timezone=None,
        listing_tier="Catalist",
        lifecycle=UniverseLifecycleMetadata(
            first_seen_at=first_seen_at,
            last_seen_in_source_at=first_seen_at,
        ),
        provenance=UniverseSourceProvenance(
            source_name="sgx_official",
            source_symbol="D05",
            source_row_number=1,
            snapshot_id="sgx-2026-05-28",
            snapshot_as_of=date(2026, 5, 28),
        ),
    )

    assert row.currency == "SGD"
    assert row.timezone == "Asia/Singapore"
    assert row.listing_tier == "catalist"
    assert row.lifecycle.first_seen_at is first_seen_at
    assert row.provenance.snapshot_as_of == date(2026, 5, 28)


def test_canonical_universe_models_reject_loose_boundary_types() -> None:
    with pytest.raises(ValueError, match="first_seen_at must be a datetime"):
        UniverseLifecycleMetadata(first_seen_at="2026-05-28")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="snapshot_as_of must be a date"):
        UniverseSourceProvenance(
            source_name="sgx_official",
            snapshot_id="sgx-2026-05-28",
            snapshot_as_of=datetime(2026, 5, 28, 8, 30, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="source_metadata.bad must be JSON-compatible"):
        UniverseSourceProvenance(
            source_name="sgx_official",
            snapshot_id="sgx-2026-05-28",
            source_metadata={"bad": object()},  # type: ignore[dict-item]
        )

    with pytest.raises(ValueError, match="source_name must be text"):
        UniverseSourceProvenance(
            source_name=123,  # type: ignore[arg-type]
            snapshot_id="sgx-2026-05-28",
        )


def test_canonical_ingestion_result_rejects_duplicate_active_market_mic_local_code() -> None:
    first = CanonicalUniverseRow(
        symbol="00700.HK",
        name="Tencent Holdings",
        market="HK",
        mic="XHKG",
        local_code="00700",
        currency="HKD",
        timezone="Asia/Hong_Kong",
        lifecycle=UniverseLifecycleMetadata.active(),
        provenance=_provenance(source_symbol="00700", row_number=1),
    )
    second = CanonicalUniverseRow(
        symbol="700.HK",
        name="Tencent Holdings duplicate",
        market="HK",
        mic="XHKG",
        local_code="00700",
        currency="HKD",
        timezone="Asia/Hong_Kong",
        lifecycle=UniverseLifecycleMetadata.active(),
        provenance=_provenance(source_symbol="700", row_number=2),
    )

    with pytest.raises(DuplicateActiveUniverseRowError) as exc_info:
        CanonicalUniverseIngestionResult(canonical_rows=(first, second))

    assert exc_info.value.identity_key == ("HK", "XHKG", "00700")
    assert exc_info.value.symbols == ("00700.HK", "700.HK")


def test_canonical_ingestion_result_allows_inactive_duplicate_identity() -> None:
    active = CanonicalUniverseRow(
        symbol="00700.HK",
        name="Tencent Holdings",
        market="HK",
        mic="XHKG",
        local_code="00700",
        currency="HKD",
        timezone="Asia/Hong_Kong",
        lifecycle=UniverseLifecycleMetadata.active(),
        provenance=_provenance(source_symbol="00700", row_number=1),
    )
    inactive = CanonicalUniverseRow(
        symbol="700.HK",
        name="Tencent Holdings old alias",
        market="HK",
        mic="XHKG",
        local_code="00700",
        currency="HKD",
        timezone="Asia/Hong_Kong",
        lifecycle=UniverseLifecycleMetadata.inactive(
            status="inactive_missing_source",
            reason="No longer present in source snapshot",
        ),
        provenance=_provenance(source_symbol="700", row_number=2),
    )

    result = CanonicalUniverseIngestionResult(
        canonical_rows=(active, inactive),
        rejected_rows=(
            RejectedUniverseRow(
                source_row_number=3,
                source_symbol="BAD",
                reason="Invalid symbol",
            ),
        ),
    )

    assert result.canonical_rows == (active, inactive)
    assert result.rejected_count == 1
    assert inactive.active_identity_key is None
