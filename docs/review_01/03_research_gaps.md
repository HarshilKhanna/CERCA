# 3. Research Gaps

Based on the systematic literature review and empirical evaluation of existing methodologies, several critical research gaps have been identified in the domain of Root Cause Analysis for microservices. These gaps severely limit the applicability of current state-of-the-art algorithms in production environments.

## 3.1 Gap 1: The Erroneous Assumption of Perfect Telemetry
The most glaring limitation in the current literature (including seminal works like MicroRCA, CloudRanger, and MicroCause) is the implicit assumption that telemetry data is 100% complete, synchronous, and uncorrupted. 

**The Production Reality:**
In real-world distributed systems, full-fidelity tracing is economically and computationally infeasible. 
* Distributed traces are heavily sampled (e.g., Google’s Dapper and open-source Jaeger routinely sample at 1% or less to manage overhead).
* Metric pipelines (Prometheus) operate on pull-based scrape intervals (e.g., 15s). During the very incidents RCA is trying to diagnose (network partitions, CPU saturation), scrapes frequently time out, creating temporal gaps in the data.

**The Algorithmic Failure:**
Existing algorithms treat missing data identically to an *absence of an anomaly*. When applied to degraded telemetry, correlation-based mechanisms (like the Pearson correlation used in MicroRCA) fail mathematically—yielding NaN or zero variance. This leads to the misattribution of blame to services that simply happen to have more data available, punishing well-instrumented services while ignoring true root causes that lack data. **There is an urgent need for an RCA framework that mathematically models, quantifies, and compensates for telemetry reliability.**

## 3.2 Gap 2: The "Sink-Trap" Bias of Iterative Random Walks
The prevailing methodology in graph-based RCA is to use iterative random walk algorithms, such as PageRank or Personalized PageRank (PPR), to propagate anomaly scores backward through the service dependency graph.

* **The Structural Issue:** PageRank inherently favors structurally central nodes. In a microservice topology, these are typically API gateways, load balancers, or highly utilized shared backend services (e.g., an authentication service or a URL shortener).
* **The "Sink-Trap":** In a fault scenario, these structural "hubs" act as sink-traps. Because they are connected to many downstream victims, iterative random walks cause them to absorb a disproportionate amount of blame probability mass, regardless of their actual localized anomaly evidence. The algorithm loops until convergence, artificially amplifying the blame on central nodes.
* **The Gap:** Iterative propagation mechanisms are often excessive and counterproductive for RCA, where fault propagation is typically localized and direct. There is a need for a bounded, non-iterative propagation mechanism that utilizes graph context without falling victim to structural centrality bias.

## 3.3 Gap 3: Computational Inefficiency During Active Incidents
RCA must be performed in real-time during an active outage, where every minute of downtime incurs severe financial and reputational costs. 
* Causal discovery algorithms (e.g., the PC-algorithm used in MicroCause) scale poorly, often requiring polynomial time relative to the number of metrics and services.
* Iterative PageRank approaches (MicroRCA) require $O(k(V+E))$ operations, where $k$ is the number of iterations until convergence (typically $k > 50$), $V$ is the number of services, and $E$ is the number of edges.
* As microservice architectures scale to thousands of nodes, the inference latency of these algorithms becomes a bottleneck. **There is a critical need for an RCA algorithm that scales strictly linearly ($O(V+E)$) to support rapid, sub-second inference on large topologies.**

## 3.4 Formal Problem Statement
Based on the identified gaps, the problem addressed by this project is formally defined as follows:

*Given a dynamic microservice dependency graph $G=(V,E)$ and a multimodal set of telemetry features (distributed traces and container metrics) collected during an anomaly window, how can we accurately rank the root cause services in a manner that is (a) robust to probabilistic telemetry sampling degradation, (b) immune to the structural sink-trap bias of central nodes, and (c) computationally efficient enough for linear-time real-time inference?*
