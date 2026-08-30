import os
import pandas as pd
import numpy as np
import logging
from algorithm import CERCA
from microrca import evaluate_true_microrca
from sklearn.utils import resample

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

def mrr_score(ranks: list[int]) -> float:
    return np.mean([1.0 / r for r in ranks])

def top_k_accuracy(ranks: list[int], k: int) -> float:
    return np.mean([1.0 if r <= k else 0.0 for r in ranks])

def evaluate_baseline_latency(features_df: pd.DataFrame) -> pd.DataFrame:
    df = features_df.copy()
    if "service_mean_latency_ms" in df.columns:
        df["score"] = df["service_mean_latency_ms"].fillna(0)
    else:
        df["score"] = 0.0
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df[["service", "rank", "score"]]

def evaluate_baseline_error(features_df: pd.DataFrame) -> pd.DataFrame:
    df = features_df.copy()
    if "error_rate" in df.columns:
        df["score"] = df["error_rate"].fillna(0)
    else:
        df["score"] = 0.0
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df[["service", "rank", "score"]]

def evaluate_baseline_pagerank(features_df: pd.DataFrame, graph_path: str) -> pd.DataFrame:
    import json
    import networkx as nx
    df = features_df.copy()
    with open(graph_path, "r") as f:
        adj = json.load(f)
    G = nx.DiGraph()
    for node in adj.get("nodes", []): G.add_node(node["id"])
    for edge in adj.get("edges", []): G.add_edge(edge["source"], edge["target"])
    H = G.reverse()
    try:
        pr = nx.pagerank(H, alpha=0.85)
    except:
        pr = {n: 1.0/len(H) for n in H.nodes()}
    df["score"] = df["service"].map(pr).fillna(0)
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df[["service", "rank", "score"]]

def simulate_telemetry_loss(df: pd.DataFrame, drop_prob: float):
    """Robustness Test: Randomly mask out telemetry for drop_prob % of nodes to simulate sampling drops."""
    noisy_df = df.copy()
    np.random.seed(42)
    services = noisy_df["service"].unique()
    num_drop = int(len(services) * drop_prob)
    drop_nodes = np.random.choice(services, size=num_drop, replace=False).tolist()
    
    noisy_df.loc[noisy_df["service"].isin(drop_nodes), "metric_samples_available"] = 0
    noisy_df.loc[noisy_df["service"].isin(drop_nodes), "total_traces"] = 0
    noisy_df.loc[noisy_df["service"].isin(drop_nodes), "service_trace_participation_rate"] = 0.0
    for col in noisy_df.columns:
        if col.startswith("service_") and col not in ["service", "service_span_count", "service_error_count"]:
            noisy_df.loc[noisy_df["service"].isin(drop_nodes), col] = 0.0
            
    return noisy_df, drop_nodes

def bootstrap_ci(ranks: list[int], n_iterations: int = 1000):
    """Compute 95% Confidence Intervals using Bootstrapping."""
    mrr_stats, top1_stats, top3_stats = [], [], []
    for _ in range(n_iterations):
        sample = resample(ranks, replace=True)
        mrr_stats.append(mrr_score(sample))
        top1_stats.append(top_k_accuracy(sample, 1))
        top3_stats.append(top_k_accuracy(sample, 3))
        
    def ci(data):
        return np.percentile(data, 2.5), np.percentile(data, 97.5)
        
    return {
        "mrr_ci": ci(mrr_stats),
        "top1_ci": ci(top1_stats),
        "top3_ci": ci(top3_stats)
    }

