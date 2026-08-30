"""
run_collection.py
─────────────────
End-to-end telemetry collection orchestrator.

Workflow
────────
  1. (Optional) Wait for the Docker Compose stack to be healthy.
  2. (Optional) Generate workload via wrk2 inside the nginx-thrift container.
  3. Sleep for <settle> seconds to let traces propagate to Jaeger.
  4. Run fetch_traces.py  → data/raw/  + data/processed/
  5. Run fetch_metrics.py → data/raw/  + data/processed/
  6. Run build_dependency_graph.py → graphs/
  7. Archive all outputs under experiments/<timestamp>/

Usage
─────
    python scripts/run_collection.py [OPTIONS]

Options
    --jaeger-url       [default: http://localhost:16686]
    --prometheus-url   [default: http://localhost:9090]
    --lookback         Lookback window for traces & metrics [default: 1h]
    --step             Prometheus step [default: 15s]
    --settle           Seconds to wait after workload before fetching [default: 30]
    --workload         Run wrk2 workload generator (requires Docker) [default: False]
    --wrk2-duration    wrk2 duration string [default: 60s]
    --wrk2-rate        wrk2 requests/sec [default: 100]
    --wrk2-threads     wrk2 thread count [default: 2]
    --wrk2-connections wrk2 connection count [default: 50]
    --no-archive       Skip archiving outputs
    --skip-graph       Skip dependency graph build
    --dry-run          Smoke test connectivity only
    --verbose          Debug logging

Exit codes: 0 = success, 1 = connectivity failure, 2 = script failure
"""

import argparse
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_collection")

SCRIPTS_DIR   = Path(__file__).parent
PROJECT_ROOT  = SCRIPTS_DIR.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"

# ─── Connectivity checks ──────────────────────────────────────────────────────


def check_endpoint(url: str, label: str, timeout: int = 10) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        ok = r.status_code < 500
        status = "✓" if ok else "✗"
        log.info("%s  %s  [HTTP %s]", status, label, r.status_code)
        return ok
    except Exception as e:
        log.warning("✗  %s  — %s", label, e)
        return False


def wait_for_stack(jaeger_url: str, prometheus_url: str, retries: int = 20, delay: int = 5):
    """Block until both Jaeger and Prometheus are reachable."""
    for attempt in range(1, retries + 1):
        j_ok = check_endpoint(f"{jaeger_url}/api/services", "Jaeger /api/services")
        p_ok = check_endpoint(f"{prometheus_url}/-/ready",  "Prometheus /-/ready")
        if j_ok and p_ok:
            log.info("Stack is healthy ✓")
            return
        log.info("Attempt %d/%d — retrying in %ds …", attempt, retries, delay)
        time.sleep(delay)
    log.error("Stack not reachable after %d attempts. Aborting.", retries)
    sys.exit(1)


# ─── Workload generation ──────────────────────────────────────────────────────


def run_workload(duration: str, rate: int, threads: int, connections: int):
    """
    Run wrk2 inside the nginx-thrift container to generate Social Network traffic.
    The Social Network exposes three main endpoints:
      POST /wrk2-api/user/compose-post  (compose post)
      GET  /wrk2-api/user/read-home-timeline (read home timeline)
      GET  /wrk2-api/user/read-user-timeline (read user timeline)
    wrk2 uses Lua scripts provided inside the nginx-thrift image.
    """
    log.info("Starting wrk2 workload: duration=%s rate=%d conns=%d threads=%d",
             duration, rate, connections, threads)
    script_path = "/usr/local/openresty/nginx/lua-scripts/wrk2_scripts/mixed-workload_type_1.lua"
    wrk2_cmd = [
        "docker", "exec", "nginx-thrift",
        "wrk", f"-D{duration}", f"-t{threads}", f"-c{connections}",
        f"-R{rate}", "--latency",
        f"-s{script_path}",
        "http://localhost:8080",
    ]
    log.debug("Command: %s", " ".join(wrk2_cmd))
    try:
        result = subprocess.run(
            wrk2_cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            log.warning("wrk2 exited with code %d:\n%s", result.returncode, result.stderr)
        else:
            log.info("wrk2 finished:\n%s", result.stdout[-2000:])  # last 2k chars
    except subprocess.TimeoutExpired:
        log.error("wrk2 timed out")
    except FileNotFoundError:
        log.error("docker not found in PATH — cannot run workload")


# ─── Script runner ────────────────────────────────────────────────────────────


def run_script(script_name: str, extra_args: list[str]) -> int:
    """Run a Python script in the current interpreter and return exit code."""
    script_path = SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path)] + extra_args
    log.info("Running: %s %s", script_name, " ".join(extra_args))
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


