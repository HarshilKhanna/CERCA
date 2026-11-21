import argparse
import subprocess
import logging
import sys
import time
import os
import json
import glob

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

def run_cmd(cmd: list[str], env=None) -> bool:
    cmd_str = " ".join(cmd)
    log.info(f"Running: {cmd_str}")
    try:
        subprocess.run(cmd, check=True, env=env)
        return True
    except subprocess.CalledProcessError:
        log.error(f"Command failed: {cmd_str}")
        return False

def label_latest_experiment(container: str, fault_type: str):
    experiments = glob.glob("E:/CERCA/experiments/*")
    if not experiments:
        return
    latest_exp = max(experiments, key=os.path.getctime)
    label_path = os.path.join(latest_exp, "fault_label.json")
    svc_name = "nginx-web-server" if container == "nginx-thrift" else container
    with open(label_path, "w") as f:
        json.dump({"root_cause_service": svc_name, "fault_type": fault_type}, f, indent=2)
    log.info(f"Labeled experiment {latest_exp} -> root_cause={svc_name}, fault_type={fault_type}")

def main():
    parser = argparse.ArgumentParser(description="Run a full fault experiment.")
    parser.add_argument("--container", required=True, help="Target container name")
    parser.add_argument("--type", choices=["cpu", "memory", "restart", "network", "diskio"], default="cpu")
    parser.add_argument("--duration", type=int, default=25, help="Workload duration in seconds")
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    log.info("=" * 60)
    log.info(f"Starting {args.type.upper()} Fault Experiment on {args.container}")
    log.info("=" * 60)

    if not run_cmd(["python", "scripts/inject_fault.py", "--container", args.container, "--action", "inject", "--type", args.type]):
        sys.exit(1)

    log.info("Waiting 3s for fault to settle...")
    time.sleep(3)
    
    if not run_cmd(["python", "scripts/generate_workload.py", "--duration", str(args.duration), "--rate", "40"], env=env):
        log.error("Workload failed. Restoring fault and exiting.")
        run_cmd(["python", "scripts/inject_fault.py", "--container", args.container, "--action", "restore", "--type", args.type])
        sys.exit(1)

    log.info("Waiting 4s for traces to flush to Jaeger...")
    time.sleep(4)

    if not run_cmd(["python", "scripts/run_collection.py", "--lookback", "3m", "--settle", "2"], env=env):
        log.error("Collection failed. Restoring fault and exiting.")
        run_cmd(["python", "scripts/inject_fault.py", "--container", args.container, "--action", "restore", "--type", args.type])
        sys.exit(1)

    label_latest_experiment(args.container, args.type)

    run_cmd(["python", "scripts/inject_fault.py", "--container", args.container, "--action", "restore", "--type", args.type])

    log.info("=" * 60)
    log.info(f"{args.type.upper()} Fault Experiment on {args.container} Complete!")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
