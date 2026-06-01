"""Unit tests for the IBD classification health report + gate (pure, no DB)."""
from app.services.ibd_classification_health import (
    HEALTH_REPORT_SCHEMA_VERSION,
    HISTOGRAM_BINS,
    build_health_report,
    confidence_histogram,
    diff_classifications,
    evaluate_gate,
    health_asset_name,
    read_health_report,
    write_health_report,
    GateResult,
)


def test_confidence_histogram_bins_and_null():
    rows = [
        {"confidence": 0.91},   # -> [0.9,1.0]
        {"confidence": 1.0},    # -> [0.9,1.0] (clamped)
        {"confidence": 0.8},    # -> [0.8,0.9)
        {"confidence": 0.05},   # -> [0.0,0.1)
        {"confidence": None},   # -> null (LLM rows carry no confidence)
    ]
    hist = confidence_histogram(rows)

    assert hist["[0.9,1.0]"] == 2
    assert hist["[0.8,0.9)"] == 1
    assert hist["[0.0,0.1)"] == 1
    assert hist["null"] == 1
    # Every bin is always present (zero-initialised) plus the null bucket.
    assert set(hist) == set(HISTOGRAM_BINS) | {"null"}
    # Counts sum to the row count.
    assert sum(hist.values()) == len(rows)


def test_diff_classifications_counts_and_churn():
    prev = [
        {"symbol": "A", "industry_group": "G1"},
        {"symbol": "B", "industry_group": "G2"},
        {"symbol": "C", "industry_group": "G3"},  # removed next week
    ]
    new = [
        {"symbol": "A", "industry_group": "G1"},  # unchanged
        {"symbol": "B", "industry_group": "G9"},  # changed group
        {"symbol": "D", "industry_group": "G4"},  # added
    ]

    diff = diff_classifications(prev, new)

    assert diff["compared"] == 2          # A, B present both weeks
    assert diff["changed_group"] == 1     # B
    assert diff["added"] == 1             # D
    assert diff["removed"] == 1           # C
    assert diff["churn_pct"] == 50.0      # 1 changed / 2 compared
    assert {"symbol": "B", "prev": "G2", "new": "G9"} in diff["changed_examples"]


def test_diff_classifications_empty_prev_is_zero_churn():
    diff = diff_classifications([], [{"symbol": "A", "industry_group": "G1"}])
    assert diff["compared"] == 0
    assert diff["added"] == 1
    assert diff["churn_pct"] == 0.0


def _payload(rows, *, as_of, summary):
    return {
        "market": "HK",
        "as_of_date": as_of,
        "generated_at": f"{as_of}T00:00:00Z",
        "source_revision": f"ibd:{as_of}",
        "model_id": "deepseek-chat",
        "summary": summary,
        "classifications": rows,
    }


def test_build_health_report_with_prev():
    new = _payload(
        [{"symbol": "A", "industry_group": "G1", "confidence": 0.9},
         {"symbol": "B", "industry_group": "G9", "confidence": None}],
        as_of="2026-06-02",
        summary={"coverage_pct": 96.0, "by_source": {"embedding": 1, "llm": 1}},
    )
    prev = _payload(
        [{"symbol": "A", "industry_group": "G1", "confidence": 0.9},
         {"symbol": "B", "industry_group": "G2", "confidence": 0.7}],
        as_of="2026-05-26",
        summary={"coverage_pct": 95.0},
    )

    report = build_health_report(
        payload=new, prev_payload=prev, embedding_model="all-MiniLM-L6-v2"
    )

    assert report["schema_version"] == HEALTH_REPORT_SCHEMA_VERSION
    assert report["market"] == "HK"
    assert report["embedding_model"] == "all-MiniLM-L6-v2"
    assert report["summary"]["coverage_pct"] == 96.0
    assert report["confidence_histogram"]["null"] == 1
    assert report["diff"]["changed_group"] == 1          # B changed
    assert report["diff"]["churn_pct"] == 50.0
    assert report["diff"]["prev_as_of_date"] == "2026-05-26"
    assert report["diff"]["prev_source_revision"] == "ibd:2026-05-26"


def test_build_health_report_without_prev_has_null_diff():
    new = _payload(
        [{"symbol": "A", "industry_group": "G1", "confidence": 0.9}],
        as_of="2026-06-02",
        summary={"coverage_pct": 96.0},
    )
    report = build_health_report(payload=new, prev_payload=None, embedding_model="x")
    assert report["diff"] is None


def test_health_asset_name_and_roundtrip(tmp_path):
    assert health_asset_name("SG") == "ibd-classification-health-sg.json"

    report = {"schema_version": 1, "market": "SG", "summary": {"coverage_pct": 90.0}}
    path = tmp_path / health_asset_name("SG")
    write_health_report(path, report)
    assert path.read_text().endswith("\n")
    assert read_health_report(path) == report


_OK = {"summary": {"coverage_pct": 96.0}, "diff": {"churn_pct": 3.0}}
_HIGH_CHURN = {"summary": {"coverage_pct": 96.0}, "diff": {"churn_pct": 40.0}}
_LOW_COVERAGE = {"summary": {"coverage_pct": 40.0}, "diff": None}


def test_gate_passes_within_thresholds():
    res = evaluate_gate(_OK, max_churn_pct=25, min_coverage_pct=50, mode="enforce")
    assert isinstance(res, GateResult)
    assert res.passed
    assert res.breaches == []


def test_gate_enforce_fails_on_high_churn():
    res = evaluate_gate(_HIGH_CHURN, max_churn_pct=25, min_coverage_pct=50, mode="enforce")
    assert not res.passed
    assert any("churn" in b for b in res.breaches)


def test_gate_enforce_fails_on_low_coverage():
    res = evaluate_gate(_LOW_COVERAGE, max_churn_pct=25, min_coverage_pct=50, mode="enforce")
    assert not res.passed
    assert any("coverage" in b for b in res.breaches)


def test_gate_warn_mode_reports_but_passes():
    res = evaluate_gate(_HIGH_CHURN, max_churn_pct=25, min_coverage_pct=50, mode="warn")
    assert res.passed                 # warn never blocks
    assert res.breaches               # but breaches are still surfaced


def test_gate_off_mode_always_passes_with_no_breaches():
    res = evaluate_gate(_HIGH_CHURN, max_churn_pct=25, min_coverage_pct=50, mode="off")
    assert res.passed
    assert res.breaches == []


def test_gate_null_diff_skips_churn_check():
    res = evaluate_gate(
        {"summary": {"coverage_pct": 96.0}, "diff": None},
        max_churn_pct=25, min_coverage_pct=50, mode="enforce",
    )
    assert res.passed
