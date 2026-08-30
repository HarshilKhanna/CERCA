"""
build_dependency_graph.py
─────────────────────────
Construct the service call dependency graph from the processed spans CSV and
persist it in three formats:

  • graphs/dependency_graph.json  – adjacency list with per-edge statistics
  • graphs/dependency_graph.csv   – edge list (caller, callee, call_count,
                                   mean_latency_ms, p50_latency_ms,
                                   p95_latency_ms, p99_latency_ms,
                                   error_rate, top_operations)
  • graphs/dependency_graph.png   – visualisation (NetworkX + Matplotlib)

How edges are derived
─────────────────────
  For every span S with a non-empty parentSpanID P:
    1. Look up P in the same trace → find its service name (caller).
    2. S's service name is the callee.
    3. Edge: caller → callee, weight = call count.

  Edge statistics are aggregated over ALL spans matching that (caller, callee)
  pair across ALL traces in the input CSV(s).

Usage
─────
    python scripts/build_dependency_graph.py [OPTIONS]

Options
    --spans-glob    Glob pattern for spans CSV files
                    [default: data/processed/spans_*.csv]
    --output-dir    Output directory for graph files [default: graphs/]
    --no-plot       Skip PNG generation (useful in headless environments)
    --verbose       Debug logging
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from glob import glob
from collections import defaultdict

import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_dependency_graph")

DEFAULT_SPANS_GLOB  = str(Path(__file__).parent.parent / "data" / "processed" / "spans_*.csv")
DEFAULT_GRAPH_DIR   = Path(__file__).parent.parent / "graphs"


# ─── Load & validate spans ────────────────────────────────────────────────────


def load_spans(glob_pattern: str) -> pd.DataFrame:
    files = sorted(glob(glob_pattern))
    if not files:
        log.error("No spans CSV files found matching: %s", glob_pattern)
        sys.exit(1)
    log.info("Loading %d span file(s): %s", len(files), [Path(f).name for f in files])
    frames = [pd.read_csv(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df.drop_duplicates(subset=["traceID", "spanID"], inplace=True)
    log.info("Total spans: %d  |  Unique traces: %d", len(df), df["traceID"].nunique())
    return df


# ─── Build span lookup & edges ────────────────────────────────────────────────


def build_edges(df: pd.DataFrame) -> pd.DataFrame:
    """
    Join child spans to their parent to extract (caller_service, callee_service) edges.
    Returns a DataFrame of raw edge instances (one row per span-parent relationship).
    """
    # Keep only spans that have a parent
    child = df[df["parentSpanID"].notna() & (df["parentSpanID"] != "")].copy()
    parent = df[["traceID", "spanID", "service", "operation"]].copy()
    parent.columns = ["traceID", "parentSpanID", "caller_service", "caller_operation"]

    merged = child.merge(parent, on=["traceID", "parentSpanID"], how="inner")
    merged = merged[merged["caller_service"] != merged["service"]].copy()  # drop self-loops

    merged.rename(columns={"service": "callee_service", "operation": "callee_operation"}, inplace=True)
    merged["duration_ms"] = merged["duration_us"] / 1000.0

    log.info(
        "Found %d parent→child cross-service edges (%d unique (caller, callee) pairs)",
        len(merged),
        merged[["caller_service", "callee_service"]].drop_duplicates().__len__(),
    )
    return merged


# ─── Aggregate edge statistics ────────────────────────────────────────────────


def aggregate_edges(raw_edges: pd.DataFrame) -> pd.DataFrame:
    """Compute per-(caller, callee) edge statistics."""
    agg = (
        raw_edges.groupby(["caller_service", "callee_service"])
        .agg(
            call_count       = ("spanID",      "count"),
            mean_latency_ms  = ("duration_ms", "mean"),
            p50_latency_ms   = ("duration_ms", lambda x: np.percentile(x, 50)),
            p95_latency_ms   = ("duration_ms", lambda x: np.percentile(x, 95)),
            p99_latency_ms   = ("duration_ms", lambda x: np.percentile(x, 99)),
            error_count      = ("error",        "sum"),
        )
        .reset_index()
    )
    agg["error_rate"] = agg["error_count"] / agg["call_count"]

    # Top-3 callee operations for each edge
    op_counts = (
        raw_edges.groupby(["caller_service", "callee_service", "callee_operation"])
        .size()
        .reset_index(name="op_count")
        .sort_values("op_count", ascending=False)
    )
    top_ops = (
        op_counts.groupby(["caller_service", "callee_service"])["callee_operation"]
        .apply(lambda x: list(x.head(3)))
        .reset_index()
        .rename(columns={"callee_operation": "top_operations"})
    )
    agg = agg.merge(top_ops, on=["caller_service", "callee_service"], how="left")
    agg["top_operations"] = agg["top_operations"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else ""
    )

    # Round floats
    for col in ["mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "error_rate"]:
        agg[col] = agg[col].round(3)

    return agg.sort_values("call_count", ascending=False)


# ─── Build adjacency list JSON ────────────────────────────────────────────────


def build_adjacency_list(agg: pd.DataFrame) -> dict:
    """Build a JSON-serialisable adjacency list from the aggregated edge table."""
    graph: dict = {"nodes": [], "edges": []}
    services = sorted(
        set(agg["caller_service"]) | set(agg["callee_service"])
    )
    graph["nodes"] = [{"id": svc} for svc in services]
    for _, row in agg.iterrows():
        graph["edges"].append({
            "source":          row["caller_service"],
            "target":          row["callee_service"],
            "call_count":      int(row["call_count"]),
            "mean_latency_ms": float(row["mean_latency_ms"]),
            "p50_latency_ms":  float(row["p50_latency_ms"]),
            "p95_latency_ms":  float(row["p95_latency_ms"]),
            "p99_latency_ms":  float(row["p99_latency_ms"]),
            "error_rate":      float(row["error_rate"]),
            "top_operations":  row["top_operations"],
        })
    return graph


# ─── Visualisation ────────────────────────────────────────────────────────────


def plot_graph(adj: dict, output_path: Path):
    """Render the dependency graph with NetworkX and Matplotlib."""
    try:
        import networkx as nx
        import matplotlib
        matplotlib.use("Agg")  # headless backend
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError as e:
        log.warning("Cannot import %s — skipping graph plot", e)
        return

    G = nx.DiGraph()
    for node in adj["nodes"]:
        G.add_node(node["id"])
    for edge in adj["edges"]:
        G.add_edge(
            edge["source"], edge["target"],
            weight=edge["call_count"],
            latency=edge["mean_latency_ms"],
        )

    fig, ax = plt.subplots(figsize=(18, 12))
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")

    pos = nx.spring_layout(G, seed=42, k=2.5)

    # Node colours by type (heuristic)
    INFRA_KEYWORDS  = {"mongodb", "redis", "memcached", "mcrouter"}
    GATEWAY_KW      = {"nginx"}
    OBSERV_KW       = {"jaeger", "prometheus", "cadvisor"}

    node_colors = []
    for node in G.nodes():
        n = node.lower()
        if any(kw in n for kw in INFRA_KEYWORDS):
            node_colors.append("#e94560")   # red – infrastructure
        elif any(kw in n for kw in GATEWAY_KW):
            node_colors.append("#f5a623")   # orange – gateway
        elif any(kw in n for kw in OBSERV_KW):
            node_colors.append("#7b68ee")   # purple – observability
        else:
            node_colors.append("#00d4aa")   # teal – application service

    # Edge thickness ∝ log(call_count)
    max_weight = max((G[u][v]["weight"] for u, v in G.edges()), default=1)
    edge_widths = [
        1 + 4 * (G[u][v]["weight"] / max_weight)
        for u, v in G.edges()
    ]

    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=1200,
                           alpha=0.9, ax=ax)
    nx.draw_networkx_labels(G, pos, font_size=7, font_color="white", ax=ax)
    nx.draw_networkx_edges(
        G, pos,
        width=edge_widths,
        edge_color="#4fc3f7",
        arrows=True,
        arrowsize=15,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.1",
        alpha=0.7,
        ax=ax,
    )

    # Legend
    legend_items = [
        mpatches.Patch(color="#00d4aa",  label="Application service"),
        mpatches.Patch(color="#e94560",  label="Infrastructure (DB/Cache)"),
        mpatches.Patch(color="#f5a623",  label="Gateway (nginx)"),
        mpatches.Patch(color="#7b68ee",  label="Observability"),
    ]
    ax.legend(handles=legend_items, loc="upper left", framealpha=0.3,
              labelcolor="white", facecolor="#1a1a2e", fontsize=9)
    ax.set_title(
        "DeathStarBench Social Network — Service Dependency Graph",
        color="white", fontsize=14, pad=15,
    )
    ax.axis("off")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)
    log.info("Graph plot saved → %s", output_path)


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Build service dependency graph from spans")
    parser.add_argument("--spans-glob",  default=DEFAULT_SPANS_GLOB,
                        help="Glob for spans CSV files (default: %(default)s)")
    parser.add_argument("--output-dir",  default=str(DEFAULT_GRAPH_DIR),
                        help="Output directory (default: %(default)s)")
    parser.add_argument("--no-plot",     action="store_true",
                        help="Skip PNG generation")
    parser.add_argument("--verbose",     action="store_true")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    graph_dir = Path(args.output_dir)
    graph_dir.mkdir(parents=True, exist_ok=True)

    # ── Load spans ────────────────────────────────────────────────────────────
    df = load_spans(args.spans_glob)

    # ── Build edges ───────────────────────────────────────────────────────────
    raw_edges = build_edges(df)
    if raw_edges.empty:
        log.warning("No cross-service edges found. Ensure parentSpanID is populated.")
        sys.exit(0)

    # ── Aggregate ─────────────────────────────────────────────────────────────
    agg = aggregate_edges(raw_edges)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    csv_cols = [
        "caller_service", "callee_service", "call_count",
        "mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
        "error_rate", "top_operations",
    ]
    csv_path = graph_dir / "dependency_graph.csv"
    agg[csv_cols].to_csv(csv_path, index=False)
    log.info("Edge list CSV saved → %s  (%d edges)", csv_path, len(agg))

    # ── Save adjacency list JSON ──────────────────────────────────────────────
    adj = build_adjacency_list(agg)
    json_path = graph_dir / "dependency_graph.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(adj, f, indent=2)
    log.info(
        "Adjacency list JSON saved → %s  (%d nodes, %d edges)",
        json_path, len(adj["nodes"]), len(adj["edges"]),
    )

    # ── Plot ──────────────────────────────────────────────────────────────────
    if not args.no_plot:
        plot_graph(adj, graph_dir / "dependency_graph.png")

    log.info("✓ Dependency graph build complete.")


if __name__ == "__main__":
    main()
