# 2. Formalization of Contextual Evidence Under Degraded Telemetry

## 2.1 The Silent-Node Problem in Microservice Architectures

A chronic vulnerability of microservice root cause analysis (RCA) is the **"Silent-Node" phenomenon**. In production environments, when a microservice $s \in V$ experiences severe failures (e.g., deadlock, thread starvation, kernel out-of-memory killing, or network partition), the monitoring sidecars and telemetry agents (e.g., Prometheus node exporters, Jaeger tracing agents) frequently fail alongside the container:
1. **Agent Starvation:** Telemetry exporters running in the same cgroup as the target service are CPU-throttled or OOM-killed, terminating Prometheus scraping loops.
2. **Aggressive Sampling Drops:** Under high network congestion or cluster strain, distributed tracing collectors dynamically enforce adaptive head-sampling (discarding up to $95\%$ of trace spans).
3. **Log Suppression:** Rate-limiters in logging forwarders (e.g., Fluentd, Vector) drop error log lines to prevent disk exhaustion.

As a consequence, when node $s$ is the true root cause, its internal observable telemetry vanishes:
$$\lim_{\text{loss} \to 100\%} \mathcal{M}_{internal}(s) = \emptyset, \quad \mathcal{T}_{internal}(s) = \emptyset$$

Standard RCA baselines (such as threshold-based detectors or MicroRCA) rely on calculating Pearson cross-correlations between internal container metrics and response time anomalies. When internal telemetry is absent, these methods assign near-zero anomaly scores to the root cause, resulting in total localization failure.

---

## 2.2 Formal Mathematical Framework of CERCA

CERCA overcomes this vulnerability by formalizing microservice diagnostic signals into a dual-component vector space consisting of **Direct Symptomatic Evidence** and **Upstream Contextual Surrogate Evidence**.

### 2.2.1 Definitions and System Graph Model
Let the microservice system be represented as a directed attributed dependency graph:
$$G = (V, E, \mathbf{X})$$
- $V = \{s_1, s_2, \dots, s_n\}$ is the set of $n$ microservices.
- $E \subseteq V \times V$ is the set of directed communication edges, where $(c, s) \in E$ denotes that caller service $c$ synchronously or asynchronously invokes callee service $s$.
- $\mathcal{P}(s) = \{c \in V \mid (c, s) \in E\}$ denotes the immediate predecessor set (direct callers) of service $s$.
- $\mathcal{C}(c) = \{s \in V \mid (c, s) \in E\}$ denotes the immediate successor set (direct callees) of service $c$.
- $\mathbf{X} \in \mathbb{R}^{n \times d}$ is the multimodal feature matrix computed over observation window $W$.

### 2.2.2 Multimodal Telemetry Reliability Multiplier $\lambda(s)$
Rather than assuming all telemetry is pristine, CERCA computes an empirical data-quality multiplier $\lambda(s) \in (0.5, 1.5)$ for every service $s$:
$$\lambda(s) = 1.0 + \left( \sigma\left( \sum_{f=1}^d w_f \cdot z_f(s) \right) - 0.5 \right)$$
Where:
- $z_f(s) = \frac{x_{s, f} - \mu_f}{\sigma_f + \epsilon}$ is the standardized value of feature $f$ for service $s$.
- $w_f = \text{Importance}_{RF}(f) \times \text{sign}(\text{Corr}_{pb}(f))$ is the empirical diagnostic weight learned via Random Forest and Point-Biserial correlation.
- $\sigma(z) = \frac{1}{1 + e^{-z}}$ is the logistic sigmoid function, ensuring that $\lambda(s)$ smoothly scales between $0.5$ (severe degradation/sampling loss) and $1.5$ (complete, corroborated telemetry).

### 2.2.3 Direct Symptomatic Evidence $L_{direct}(s)$
Direct evidence captures the internal symptom severity of service $s$:
$$L_{direct}(s) = A(s) \cdot \lambda(s)$$
Where base anomaly score $A(s) \in [0, 1]$ is formulated from normalized tail instability:
$$A(s) = \frac{1}{2} \left[ \frac{\text{error\_rate}(s)}{\max_{u \in V} \text{error\_rate}(u) + \epsilon} + \frac{\text{latency\_cv}(s)}{\max_{u \in V} \text{latency\_cv}(u) + \epsilon} \right]$$

### 2.2.4 Upstream Contextual Surrogate Evidence $\Gamma(s)$
When callee $s$ degrades, it induces backpressure, synchronous blocking I/O, socket buffer exhaustion, and RPC timeouts in its upstream callers $c \in \mathcal{P}(s)$. Crucially, **callers maintain their own independent telemetry streams**.

We define the upstream contextual surrogate evidence $\Gamma(s)$ as:
$$\Gamma(s) = \sum_{c \in \mathcal{P}(s)} \omega(c, s) \cdot L_{direct}(c)$$