def main():
    print("═══ CERCA Evaluation Framework & Ablation Study ═══")
    dataset_path = "E:/CERCA/data/rca_dataset.csv"
    if not os.path.exists(dataset_path):
        print(f"Error: {dataset_path} not found.")
        return
        
    df = pd.read_csv(dataset_path)
    fault_exps = df[df["true_root_cause"] != "none"]["experiment_id"].unique()
    
    cerca_algo = CERCA()
    
    methods = [
        "CERCA (Full)", 
        "CERCA (No Graph)", 
        "CERCA (No Reliability)", 
        "CERCA (Equal Weights)",
        "Baseline (Latency)",
        "Baseline (Error Rate)",
        "Baseline (PageRank)",
        "Baseline (True MicroRCA)",
        "CERCA (20% Sampling)",
        "CERCA (5% Sampling)",
        "MicroRCA (20% Sampling)",
        "MicroRCA (5% Sampling)"
    ]
    
    results = {m: [] for m in methods}
    
    for exp_id in fault_exps:
        exp_df = df[df["experiment_id"] == exp_id].copy()
        true_rc = exp_df["true_root_cause"].iloc[0]
        graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
        
        # Standard Variants
        ranks_full = cerca_algo.rank_services(exp_df, graph_path, variant="full")
        results["CERCA (Full)"].append(ranks_full[ranks_full["service"] == true_rc]["rank"].iloc[0])
        
        ranks_nograph = cerca_algo.rank_services(exp_df, graph_path, variant="no_graph")
        results["CERCA (No Graph)"].append(ranks_nograph[ranks_nograph["service"] == true_rc]["rank"].iloc[0])
        
        ranks_norel = cerca_algo.rank_services(exp_df, graph_path, variant="no_reliability")
        results["CERCA (No Reliability)"].append(ranks_norel[ranks_norel["service"] == true_rc]["rank"].iloc[0])
        
        ranks_equal = cerca_algo.rank_services(exp_df, graph_path, variant="equal_weights")
        results["CERCA (Equal Weights)"].append(ranks_equal[ranks_equal["service"] == true_rc]["rank"].iloc[0])
        
        # Baselines
        ranks_lat = evaluate_baseline_latency(exp_df)
        results["Baseline (Latency)"].append(ranks_lat[ranks_lat["service"] == true_rc]["rank"].iloc[0])
        
        ranks_err = evaluate_baseline_error(exp_df)
        results["Baseline (Error Rate)"].append(ranks_err[ranks_err["service"] == true_rc]["rank"].iloc[0])
        
        ranks_pr = evaluate_baseline_pagerank(exp_df, graph_path)
        results["Baseline (PageRank)"].append(ranks_pr[ranks_pr["service"] == true_rc]["rank"].iloc[0])
        
        ranks_mrca = evaluate_true_microrca(exp_df, graph_path, exp_id)
        results["Baseline (True MicroRCA)"].append(ranks_mrca[ranks_mrca["service"] == true_rc]["rank"].iloc[0])
        
        # 20% Sampling (80% Drop)
        df_80, drop_80 = simulate_telemetry_loss(exp_df, 0.80)
        ranks_cerca_80 = cerca_algo.rank_services(df_80, graph_path, variant="full")
        results["CERCA (20% Sampling)"].append(ranks_cerca_80[ranks_cerca_80["service"] == true_rc]["rank"].iloc[0])
        
        ranks_mrca_80 = evaluate_true_microrca(df_80, graph_path, exp_id, drop_nodes=drop_80)
        results["MicroRCA (20% Sampling)"].append(ranks_mrca_80[ranks_mrca_80["service"] == true_rc]["rank"].iloc[0])
        
        # 5% Sampling (95% Drop)
        df_95, drop_95 = simulate_telemetry_loss(exp_df, 0.95)
        ranks_cerca_95 = cerca_algo.rank_services(df_95, graph_path, variant="full")
        results["CERCA (5% Sampling)"].append(ranks_cerca_95[ranks_cerca_95["service"] == true_rc]["rank"].iloc[0])
        
        ranks_mrca_95 = evaluate_true_microrca(df_95, graph_path, exp_id, drop_nodes=drop_95)
        results["MicroRCA (5% Sampling)"].append(ranks_mrca_95[ranks_mrca_95["service"] == true_rc]["rank"].iloc[0])
        
    print("\nEvaluation Results (Mean over 7 experiments with 95% CI):")
    print("-" * 115)
    print(f"{'Method':<25} | {'MRR':<25} | {'Top-1':<25} | {'Top-3':<25}")
    print("-" * 115)
    
    metrics_data = []
    
    for method in methods:
        ranks = results[method]
        mrr = mrr_score(ranks)
        top1 = top_k_accuracy(ranks, 1)
        top3 = top_k_accuracy(ranks, 3)
        
        # Bootstrap CI
        ci = bootstrap_ci(ranks)
        mrr_str = f"{mrr:.4f} ({ci['mrr_ci'][0]:.2f}-{ci['mrr_ci'][1]:.2f})"
        top1_str = f"{top1:.4f} ({ci['top1_ci'][0]:.2f}-{ci['top1_ci'][1]:.2f})"
        top3_str = f"{top3:.4f} ({ci['top3_ci'][0]:.2f}-{ci['top3_ci'][1]:.2f})"
        
        print(f"{method:<25} | {mrr_str:<25} | {top1_str:<25} | {top3_str:<25}")
        metrics_data.append({"Method": method, "MRR": mrr, "Top-1": top1, "Top-3": top3})
    
    pd.DataFrame(metrics_data).to_csv("E:/CERCA/cerca/evaluation_metrics.csv", index=False)

if __name__ == "__main__":
    main()
