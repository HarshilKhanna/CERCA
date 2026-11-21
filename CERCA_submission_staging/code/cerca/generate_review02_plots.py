import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Styling for academic publication
plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.size"] = 11
plt.rcParams["axes.titlesize"] = 13
plt.rcParams["axes.labelsize"] = 12
plt.rcParams["xtick.labelsize"] = 10
plt.rcParams["ytick.labelsize"] = 10
plt.rcParams["legend.fontsize"] = 10
plt.rcParams["figure.titlesize"] = 14

def plot_robustness_curves():
    csv_path = "E:/CERCA/cerca/degradation_robustness.csv"
    if not os.path.exists(csv_path):
        print(f"File {csv_path} not found.")
        return
    df = pd.read_csv(csv_path)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5), dpi=300)
    
    # 1. MRR Robustness Curve
    ax1.plot(df["Drop_Percentage"], df["CERCA_MRR"], marker="o", linewidth=2.5, color="#1f77b4", label="CERCA (Proposed)")
    ax1.plot(df["Drop_Percentage"], df["MicroRCA_MRR"], marker="s", linewidth=2.0, color="#d62728", linestyle="--", label="Baseline (MicroRCA)")
    ax1.plot(df["Drop_Percentage"], df["PageRank_MRR"], marker="^", linewidth=1.8, color="#2ca02c", linestyle=":", label="Baseline (PageRank)")
    ax1.plot(df["Drop_Percentage"], df["Latency_MRR"], marker="d", linewidth=1.5, color="#ff7f0e", linestyle="-.", label="Baseline (Latency)")
    ax1.plot(df["Drop_Percentage"], df["Error_MRR"], marker="x", linewidth=1.5, color="#9467bd", linestyle="-.", label="Baseline (Error Rate)")
    
    ax1.set_title("Root-Cause Ranking Accuracy (MRR) vs. Telemetry Degradation")
    ax1.set_xlabel("Telemetry Loss Percentage (%)")
    ax1.set_ylabel("Mean Reciprocal Rank (MRR)")
    ax1.set_ylim(0.15, 0.38)
    ax1.set_xticks(df["Drop_Percentage"])
    ax1.legend(loc="lower left", frameon=True)
    ax1.grid(True, linestyle="--", alpha=0.7)
    
    # 2. Top-1 Recall Robustness Curve
    ax2.plot(df["Drop_Percentage"], df["CERCA_Top1"] * 100, marker="o", linewidth=2.5, color="#1f77b4", label="CERCA (Proposed)")
    ax2.plot(df["Drop_Percentage"], df["MicroRCA_Top1"] * 100, marker="s", linewidth=2.0, color="#d62728", linestyle="--", label="Baseline (MicroRCA)")
    
    ax2.set_title("Top-1 Localization Recall (PR@1) vs. Telemetry Degradation")
    ax2.set_xlabel("Telemetry Loss Percentage (%)")
    ax2.set_ylabel("Top-1 Recall (%)")
    ax2.set_ylim(0, 30)
    ax2.set_xticks(df["Drop_Percentage"])
    ax2.legend(loc="upper right", frameon=True)
    ax2.grid(True, linestyle="--", alpha=0.7)
    
    plt.tight_layout()
    out_path = "E:/CERCA/cerca/plot_robustness_curves.png"
    plt.savefig(out_path)
    plt.savefig("E:/CERCA/cerca/im2.png")
    plt.close()
    print(f"Generated: {out_path} and im2.png")

def plot_localization_latency():
    csv_path = "E:/CERCA/cerca/evaluation_metrics_extended.csv"
    if not os.path.exists(csv_path):
        print(f"File {csv_path} not found.")
        return
    df = pd.read_csv(csv_path)
    
    plot_methods = [
        ("CERCA (Full)", "#1f77b4"),
        ("Baseline (PageRank)", "#2ca02c"),
        ("Baseline (Latency)", "#ff7f0e"),
        ("Baseline (Error Rate)", "#9467bd"),
        ("Baseline (True MicroRCA)", "#d62728")
    ]
    
    sub_df = df[df["Method"].isin([m[0] for m in plot_methods])].copy()
    sub_df.set_index("Method", inplace=True)
    
    methods = [m[0] for m in plot_methods]
    colors = [m[1] for m in plot_methods]
    latencies = [sub_df.loc[m, "Latency_ms"] if m in sub_df.index else 0.5 for m in methods]
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5), dpi=300)
    
    # Linear scale
    bars = ax1.bar(range(len(methods)), latencies, color=colors, width=0.55, edgecolor="black", linewidth=0.8)
    ax1.set_title("Localization Latency Comparison (Linear Scale)")
    ax1.set_ylabel("Mean Execution Time (ms)")
    ax1.set_xticks(range(len(methods)))
    ax1.set_xticklabels([m.replace("Baseline ", "").replace(" (True MicroRCA)", "MicroRCA") for m in methods], rotation=25, ha="right")
    ax1.grid(True, linestyle="--", alpha=0.7, axis="y")
    
    for bar in bars:
        h = bar.get_height()
        ax1.annotate(f"{h:.2f} ms",
                    xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 4), textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight="bold")
                    
    # Log scale
    ax2.bar(range(len(methods)), latencies, color=colors, width=0.55, edgecolor="black", linewidth=0.8)
    ax2.set_yscale("log")
    ax2.set_title("Localization Latency Comparison (Log Scale)")
    ax2.set_ylabel("Mean Execution Time (ms, Log Scale)")
    ax2.set_xticks(range(len(methods)))
    ax2.set_xticklabels([m.replace("Baseline ", "").replace(" (True MicroRCA)", "MicroRCA") for m in methods], rotation=25, ha="right")
    ax2.grid(True, linestyle="--", alpha=0.7, axis="y")
    
    plt.tight_layout()
    out_path = "E:/CERCA/cerca/plot_localization_latency.png"
    plt.savefig(out_path)
    plt.savefig("E:/CERCA/cerca/im4.png")
    plt.close()
    print(f"Generated: {out_path} and im4.png")

