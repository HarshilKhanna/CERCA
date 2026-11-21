import os
import time
import json
import logging
import pandas as pd
import numpy as np
import networkx as nx
from scipy import stats
from sklearn.utils import resample

from algorithm import CERCA
from microrca import evaluate_true_microrca

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------
# Evaluation Metrics
# ---------------------------------------------------------
def mrr_score(ranks: list[int]) -> float:
    return float(np.mean([1.0 / r for r in ranks]))

def mar_score(ranks: list[int]) -> float:
    return float(np.mean(ranks))

def top_k_accuracy(ranks: list[int], k: int) -> float:
    return float(np.mean([1.0 if r <= k else 0.0 for r in ranks]))

def bootstrap_ci(ranks: list[int], n_iterations: int = 1000) -> dict:
    """Compute 95% Confidence Intervals using Bootstrapping."""
    mrr_stats, mar_stats, top1_stats, top3_stats, top5_stats = [], [], [], [], []
    for _ in range(n_iterations):
        sample = resample(ranks, replace=True)
        mrr_stats.append(mrr_score(sample))
        mar_stats.append(mar_score(sample))
        top1_stats.append(top_k_accuracy(sample, 1))
        top3_stats.append(top_k_accuracy(sample, 3))
        top5_stats.append(top_k_accuracy(sample, 5))
        
    def ci(data):
        return (float(np.percentile(data, 2.5)), float(np.percentile(data, 97.5)))
        
    return {
        "mrr_ci": ci(mrr_stats),
        "mar_ci": ci(mar_stats),
        "top1_ci": ci(top1_stats),
        "top3_ci": ci(top3_stats),
        "top5_ci": ci(top5_stats)
    }

# ---------------------------------------------------------
# Baseline Implementations
# ---------------------------------------------------------
def evaluate_baseline_latency(features_df: pd.DataFrame) -> pd.DataFrame:
    t0 = time.perf_counter()
    df = features_df.copy()
    if "service_mean_latency_ms" in df.columns:
        df["score"] = df["service_mean_latency_ms"].fillna(0)
    else:
        df["score"] = 0.0
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    res = df[["service", "rank", "score"]]
    res.attrs["latency_ms"] = (time.perf_counter() - t0) * 1000.0
    return res

def evaluate_baseline_error(features_df: pd.DataFrame) -> pd.DataFrame:
    t0 = time.perf_counter()
    df = features_df.copy()
    if "error_rate" in df.columns:
        df["score"] = df["error_rate"].fillna(0)
    else:
        df["score"] = 0.0
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    res = df[["service", "rank", "score"]]
    res.attrs["latency_ms"] = (time.perf_counter() - t0) * 1000.0
    return res

def evaluate_baseline_pagerank(features_df: pd.DataFrame, graph_path: str) -> pd.DataFrame:
    t0 = time.perf_counter()
    df = features_df.copy()
    with open(graph_path, "r") as f:
        adj = json.load(f)
    G = nx.DiGraph()
    for node in adj.get("nodes", []): G.add_node(node["id"])
    for edge in adj.get("edges", []): G.add_edge(edge["source"], edge["target"])
    H = G.reverse()
    try:
        pr = nx.pagerank(H, alpha=0.85)
    except Exception:
        pr = {n: 1.0 / len(H) for n in H.nodes()}
    df["score"] = df["service"].map(pr).fillna(0)
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    res = df[["service", "rank", "score"]]
    res.attrs["latency_ms"] = (time.perf_counter() - t0) * 1000.0
    return res

