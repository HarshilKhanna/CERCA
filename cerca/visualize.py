import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import json

plt.style.use('seaborn-v0_8-whitegrid')

def plot_feature_importance():
    with open("E:/CERCA/cerca/learned_weights.json", "r") as f:
        weights = json.load(f)
        
    df = pd.DataFrame(list(weights.items()), columns=["Feature", "Weight"])
    df["Abs_Weight"] = df["Weight"].abs()
    df = df.sort_values(by="Abs_Weight", ascending=False).head(8)
    
    plt.figure(figsize=(10, 6))
    colors = ["#4C72B0" if w > 0 else "#C44E52" for w in df["Weight"]]
    sns.barplot(data=df, x="Weight", y="Feature", hue="Feature", palette=colors, dodge=False)
    
    plt.legend([],[], frameon=False)
    
    plt.title("Reliability Feature Importance (GridSearch RF)", fontsize=14, pad=15)
    plt.xlabel("Assigned Weight", fontsize=12)
    plt.ylabel("")
    plt.tight_layout()
    plt.savefig("E:/CERCA/cerca/plot_feature_importance.png", dpi=300)
    plt.close()

def plot_accuracy_comparison():
    df = pd.read_csv("E:/CERCA/cerca/evaluation_metrics.csv")
    
    # Filter to only the most important rows to not clutter
    keep_methods = [
        "CERCA (Full)", 
        "Baseline (Latency)", 
        "Baseline (Error Rate)", 
        "Baseline (PageRank)",
        "Baseline (True MicroRCA)",
        "Robustness (50% Drop)"
    ]
    df = df[df["Method"].isin(keep_methods)]
    
    df_melt = pd.melt(df, id_vars="Method", value_vars=["Top-1", "Top-3"], var_name="Metric", value_name="Accuracy")
    
    plt.figure(figsize=(12, 6))
    sns.barplot(data=df_melt, x="Method", y="Accuracy", hue="Metric", palette="Blues_d")
    
    plt.title("RCA Evaluation & Robustness", fontsize=14, pad=15)
    plt.xlabel("")
    plt.ylabel("Accuracy", fontsize=12)
    plt.xticks(rotation=45, ha='right')
    plt.ylim(0, 0.4)
    
def plot_robustness_comparison():
    df = pd.read_csv("E:/CERCA/cerca/evaluation_metrics.csv")
    
    methods = ["CERCA", "MicroRCA"]
    samplings = ["100% Sampling", "20% Sampling", "5% Sampling"]
    
    # We need to map the methods in the CSV to this structure
    mapping = {
        "CERCA (Full)": ("CERCA", "100% Sampling"),
        "CERCA (20% Sampling)": ("CERCA", "20% Sampling"),
        "CERCA (5% Sampling)": ("CERCA", "5% Sampling"),
        "Baseline (True MicroRCA)": ("MicroRCA", "100% Sampling"),
        "MicroRCA (20% Sampling)": ("MicroRCA", "20% Sampling"),
        "MicroRCA (5% Sampling)": ("MicroRCA", "5% Sampling")
    }
    
    plot_data = []
    for row_method, (model, sampling) in mapping.items():
        row = df[df["Method"] == row_method]
        if not row.empty:
            mrr = row.iloc[0]["MRR"]
            plot_data.append({"Model": model, "Sampling": sampling, "MRR": mrr})
            
    if not plot_data: return
    
    plot_df = pd.DataFrame(plot_data)
    
    plt.figure(figsize=(10, 6))
    sns.lineplot(data=plot_df, x="Sampling", y="MRR", hue="Model", marker="o", linewidth=2.5, markersize=10, palette=["#2ca02c", "#d62728"])
    
    plt.title("RCA Robustness to Telemetry Degradation", fontsize=14, pad=15)
    plt.xlabel("Available Telemetry (Sampling Rate)", fontsize=12)
    plt.ylabel("Mean Reciprocal Rank (MRR)", fontsize=12)
    plt.ylim(0, 0.4)
    plt.grid(True, linestyle="--", alpha=0.7)
    
    plt.tight_layout()
    plt.savefig("E:/CERCA/cerca/plot_robustness.png", dpi=300)
    plt.close()

if __name__ == "__main__":
    plot_feature_importance()
    plot_accuracy_comparison()
    plot_robustness_comparison()
    print("Visualizations saved to E:/CERCA/cerca/")
