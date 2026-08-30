import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import glob
from pathlib import Path

# Use a clean style for publication-quality plots
plt.style.use('seaborn-v0_8-whitegrid')

def load_data(outputs_dir: str):
    """Load and label normal vs fault datasets."""
    normal_dir = os.path.join(outputs_dir, "normal")
    fault_dir = os.path.join(outputs_dir, "fault")

    # Service Features
    df_n = pd.read_csv(os.path.join(normal_dir, "service_features.csv"))
    df_n["condition"] = "normal"
    df_f = pd.read_csv(os.path.join(fault_dir, "service_features.csv"))
    df_f["condition"] = "fault"
    
    df_service = pd.concat([df_n, df_f], ignore_index=True)
    return df_service

def plot_distributions(df: pd.DataFrame, output_dir: str):
    """Plot feature distributions comparing normal vs fault."""
    features = [
        "latency_mean", "latency_p95", 
        "error_rate", "throughput_mean",
        "cpu_usage_mean"
    ]
    
    fig, axes = plt.subplots(1, len(features), figsize=(20, 5))
    for i, feature in enumerate(features):
        if feature in df.columns:
            sns.violinplot(
                data=df, x="condition", y=feature, 
                ax=axes[i], inner="point", palette=["#4C72B0", "#C44E52"]
            )
            axes[i].set_title(f"Distribution of {feature}")
            axes[i].set_xlabel("")
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "feature_distributions.png"), dpi=300)
    plt.close()

def plot_correlations(df: pd.DataFrame, output_dir: str):
    """Plot correlation heatmap of reliability features."""
    # Select numeric features
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    # Drop IDs and constant columns
    numeric_cols = [c for c in numeric_cols if df[c].nunique() > 1 and "timestamp" not in c]
    
    corr = df[numeric_cols].corr()
    
    plt.figure(figsize=(12, 10))
    sns.heatmap(corr, annot=False, cmap="coolwarm", center=0, square=True)
    plt.title("Pairwise Feature Correlations")
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "feature_correlations.png"), dpi=300)
    plt.close()

def identify_differentiating_features(df: pd.DataFrame) -> pd.DataFrame:
    """Identify which features best separate normal from fault conditions."""
    df_encoded = df.copy()
    df_encoded["is_fault"] = (df_encoded["condition"] == "fault").astype(int)
    
    numeric_cols = df_encoded.select_dtypes(include=[np.number]).columns
    numeric_cols = [c for c in numeric_cols if c != "is_fault" and "timestamp" not in c]
    
    corrs = []
    for col in numeric_cols:
        if df_encoded[col].nunique() > 1:
            r = df_encoded[col].corr(df_encoded["is_fault"])
            corrs.append({"feature": col, "correlation_with_fault": r, "abs_correlation": abs(r)})
            
    res = pd.DataFrame(corrs).sort_values(by="abs_correlation", ascending=False)
    return res

def main():
    outputs_dir = "E:/CERCA/telemetry_features/outputs"
    plots_dir = "E:/CERCA/graphs/eda"
    os.makedirs(plots_dir, exist_ok=True)
    
    print("═══ CERCA Exploratory Data Analysis ═══")
    
    try:
        df = load_data(outputs_dir)
        print(f"Loaded {len(df)} service records ({len(df[df['condition']=='normal'])} normal, {len(df[df['condition']=='fault'])} fault).")
    except Exception as e:
        print(f"Error loading data: {e}")
        print("Please ensure you have extracted normal features to 'outputs/normal' and fault features to 'outputs/fault'.")
        return

    print("\n1. Computing Feature Distributions...")
    plot_distributions(df, plots_dir)
    print(f"   Saved -> {plots_dir}/feature_distributions.png")
    
    print("\n2. Computing Pairwise Correlations...")
    plot_correlations(df, plots_dir)
    print(f"   Saved -> {plots_dir}/feature_correlations.png")
    
    print("\n3. Identifying Top Differentiating Features...")
    diff_df = identify_differentiating_features(df)
    print("\nTop 10 features most correlated with fault state:")
    print("-" * 55)
    print(diff_df.head(10).to_string(index=False))
    
    # Save statistics report
    stats_path = os.path.join(plots_dir, "eda_report.txt")
    with open(stats_path, "w") as f:
        f.write("CERCA EDA Statistics Report\n")
        f.write("===========================\n\n")
        f.write("Descriptive Statistics (Normal):\n")
        f.write(df[df["condition"] == "normal"].describe().to_string())
        f.write("\n\nDescriptive Statistics (Fault):\n")
        f.write(df[df["condition"] == "fault"].describe().to_string())
        f.write("\n\nTop Differentiating Features:\n")
        f.write(diff_df.head(20).to_string())
    
    print(f"\n   Detailed report saved -> {stats_path}")
    print("═══════════════════════════════════════")

if __name__ == "__main__":
    main()
