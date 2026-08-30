"""
generate_workload.py
────────────────────
Generate realistic HTTP workload against the DeathStarBench Social Network
gateway (nginx-thrift) to produce distributed traces in Jaeger.

Workload mix (matches DSB research paper benchmark):
  40%  compose-post     — POST /wrk2-api/user/compose-post
  30%  read-home-timeline — GET /wrk2-api/user/read-home-timeline
  30%  read-user-timeline — GET /wrk2-api/user/read-user-timeline

Uses Python threading to achieve target concurrency without wrk2.
All requests go through nginx-thrift → Thrift RPC → backend services,
which generates the full distributed trace across all participating services.

Usage
─────
    python scripts/generate_workload.py [OPTIONS]

    python scripts/generate_workload.py --duration 120 --rate 50
    python scripts/generate_workload.py --duration 60 --workers 5 --users 50
    python scripts/generate_workload.py --duration 300 --rate 100 --users 100

Options
    --url        nginx-thrift base URL [default: http://localhost:8080]
    --duration   How long to run in seconds [default: 120]
    --rate       Target requests per second (approximate) [default: 50]
    --workers    Concurrent worker threads [default: 4]
    --users      Number of registered users in the system [default: 50]
"""

import argparse
import logging
import queue
import random
import statistics
import string
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("generate_workload")

DEFAULT_URL      = "http://localhost:8080"
DEFAULT_DURATION = 120
DEFAULT_RATE     = 50
DEFAULT_WORKERS  = 4
DEFAULT_USERS    = 50

# Request mix weights
MIX = {
    "compose_post":        0.40,
    "read_home_timeline":  0.30,
    "read_user_timeline":  0.30,
}


# ─── Stats collector ──────────────────────────────────────────────────────────

@dataclass
class Stats:
    lock:         threading.Lock = field(default_factory=threading.Lock)
    total:        int            = 0
    success:      int            = 0
    error:        int            = 0
    latencies_ms: list           = field(default_factory=list)

    def record(self, success: bool, latency_ms: float):
        with self.lock:
            self.total    += 1
            self.latencies_ms.append(latency_ms)
            if success:
                self.success += 1
            else:
                self.error   += 1

    def summary(self) -> dict:
        with self.lock:
            lats = sorted(self.latencies_ms)
            n = len(lats)
            return {
                "total":      self.total,
                "success":    self.success,
                "error":      self.error,
                "error_rate": self.error / self.total if self.total else 0,
                "mean_ms":    statistics.mean(lats) if lats else 0,
                "p50_ms":     lats[int(n * 0.50)] if lats else 0,
                "p95_ms":     lats[int(n * 0.95)] if lats else 0,
                "p99_ms":     lats[int(n * 0.99)] if lats else 0,
            }


# ─── Request implementations ──────────────────────────────────────────────────


def _rand_text(words: int = 8) -> str:
    """Generate a random post body."""
    vocab = [
        "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
        "microservice", "latency", "trace", "span", "graph", "service",
        "request", "response", "data", "system", "network", "fault",
    ]
    return " ".join(random.choices(vocab, k=words))


def compose_post(base_url: str, user_id: int, n_users: int) -> tuple[bool, float]:
    """POST a new text post as user_id."""
    mentions = [f"@username_{random.randint(1, n_users)}" for _ in range(random.randint(0, 2))]
    body_text = _rand_text(10) + " " + " ".join(mentions)

    payload = {
        "username":   f"username_{user_id}",
        "user_id":    user_id,
        "text":       body_text,
        "media_ids":  "[]",
        "media_types": "[]",
        "post_type":  0,
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{base_url}/wrk2-api/post/compose",
            data=payload, timeout=10,
        )
        lat = (time.perf_counter() - t0) * 1000
        return r.status_code == 200, lat
    except Exception:
        return False, (time.perf_counter() - t0) * 1000


def read_home_timeline(base_url: str, user_id: int) -> tuple[bool, float]:
    """GET home timeline for user_id."""
    params = {
        "user_id": user_id,
        "start":   0,
        "stop":    10,
    }
    t0 = time.perf_counter()
    try:
        r = requests.get(
            f"{base_url}/wrk2-api/home-timeline/read",
            params=params, timeout=10,
        )
        lat = (time.perf_counter() - t0) * 1000
        return r.status_code == 200, lat
    except Exception:
        return False, (time.perf_counter() - t0) * 1000


