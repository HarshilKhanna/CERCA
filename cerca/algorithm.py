import os
import json
import time
import networkx as nx
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

class CERCA:
    """
    Context-Aware Empirical Root Cause Analysis (CERCA)

    Formal Formulation:
        S_CERCA(s) = L_direct(s) + alpha * sum_{c in Pred(s)} omega(c, s) * L_direct(c)
    where:
        L_direct(s) = A(s) * lambda(s)
        A(s) is the normalized base anomaly score in [0, 1]
        lambda(s) is the data-quality reliability multiplier in [0.5, 1.5]
        Pred(s) is the set of upstream callers of s in dependency graph G
        omega(c, s) is the caller-callee coupling coefficient (latency correlation)
        alpha is the contextual dampening factor (default: 0.5)

    Hub-Centrality Mitigation (Issue 4 fix):
        High in-degree "hub" services (e.g. url-shorten-service with 2 callers) accumulate
        caller blame proportional to their in-degree, outranking true root causes.
        Mitigation: normalise caller blame by the callee's in-degree (fan-in), so each
        edge contributes blame / fan_in rather than accumulating unboundedly.
        This is equivalent to averaging rather than summing upstream blame.
    """

    def __init__(self, weights_path: str = "E:/CERCA/cerca/learned_weights.json",
                 alpha: float = 0.5, hub_mitigation: bool = True):
        self.weights_path = weights_path
        self.alpha = alpha
        self.hub_mitigation = hub_mitigation   # normalise by in-degree to suppress hub accumulation
        self.last_latency_ms = 0.0

        if os.path.exists(weights_path):
            with open(weights_path, "r") as f:
                self.weights = json.load(f)
        else:
            self.weights = {}

    # ------------------------------------------------------------------
    # Step 1 – Base Anomaly Score A(s)
    # ------------------------------------------------------------------
    def compute_anomaly_score(self, df: pd.DataFrame) -> pd.Series:
        """
        Compute base anomaly score A(s) in [0,1] using:
          - Normalised latency coefficient of variation (CV)
          - Normalised error rate
        Both are weighted equally at 0.5 each.
        """
        scores = pd.Series(0.0, index=df.index)

        if "error_rate" in df.columns:
            err = df["error_rate"].fillna(0)
            max_err = err.max()
            if max_err > 0:
                scores += (err / max_err) * 0.5

        if "service_latency_cv" in df.columns:
            cv = df["service_latency_cv"].fillna(0)
            max_cv = cv.max()
            if max_cv > 0:
                scores += (cv / max_cv) * 0.5

        return scores

    # ------------------------------------------------------------------
    # Step 2 – Reliability Multiplier lambda(s)
    # ------------------------------------------------------------------
    def compute_reliability_weight(self, df: pd.DataFrame,
                                   equal_weights: bool = False) -> pd.Series:
        """
        Compute reliability multiplier lambda(s) bounded in (0.5, 1.5) via sigmoid:
            lambda(s) = 1.0 + (sigmoid(sum_f w_f * z_f(s)) - 0.5)
        where z_f(s) is the z-score of feature f for service s.
        """
        rel_scores = pd.Series(0.0, index=df.index)

        for feat, weight in self.weights.items():
            if feat in df.columns:
                val = df[feat].fillna(0)
                std = val.std()
                val_norm = (val - val.mean()) / (std if std > 0 else 1.0)
                w = 1.0 if equal_weights else weight
                rel_scores += val_norm * w

        # Sigmoid squashed to (0.5, 1.5)
        sigmoid = 1.0 / (1.0 + np.exp(-np.clip(rel_scores, -10, 10)))
        multiplier = 1.0 + (sigmoid - 0.5)
        return multiplier

    # ------------------------------------------------------------------
    # Step 3 – Correlation-based Edge Coupling (for hub mitigation)
    # ------------------------------------------------------------------
    def _compute_edge_coupling(self, df: pd.DataFrame, G: nx.DiGraph) -> dict:
        """
        Compute latency-correlation coupling coefficient omega(caller, callee).

        If service_latency_cv is available we approximate coupling as the
        similarity of anomaly profiles between connected services (both
        elevated CV means strong coupling). Falls back to uniform weight 1.0.

        Returns dict[(caller, callee)] -> coupling in [0.01, 1.0]
        """
        coupling = {}
        for u, v in G.edges():
            if u in df.index and v in df.index:
                cv_u = float(df.loc[u, "service_latency_cv"]) if "service_latency_cv" in df.columns else 0.0
                cv_v = float(df.loc[v, "service_latency_cv"]) if "service_latency_cv" in df.columns else 0.0
                # Geometric mean of normalised CV scores as a proxy for co-anomaly
                max_cv = df["service_latency_cv"].max() if "service_latency_cv" in df.columns else 1.0
                if max_cv > 0:
                    norm_u = cv_u / max_cv
                    norm_v = cv_v / max_cv
                    w = max(0.01, (norm_u * norm_v) ** 0.5)
                else:
                    w = 1.0
                coupling[(u, v)] = w
            else:
                coupling[(u, v)] = 1.0
        return coupling

    # ------------------------------------------------------------------
    # Main ranking entry-point
    # ------------------------------------------------------------------
    def rank_services(self, features_df: pd.DataFrame, graph_path: str,
                      variant: str = "full") -> pd.DataFrame:
        """
        Execute the CERCA inference algorithm with ablation support.

        Variants:
        - full             : Direct Evidence L(s) + Upstream Contextual Blame Gamma(s)
                             with hub-centrality mitigation (in-degree normalisation)
        - no_graph         : Direct Evidence L(s) only (no contextual propagation)
        - no_reliability   : Base Anomaly A(s) with lambda(s)=1.0 + Graph Context
        - equal_weights    : Base Anomaly A(s) with unweighted lambda(s) + Graph Context
        - no_hub_mitigation: Full CERCA without in-degree normalisation (ablation)
        """
        t0 = time.perf_counter()

        df = features_df.copy()
        df.set_index("service", inplace=True)

        # --- Step 1: Base Anomaly Score A(s) ---
        df["anomaly_score"] = self.compute_anomaly_score(df)

        # --- Step 2: Reliability Multiplier lambda(s) ---
        if variant == "no_reliability":
            df["reliability_weight"] = 1.0
        elif variant == "equal_weights":
            df["reliability_weight"] = self.compute_reliability_weight(df, equal_weights=True)
        else:
            df["reliability_weight"] = self.compute_reliability_weight(df, equal_weights=False)

        # --- Step 3: Direct Symptomatic Evidence L(s) = A(s) * lambda(s) ---
        df["local_score"] = df["anomaly_score"] * df["reliability_weight"]

        if variant == "no_graph":
            df["cerca_score"] = df["local_score"]
        else:
            # --- Step 4: Load dependency graph ---
            with open(graph_path, "r") as f:
                adj = json.load(f)

            G = nx.DiGraph()
            for node in adj.get("nodes", []):
                G.add_node(node["id"])
            for edge in adj.get("edges", []):
                G.add_edge(edge["source"], edge["target"])

            # Reversed graph: successors of s = actual callers of s
            H = G.reverse()

            # --- Hub-Centrality Mitigation ---
            # Compute in-degree in original graph for each node (= number of callers).
            # When accumulating blame, divide each caller's contribution by the
            # total fan-in so that high-degree hubs don't accumulate disproportionate
            # blame. Equivalent to averaging rather than summing upstream signals.
            in_degree = dict(G.in_degree())   # G (caller->callee), so in_degree[s] = #callers of s

            # Edge coupling coefficients (latency co-anomaly)
            use_hub_mitigation = self.hub_mitigation and (variant != "no_hub_mitigation")
            coupling = self._compute_edge_coupling(df, G)

            # --- Step 5: 1-Hop Bounded Contextual Blame Propagation ---
            cerca_scores = {}
            for node in df.index:
                local = df.loc[node, "local_score"]
                caller_blame = 0.0

                if node in H:
                    callers = [c for c in H.successors(node) if c in df.index]
                    n_callers = len(callers)

                    for caller in callers:
                        edge_w = coupling.get((caller, node), 1.0)
                        contrib = df.loc[caller, "local_score"] * self.alpha * edge_w

                        if use_hub_mitigation and n_callers > 1:
                            # Normalise by fan-in: each caller contributes 1/n_callers weight.
                            # This prevents hub services (many callers) from accumulating
                            # disproportionate aggregate blame scores.
                            contrib /= n_callers

                        caller_blame += contrib

                cerca_scores[node] = local + caller_blame

            df["cerca_score"] = pd.Series(cerca_scores)

        # --- Rank by descending score ---
        df = df.sort_values(by="cerca_score", ascending=False)
        df["rank"] = range(1, len(df) + 1)

        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
        res = df.reset_index()[["rank", "service", "cerca_score", "anomaly_score", "reliability_weight"]]
        res.attrs["latency_ms"] = self.last_latency_ms
        return res
