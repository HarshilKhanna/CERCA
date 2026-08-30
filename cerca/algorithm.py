import os
import json
import networkx as nx
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

class CERCA:
    def __init__(self, weights_path: str = "E:/CERCA/cerca/learned_weights.json"):
        with open(weights_path, "r") as f:
            self.weights = json.load(f)
            
    def compute_anomaly_score(self, df: pd.DataFrame) -> pd.Series:
        """Compute base anomaly score using simple latency/error spikes."""
        scores = pd.Series(0.0, index=df.index)
        
        if "error_rate" in df.columns:
            err = df["error_rate"].fillna(0)
            scores += (err / (err.max() + 1e-9)) * 0.5
            
        if "service_latency_cv" in df.columns:
            cv = df["service_latency_cv"].fillna(0)
            scores += (cv / (cv.max() + 1e-9)) * 0.5
            
        return scores
        
    def compute_reliability_weight(self, df: pd.DataFrame, equal_weights: bool = False) -> pd.Series:
        """Compute reliability multiplier based on EDA-learned feature weights."""
        rel_scores = pd.Series(0.0, index=df.index)
        
        for feat, weight in self.weights.items():
            if feat in df.columns:
                val = df[feat].fillna(0)
                val_norm = (val - val.mean()) / (val.std() + 1e-9)
                w = 1.0 if equal_weights else weight
                rel_scores += val_norm * w
                
        # Sigmoid to squish between 0.1 and 1.9
        multiplier = 1.0 + (1.0 / (1.0 + np.exp(-rel_scores)) - 0.5)
        return multiplier

    def rank_services(self, features_df: pd.DataFrame, graph_path: str, variant: str = "full") -> pd.DataFrame:
        """
        Run the CERCA inference algorithm with ablation variants.
        Variants:
        - full: Anomaly * Reliability * Graph
        - no_graph: Anomaly * Reliability
        - no_reliability: Anomaly * Graph
        - equal_weights: Anomaly * EqualReliability * Graph
        """
        
        df = features_df.copy()
        df.set_index("service", inplace=True)
        
        # 1. Base Anomaly Score
        df["anomaly_score"] = self.compute_anomaly_score(df)
        
        # 2. Reliability Weight
        if variant == "no_reliability":
            df["reliability_weight"] = 1.0
        elif variant == "equal_weights":
            df["reliability_weight"] = self.compute_reliability_weight(df, equal_weights=True)
        else:
            df["reliability_weight"] = self.compute_reliability_weight(df, equal_weights=False)
        
        # 3. Local Score (Node Internal Score)
        df["local_score"] = df["anomaly_score"] * df["reliability_weight"]
        
        if variant == "no_graph":
            df["cerca_score"] = df["local_score"]
        else:
            # 4. Graph Influence (Targeted Propagation)
            with open(graph_path, "r") as f:
                adj = json.load(f)
                
            G = nx.DiGraph()
            for node in adj.get("nodes", []):
                G.add_node(node["id"])
            for edge in adj.get("edges", []):
                G.add_edge(edge["source"], edge["target"])
                
            H = G.reverse() # Propagate blame from callers to callees
            
            # Custom propagation: Score = LocalScore + 0.5 * sum(CallerScores)
            # This avoids the "sink trap" of PageRank by only propagating exactly 1 hop
            # with dampening, representing direct blame from clients.
            
            cerca_scores = {}
            for node in df.index:
                if node not in H:
                    cerca_scores[node] = df.loc[node, "local_score"]
                    continue
                    
                local = df.loc[node, "local_score"]
                caller_blame = 0.0
                callers = list(H.successors(node)) # In reversed graph, successors are actual callers
                for caller in callers:
                    if caller in df.index:
                        # Add half of the caller's anomaly score
                        caller_blame += df.loc[caller, "local_score"] * 0.5
                        
                cerca_scores[node] = local + caller_blame
                
            df["cerca_score"] = pd.Series(cerca_scores)
        
        # Rank
        df = df.sort_values(by="cerca_score", ascending=False)
        df["rank"] = range(1, len(df) + 1)
        
        return df.reset_index()[["rank", "service", "cerca_score", "anomaly_score", "reliability_weight"]]
