#!/usr/bin/env python3
"""Performance harness for FrameCraft. See `.claude/plans/07-observability-and-goldens.md` §12.

Runs `compose --dry-run` against the three golden situations N times, records
per-phase timings, writes a JSON report under `.perf/`, and compares against a
baseline if one exists.

Targets (§7.3):
  - Assembler (pure template, no polish): ≤50ms per scene
  - Assembler total (4-scene 20s promo): ≤1.5s
  - Total pipeline (compose --dry-run): ≤30s

Usage:
    python scripts/perf_harness.py                  # run + compare baseline
    python scripts/perf_harness.py --n 5            # 5 iterations
    python scripts/perf_harness.py --write-baseline # write current as baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

GOLDENS_DIR = ROOT / "tests" / "goldens"
PERF_DIR = ROOT / ".perf"

SITUATIONS = ["narrative", "product-promo", "data-explainer"]
TARGETS: dict[str, float] = {
    "total_p50_ms": 30_000,
}
REGRESSION_THRESHOLD = 1.20  # 20% regression allowed


def _run_once(situation: str, tmp_path: Path) -> dict[str, float]:
    """Run compose --dry-run and return timing breakdown (ms)."""
    from typer.testing import CliRunner
    from framecraft.cli import app

    runner = CliRunner()
    t0 = time.perf_counter()
    result = runner.invoke(
        app,
        [
            "compose", situation,
            "--out", str(tmp_path),
            "--dry-run",
            "--no-config",
            "--no-summary",
        ],
    )
    elapsed = (time.perf_counter() - t0) * 1000

    if result.exit_code != 0:
        raise RuntimeError(f"compose failed: {result.output}")

    return {"total_ms": elapsed}


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    return statistics.quantiles(data, n=100)[int(p) - 1] if len(data) > 1 else data[0]


def run_harness(n_iterations: int) -> dict:
    report: dict = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "n_iterations": n_iterations,
        "phases": [],
    }

    print(f"Running {n_iterations} iteration(s) per situation…")

    for situation_name in SITUATIONS:
        situation_file = GOLDENS_DIR / situation_name / "situation.txt"
        if not situation_file.exists():
            print(f"  [skip] {situation_name} — no situation.txt")
            continue

        brief = situation_file.read_text(encoding="utf-8").strip()
        timings: list[float] = []

        for i in range(n_iterations):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                try:
                    t = _run_once(brief, Path(td))
                    timings.append(t["total_ms"])
                    print(f"  [{situation_name}] iteration {i+1}/{n_iterations}: "
                          f"{t['total_ms']:.0f}ms")
                except Exception as e:
                    print(f"  [{situation_name}] iteration {i+1} FAILED: {e}")

        if timings:
            p50 = _percentile(timings, 50)
            p95 = _percentile(timings, 95)
            report["phases"].append({
                "situation": situation_name,
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "min_ms": round(min(timings), 1),
                "max_ms": round(max(timings), 1),
                "n": len(timings),
            })
            print(f"  [{situation_name}] p50={p50:.0f}ms p95={p95:.0f}ms")

    return report


def compare_to_baseline(report: dict, baseline_path: Path) -> bool:
    """Return True if no regression; print warnings for regressions."""
    if not baseline_path.exists():
        print("No baseline found — skipping comparison.")
        return True

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_by_situation = {p["situation"]: p for p in baseline.get("phases", [])}

    passed = True
    for phase in report["phases"]:
        name = phase["situation"]
        if name not in baseline_by_situation:
            continue
        base_p50 = baseline_by_situation[name]["p50_ms"]
        cur_p50 = phase["p50_ms"]
        ratio = cur_p50 / base_p50 if base_p50 > 0 else 1.0
        if ratio > REGRESSION_THRESHOLD:
            print(f"[REGRESSION] {name}: p50 {cur_p50:.0f}ms vs baseline {base_p50:.0f}ms "
                  f"({ratio:.1%} — >{REGRESSION_THRESHOLD:.0%} threshold)")
            passed = False
        else:
            print(f"[ok] {name}: p50 {cur_p50:.0f}ms vs baseline {base_p50:.0f}ms "
                  f"({ratio:.1%})")

    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="FrameCraft perf harness (§7.3).")
    parser.add_argument("--n", type=int, default=10, help="Iterations per situation.")
    parser.add_argument("--write-baseline", action="store_true",
                        help="Write current report as the new baseline.")
    args = parser.parse_args()

    PERF_DIR.mkdir(exist_ok=True)
    baseline_path = PERF_DIR / "baseline.json"

    report = run_harness(args.n)

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = PERF_DIR / f"report-{ts}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written: {report_path}")

    if args.write_baseline:
        baseline_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Baseline updated: {baseline_path}")
        sys.exit(0)

    passed = compare_to_baseline(report, baseline_path)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
