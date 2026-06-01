"""Evaluate the IBD classification health gate (coverage + week-over-week churn).

Reads an ``ibd-classification-health-<market>.json`` report and applies the
configured thresholds. In ``enforce`` mode a breach exits non-zero so the calling
workflow fails *before* the new bundle is uploaded — the prior good ``-latest``
manifest stays referenced and the static-site build falls back to it. In ``warn``
mode breaches are surfaced as GitHub annotations but the step still succeeds, so
you can observe a few weeks of real churn before gating hard.

Dependency-free (no DB, no network) so it runs fast in CI.

Usage:
    python -m app.scripts.check_ibd_classification_gate \
      --health /tmp/ibd-classification/ibd-classification-health-hk.json \
      --max-churn-pct 25 --min-coverage-pct 50 --mode warn
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.services.ibd_classification_health import evaluate_gate, read_health_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", required=True)
    parser.add_argument("--max-churn-pct", type=float, default=25.0)
    parser.add_argument("--min-coverage-pct", type=float, default=50.0)
    parser.add_argument("--mode", choices=("off", "warn", "enforce"), default="warn")
    args = parser.parse_args(argv)

    report = read_health_report(Path(args.health))
    result = evaluate_gate(
        report,
        max_churn_pct=args.max_churn_pct,
        min_coverage_pct=args.min_coverage_pct,
        mode=args.mode,
    )

    market = report.get("market", "?")
    summary = report.get("summary") or {}
    diff = report.get("diff") or {}
    print(
        f"IBD health gate [{market}] mode={result.mode} "
        f"coverage={summary.get('coverage_pct')}% churn={diff.get('churn_pct')}%"
    )

    if not result.breaches:
        print("  gate: OK")
        return 0

    annotation = "::error::" if result.mode == "enforce" else "::warning::"
    for breach in result.breaches:
        print(f"{annotation} IBD health gate [{market}]: {breach}")

    if result.passed:
        print("  gate: passed (non-enforcing mode)")
        return 0
    print("  gate: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
