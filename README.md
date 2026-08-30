# CERCA: Context-Aware Empirical Root Cause Analysis

> A Foundations of Data Science project evaluating whether **telemetry reliability features** - extracted from distributed traces and Prometheus metrics - can improve graph-based Root Cause Analysis (RCA) in microservice architectures.

---

## Table of Contents

1. [What is CERCA?](#1-what-is-cerca)
2. [System Architecture](#2-system-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Prerequisites & Installation](#4-prerequisites--installation)
5. [One-Command Reproducibility](#5-one-command-reproducibility)
6. [CERCA in Detail](#6-cerca-in-detail)
7. [Evaluation Results](#7-evaluation-results)
8. [Ablation Study](#8-ablation-study)
9. [Robustness to Telemetry Degradation](#9-robustness-to-telemetry-degradation)
10. [Error Analysis](#10-error-analysis)
11. [Further Reading](#11-further-reading)

---

## 1. What is CERCA?

Modern microservice systems are highly distributed - a single user request can touch dozens of services before returning a response. When something goes wrong, pinpointing the *root cause* service among tens or hundreds of candidates is exceedingly hard. Traditional monitoring tools rely on metric spikes (latency / error rate), but these signals are often noisy, correlated, and **propagate upstream** - masking the true origin of a fault.

**CERCA** addresses this by combining three complementary signals:

| Signal | What it captures |
|---|---|
| **Anomaly Score** | How anomalous is a service's latency and error rate right now? |
| **Reliability Weight** | How trustworthy is the telemetry data we have for this service? |
| **Graph Propagation** | Which upstream callers are likely responsible for a downstream anomaly? |

The core insight is that telemetry is never perfectly sampled in production. Jaeger agents drop spans, Prometheus scrapes fail, and services go temporarily uninstrumented. CERCA's reliability weighting mechanism gracefully discounts services with sparse or missing telemetry, preventing telemetry-dark nodes from being incorrectly blamed.

---

## 2. System Architecture

```
+----------------------------------------------------------+
|             DeathStarBench Social Network                |
|             (30 Microservices, Docker Compose)           |
+--------------------+---------------------+--------------+
                     |                     |
               Jaeger Traces        Prometheus Metrics
                     |                     |
                     v                     v
+----------------------------------------------------------+
|              telemetry_features/                         |
|  +--------------+ +--------------+ +------------------+ |
|  |trace_features| |service_feats | |temporal_features | |
|  |edge_features | |              | |                  | |
|  +--------------+ +--------------+ +------------------+ |
+---------------------------+------------------------------+
                            |
                     Feature Matrix (per service)
                            |
                            v
+----------------------------------------------------------+
|                  scripts/advanced_eda.py                 |
|   Random Forest + Mutual Info  ->  learned_weights.json  |
+---------------------------+------------------------------+
                            |
                            v
+----------------------------------------------------------+
|                    cerca/algorithm.py                    |
|   A(s)  -------->  Anomaly                               |
|   L(s)  -------->  x Reliability  -->  Local Score       |
|   Graph -------->  + Graph Prop   -->  CERCA Score       |
+----------------------------------------------------------+
                            |
                            v
                   Ranked Service List
                   (MRR / Top-K Accuracy)
```

---

## 3. Repository Structure

```
CERCA/
|-- cerca/                       # Core inference algorithm and evaluation
|   |-- algorithm.py             # CERCA class: anomaly, reliability, graph propagation
|   |-- microrca.py              # Faithful MicroRCA baseline implementation
|   |-- evaluate.py              # Full evaluation and ablation study runner
|   |-- visualize.py             # Publication-quality plots
|   |-- learned_weights.json     # RF-importance-derived feature weights (auto-generated)
|   +-- evaluation_metrics.csv   # Results table (auto-generated)
|
|-- telemetry_features/          # Feature extraction pipeline
|   |-- trace_features.py        # Per-trace structural integrity features
|   |-- service_features.py      # Per-service latency, error, and span features
|   |-- temporal_features.py     # Metric coverage, gap counts, clock skew
|   |-- edge_features.py         # Inter-service call edge features
|   |-- extract_all.py           # Orchestrator: runs all extractors
|   +-- _io.py                   # Shared I/O utilities
|
|-- scripts/                     # Data collection and experiment automation
|   |-- run_all_experiments.ps1  # Master orchestration script (PowerShell)
|   |-- generate_workload.py     # Locust-based load generation
|   |-- inject_fault.py          # CPU starvation / memory / container restart injection
|   |-- fetch_traces.py          # Jaeger HTTP API collector
|   |-- fetch_metrics.py         # Prometheus HTTP API collector
|   |-- build_dependency_graph.py # Trace-derived service graph builder
|   |-- build_rca_dataset.py     # Assembles labeled RCA dataset CSV
|   +-- advanced_eda.py          # Feature selection and weight learning
|
|-- data/
|   +-- rca_dataset.csv          # 118 service records across 7 fault experiments
|
|-- experiments/                 # Per-experiment raw data (auto-generated)
|   +-- exp_XX/
|       |-- data/processed/      # spans_*.csv, metrics_*.csv
|       +-- graphs/              # dependency_graph.json
|
|-- docs/
|   |-- feasibility_report.md    # Full experimental report
|   +-- PROJECT_HANDOVER.md      # Detailed implementation notes
|
|-- dsb/                         # DeathStarBench Social Network
|-- docker-compose.yml           # Full stack: app + Jaeger + Prometheus
|-- prometheus.yml               # Prometheus scrape config
+-- requirements.txt             # Python dependencies
```

---

## 4. Prerequisites & Installation

**System Requirements:**
- Docker and Docker Compose
- Python 3.10+
- PowerShell 7+ (for experiment automation on Windows)

**Install Python dependencies:**

```powershell
pip install -r requirements.txt
```

Key packages: `pandas`, `numpy`, `networkx`, `scikit-learn`, `matplotlib`, `seaborn`, `scipy`, `locust`

**Start the microservice stack:**

```powershell
docker-compose up -d
```

This starts the 30-service DeathStarBench Social Network along with Jaeger (`:16686`) and Prometheus (`:9090`).

---

## 5. One-Command Reproducibility

A TA or reviewer can reproduce the entire pipeline by running:

```powershell
# Step 1 - Generate the Dataset
# Runs workload generation + fault injection for all 9 experiments (7 fault + 2 normal)
powershell -ExecutionPolicy Bypass -File scripts\run_all_experiments.ps1

# Step 2 - Feature Selection and Weight Learning
# Trains a Random Forest on the feature matrix, outputs cerca/learned_weights.json
python scripts\advanced_eda.py

# Step 3 - CERCA Evaluation and Ablation Study
# Evaluates all methods (CERCA variants + baselines) with 95% CI bootstrapping
python cerca\evaluate.py

# Step 4 - Generate Publication Plots
# Produces plot_accuracy.png, plot_feature_importance.png, plot_robustness.png
python cerca\visualize.py
```

All outputs are written to `cerca/` and `telemetry_features/outputs/`.

---

## 6. CERCA in Detail

### 6.1 Problem Statement

Given a microservice dependency graph **G = (V, E)** and a feature matrix of telemetry signals collected during a fault window, rank all services **V** such that the true root cause service appears as close to rank 1 as possible.

**Why existing approaches fall short:**

- *Simple heuristics* (latency, error rate) rank by symptom, not cause - downstream victims always appear more anomalous than the actual root cause.
- *Standard PageRank* ignores anomaly evidence entirely and ranks purely by structural centrality.
- *MicroRCA* improves on PageRank by weighting graph walks with Pearson correlations, but its mechanism degrades sharply when timeseries data is missing or incomplete.

### 6.2 Dataset

We deployed the open-source **DeathStarBench Social Network** across 30 microservices on Docker Compose. We injected 7 distinct faults (CPU starvation, memory limits, container restarts) and generated a labeled RCA dataset:

- **9 total experiments** (7 fault injection + 2 normal baselines)
- **118 service state records** across the 7 independent fault incidents
- **Ground truth labels** for the true root cause service per experiment

### 6.3 Feature Engineering

For each microservice, CERCA extracts features across four classes from Jaeger traces and Prometheus metrics:

| Class | Features |
|---|---|
| **Trace-derived** | `service_latency_cv`, `service_std_latency_ms`, `service_p99_latency_ms`, `service_error_rate`, `service_span_count`, `service_trace_participation_rate`, `service_fan_out_mean`, `service_orphan_span_rate` |
| **Metric-derived** | `service_cpu_cv`, `service_mem_stability`, `metric_samples_available` |
| **Temporal** | `metric_coverage_ratio`, `metric_gap_count`, `clock_skew_rate`, `span_density_per_15s_cv` |
| **Edge** | `edge_observation_rate`, `edge_operation_entropy`, `edge_latency_cv`, `edge_p95_p50_ratio` |

Features are extracted by the modules in `telemetry_features/` and assembled into a flat CSV by `extract_all.py`. Trace completeness features (orphan span rate, single-span traces) directly measure telemetry reliability per service.

### 6.4 Feature Selection and Weight Learning

`scripts/advanced_eda.py` uses a **GridSearchCV-tuned Random Forest Classifier** (optimal: `max_depth=3`, `n_estimators=50`) combined with Mutual Information and point-biserial correlation to identify discriminative features. Three independent scoring methods converge on the same top-5 features:

| Rank | Feature | Why it matters |
|---|---|---|
| 1 | `service_p99_latency_ms` | Tail latency captures fault propagation better than mean latency |
| 2 | `service_span_count` | Low span counts indicate telemetry data loss at this service |
| 3 | `service_std_latency_ms` | High variance reveals services under stress |
| 4 | `service_fan_out_mean` | High fan-out services are structural amplifiers of fault signals |
| 5 | `service_trace_participation_rate` | Low rate = service is being skipped or dropped by the tracer |

Learned signed weights are saved to `cerca/learned_weights.json` for use at inference time.

### 6.5 The CERCA Algorithm

CERCA computes a ranking score for each microservice in four steps (see `cerca/algorithm.py`):

**Step 1 - Base Anomaly Score**

Combines normalized error rate and latency coefficient of variation:

```
A(s) = 0.5 x (error_rate(s) / max_error_rate)
     + 0.5 x (latency_cv(s) / max_latency_cv)
```

**Step 2 - Reliability Multiplier (sigmoid-bounded)**

Computes a data-quality multiplier using the learned feature weights. The sigmoid bounds it to (0.5, 1.5), preventing any single service from dominating or being fully suppressed:

```
L(s) = 1.0 + sigmoid( sum(w_f x z_f(s)) ) - 0.5    bounded in (0.5, 1.5)
```

Where `w_f` are RF-importance-derived signed weights and `z_f(s)` are z-scored feature values. Services with zeroed or missing telemetry receive L ~= 0.5, suppressing their anomaly contribution.

**Step 3 - Local Score**

```
Local(s) = A(s) x L(s)
```

**Step 4 - Graph Propagation (1-hop, dampened)**

Blame flows backward along the dependency graph - from a downstream anomalous service to its upstream callers:

```
CERCA(s) = Local(s) + 0.5 x sum( Local(c) )   for c in callers(s)
```

The 0.5 dampening factor limits propagation to exactly **one hop**, avoiding the sink-trap of iterative PageRank where structurally central nodes absorb all blame regardless of anomaly evidence.

### 6.6 Algorithm Variants

The `CERCA.rank_services()` method accepts a `variant` parameter for ablation:

| Variant | Description |
|---|---|
| `full` | Anomaly x Reliability x Graph propagation (standard CERCA) |
| `no_graph` | Anomaly x Reliability, no graph context |
| `no_reliability` | Anomaly x Graph, reliability weight fixed to 1.0 |
| `equal_weights` | Anomaly x Uniform Reliability x Graph, all feature weights set to 1.0 |

### 6.7 Inference Complexity

CERCA's 1-hop propagation is a **single graph traversal - O(V+E)** - with no iterative convergence. This contrasts with MicroRCA's personalized PageRank which requires O(k(V+E)) iterations.

| Graph Size | Runtime |
|---|---|
| 10 nodes, 30 edges | **5.62 ms** |
| 100 nodes, 300 edges | **9.14 ms** |
| 1,000 nodes, 3,000 edges | **51.87 ms** |
| 10,000 nodes, 30,000 edges | **534.94 ms** |

Benchmarked synthetically on sparse directed graphs (E = 3V), averaged over 10 runs per size with a warmup pass (see `docs/complexity_benchmark.json`). For a 1,000-service system, CERCA inference completes in **under 100 ms** - suitable for real-time use during production outages.

---

## 7. Evaluation Results

CERCA was evaluated against industry-standard heuristics and graph-based baselines over **7 independent fault injection incidents** using **1,000-iteration bootstrapping** for 95% Confidence Intervals. The True MicroRCA baseline is a faithful implementation of the original 2020 IEEE algorithm.

| Method | MRR (95% CI) | Top-1 Accuracy | Top-3 Accuracy |
|---|---|---|---|
| **CERCA (Full)** | **0.3026** (0.14-0.56) | **0.1429** | **0.2857** |
| Baseline (True MicroRCA) | 0.2921 (0.13-0.55) | 0.1429 | 0.2857 |
| Baseline (PageRank) | 0.1863 (0.13-0.24) | 0.0000 | 0.1429 |
| Baseline (Error Rate) | 0.1837 (0.13-0.24) | 0.0000 | 0.1429 |
| Baseline (Latency) | 0.1497 (0.11-0.19) | 0.0000 | 0.0000 |

CERCA achieves the highest MRR (**+3.6% over MicroRCA**) while matching MicroRCA on Top-1 and Top-3. CIs overlap due to small dataset size (N=7), making the advantage directional. CERCA's advantage over heuristic baselines is substantial and consistent.

---

## 8. Ablation Study

| Variant | MRR | Top-1 | Top-3 |
|---|---|---|---|
| **CERCA (Full)** | 0.3026 | 0.1429 | **0.2857** |
| CERCA (Equal Weights) | 0.3087 | 0.1429 | **0.2857** |
| CERCA (No Reliability) | 0.3026 | 0.1429 | **0.2857** |
| CERCA (No Graph) | 0.2861 | 0.1429 | 0.1429 |

**Key Findings:**

- **Removing graph propagation** halves Top-3 accuracy (28.57% -> 14.29%). Without graph context, local anomaly scores cannot distinguish root causes from their high-anomaly callers.
- **Removing reliability weights** produces identical MRR (0.3026) under clean telemetry. Their value is only revealed by the degraded-telemetry robustness experiment.
- **Equal weights** (0.3087 MRR) slightly outperform learned weights, indicating the directional sign of feature contributions matters more than the specific magnitudes at this dataset size.

---

## 9. Robustness to Telemetry Degradation

CERCA's primary contribution is graceful degradation when telemetry is sparse or missing. We simulated this by randomly zeroing feature values for a fraction of services (seed=42 for reproducibility).

| Sampling Rate | CERCA MRR | MicroRCA MRR | Delta |
|---|---|---|---|
| **100% Sampling** | **0.3026** | 0.2921 | +0.0105 |
| **20% Sampling** | **0.1967** | 0.1516 | **+0.0451 (+29.7%)** |
| **5% Sampling** | 0.1565 | 0.2637* | -0.1072 |

*\*The MicroRCA spike at 5% sampling is a dataset artifact: with 95% of timeseries zeroed, MicroRCA's anomaly detector collapses onto 1-2 remaining services that happen to include the root cause. Not a genuine robustness property.*

At 20% sampling, MicroRCA's MRR (0.1516) drops **below the simple error-rate heuristic** (0.1837). CERCA's reliability weighting suppresses scores for telemetry-dark services, maintaining MRR at 0.1967 - a **+29.7% advantage** in the realistic scenario of incomplete telemetry.

---

## 10. Error Analysis

All incorrect Top-1 predictions share a single failure mode: **downstream error masking via `url-shorten-service`**.

When a root cause service fails (e.g., `social-graph-service` under CPU starvation), immediate callers accumulate extreme latency spikes. The `url-shorten-service`, called on many request paths, consistently exhibits the highest combined anomaly score and absorbs the Rank 1 prediction.

**Root cause of the failure mode:** CERCA propagates blame one hop backward but does not prune edges where inter-service correlation has dropped below a threshold. A structurally central but causally uninvolved service can still aggregate blame from multiple upstream paths.

**Future Work:** Dynamic graph pruning - severing edges where inter-service correlation drops below a threshold before propagation - to prevent false blame transfer through central but uninvolved services.

Full per-experiment error logs: `cerca/error_analysis.txt`

---

## 11. Further Reading

| Document | Description |
|---|---|
| `docs/feasibility_report.md` | Full experimental report: methodology, results, ablation, error analysis |
| `docs/PROJECT_HANDOVER.md` | Detailed implementation notes and design decisions |
| `cerca/algorithm.py` | CERCA inference algorithm source |
| `cerca/evaluate.py` | Evaluation and ablation study runner |
| `cerca/learned_weights.json` | Auto-generated RF feature weights |
| `cerca/evaluation_metrics.csv` | Auto-generated results table |
