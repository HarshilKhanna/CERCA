# 1. Introduction

## 1.1 Background and Context
Modern cloud-native applications are predominantly built using microservice architectures, where monolithic applications are decoupled into dozens or hundreds of independent, loosely coupled services communicating over the network via APIs or RPCs (Remote Procedure Calls). While this paradigm provides high scalability, agility, and deployment velocity, it introduces unprecedented complexity in system observability and fault diagnosis. 

In a highly distributed environment, a single user request traverses an intricate, dynamically changing service dependency graph. When a performance degradation or failure occurs—such as a memory leak, CPU starvation, or network partition—the anomaly propagates rapidly upstream through the call chain. Consequently, a single root cause (e.g., resource exhaustion in a backend database service) can trigger a cascading failure, manifesting as latency spikes and HTTP 5xx errors across multiple downstream and frontend services. 

Root Cause Analysis (RCA) in this context is the critical operational task of ranking all microservices from most to least likely to be the origin of a fault during an active incident. Accurate and rapid RCA is essential for Site Reliability Engineers (SREs) to minimize Mean Time to Recovery (MTTR) and maintain strict Service Level Objectives (SLOs).

## 1.2 The Problem of Telemetry Incompleteness and Degradation
To perform RCA, modern observability platforms rely on three pillars of telemetry: logs, metrics, and distributed traces. Graph-based RCA algorithms, which represent the current state-of-the-art (e.g., MicroRCA), heavily depend on distributed tracing (e.g., Jaeger, Zipkin) to reconstruct the dynamic call graph, and container metrics (e.g., Prometheus) to measure resource utilization and latency variance.

However, a fundamental limitation of current RCA approaches is the **implicit assumption of perfect telemetry completeness**. In real-world production environments, telemetry data is inherently lossy and probabilistic:
1. **Probabilistic Trace Sampling:** Distributed tracing systems generate massive volumes of data. To manage storage overhead and network bandwidth, organizations aggressively sample requests, often recording 1% or less of total traffic.
2. **Metric Scrape Failures:** Metric scraping pipelines (like Prometheus) operate on periodic pull models (e.g., 15-second intervals). During severe network partitions or heavy load—the exact moments RCA is needed most—these scrapes frequently fail or timeout, resulting in temporal gaps.
3. **Instrumentation Gaps:** Services may be temporarily uninstrumented, misconfigured, or lack standardized headers for trace context propagation.

When existing algorithms process data from a system with degraded telemetry, they fail unpredictably. Because they treat missing data as an "absence of anomaly" rather than an "absence of evidence," they either misattribute blame to structurally central services that happen to have complete data, or they suffer a complete collapse in ranking accuracy.

## 1.3 Dataset Readiness and Experimental Setup
A major pitfall in RCA literature is the reliance on synthetic toy datasets or simplistic, non-representative architectures. To ensure rigorous empirical evaluation, this project utilizes a fully validated, production-grade dataset.

The research is conducted using the industry-standard **DeathStarBench Social Network** microservice suite, originally developed by Cornell University.
* **Architecture:** 30 interconnected microservices deployed via Docker Compose, including complex topologies with Nginx, Redis caching layers, MongoDB, and RabbitMQ message queues.
* **Workload Generation:** Realistic, dynamic user simulations (e.g., reading home timelines, composing posts, following users) are generated using Locust, maintaining a steady throughput to establish baseline telemetry.
* **Fault Injection Methodology:** The dataset encompasses 7 distinct, reproducible fault scenarios across three categories: CPU starvation (using Docker cgroups to throttle CPU allocation), memory limits (simulating memory leaks), and sudden container restarts (simulating crash-loop backoffs). 
* **Data Readiness:** The dataset is fully generated, processed, and validated. It comprises 118 labeled service records across the 7 fault incidents. Crucially, it includes multimodal observability data: high-resolution distributed spans exported from Jaeger and 15-second container metrics exported from Prometheus. Every instance is fully annotated with ground-truth root cause labels, ensuring robust training and evaluation capability.

## 1.4 Research Objectives
This research proposes **CERCA (Context-Aware Empirical Root Cause Analysis)**, an algorithmic framework designed to address the telemetry gap in existing literature by explicitly modeling data reliability. The primary objectives are:

1. **Multimodal Feature Engineering:** To engineer and mathematically define a comprehensive set of multimodal features that quantify both service anomalies and telemetry data quality (e.g., trace coverage ratios, orphan span rates).
2. **Empirical Reliability Modeling:** To introduce a novel, empirically learned reliability multiplier ($\lambda(s)$) that dynamically modulates a service's anomaly score based on the statistical completeness and integrity of its telemetry data.
3. **Linear-Time Graph Propagation:** To replace computationally expensive iterative random walk algorithms (e.g., Personalized PageRank) with a linear-time ($O(V+E)$) dampened graph propagation mechanism that avoids structural centrality biases.
4. **Robustness Validation:** To empirically demonstrate that CERCA achieves comparable accuracy to state-of-the-art methods under ideal telemetry conditions, while exhibiting significantly higher robustness and statistical stability under realistically degraded (probabilistically sampled) telemetry.
