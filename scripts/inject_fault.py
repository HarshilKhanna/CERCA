import argparse
import subprocess
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

def run_cmd(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        log.error("Command failed: %s\n%s", " ".join(cmd), e.stderr.decode("utf-8", errors="ignore"))
        return False

def inject_cpu(container: str, quota: int = 10000) -> bool:
    log.info(f"Injecting CPU fault on {container}: quota={quota} (10% of 1 core)")
    return run_cmd(["docker", "update", "--cpu-quota", str(quota), container])

def inject_memory(container: str, limit: str = "20m") -> bool:
    log.info(f"Injecting Memory fault on {container}: limit={limit}")
    return run_cmd(["docker", "update", "-m", limit, container])

def inject_restart(container: str) -> bool:
    log.info(f"Injecting Restart fault on {container}")
    # We restart it and then exit, but for a continuous fault during workload,
    # we might want a background loop. For simplicity, just restarting it once 
    # right before workload causes a temporary outage and cache miss.
    return run_cmd(["docker", "restart", container])

def restore_cpu(container: str) -> bool:
    log.info(f"Restoring CPU quota on {container} to unlimited (-1)")
    return run_cmd(["docker", "update", "--cpu-quota", "-1", container])

def restore_memory(container: str) -> bool:
    log.info(f"Restoring Memory limit on {container} to unlimited (0)")
    return run_cmd(["docker", "update", "-m", "0", container])

def main():
    parser = argparse.ArgumentParser(description="Inject simulated faults into DSB containers.")
    parser.add_argument("--container", required=True, help="Target container name")
    parser.add_argument("--action", choices=["inject", "restore"], required=True)
    parser.add_argument("--type", choices=["cpu", "memory", "restart"], default="cpu")
    
    args = parser.parse_args()

    success = False
    if args.action == "inject":
        if args.type == "cpu":
            success = inject_cpu(args.container)
        elif args.type == "memory":
            success = inject_memory(args.container)
        elif args.type == "restart":
            success = inject_restart(args.container)
    else:
        if args.type == "cpu":
            success = restore_cpu(args.container)
        elif args.type == "memory":
            success = restore_memory(args.container)
        elif args.type == "restart":
            # Restart fault restores itself automatically by coming back online
            success = True

    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
