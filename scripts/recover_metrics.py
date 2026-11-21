"""
recover_metrics.py
──────────────────
Recovers CPU, memory, and network container metrics from the raw Prometheus
JSON files and patches rca_dataset.csv with real values.

The raw JSON files have empty data[] lists because cAdvisor used the label
  container_label_com_docker_compose_service
but the metric series were recorded under the container's own name label
  name={container_name}

This script:
1. Reads each raw metrics_*.json
2. Tries multiple label keys to resolve service name
3. Computes per-service aggregates (mean CPU rate, mean mem, CV, etc.)
4. Matches experiments by timestamps
5. Patches rca_dataset.csv with real values
6. Saves patched dataset as rca_dataset_with_metrics.csv
"""

import json
import glob
import os
import re
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path("e:/CERCA")
RAW_DIR = BASE / "data" / "raw"
DATA_DIR = BASE / "data"

# Mapping from docker-compose service names to common container name patterns
# (cAdvisor uses the docker container name as the 'name' label)
COMPOSE_TO_CONTAINER = {
    "nginx-web-server": ["nginx-web-server", "socialnetwork-nginx-web-server-1", "nginx"],
    "user-service": ["user-service", "socialnetwork-user-service-1"],
    "compose-post-service": ["compose-post-service", "socialnetwork-compose-post-service-1"],
    "home-timeline-service": ["home-timeline-service", "socialnetwork-home-timeline-service-1"],
    "user-timeline-service": ["user-timeline-service", "socialnetwork-user-timeline-service-1"],
    "social-graph-service": ["social-graph-service", "socialnetwork-social-graph-service-1"],
    "post-storage-service": ["post-storage-service", "socialnetwork-post-storage-service-1"],
    "media-service": ["media-service", "socialnetwork-media-service-1"],
    "text-service": ["text-service", "socialnetwork-text-service-1"],
    "url-shorten-service": ["url-shorten-service", "socialnetwork-url-shorten-service-1"],
    "user-mention-service": ["user-mention-service", "socialnetwork-user-mention-service-1"],
    "unique-id-service": ["unique-id-service", "socialnetwork-unique-id-service-1"],
    "jaeger": ["jaeger", "socialnetwork-jaeger-1"],
}

# Reverse lookup: container name fragment -> compose service
CONTAINER_TO_COMPOSE = {}
for svc, containers in COMPOSE_TO_CONTAINER.items():
    for c in containers:
        CONTAINER_TO_COMPOSE[c.lower()] = svc


def resolve_service_name(labels: dict) -> str:
    """Try to resolve compose service name from Prometheus labels."""
    # 1. Direct compose label
    svc = labels.get("container_label_com_docker_compose_service", "")
    if svc:
        return svc
    # 2. 'name' label (docker container name)
    name = labels.get("name", "").lower()
    if name:
        for pattern, compose_svc in CONTAINER_TO_COMPOSE.items():
            if pattern in name:
                return compose_svc
    # 3. 'container' label
    container = labels.get("container", "").lower()
    if container:
        for pattern, compose_svc in CONTAINER_TO_COMPOSE.items():
            if pattern in container:
                return compose_svc
    # 4. job label
    job = labels.get("job", "")
    if job and job in COMPOSE_TO_CONTAINER:
        return job
    return ""


