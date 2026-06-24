# Single Version Analysis Guide — RMEDA Dashboard

This guide provides a detailed technical and functional breakdown of the **Single Version Analysis** page (`templates/analyze_results.html`). It maps each visual widget/section in the UI back to the backend code and explains the data flow and mathematical/logical purpose.

---

## Page Overview
* **Route**: `/analyze/run` (triggered via POST from `/analyze`)
* **Template**: [analyze_results.html](file:///c:/Smeet_internTask/analysisWork3/templates/analyze_results.html)
* **Controller**: `analyze_run()` in [app.py](file:///c:/Smeet_internTask/analysisWork3/app.py)
* **Core Entrypoint**: `run_analysis()` in [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)

---

## 1. Score & Summary Metrics

### S0 · Dataset Readiness Score
* **UI Location**: Headline radial gauge / badge at the top right.
* **What it does**: Computes a single weighted composite readiness score (0–100) representing the overall usability of the dataset.
* **What it tells**: If the score is below the minimum threshold (e.g. 45/100), the dataset is considered blocked (`NOT READY`) for training.
* **Backend Logic**:
  * **File**: [abt/insights.py](file:///c:/Smeet_internTask/analysisWork3/abt/insights.py)
  * **Function**: `dataset_readiness_score(health_scores, readiness_statuses)`
  * **Math**: Computes a weighted average of individual column health scores. Columns marked as `drop` or with severe blockers are assigned a score of `0.0`.

### S1 · Health Summary Cards
* **UI Location**: 6-column metric grid at the top of the page.
* **What it does**: Tallies overall counts of dataset properties (Total Columns, Fully Complete, High Missing, Privacy Flagged, Zero Variance, Mismatches).
* **What it tells**: Quick diagnostic high-level health status (`healthy`, `caution`, or `critical`).
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s1_health_summary(abt)`

---

## 2. Technical Quality Checks

### S2 · Columns Requiring Action Before Modeling (Blockers)
* **UI Location**: Blocker Checklist card (highlighted in red).
* **What it does**: Flags columns that contain critical defects which prevent safe risk modeling.
* **What it tells**: Tells developers exactly which fields must be dropped or repaired before proceeding.
* **Blocker Criteria**:
  1. Completeness below 50%.
  2. Unary scale (zero variance / single category).
  3. Mismatch rate (e.g., character values in a numeric field) exceeding 15%.
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s2_blockers(abt)`

### S3 · Data Quality Warnings
* **UI Location**: Warnings checklist card (highlighted in amber).
* **What it does**: Flags non-critical but notable defects (e.g. blank strings, partial missingness, minor format mismatches).
* **What it tells**: Recommends preprocessing adjustments such as standard imputation or string trimming.
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s3_warnings(abt)`

### S4 · Governance, Privacy & Leakage Risks
* **UI Location**: Governance & Leakage card.
* **What it does**: Checks for identifiers, sensitive protected attributes (`informationPrivacy=private`), and target leakage features.
* **What it tells**: Warns of regulatory compliance violations or target leakage features (which artificially inflate model performance).
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s4_governance(abt)`

---

## 3. Profiling & Health Scoring

### S5 · Column Readiness Classification
* **UI Location**: Readiness filter buttons & data table grid.
* **What it does**: Classifies each column into status categories (`ready`, `caution`, or `drop`) with detailed reasons.
* **What it tells**: The final deployment checklist of which variables are clean, require warning handling, or must be excluded.
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s5_readiness(abt)`

### S6 · Target Variable Analysis
* **UI Location**: Target Variable Analysis block.
* **What it does**: Computes event rates (for target=1), non-event rates, minority-to-majority imbalance ratios, and assigns an imbalance label (`stable`, `moderate`, `severe`).
* **What it tells**: Diagnoses the difficulty of model training given class skewness and suggests sampling strategies.
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s6_target_analysis(abt, target_col)`

### S7 · Distribution Health
* **UI Location**: Numeric feature cards grid (outlying ranges and IQR boundaries).
* **What it does**: Checks means, skewness values, standard deviation, outliers, and suggests transformations.
* **What it tells**: Identifies highly skewed variables (suggesting log/exp transformations) and highlights extreme outlier counts.
* **Backend Logic**:
  * **File**: [abt/analyze.py](file:///c:/Smeet_internTask/analysisWork3/abt/analyze.py)
  * **Function**: `s7_distribution_health(abt)`

### S8 · Column Health Scores
* **UI Location**: Horizontal bar chart layout.
* **What it does**: Computes a composite 0-100 score for each column.
* **What it tells**: Displays a ranked list of the worst columns in terms of data quality.
* **Backend Logic**:
  * **File**: [abt/insights.py](file:///c:/Smeet_internTask/analysisWork3/abt/insights.py) (called via `s8_column_health_scores` in `analyze.py`)
  * **Function**: `column_health_score(col)`

### S9 · Prioritized Action List
* **UI Location**: Action list cards (sorted by priority).
* **What it does**: Compiles a checklist of actions (e.g. standard imputation, target leakage exclusion).
* **What it tells**: Serves as a step-by-step roadmap of modeling fixes, prioritized by their severity.
* **Backend Logic**:
  * **File**: [abt/insights.py](file:///c:/Smeet_internTask/analysisWork3/abt/insights.py) (called via `s9_action_list` in `analyze.py`)
  * **Function**: `build_action_list(health_scores, blockers, warnings, gov_risks)`

---

## 4. Modeling Interpretations

### I1 · Feature Usability Verdicts
* **UI Location**: Feature Usability Verdicts data table.
* **What it does**: Assigns one of four verdicts to each column: `use`, `fix_then_use`, `drop`, or `exclude`.
* **What it tells**: Final operational verdict indicating whether a variable is model-ready.
* **Backend Logic**:
  * **File**: [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)
  * **Function**: `i1_feature_verdicts(...)`

### I2 · Training Readiness
* **UI Location**: Readiness status timeline + class imbalance strategy box.
* **What it does**: Prescribes evaluation metrics (e.g. Gini/KS vs F1/AUC) and training/validation split ratios.
* **What it tells**: Dictates baseline parameters for the modeling environment setup.
* **Backend Logic**:
  * **File**: [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)
  * **Function**: `i2_training_readiness(...)`

### I3 · Preprocessing Checklist
* **UI Location**: Ordered timeline steps.
* **What it does**: Provides an ordered sequence of data preparation steps.
* **What it tells**: Directs engineers on the exact order of data cleaning operations.
* **Backend Logic**:
  * **File**: [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)
  * **Function**: `i3_preprocessing_checklist(...)`
