"""
fetch_traces.py
───────────────
Fetch all distributed traces from the Jaeger HTTP Query API and persist them in
two formats:
  • data/raw/traces_<timestamp>.json   – verbatim Jaeger API response per service
  • data/processed/spans_<timestamp>.csv – flat, tidy span table

Usage
─────
    python scripts/fetch_traces.py [OPTIONS]

Options
    --jaeger-url   Base URL of Jaeger query service  [default: http://localhost:16686]
    --lookback     How far back to fetch traces (e.g. 1h, 30m, 2h)  [default: 1h]
    --limit        Max traces per service per window  [default: 2000]
    --output-dir   Root data directory  [default: data/]
    --dry-run      Print discovered services and exit without saving
    --verbose      Enable debug logging

Output CSV columns
──────────────────
    traceID, spanID, parentSpanID, service, operation,
    startTime_us, duration_us, startTime_iso,
    error, http_status_code, http_method, http_url

Exit codes: 0 = success, 1 = connection error, 2 = no traces found
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
from tqdm import tqdm

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_JAEGER_URL  = "http://localhost:16686"
DEFAULT_LOOKBACK    = "1h"
DEFAULT_LIMIT       = 2000
DEFAULT_OUTPUT_DIR  = Path(__file__).parent.parent / "data"

LOOKBACK_MAP = {
    "m": 60,
    "h": 3600,
    "d": 86400,
}

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_traces")

# ─── Helpers ──────────────────────────────────────────────────────────────────


def parse_lookback(lookback_str: str) -> int:
    """Parse lookback string like '1h', '30m', '2d' into seconds."""
    if lookback_str[-1] in LOOKBACK_MAP:
        return int(lookback_str[:-1]) * LOOKBACK_MAP[lookback_str[-1]]
    raise ValueError(f"Unknown lookback unit in '{lookback_str}'. Use s/m/h/d.")


def get_services(base_url: str) -> list[str]:
    """Return list of all service names known to Jaeger."""
    url = f"{base_url}/api/services"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        log.error("Cannot connect to Jaeger at %s — is the stack running?", base_url)
        sys.exit(1)
    data = resp.json()
    services = data.get("data", [])
    if not services:
        log.warning("Jaeger returned zero services. Has any traffic been generated?")
    return sorted(services)


def fetch_traces_for_service(
    base_url: str,
    service: str,
    start_us: int,
    end_us: int,
    limit: int,
) -> list[dict]:
    """
    Fetch traces for one service within [start_us, end_us] (microseconds).
    Automatically windows if Jaeger returns `limit` results (possible more data).
    """
    params = {
        "service": service,
        "start":   start_us,
        "end":     end_us,
        "limit":   limit,
    }
    resp = requests.get(f"{base_url}/api/traces", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def extract_tag_value(tags: list[dict], key: str, default=None):
    """Extract value of first tag matching key from a Jaeger tags list."""
    for tag in tags:
        if tag.get("key") == key:
            return tag.get("value", default)
    return default


def flatten_spans(traces: list[dict], service_name: str) -> list[dict]:
    """
    Flatten a Jaeger traces payload into a list of per-span records.
    Each record maps to one row in the output CSV.
    """
    rows = []
    for trace in traces:
        trace_id    = trace["traceID"]
        # Build a lookup from spanID → process/service mapping
        processes   = trace.get("processes", {})
        span_to_svc = {}
        for span in trace.get("spans", []):
            pid = span.get("processID", "")
            svc = processes.get(pid, {}).get("serviceName", service_name)
            span_to_svc[span["spanID"]] = svc

        for span in trace.get("spans", []):
            span_id    = span["spanID"]
            parent_id  = ""
            if span.get("references"):
                ref = span["references"][0]
                if ref.get("refType") == "CHILD_OF":
                    parent_id = ref.get("spanID", "")

            pid          = span.get("processID", "")
            svc          = processes.get(pid, {}).get("serviceName", service_name)
            start_us     = span["startTime"]
            duration_us  = span["duration"]
            tags         = span.get("tags", [])
            has_error    = extract_tag_value(tags, "error", False)
            http_status  = extract_tag_value(tags, "http.status_code")
            http_method  = extract_tag_value(tags, "http.method")
            http_url     = extract_tag_value(tags, "http.url")

            start_dt = datetime.fromtimestamp(start_us / 1e6, tz=timezone.utc).isoformat()

            rows.append({
                "traceID":          trace_id,
                "spanID":           span_id,
                "parentSpanID":     parent_id,
                "service":          svc,
                "operation":        span.get("operationName", ""),
                "startTime_us":     start_us,
                "duration_us":      duration_us,
                "startTime_iso":    start_dt,
                "error":            bool(has_error),
                "http_status_code": http_status,
                "http_method":      http_method,
                "http_url":         http_url,
            })
    return rows


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fetch traces from Jaeger HTTP API")
    parser.add_argument("--jaeger-url",  default=DEFAULT_JAEGER_URL,
                        help="Jaeger query base URL (default: %(default)s)")
    parser.add_argument("--lookback",    default=DEFAULT_LOOKBACK,
                        help="Lookback window, e.g. 1h 30m 2d (default: %(default)s)")
    parser.add_argument("--limit",       type=int, default=DEFAULT_LIMIT,
                        help="Max traces per service (default: %(default)s)")
    parser.add_argument("--output-dir",  default=str(DEFAULT_OUTPUT_DIR),
                        help="Root data directory (default: %(default)s)")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Print services and exit without saving")
    parser.add_argument("--verbose",     action="store_true",
                        help="Enable debug output")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    output_dir = Path(args.output_dir)
    raw_dir    = output_dir / "raw"
    proc_dir   = output_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    # ── Time window ──────────────────────────────────────────────────────────
    lookback_s  = parse_lookback(args.lookback)
    end_dt      = datetime.now(tz=timezone.utc)
    start_dt    = end_dt - timedelta(seconds=lookback_s)
    end_us      = int(end_dt.timestamp()   * 1_000_000)
    start_us    = int(start_dt.timestamp() * 1_000_000)

    log.info("Querying Jaeger at %s", args.jaeger_url)
    log.info("Window: %s → %s", start_dt.isoformat(), end_dt.isoformat())

    # ── Discover services ─────────────────────────────────────────────────────
    services = get_services(args.jaeger_url)
    log.info("Discovered %d services: %s", len(services), services)

    if args.dry_run:
        print("\nServices found:")
        for svc in services:
            print(f"  • {svc}")
        sys.exit(0)

    if not services:
        sys.exit(2)

    # ── Fetch traces per service ──────────────────────────────────────────────
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_raw     = {}
    all_spans   = []

    for svc in tqdm(services, desc="Fetching traces", unit="service"):
        log.debug("Fetching traces for: %s", svc)
        try:
            traces = fetch_traces_for_service(
                args.jaeger_url, svc, start_us, end_us, args.limit
            )
        except requests.HTTPError as e:
            log.warning("HTTP error fetching %s: %s", svc, e)
            traces = []

        all_raw[svc] = {
            "service":    svc,
            "trace_count": len(traces),
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "window_start": start_dt.isoformat(),
            "window_end":   end_dt.isoformat(),
            "traces":     traces,
        }

        spans = flatten_spans(traces, svc)
        all_spans.extend(spans)
        log.debug("  → %d traces, %d spans", len(traces), len(spans))

    # ── Save raw JSON ─────────────────────────────────────────────────────────
    raw_path = raw_dir / f"traces_{timestamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(all_raw, f, indent=2)
    log.info("Raw traces saved → %s", raw_path)

    # ── Save processed CSV ────────────────────────────────────────────────────
    if all_spans:
        df = pd.DataFrame(all_spans)
        # Deduplicate spans that appear in multiple service queries
        df.drop_duplicates(subset=["traceID", "spanID"], inplace=True)
        csv_path = proc_dir / f"spans_{timestamp}.csv"
        df.to_csv(csv_path, index=False)
        log.info(
            "Processed spans saved → %s  (%d rows, %d unique traces)",
            csv_path, len(df), df["traceID"].nunique(),
        )
    else:
        log.warning("No spans collected. Check that workload has been generated.")
        sys.exit(2)


if __name__ == "__main__":
    main()
