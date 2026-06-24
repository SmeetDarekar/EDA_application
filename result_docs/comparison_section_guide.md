# Version Comparison Guide — RMEDA Dashboard

This guide provides a detailed technical and functional breakdown of the **Version Comparison** page (`templates/compare_results.html`). It maps each comparison section in the UI back to the backend code and explains the data flow, mathematical/logical calculations, and diagnostics.

---

## Page Overview
* **Route**: `/compare/run` (triggered via POST from `/compare/versions`)
* **Template**: [compare_results.html](file:///c:/Smeet_internTask/analysisWork3/templates/compare_results.html)
* **Controller**: `compare_run()` in [app.py](file:///c:/Smeet_internTask/analysisWork3/app.py)
* **Core Entrypoint**: `run_comparison()` in [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)

---

## 1. Top Headline Metrics

### C0 · Comparison Verdict Banner
* **UI Location**: Large color-coded banner at the top of the comparison results.
* **What it does**: Synthesizes target drift, population stability index (PSI), and completeness regressions into a unified operational verdict:
  * `CLEAR` (Green): Low drift, minor changes. Safe.
  * `MONITOR` (Amber): Notable drift or quality regression. Monitor.
  * `BACK_TEST_REQUIRED` (Amber/Red): Target drift detected. Model must be backtested.
  * `BLOCK` (Red): Critical schema change or severe quality deterioration.
* **What it tells**: The final deployment decision/gate for the scoring pipeline.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c0_compare_verdict(c1, c2, c3, c4, c5, c6, c8, c9)`

### C1 · Version strip & Deltas Summary
* **UI Location**: Top timeline boxes and 6-column metric comparison cards.
* **What it does**: Computes row and column counts, column addition/deletion flags, and tallies columns that worsened/improved in readiness.
* **What it tells**: Summarizes volume and layout modifications across versions.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c1_version_summary(abts)`

---

## 2. Structural & Quality Drift

### C2 · Schema Changes
* **UI Location**: Schema Changes details card.
* **What it does**: Identifies added columns, dropped columns, and columns that changed data types or statistical scales.
* **What it tells**: Warns of database changes that would cause the model scoring pipeline to crash.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c2_schema_changes(abts)`

### C3 · Completeness & Missingness Patterns
* **UI Location**: Missingness timeline data grid + color indicators.
* **What it does**: Evaluates missing value trends across all versions and assigns a pattern:
  * `growing_missing`: Completeness steadily degrading (severe pipeline issue).
  * `newly_missing`: Column was complete, but has missing values in the latest version.
  * `sparse`: Always has high missingness.
  * `recovering`: Missingness is decreasing.
  * `stable_missing` / `complete`: Normal profiles.
* **What it tells**: Highlights system/pipeline regressions in data delivery.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c3_completeness_drift(abts, cfg)`

### C4 · Distribution Drift (Numeric Features)
* **UI Location**: Numeric feature stats table (showing means, skews, and outliers).
* **What it does**: Evaluates feature mean shifts, standard deviation drift scores, and outlier introduction.
* **What it tells**: Flags columns whose statistical distributions are shifting.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c4_distribution_drift(abts)`

### C5 · Target Variable Drift
* **UI Location**: Target event-rate cards and timeline line chart.
* **What it does**: Measures shifts in the target event rate across all versions.
* **What it tells**: Flags if the modeling default/bad rates have shifted significantly, which impacts threshold tuning.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c5_target_drift(abts)`

### C6 · Data Quality Regression Tracker
* **UI Location**: Quality Regression details grid.
* **What it does**: Evaluates mismatch rates and empty/blank string profiles.
* **What it tells**: Detects formatting issues introduced in downstream database tables.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c6_quality_regression(abts)`

---

## 3. Stability & Trend Metrics

### C7 · Modeling Readiness Change Summary
* **UI Location**: Column Readiness change log grid.
* **What it does**: Tracks readiness status shifts (e.g. caution → drop) for all columns across versions.
* **What it tells**: Flags columns that are degrading or improving over time.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c7_readiness_change(abts, c2)`

### C8 · Population Stability Index (PSI) Matrix
* **UI Location**: PSI Grid and health score matrices.
* **What it does**: Calculates the pairwise PSI score for consecutive versions:
  * PSI < 0.10: Stable.
  * PSI 0.10 - 0.20: Notable shift (requires monitoring).
  * PSI > 0.20: Severe shift (requires model action/retrain).
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py) & [abt/insights.py](file:///c:/Smeet_internTask/analysisWork3/abt/insights.py)
  * **Function**: `c8_psi_matrix(abts)` calling `_psi_matrix_union(...)` in [abt/drift_metrics.py](file:///c:/Smeet_internTask/analysisWork3/abt/drift_metrics.py)

### C9 · Health Score Trend
* **UI Location**: Line graph / summary metrics.
* **What it does**: Calculates readiness score slopes across versions to evaluate trend direction.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c9_health_score_trend(abts)`

### C10 · Cardinality Drift
* **UI Location**: Schema table flags.
* **What it does**: Evaluates unique value counts.
* **Backend Logic**:
  * **File**: [abt/compare.py](file:///c:/Smeet_internTask/analysisWork3/abt/compare.py)
  * **Function**: `c10_cardinality_drift(abts)`

---

## 4. Multi-Version Interpretations

### I4 · Population Shift Interpretation
* **What it does**: Analyzes if the dataset drift is broad (many columns) or narrow (few columns).
* **Backend Logic**: `i4_population_shift(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)

### I5 · Target Stability Interpretation
* **What it does**: Checks target event rate trends to diagnose definition changes.
* **Backend Logic**: `i5_target_stability(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)

### I6 · Feature Drift Interpretation
* **What it does**: Pinpoints feature drift root causes (e.g. median shift, boundary expansion, std change).
* **Backend Logic**: `i6_feature_drift_impact(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)

### I7 · Model Action Decision
* **What it does**: Recommends `retrain`, `rebin`, `recalibrate`, or `hold`.
* **Backend Logic**: `i7_model_action(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)

### I8 · Pipeline Break Risks
* **What it does**: Flags changes that will break active scorecard scoring.
* **Backend Logic**: `i8_pipeline_break_risks(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)

### I9 · Pipeline Health
* **What it does**: Determines if data degradation is isolated or systematic.
* **Backend Logic**: `i9_pipeline_health(...)` in [abt/interpretations.py](file:///c:/Smeet_internTask/analysisWork3/abt/interpretations.py)