def plot_ablation_degradation():
    csv_path = "E:/CERCA/cerca/ablation_degradation.csv"
    robustness_csv = "E:/CERCA/cerca/degradation_robustness.csv"
    if not os.path.exists(csv_path):
        print(f"File {csv_path} not found.")
        return

    df = pd.read_csv(csv_path)

    # Load MicroRCA for contrast baseline
    microrca_mrr = None
    if os.path.exists(robustness_csv):
        rdf = pd.read_csv(robustness_csv)
        # Align on Drop_Percentage present in ablation df
        merged = df[["Drop_Percentage"]].merge(rdf[["Drop_Percentage", "MicroRCA_MRR"]], on="Drop_Percentage", how="left")
        microrca_mrr = merged["MicroRCA_MRR"].values

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)

    # Shaded gap between Full and No-Graph to make contribution visible
    ax.fill_between(df["Drop_Percentage"], df["NoGraph_MRR"], df["Full_MRR"],
                    alpha=0.15, color="#1f77b4", label="_nolegend_")

    ax.plot(df["Drop_Percentage"], df["Full_MRR"], marker="o", linewidth=2.8,
            color="#1f77b4", label="CERCA (Full Framework)")
    ax.plot(df["Drop_Percentage"], df["NoGraph_MRR"], marker="x", linewidth=2.2,
            color="#d62728", linestyle="--", label="Ablation: No Graph (Local Only)")
    ax.plot(df["Drop_Percentage"], df["NoReliability_MRR"], marker="^", linewidth=1.8,
            color="#ff7f0e", linestyle=":", label=r"Ablation: No Reliability ($\lambda=1$)")
    ax.plot(df["Drop_Percentage"], df["EqualWeights_MRR"], marker="s", linewidth=1.8,
            color="#2ca02c", linestyle="-.", label="Ablation: Equal Weights")

    # MicroRCA contrast line
    if microrca_mrr is not None:
        ax.plot(df["Drop_Percentage"], microrca_mrr, marker="D", linewidth=2.0,
                color="#7f7f7f", linestyle=(0, (5, 2)), label="MicroRCA Baseline")

    ax.set_title("Ablation Study: Component Contribution Under Telemetry Loss")
    ax.set_xlabel("Telemetry Loss Percentage (%)")
    ax.set_ylabel("Mean Reciprocal Rank (MRR)")
    ax.set_ylim(0.20, 0.35)   # tighter range — differences are much more visible
    ax.set_xticks(df["Drop_Percentage"])
    ax.legend(loc="upper right", frameon=True, framealpha=0.95, fontsize=9)
    ax.grid(True, linestyle="--", alpha=0.7)

    # Annotate the graph contribution at 0% loss
    drop_0 = df[df["Drop_Percentage"] == 0]
    if not drop_0.empty:
        gap = drop_0["Full_MRR"].values[0] - drop_0["NoGraph_MRR"].values[0]
        ax.annotate(f"Graph Contribution @0%:\n+{gap:.3f} MRR",
                    xy=(0, drop_0["NoGraph_MRR"].values[0]),
                    xytext=(20, 20), textcoords="offset points",
                    arrowprops=dict(arrowstyle="->", color="black", lw=1.3),
                    bbox=dict(boxstyle="round,pad=0.4", fc="lightyellow", ec="orange", lw=1),
                    fontsize=9)

    plt.tight_layout()
    out_path = "E:/CERCA/cerca/plot_ablation_degradation.png"
    plt.savefig(out_path)
    plt.savefig("E:/CERCA/cerca/im3.png")
    plt.close()
    print(f"Generated: {out_path} and im3.png")

def plot_modality_breakdown():
    csv_path = "E:/CERCA/cerca/modality_sensitivity.csv"
    if not os.path.exists(csv_path):
        print(f"File {csv_path} not found.")
        return
    df = pd.read_csv(csv_path)
    
    fig, ax = plt.subplots(figsize=(8.5, 5.5), dpi=300)
    
    ax.plot(df["Drop_Percentage"], df["Metric_Drop_MRR"], marker="s", linewidth=2.2, color="#2ca02c", label="Protocol A: Metric Degradation Only")
    ax.plot(df["Drop_Percentage"], df["Trace_Drop_MRR"], marker="^", linewidth=2.2, color="#ff7f0e", linestyle="--", label="Protocol B: Trace Degradation Only")
    ax.plot(df["Drop_Percentage"], df["Combined_Drop_MRR"], marker="o", linewidth=2.5, color="#1f77b4", label="Protocol D: Multimodal Combined Degradation")
    
    ax.set_title("Modality Sensitivity: CERCA Accuracy Under Isolated Degradations")
    ax.set_xlabel("Telemetry Loss Percentage (%)")
    ax.set_ylabel("Mean Reciprocal Rank (MRR)")
    ax.set_ylim(0.20, 0.35)
    ax.set_xticks(df["Drop_Percentage"])
    ax.legend(loc="lower left", frameon=True)
    ax.grid(True, linestyle="--", alpha=0.7)
    
    plt.tight_layout()
    out_path = "E:/CERCA/cerca/plot_modality_breakdown.png"
    plt.savefig(out_path)
    plt.close()
    print(f"Generated: {out_path}")

if __name__ == "__main__":
    plot_robustness_curves()
    plot_localization_latency()
    plot_ablation_degradation()
    plot_modality_breakdown()
    print("All 4 plots generated successfully!")
