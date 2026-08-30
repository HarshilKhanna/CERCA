"""
telemetry_features/temporal_features.py
────────────────────────────────────────
Extract temporal consistency and coverage features from both signals.

Two sub-domains
───────────────
1. METRIC COVERAGE (from metrics CSV)
   Measures whether Prometheus scraped each service consistently over the
   collection window. Gaps indicate scrape failures, container restarts, or
   cAdvisor misconfiguration.

2. SPAN TEMPORAL CONSISTENCY (from spans CSV)
   Measures whether the distributed clock across services is consistent.
   A child span starting before its parent indicates clock skew between hosts,
   which degrades trace-based causal inference.

   Also computes span density per 15s bucket — intentionally aligned with the
   Prometheus scrape interval to enable crude cross-signal window comparison.

Output Schema (temporal_features.csv)
──────────────────────────────────────
One row per (service, metric_name) for metric features, plus one row per
service for span temporal features. The `feature_domain` column separates them.

Metric coverage features (feature_domain = "metric_coverage"):
  service               str
  metric_name           str
  expected_samples      int   — window_duration_s / step_s
  actual_samples        int   — observed Prometheus samples
  metric_coverage_ratio float — actual / expected ∈ [0, 1] (>1 impossible)
  metric_gap_count      int   — number of gaps where Δt > 1.5 × step_s
  metric_max_gap_s      float — largest consecutive gap in seconds
  step_s                float — inferred scrape step in seconds
  window_s              float — total observation window in seconds

Span temporal features (feature_domain = "span_temporal"):
  service                       str
  metric_name                   str   — "span_metrics" (sentinel)
  temporal_ordering_violations  int   — child.startTime < parent.startTime
  clock_skew_rate               float — violations / total parent-child pairs
  span_density_per_15s_mean     float — mean spans per 15s bucket
  span_density_per_15s_std      float — std dev of spans per 15s bucket
  span_density_per_15s_cv       float — CV of span density (load variability)

Mathematical Definitions
────────────────────────
  Let W = collection window duration in seconds.
  Let Δ = configured scrape step (inferred as median inter-sample interval).

  expected_samples      = ⌈W / Δ⌉
  actual_samples        = |{t_k : M(service, metric, t_k) exists}|
  metric_coverage_ratio = actual_samples / expected_samples
  metric_gap_count      = |{k : t_{k+1} - t_k > 1.5·Δ}|
  metric_max_gap_s      = max_{k} (t_{k+1} - t_k)    [0 if no gaps]

  clock_skew_rate = |{(child, parent) : child.startTime < parent.startTime}|
                    / total_parent_child_pairs

  span_density_per_15s  = |{spans with startTime in [b, b+15s)}|  for each 15s bucket b
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import _io

log = logging.getLogger(__name__)

INFERRED_STEP_S = 15.0  # default; overridden by actual data if available


# ─── Metric coverage features ─────────────────────────────────────────────────


def _infer_step(ts: pd.Series) -> float:
    """Infer scrape step from median inter-sample interval (seconds)."""
    sorted_ts = ts.sort_values()
    diffs = sorted_ts.diff().dropna()
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return INFERRED_STEP_S
    return float(diffs.median())


def _metric_coverage(metrics: pd.DataFrame, window_s: float) -> pd.DataFrame:
    """
    Compute coverage statistics for each (service, metric_name) pair.

    Parameters
    ----------
    metrics   : normalised metrics DataFrame
    window_s  : collection window duration in seconds (from span data or CLI)
    """
    rows = []
    for (svc, metric), grp in metrics.groupby(["service", "metric_name"]):
        ts = grp["timestamp_unix"].dropna().sort_values()
        if len(ts) < 2:
            rows.append({
                "service": svc, "metric_name": metric,
                "expected_samples": 1, "actual_samples": len(ts),
                "metric_coverage_ratio": float(len(ts)), "metric_gap_count": 0,
                "metric_max_gap_s": 0.0, "step_s": INFERRED_STEP_S,
                "window_s": window_s, "feature_domain": "metric_coverage",
            })
            continue

        step_s           = _infer_step(ts)
        expected_samples = max(1, round(window_s / step_s))
        actual_samples   = len(ts)
        coverage_ratio   = actual_samples / expected_samples

        diffs = ts.diff().dropna()
        gap_threshold    = 1.5 * step_s
        gaps             = diffs[diffs > gap_threshold]
        gap_count        = len(gaps)
        max_gap_s        = float(gaps.max()) if gap_count else 0.0

        rows.append({
            "service":              svc,
            "metric_name":          metric,
            "expected_samples":     expected_samples,
            "actual_samples":       actual_samples,
            "metric_coverage_ratio": round(coverage_ratio, 6),
            "metric_gap_count":     gap_count,
            "metric_max_gap_s":     round(max_gap_s, 3),
            "step_s":               round(step_s, 3),
            "window_s":             round(window_s, 3),
            "feature_domain":       "metric_coverage",
        })
    return pd.DataFrame(rows)


# ─── Span temporal consistency features ───────────────────────────────────────


def _clock_skew(spans: pd.DataFrame) -> pd.DataFrame:
    """
    Detect temporal ordering violations: child span starts before parent.

    This is computed per service (caller service perspective) to localise
    which service's clock is drifting.

    Returns DataFrame with one row per service.
    """
    child = spans[spans["parentSpanID"] != ""].copy()
    parent_times = (
        spans[["traceID", "spanID", "startTime_us"]]
        .rename(columns={"spanID": "parentSpanID", "startTime_us": "parent_start_us"})
    )
    merged = child.merge(parent_times, on=["traceID", "parentSpanID"], how="inner")

    merged["_violation"] = merged["startTime_us"] < merged["parent_start_us"]

    per_svc = (
        merged.groupby("service")
        .agg(
            temporal_ordering_violations = ("_violation", "sum"),
            _total_pairs                 = ("_violation", "count"),
        )
        .reset_index()
    )
    per_svc["clock_skew_rate"] = (
        per_svc["temporal_ordering_violations"] / per_svc["_total_pairs"]
    ).round(6)
    per_svc.drop(columns=["_total_pairs"], inplace=True)
    return per_svc


def _span_density(spans: pd.DataFrame, bucket_s: float = 15.0) -> pd.DataFrame:
    """
    Compute span density per (service, 15s bucket).

    Aligns with Prometheus scrape resolution so that span load can be
    compared against container resource samples in the same window.
    """
    df = spans.copy()
    df["bucket"] = (df["startTime_us"] // (bucket_s * 1e6)).astype(int)

    density = (
        df.groupby(["service", "bucket"])
        .size()
        .reset_index(name="span_count")
    )
    per_svc = (
        density.groupby("service")["span_count"]
        .agg(
            span_density_per_15s_mean = "mean",
            span_density_per_15s_std  = "std",
        )
        .reset_index()
    )
    per_svc["span_density_per_15s_cv"] = np.where(
        per_svc["span_density_per_15s_mean"] > 0,
        per_svc["span_density_per_15s_std"] / per_svc["span_density_per_15s_mean"],
        np.nan,
    )
    return per_svc


def _span_temporal_features(spans: pd.DataFrame) -> pd.DataFrame:
    """Combine clock skew and span density into one per-service DataFrame."""
    skew    = _clock_skew(spans)
    density = _span_density(spans)

    result = skew.merge(density, on="service", how="outer")
    result["metric_name"]    = "span_metrics"
    result["feature_domain"] = "span_temporal"

    # Fill NaN for services with no parent-child pairs (leaf services)
    result["temporal_ordering_violations"] = (
        result["temporal_ordering_violations"].fillna(0).astype(int)
    )
    result["clock_skew_rate"] = result["clock_skew_rate"].fillna(0.0)

    return result


# ─── Main extractor ───────────────────────────────────────────────────────────


def extract(
    spans: pd.DataFrame,
    metrics: pd.DataFrame | None = None,
    window_s: float | None = None,
) -> pd.DataFrame:
    """
    Compute all temporal features.

    Parameters
    ----------
    spans     : normalised spans DataFrame from _io.load_spans()
    metrics   : normalised metrics DataFrame from _io.load_metrics() (optional)
    window_s  : collection window duration override in seconds.
                If None, inferred from spans startTime_us range.

    Returns
    -------
    pd.DataFrame
        Mixed rows: metric_coverage and span_temporal, distinguished by
        `feature_domain` column.
    """
    # Infer window from spans if not provided
    if window_s is None:
        start_s, end_s = _io.infer_collection_window(spans)
        window_s = max(end_s - start_s, 60.0)  # at least 60s to avoid division edge cases
        log.info("[temporal_features] Inferred window: %.1f s", window_s)

    frames = []

    # Metric coverage
    if metrics is not None and not metrics.empty:
        log.info("[temporal_features] Computing metric coverage …")
        cov = _metric_coverage(metrics, window_s)
        frames.append(cov)
        log.info("[temporal_features] %d (service, metric) coverage rows", len(cov))
    else:
        log.warning("[temporal_features] No metrics — metric_coverage rows skipped")

    # Span temporal consistency
    log.info("[temporal_features] Computing span temporal consistency …")
    span_temp = _span_temporal_features(spans)
    frames.append(span_temp)
    log.info(
        "[temporal_features] Clock skew violations: %d total",
        int(span_temp["temporal_ordering_violations"].sum()),
    )

    result = pd.concat(frames, ignore_index=True, sort=False)
    return result


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
        log.warning("[temporal_features] No metrics found")
        metrics = None

    result = extract(spans, metrics)

    if dry_run:
        print(f"\n[temporal_features] {len(result)} rows × {len(result.columns)} features")
        print(result["feature_domain"].value_counts().to_string())
        return result

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "temporal_features.csv"
    result.to_csv(path, index=False)
    log.info("[temporal_features] Saved → %s", path)
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    p = argparse.ArgumentParser(description="Extract temporal telemetry features")
    p.add_argument("--spans-glob",   default=_io.DEFAULT_SPANS)
    p.add_argument("--metrics-glob", default=_io.DEFAULT_METRICS)
    p.add_argument("--output-dir",   default=str(Path(__file__).parent / "outputs"))
    p.add_argument("--dry-run",      action="store_true")
    args = p.parse_args()
    main(args.spans_glob, args.metrics_glob, args.output_dir, args.dry_run)
