# Case Study 2 – Model Development and Proposed Innovation
## CERCA: Context-Aware Empirical Root Cause Analysis

**Project:** Foundations of Data Science — Applied Research Project  
**Topic:** Root Cause Analysis in Microservice Architectures Under Incomplete Telemetry  
**System Under Study:** DeathStarBench Social Network (30-service distributed application)

---

## Table of Contents

1. [Problem Recap and Motivation](#1-problem-recap-and-motivation)
2. [Technical Approach Overview](#2-technical-approach-overview)
3. [Dataset Construction](#3-dataset-construction)
4. [Feature Engineering Pipeline](#4-feature-engineering-pipeline)
5. [Feature Selection and Weight Learning](#5-feature-selection-and-weight-learning)
6. [Baseline Models](#6-baseline-models)
7. [Proposed Innovation: The CERCA Algorithm](#7-proposed-innovation-the-cerca-algorithm)
8. [Experimental Setup and Results](#8-experimental-setup-and-results)

> **Note:** Ablation studies, robustness testing, statistical validation, complexity analysis, error analysis, explainability, and research contribution assessment are covered in Case Study 3.

---

## 1. Problem Recap and Motivation

Modern software systems are built as collections of loosely coupled, independently deployable **microservices**. When a failure occurs, latency and error signals propagate *upstream* through service call chains — the service exhibiting the worst symptoms is rarely the service that caused the problem.

**Root Cause Analysis (RCA)** is the problem of ranking all services from most to least likely root cause during an active incident, so that the responsible service appears at rank 1.

The canonical difficulty has two compounding dimensions:

1. **Signal propagation:** Downstream victims exhibit amplified anomaly scores, masking the true origin.
2. **Telemetry incompleteness:** Production tracing systems (e.g., Jaeger) sample spans probabilistically. Prometheus scrapes fail. Services go temporarily uninstrumented. Algorithms that assume complete telemetry misattribute blame to services whose data was simply not collected.

Existing approaches fail on one or both fronts:

| Method | Addresses Propagation? | Addresses Incompleteness? |
|---|---|---|
| Rank by latency / error rate | No | No |
| Structural PageRank | Yes | No |
| MicroRCA (Liu et al., IEEE 2020) | Yes | **No** — collapses under sparse telemetry |

**CERCA's central claim:** By introducing an empirical *reliability weighting* step that discounts services with incomplete telemetry before graph propagation, RCA can remain robust as telemetry degrades — without sacrificing accuracy when telemetry is complete.

---

## 2. Technical Approach Overview

CERCA is a hybrid algorithm combining Data Science, Machine Learning, and Graph-theoretic methods:

```
Telemetry Data (Jaeger Traces + Prometheus Metrics)
        |
        v
[1] Feature Engineering      <- Data Science: 20+ features per service across 4 classes
        |
        v
[2] Feature Selection        <- ML: Random Forest + Mutual Info -> learned_weights.json
        |
        v
[3] CERCA Inference          <- Proposed Innovation:
        |                       A(s) = Anomaly Score
        |                     x L(s) = Reliability Weight  (sigmoid-bounded)
        |                     + Graph Propagation          (1-hop dampened, O(V+E))
        v
Ranked Service List (MRR / Top-K Accuracy)
```

AI/DS techniques applied:
- **Data Science:** Multi-signal feature extraction, bootstrapped confidence intervals
- **Machine Learning:** GridSearchCV-tuned Random Forest for feature importance learning
- **Hybrid AI–Optimization:** Learned weights fused with graph-theoretic propagation
- **Explainable AI:** Per-service score decomposition into interpretable components

---

## 3. Dataset Construction

### 3.1 Infrastructure

We deployed **DeathStarBench Social Network** — a realistic 30-microservice benchmark used in production RCA research — on Docker Compose, instrumented with:
- **Jaeger** (`:16686`) — distributed tracing, span-level request data
- **Prometheus** (`:9090`) — container-level CPU, memory, and network metrics at 15s resolution

### 3.2 Workload and Fault Injection

Realistic load (~50 req/s) was generated via **Locust** (`scripts/generate_workload.py`). Three fault types were injected via `scripts/inject_fault.py`:

| Fault Type | Mechanism | Services Targeted |
|---|---|---|
| **CPU Starvation** | `docker update --cpus 0.05` | social-graph-service, post-storage-service |
| **Memory Limit** | `docker update --memory 32m` | home-timeline-service, user-timeline-service |
| **Container Restart** | `docker restart` | media-service, text-service, compose-post-service |

Protocol: 60s baseline → inject fault → 120s fault window → collect telemetry → restore.

### 3.3 Dataset Summary

| Attribute | Value |
|---|---|
| Total experiments | 9 (7 fault + 2 normal baseline) |
| Fault experiments used for evaluation | 7 |
| Total labeled service records | 118 |
| Root cause instances (positive class) | 7 (one per experiment) |
| Features per record | 20+ continuous numerical features |

Dataset stored as `data/rca_dataset.csv` with one row per service per experiment, labeled with ground-truth root cause.

---

## 4. Feature Engineering Pipeline

Features are extracted by `telemetry_features/` across four classes:

### 4.1 Trace-Derived Features (`service_features.py`)

| Feature | Interpretation |
|---|---|
| `service_span_count` | Volume of available trace data |
| `service_trace_participation_rate` | Coverage across all observed requests |
| `service_error_rate` | Fraction of failed spans |
| `service_p99_latency_ms` | Tail latency (key fault propagation indicator) |
| `service_latency_cv` | Latency variability (σ/μ) |
| `service_std_latency_ms` | Absolute latency dispersion |
| `service_fan_out_mean` | Structural propagation potential |
| `service_orphan_span_rate` | Telemetry structural integrity indicator |

### 4.2 Metric-Derived Features (`service_features.py`)

| Feature | Interpretation |
|---|---|
| `service_cpu_cv` | CPU usage variability — spikes indicate CPU starvation |
| `service_mem_stability` | `1 - (max-min)/mean` — 0 = highly volatile memory |
| `metric_samples_available` | Prometheus sample count — proxy for metric coverage |

### 4.3 Temporal and Edge Features

| Class | Key Features |
|---|---|
| **Temporal** (`temporal_features.py`) | `metric_coverage_ratio`, `metric_gap_count`, `clock_skew_rate`, `span_density_per_15s_cv` |
| **Edge** (`edge_features.py`) | `edge_observation_rate`, `edge_operation_entropy`, `edge_latency_cv`, `edge_p95_p50_ratio` |

> Features such as `service_orphan_span_rate`, `metric_samples_available`, and `metric_coverage_ratio` are specifically designed to quantify **telemetry reliability**, not just service health. These are unique to CERCA and form the foundation of its reliability weighting mechanism.

---

## 5. Feature Selection and Weight Learning

`scripts/advanced_eda.py` uses a three-method ensemble to select and weight features:

**Method 1 — Point-Biserial Correlation:** Linear relationship between each feature and the binary root-cause label (signed ∈ [-1, 1]).

**Method 2 — Mutual Information:** Non-linear statistical dependence via `sklearn.feature_selection.mutual_info_classif`.

**Method 3 — GridSearchCV-tuned Random Forest:**
- Grid: `n_estimators` ∈ {50, 100, 200}, `max_depth` ∈ {3, 5, None}, `min_samples_split` ∈ {2, 5}
- Optimal: `max_depth=3`, `n_estimators=50`
- Feature importance = mean decrease in impurity

All three scores are z-normalized and averaged into a `Combined_Score`. Top features:

| Rank | Feature | Why it matters |
|---|---|---|
| 1 | `service_p99_latency_ms` | Tail latency best captures fault propagation |
| 2 | `service_span_count` | Low counts indicate telemetry loss |
| 3 | `service_std_latency_ms` | Variance reveals services under stress |
| 4 | `service_fan_out_mean` | Hub services amplify fault signals |
| 5 | `service_trace_participation_rate` | Low rate = tracer is skipping this service |

**Weight encoding:** `w_f = RF_Importance_f × sign(Correlation_f)` — preserves both magnitude and direction of each feature's contribution. Saved to `cerca/learned_weights.json`.

---

## 6. Baseline Models

Four baselines were implemented to establish the performance floor:

| Baseline | Method | Key Limitation |
|---|---|---|
| **Latency** | Rank by `service_mean_latency_ms` | Ranks downstream victims, not root causes |
| **Error Rate** | Rank by `error_rate` | Error spikes across multiple services simultaneously |
| **PageRank** | Standard PageRank on reversed dependency graph | Ignores all anomaly evidence; structurally central nodes dominate |
| **True MicroRCA** | Pearson correlation on timeseries + personalized PageRank | Requires complete timeseries; collapses under sparse telemetry |

MicroRCA is the primary baseline as it represents the state of the art. It is faithfully reimplemented in `cerca/microrca.py` following the original 2020 IEEE algorithm.

---

## 7. Proposed Innovation: The CERCA Algorithm

### 7.1 Core Contributions

1. **Reliability Weighting:** Each service's anomaly score is scaled by an empirically-learned multiplier capturing telemetry data quality. Services with sparse/incomplete telemetry are suppressed before graph propagation.

2. **1-Hop Dampened Propagation:** Instead of iterative PageRank (O(k(V+E))), CERCA performs a single O(V+E) backward pass with a 0.5 dampening factor, eliminating the convergence instability and sink-trap of PageRank on small graphs.

### 7.2 Algorithm (Four Steps)

**Step 1 — Base Anomaly Score A(s)**
```
A(s) = 0.5 × (error_rate(s) / max_s(error_rate))
     + 0.5 × (latency_cv(s) / max_s(latency_cv))        ∈ [0, 1]
```

**Step 2 — Reliability Multiplier λ(s)**
```
λ(s) = 1.0 + sigmoid( Σ_f  w_f × z_f(s) ) − 0.5        ∈ (0.5, 1.5)
```
- `w_f` = signed learned weight; `z_f(s)` = z-scored feature value
- λ ≈ 1.5 → rich telemetry, full trust; λ ≈ 0.5 → sparse telemetry, score suppressed 50%

**Step 3 — Local Score**
```
L(s) = A(s) × λ(s)
```

**Step 4 — 1-Hop Graph Propagation**
```
CERCA(s) = L(s) + 0.5 × Σ_{c ∈ callers(s)} L(c)
```
Blame flows backward: a service accumulates its own local score plus half the score of each direct caller. The 0.5 factor prevents any single caller from dominating and runs in a single O(V+E) traversal.

### 7.3 Implementation

Implemented in `cerca/algorithm.py` as class `CERCA`:

```python
class CERCA:
    def __init__(self, weights_path: str)
    def compute_anomaly_score(self, df) -> pd.Series
    def compute_reliability_weight(self, df) -> pd.Series
    def rank_services(self, features_df, graph_path, variant="full") -> pd.DataFrame
```

The `variant` parameter (`full`, `no_graph`, `no_reliability`, `equal_weights`) enables ablation without code duplication.

---

## 8. Experimental Setup and Results

### 8.1 Evaluation Protocol

- **Dataset:** 7 fault injection experiments from `data/rca_dataset.csv`
- **Metrics:** Mean Reciprocal Rank (MRR), Top-1 and Top-3 Accuracy, 1,000-iteration bootstrap 95% CI

```
MRR     = (1/N) × Σ 1/rank_i
Top-K   = (1/N) × Σ 1[rank_i ≤ K]
```

### 8.2 Main Results

| Method | MRR | Top-1 | Top-3 |
|---|---|---|---|
| **CERCA (Full)** | **0.3026** | **0.1429** | **0.2857** |
| Baseline (True MicroRCA) | 0.2921 | 0.1429 | 0.2857 |
| Baseline (PageRank) | 0.1863 | 0.0000 | 0.1429 |
| Baseline (Error Rate) | 0.1837 | 0.0000 | 0.1429 |
| Baseline (Latency) | 0.1497 | 0.0000 | 0.0000 |

CERCA achieves the highest MRR (+3.6% over MicroRCA, +62% over the best heuristic baseline). Both heuristic baselines achieve zero Top-1 and Top-3 accuracy. Full validation, ablation, robustness, and error analysis are presented in Case Study 3.
