"""
telemetry_features/_io.py
─────────────────────────
Shared data loading utilities for the CERCA feature extraction layer.

All loaders:
  • Resolve glob patterns (multiple experiment files → concatenated DataFrame)
  • Validate required columns against the known schema
  • Normalise types (e.g. ensure parentSpanID is str, not float NaN)
  • Return a DataFrame with a `_source_file` column for traceability

These loaders are intentionally thin — no feature computation happens here.
"""

from __future__ import annotations

import json
import logging
from glob import glob
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# ─── Known column schemas ─────────────────────────────────────────────────────

SPANS_REQUIRED_COLS = {
    "traceID", "spanID", "parentSpanID", "service", "operation",
    "startTime_us", "duration_us", "startTime_iso", "error",
}

METRICS_REQUIRED_COLS = {
    "metric_name", "service", "container_name", "labels_json",
    "timestamp_unix", "value",
}

GRAPH_REQUIRED_COLS = {
    "caller_service", "callee_service", "call_count",
    "mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
    "error_rate",
}

# Default glob patterns relative to project root
PROJECT_ROOT    = Path(__file__).parent.parent
DEFAULT_SPANS   = str(PROJECT_ROOT / "data" / "processed" / "spans_*.csv")
DEFAULT_METRICS = str(PROJECT_ROOT / "data" / "processed" / "metrics_*.csv")
DEFAULT_GRAPH   = str(PROJECT_ROOT / "graphs" / "dependency_graph.csv")


# ─── Generic loader ───────────────────────────────────────────────────────────


def _load_glob(
    pattern: str,
    required_cols: set[str],
    label: str,
    dtype_overrides: dict | None = None,
) -> pd.DataFrame:
    """
    Load one or more CSV files matching *pattern*, validate columns, and return
    a concatenated DataFrame.

    Parameters
    ----------
    pattern : str
        Glob pattern, e.g. "data/processed/spans_*.csv"
    required_cols : set[str]
        Columns that must be present; raises ValueError if any are missing.
    label : str
        Human-readable name for error messages.
    dtype_overrides : dict, optional
        Passed directly to pd.read_csv as dtype= argument.

    Returns
    -------
    pd.DataFrame
        Concatenated data from all matched files, with `_source_file` column.
    """
    files = sorted(glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No {label} files found matching: {pattern}\n"
            "Run scripts/fetch_traces.py (or fetch_metrics.py) first."
        )

    log.info("[_io] Loading %d %s file(s)", len(files), label)
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=dtype_overrides, low_memory=False)
        df["_source_file"] = Path(f).name
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)

    missing = required_cols - set(combined.columns)
    if missing:
        raise ValueError(
            f"{label} CSV is missing required columns: {missing}\n"
            "Has the schema changed? Check scripts/fetch_traces.py output."
        )

    log.info("[_io] Loaded %d %s rows from %d file(s)", len(combined), label, len(files))
    return combined


# ─── Span loader ──────────────────────────────────────────────────────────────


def load_spans(pattern: str = DEFAULT_SPANS) -> pd.DataFrame:
    """
    Load and normalise span data.

    Normalisation steps:
      • parentSpanID: NaN → "" (root spans have empty string, not float NaN)
      • error: cast to bool (may be stored as "True"/"False" string)
      • startTime_us / duration_us: cast to int64
      • Deduplicate on (traceID, spanID) — cross-service fetches may overlap
    """
    df = _load_glob(
        pattern,
        SPANS_REQUIRED_COLS,
        "spans",
        dtype_overrides={"traceID": str, "spanID": str, "parentSpanID": str},
    )

    # Normalise parentSpanID: NaN → "" (root or orphan detection uses this)
    df["parentSpanID"] = df["parentSpanID"].fillna("").astype(str).str.strip()
    # "nan" strings from dtype coercion
    df.loc[df["parentSpanID"].str.lower() == "nan", "parentSpanID"] = ""

    # Cast numeric columns
    df["startTime_us"] = pd.to_numeric(df["startTime_us"], errors="coerce").astype("Int64")
    df["duration_us"]  = pd.to_numeric(df["duration_us"],  errors="coerce").astype("Int64")

    # Normalise error column
    if df["error"].dtype == object:
        df["error"] = df["error"].map({"True": True, "False": False, True: True, False: False})
    df["error"] = df["error"].fillna(False).astype(bool)

    # Deduplicate (cross-service Jaeger queries fetch overlapping traces)
    before = len(df)
    df.drop_duplicates(subset=["traceID", "spanID"], inplace=True)
    dropped = before - len(df)
    if dropped:
        log.info("[_io] Deduplicated %d duplicate spans", dropped)

    return df.reset_index(drop=True)


# ─── Metrics loader ───────────────────────────────────────────────────────────


def load_metrics(pattern: str = DEFAULT_METRICS) -> pd.DataFrame:
    """
    Load and normalise metrics data.

    Normalisation steps:
      • timestamp_unix: ensure float
      • value: ensure float, drop rows where value is NaN
      • labels_json: kept as string (parse on demand with json.loads())
    """
    df = _load_glob(pattern, METRICS_REQUIRED_COLS, "metrics")

    df["timestamp_unix"] = pd.to_numeric(df["timestamp_unix"], errors="coerce")
    df["value"]          = pd.to_numeric(df["value"],          errors="coerce")

    bad = df["value"].isna().sum()
    if bad:
        log.warning("[_io] Dropping %d rows with NaN metric values", bad)
    df = df.dropna(subset=["value"]).reset_index(drop=True)

    return df


# ─── Dependency graph loader ──────────────────────────────────────────────────


def load_graph(pattern: str = DEFAULT_GRAPH) -> pd.DataFrame:
    """
    Load the pre-aggregated dependency graph edge list.

    The graph CSV is not glob-resolved (one file per experiment set).
    Pattern can be an exact path or a glob; first match is used.
    """
    files = sorted(glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No dependency graph CSV found at: {pattern}\n"
            "Run scripts/build_dependency_graph.py first."
        )
    df = pd.read_csv(files[0])
    missing = GRAPH_REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"dependency_graph.csv missing columns: {missing}")
    log.info("[_io] Loaded graph with %d edges from %s", len(df), files[0])
    return df


# ─── Utility ──────────────────────────────────────────────────────────────────


def infer_collection_window(spans: pd.DataFrame) -> tuple[float, float]:
    """
    Return (start_unix_s, end_unix_s) of the span collection window.
    Used by temporal_features to compute expected metric sample counts.
    """
    start_us = spans["startTime_us"].dropna().min()
    end_us   = (spans["startTime_us"] + spans["duration_us"]).dropna().max()
    return float(start_us) / 1e6, float(end_us) / 1e6


def parse_labels(labels_json: str) -> dict:
    """Safely parse a labels_json string into a dict."""
    try:
        return json.loads(labels_json)
    except (json.JSONDecodeError, TypeError):
        return {}
