import argparse
import subprocess
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run_cmd(cmd: list[str], timeout: int = 30) -> bool:
    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        log.error("Command timed out after %ds: %s", timeout, " ".join(cmd))
        return False
    except subprocess.CalledProcessError as e:
        log.error("Command failed: %s\n%s", " ".join(cmd),
                  e.stderr.decode("utf-8", errors="ignore"))
        return False

# ---------------------------------------------------------------------------
# Fault injectors
# ---------------------------------------------------------------------------
def inject_cpu(container: str, quota: int = 10000) -> bool:
    """Throttle CPU to quota microseconds per 100ms period (~10% of 1 core)."""
    log.info("Injecting CPU fault on %s: quota=%d (≈10%% of 1 core)", container, quota)
    return run_cmd(["docker", "update", "--cpu-quota", str(quota), container])

def inject_memory(container: str, limit: str = "20m") -> bool:
    """Hard-cap container memory to limit (e.g. '20m')."""
    log.info("Injecting Memory fault on %s: limit=%s", container, limit)
    return run_cmd(["docker", "update", "-m", limit, "--memory-swap", limit, container])

def inject_restart(container: str) -> bool:
    """Force-restart the container to simulate a crash/restart fault."""
    log.info("Injecting Restart fault on %s", container)
    return run_cmd(["docker", "restart", container])

def inject_network(container: str, delay_ms: int = 200, loss_pct: float = 10.0) -> bool:
    """
    Inject network degradation using Linux tc (traffic control) inside the container.
    Adds latency delay and packet loss on the eth0 interface.
    Requires the container to have NET_ADMIN capability or run as root.
    """
    log.info("Injecting Network fault on %s: delay=%dms, loss=%.1f%%",
             container, delay_ms, loss_pct)

    # Remove any existing qdisc first (ignore error if none)
    run_cmd(["docker", "exec", container,
             "tc", "qdisc", "del", "dev", "eth0", "root"], timeout=10)

    # Add netem qdisc with delay + loss
    ok = run_cmd(["docker", "exec", container,
                  "tc", "qdisc", "add", "dev", "eth0", "root", "netem",
                  "delay", f"{delay_ms}ms", "10ms", "distribution", "normal",
                  "loss", f"{loss_pct}%"])
    if not ok:
        log.warning("tc netem injection failed — container may lack NET_ADMIN. "
                    "Falling back to iptables packet drop.")
        # Fallback: iptables OUTPUT drop at loss_pct rate
        ok = run_cmd(["docker", "exec", container,
                      "iptables", "-A", "OUTPUT",
                      "-m", "statistic", "--mode", "random",
                      "--probability", str(loss_pct / 100),
                      "-j", "DROP"])
    return ok

def inject_diskio(container: str, write_bps: int = 1_048_576,
                  read_bps: int = 1_048_576) -> bool:
    """
    Throttle container block device I/O bandwidth using docker update blkio throttle.
    Default: 1 MB/s read + write (simulates constrained disk I/O).
    Requires knowledge of the host block device major:minor (auto-detected via /proc).
    """
    log.info("Injecting Disk I/O fault on %s: write=%d B/s, read=%d B/s",
             container, write_bps, read_bps)

    # Auto-detect the block device inside the container via /proc/mounts
    result = subprocess.run(
        ["docker", "exec", container, "cat", "/proc/mounts"],
        capture_output=True, text=True, timeout=10
    )
    device = None
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "/":
                dev_name = parts[0].split("/")[-1]
                # Get major:minor from /sys/block
                sys_result = subprocess.run(
                    ["docker", "exec", container, "cat", f"/sys/block/{dev_name}/dev"],
                    capture_output=True, text=True, timeout=5
                )
                if sys_result.returncode == 0:
                    device = sys_result.stdout.strip()
                break

    if device is None:
        # Fallback: common device major:minor for overlay2 (8:0 = sda, 252:0 = vda)
        device = "8:0"
        log.warning("Could not detect block device — using fallback %s", device)

    ok_w = run_cmd(["docker", "update",
                    f"--blkio-weight-device={device}:10",
                    f"--device-write-bps={device}:{write_bps}",
                    container])
    ok_r = run_cmd(["docker", "update",
                    f"--device-read-bps={device}:{read_bps}",
                    container])
    return ok_w and ok_r

