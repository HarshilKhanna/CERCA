"""
telemetry_features/service_features.py
───────────────────────────────────────
Extract per-service features from span and metric datasets.

Two Signal Classes
──────────────────
This module intentionally keeps trace-derived and metric-derived features
separate in the output. Each row carries a `signal_class` indicator:
  "trace_only"   → computed from spans only (full microsecond resolution)
  "metric_coarse" → computed from Prometheus (15s container-level resolution)

See docs/feasibility_report.md §3 for the cross-signal alignment discussion.

Output Schema (service_features.csv)
──────────────────────────────────────
Trace-derived features (signal_class = "trace_only"):
  service                         str
  total_traces                    int   — dataset denominator
  service_span_count              int   — total spans emitted by this service
  service_trace_participation_rate float — unique traces with this service / total
  service_error_rate              float — sum(error) / span_count ∈ [0, 1]
  service_error_count             int
  service_mean_latency_ms         float
  service_std_latency_ms          float
  service_p50_latency_ms          float
  service_p95_latency_ms          float
  service_p99_latency_ms          float
  service_latency_cv              float — std / mean ∈ [0, ∞); NaN if mean = 0
  service_root_span_rate          float — fraction with parentSpanID == ""
  service_fan_out_mean            float — mean children per span for this service
  service_orphan_span_rate        float — fraction with parent not found in trace

Metric-derived features (appended as separate columns):
  service_cpu_cv                  float — CV of CPU rate over window (15s resolution)
  service_cpu_mean_rate           float — mean CPU rate (seconds/second)
  service_mem_mean_bytes          float — mean memory_usage_bytes
  service_mem_stability           float — 1 - (max-min)/mean over window ∈ (-∞, 1]
  service_net_rx_mean_rate        float — mean network receive rate (bytes/s)
  service_net_tx_mean_rate        float — mean network transmit rate (bytes/s)
  metric_samples_available        int   — Prometheus samples found for this service
  signal_class                    str

Mathematical Definitions
────────────────────────
  Let X_s = {span_i : span_i.service == s}
  Let T = total unique trace IDs.

  service_span_count              = |X_s|
  service_trace_participation_rate= |{traceID : ∃ x ∈ X_s}| / T
  service_error_rate              = Σ error_i / |X_s|
  service_latency_cv              = σ(duration_us) / μ(duration_us)
  service_root_span_rate          = |{x : parentSpanID == ""}| / |X_s|
  service_fan_out_mean            = mean(|children(x)| for x ∈ X_s)
  service_orphan_span_rate        = |{x : parentSpanID ≠ "" ∧ parent ∉ trace}| / |X_s|

  service_cpu_cv                  = σ(cpu_rate_samples) / μ(cpu_rate_samples)
  service_mem_stability           = 1 - (max(mem) - min(mem)) / mean(mem)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import _io

log = logging.getLogger(__name__)

METRIC_CPU    = "container_cpu_usage_seconds_total"
METRIC_MEM    = "container_memory_usage_bytes"
METRIC_NET_RX = "container_network_receive_bytes_total"
METRIC_NET_TX = "container_network_transmit_bytes_total"


# ─── Trace-derived features ───────────────────────────────────────────────────


def _compute_fan_out(spans: pd.DataFrame) -> pd.Series:
    """
    Compute mean number of children per span, grouped by the PARENT service.

    Fan-out measures how many downstream calls a service triggers per span.
    A service with high fan-out amplifies cascading failures.
    """
    # For each span that is a child, find its parent's service
    child = spans[spans["parentSpanID"] != ""].copy()
    parent_svc = (
        spans[["traceID", "spanID", "service"]]
        .rename(columns={"spanID": "parentSpanID", "service": "parent_service"})
    )
    merged = child.merge(parent_svc, on=["traceID", "parentSpanID"], how="inner")

    # Count children per (parent_service, parent_spanID)
    child_counts = (
        merged.groupby(["parent_service", "parentSpanID"])
        .size()
        .reset_index(name="child_count")
    )
    # Mean children per service
    fan_out = (
        child_counts.groupby("parent_service")["child_count"]
        .mean()
        .rename("service_fan_out_mean")
    )
    return fan_out


def _compute_orphan_rate(spans: pd.DataFrame) -> pd.Series:
    """
    Orphan spans: spans with a non-empty parentSpanID whose parent does not
    appear in the same trace.

    These indicate one of:
      (a) The parent span was dropped by the Jaeger agent (buffer overflow)
      (b) The parent span belongs to a different service not queried
      (c) Clock/ID mismatch in instrumentation

    Orphan rate = orphan_count / span_count per service.
    This is a proxy for instrumentation loss — NOT a definitive measure.
    See feasibility_report.md §1.1 for the ambiguity caveat.
    """
    # Build set of valid (traceID, spanID) pairs
    valid_pairs = set(zip(spans["traceID"], spans["spanID"]))

    # Child spans (non-root)
    child = spans[spans["parentSpanID"] != ""].copy()

    # A child is orphaned if its (traceID, parentSpanID) is not in valid_pairs
    child["_is_orphan"] = child.apply(
        lambda row: (row["traceID"], row["parentSpanID"]) not in valid_pairs,
        axis=1,
    )
    orphan_counts = (
        child.groupby("service")["_is_orphan"]
        .agg(orphan_count="sum", child_count="count")
        .reset_index()
    )
    orphan_counts["service_orphan_span_rate"] = (
        orphan_counts["orphan_count"] / orphan_counts["child_count"]
    )
    return orphan_counts.set_index("service")["service_orphan_span_rate"]


def _trace_features_from_spans(spans: pd.DataFrame) -> pd.DataFrame:
    """Core per-service aggregations from the span dataset."""
    total_traces = spans["traceID"].nunique()

    agg = (
        spans.groupby("service")
        .agg(
            service_span_count      = ("spanID",      "count"),
            service_error_count     = ("error",        "sum"),
            service_mean_latency_ms = ("duration_us",  lambda x: x.mean() / 1000),
            service_std_latency_ms  = ("duration_us",  lambda x: x.std()  / 1000),
            service_p50_latency_ms  = ("duration_us",  lambda x: np.percentile(x, 50) / 1000),
            service_p95_latency_ms  = ("duration_us",  lambda x: np.percentile(x, 95) / 1000),
            service_p99_latency_ms  = ("duration_us",  lambda x: np.percentile(x, 99) / 1000),
        )
        .reset_index()
    )

    agg["service_error_rate"] = agg["service_error_count"] / agg["service_span_count"]
    agg["service_latency_cv"] = np.where(
        agg["service_mean_latency_ms"] > 0,
        agg["service_std_latency_ms"] / agg["service_mean_latency_ms"],
        np.nan,
    )

    # Trace participation rate
    trace_part = (
        spans.groupby("service")["traceID"]
        .nunique()
        .reset_index()
        .rename(columns={"traceID": "_unique_traces"})
    )
    agg = agg.merge(trace_part, on="service", how="left")
    agg["service_trace_participation_rate"] = agg["_unique_traces"] / total_traces
    agg.drop(columns=["_unique_traces"], inplace=True)

    # Root span rate
    root_counts = (
        spans.groupby("service")
        .apply(lambda g: (g["parentSpanID"] == "").sum() / len(g))
        .reset_index(name="service_root_span_rate")
    )
    agg = agg.merge(root_counts, on="service", how="left")

    # Fan-out
    fan_out = _compute_fan_out(spans).reset_index()
    fan_out.columns = ["service", "service_fan_out_mean"]
    agg = agg.merge(fan_out, on="service", how="left")
    agg["service_fan_out_mean"] = agg["service_fan_out_mean"].fillna(0.0)

    # Orphan span rate
    orphan = _compute_orphan_rate(spans).reset_index()
    orphan.columns = ["service", "service_orphan_span_rate"]
    agg = agg.merge(orphan, on="service", how="left")
    agg["service_orphan_span_rate"] = agg["service_orphan_span_rate"].fillna(0.0)

    agg["total_traces"] = total_traces
    return agg


# ─── Metric-derived features ──────────────────────────────────────────────────


def _metric_features_from_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """
    Compute coarse (15s resolution) resource stability features per service.

    IMPORTANT: These features are computed at container-level, 15s aggregation.
    They cannot be correlated with individual spans without interpolation assumptions.
    See feasibility_report.md §3 for the cross-signal alignment problem.
    """
    rows = []
    services = metrics["service"].dropna().unique()

    for svc in services:
        svc_metrics = metrics[metrics["service"] == svc]
        row = {"service": svc}

        # CPU coefficient of variation
        cpu = svc_metrics[svc_metrics["metric_name"] == METRIC_CPU]["value"]
        if len(cpu) >= 2:
            row["service_cpu_mean_rate"] = float(cpu.mean())
            row["service_cpu_cv"] = (
                float(cpu.std() / cpu.mean()) if cpu.mean() > 0 else float("nan")
            )
        else:
            row["service_cpu_mean_rate"] = float("nan")
            row["service_cpu_cv"]        = float("nan")

        # Memory stability: 1 - (max-min)/mean
        mem = svc_metrics[svc_metrics["metric_name"] == METRIC_MEM]["value"]
        if len(mem) >= 2 and mem.mean() > 0:
            row["service_mem_mean_bytes"] = float(mem.mean())
            row["service_mem_stability"]  = float(1.0 - (mem.max() - mem.min()) / mem.mean())
        else:
            row["service_mem_mean_bytes"] = float("nan")
            row["service_mem_stability"]  = float("nan")

        # Network rates
        rx = svc_metrics[svc_metrics["metric_name"] == METRIC_NET_RX]["value"]
        tx = svc_metrics[svc_metrics["metric_name"] == METRIC_NET_TX]["value"]
        row["service_net_rx_mean_rate"] = float(rx.mean()) if len(rx) else float("nan")
        row["service_net_tx_mean_rate"] = float(tx.mean()) if len(tx) else float("nan")

        row["metric_samples_available"] = int(len(svc_metrics))
        rows.append(row)

    return pd.DataFrame(rows)


# ─── Main extractor ───────────────────────────────────────────────────────────


def extract(
    spans: pd.DataFrame,
    metrics: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Compute all service-level features.

    Parameters
    ----------
    spans : pd.DataFrame
        Output of _io.load_spans()
    metrics : pd.DataFrame or None
        Output of _io.load_metrics(). If None, metric columns are NaN.

    Returns
    -------
    pd.DataFrame
        One row per service name.
    """
    log.info("[service_features] Computing trace-derived features …")
    result = _trace_features_from_spans(spans)

    if metrics is not None and not metrics.empty:
        log.info("[service_features] Computing metric-derived features (coarse, 15s) …")
        met = _metric_features_from_metrics(metrics)
        result = result.merge(met, on="service", how="left")
        result["signal_class"] = "trace_and_metric_coarse"
    else:
        log.warning("[service_features] No metrics provided — metric columns will be NaN")
        for col in [
            "service_cpu_cv", "service_cpu_mean_rate", "service_mem_mean_bytes",
            "service_mem_stability", "service_net_rx_mean_rate", "service_net_tx_mean_rate",
            "metric_samples_available",
        ]:
            result[col] = float("nan")
        result["metric_samples_available"] = 0
        result["signal_class"] = "trace_only"

    # Round floats
    float_cols = [
        "service_mean_latency_ms", "service_std_latency_ms",
        "service_p50_latency_ms", "service_p95_latency_ms", "service_p99_latency_ms",
        "service_latency_cv", "service_error_rate", "service_trace_participation_rate",
        "service_root_span_rate", "service_fan_out_mean", "service_orphan_span_rate",
        "service_cpu_cv", "service_cpu_mean_rate", "service_mem_mean_bytes",
        "service_mem_stability", "service_net_rx_mean_rate", "service_net_tx_mean_rate",
    ]
    for col in float_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").round(6)

    log.info("[service_features] Extracted features for %d services", len(result))
    return result.sort_values("service_span_count", ascending=False).reset_index(drop=True)


