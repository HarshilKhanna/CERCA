"""
telemetry_features/trace_features.py
─────────────────────────────────────
Extract per-trace structural integrity features from the span dataset.

Research Motivation
───────────────────
A single distributed trace is a causal tree of spans representing one
end-to-end request. For RCA, a trace is useful only if:
  1. It is structurally complete (no orphan spans, tree is connected).
  2. It has sufficient depth to traverse the service dependency graph.
  3. Error signals are correctly positioned (error depth relative to root).

These features characterise how *trustworthy* any given trace is as a unit
of evidence for causal inference.

The `trace_completeness_score` is specifically designed as a per-trace
reliability weight that downstream algorithms can use to discount
structurally-compromised traces.

Output Schema (trace_features.csv)
───────────────────────────────────
One row per unique traceID.

  traceID                  str
  trace_span_count         int    — total spans in trace
  trace_service_count      int    — unique services participating
  trace_depth              int    — max tree depth from root span (root = 0)
  trace_width_max          int    — max number of spans at any single depth level
  trace_orphan_count       int    — spans with parentSpanID not found in trace
  trace_completeness_score float  — 1 - (orphan_count / span_count) ∈ [0, 1]
  trace_has_error          bool   — any span has error=True
  trace_error_count        int    — total error spans
  trace_error_depth        int    — depth of first error span (-1 if no errors)
  trace_duration_us        int    — max(startTime + duration) - min(startTime)
  trace_is_single_span     bool   — span_count == 1 (likely incomplete)
  trace_root_service       str    — service of the root span (parentSpanID == "")
  signal_class             str    — "trace_only"

Mathematical Definitions
────────────────────────
  Let S_t = {span_i : span_i.traceID == t}
  Let parent(s) = span where spanID == s.parentSpanID ∈ S_t (or None)

  trace_depth              = max_{s ∈ S_t} depth(s)
                             where depth(root) = 0, depth(s) = 1 + depth(parent(s))
                             [If multiple roots exist due to orphans, each orphan
                              subtree root has depth 0]

  trace_completeness_score = 1 - |{s : s.parentSpanID ≠ "" ∧ parent(s) ∉ S_t}| / |S_t|

  trace_duration_us        = max(s.startTime + s.duration) - min(s.startTime)
                             over all s ∈ S_t

  trace_error_depth        = min_{s : s.error=True} depth(s)   [-1 if none]

Note on trace_depth computation:
  Tree depth is reconstructed via BFS from root spans. Orphan spans (parent
  not in trace) are treated as secondary roots (depth=0) to avoid infinite
  recursion. This is the only tractable approach without knowing the true
  parent.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

from . import _io

log = logging.getLogger(__name__)


# ─── Tree reconstruction ──────────────────────────────────────────────────────


def _build_depth_map(span_ids: set[str], parent_map: dict[str, str]) -> dict[str, int]:
    """
    BFS to compute depth of each span in a trace tree.

    Parameters
    ----------
    span_ids   : set of spanIDs in this trace
    parent_map : {spanID: parentSpanID} for all spans in trace

    Returns
    -------
    dict {spanID: depth}
    Orphan spans (parent not in span_ids) are assigned depth 0 (treated as roots).
    """
    # Build children map
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []

    for sid, pid in parent_map.items():
        if pid == "" or pid not in span_ids:
            roots.append(sid)
        else:
            children[pid].append(sid)

    # BFS
    depth_map: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque((r, 0) for r in roots)

    while queue:
        node, d = queue.popleft()
        depth_map[node] = d
        for child in children[node]:
            if child not in depth_map:
                queue.append((child, d + 1))

    # Any span not reached (cycles, which shouldn't exist but guard anyway)
    for sid in span_ids:
        if sid not in depth_map:
            depth_map[sid] = 0

    return depth_map


def _width_at_depth(depth_map: dict[str, int]) -> int:
    """Return max number of spans at any single depth level."""
    if not depth_map:
        return 0
    counts: dict[int, int] = defaultdict(int)
    for d in depth_map.values():
        counts[d] += 1
    return max(counts.values())


# ─── Per-trace feature computation ───────────────────────────────────────────


def _process_trace(trace_id: str, group: pd.DataFrame) -> dict:
    """Compute all features for a single trace group."""
    span_ids = set(group["spanID"])
    parent_map = dict(zip(group["spanID"], group["parentSpanID"]))

    # Orphans: non-root spans whose parent is not in this trace
    child_mask     = group["parentSpanID"] != ""
    child_spans    = group[child_mask]
    orphan_mask    = child_mask & ~group["parentSpanID"].isin(span_ids)
    orphan_count   = int(orphan_mask.sum())
    span_count     = len(group)
    completeness   = 1.0 - (orphan_count / span_count) if span_count > 0 else 1.0

    # Tree depth and width
    depth_map  = _build_depth_map(span_ids, parent_map)
    max_depth  = max(depth_map.values()) if depth_map else 0
    max_width  = _width_at_depth(depth_map)

    # Errors
    has_error    = bool(group["error"].any())
    error_count  = int(group["error"].sum())
    error_depth  = -1
    if has_error:
        error_spans = group[group["error"] == True]["spanID"]
        error_depths = [depth_map.get(sid, 0) for sid in error_spans]
        error_depth  = int(min(error_depths))

    # Trace duration
    end_times = (group["startTime_us"] + group["duration_us"]).dropna()
    start_min = group["startTime_us"].dropna().min()
    trace_duration = int(end_times.max() - start_min) if (len(end_times) and not pd.isna(start_min)) else 0

    # Root service (span with parentSpanID == "")
    roots = group[group["parentSpanID"] == ""]
    root_service = str(roots["service"].iloc[0]) if len(roots) else "unknown"

    return {
        "traceID":                trace_id,
        "trace_span_count":       span_count,
        "trace_service_count":    int(group["service"].nunique()),
        "trace_depth":            max_depth,
        "trace_width_max":        max_width,
        "trace_orphan_count":     orphan_count,
        "trace_completeness_score": round(completeness, 6),
        "trace_has_error":        has_error,
        "trace_error_count":      error_count,
        "trace_error_depth":      error_depth,
        "trace_duration_us":      trace_duration,
        "trace_is_single_span":   span_count == 1,
        "trace_root_service":     root_service,
        "signal_class":           "trace_only",
    }


# ─── Main extractor ───────────────────────────────────────────────────────────


def extract(spans: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-trace structural integrity features.

    Parameters
    ----------
    spans : pd.DataFrame
        Output of _io.load_spans()

    Returns
    -------
    pd.DataFrame
        One row per unique traceID.
    """
    total_traces = spans["traceID"].nunique()
    log.info(
        "[trace_features] Processing %d unique traces (%d spans total) …",
        total_traces, len(spans),
    )

    rows = []
    for trace_id, group in spans.groupby("traceID"):
        rows.append(_process_trace(trace_id, group))

    result = pd.DataFrame(rows)

    # Log sanity check values
    log.info(
        "[trace_features] Completeness score: mean=%.3f  min=%.3f  (n_single_span=%d)",
        result["trace_completeness_score"].mean(),
        result["trace_completeness_score"].min(),
        int(result["trace_is_single_span"].sum()),
    )
    total_violations = int(
        (result["temporal_ordering_violations"] if "temporal_ordering_violations" in result.columns
         else pd.Series([0])).sum()
    )

    log.info("[trace_features] Error traces: %d / %d", int(result["trace_has_error"].sum()), total_traces)
    return result.sort_values("trace_span_count", ascending=False).reset_index(drop=True)


# ─── CLI entry point ──────────────────────────────────────────────────────────


def main(
    spans_glob: str = _io.DEFAULT_SPANS,
    output_dir: str = str(Path(__file__).parent / "outputs"),
    dry_run: bool = False,
) -> pd.DataFrame:
    spans  = _io.load_spans(spans_glob)
    result = extract(spans)

    if dry_run:
        print(f"\n[trace_features] {len(result)} traces × {len(result.columns)} features")
        print(result[[
            "trace_span_count", "trace_depth", "trace_completeness_score",
            "trace_has_error", "trace_is_single_span"
        ]].describe().to_string())
        return result

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "trace_features.csv"
    result.to_csv(path, index=False)
    log.info("[trace_features] Saved → %s", path)
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    p = argparse.ArgumentParser(description="Extract per-trace structural integrity features")
    p.add_argument("--spans-glob",  default=_io.DEFAULT_SPANS)
    p.add_argument("--output-dir",  default=str(Path(__file__).parent / "outputs"))
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    main(args.spans_glob, args.output_dir, args.dry_run)
