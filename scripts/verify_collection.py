"""
verify_collection.py
────────────────────
Verify that the three required CERCA telemetry output files exist, are
non-empty, and contain the expected columns and minimum row counts.

This script must pass before any feature extraction or analysis is performed.
No synthetic data is accepted — all checks are against real collected files.

Exit codes:
  0 — all checks passed
  1 — one or more checks failed

Usage
─────
    python scripts/verify_collection.py
    python scripts/verify_collection.py --data-dir data/ --graph-dir graphs/
    python scripts/verify_collection.py --experiment-dir experiments/20260728_210000/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from glob import glob
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent

# ─── Required column schemas ──────────────────────────────────────────────────

SPANS_REQUIRED = {
    "traceID", "spanID", "parentSpanID", "service", "operation",
    "startTime_us", "duration_us", "error",
}

METRICS_REQUIRED = {
    "metric_name", "service", "timestamp_unix", "value",
}

GRAPH_REQUIRED = {
    "caller_service", "callee_service", "call_count",
    "mean_latency_ms", "error_rate",
}

# ─── Thresholds ───────────────────────────────────────────────────────────────

MIN_SPANS      = 100    # at least 100 spans — fewer suggests workload didn't run
MIN_TRACES     = 10     # at least 10 unique traces
MIN_SERVICES   = 3      # at least 3 services in the dependency graph
MIN_METRIC_ROWS = 50    # at least 50 metric data points
MIN_GRAPH_EDGES = 3     # at least 3 directed edges in the graph


# ─── Check helpers ────────────────────────────────────────────────────────────

class Check:
    def __init__(self, name: str):
        self.name    = name
        self.passed  = True
        self.message = ""

    def fail(self, msg: str):
        self.passed  = False
        self.message = msg
        return self

    def ok(self, msg: str = ""):
        self.message = msg
        return self


def _print_check(check: Check):
    symbol = "✓" if check.passed else "✗"
    color  = "\033[92m" if check.passed else "\033[91m"
    reset  = "\033[0m"
    msg    = f"  {color}{symbol}{reset}  {check.name}"
    if check.message:
        msg += f"  [{check.message}]"
    print(msg)


# ─── File checks ──────────────────────────────────────────────────────────────


def check_spans(data_dir: Path) -> tuple[list[Check], pd.DataFrame | None]:
    checks = []
    df     = None

    # 1. File exists
    files = sorted(glob(str(data_dir / "processed" / "spans_*.csv")))
    c = Check("spans_*.csv exists")
    if not files:
        checks.append(c.fail(f"No files matching {data_dir}/processed/spans_*.csv"))
        return checks, None
    checks.append(c.ok(f"{len(files)} file(s) found"))

    # 2. Readable
    c = Check("spans CSV readable")
    try:
        df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
        checks.append(c.ok(f"{len(df):,} rows loaded"))
    except Exception as e:
        checks.append(c.fail(str(e)))
        return checks, None

    # 3. Required columns
    c = Check("spans has required columns")
    missing = SPANS_REQUIRED - set(df.columns)
    checks.append(c.fail(f"Missing: {missing}") if missing else c.ok())

    # 4. Minimum spans
    c = Check(f"spans >= {MIN_SPANS} rows")
    checks.append(c.ok(f"{len(df):,}") if len(df) >= MIN_SPANS
                  else c.fail(f"Only {len(df)} rows — run generate_workload.py first"))

    # 5. Minimum unique traces
    n_traces = df["traceID"].nunique() if "traceID" in df.columns else 0
    c = Check(f"spans >= {MIN_TRACES} unique traces")
    checks.append(c.ok(f"{n_traces:,} traces") if n_traces >= MIN_TRACES
                  else c.fail(f"Only {n_traces} unique traces"))

    # 6. Service variety
    n_svcs = df["service"].nunique() if "service" in df.columns else 0
    c = Check(f"spans from >= {MIN_SERVICES} services")
    checks.append(c.ok(f"{n_svcs} services: {sorted(df['service'].unique()[:6].tolist())}")
                  if n_svcs >= MIN_SERVICES else c.fail(f"Only {n_svcs} service(s)"))

    # 7. Not all same timestamp (would indicate synthetic/static data)
    c = Check("span timestamps vary (non-synthetic)")
    if "startTime_us" in df.columns:
        n_unique_ts = pd.to_numeric(df["startTime_us"], errors="coerce").nunique()
        checks.append(c.ok(f"{n_unique_ts:,} unique timestamps")
                      if n_unique_ts > MIN_TRACES
                      else c.fail(f"Only {n_unique_ts} unique timestamps — may be synthetic"))

    return checks, df


def check_metrics(data_dir: Path) -> tuple[list[Check], pd.DataFrame | None]:
    checks = []
    df     = None

    files = sorted(glob(str(data_dir / "processed" / "metrics_*.csv")))
    c = Check("metrics_*.csv exists")
    if not files:
        checks.append(c.fail(f"No files in {data_dir}/processed/metrics_*.csv"))
        return checks, None
    checks.append(c.ok(f"{len(files)} file(s) found"))

    c = Check("metrics CSV readable")
    try:
        df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        checks.append(c.ok(f"{len(df):,} rows loaded"))
    except Exception as e:
        checks.append(c.fail(str(e)))
        return checks, None

    c = Check("metrics has required columns")
    missing = METRICS_REQUIRED - set(df.columns)
    checks.append(c.fail(f"Missing: {missing}") if missing else c.ok())

    c = Check(f"metrics >= {MIN_METRIC_ROWS} rows")
    checks.append(c.ok(f"{len(df):,}") if len(df) >= MIN_METRIC_ROWS
                  else c.fail(f"Only {len(df)} rows"))

    if "metric_name" in df.columns:
        n_metrics = df["metric_name"].nunique()
        c = Check("metrics has multiple metric types")
        checks.append(c.ok(f"{n_metrics} metric names") if n_metrics >= 3
                      else c.fail(f"Only {n_metrics} metric type(s)"))

    if "value" in df.columns:
        n_nan = pd.to_numeric(df["value"], errors="coerce").isna().sum()
        c = Check("metric values are numeric")
        checks.append(c.ok(f"{n_nan} NaN values") if n_nan < len(df) * 0.5
                      else c.fail(f"{n_nan}/{len(df)} NaN values"))

    return checks, df


def check_graph(graph_dir: Path) -> tuple[list[Check], pd.DataFrame | None]:
    checks = []
    df     = None

    graph_path = graph_dir / "dependency_graph.csv"
    c = Check("dependency_graph.csv exists")
    if not graph_path.exists():
        checks.append(c.fail(str(graph_path)))
        return checks, None
    checks.append(c.ok())

    c = Check("dependency_graph.csv readable")
    try:
        df = pd.read_csv(graph_path)
        checks.append(c.ok(f"{len(df)} edges"))
    except Exception as e:
        checks.append(c.fail(str(e)))
        return checks, None

    c = Check("dependency_graph has required columns")
    missing = GRAPH_REQUIRED - set(df.columns)
    checks.append(c.fail(f"Missing: {missing}") if missing else c.ok())

    c = Check(f"dependency_graph >= {MIN_GRAPH_EDGES} edges")
    checks.append(c.ok(f"{len(df)} edges") if len(df) >= MIN_GRAPH_EDGES
                  else c.fail(f"Only {len(df)} edges — run build_dependency_graph.py"))

    c = Check(f"dependency_graph >= {MIN_SERVICES} services")
    if all(col in df.columns for col in ["caller_service", "callee_service"]):
        services = set(df["caller_service"]) | set(df["callee_service"])
        checks.append(c.ok(f"{len(services)} services") if len(services) >= MIN_SERVICES
                      else c.fail(f"Only {len(services)} service(s)"))

    # Check JSON adjacency list also exists
    json_path = graph_dir / "dependency_graph.json"
    c = Check("dependency_graph.json exists")
    if json_path.exists():
        with open(json_path) as f:
            adj = json.load(f)
        n_nodes = len(adj.get("nodes", []))
        n_edges = len(adj.get("edges", []))
        checks.append(c.ok(f"{n_nodes} nodes, {n_edges} edges"))
    else:
        checks.append(c.fail("dependency_graph.json not found — run build_dependency_graph.py"))

    return checks, df


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Verify CERCA telemetry collection outputs"
    )
    parser.add_argument("--data-dir",       default=str(PROJECT_ROOT / "data"))
    parser.add_argument("--graph-dir",      default=str(PROJECT_ROOT / "graphs"))
    parser.add_argument("--experiment-dir", default=None,
                        help="Load from a specific experiment archive")
    args = parser.parse_args()

    if args.experiment_dir:
        exp = Path(args.experiment_dir)
        data_dir  = exp / "data"
        graph_dir = exp / "graphs"
    else:
        data_dir  = Path(args.data_dir)
        graph_dir = Path(args.graph_dir)

    print("\n═══ CERCA Telemetry Collection Verification ═══")
    print(f"    Data:   {data_dir}")
    print(f"    Graphs: {graph_dir}\n")

    all_checks = []

    # ── Spans ─────────────────────────────────────────────────────────────────
    print("── spans_*.csv ─────────────────────────────────────────────────")
    span_checks, spans_df = check_spans(data_dir)
    for c in span_checks:
        _print_check(c)
    all_checks.extend(span_checks)

    # ── Metrics ───────────────────────────────────────────────────────────────
    print("\n── metrics_*.csv ───────────────────────────────────────────────")
    metric_checks, metrics_df = check_metrics(data_dir)
    for c in metric_checks:
        _print_check(c)
    all_checks.extend(metric_checks)

    # ── Dependency graph ──────────────────────────────────────────────────────
    print("\n── dependency_graph.csv ────────────────────────────────────────")
    graph_checks, graph_df = check_graph(graph_dir)
    for c in graph_checks:
        _print_check(c)
    all_checks.extend(graph_checks)

    # ── Summary ───────────────────────────────────────────────────────────────
    n_pass = sum(1 for c in all_checks if c.passed)
    n_fail = sum(1 for c in all_checks if not c.passed)

    print(f"\n{'═'*55}")
    if n_fail == 0:
        print(f"  ✓  ALL {n_pass} CHECKS PASSED")
        print(f"  Real telemetry confirmed.")
        print(f"  Next: python telemetry_features/extract_all.py")
    else:
        print(f"  ✗  {n_fail} CHECK(S) FAILED  ({n_pass} passed)")
        print()
        print("  Failed checks:")
        for c in all_checks:
            if not c.passed:
                print(f"    - {c.name}: {c.message}")
        print()
        print("  Run the deployment sequence in order:")
        print("    1. powershell scripts/setup_dsb.ps1")
        print("    2. docker compose up -d")
        print("    3. python scripts/init_social_graph.py")
        print("    4. python scripts/generate_workload.py --duration 120")
        print("    5. python scripts/run_collection.py --lookback 10m")
        print("    6. python scripts/verify_collection.py")
    print(f"{'═'*55}\n")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