def extract_metrics_from_raw(json_path: str) -> pd.DataFrame:
    """Extract per-service metric aggregates from one raw metrics JSON file."""
    with open(json_path, "r") as f:
        data = json.load(f)

    records = []

    # CPU rate
    cpu_entry = data.get("container_cpu_usage_seconds_total", {})
    cpu_series = (cpu_entry.get("data", [])
                  if isinstance(cpu_entry.get("data"), list)
                  else [])
    for series in cpu_series:
        labels = series.get("metric", {})
        svc = resolve_service_name(labels)
        if not svc:
            continue
        values = [float(v[1]) for v in series.get("values", [])
                  if v[1] not in ("NaN", "Inf", "-Inf")]
        if not values:
            continue
        records.append({
            "service": svc,
            "metric": "cpu",
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "n": len(values),
        })

    # Memory usage
    mem_entry = data.get("container_memory_usage_bytes", {})
    mem_series = (mem_entry.get("data", [])
                  if isinstance(mem_entry.get("data"), list)
                  else [])
    for series in mem_series:
        labels = series.get("metric", {})
        svc = resolve_service_name(labels)
        if not svc:
            continue
        values = [float(v[1]) for v in series.get("values", [])
                  if v[1] not in ("NaN", "Inf", "-Inf")]
        if not values:
            continue
        arr = np.array(values)
        stability = float(1.0 - (arr.max() - arr.min()) / arr.mean()) if arr.mean() > 0 else 0.0
        records.append({
            "service": svc,
            "metric": "mem",
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "stability": stability,
            "n": len(values),
        })

    # Network RX
    rx_entry = data.get("container_network_receive_bytes_total", {})
    rx_series = (rx_entry.get("data", [])
                 if isinstance(rx_entry.get("data"), list)
                 else [])
    for series in rx_series:
        labels = series.get("metric", {})
        svc = resolve_service_name(labels)
        if not svc:
            continue
        values = [float(v[1]) for v in series.get("values", [])
                  if v[1] not in ("NaN", "Inf", "-Inf")]
        if not values:
            continue
        records.append({
            "service": svc,
            "metric": "net_rx",
            "mean": float(np.mean(values)),
            "std": 0.0,
            "n": len(values),
        })

    # Network TX
    tx_entry = data.get("container_network_transmit_bytes_total", {})
    tx_series = (tx_entry.get("data", [])
                 if isinstance(tx_entry.get("data"), list)
                 else [])
    for series in tx_series:
        labels = series.get("metric", {})
        svc = resolve_service_name(labels)
        if not svc:
            continue
        values = [float(v[1]) for v in series.get("values", [])
                  if v[1] not in ("NaN", "Inf", "-Inf")]
        if not values:
            continue
        records.append({
            "service": svc,
            "metric": "net_tx",
            "mean": float(np.mean(values)),
            "std": 0.0,
            "n": len(values),
        })

    return pd.DataFrame(records)


def metrics_df_to_service_dict(metrics_df: pd.DataFrame) -> dict:
    """Convert extracted metric records to per-service feature dict."""
    result = {}
    if metrics_df.empty:
        return result

    for svc in metrics_df["service"].unique():
        sdf = metrics_df[metrics_df["service"] == svc]
        entry = {}

        cpu = sdf[sdf["metric"] == "cpu"]
        if not cpu.empty:
            c = cpu.iloc[0]
            entry["service_cpu_mean_rate"] = c["mean"]
            entry["service_cpu_cv"] = (c["std"] / c["mean"]) if c["mean"] > 0 else 0.0

        mem = sdf[sdf["metric"] == "mem"]
        if not mem.empty:
            m = mem.iloc[0]
            entry["service_mem_mean_bytes"] = m["mean"]
            entry["service_mem_stability"] = m.get("stability", 0.0)

        rx = sdf[sdf["metric"] == "net_rx"]
        if not rx.empty:
            entry["service_net_rx_mean_rate"] = rx.iloc[0]["mean"]

        tx = sdf[sdf["metric"] == "net_tx"]
        if not tx.empty:
            entry["service_net_tx_mean_rate"] = tx.iloc[0]["mean"]

        result[svc] = entry

    return result


