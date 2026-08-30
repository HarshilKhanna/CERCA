"""
telemetry_features/edge_features.py
────────────────────────────────────
Extract per-(caller_service, callee_service) features from the span dataset.

All features are derived strictly from the spans CSV schema:
  traceID, spanID, parentSpanID, service, operation,
  startTime_us, duration_us, error

Research Motivation
───────────────────
In a graph-based RCA algorithm, each directed edge (A → B) carries statistical
estimates (mean latency, error rate) that are only trustworthy when:
  1. The edge has been observed enough times (statistical reliability).
  2. The latency distribution is stable, not wildly variable (signal quality).
  3. The edge appears consistently across traces, not only in unusual request paths.

These features operationalise those intuitions without requiring fault injection.

Output Schema (edge_features.csv)
──────────────────────────────────
  caller_service        str   — upstream service name
  callee_service        str   — downstream service name
  total_traces          int   — total traces in dataset (denominator)

  edge_observation_count   int    — raw count of (caller→callee) span pairs
  edge_observation_rate    float  — edge_count / total_traces  ∈ [0, 1]
  edge_trace_coverage_rate float  — unique traces containing edge / total_traces ∈ [0, 1]
  edge_operation_entropy   float  — Shannon entropy over callee operation distribution ≥ 0
  edge_operation_count     int    — number of distinct operations observed on this edge

  edge_mean_latency_ms     float  — mean callee span duration in milliseconds
  edge_std_latency_ms      float  — std dev of callee span duration in milliseconds
  edge_p50_latency_ms      float  — median latency
  edge_p95_latency_ms      float  — 95th percentile latency
  edge_p99_latency_ms      float  — 99th percentile latency
  edge_latency_cv          float  — coefficient of variation = std / mean  ∈ [0, ∞)
  edge_p95_p50_ratio       float  — p95 / p50; tail heaviness indicator

  edge_error_rate          float  — fraction of callee spans with error=True ∈ [0, 1]
  edge_error_count         int    — raw error span count

  signal_class             str    — "trace_only" (no metric data used here)

Mathematical Definitions
────────────────────────
  Let E = {s_i} be the set of callee spans on edge (A → B).
  Let T = total unique trace IDs in dataset.

  edge_observation_count   = |E|
  edge_observation_rate    = |E| / T
  edge_trace_coverage_rate = |{traceID : ∃ s_i ∈ E with s_i.traceID}| / T
  edge_latency_cv          = σ(duration_us) / μ(duration_us)    [NaN if μ = 0]
  edge_p95_p50_ratio       = p95(duration_us) / p50(duration_us) [NaN if p50 = 0]
  edge_operation_entropy   = -Σ_k p_k · log2(p_k)
                             where p_k = count(operation=k) / |E|
  edge_error_rate          = Σ error_i / |E|
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import _io

log = logging.getLogger(__name__)


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _build_raw_edges(spans: pd.DataFrame) -> pd.DataFrame:
    """
    Join child spans to their parents to produce a flat edge table.

    For every span S with parentSpanID P (non-empty):
      - Look up P in the same trace → get caller_service
      - S provides: callee_service, operation, duration_us, error

    Returns a DataFrame with columns:
      traceID, caller_service, callee_service, callee_operation,
      duration_us, error, spanID
    """
    # Child spans (have a parent)
    child = spans[spans["parentSpanID"] != ""].copy()

    # Parent lookup table: (traceID, spanID) → service
    parent_lookup = (
        spans[["traceID", "spanID", "service"]]
        .rename(columns={"spanID": "parentSpanID", "service": "caller_service"})
    )

    merged = child.merge(parent_lookup, on=["traceID", "parentSpanID"], how="inner")

    # Drop intra-service calls (same service calling itself — not cross-service edges)
    merged = merged[merged["caller_service"] != merged["service"]].copy()
    merged.rename(columns={"service": "callee_service", "operation": "callee_operation"}, inplace=True)
    merged["duration_ms"] = merged["duration_us"].astype(float) / 1000.0

    log.info(
        "[edge_features] Raw edges: %d cross-service parent→child span pairs",
        len(merged),
    )
    return merged


def _shannon_entropy(series: pd.Series) -> float:
    """Shannon entropy (bits) of the value distribution in *series*."""
    counts = series.value_counts(normalize=True)
    entropy = -(counts * np.log2(counts + 1e-12)).sum()
    return float(entropy)


# ─── Main extractor ───────────────────────────────────────────────────────────


def extract(spans: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all edge-level features from a spans DataFrame.

    Parameters
    ----------
    spans : pd.DataFrame
        Output of _io.load_spans() — normalised spans table.

    Returns
    -------
    pd.DataFrame
        One row per (caller_service, callee_service) pair.
        All feature columns documented in module docstring.
    """
    total_traces = spans["traceID"].nunique()
    log.info("[edge_features] Dataset: %d spans, %d unique traces", len(spans), total_traces)

    raw_edges = _build_raw_edges(spans)

    if raw_edges.empty:
        log.warning("[edge_features] No cross-service edges found — returning empty DataFrame")
        return pd.DataFrame()

    group_key = ["caller_service", "callee_service"]

    # ── Core aggregation ──────────────────────────────────────────────────────
    agg = (
        raw_edges.groupby(group_key)
        .agg(
            edge_observation_count = ("spanID",      "count"),
            edge_mean_latency_ms   = ("duration_ms", "mean"),
            edge_std_latency_ms    = ("duration_ms", "std"),
            edge_p50_latency_ms    = ("duration_ms", lambda x: np.percentile(x, 50)),
            edge_p95_latency_ms    = ("duration_ms", lambda x: np.percentile(x, 95)),
            edge_p99_latency_ms    = ("duration_ms", lambda x: np.percentile(x, 99)),
            edge_error_count       = ("error",        "sum"),
        )
        .reset_index()
    )

    # ── Derived scalar features ───────────────────────────────────────────────
    agg["edge_error_rate"]   = agg["edge_error_count"] / agg["edge_observation_count"]
    agg["edge_observation_rate"] = agg["edge_observation_count"] / total_traces

    # Latency CV: undefined (NaN) when mean latency is zero
    agg["edge_latency_cv"] = np.where(
        agg["edge_mean_latency_ms"] > 0,
        agg["edge_std_latency_ms"] / agg["edge_mean_latency_ms"],
        np.nan,
    )

    # P95/P50 ratio: undefined when p50 is zero
    agg["edge_p95_p50_ratio"] = np.where(
        agg["edge_p50_latency_ms"] > 0,
        agg["edge_p95_latency_ms"] / agg["edge_p50_latency_ms"],
        np.nan,
    )

    # ── Per-edge trace coverage ───────────────────────────────────────────────
    # unique traces containing this edge / total traces
    trace_cov = (
        raw_edges.groupby(group_key)["traceID"]
        .nunique()
        .reset_index()
        .rename(columns={"traceID": "_unique_traces"})
    )
    agg = agg.merge(trace_cov, on=group_key, how="left")
    agg["edge_trace_coverage_rate"] = agg["_unique_traces"] / total_traces
    agg.drop(columns=["_unique_traces"], inplace=True)

    # ── Operation entropy ─────────────────────────────────────────────────────
    op_entropy = (
        raw_edges.groupby(group_key)["callee_operation"]
        .agg(
            edge_operation_entropy = _shannon_entropy,
            edge_operation_count   = "nunique",
        )
        .reset_index()
    )
    agg = agg.merge(op_entropy, on=group_key, how="left")

    # ── Metadata ──────────────────────────────────────────────────────────────
    agg["total_traces"] = total_traces
    agg["signal_class"] = "trace_only"

    # ── Round floats for readability ──────────────────────────────────────────
    float_cols = [
        "edge_mean_latency_ms", "edge_std_latency_ms",
        "edge_p50_latency_ms", "edge_p95_latency_ms", "edge_p99_latency_ms",
        "edge_latency_cv", "edge_p95_p50_ratio",
        "edge_error_rate", "edge_observation_rate", "edge_trace_coverage_rate",
        "edge_operation_entropy",
    ]
    for col in float_cols:
        if col in agg.columns:
            agg[col] = agg[col].round(6)

    log.info("[edge_features] Extracted features for %d edges", len(agg))
    return agg.sort_values("edge_observation_count", ascending=False).reset_index(drop=True)


# ─── CLI entry point ──────────────────────────────────────────────────────────


def main(
    spans_glob: str = _io.DEFAULT_SPANS,
    output_dir: str = str(Path(__file__).parent / "outputs"),
    dry_run: bool = False,
) -> pd.DataFrame:
    spans  = _io.load_spans(spans_glob)
    result = extract(spans)

    if dry_run:
        print(f"\n[edge_features] {len(result)} edges × {len(result.columns)} features")
        print(result.dtypes.to_string())
        return result

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "edge_features.csv"
    result.to_csv(path, index=False)
    log.info("[edge_features] Saved → %s", path)
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    p = argparse.ArgumentParser(description="Extract edge-level telemetry features")
    p.add_argument("--spans-glob",  default=_io.DEFAULT_SPANS)
    p.add_argument("--output-dir",  default=str(Path(__file__).parent / "outputs"))
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    main(args.spans_glob, args.output_dir, args.dry_run)