Where $\omega(c, s) \in [0, 1]$ is the directed caller-callee coupling coefficient:
$$\omega(c, s) = \frac{\text{TraceInvocations}(c \to s)}{\sum_{u \in \mathcal{C}(c)} \text{TraceInvocations}(c \to u)}$$
If distributed tracing is fully degraded, the topological coupling defaults to uniform allocation $\omega(c, s) = \frac{1}{|\mathcal{C}(c)|}$.

### 2.2.5 Unified CERCA Inference Equation
The final composite Root Cause Score $\mathcal{S}_{CERCA}(s)$ integrates direct and contextual evidence:
$$\mathcal{S}_{CERCA}(s) = L_{direct}(s) + \alpha \cdot \Gamma(s) = L_{direct}(s) + \alpha \sum_{c \in \mathcal{P}(s)} \omega(c, s) \cdot L_{direct}(c)$$
Where $\alpha = 0.5$ is the contextual propagation damping factor.

---

## 2.3 Mathematical Proof: Attribution Under Complete Telemetry Loss

**Theorem 1 (Contextual Identifiability of Silent Nodes).**  
*Let service $s^* \in V$ be the true root cause. Suppose that telemetry collection at $s^*$ completely collapses such that all direct telemetry features are nullified ($L_{direct}(s^*) = 0$). If at least one active upstream caller $c \in \mathcal{P}(s^*)$ remains instrumented and observes downstream RPC latency or error propagation ($L_{direct}(c) > 0$), then $\mathcal{S}_{CERCA}(s^*) > 0$ and $s^*$ remains diagnostic.*

**Proof:**  
1. By condition of complete telemetry failure at $s^*$:
   $$A(s^*) = 0 \implies L_{direct}(s^*) = A(s^*) \cdot \lambda(s^*) = 0$$
2. Since $s^*$ is the root cause executing anomalous work, synchronous invocations from caller $c \in \mathcal{P}(s^*)$ experience elevated round-trip latency and client-side timeouts.
3. Because caller $c$ is instrumented, its internal latency coefficient of variation and error rate elevate, yielding:
   $$A(c) > 0, \quad \lambda(c) \ge 0.5 \implies L_{direct}(c) > 0$$
4. Evaluating the unified CERCA inference equation for $s^*$:
   $$\mathcal{S}_{CERCA}(s^*) = 0 + \alpha \sum_{c \in \mathcal{P}(s^*)} \omega(c, s^*) \cdot L_{direct}(c)$$
   Since $\alpha = 0.5 > 0$, $\omega(c, s^*) > 0$, and $L_{direct}(c) > 0$, the sum consists of strictly positive terms:
   $$\mathcal{S}_{CERCA}(s^*) \ge \alpha \cdot \omega(c, s^*) \cdot L_{direct}(c) > 0$$
5. For any unrelated non-root service $u \notin \{s^*\} \cup \mathcal{P}(s^*)$ with no downstream failure and no symptomatic callers:
   $$\mathcal{S}_{CERCA}(u) \approx 0$$
   Consequently, $\mathcal{S}_{CERCA}(s^*) > \mathcal{S}_{CERCA}(u)$, establishing that root cause $s^*$ is ranked above healthy background services despite having zero direct telemetry. $\blacksquare$

---

## 2.4 Elimination of the PageRank "Sink-Trap" Bias

State-of-the-art causal and graph-based RCA tools (e.g., MicroRCA, CloudRanger, Microscope) utilize Personalized PageRank or random walk formulations over graph $G$:
$$\mathbf{p}^{(t+1)} = (1 - d)\mathbf{p}_0 + d \cdot \mathbf{M}^T \mathbf{p}^{(t)}$$
Where $\mathbf{M}$ is the transition probability matrix and $d \approx 0.85$ is the teleportation factor.

### The Sink-Trap Failure Mode
In microservice architectures, leaf nodes (e.g., databases like MongoDB, Redis caches, storage engines) have large in-degree from business logic services, but zero out-degree ($\mathcal{C}(s_{leaf}) = \emptyset$). 
Under iterative matrix multiplication ($t \to \infty$):
1. In the reversed graph $H$ where blame flows upstream, terminal nodes continually absorb probability mass.
2. Even when a database or leaf service is operating normally, random walk transitions concentrate stationary probability at leaf nodes.
3. This creates a severe **false-positive bias**, where storage sinks are persistently ranked as false root causes.

### CERCA's 1-Hop Bounded Formulation
CERCA eliminates this structural pathology through **strictly bounded 1-hop backward propagation**:
1. Blame is passed **only** across direct incident edges $(c, s) \in E$.
2. Transitive chains ($c_1 \to c_2 \to s$) are not allowed to circulate through infinite-depth random walks. Caller $c_1$ blames $c_2$, and $c_2$ blames $s$, maintaining strictly localized causality.
3. **Linear Computational Complexity:** Bounding the depth to 1 eliminates matrix inversions and iterative power methods, reducing execution time from $O(k(|V|+|E|))$ to a single graph pass $O(|V| + |E|)$.
