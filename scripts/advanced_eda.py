import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.preprocessing import StandardScaler

plt.style.use('seaborn-v0_8-whitegrid')

def main():
    print("═══ CERCA Advanced EDA & Feature Selection ═══")
    data_path = "E:/CERCA/data/rca_dataset.csv"
    if not os.path.exists(data_path):
        print("Error: rca_dataset.csv not found. Waiting for experiments to complete.")
        return

    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} records from {df['experiment_id'].nunique()} experiments.")
    print(f"Total Root Cause instances: {df['is_root_cause'].sum()}")
    print("-" * 50)
    
    # 1. Prepare Data
    # Drop IDs and constant columns
    drop_cols = ["service", "experiment_id", "fault_type", "true_root_cause", "is_root_cause"]
    X = df.drop(columns=[c for c in drop_cols if c in df.columns])
    
    # Filter only numeric columns
    numeric_cols = X.select_dtypes(include=[np.number]).columns
    X = X[numeric_cols].fillna(0)
    
    # Drop columns with 0 variance
    X = X.loc[:, X.var() > 0]
    y = df["is_root_cause"]

    features = X.columns.tolist()

    # 2. Point-Biserial Correlation (Linear)
    print("\n[1/3] Computing Linear Correlations...")
    correlations = []
    for col in features:
        r = X[col].corr(y)
        correlations.append(r)
    
    # 3. Mutual Information (Non-linear dependency)
    print("[2/3] Computing Mutual Information...")
    mi_scores = mutual_info_classif(X, y, random_state=42)
    
    # 4. Random Forest Feature Importance (Multivariate interactions) + Hyperparameter Tuning
    print("[3/3] Computing Random Forest Importance (with GridSearchCV)...")
    from sklearn.model_selection import GridSearchCV
    
    param_grid = {
        'n_estimators': [50, 100, 200],
        'max_depth': [3, 5, None],
        'min_samples_split': [2, 5]
    }
    rf = RandomForestClassifier(random_state=42)
    grid = GridSearchCV(rf, param_grid, cv=3, scoring='accuracy', n_jobs=-1)
    
    # Since dataset is extremely small (117 rows) and heavily imbalanced (7 anomalies),
    # CV might fail if a fold has 0 anomalies. We'll use a simpler StratifiedKFold or just fit directly if it fails.
    try:
        grid.fit(X, y)
        best_rf = grid.best_estimator_
        print(f"      Best Params: {grid.best_params_}")
    except ValueError:
        # Fallback if CV splits fail due to rare classes
        best_rf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
        best_rf.fit(X, y)
        
    rf_importances = best_rf.feature_importances_

    # 5. Consolidate Results
    results = pd.DataFrame({
        "Feature": features,
        "Correlation": correlations,
        "Abs_Correlation": np.abs(correlations),
        "Mutual_Information": mi_scores,
        "RF_Importance": rf_importances
    })

    # Normalize scores to 0-1 for a combined ranking
    scaler = StandardScaler()
    scaled = scaler.fit_transform(results[["Abs_Correlation", "Mutual_Information", "RF_Importance"]])
    results["Combined_Score"] = scaled.mean(axis=1)
    
    results = results.sort_values(by="Combined_Score", ascending=False).reset_index(drop=True)

    print("\nTop 15 Most Informative Reliability Features:")
    print("═" * 80)
    print(results.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("═" * 80)

    # 6. Save weights for CERCA
    # Select top 5 features
    top_features = results.head(5)["Feature"].tolist()
    
    out_dir = "E:/CERCA/cerca"
    os.makedirs(out_dir, exist_ok=True)
    weights_path = os.path.join(out_dir, "learned_weights.json")
    
    # In a real scenario, we might use logistic regression coefficients, 
    # but here we'll just save the RF importance as the weight, with the sign of the correlation.
    weights_dict = {}
    for _, row in results.head(10).iterrows():
        feat = row["Feature"]
        # Weight is RF_importance * sign(Correlation)
        sign = 1 if row["Correlation"] >= 0 else -1
        weight = row["RF_Importance"] * sign
        weights_dict[feat] = weight
        
    import json
    with open(weights_path, "w") as f:
        json.dump(weights_dict, f, indent=4)
        
    print(f"\nSaved empirical weights to {weights_path}")
    print("These will be used in the CERCA graph inference algorithm!")

if __name__ == "__main__":
    main()
