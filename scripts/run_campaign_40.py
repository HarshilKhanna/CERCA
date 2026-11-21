"""
run_campaign_40.py
──────────────────
Automated fault injection campaign to reach N=40 independent fault incidents.
Executes 32 new fault injection experiments across all DeathStarBench microservices,
bringing the total fault incident count from 8 to 40 (plus 2 baseline normal runs = 42 total).
"""

import subprocess
import time
import sys
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("campaign_40")

# 32 remaining experiments to reach N=40 fault incidents
# (Existing 8: user-service cpu, social-graph-service cpu, post-storage-service cpu,
#  unique-id-service cpu, home-timeline-service mem, user-timeline-service mem,
#  media-service restart, text-service restart)
EXPERIMENTS = [
    # --- CPU Faults (8 new, 12 total across all services) ---
    {"container": "compose-post-service", "type": "cpu"},
    {"container": "home-timeline-service", "type": "cpu"},
    {"container": "user-timeline-service", "type": "cpu"},
    {"container": "media-service", "type": "cpu"},
    {"container": "text-service", "type": "cpu"},
    {"container": "url-shorten-service", "type": "cpu"},
    {"container": "user-mention-service", "type": "cpu"},
    {"container": "nginx-thrift", "type": "cpu"},

    # --- Memory Faults (10 new, 12 total across all services) ---
    {"container": "user-service", "type": "memory"},
    {"container": "social-graph-service", "type": "memory"},
    {"container": "post-storage-service", "type": "memory"},
    {"container": "compose-post-service", "type": "memory"},
    {"container": "media-service", "type": "memory"},
    {"container": "text-service", "type": "memory"},
    {"container": "unique-id-service", "type": "memory"},
    {"container": "url-shorten-service", "type": "memory"},
    {"container": "user-mention-service", "type": "memory"},
    {"container": "nginx-thrift", "type": "memory"},

    # --- Restart Faults (10 new, 12 total across all services) ---
    {"container": "user-service", "type": "restart"},
    {"container": "social-graph-service", "type": "restart"},
    {"container": "post-storage-service", "type": "restart"},
    {"container": "home-timeline-service", "type": "restart"},
    {"container": "user-timeline-service", "type": "restart"},
    {"container": "unique-id-service", "type": "restart"},
    {"container": "compose-post-service", "type": "restart"},
    {"container": "url-shorten-service", "type": "restart"},
    {"container": "user-mention-service", "type": "restart"},
    {"container": "nginx-thrift", "type": "restart"},

    # --- Replicate/Severe Stress on Core Fan-in/Hub Tiers (4 new -> total 40) ---
    {"container": "compose-post-service", "type": "cpu"},
    {"container": "post-storage-service", "type": "cpu"},
    {"container": "home-timeline-service", "type": "memory"},
    {"container": "social-graph-service", "type": "restart"},
]

def main():
    log.info("=" * 70)
    log.info(" STARTING N=40 FAULT INJECTION CAMPAIGN (32 Experiments to Run)")
    log.info("=" * 70)
    start_time = time.time()

    successes = 0
    failures = 0

    for idx, exp in enumerate(EXPERIMENTS, 1):
        c = exp["container"]
        t = exp["type"]
        log.info(f"\n[{idx}/{len(EXPERIMENTS)}] Launching {t.upper()} fault on {c} ...")

        cmd = [
            sys.executable, "scripts/run_fault_experiment.py",
            "--container", c,
            "--type", t,
            "--duration", "20"
        ]

        res = subprocess.run(cmd)
        if res.returncode == 0:
            successes += 1
            log.info(f"[{idx}/{len(EXPERIMENTS)}] ✓ {t.upper()} on {c} SUCCESSFUL")
        else:
            failures += 1
            log.error(f"[{idx}/{len(EXPERIMENTS)}] ✗ {t.upper()} on {c} FAILED (exit code {res.returncode})")

        # Brief cooldown between experiments to let Jaeger & queues drain
        time.sleep(3)

    elapsed_min = (time.time() - start_time) / 60.0
    log.info("\n" + "=" * 70)
    log.info(f" CAMPAIGN COMPLETE: {successes} succeeded, {failures} failed in {elapsed_min:.1f} minutes")
    log.info("=" * 70)

    log.info("Rebuilding RCA Dataset across all experiments...")
    subprocess.run([sys.executable, "scripts/build_rca_dataset.py"], check=True)
    log.info("Dataset rebuild complete!")

if __name__ == "__main__":
    main()