# ---------------------------------------------------------------------------
# Restorers
# ---------------------------------------------------------------------------
def restore_cpu(container: str) -> bool:
    log.info("Restoring CPU quota on %s to unlimited (-1)", container)
    return run_cmd(["docker", "update", "--cpu-quota", "-1", container])

def restore_memory(container: str) -> bool:
    log.info("Restoring Memory limit on %s to unlimited (0)", container)
    return run_cmd(["docker", "update", "-m", "0", "--memory-swap", "0", container])

def restore_restart(_container: str) -> bool:
    # Container came back online by itself after restart
    return True

def restore_network(container: str) -> bool:
    log.info("Restoring network on %s (removing netem qdisc)", container)
    ok = run_cmd(["docker", "exec", container,
                  "tc", "qdisc", "del", "dev", "eth0", "root"], timeout=10)
    # Also flush iptables fallback rules if any
    run_cmd(["docker", "exec", container, "iptables", "-F", "OUTPUT"], timeout=10)
    return ok

def restore_diskio(container: str) -> bool:
    log.info("Restoring Disk I/O on %s", container)
    # Remove blkio throttle by resetting to defaults (0 = unlimited)
    return run_cmd(["docker", "update",
                    "--blkio-weight=0",
                    "--device-write-bps=",    # not all docker versions accept empty; best effort
                    container], timeout=10)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Inject simulated faults into DeathStarBench containers."
    )
    parser.add_argument("--container", required=True,
                        help="Target container name (e.g. user-service)")
    parser.add_argument("--action", choices=["inject", "restore"], required=True)
    parser.add_argument("--type",
                        choices=["cpu", "memory", "restart", "network", "diskio"],
                        default="cpu",
                        help="Fault type to inject/restore")

    # cpu options
    parser.add_argument("--cpu-quota", type=int, default=10000,
                        help="CPU quota in microseconds per 100ms (default 10000 = 10%%)")
    # memory options
    parser.add_argument("--mem-limit", default="20m",
                        help="Memory hard limit (default '20m')")
    # network options
    parser.add_argument("--net-delay-ms", type=int, default=200,
                        help="Network delay in ms (default 200)")
    parser.add_argument("--net-loss-pct", type=float, default=10.0,
                        help="Network packet loss %% (default 10.0)")
    # diskio options
    parser.add_argument("--disk-write-bps", type=int, default=1_048_576,
                        help="Disk write throttle in bytes/s (default 1MB/s)")
    parser.add_argument("--disk-read-bps", type=int, default=1_048_576,
                        help="Disk read throttle in bytes/s (default 1MB/s)")

    args = parser.parse_args()
    success = False

    if args.action == "inject":
        if args.type == "cpu":
            success = inject_cpu(args.container, args.cpu_quota)
        elif args.type == "memory":
            success = inject_memory(args.container, args.mem_limit)
        elif args.type == "restart":
            success = inject_restart(args.container)
        elif args.type == "network":
            success = inject_network(args.container, args.net_delay_ms, args.net_loss_pct)
        elif args.type == "diskio":
            success = inject_diskio(args.container, args.disk_write_bps, args.disk_read_bps)
    else:  # restore
        if args.type == "cpu":
            success = restore_cpu(args.container)
        elif args.type == "memory":
            success = restore_memory(args.container)
        elif args.type == "restart":
            success = restore_restart(args.container)
        elif args.type == "network":
            success = restore_network(args.container)
        elif args.type == "diskio":
            success = restore_diskio(args.container)

    if not success:
        log.error("Fault operation failed for %s [%s %s]",
                  args.container, args.action, args.type)
        sys.exit(1)
    else:
        log.info("Done: %s %s on %s", args.action, args.type, args.container)


if __name__ == "__main__":
    main()