# ─── Archive ──────────────────────────────────────────────────────────────────


def archive_outputs(timestamp: str):
    """Copy data/ and graphs/ into experiments/<timestamp>/."""
    exp_dir = EXPERIMENTS_DIR / timestamp
    exp_dir.mkdir(parents=True, exist_ok=True)

    for src_dir in [PROJECT_ROOT / "data", PROJECT_ROOT / "graphs"]:
        if src_dir.exists():
            dst = exp_dir / src_dir.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src_dir, dst)

    log.info("Outputs archived → %s", exp_dir)
    return exp_dir


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="DeathStarBench telemetry collection orchestrator")
    parser.add_argument("--jaeger-url",        default="http://localhost:16686")
    parser.add_argument("--prometheus-url",    default="http://localhost:9090")
    parser.add_argument("--lookback",          default="1h",
                        help="Lookback window for traces & metrics (default: 1h)")
    parser.add_argument("--step",              default="15s",
                        help="Prometheus resolution step (default: 15s)")
    parser.add_argument("--settle",            type=int, default=30,
                        help="Seconds to wait after workload before fetching (default: 30)")
    parser.add_argument("--workload",          action="store_true",
                        help="Run wrk2 workload inside nginx-thrift container")
    parser.add_argument("--wrk2-duration",     default="60s")
    parser.add_argument("--wrk2-rate",         type=int, default=100)
    parser.add_argument("--wrk2-threads",      type=int, default=2)
    parser.add_argument("--wrk2-connections",  type=int, default=50)
    parser.add_argument("--no-archive",        action="store_true")
    parser.add_argument("--skip-graph",        action="store_true")
    parser.add_argument("--dry-run",           action="store_true",
                        help="Check connectivity only, do not collect")
    parser.add_argument("--verbose",           action="store_true")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("═══ DeathStarBench Telemetry Collection ═══")
    log.info("Experiment timestamp: %s", timestamp)

    # ── Step 1: Connectivity check ────────────────────────────────────────────
    log.info("── Step 1: Checking stack connectivity")
    wait_for_stack(args.jaeger_url, args.prometheus_url)

    if args.dry_run:
        log.info("Dry run complete — connectivity OK.")
        sys.exit(0)

    # ── Step 2: (Optional) Workload generation ────────────────────────────────
    if args.workload:
        log.info("── Step 2: Generating workload via wrk2")
        run_workload(
            args.wrk2_duration,
            args.wrk2_rate,
            args.wrk2_threads,
            args.wrk2_connections,
        )
        log.info("Waiting %ds for traces to propagate to Jaeger …", args.settle)
        time.sleep(args.settle)
    else:
        log.info("── Step 2: Skipping workload (--workload not set)")

    # ── Step 3: Fetch traces ──────────────────────────────────────────────────
    log.info("── Step 3: Fetching traces from Jaeger")
    rc = run_script("fetch_traces.py", [
        "--jaeger-url", args.jaeger_url,
        "--lookback",   args.lookback,
        *(["--verbose"] if args.verbose else []),
    ])
    if rc != 0:
        log.error("fetch_traces.py failed with exit code %d", rc)
        sys.exit(2)

    # ── Step 4: Fetch metrics ─────────────────────────────────────────────────
    log.info("── Step 4: Fetching metrics from Prometheus")
    rc = run_script("fetch_metrics.py", [
        "--prometheus-url", args.prometheus_url,
        "--lookback",       args.lookback,
        "--step",           args.step,
        *(["--verbose"] if args.verbose else []),
    ])
    if rc != 0:
        log.error("fetch_metrics.py failed with exit code %d", rc)
        sys.exit(2)

    # ── Step 5: Build dependency graph ────────────────────────────────────────
    if not args.skip_graph:
        log.info("── Step 5: Building service dependency graph")
        rc = run_script("build_dependency_graph.py", [
            *(["--verbose"] if args.verbose else []),
        ])
        if rc != 0:
            log.warning("build_dependency_graph.py returned code %d (non-fatal)", rc)
    else:
        log.info("── Step 5: Skipping graph build (--skip-graph)")

    # ── Step 6: Archive ───────────────────────────────────────────────────────
    if not args.no_archive:
        log.info("── Step 6: Archiving outputs")
        exp_dir = archive_outputs(timestamp)
        log.info("All outputs archived → %s", exp_dir)
    else:
        log.info("── Step 6: Skipping archive (--no-archive)")

    log.info("═══ Collection complete (%s) ═══", timestamp)


if __name__ == "__main__":
    main()
