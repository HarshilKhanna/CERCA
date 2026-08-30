# 2. Systematic Literature Review

To establish the state of the art in Root Cause Analysis (RCA) for microservices and accurately identify the research gap, a systematic literature review was conducted. The reviewed literature is categorized based on the primary telemetry source utilized (metrics, traces, logs) and the underlying algorithmic approach (statistical, causal, graph-based). This taxonomic approach highlights the evolution of RCA techniques and the systemic limitations regarding telemetry degradation.

## 2.1 Taxonomy of RCA Approaches

Existing RCA methodologies can be systematically classified into three distinct categories:

### 2.1.1 Metric-Based Heuristics & Statistical Methods
Early approaches to RCA relied primarily on univariate or multivariate time-series anomaly detection on system metrics (CPU, Memory, Network) and golden signals (Latency, Errors, Traffic). 
* **Key Literature:** Methods like *CloudRanger* (IEEE INFOCOM) construct static topological graphs and use random walks based purely on metric correlations.
* **Limitations:** While computationally inexpensive, these methods lack dynamic structural context. They struggle to differentiate between the true root cause and a propagated symptom, as metrics alone do not capture the execution path of a specific request.

### 2.1.2 Trace-Based & Causal Discovery Methods
These methods utilize distributed tracing (e.g., Dapper, Jaeger) to reconstruct exact request execution paths. They often apply causal inference techniques, such as the PC algorithm, to build Directed Acyclic Graphs (DAGs) representing causality.
* **Key Literature:** *Microscope* (IEEE ICWS) utilizes causal discovery on metric and log data. *MicroCause* (IEEE TSE) leverages temporal causal discovery algorithms combining traces and metrics.
* **Limitations:** Causal discovery algorithms (like the PC algorithm) are notoriously slow, scaling non-linearly with the number of nodes, making them unsuitable for real-time incident response. Furthermore, they are highly sensitive to missing data; a single dropped span can sever the causal graph.

### 2.1.3 Graph-Based Random Walk Methods
The current state-of-the-art combines dependency graphs (extracted dynamically from traces) with metric-based anomaly scores, utilizing algorithms like PageRank or Personalized PageRank to propagate fault probabilities.
* **Key Literature:** *MicroRCA* (Liu et al., IEEE ICWS 2020) is the foundational paper in this space. It extracts a 1-hop subgraph around anomalous services and weights the edges using Pearson correlation between latency and host metrics, subsequently applying Personalized PageRank.

## 2.2 Deep Dive into Key Baseline: MicroRCA
MicroRCA represents the most relevant baseline for this research. Its core contribution was linking application-level symptoms (latency) to system-level causes (CPU/Memory spikes) using Pearson correlation to direct a random walk.
* **The Mechanism:** If Service A calls Service B, MicroRCA computes the correlation between Service B's latency and Service B's CPU usage. If highly correlated, the edge weight is increased, drawing the Random Walk toward Service B.
* **The Critical Vulnerability:** MicroRCA’s reliance on Pearson correlation over timeseries windows makes it exceptionally brittle. If metric scrapes are missing due to load, or tracing sampling rates are low (e.g., 20%), the timeseries correlation yields zero, NaN, or spurious results. This causes the algorithm's ranking accuracy to collapse below that of simple heuristics under realistic production conditions.

## 2.3 Comparative Matrix of State-of-the-Art

The following matrix systematically compares prominent peer-reviewed RCA frameworks against critical operational dimensions, isolating the gap that CERCA aims to fill:

| Framework | Reference Venue | Telemetry Source | Graph Construction | Inference Mechanism | Telemetry Completeness Assumption | Computational Complexity |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CloudRanger** | IEEE INFOCOM | Metrics | Static Topology | Random Walk / Heuristics | Assumes 100% complete metrics | $O(k(V+E))$ |
| **Microscope** | IEEE ICWS | Metrics + Logs | Causal Discovery | PC Algorithm + Graph | Assumes perfect log/metric sync | High (Causal Discovery) |
| **MicroCause** | IEEE TSE | Metrics + Traces | Temporal Causal | PC-based Causal Inference | Assumes uncorrupted timeseries | High |
| **MicroRCA** | IEEE ICWS | Metrics + Traces | Dynamic Call Graph | Pearson Corr. + PageRank | Assumes 100% complete metrics | $O(k(V+E))$ |
| **CERCA (Proposed)** | - | Metrics + Traces | Dynamic Call Graph | Reliability-Weighted 1-Hop | **Explicitly models degradation** | **$O(V+E)$** |

## 2.4 Summary of Prior Art Limitations
A systematic analysis of the peer-reviewed literature reveals that while graph-based and causal methods have steadily improved accuracy under laboratory conditions, **they universally share a critical vulnerability:** an implicit assumption of perfect telemetry completeness. None of the surveyed methods incorporate data reliability, sampling degradation, or metric loss directly into their propagation mechanics, rendering them brittle in actual production environments.
