import os
import glob
import json
import subprocess
import pandas as pd
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

def run_extraction(exp_dir: str, out_dir: str):
    """Run extract_all.py on a specific experiment directory."""
    cmd = [
        "python", "telemetry_features/extract_all.py",
        "--experiment-dir", exp_dir,
        "--output-dir", out_dir
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL)

def main():
    experiments = glob.glob("E:/CERCA/experiments/*")
    
    all_features = []
    
    for exp_dir in experiments:
        exp_name = os.path.basename(exp_dir)
        label_file = os.path.join(exp_dir, "fault_label.json")
        
        # Determine labels
        if os.path.exists(label_file):
            with open(label_file, "r") as f:
                labels = json.load(f)
            root_cause = labels.get("root_cause_service", "none")
            fault_type = labels.get("fault_type", "unknown")
        else:
            # If no label file, assume normal baseline
            root_cause = "none"
            fault_type = "normal"
            
        log.info(f"Processing {exp_name} [Root Cause: {root_cause}, Fault: {fault_type}]")
        
        # Extract features for this experiment
        out_dir = f"E:/CERCA/telemetry_features/outputs/{exp_name}"
        try:
            run_extraction(exp_dir, out_dir)
        except subprocess.CalledProcessError:
            log.error(f"Failed to extract features for {exp_name}")
            continue
            
        # Load service features
        sf_path = os.path.join(out_dir, "service_features.csv")
        if not os.path.exists(sf_path):
            log.warning(f"No service_features.csv found for {exp_name}")
            continue
            
        df = pd.read_csv(sf_path)
        
        # Apply labels
        df["experiment_id"] = exp_name
        df["fault_type"] = fault_type
        df["true_root_cause"] = root_cause
        df["is_root_cause"] = (df["service"] == root_cause).astype(int)
        
        all_features.append(df)
        
    if not all_features:
        log.error("No data processed!")
        return
        
    final_df = pd.concat(all_features, ignore_index=True)
    out_path = "E:/CERCA/data/rca_dataset.csv"
    final_df.to_csv(out_path, index=False)
    log.info(f"Successfully built RCA dataset -> {out_path} ({len(final_df)} rows, {final_df['is_root_cause'].sum()} root cause instances)")

if __name__ == "__main__":
    main()
