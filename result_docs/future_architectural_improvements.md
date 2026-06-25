# EDA — Future Architectural Improvements & Roadmap

This document outlines key technical roadmap items for extending the Risk Model Exploratory Data Analysis (RMEDA) platform. It is split into enhancements for the metadata-only pipeline and advanced features unlocked by raw dataset access.

---

## 1. Enhancements to the Metadata-Only Pipeline

These improvements can be implemented directly on the current catalog metadata ingestion structure.

### A. Headless/Offline AI via Local LLM Integrations (Ollama)
* **Objective**: Replace external OpenAI APIs with self-contained, zero-cost local runners for headless enterprise deployments.
* **Details**: Update `abt/llm/llm_client.py` and `abt/llm/llm_config.py` to support an offline model host (e.g., `http://localhost:11434/api/chat` running Llama 3 or Mistral). This secures sensitive information catalog metadata locally and ensures consistent service uptime.

### B. Feature Importance Integration vs. Derived Urgency Metrics
* **Objective**: Prevent irrelevant or highly volatile variables from pushing critical predictors out of the Decision View cards.
* **Details**: Expand the metadata ingestion schema to accept true model coefficients or feature importances (e.g., SHAP values, Gini importances, or Permutation importances from training). These actual predictor weights will be used to rank drift signals, ensuring major changes in high-impact features (like `income` or `DTI`) are prioritized first.

### C. In-Context Learning (ICL) few-shot prompts
* **Objective**: Refine LLM narration, boundaries evidence, and action recommendations.
* **Details**: Embed 2–3 concrete input/output examples inside the `_CARD_SYSTEM` system prompts in `abt/llm/llm_prompts.py` for target domains like Fraud or Churn. This acts as a guide to teach the model domain-specific terminology (e.g., chargeback rates vs. customer retention metrics) and ensure style consistency.

### D. Segment-Conditioned Metadata Ingestion
* **Objective**: Enable segment-specific diagnostic recommendations.
* **Details**: Allow the catalog ingestion engine to accept column profiles grouped by categorical variables (e.g., `segment` or `risk_band`). The comparison engine can then track sub-population drift trajectories (e.g. *"DTI increased by 8% specifically within the High Risk segment"*), enabling targeted recommendations instead of global refit instructions.

---

## 2. Advanced Capabilities Unlocked by Raw Dataset Access

If the platform is integrated with raw data lakes or database access, the following features can be added.

### A. Dynamic WoE Binning & Information Value (IV) Calculations
* **Objective**: Calculate proposed binning updates automatically.
* **Details**: Analyze the raw dataset to compute new Weight of Evidence (WoE) boundaries and quantify the performance gain. The UI can display a comparison of the feature's Information Value (IV) before and after re-binning (e.g., *"Refitting bin boundaries for `income` restores its IV from 0.12 back to 0.28"*), giving the validation team clear quantitative evidence to proceed.

### B. Multivariate Drift Detection (Joint Distribution Shift)
* **Objective**: Identify correlation shifts across columns that univariate PSI checks miss.
* **Details**: Compute multivariate statistics (such as Mahalanobis Distance, Covariance matrix differences, or the reconstruction error of a simple Autoencoder) on the raw dataset. This will flag joint distribution shifts (e.g., when `age` and `income` distributions appear stable individually, but their correlation flips).

### C. Real-Time Model Performance Back-Testing (Gini & KS Decay)
* **Objective**: Measure exact performance degradation on consecutive version datasets.
* **Details**: Align raw features with true target labels and deployed model score predictions. Calculate exact validation metrics (such as ROC-AUC, Gini coefficient, and Kolmogorov-Smirnov KS scores) to monitor predictive power decay over time.

### D. Cross-Column Conditional Quality Rules
* **Objective**: Identify record-level logical contradictions.
* **Details**: Validate cross-feature logical constraints on raw rows (e.g., *"If `loan_status` is 'defaulted', `days_past_due` must be > 90"*). This flags silent data corruption and pipeline issues that single-column completeness checks cannot detect.