def match_experiment_to_metrics(exp_id: str) -> str:
    """
    Match experiment_id (20260728_225443) to the closest metrics_*.json file.
    Experiment IDs are timestamps; metrics files are collected ~after the fault window.
    """
    # Extract time from experiment ID
    exp_ts_str = exp_id  # format: YYYYMMDD_HHMMSS
    metrics_files = sorted(glob.glob(str(RAW_DIR / "metrics_*.json")))

    # Find closest metrics file (by timestamp string proximity)
    exp_clean = exp_ts_str.replace("_", "")
    best_file = None
    best_diff = float("inf")

    for mf in metrics_files:
        base = os.path.basename(mf)
        m = re.search(r"metrics_(\d{8}_\d{6})\.json", base)
        if m:
            m_ts = m.group(1).replace("_", "")
            diff = abs(int(m_ts) - int(exp_clean))
            if diff < best_diff:
                best_diff = diff
                best_file = mf

    return best_file


def main():
    print("=" * 60)
    print("CERCA Metric Recovery Script")
    print("=" * 60)

    # Load dataset
    dataset_path = DATA_DIR / "rca_dataset.csv"
    df = pd.read_csv(dataset_path)
    print(f"Loaded dataset: {df.shape[0]} rows, {df.shape[1]} columns")
    print(f"CPU nulls before: {df['service_cpu_mean_rate'].isna().sum()}/{len(df)}")

    # Process each experiment
    experiments = df["experiment_id"].unique()
    print(f"\nExperiments to process: {len(experiments)}")

    total_recovered = 0

    for exp_id in sorted(experiments):
        print(f"\n  Experiment: {exp_id}")
        mf = match_experiment_to_metrics(exp_id)
        print(f"    Matched metrics file: {os.path.basename(mf) if mf else 'NONE'}")

        if not mf:
            print("    WARNING: No metrics file found, skipping")
            continue

        metrics_df = extract_metrics_from_raw(mf)
        print(f"    Extracted {len(metrics_df)} metric records from "
              f"{metrics_df['service'].nunique() if not metrics_df.empty else 0} services")

        if metrics_df.empty:
            print("    WARNING: No usable metric data (data[] lists are all empty)")
            print("    This indicates cAdvisor did not scrape container metrics.")
            print("    The label filter 'container_label_com_docker_compose_service!=\"\"'")
            print("    may not match your Docker setup.")
            continue

        svc_dict = metrics_df_to_service_dict(metrics_df)
        print(f"    Services with recoverable metrics: {list(svc_dict.keys())}")

        # Patch rows for this experiment
        exp_mask = df["experiment_id"] == exp_id
        for svc, feat in svc_dict.items():
            svc_mask = exp_mask & (df["service"] == svc)
            for col, val in feat.items():
                if col in df.columns:
                    df.loc[svc_mask, col] = val
                    total_recovered += 1

    print(f"\nTotal metric values recovered: {total_recovered}")
    print(f"CPU nulls after: {df['service_cpu_mean_rate'].isna().sum()}/{len(df)}")

    # Save patched dataset
    out_path = DATA_DIR / "rca_dataset_with_metrics.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved patched dataset: {out_path}")

    # Summary of what's still missing
    null_counts = df[["service_cpu_mean_rate", "service_mem_mean_bytes",
                       "service_net_rx_mean_rate"]].isna().sum()
    print("\nRemaining nulls after recovery attempt:")
    print(null_counts.to_string())

    if total_recovered == 0:
        print("\n" + "=" * 60)
        print("DIAGNOSIS: All data[] arrays in raw JSON are empty.")
        print("Root cause: The Prometheus cAdvisor scrape target was not")
        print("configured to return container metrics with the label")
        print("  container_label_com_docker_compose_service")
        print("")
        print("To recover metrics, you must re-run the experiments with")
        print("cAdvisor properly configured. See prometheus.yml:")
        print("  - The cAdvisor job must target port 8080/cadvisor or 9338")
        print("  - Container labels must be preserved in the metrics")
        print("")
        print("INTERIM FIX: Use metric_samples_available (Jaeger-derived)")
        print("as a proxy for telemetry coverage in the paper, and explicitly")
        print("state CPU/memory metrics could not be recovered from the")
        print("existing Prometheus scrape configuration.")
        print("=" * 60)


if __name__ == "__main__":
    main()
