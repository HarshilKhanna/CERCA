# 4. Experimental Testbed, Fault Injections & Telemetry Specification

To ensure 100% reproducibility and address the review panel's requirements, this document specifies the benchmark microservice testbed, service topology, fault categories, injection procedure, and exact telemetry signals collected.

---

## 4.1 Benchmark Testbed: DeathStarBench Social Network

The evaluation testbed is built on the industry-standard benchmark **DeathStarBench Social Network**, a multi-tiered microservice application modeling a broadcast social media platform. The system is containerized via Docker Compose (`docker-compose.yml`) and orchestrated under real HTTP/REST and Thrift RPC workloads.

### 4.1.1 Microservice Inventory (14 Core Services)

| Service Name | Primary Role | In-Degree (Callers) | Out-Degree (Callees) | Underlying Technology |
| :--- | :--- | :---: | :---: | :--- |
| `nginx-web-server` | Ingress API gateway & reverse proxy | 0 (Entrypoint) | 3 | Nginx / C |
| `compose-post-service` | Aggregates media, text, user mentions | 1 | 5 | C++ Thrift |
| `post-storage-service` | Post persistence & document retrieval | 3 | 1 | C++ Thrift / MongoDB |
| `user-timeline-service`| Reads/writes individual user timelines | 2 | 2 | C++ Thrift / Redis |
| `home-timeline-service`| Fan-out delivery of follower feeds | 2 | 2 | C++ Thrift / Redis |
| `text-service` | Parses hashtags, mentions, and links | 1 | 2 | C++ Thrift |
| `social-graph-service` | Manages follow/unfollow relationships | 2 | 1 | C++ Thrift / Redis |
| `user-service` | User authentication & profile storage | 3 | 1 | C++ Thrift / MongoDB |
| `media-service` | Uploads and registers media assets | 1 | 1 | C++ Thrift / MongoDB |
| `unique-id-service` | Generates 64-bit snowflake IDs | 1 | 0 | C++ Thrift |
| `url-shorten-service` | Shortens URLs in post text | 1 | 0 | C++ Thrift / MongoDB |
| `user-mention-service`| Resolves `@username` strings | 1 | 1 | C++ Thrift |
| `jaeger-all-in-one` | Distributed tracing collector/query | Dynamic | Dynamic | Go |
| `prometheus` | Time-series metric collection engine | Scraping | Dynamic | Go |

**Topological Properties:**
- Total Nodes ($|V|$): 14
- Total Service Dependency Edges ($|E|$): 19 directed RPC invocation paths
- Network Graph Diameter: 4 hops
- Average Degree: 2.71

---

## 4.2 Fault Injection Framework & Dataset Catalog

Fault injections were orchestrated using a controlled synthetic load generator driven by Locust (`scripts/generate_workload.py`) executing concurrent user scenarios (reading feeds, composing posts, following users). Faults were injected directly into container cgroups via `scripts/inject_fault.py`.

### 4.2.1 Three Fault Categories
1. **CPU Throttling (`CPU`):** Restricts the Docker container's CFS CPU quota (`--cpu-quota=20000` on 100000 period, limiting container to 0.2 CPU cores). Simulates background computational exhaustion or runaway thread contention.
2. **Memory Starvation (`Memory`):** Dynamically clamps the container memory ceiling (`--memory=64m`), triggering swap thrashing and kernel page reclaim latency.
3. **Container Crash/Restart (`Restart`):** Forces a hard container termination and restart, generating connection refused errors and socket backlog timeouts in upstream callers.

### 4.2.2 Labeled Experimental Dataset Catalog (7 Faults + 2 Baselines)

| Experiment ID | Target Service (Ground Truth) | Injected Fault Category | Workload Duration | Total Spans Collected | Total Metrics Collected | Labeled Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `20260728_221633` | *None* | `Normal Baseline` | 120s | 2,133 | 1,480 | Baseline |
| `20260728_222830` | *None* | `Normal Baseline` | 120s | 4,527 | 1,480 | Baseline |
| `20260728_225443` | `user-service` | `CPU Throttling` | 120s | 3,184 | 1,480 | Labeled Fault 1 |
| `20260728_225813` | `social-graph-service` | `CPU Throttling` | 120s | 3,210 | 1,480 | Labeled Fault 2 |
| `20260728_230237` | `post-storage-service` | `CPU Throttling` | 120s | 3,450 | 1,480 | Labeled Fault 3 |
| `20260728_230630` | `home-timeline-service`| `Memory Starvation` | 120s | 2,980 | 1,480 | Labeled Fault 4 |
| `20260728_231039` | `user-timeline-service`| `Memory Starvation` | 120s | 3,015 | 1,480 | Labeled Fault 5 |
| `20260728_231455` | `media-service` | `Container Restart` | 120s | 2,750 | 1,480 | Labeled Fault 6 |
| `20260728_231909` | `text-service` | `Container Restart` | 120s | 2,890 | 1,480 | Labeled Fault 7 |

---

## 4.3 Telemetry Features Collected Across Modalities

The telemetry ingestion engine (`telemetry_features/extract_all.py`) merges high-resolution Jaeger spans with 15-second Prometheus metric scrapes into 27 engineered statistical features per service:

### 1. Distributed Trace Features (Jaeger)
* `service_span_count`: Total executed spans for service $s$ during window $W$.
* `service_mean_latency_ms`: Average execution duration.
* `service_std_latency_ms`: Latency standard deviation.
* `service_p50_latency_ms`, `service_p95_latency_ms`, `service_p99_latency_ms`: Percentile tail latency.
* `service_latency_cv`: Coefficient of variation ($\sigma / \mu$), identifying localized instability.
* `service_trace_participation_rate`: Percentage of global request traces involving service $s$.
* `service_root_span_rate`: Ratio of traces initiated at this service.
* `service_fan_out_mean`: Mean number of downstream RPCs spawned per span.
* `service_orphan_span_rate`: Ratio of spans missing parent context due to trace buffer overflows.

### 2. Time-Series System Metrics (Prometheus / cAdvisor)
* `service_cpu_mean_rate`: Normalized CPU core utilization.
* `service_cpu_cv`: Temporal volatility of CPU utilization.
* `service_mem_mean_bytes`: Resident set size (RSS) memory consumption in bytes.
* `service_mem_stability`: Inverted standard deviation of memory usage.
* `service_net_rx_mean_rate`: Ingress network throughput (bytes/sec).
* `service_net_tx_mean_rate`: Egress network throughput (bytes/sec).

### 3. Log & Error Signals
* `service_error_count`: Count of spans or proxy logs recording HTTP 5xx codes or Thrift exceptions.
* `service_error_rate`: Proportion of failing requests ($\text{errors} / \text{total spans}$).

### 4. Telemetry Completeness & Reliability Signals (Inputs to $\lambda(s)$)
* `metric_samples_available`: Count of successful Prometheus scrapes in window $W$.
* `metric_coverage_ratio`: Temporal completeness ratio of time-series observations.
* `total_traces`: Gross sample volume of distributed traces.