def read_user_timeline(base_url: str, user_id: int) -> tuple[bool, float]:
    """GET personal timeline for user_id."""
    params = {
        "user_id": user_id,
        "start":   0,
        "stop":    10,
    }
    t0 = time.perf_counter()
    try:
        r = requests.get(
            f"{base_url}/wrk2-api/user-timeline/read",
            params=params, timeout=10,
        )
        lat = (time.perf_counter() - t0) * 1000
        return r.status_code == 200, lat
    except Exception:
        return False, (time.perf_counter() - t0) * 1000


# ─── Worker thread ────────────────────────────────────────────────────────────


def worker(base_url: str, n_users: int, task_queue: queue.Queue, stats: Stats):
    """Pull tasks from queue and execute them until None sentinel received."""
    while True:
        task = task_queue.get()
        if task is None:
            task_queue.task_done()
            break

        user_id = random.randint(1, n_users)
        if task == "compose_post":
            ok, lat = compose_post(base_url, user_id, n_users)
        elif task == "read_home_timeline":
            ok, lat = read_home_timeline(base_url, user_id)
        else:
            ok, lat = read_user_timeline(base_url, user_id)

        stats.record(ok, lat)
        task_queue.task_done()


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Generate DSB Social Network HTTP workload")
    parser.add_argument("--url",      default=DEFAULT_URL)
    parser.add_argument("--duration", type=int,   default=DEFAULT_DURATION,
                        help="Duration in seconds (default: 120)")
    parser.add_argument("--rate",     type=int,   default=DEFAULT_RATE,
                        help="Target requests/sec (default: 50)")
    parser.add_argument("--workers",  type=int,   default=DEFAULT_WORKERS,
                        help="Concurrent worker threads (default: 4)")
    parser.add_argument("--users",    type=int,   default=DEFAULT_USERS,
                        help="Number of registered users (default: 50)")
    args = parser.parse_args()

    # Verify gateway reachable
    log.info("Checking gateway at %s …", args.url)
    try:
        requests.get(args.url, timeout=5)
    except Exception as e:
        log.error("Cannot reach %s: %s — is the stack running?", args.url, e)
        sys.exit(1)

    log.info(
        "Starting workload: duration=%ds  rate=%d rps  workers=%d  users=%d",
        args.duration, args.rate, args.workers, args.users,
    )

    stats     = Stats()
    tq        = queue.Queue(maxsize=args.rate * 5)
    workers   = []

    # Start worker threads
    for _ in range(args.workers):
        t = threading.Thread(target=worker, args=(args.url, args.users, tq, stats), daemon=True)
        t.start()
        workers.append(t)

    # Request types weighted by MIX
    request_types = list(MIX.keys())
    weights       = list(MIX.values())
    interval      = 1.0 / args.rate  # seconds between requests

    start     = time.time()
    deadline  = start + args.duration
    last_log  = start

    while time.time() < deadline:
        task = random.choices(request_types, weights=weights, k=1)[0]
        tq.put(task)

        # Progress log every 15 seconds
        now = time.time()
        if now - last_log >= 15:
            elapsed  = now - start
            s        = stats.summary()
            rps_act  = s["total"] / elapsed if elapsed > 0 else 0
            log.info(
                "[%.0fs / %ds]  sent=%d  ok=%d  err=%d  actual_rps=%.1f  p95=%.0fms",
                elapsed, args.duration,
                s["total"], s["success"], s["error"], rps_act, s["p95_ms"],
            )
            last_log = now

        time.sleep(interval)

    # Send sentinel to stop workers
    for _ in workers:
        tq.put(None)
    tq.join()

    # Final summary
    s        = stats.summary()
    elapsed  = time.time() - start
    rps_act  = s["total"] / elapsed if elapsed > 0 else 0

    print("\n" + "-" * 55)
    print("  Workload Complete")
    print("-" * 55)
    print(f"  Duration:     {elapsed:.1f}s")
    print(f"  Total reqs:   {s['total']:,}")
    print(f"  Success:      {s['success']:,}  ({100*(1-s['error_rate']):.1f}%)")
    print(f"  Errors:       {s['error']:,}  ({100*s['error_rate']:.1f}%)")
    print(f"  Actual RPS:   {rps_act:.1f}")
    print(f"  Mean latency: {s['mean_ms']:.0f}ms")
    print(f"  P50 latency:  {s['p50_ms']:.0f}ms")
    print(f"  P95 latency:  {s['p95_ms']:.0f}ms")
    print(f"  P99 latency:  {s['p99_ms']:.0f}ms")
    print("-" * 55)

    if s["success"] == 0:
        log.error("No successful requests. Check that init_social_graph.py was run first.")
        sys.exit(1)

    log.info("Traces are now in Jaeger. Run scripts/run_collection.py to collect them.")


if __name__ == "__main__":
    main()
