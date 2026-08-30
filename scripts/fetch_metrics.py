"""
fetch_metrics.py
────────────────
Fetch time-series metrics from the Prometheus HTTP API and persist them as:
  • data/raw/metrics_<timestamp>.json   – raw Prometheus query_range responses
  • data/processed/metrics_<timestamp>.csv – tidy, long-format table

Metric categories collected
────────────────────────────
  Container (via cAdvisor):
    container_cpu_usage_seconds_total
    container_memory_usage_bytes
    container_memory_working_set_bytes
    container_network_receive_bytes_total
    container_network_transmit_bytes_total
    container_fs_reads_bytes_total
    container_fs_writes_bytes_total

  Jaeger (span ingestion):
    jaeger_collector_spans_received_total
    jaeger_collector_spans_saved_by_svc_total
    jaeger_collector_traces_saved_by_svc_total

Usage
─────
    python scripts/fetch_metrics.py [OPTIONS]

Options
    --prometheus-url   Base URL  [default: http://localhost:9090]
    --lookback         Window e.g. 1h 30m  [default: 1h]
    --step             Resolution e.g. 15s 1m  [default: 15s]
    --output-dir       Root data directory  [default: data/]
    --dry-run          Print metric names and exit
    --verbose          Debug logging

Output CSV columns
──────────────────
    metric_name, service, container_name, labels_json,
    timestamp_iso, timestamp_unix, value
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
from tqdm import tqdm

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_LOOKBACK       = "1h"
DEFAULT_STEP           = "15s"
DEFAULT_OUTPUT_DIR     = Path(__file__).parent.parent / "data"

# Metrics to collect with their PromQL queries.
# {service} will be resolved from container labels at post-processing time.
METRIC_QUERIES: dict[str, str] = {
    # ── CPU ──────────────────────────────────────────────────────────────────
    "container_cpu_usage_seconds_total": (
        'rate(container_cpu_usage_seconds_total'
        '{container_label_com_docker_compose_service!=""}[1m])'
    ),
    # ── Memory ───────────────────────────────────────────────────────────────
    "container_memory_usage_bytes": (
        'container_memory_usage_bytes'
        '{container_label_com_docker_compose_service!=""}'
    ),
    "container_memory_working_set_bytes": (
        'container_memory_working_set_bytes'
        '{container_label_com_docker_compose_service!=""}'
    ),
    # ── Network ───────────────────────────────────────────────────────────────
    "container_network_receive_bytes_total": (
        'rate(container_network_receive_bytes_total'
        '{container_label_com_docker_compose_service!=""}[1m])'
    ),
    "container_network_transmit_bytes_total": (
        'rate(container_network_transmit_bytes_total'
        '{container_label_com_docker_compose_service!=""}[1m])'
    ),
    # ── Disk I/O ──────────────────────────────────────────────────────────────
    "container_fs_reads_bytes_total": (
        'rate(container_fs_reads_bytes_total'
        '{container_label_com_docker_compose_service!=""}[1m])'
    ),
    "container_fs_writes_bytes_total": (
        'rate(container_fs_writes_bytes_total'
        '{container_label_com_docker_compose_service!=""}[1m])'
    ),
    # ── Jaeger span ingestion ─────────────────────────────────────────────────
    "jaeger_spans_received_total": (
        'rate(jaeger_collector_spans_received_total[1m])'
    ),
    "jaeger_spans_saved_by_svc_total": (
        'rate(jaeger_collector_spans_saved_by_svc_total[1m])'
    ),
    "jaeger_traces_saved_by_svc_total": (
        'rate(jaeger_collector_traces_saved_by_svc_total[1m])'
    ),
}

LOOKBACK_MAP = {"s": 1, "m": 60, "h": 3600, "d": 86400}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_metrics")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def parse_duration_seconds(s: str) -> int:
    """Parse '1h', '30m', '15s' → seconds."""
    if s[-1] in LOOKBACK_MAP:
        return int(s[:-1]) * LOOKBACK_MAP[s[-1]]
    raise ValueError(f"Unknown duration unit in '{s}'")


def prom_request(base_url: str, endpoint: str, params: dict) -> dict:
    """Issue a GET request to Prometheus and return JSON payload."""
    url = f"{base_url}{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        log.error(
            "Cannot connect to Prometheus at %s — is the stack running?", base_url
        )
        sys.exit(1)
    return resp.json()


def get_all_metric_names(base_url: str) -> list[str]:
    """Fetch list of all metric names from Prometheus."""
    data = prom_request(base_url, "/api/v1/label/__name__/values", {})
    return sorted(data.get("data", []))


def query_range(
    base_url: str,
    promql: str,
    start_ts: float,
    end_ts: float,
    step: str,
) -> list[dict]:
    """Execute a range query and return the result list."""
    payload = prom_request(
        base_url,
        "/api/v1/query_range",
        {"query": promql, "start": start_ts, "end": end_ts, "step": step},
    )
    if payload.get("status") != "success":
        log.warning("Query failed: %s", payload.get("error", "unknown"))
        return []
    return payload.get("data", {}).get("result", [])


def flatten_range_result(metric_name: str, results: list[dict]) -> list[dict]:
    """
    Convert Prometheus query_range result into tidy rows.
    Each (metric_name, label_combo, timestamp) becomes one row.
    """
    rows = []
    for series in results:
        labels = series.get("metric", {})
        service = (
            labels.get("container_label_com_docker_compose_service")
            or labels.get("svc")
            or labels.get("job", "unknown")
        )
        container = labels.get("name", labels.get("container", ""))
        labels_json = json.dumps({k: v for k, v in sorted(labels.items())})

        for ts_str, value_str in series.get("values", []):
            ts = float(ts_str)
            try:
                val = float(value_str)
            except ValueError:
                val = None  # NaN strings
            ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            rows.append(
                {
                    "metric_name":    metric_name,
                    "service":        service,
                    "container_name": container,
                    "labels_json":    labels_json,
                    "timestamp_iso":  ts_iso,
                    "timestamp_unix": ts,
                    "value":          val,
                }
            )
    return rows


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fetch metrics from Prometheus HTTP API")
    parser.add_argument("--prometheus-url", default=DEFAULT_PROMETHEUS_URL,
                        help="Prometheus base URL (default: %(default)s)")
    parser.add_argument("--lookback",       default=DEFAULT_LOOKBACK,
                        help="Lookback window e.g. 1h 30m (default: %(default)s)")
    parser.add_argument("--step",           default=DEFAULT_STEP,
                        help="Scrape resolution e.g. 15s 1m (default: %(default)s)")
    parser.add_argument("--output-dir",     default=str(DEFAULT_OUTPUT_DIR),
                        help="Root data directory (default: %(default)s)")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Print metric names from Prometheus and exit")
    parser.add_argument("--verbose",        action="store_true")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    output_dir = Path(args.output_dir)
    raw_dir    = output_dir / "raw"
    proc_dir   = output_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        names = get_all_metric_names(args.prometheus_url)
        print(f"\nPrometheus metric names ({len(names)} total):")
        for n in names:
            print(f"  • {n}")
        print(f"\nConfigured queries ({len(METRIC_QUERIES)}):")
        for key in METRIC_QUERIES:
            print(f"  • {key}")
        sys.exit(0)

    # ── Time window ───────────────────────────────────────────────────────────
    lookback_s = parse_duration_seconds(args.lookback)
    end_dt     = datetime.now(tz=timezone.utc)
    start_dt   = end_dt - timedelta(seconds=lookback_s)
    end_ts     = end_dt.timestamp()
    start_ts   = start_dt.timestamp()

    log.info("Querying Prometheus at %s", args.prometheus_url)
    log.info("Window: %s → %s  step=%s", start_dt.isoformat(), end_dt.isoformat(), args.step)

    # ── Collect metrics ───────────────────────────────────────────────────────
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_raw    = {}
    all_rows   = []

    for metric_name, promql in tqdm(METRIC_QUERIES.items(), desc="Fetching metrics", unit="metric"):
        log.debug("Query: %s", promql)
        results = query_range(args.prometheus_url, promql, start_ts, end_ts, args.step)
        all_raw[metric_name] = {
            "query":        promql,
            "fetched_at":   datetime.now(tz=timezone.utc).isoformat(),
            "window_start": start_dt.isoformat(),
            "window_end":   end_dt.isoformat(),
            "step":         args.step,
            "series_count": len(results),
            "data":         results,
        }
        rows = flatten_range_result(metric_name, results)
        all_rows.extend(rows)
        log.debug("  → %d series, %d data points", len(results), len(rows))

    # ── Save raw JSON ─────────────────────────────────────────────────────────
    raw_path = raw_dir / f"metrics_{timestamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(all_raw, f, indent=2)
    log.info("Raw metrics saved → %s", raw_path)

    # ── Save processed CSV ────────────────────────────────────────────────────
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_path = proc_dir / f"metrics_{timestamp}.csv"
        df.to_csv(csv_path, index=False)
        log.info(
            "Processed metrics saved → %s  (%d rows, %d series, %d services)",
            csv_path,
            len(df),
            df[["metric_name", "service", "container_name"]].drop_duplicates().__len__(),
            df["service"].nunique(),
        )
    else:
        log.warning("No metric data collected. Is cAdvisor/Prometheus running?")


if __name__ == "__main__":
    main()
