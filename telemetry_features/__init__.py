"""
telemetry_features/__init__.py
──────────────────────────────
CERCA Telemetry Feature Extraction Module

Extracts research-grade features from the collected telemetry datasets:
  • spans_<ts>.csv  (Jaeger distributed traces)
  • metrics_<ts>.csv (Prometheus/cAdvisor container metrics)
  • dependency_graph.csv (pre-aggregated edge statistics)

Feature modules:
  edge_features     → per (caller, callee) structural + statistical features
  service_features  → per service span-level + metric-level features
  temporal_features → metric coverage + span density + clock skew
  trace_features    → per-trace structural integrity features

All features are derived strictly from the available telemetry.
No features are invented or assumed.

See docs/feasibility_report.md for the full feasibility analysis.
"""

__version__ = "1.0.0"
__all__ = ["edge_features", "service_features", "temporal_features", "trace_features"]
