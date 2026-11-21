import os
import glob
import json
import time
import networkx as nx
import pandas as pd
import numpy as np

def load_timeseries(exp_id: str):
    exp_dir = f"E:/CERCA/experiments/{exp_id}/data/processed"
    
    # Load spans
    span_files = glob.glob(f"{exp_dir}/spans_*.csv")
    spans = []
    for f in span_files:
        df = pd.read_csv(f)
        spans.append(df)
    spans_df = pd.concat(spans, ignore_index=True) if spans else pd.DataFrame()
    
    # Load metrics
    metric_files = glob.glob(f"{exp_dir}/metrics_*.csv")
    metrics = []
    for f in metric_files:
        df = pd.read_csv(f)
        metrics.append(df)
    metrics_df = pd.concat(metrics, ignore_index=True) if metrics else pd.DataFrame()
    
    return spans_df, metrics_df

def compute_timeseries(spans_df: pd.DataFrame, metrics_df: pd.DataFrame, window_s=15):
    """Align spans and metrics into 15s buckets for Pearson correlation."""
    # Group spans by service and 15s window to get mean duration
    if spans_df.empty:
        return pd.DataFrame()
        
    spans_df["bucket"] = (spans_df["startTime_us"] / 1e6 // window_s).astype(int)
    rt_ts = spans_df.groupby(["service", "bucket"])["duration_us"].mean().reset_index()
    rt_ts = rt_ts.rename(columns={"duration_us": "rt"})
    
    if metrics_df.empty:
        return rt_ts

    # Group metrics by service, metric_name, and 15s window
    metrics_df["bucket"] = (metrics_df["timestamp_unix"] // window_s).astype(int)
    met_ts = metrics_df.groupby(["service", "metric_name", "bucket"])["value"].mean().reset_index()
    
    # Pivot metrics so each metric is a column
    met_pivot = met_ts.pivot_table(index=["service", "bucket"], columns="metric_name", values="value").reset_index()
    
    # Merge RT and Metrics
    ts = pd.merge(rt_ts, met_pivot, on=["service", "bucket"], how="left").fillna(0)
    return ts

def evaluate_true_microrca(features_df: pd.DataFrame, graph_path: str, exp_id: str, drop_nodes: list = None) -> pd.DataFrame:
    t0 = time.perf_counter()
    df = features_df.copy()
    df.set_index("service", inplace=True)
    
    # 1. Anomaly Detection (Find the most anomalous service based on Latency CV)
    if "service_latency_cv" not in df.columns:
        df["score"] = 0.0
        df["rank"] = range(1, len(df) + 1)
        res = df.reset_index()[["service", "rank", "score"]]
        res.attrs["latency_ms"] = (time.perf_counter() - t0) * 1000.0
        return res
        
    anomalous_service = df["service_latency_cv"].idxmax()
    
    # Load graph
    with open(graph_path, "r") as f:
        adj = json.load(f)
    
    G = nx.DiGraph()
    for node in adj.get("nodes", []): G.add_node(node["id"])
    for edge in adj.get("edges", []): G.add_edge(edge["source"], edge["target"])
    
    # 3. Subgraph Extraction
    # Include anomalous_service and its 1-hop neighbors (incoming and outgoing)
    if anomalous_service in G:
        neighbors = set(G.predecessors(anomalous_service)) | set(G.successors(anomalous_service))
        subgraph_nodes = neighbors | {anomalous_service}
        sub_G = G.subgraph(subgraph_nodes).copy()
    else:
        sub_G = G.copy()
        
    # Process timeseries for correlations
    spans, metrics = load_timeseries(exp_id)
    
    if drop_nodes:
        # Simulate telemetry loss by dropping spans and metrics for these nodes
        spans = spans[~spans["service"].isin(drop_nodes)]
        metrics = metrics[~metrics["service"].isin(drop_nodes)]
        
    ts = compute_timeseries(spans, metrics)
    
    host_metrics = [
        "container_cpu_usage_seconds_total", 
        "container_memory_usage_bytes", 
        "container_network_receive_bytes_total", 
        "container_network_transmit_bytes_total"
    ]
    
    # 4 & 5. Edge Weights and Service Anomaly Score
    anomaly_scores = {}
    
    # Get the RT timeseries of the anomalous service
    rt_anomaly = ts[ts["service"] == anomalous_service].set_index("bucket")["rt"] if anomalous_service in ts["service"].values else pd.Series()
    
    for node in sub_G.nodes():
        node_ts = ts[ts["service"] == node].set_index("bucket")
        if node_ts.empty:
            anomaly_scores[node] = 0.01
            continue
            
        # Max Host Correlation
        max_host_corr = 0.01
        for m in host_metrics:
            if m in node_ts.columns:
                # CPU and Network are monotonic counters, so we should take the diff(), but simple corr on raw counters often captures spikes too.
                # To be rigorous, we apply diff to counters.
                if m != "container_memory_usage_bytes":
                    val = node_ts[m].diff().fillna(0)
                else:
                    val = node_ts[m]
                    
                if val.std() > 0 and node_ts["rt"].std() > 0:
                    corr = val.corr(node_ts["rt"])
                    if not pd.isna(corr):
                        max_host_corr = max(max_host_corr, corr)
                        
        # Incoming edge correlation (Simplified as corr(RT_node, RT_anomalous))
        incoming_corr = 0.01
        if node != anomalous_service and not rt_anomaly.empty and not node_ts.empty:
            # Align buckets
            aligned = pd.concat([rt_anomaly, node_ts["rt"]], axis=1).dropna()
            if len(aligned) > 2 and aligned.iloc[:, 0].std() > 0 and aligned.iloc[:, 1].std() > 0:
                corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                if not pd.isna(corr):
                    incoming_corr = max(0.01, corr)
        elif node == anomalous_service:
            incoming_corr = 1.0 # Self is fully correlated
            
        anomaly_scores[node] = max(0.01, incoming_corr * max_host_corr)
        
    # 6. Personalized PageRank
    H = sub_G.reverse() # Blame flows backwards
    
    total_p = sum(anomaly_scores.values())
    if total_p > 0:
        pers = {k: v/total_p for k, v in anomaly_scores.items()}
    else:
        pers = {k: 1.0/len(anomaly_scores) for k in anomaly_scores}
        
    try:
        pr = nx.pagerank(H, alpha=0.85, personalization=pers)
    except:
        pr = pers
        
    # Map back to full dataframe
    df["score"] = pd.Series(pr).fillna(0)
    df = df.sort_values(by="score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    
    res = df.reset_index()[["service", "rank", "score"]]
    res.attrs["latency_ms"] = (time.perf_counter() - t0) * 1000.0
    return res
