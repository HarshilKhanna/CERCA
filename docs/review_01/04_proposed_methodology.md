# 4. Proposed Methodology: CERCA

To address the identified research gaps, this project proposes **CERCA (Context-Aware Empirical Root Cause Analysis)**. CERCA is a novel framework that does not merely pipeline existing tools; it introduces a new mathematical formulation to quantify telemetry reliability and a linear-time bounded graph propagation mechanism to avoid structural biases.

## 4.1 System Architecture & Multimodal Feature Engineering
CERCA processes multimodal telemetry, merging high-resolution Jaeger spans with 15-second Prometheus metrics. Instead of relying solely on raw timeseries correlation, CERCA engineers over 20 continuous statistical features per service across four classes:

1. **Trace-Derived Anomaly Features:** 
   * `service_p99_latency_ms`: Tail latency, a prime indicator of propagated faults.
   * `service_latency_cv`: Coefficient of variation ($\sigma/\mu$), highlighting localized instability.
   * `service_error_rate`: Proportion of spans returning HTTP 5xx codes.
2. **Metric-Derived Features:** 
   * `service_cpu_cv`: Volatility in CPU usage, indicating resource starvation.
3. **Telemetry Integrity & Temporal Features:** 
   * `metric_samples_available`: Count of successful Prometheus scrapes in the window.
   * `metric_coverage_ratio`: Temporal completeness of data.
4. **Graph Edge Features:** 
   * `service_fan_out_mean`: Structural propagation potential.
   * `service_orphan_span_rate`: Ratio of spans missing parent references due to sampling loss.

Crucially, the features in class 3 and 4 are explicitly designed to quantify *telemetry completeness and integrity*, serving as direct inputs to the reliability weighting mechanism.

## 4.2 Feature Selection and Empirical Weight Learning
To determine the diagnostic power of each feature, CERCA employs a robust machine learning ensemble rather than relying on arbitrary heuristics. The ensemble consists of:
1. **Point-Biserial Correlation (Linear):** Measures the linear association between continuous features and the binary root-cause label.
2. **Mutual Information (Non-linear):** Captures non-linear dependencies via entropy reduction.
3. **GridSearchCV-tuned Random Forest Classifier:** Captures multivariate interactions, utilizing a strictly regularized tree ($max\_depth=3$) to prevent overfitting on small anomaly datasets.

The empirical weight $w_f$ for each feature $f$ is learned by combining its normalized Random Forest importance with the sign of its linear correlation:
$$w_f = 	ext{RF\_Importance}_f \times 	ext{sign}(	ext{Correlation}_f)$$
This formulation is critical: it ensures the algorithm learns not just which features are important, but the *direction* of their impact. For example, high tail latency is a negative indicator (victim), whereas a high span count is a positive indicator (reliable data).

## 4.3 The CERCA Inference Algorithm
The core algorithmic novelty of CERCA lies in its four-step inference process, designed to replace traditional iterative random walks.

### Step 1: Base Anomaly Score $A(s)$
Quantifies the normalized severity of symptoms for a given service $s$:
$$A(s) = 0.5 \times \frac{	ext{error\_rate}(s)}{\max(	ext{error\_rate})} + 0.5 \times \frac{	ext{latency\_cv}(s)}{\max(	ext{latency\_cv})}$$
This score lies strictly in $[0,1]$.

### Step 2: Novel Reliability Multiplier $\lambda(s)$
This is CERCA's primary conceptual innovation. It computes a data-quality multiplier bounded by a sigmoid transfer function, mapping the unbounded feature space into a strict modulation range:
$$\lambda(s) = 1.0 + \left( \sigma\left( \sum w_f \cdot z_f(s) ight) - 0.5 ight) \in (0.5, 1.5)$$
Where $z_f(s)$ is the z-scored feature value for feature $f$, and $\sigma$ is the standard logistic function. 
* **High Reliability:** If a service has rich, complete telemetry, $\lambda(s) pprox 1.5$, amplifying its anomaly score as trusted evidence.
* **Low Reliability:** If a service has dropped/sparse telemetry, $\lambda(s) pprox 0.5$, suppressing its anomaly score by 50% to prevent false positives caused by sampling artifacts.

### Step 3: Local Score $L(s)$
$$L(s) = A(s) \times \lambda(s)$$
This integrates symptomatic evidence with evidential reliability into a single metric.

### Step 4: 1-Hop Dampened Graph Propagation
To overcome the PageRank sink-trap and computational inefficiency, blame flows backward exactly one hop along the dependency graph with a uniform dampening factor:
$$CERCA(s) = L(s) + 0.5 \times \sum_{c \in 	ext{callers}(s)} L(c)$$
* **Why 1-Hop?** It prevents the transitive absorption of blame by central hubs.
* **Why 0.5 Dampening?** It treats upstream symptoms as corroborating evidence rather than absolute proof.
* **Computational Efficiency:** This operates in strictly $O(V+E)$ time, requiring only a single traversal of the graph edges, making it exceptionally fast for real-time inference.

## 4.4 Evaluation Methodology and Metrics
The proposed methodology will be evaluated against industry heuristics (Rank by Latency, Rank by Error Rate) and the state-of-the-art graph baseline (MicroRCA). 

**Performance Metrics:**
* **Mean Reciprocal Rank (MRR):** Evaluates the average rank of the true root cause.
* **Top-K Accuracy (Top-1, Top-3):** Measures the probability that the true root cause is ranked in the top K positions.
* **Statistical Validation:** All results will be bounded by 1,000-iteration bootstrapping to generate 95% Confidence Intervals.

**Robustness Validation:**
To explicitly prove the efficacy of the reliability multiplier, robustness will be validated by artificially degrading telemetry sampling rates (e.g., zeroing features for 80% and 95% of services) to simulate aggressive production sampling, demonstrating CERCA's superior degradation curve compared to correlation-based baselines.