# ─── CLI entry point ──────────────────────────────────────────────────────────


def main(
    spans_glob: str = _io.DEFAULT_SPANS,
    metrics_glob: str = _io.DEFAULT_METRICS,
    output_dir: str = str(Path(__file__).parent / "outputs"),
    dry_run: bool = False,
) -> pd.DataFrame:
    spans = _io.load_spans(spans_glob)
    try:
        metrics = _io.load_metrics(metrics_glob)
    except FileNotFoundError:
        log.warning("[service_features] No metrics found — proceeding trace-only")
        metrics = None

    result = extract(spans, metrics)

    if dry_run:
        print(f"\n[service_features] {len(result)} services × {len(result.columns)} features")
        print(result[["service", "service_span_count", "service_error_rate",
                       "service_latency_cv", "signal_class"]].to_string())
        return result

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "service_features.csv"
    result.to_csv(path, index=False)
    log.info("[service_features] Saved → %s", path)
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    p = argparse.ArgumentParser(description="Extract service-level telemetry features")
    p.add_argument("--spans-glob",   default=_io.DEFAULT_SPANS)
    p.add_argument("--metrics-glob", default=_io.DEFAULT_METRICS)
    p.add_argument("--output-dir",   default=str(Path(__file__).parent / "outputs"))
    p.add_argument("--dry-run",      action="store_true")
    args = p.parse_args()
    main(args.spans_glob, args.metrics_glob, args.output_dir, args.dry_run)
