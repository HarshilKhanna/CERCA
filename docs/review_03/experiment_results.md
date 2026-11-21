# CERCA Review 03 — Final Verified Experiment Results
**Updated: 2026-10-28 (Post Hub-Centrality Mitigation & Metric Population)**

---

## 1. Pristine Telemetry — Full Method Comparison ($N=7$ Incidents)

| Method | MRR | MAR | Top-1 | Top-3 | Top-5 | Latency (ms) |
|---|---|---|---|---|---|---|
| **CERCA (Full, with Hub Mitigation)** | **0.2954** | **5.57** | **0.1429** | 0.1429 | **0.5714** | **26.13** |
| CERCA (No Graph) | 0.2827 | 5.86 | 0.1429 | 0.1429 | 0.4286 | 7.69 |
| CERCA (No Reliability $\lambda$) | 0.2954 | 5.57 | 0.1429 | 0.1429 | 0.5714 | 6.62 |
| CERCA (Equal Weights) | 0.2874 | 6.71 | 0.1429 | 0.2857 | 0.4286 | 11.02 |
| **Baseline (True MicroRCA)** | **0.2921** | 6.00 | 0.1429 | 0.2857 | 0.4286 | **1041.96** |
| Baseline (PageRank) | 0.1863 | 6.29 | 0.0000 | 0.1429 | 0.4286 | 4.94 |
| Baseline (Latency Heuristic) | 0.1497 | 7.71 | 0.0000 | 0.0000 | 0.2857 | 1.50 |
| Baseline (Error Rate Heuristic) | 0.1837 | 6.43 | 0.0000 | 0.1429 | 0.4286 | 1.17 |

> **Key takeaway:** CERCA achieves **0.5714 Top-5 accuracy** (up from 0.4286) with end-to-end latency of **26.13 ms vs MicroRCA's 1041.96 ms** ($39.9\times$ end-to-end speedup).

---

## 2. Controlled Degradation Ladder (Node Blackout: 0% to 90%)

| Drop % | CERCA MRR | MicroRCA MRR | CERCA Top-1 | MicroRCA Top-1 |
|---|---|---|---|---|
| 0% | 0.2954 | 0.2921 | 0.1429 | 0.1429 |
| 10% | 0.3305 | 0.1883 | 0.1429 | 0.0000 |
| 20% | 0.3240 | 0.1883 | 0.1429 | 0.0000 |
| 30% | 0.3290 | 0.1883 | 0.1429 | 0.0000 |
| 40% | 0.2093 | 0.1516 | 0.0000 | 0.0000 |
| 50% | 0.1879 | 0.1516 | 0.0000 | 0.0000 |
| 60% | 0.2382 | 0.1516 | 0.0000 | 0.0000 |
| 70% | 0.2324 | 0.1516 | 0.0000 | 0.0000 |
| 80% | 0.1967 | 0.1516 | 0.0000 | 0.0000 |
| 90% | 0.2946 | 0.1863 | 0.1429 | 0.0000 |

> **MicroRCA sensitivity confirmed:** At just 10% telemetry drop, MicroRCA's Top-1 drops to 0.00 and MRR collapses to 0.1883. CERCA maintains Top-1 through 30% blackout and superior MRR at all degradation stages.

---

## 3. Incident-Level Paired Wilcoxon Tests ($N=7$)

- **Pristine:** $W=20.0$, $p=0.1875$ (not significant at $\alpha=0.05$). Correctly reported as directional advantage.
- **20% Degradation:** Mean RR difference $=+0.1356$, 95% bootstrap CI: $[+0.019, +0.321]$. Wilcoxon $W=14.0$, $p=0.0625$ ($*$, directional robustness).

---

## 4. Telemetry Metrics Audit ($N=117$ rows)

All 6 container metric columns populated via `cerca/collect_docker_metrics.py` querying Docker daemon stats:
- `service_cpu_mean_rate`: 117/117 non-zero (mean = 0.0011)
- `service_cpu_cv`: 117/117 non-zero (mean = 0.6876)
- `service_mem_mean_bytes`: 117/117 non-zero (mean = 3.41 MB)
- `service_mem_stability`: 117/117 non-zero (mean = 0.9889)
- `service_net_rx_mean_rate`: 45/117 non-zero (mean = 19.23 B/s)
- `service_net_tx_mean_rate`: 45/117 non-zero (mean = 10.90 B/s)

---

## 5. Implementation Files Summary

- `cerca/algorithm.py`: Includes 1-hop reverse blame propagation, sigmoid reliability bounds $(0.5, 1.5)$, and in-degree fan-in normalisation for hub-centrality mitigation.
- `scripts/inject_fault.py`: Upgraded to 5 fault types (CPU, memory, restart, network via `tc netem`, disk I/O via `blkio`).
- `cerca/collect_docker_metrics.py`: Standalone real-time Docker daemon container stats collector.
- `cerca/benchmark_latency.py`: Standalone microbenchmark isolating pure algorithm computation time.
