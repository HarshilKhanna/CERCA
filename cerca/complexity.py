import time
import pandas as pd
import numpy as np
import networkx as nx
from algorithm import CERCA
import json
import os

def create_synthetic_graph(nodes: int, edges: int):
    # Erdos-Renyi or Barabasi-Albert, we just need a directed graph
    # to simulate the data structures.
    G = nx.erdos_renyi_graph(nodes, p=min(1.0, edges/(nodes**2)), directed=True)
    
    # Save as JSON structure matching DSB
    adj = {"nodes": [{"id": f"s_{i}"} for i in range(nodes)], "edges": []}
    for u, v in G.edges():
        adj["edges"].append({"source": f"s_{u}", "target": f"s_{v}"})
        
    os.makedirs("E:/CERCA/cerca/tmp", exist_ok=True)
    graph_path = "E:/CERCA/cerca/tmp/synthetic_graph.json"
    with open(graph_path, "w") as f:
        json.dump(adj, f)
        
    return graph_path

def create_synthetic_features(nodes: int) -> pd.DataFrame:
    data = []
    for i in range(nodes):
        data.append({
            "service": f"s_{i}",
            "error_rate": np.random.rand(),
            "service_latency_cv": np.random.rand(),
            "service_p99_latency_ms": np.random.rand(),
            "service_span_count": np.random.rand()
        })
    return pd.DataFrame(data)

def main():
    print("═══ CERCA Complexity Benchmark ═══")
    
    algo = CERCA()
    sizes = [10, 100, 1000, 10000]
    
    print(f"{'Nodes (V)':<12} | {'Edges (E)':<12} | {'Inference Time (ms)':<20}")
    print("-" * 50)
    
    for v in sizes:
        e = v * 3 # Sparse graph assumption for microservices
        graph_path = create_synthetic_graph(v, e)
        features = create_synthetic_features(v)
        
        # Warmup
        algo.rank_services(features, graph_path)
        
        # Benchmark
        start = time.perf_counter()
        # Run 10 times for average
        for _ in range(10):
            algo.rank_services(features, graph_path)
        end = time.perf_counter()
        
        avg_ms = ((end - start) / 10.0) * 1000
        print(f"{v:<12} | {e:<12} | {avg_ms:.2f} ms")
        
if __name__ == "__main__":
    main()
