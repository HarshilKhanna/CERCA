"""
init_social_graph.py
────────────────────
Register a small set of test users in the DeathStarBench Social Network and
establish a follow graph between them. This must be run ONCE after
`docker compose up` before generating workload.

The Social Network's nginx-thrift gateway exposes these HTTP endpoints:
  POST /wrk2-api/user/register
  POST /wrk2-api/user/follow

Usage
─────
    python scripts/init_social_graph.py [--url http://localhost:8080] [--users 50]
"""

import argparse
import logging
import random
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("init_social_graph")

DEFAULT_URL   = "http://localhost:8080"
DEFAULT_USERS = 50


def wait_for_nginx(base_url: str, retries: int = 30, delay: float = 3.0):
    """Block until nginx-thrift responds on port 8080."""
    log.info("Waiting for nginx-thrift at %s …", base_url)
    for i in range(retries):
        try:
            r = requests.get(f"{base_url}/", timeout=3)
            # Any response (even 404) means nginx is alive
            log.info("nginx-thrift is up (HTTP %s)", r.status_code)
            return
        except requests.exceptions.RequestException:
            log.info("Attempt %d/%d — not ready yet, retrying in %.0fs …", i + 1, retries, delay)
            time.sleep(delay)
    log.error("nginx-thrift did not respond after %d attempts", retries)
    sys.exit(1)


def register_user(base_url: str, user_id: int) -> bool:
    """Register a single user. Returns True on success."""
    username   = f"username_{user_id}"
    first_name = f"First{user_id}"
    last_name  = f"Last{user_id}"
    password   = f"password_{user_id}"

    payload = {
        "username":   username,
        "first_name": first_name,
        "last_name":  last_name,
        "password":   password,
        "user_id":    user_id,
    }
    try:
        r = requests.post(f"{base_url}/wrk2-api/user/register", data=payload, timeout=10)
        if r.status_code in (200, 201):
            return True
        # 400 may mean already registered — treat as OK
        if r.status_code == 400 and "already" in r.text.lower():
            return True
        log.warning("Register user %d: HTTP %s — %s", user_id, r.status_code, r.text[:100])
        return False
    except Exception as e:
        log.warning("Register user %d failed: %s", user_id, e)
        return False


def follow_user(base_url: str, follower_id: int, followee_id: int) -> bool:
    """Make follower_id follow followee_id."""
    payload = {
        "user_id":     follower_id,
        "followee_id": followee_id,
    }
    try:
        r = requests.post(f"{base_url}/wrk2-api/user/follow", data=payload, timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("Follow %d→%d failed: %s", follower_id, followee_id, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Initialise DSB Social Network social graph")
    parser.add_argument("--url",   default=DEFAULT_URL,   help="nginx-thrift base URL")
    parser.add_argument("--users", type=int, default=DEFAULT_USERS,
                        help="Number of users to register (default: 50)")
    args = parser.parse_args()

    wait_for_nginx(args.url)

    # ── Register users ────────────────────────────────────────────────────────
    log.info("Registering %d users …", args.users)
    ok = err = 0
    for uid in range(1, args.users + 1):
        if register_user(args.url, uid):
            ok += 1
        else:
            err += 1
        time.sleep(0.05)  # gentle rate limiting

    log.info("Registration: %d OK, %d errors", ok, err)
    if ok == 0:
        log.error("No users registered. Check nginx-thrift is running and endpoints are correct.")
        sys.exit(1)

    # ── Create follow edges (random graph, ~5 follows per user) ───────────────
    log.info("Creating follow relationships (random graph) …")
    follow_ok = follow_err = 0
    for follower in range(1, args.users + 1):
        # Each user follows ~5 random others
        followees = random.sample(
            [u for u in range(1, args.users + 1) if u != follower],
            k=min(5, args.users - 1),
        )
        for followee in followees:
            if follow_user(args.url, follower, followee):
                follow_ok += 1
            else:
                follow_err += 1
        time.sleep(0.02)

    log.info("Follow edges: %d created, %d errors", follow_ok, follow_err)
    log.info("✓ Social graph initialised. Ready for workload generation.")


if __name__ == "__main__":
    main()
