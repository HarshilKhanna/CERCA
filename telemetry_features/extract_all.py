"""
telemetry_features/extract_all.py
──────────────────────────────────
Orchestrator for the CERCA telemetry feature extraction layer.

Runs all four extractors in sequence and saves their outputs to the
telemetry_features/outputs/ directory.  Also prints a research-quality
summary table of what was extracted.

Usage
─────
    python telemetry_features/extract_all.py [OPTIONS]

    python telemetry_features/extract_all.py --dry-run
    python telemetry_features/extract_all.py --spans-glob "data/processed/spans_*.csv"
    python telemetry_features/extract_all.py --experiment-dir "experiments/20260728_210000/"

Options
    --spans-glob     Glob for span CSV files [default: data/processed/spans_*.csv]
    --metrics-glob   Glob for metric CSV files [default: data/processed/metrics_*.csv]
    --output-dir     Where to save feature CSVs [default: telemetry_features/outputs/]
    --experiment-dir If set, loads data/raw from an experiment archive directory
    --dry-run        Run all extractors, print summary, do NOT save CSVs
    --verbose        Debug logging

Output Files
────────────
    telemetry_features/outputs/
        edge_features.csv
        service_features.csv
        temporal_features.csv
        trace_features.csv
        extraction_summary.json   ← machine-readable extraction metadata

Exit codes: 0 = success, 1 = data not found, 2 = extraction error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ─── Module imports ───────────────────────────────────────────────────────────

# Support both `python telemetry_features/extract_all.py` (direct) and
# `python -m telemetry_features.extract_all` (module) invocation styles.
try:
    from . import _io, edge_features, service_features, temporal_features, trace_features
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from telemetry_features import _io, edge_features, service_features, temporal_features, trace_features

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_OUT  = Path(__file__).parent / "outputs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("extract_all")


# ─── Summary printer ─────────────────────────────────────────────────────────


def _print_summary(results: dict[str, pd.DataFrame]):
    """Print a human-readable extraction summary table."""
    print("\n" + "═" * 68)
    print("  CERCA  ·  Telemetry Feature Extraction Summary")
    print("═" * 68)
    print(f"  {'Module':<28}  {'Rows':>6}  {'Cols':>4}  {'Signal Classes'}")
    print("─" * 68)
    for name, df in results.items():
        if df is None or df.empty:
            print(f"  {name:<28}  {'EMPTY':>6}  {'—':>4}  —")
            continue
        rows = len(df)
        cols = len(df.columns)
        classes = (
            df["signal_class"].unique().tolist()
            if "signal_class" in df.columns
            else df["feature_domain"].unique().tolist()
            if "feature_domain" in df.columns
            else ["—"]
        )
        print(f"  {name:<28}  {rows:>6,}  {cols:>4}  {', '.join(classes)}")
    print("═" * 68)


def _research_sanity_checks(results: dict[str, pd.DataFrame]):
    """Print research-oriented sanity check results."""
    print("\n── Research Sanity Checks ──────────────────────────────────────")

    # 1. Temporal ordering violations (should be 0 on healthy instrumentation)
    temp = results.get("temporal_features")
    if temp is not None and "temporal_ordering_violations" in temp.columns:
        viol = temp["temporal_ordering_violations"].fillna(0).sum()
        flag = "⚠ INVESTIGATE" if viol > 0 else "✓ OK"
        print(f"  Clock skew violations (total): {int(viol):,}   {flag}")

    # 2. Metric coverage (should be ~1.0 on healthy stack)
    if temp is not None and "metric_coverage_ratio" in temp.columns:
        cov = temp["metric_coverage_ratio"].dropna()
        if len(cov):
            flag = "⚠ GAPS DETECTED" if cov.min() < 0.9 else "✓ OK"
            print(f"  Metric coverage ratio:  mean={cov.mean():.3f}  min={cov.min():.3f}   {flag}")

    # 3. Trace completeness (should peak near 1.0 on normal operation)
    tr = results.get("trace_features")
    if tr is not None and "trace_completeness_score" in tr.columns:
        cs = tr["trace_completeness_score"].dropna()
        orphan_traces = int((cs < 0.9).sum())
        flag = "⚠ HIGH ORPHAN RATE" if orphan_traces > len(cs) * 0.05 else "✓ OK"
        print(f"  Trace completeness:  mean={cs.mean():.3f}  orphan_traces(<0.9)={orphan_traces}   {flag}")

    # 4. Single-span traces (completeness proxy)
    if tr is not None and "trace_is_single_span" in tr.columns:
        n_single = int(tr["trace_is_single_span"].sum())
        total    = len(tr)
        pct      = 100 * n_single / total if total else 0
        flag     = "⚠ HIGH" if pct > 20 else "✓ OK"
        print(f"  Single-span traces:  {n_single}/{total} ({pct:.1f}%)   {flag}")

    # 5. Edge observation rate distribution
    ef = results.get("edge_features")
    if ef is not None and "edge_observation_rate" in ef.columns:
        rare = int((ef["edge_observation_rate"] < 0.01).sum())
        print(f"  Rare edges (observation_rate < 1%):  {rare}/{len(ef)}")

    print("─" * 68)


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="CERCA: Extract all telemetry features from collected datasets"
    )
    parser.add_argument(
        "--spans-glob",
        default=str(PROJECT_ROOT / "data" / "processed" / "spans_*.csv"),
        help="Glob for spans CSVs",
    )
    parser.add_argument(
        "--metrics-glob",
        default=str(PROJECT_ROOT / "data" / "processed" / "metrics_*.csv"),
        help="Glob for metrics CSVs",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUT),
        help="Output directory for feature CSVs",
    )
    parser.add_argument(
        "--experiment-dir",
        default=None,
        help="Load data from a specific experiment archive directory",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Override globs if experiment-dir provided
    if args.experiment_dir:
        exp = Path(args.experiment_dir)
        args.spans_glob   = str(exp / "data" / "processed" / "spans_*.csv")
        args.metrics_glob = str(exp / "data" / "processed" / "metrics_*.csv")
        log.info("Experiment mode: loading from %s", exp)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("═══ CERCA Feature Extraction ═══")
    log.info("Spans:   %s", args.spans_glob)
    log.info("Metrics: %s", args.metrics_glob)

    # ── Load data ─────────────────────────────────────────────────────────────
    try:
        spans = _io.load_spans(args.spans_glob)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    try:
        metrics = _io.load_metrics(args.metrics_glob)
    except FileNotFoundError:
        log.warning("No metrics files found — metric-derived features will be NaN")
        metrics = None

    # ── Run extractors ────────────────────────────────────────────────────────
    results: dict[str, pd.DataFrame | None] = {}
    extraction_meta = {
        "timestamp":    datetime.now(tz=timezone.utc).isoformat(),
        "spans_files":  args.spans_glob,
        "metrics_files": args.metrics_glob,
        "span_count":   len(spans),
        "trace_count":  int(spans["traceID"].nunique()),
        "metric_rows":  len(metrics) if metrics is not None else 0,
        "modules":      {},
    }

    for module_name, extractor_fn, extractor_args in [
        ("edge_features",     edge_features.extract,     (spans,)),
        ("service_features",  service_features.extract,  (spans, metrics)),
        ("temporal_features", temporal_features.extract, (spans, metrics)),
        ("trace_features",    trace_features.extract,    (spans,)),
    ]:
        log.info("── Running %s …", module_name)
        try:
            df = extractor_fn(*extractor_args)
            results[module_name] = df
            extraction_meta["modules"][module_name] = {
                "rows": len(df),
                "cols": len(df.columns),
                "status": "ok",
            }
            if not args.dry_run and df is not None and not df.empty:
                path = out_dir / f"{module_name}.csv"
                df.to_csv(path, index=False)
                log.info("  Saved → %s", path)
        except Exception as exc:
            log.error("  [%s] FAILED: %s", module_name, exc)
            if args.verbose:
                traceback.print_exc()
            results[module_name] = None
            extraction_meta["modules"][module_name] = {
                "rows": 0, "cols": 0, "status": f"error: {exc}"
            }

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_summary(results)
    _research_sanity_checks(results)

    # ── Save extraction metadata ───────────────────────────────────────────────
    if not args.dry_run:
        meta_path = out_dir / "extraction_summary.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(extraction_meta, f, indent=2)
        log.info("Extraction metadata → %s", meta_path)

    n_ok = sum(1 for m in extraction_meta["modules"].values() if m["status"] == "ok")
    n_total = len(extraction_meta["modules"])
    log.info("═══ Done: %d/%d modules OK ═══", n_ok, n_total)

    if n_ok < n_total:
        sys.exit(2)


if __name__ == "__main__":
    main()