# ---------------------------------------------------------
# Controlled Telemetry Degradation Protocols
# ---------------------------------------------------------
def simulate_metric_degradation(df: pd.DataFrame, drop_prob: float, seed: int = 42):
    """Protocol A: Progressively zero out Prometheus metrics while retaining traces."""
    noisy = df.copy()
    np.random.seed(seed)
    services = noisy["service"].unique()
    num_drop = int(len(services) * drop_prob)
    drop_nodes = np.random.choice(services, size=num_drop, replace=False).tolist()
    
    metric_cols = [
        "metric_samples_available", "service_cpu_mean_rate", "service_cpu_cv",
        "service_mem_mean_bytes", "service_mem_stability",
        "service_net_rx_mean_rate", "service_net_tx_mean_rate"
    ]
    for col in metric_cols:
        if col in noisy.columns:
            noisy.loc[noisy["service"].isin(drop_nodes), col] = 0.0
    return noisy, drop_nodes

def simulate_trace_degradation(df: pd.DataFrame, drop_prob: float, seed: int = 42):
    """Protocol B: Progressively zero out Jaeger trace features simulating trace sampling loss."""
    noisy = df.copy()
    np.random.seed(seed)
    services = noisy["service"].unique()
    num_drop = int(len(services) * drop_prob)
    drop_nodes = np.random.choice(services, size=num_drop, replace=False).tolist()
    
    trace_cols = [
        "total_traces", "service_span_count", "service_trace_participation_rate",
        "service_fan_out_mean", "service_orphan_span_rate", "service_root_span_rate",
        "service_mean_latency_ms", "service_std_latency_ms", "service_p50_latency_ms",
        "service_p95_latency_ms", "service_p99_latency_ms", "service_latency_cv"
    ]
    for col in trace_cols:
        if col in noisy.columns:
            noisy.loc[noisy["service"].isin(drop_nodes), col] = 0.0
    return noisy, drop_nodes

def simulate_node_blackout(df: pd.DataFrame, drop_prob: float, seed: int = 42):
    """Protocol C: Completely drop all direct telemetry for drop_prob % of services (Silent Node)."""
    noisy = df.copy()
    np.random.seed(seed)
    services = noisy["service"].unique()
    num_drop = int(len(services) * drop_prob)
    drop_nodes = np.random.choice(services, size=num_drop, replace=False).tolist()
    
    noisy.loc[noisy["service"].isin(drop_nodes), "metric_samples_available"] = 0
    noisy.loc[noisy["service"].isin(drop_nodes), "total_traces"] = 0
    noisy.loc[noisy["service"].isin(drop_nodes), "service_trace_participation_rate"] = 0.0
    for col in noisy.columns:
        if col.startswith("service_") and col not in ["service"]:
            noisy.loc[noisy["service"].isin(drop_nodes), col] = 0.0
    return noisy, drop_nodes

def simulate_multimodal_degradation(df: pd.DataFrame, drop_prob: float, seed: int = 42):
    """Protocol D: Simultaneous degradation across metrics and traces."""
    noisy, _ = simulate_metric_degradation(df, drop_prob, seed=seed)
    noisy, drop_nodes = simulate_trace_degradation(noisy, drop_prob, seed=seed)
    return noisy, drop_nodes

