"""
benchmark_latency.py — Isolated pure-algorithmic latency benchmark for CERCA vs MicroRCA.

Measures ONLY the inference computation time, explicitly excluding:
  - Dataset loading / CSV parsing
  - JSON graph file I/O
  - Disk writes / plot generation

Usage:
    python benchmark_latency.py [--n-repeats 200]

Hardware: AMD Ryzen 7 7840HS @ 3.80 GHz, 16 GB DDR5, Windows 11
"""
import sys, argparse, json, time
sys.path.insert(0, "E:/CERCA/cerca")

import numpy as np
import pandas as pd
import networkx as nx
from algorithm import CERCA
from microrca import evaluate_true_microrca

# ---------------------------------------------------------------------------
# Load data once (excluded from timing)
# ---------------------------------------------------------------------------
DATASET_PATH   = "E:/CERCA/data/rca_dataset.csv"
GRAPH_PATH     = "E:/CERCA/experiments/20260728_225443/graphs/dependency_graph.json"
EXPERIMENT_ID  = "20260728_225443"

df_full   = pd.read_csv(DATASET_PATH)
exp_df    = df_full[df_full["experiment_id"] == EXPERIMENT_ID].copy()
true_rc   = exp_df["true_root_cause"].iloc[0]

# Pre-load graph JSON once (excluded from timing)
with open(GRAPH_PATH) as f:
    adj_data = json.load(f)

cerca_algo = CERCA()

# Pre-build graph for CERCA (excluded from timing)
G_prebuilt = nx.DiGraph()
for node in adj_data.get("nodes", []):
    G_prebuilt.add_node(node["id"])
for edge in adj_data.get("edges", []):
    G_prebuilt.add_edge(edge["source"], edge["target"])
H_prebuilt = G_prebuilt.reverse()

# ---------------------------------------------------------------------------
# Isolated CERCA timing function (pure computation only)
# ---------------------------------------------------------------------------
def time_cerca_pure(df: pd.DataFrame, n: int) -> np.ndarray:
    """Time CERCA anomaly scoring + reliability + 1-hop propagation only."""
    timings = []
    df_indexed = df.copy()
    df_indexed.set_index("service", inplace=True)

    for _ in range(n):
        t0 = time.perf_counter()

        # Step 1: Anomaly score
        scores = cerca_algo.compute_anomaly_score(df_indexed)
        # Step 2: Reliability weight
        rel = cerca_algo.compute_reliability_weight(df_indexed)
        # Step 3: Local score
        local_scores = scores * rel
        # Step 4: 1-hop graph propagation (using pre-built graph)
        cerca_scores_raw = {}
        in_degree = dict(G_prebuilt.in_degree())
        for node in df_indexed.index:
            local = float(local_scores[node]) if node in local_scores.index else 0.0
            caller_blame = 0.0
            if node in H_prebuilt:
                callers = [c for c in H_prebuilt.successors(node) if c in df_indexed.index]
                n_callers = len(callers)
                for caller in callers:
                    contrib = float(local_scores[caller]) * cerca_algo.alpha
                    if n_callers > 1:
                        contrib /= n_callers
                    caller_blame += contrib
            cerca_scores_raw[node] = local + caller_blame

        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    return np.array(timings)


# ---------------------------------------------------------------------------
# Isolated MicroRCA timing function (pure PageRank computation only)
# ---------------------------------------------------------------------------
def time_microrca_pure(df: pd.DataFrame, n: int) -> np.ndarray:
    """Time MicroRCA anomaly scoring + subgraph + PageRank only."""
    timings = []
    df_indexed = df.copy()
    df_indexed.set_index("service", inplace=True)

    for _ in range(n):
        t0 = time.perf_counter()

        # Step 1: Find most anomalous service
        if "service_latency_cv" in df_indexed.columns:
            anomalous = df_indexed["service_latency_cv"].idxmax()
        else:
            anomalous = df_indexed.index[0]

        # Step 2: Subgraph extraction (1-hop)
        if anomalous in G_prebuilt:
            neighbors = (set(G_prebuilt.predecessors(anomalous)) |
                         set(G_prebuilt.successors(anomalous)))
            sub_G = G_prebuilt.subgraph(neighbors | {anomalous}).copy()
        else:
            sub_G = G_prebuilt.copy()

        # Step 3: Anomaly scores (latency CV based)
        anomaly_scores = {}
        for node in sub_G.nodes():
            if node in df_indexed.index and "service_latency_cv" in df_indexed.columns:
                anomaly_scores[node] = max(0.01, float(df_indexed.loc[node, "service_latency_cv"]))
            else:
                anomaly_scores[node] = 0.01

        # Step 4: Personalised PageRank
        H_sub = sub_G.reverse()
        total_p = sum(anomaly_scores.values())
        pers = {k: v / total_p for k, v in anomaly_scores.items()} if total_p > 0 else None
        try:
            pr = nx.pagerank(H_sub, alpha=0.85, personalization=pers, max_iter=100)
        except Exception:
            pr = pers or {}

        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    return np.array(timings)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Pure-algorithmic latency benchmark")
    parser.add_argument("--n-repeats", type=int, default=200,
                        help="Number of timed repetitions (default 200)")
    args = parser.parse_args()
    n = args.n_repeats

    print(f"Running {n} repetitions each — measuring PURE algorithmic time only")
    print(f"(graph I/O, CSV loading, disk writes excluded)\n")

    # Warm-up (excluded from results)
    time_cerca_pure(exp_df, 10)
    time_microrca_pure(exp_df, 10)

    cerca_times   = time_cerca_pure(exp_df, n)
    microrca_times = time_microrca_pure(exp_df, n)

    def report(name, t):
        print(f"  {name:30s}  mean={np.mean(t):.4f} ms  ±{np.std(t):.4f} ms"
              f"  p50={np.percentile(t,50):.4f}  p95={np.percentile(t,95):.4f}"
              f"  min={t.min():.4f}  max={t.max():.4f}")

    print("=== Results ===")
    report("CERCA (Full)", cerca_times)
    report("MicroRCA (PageRank)", microrca_times)

    speedup = np.mean(microrca_times) / np.mean(cerca_times)
    print(f"\n  Speedup (MicroRCA / CERCA): {speedup:.1f}×")

    # Save results
    results = {
        "n_repeats": n,
        "hardware": "AMD Ryzen 7 7840HS @ 3.80 GHz, 16 GB DDR5, Windows 11",
        "cerca_mean_ms": float(np.mean(cerca_times)),
        "cerca_std_ms":  float(np.std(cerca_times)),
        "cerca_p50_ms":  float(np.percentile(cerca_times, 50)),
        "cerca_p95_ms":  float(np.percentile(cerca_times, 95)),
        "microrca_mean_ms": float(np.mean(microrca_times)),
        "microrca_std_ms":  float(np.std(microrca_times)),
        "microrca_p50_ms":  float(np.percentile(microrca_times, 50)),
        "microrca_p95_ms":  float(np.percentile(microrca_times, 95)),
        "speedup_mean":  speedup,
        "note": (
            "Pure algorithmic latency only. "
            "Graph JSON pre-loaded before timing loop. "
            "CSV parsing, disk I/O, and plot generation excluded."
        )
    }
    import json as _json
    out_path = "E:/CERCA/cerca/benchmark_latency_results.json"
    with open(out_path, "w") as f:
        _json.dump(results, f, indent=2)
    print(f"\n  Saved to {out_path}")


if __name__ == "__main__":
    main()

