==============================================================================
CERCA: Context-Aware Empirical Root Cause Analysis Under Incomplete Telemetry
==============================================================================
Authors: Harshil Khanna (23BCE0867), Charvi Sood (23BCE2202)
Faculty Guide: Dr. Kalyanaraman P
Institution: Vellore Institute of Technology (VIT), Vellore
Course: Foundations of Data Science

PACKAGE CONTENTS & MANIFEST:
------------------------------------------------------------------------------
1. PRIMARY PAPER DOCUMENT:
   - CERCA_first_draft.pdf
     The full research paper formatted in standard 2-column IEEE conference style.
     Contains the theoretical foundations (Positive Upstream Blame Propagation
     Guarantee, Proposition 1 Rank-1 Identifiability with fan-in normalization),
     the 6 context dimensions, comprehensive experimental evaluations against
     MicroRCA across 0%-90% degradation, ablation study, and incident-level
     Wilcoxon signed-rank significance testing (N = 40 independent fault incidents).

2. BASE PAPER:
   - base_paper.pdf
     "MicroRCA: Root Cause Localization of Performance Issues in Microservices"
     Authors: Ping Wu, Yunzhe Liu, Yijun Meng, Zhili Zhang
     Published in IEEE/IFIP Network Operations and Management Symposium (NOMS), 2020.
     Foundational benchmark against which CERCA was evaluated and enhanced.

3. VALIDATION & COMPREHENSIVE REPORTS:
   - CERCA_case_study_3.pdf
     The full Case Study 3 validation report (Validation, Research Contribution,
     and Publication/Patent Potential).
   - faculty_feedback_response.md
     Comprehensive follow-up document addressing each faculty review observation
     (Docker metrics extraction, identifiability theorem, Wilcoxon test correction,
     MicroRCA fairness, ablation results, and latency benchmarking).

4. EXPERIMENTAL DATASET:
   - rca_dataset.csv
     Complete multimodal dataset containing 543 observations across 42 independent
     experiments (40 ground-truth fault injections + 2 normal baselines) in
     DeathStarBench Social Network. All 27 columns (12 trace features, 6 container
     resource metrics, 2 error features, 2 reliability features, and 5 metadata fields)
     are fully populated with 100% microservice coverage.

5. LATEX SOURCE PACKAGE:
   - latex_source/
     ├── conference_101719.tex   (Full IEEEtran LaTeX source code)
     ├── im1.png                 (CERCA Two-tier Inference Architecture)
     ├── im2.png                 (Robustness Curves Under Progressive Degradation)
     ├── im3.png                 (Ablation Trajectories Across Progressive Telemetry Loss)
     └── im4.png                 (Algorithmic & End-to-End Localization Latency)

6. PEER-REVIEWED REFERENCE PAPERS:
   - reference_papers/
     Direct PDF copies of key peer-reviewed literature cited throughout the paper:
     - 2004 Chen et al. (Failure Diagnosis Using Decision Trees, ICAC)
     - 2010 Sigelman et al. (Dapper Distributed Systems Tracing, Google TR)
     - 2015 Vaarandi & Pihelgas (LogCluster Event Log Mining, CNSM)
     - 2021 Yu et al. (MicroRank End-to-End Latency Localization, WWW)
     - 2022 Nguyen et al. (GNNs for Microservice Applications Survey)
     - 2025 Abdulsatar et al. (Deep Learning Cybersecurity Risk Assessment)
     - 2025 Adapa et al. (Quantifying Chaos Engineering in Microservices)
     - 2025 Ambani et al. (Chaos Engineering Survey)
     - 2025 Krasnovsky (Asynchronous Semantics in Trace Models)
     - 2025 Krasnovsky (Model Discovery & Graph Simulation)
     - 2025 Xie et al. (Cascaded Conditional Learning with Hypergraphs)
     - 2026 Krasnovsky et al. (Stochastic Connectivity Runtime Model)
     - 2026 Tariq et al. (Service Failure Detection in Microservices)
     - 2026 Xue et al. (Joint Temporal-Structural Representation Learning)

7. SOURCE CODE, MODEL WEIGHTS & REPLICATION SCRIPTS:
   - code/
     ├── cerca/
     │   ├── algorithm.py                        (Core CERCA inference with fan-in normalization)
     │   ├── evaluate.py                         (Evaluation benchmark, degradation ladder & tests)
     │   ├── microrca.py                         (Re-implemented MicroRCA baseline with imputation)
     │   ├── collect_docker_metrics.py           (Live Docker container stats extractor)
     │   ├── learned_weights.json                (Trained reliability model weights artifact)
     │   ├── evaluation_metrics_extended.csv     (Detailed empirical benchmark output, N=40)
     │   ├── degradation_robustness.csv          (Degradation ladder results 0%-90%)
     │   ├── ablation_degradation.csv            (Component ablation trajectory data)
     │   └── statistical_analysis_corrected.json (Incident-level paired Wilcoxon test results)
     ├── requirements.txt                        (Python dependencies)
     └── README.md                               (Replication guide)