# ---------------------------------------------------------
# Main Experimental Suite
# ---------------------------------------------------------
def run_evaluation():
    log.info("═════════════════════════════════════════════════════════════════════════")
    log.info(" CERCA Review 02 Comprehensive Evaluation & Controlled Degradation Suite ")
    log.info("═════════════════════════════════════════════════════════════════════════")
    
    dataset_path = "E:/CERCA/data/rca_dataset.csv"
    if not os.path.exists(dataset_path):
        log.error(f"Error: {dataset_path} not found.")
        return
        
    df = pd.read_csv(dataset_path)
    fault_exps = df[df["true_root_cause"] != "none"]["experiment_id"].unique()
    log.info(f"Loaded {len(fault_exps)} identical fault injection experiments.")
    
    cerca_algo = CERCA()
    
    # -------------------------------------------------------------------------
    # Experiment 1: Standard Evaluation on Pristine Telemetry (All Baselines)
    # -------------------------------------------------------------------------
    methods = [
        "CERCA (Full)",
        "CERCA (No Graph)",
        "CERCA (No Reliability)",
        "CERCA (Equal Weights)",
        "Baseline (True MicroRCA)",
        "Baseline (PageRank)",
        "Baseline (Latency)",
        "Baseline (Error Rate)"
    ]
    
    results = {m: [] for m in methods}
    latencies = {m: [] for m in methods}
    # Per-incident reciprocal ranks (for incident-level paired tests later)
    incident_rr = {m: [] for m in methods}
    incident_top1 = {m: [] for m in methods}
    
    for exp_id in fault_exps:
        exp_df = df[df["experiment_id"] == exp_id].copy()
        true_rc = exp_df["true_root_cause"].iloc[0]
        graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
        
        # CERCA Variants
        r_full = cerca_algo.rank_services(exp_df, graph_path, variant="full")
        results["CERCA (Full)"].append(r_full[r_full["service"] == true_rc]["rank"].iloc[0])
        latencies["CERCA (Full)"].append(r_full.attrs.get("latency_ms", 0.5))
        
        r_ng = cerca_algo.rank_services(exp_df, graph_path, variant="no_graph")
        results["CERCA (No Graph)"].append(r_ng[r_ng["service"] == true_rc]["rank"].iloc[0])
        latencies["CERCA (No Graph)"].append(r_ng.attrs.get("latency_ms", 0.4))
        
        r_nr = cerca_algo.rank_services(exp_df, graph_path, variant="no_reliability")
        results["CERCA (No Reliability)"].append(r_nr[r_nr["service"] == true_rc]["rank"].iloc[0])
        latencies["CERCA (No Reliability)"].append(r_nr.attrs.get("latency_ms", 0.5))
        
        r_eq = cerca_algo.rank_services(exp_df, graph_path, variant="equal_weights")
        results["CERCA (Equal Weights)"].append(r_eq[r_eq["service"] == true_rc]["rank"].iloc[0])
        latencies["CERCA (Equal Weights)"].append(r_eq.attrs.get("latency_ms", 0.5))
        
        # Baselines
        r_mrca = evaluate_true_microrca(exp_df, graph_path, exp_id)
        results["Baseline (True MicroRCA)"].append(r_mrca[r_mrca["service"] == true_rc]["rank"].iloc[0])
        latencies["Baseline (True MicroRCA)"].append(r_mrca.attrs.get("latency_ms", 50.0))
        
        r_pr = evaluate_baseline_pagerank(exp_df, graph_path)
        results["Baseline (PageRank)"].append(r_pr[r_pr["service"] == true_rc]["rank"].iloc[0])
        latencies["Baseline (PageRank)"].append(r_pr.attrs.get("latency_ms", 1.5))
        
        r_lat = evaluate_baseline_latency(exp_df)
        results["Baseline (Latency)"].append(r_lat[r_lat["service"] == true_rc]["rank"].iloc[0])
        latencies["Baseline (Latency)"].append(r_lat.attrs.get("latency_ms", 0.2))
        
        r_err = evaluate_baseline_error(exp_df)
        results["Baseline (Error Rate)"].append(r_err[r_err["service"] == true_rc]["rank"].iloc[0])
        latencies["Baseline (Error Rate)"].append(r_err.attrs.get("latency_ms", 0.2))

        # Track per-incident reciprocal ranks for paired significance tests
        for m in methods:
            r = results[m][-1]
            incident_rr[m].append(1.0 / r)
            incident_top1[m].append(1 if r == 1 else 0)
        
    metrics_summary = []
    log.info("\nTable 1: Overall Performance on Pristine Telemetry (Mean over 7 Faults with 95% CI):")
    log.info(f"{'Method':<25} | {'MRR':<18} | {'MAR':<10} | {'Top-1':<18} | {'Top-3':<18} | {'Top-5':<18} | {'Latency (ms)':<12}")
    log.info("-" * 125)
    
    for m in methods:
        ranks = results[m]
        mrr = mrr_score(ranks)
        mar = mar_score(ranks)
        t1 = top_k_accuracy(ranks, 1)
        t3 = top_k_accuracy(ranks, 3)
        t5 = top_k_accuracy(ranks, 5)
        avg_lat = float(np.mean(latencies[m]))
        
        ci = bootstrap_ci(ranks)
        mrr_str = f"{mrr:.4f} ({ci['mrr_ci'][0]:.2f}-{ci['mrr_ci'][1]:.2f})"
        t1_str = f"{t1:.4f} ({ci['top1_ci'][0]:.2f}-{ci['top1_ci'][1]:.2f})"
        t3_str = f"{t3:.4f} ({ci['top3_ci'][0]:.2f}-{ci['top3_ci'][1]:.2f})"
        t5_str = f"{t5:.4f} ({ci['top5_ci'][0]:.2f}-{ci['top5_ci'][1]:.2f})"
        
        log.info(f"{m:<25} | {mrr_str:<18} | {mar:<10.2f} | {t1_str:<18} | {t3_str:<18} | {t5_str:<18} | {avg_lat:<12.2f}")
        metrics_summary.append({
            "Method": m, "MRR": mrr, "MAR": mar,
            "Top-1": t1, "Top-3": t3, "Top-5": t5,
            "MRR_CI_Low": ci["mrr_ci"][0], "MRR_CI_High": ci["mrr_ci"][1],
            "Top1_CI_Low": ci["top1_ci"][0], "Top1_CI_High": ci["top1_ci"][1],
            "Top3_CI_Low": ci["top3_ci"][0], "Top3_CI_High": ci["top3_ci"][1],
            "Top5_CI_Low": ci["top5_ci"][0], "Top5_CI_High": ci["top5_ci"][1],
            "Latency_ms": avg_lat
        })
        
    pd.DataFrame(metrics_summary).to_csv("E:/CERCA/cerca/evaluation_metrics_extended.csv", index=False)
    log.info("Saved extended metrics to: E:/CERCA/cerca/evaluation_metrics_extended.csv")

    # -------------------------------------------------------------------------
    # Experiment 2: Controlled Degradation Ladder (0% to 90% Drop) - CERCA vs Baselines
    # -------------------------------------------------------------------------
    log.info("\n═════════════════════════════════════════════════════════════════════════")
    log.info(" Running Controlled Multimodal Degradation Ladder (0% to 90% Telemetry Loss)")
    log.info("═════════════════════════════════════════════════════════════════════════")
    
    drop_levels = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    degradation_rows = []
    
    for p in drop_levels:
        cerca_ranks = []
        mrca_ranks = []
        pr_ranks = []
        lat_ranks = []
        err_ranks = []
        
        for exp_id in fault_exps:
            exp_df = df[df["experiment_id"] == exp_id].copy()
            true_rc = exp_df["true_root_cause"].iloc[0]
            graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
            
            noisy_df, drop_nodes = simulate_node_blackout(exp_df, p, seed=42)
            
            # CERCA
            r_c = cerca_algo.rank_services(noisy_df, graph_path, variant="full")
            cerca_ranks.append(r_c[r_c["service"] == true_rc]["rank"].iloc[0])
            
            # MicroRCA
            r_m = evaluate_true_microrca(noisy_df, graph_path, exp_id, drop_nodes=drop_nodes)
            mrca_ranks.append(r_m[r_m["service"] == true_rc]["rank"].iloc[0])
            
            # PageRank
            r_p = evaluate_baseline_pagerank(noisy_df, graph_path)
            pr_ranks.append(r_p[r_p["service"] == true_rc]["rank"].iloc[0])
            
            # Latency Heuristic
            r_l = evaluate_baseline_latency(noisy_df)
            lat_ranks.append(r_l[r_l["service"] == true_rc]["rank"].iloc[0])
            
            # Error Rate Heuristic
            r_e = evaluate_baseline_error(noisy_df)
            err_ranks.append(r_e[r_e["service"] == true_rc]["rank"].iloc[0])
            
        degradation_rows.append({
            "Drop_Percentage": int(p * 100),
            "CERCA_MRR": mrr_score(cerca_ranks),
            "MicroRCA_MRR": mrr_score(mrca_ranks),
            "PageRank_MRR": mrr_score(pr_ranks),
            "Latency_MRR": mrr_score(lat_ranks),
            "Error_MRR": mrr_score(err_ranks),
            "CERCA_Top1": top_k_accuracy(cerca_ranks, 1),
            "MicroRCA_Top1": top_k_accuracy(mrca_ranks, 1),
            "CERCA_Top3": top_k_accuracy(cerca_ranks, 3),
            "MicroRCA_Top3": top_k_accuracy(mrca_ranks, 3)
        })
        log.info(f"Drop {int(p*100):>2}% | CERCA MRR: {mrr_score(cerca_ranks):.4f} (Top-1: {top_k_accuracy(cerca_ranks, 1):.2f}) | MicroRCA MRR: {mrr_score(mrca_ranks):.4f} (Top-1: {top_k_accuracy(mrca_ranks, 1):.2f})")
        
    degrad_df = pd.DataFrame(degradation_rows)
    degrad_df.to_csv("E:/CERCA/cerca/degradation_robustness.csv", index=False)
    log.info("Saved degradation robustness data to: E:/CERCA/cerca/degradation_robustness.csv")

    # -------------------------------------------------------------------------
    # Experiment 3: Controlled Ablation Ladder under Telemetry Degradation
    # -------------------------------------------------------------------------
    log.info("\n═════════════════════════════════════════════════════════════════════════")
    log.info(" Running Ablation Study Across Telemetry Degradation Ladder ")
    log.info("═════════════════════════════════════════════════════════════════════════")
    
    ablation_levels = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9]
    ablation_rows = []
    
    for p in ablation_levels:
        full_ranks = []
        nograph_ranks = []
        norel_ranks = []
        equal_ranks = []
        
        for exp_id in fault_exps:
            exp_df = df[df["experiment_id"] == exp_id].copy()
            true_rc = exp_df["true_root_cause"].iloc[0]
            graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
            
            noisy_df, _ = simulate_node_blackout(exp_df, p, seed=42)
            
            # 1. Full CERCA
            r_full = cerca_algo.rank_services(noisy_df, graph_path, variant="full")
            full_ranks.append(r_full[r_full["service"] == true_rc]["rank"].iloc[0])
            
            # 2. No Graph (Contextual Blame Ablation)
            r_ng = cerca_algo.rank_services(noisy_df, graph_path, variant="no_graph")
            nograph_ranks.append(r_ng[r_ng["service"] == true_rc]["rank"].iloc[0])
            
            # 3. No Reliability (Lambda Ablation)
            r_nr = cerca_algo.rank_services(noisy_df, graph_path, variant="no_reliability")
            norel_ranks.append(r_nr[r_nr["service"] == true_rc]["rank"].iloc[0])
            
            # 4. Equal Weights (Learned Weight Ablation)
            r_eq = cerca_algo.rank_services(noisy_df, graph_path, variant="equal_weights")
            equal_ranks.append(r_eq[r_eq["service"] == true_rc]["rank"].iloc[0])
            
        ablation_rows.append({
            "Drop_Percentage": int(p * 100),
            "Full_MRR": mrr_score(full_ranks),
            "NoGraph_MRR": mrr_score(nograph_ranks),
            "NoReliability_MRR": mrr_score(norel_ranks),
            "EqualWeights_MRR": mrr_score(equal_ranks),
            "Full_Top1": top_k_accuracy(full_ranks, 1),
            "NoGraph_Top1": top_k_accuracy(nograph_ranks, 1),
            "NoReliability_Top1": top_k_accuracy(norel_ranks, 1),
            "EqualWeights_Top1": top_k_accuracy(equal_ranks, 1)
        })
        log.info(f"Ablation Drop {int(p*100):>2}% | Full: {mrr_score(full_ranks):.4f} | NoGraph: {mrr_score(nograph_ranks):.4f} | NoRel: {mrr_score(norel_ranks):.4f} | Equal: {mrr_score(equal_ranks):.4f}")
        
    ablation_df = pd.DataFrame(ablation_rows)
    ablation_df.to_csv("E:/CERCA/cerca/ablation_degradation.csv", index=False)
    log.info("Saved ablation degradation data to: E:/CERCA/cerca/ablation_degradation.csv")

    # -------------------------------------------------------------------------
    # Experiment 4: Modality Sensitivity (Metric-Only vs Trace-Only vs Combined)
    # -------------------------------------------------------------------------
    log.info("\n═════════════════════════════════════════════════════════════════════════")
    log.info(" Running Modality Sensitivity (Metric-Only vs Trace-Only vs Combined) ")
    log.info("═════════════════════════════════════════════════════════════════════════")
    
    mod_levels = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9]
    mod_rows = []
    
    for p in mod_levels:
        metric_ranks = []
        trace_ranks = []
        combined_ranks = []
        
        for exp_id in fault_exps:
            exp_df = df[df["experiment_id"] == exp_id].copy()
            true_rc = exp_df["true_root_cause"].iloc[0]
            graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
            
            # Metric-only drop
            m_df, _ = simulate_metric_degradation(exp_df, p, seed=42)
            r_m = cerca_algo.rank_services(m_df, graph_path, variant="full")
            metric_ranks.append(r_m[r_m["service"] == true_rc]["rank"].iloc[0])
            
            # Trace-only drop
            t_df, _ = simulate_trace_degradation(exp_df, p, seed=42)
            r_t = cerca_algo.rank_services(t_df, graph_path, variant="full")
            trace_ranks.append(r_t[r_t["service"] == true_rc]["rank"].iloc[0])
            
            # Combined drop
            c_df, _ = simulate_node_blackout(exp_df, p, seed=42)
            r_c = cerca_algo.rank_services(c_df, graph_path, variant="full")
            combined_ranks.append(r_c[r_c["service"] == true_rc]["rank"].iloc[0])
            
        mod_rows.append({
            "Drop_Percentage": int(p * 100),
            "Metric_Drop_MRR": mrr_score(metric_ranks),
            "Trace_Drop_MRR": mrr_score(trace_ranks),
            "Combined_Drop_MRR": mrr_score(combined_ranks)
        })
        log.info(f"Modality Drop {int(p*100):>2}% | Metric-Only MRR: {mrr_score(metric_ranks):.4f} | Trace-Only MRR: {mrr_score(trace_ranks):.4f} | Combined MRR: {mrr_score(combined_ranks):.4f}")
        
    mod_df = pd.DataFrame(mod_rows)
    mod_df.to_csv("E:/CERCA/cerca/modality_sensitivity.csv", index=False)
    log.info("Saved modality sensitivity data to: E:/CERCA/cerca/modality_sensitivity.csv")

    # -------------------------------------------------------------------------
    # Experiment 5: Correct Incident-Level Paired Statistical Analysis
    # ─────────────────────────────────────────────────────────────────────────
    # IMPORTANT: The 10 degradation levels in Experiment 2 are *repeated conditions*
    # applied to the SAME 7 fault incidents. They do NOT constitute 10 independent
    # operational incidents. Therefore the t=5.738, p=0.00028 result previously
    # reported is INVALID — it inflates N by 10x.
    #
    # This section performs the correct analysis:
    #   Unit of analysis: the 7 independent fault incidents
    #   Test: Wilcoxon signed-rank (non-parametric, appropriate for N=7)
    #   Factor: degradation severity is treated as a repeated-measures factor
    # -------------------------------------------------------------------------
    log.info("\n═════════════════════════════════════════════════════════════════════════")
    log.info(" Experiment 5: Incident-Level Paired Statistical Analysis (CORRECTED) ")
    log.info("═════════════════════════════════════════════════════════════════════════")
    log.info("  Unit of analysis: N=7 independent fault incidents")
    log.info("  Test: Wilcoxon signed-rank (non-parametric, correct for small N)")
    log.info("  NOTE: Degradation levels are NOT independent incidents.")
    log.info("        Do NOT report p-values from pooling degradation levels as observations.")

    # --- Pristine telemetry: CERCA (Full) vs Baseline (True MicroRCA) ---
    cerca_rr  = np.array(incident_rr["CERCA (Full)"])
    mrca_rr   = np.array(incident_rr["Baseline (True MicroRCA)"])
    diff = cerca_rr - mrca_rr

    # Wilcoxon signed-rank test (requires at least 2 non-zero differences)
    nonzero_diffs = diff[diff != 0]
    if len(nonzero_diffs) >= 2:
        w_stat, w_p = stats.wilcoxon(cerca_rr, mrca_rr, alternative="greater")
    else:
        w_stat, w_p = float("nan"), float("nan")

    # Bootstrap effect size (mean RR difference with 95% CI)
    n_boot = 5000
    boot_diffs = []
    rng = np.random.default_rng(42)
    for _ in range(n_boot):
        idx = rng.integers(0, len(cerca_rr), size=len(cerca_rr))
        boot_diffs.append(np.mean(cerca_rr[idx]) - np.mean(mrca_rr[idx]))
    boot_diffs = np.array(boot_diffs)
    effect_mean = float(np.mean(boot_diffs))
    effect_ci   = (float(np.percentile(boot_diffs, 2.5)),
                   float(np.percentile(boot_diffs, 97.5)))

    log.info("\n  [Pristine Telemetry] CERCA (Full) vs True MicroRCA")
    log.info(f"  N (incidents) = {len(cerca_rr)}")
    log.info(f"  CERCA mean RR = {np.mean(cerca_rr):.4f}")
    log.info(f"  MicroRCA mean RR = {np.mean(mrca_rr):.4f}")
    log.info(f"  Mean RR difference = {effect_mean:.4f}  95% CI: ({effect_ci[0]:.4f}, {effect_ci[1]:.4f})")
    log.info(f"  Wilcoxon W = {w_stat}, p = {w_p:.4f}")
    if np.isnan(w_p) or w_p > 0.05:
        log.info("  CONCLUSION: Difference is NOT statistically significant at alpha=0.05 (N=7).")
        log.info("  Report this as DIRECTIONAL with bootstrap CI, not as a significant finding.")
    else:
        log.info("  CONCLUSION: Significant at alpha=0.05 (one-tailed). Interpret cautiously at N=7.")

    # --- 20% degradation: CERCA vs MicroRCA (incident-level) ---
    log.info("\n  [20% Telemetry Degradation] CERCA (Full) vs True MicroRCA")
    cerca_20_rr, mrca_20_rr = [], []
    for exp_id in fault_exps:
        exp_df = df[df["experiment_id"] == exp_id].copy()
        true_rc = exp_df["true_root_cause"].iloc[0]
        graph_path = f"E:/CERCA/experiments/{exp_id}/graphs/dependency_graph.json"
        noisy_df, drop_nodes = simulate_node_blackout(exp_df, 0.20, seed=42)
        r_c = cerca_algo.rank_services(noisy_df, graph_path, variant="full")
        r_m = evaluate_true_microrca(noisy_df, graph_path, exp_id, drop_nodes=drop_nodes)
        cerca_20_rr.append(1.0 / r_c[r_c["service"] == true_rc]["rank"].iloc[0])
        mrca_20_rr.append(1.0 / r_m[r_m["service"] == true_rc]["rank"].iloc[0])

    cerca_20_rr = np.array(cerca_20_rr)
    mrca_20_rr  = np.array(mrca_20_rr)
    diff_20 = cerca_20_rr - mrca_20_rr
    nonzero_20 = diff_20[diff_20 != 0]
    if len(nonzero_20) >= 2:
        w_stat_20, w_p_20 = stats.wilcoxon(cerca_20_rr, mrca_20_rr, alternative="greater")
    else:
        w_stat_20, w_p_20 = float("nan"), float("nan")

    boot_diffs_20 = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(cerca_20_rr), size=len(cerca_20_rr))
        boot_diffs_20.append(np.mean(cerca_20_rr[idx]) - np.mean(mrca_20_rr[idx]))
    boot_diffs_20 = np.array(boot_diffs_20)
    effect_20     = float(np.mean(boot_diffs_20))
    effect_ci_20  = (float(np.percentile(boot_diffs_20, 2.5)),
                     float(np.percentile(boot_diffs_20, 97.5)))

    log.info(f"  N (incidents) = {len(cerca_20_rr)}")
    log.info(f"  CERCA@20% mean RR = {np.mean(cerca_20_rr):.4f}")
    log.info(f"  MicroRCA@20% mean RR = {np.mean(mrca_20_rr):.4f}")
    log.info(f"  Mean RR difference = {effect_20:.4f}  95% CI: ({effect_ci_20[0]:.4f}, {effect_ci_20[1]:.4f})")
    log.info(f"  Wilcoxon W = {w_stat_20}, p = {w_p_20:.4f}")
    if np.isnan(w_p_20) or w_p_20 > 0.05:
        log.info("  CONCLUSION: Directional advantage only (not significant at alpha=0.05, N=7).")
    else:
        log.info("  CONCLUSION: Significant at alpha=0.05 (one-tailed).")

    # Save corrected stats summary
    stats_summary = {
        "analysis_note": (
            "Incident-level paired analysis. N=7 independent fault incidents. "
            "Degradation levels are repeated-measures factors, NOT independent observations. "
            "The previously reported t=5.738 (p=0.00028) was invalid (pooled 10 degradation "
            "levels x 7 incidents as 70 independent observations)."
        ),
        "pristine_cerca_mean_rr": float(np.mean(cerca_rr)),
        "pristine_mrca_mean_rr": float(np.mean(mrca_rr)),
        "pristine_mean_rr_diff": effect_mean,
        "pristine_rr_diff_ci_low": effect_ci[0],
        "pristine_rr_diff_ci_high": effect_ci[1],
        "pristine_wilcoxon_W": float(w_stat) if not np.isnan(w_stat) else None,
        "pristine_wilcoxon_p": float(w_p) if not np.isnan(w_p) else None,
        "pristine_significant": bool(not np.isnan(w_p) and w_p < 0.05),
        "deg20_cerca_mean_rr": float(np.mean(cerca_20_rr)),
        "deg20_mrca_mean_rr": float(np.mean(mrca_20_rr)),
        "deg20_mean_rr_diff": effect_20,
        "deg20_rr_diff_ci_low": effect_ci_20[0],
        "deg20_rr_diff_ci_high": effect_ci_20[1],
        "deg20_wilcoxon_W": float(w_stat_20) if not np.isnan(w_stat_20) else None,
        "deg20_wilcoxon_p": float(w_p_20) if not np.isnan(w_p_20) else None,
        "deg20_significant": bool(not np.isnan(w_p_20) and w_p_20 < 0.05),
    }
    import json as _json
    with open("E:/CERCA/cerca/statistical_analysis_corrected.json", "w") as sf:
        _json.dump(stats_summary, sf, indent=2)
    log.info("\nSaved corrected statistical analysis: E:/CERCA/cerca/statistical_analysis_corrected.json")

    log.info("\n═════════════════════════════════════════════════════════════════════════")
    log.info(" ALL EXPERIMENTS COMPLETE SUCCESSFULLY!")
    log.info("═════════════════════════════════════════════════════════════════════════")

if __name__ == "__main__":
    run_evaluation()
