# RMEDA — Session Context File
# Last updated: Decision Intelligence Layer planning session

---

## 1. Project Identity

**Name:** RMEDA — Risk Modeling Exploratory Data Analysis Service
**Stack:** Python, Flask, Jinja2, Chart.js
**Domain:** Credit risk model validation (ABT metadata analysis)
**Constraint:** Metadata-only — no raw dataset, only pre-computed KPIs from SAS/IC

**System identity (current):**
"A metadata-driven EDA and drift detection system evolving into a hybrid decision intelligence engine for risk modeling."

---

## 2. Architecture Overview

```
Input: ABT metadata (column profiles, statistics, KPIs)
       ↓
Analyze Module (S0–S9)     — single version health check
Compare Module (C0–C11)    — two-version baseline vs current comparison
Interpretation Layer (I4–I9) — rule-based cross-section synthesis
LLM Layer                  — narrative enrichment only, never computes decisions
Business Insights Layer    — 5-slot decision intelligence view (NEW)
       ↓
Output: Technical dashboard (existing) + Decision View (new)
```

---

## 3. Analyze Module (S0–S9)

| Section | Purpose |
|---------|---------|
| S0 | Dataset readiness score (0–100) |
| S1 | Health summary |
| S2 | Blockers — columns requiring action before modeling |
| S3 | Data quality warnings |
| S4 | Governance, privacy, leakage risks |
| S5 | Column readiness classification (ready/caution/drop) |
| S6 | Target variable analysis |
| S7 | Distribution health of numeric features |
| S8 | Per-column health scores |
| S9 | Prioritized action list |

Interpretation layer for analyze: I1 (feature verdicts), I2 (training readiness), I3 (preprocessing checklist)

---

## 4. Compare Module (C0–C11)

**Design principle:** Always 2 versions — baseline vs current.
N-version comparison is technically supported but the decision layer enforces 2-version focus.

| Section | Purpose |
|---------|---------|
| C0 | Overall drift verdict: CLEAR / MONITOR / BACK_TEST_REQUIRED / BLOCK |
| C1 | Version-level diff summary |
| C2 | Schema changes (added/dropped/type-changed columns) |
| C3 | Completeness drift + missingness patterns |
| C4 | Distribution drift (numeric, mean/skew/outlier) |
| C5 | Target variable drift |
| C6 | Data quality regression tracker |
| C7 | Readiness change summary |
| C8 | PSI matrix (union boundaries — Evidently AI approach) |
| C9 | Health score trend across versions |
| C10 | Cardinality explosion detection |
| C11 | Advanced drift suite (FSI, velocity, baseline drift, KS, CV, quantile shift, boundary drift) |

Interpretation layer for compare: I4–I9 (see Section 6)

---

## 5. Drift Metrics (drift_metrics.py)

All computable from metadata only. PSI uses union boundaries (fixes range inflation problem).

| Metric | Purpose |
|--------|---------|
| PSI (union) | Population Stability Index — standard Basel drift signal |
| KS approx | Max CDF deviation from quantiles |
| CV drift | Relative spread change |
| Std drift | Absolute spread change |
| Quantile shift | Q1/Median/Q3 independent movement |
| Boundary drift | Min/max range expansion/compression |
| Kurtosis drift | Tail behaviour change |
| Entropy drift | Categorical diversity change |
| FSI | Feature Stability Index — longitudinal PSI average |
| Drift velocity | PSI slope per version (acceleration signal) |
| Baseline drift | Every version vs V1 (model degradation signal) |

PSI thresholds: < 0.10 stable | 0.10–0.25 monitor | > 0.25 significant shift

---

## 6. Interpretation Layer (interpretations.py)

Rule-based. Pure dict-in, dict-out. No LLM dependency. No ABTProfile imports.

| Function | Reads from | Answers |
|----------|-----------|---------|
| I4 population_shift | C1, C4, C9, drift_suite | Has the training population fundamentally changed? |
| I5 target_stability | C5, C3, C6 | Is the target still measuring the same thing? |
| I6 feature_drift_impact | C3, C4, C8, drift_suite | Which drifted features will hurt model predictions? |
| I7 model_action | C0, I5, I6, drift_suite | Retrain / rebin / recalibrate / hold? |
| I8 pipeline_break_risks | C2, C8, C10, drift_suite | What will silently produce wrong scores? |
| I9 pipeline_health | C3, C6, C9 | Is the data pipeline getting better or worse? |

**I7 decisions:** retrain | rebin | recalibrate | hold (with urgency: immediate / next_cycle / none / pipeline_fix_first)

**Key I6 distinction:** Separates genuine drift from data-loss false positives before recommending model action.

**Root cause taxonomy (used by I4, I5, shared across interpretation):**
- `pipeline_issue` — completeness degraded, mismatches increased
- `sampling_change` — row count changed + broad drift
- `organic_shift` — stable volume + coordinated feature movement
- `label_event` — single-version target jump
- `schema_event` — columns added/dropped/retyped
- `unknown` — drift present, no clear signal

---

## 7. LLM Layer (llm_insights.py + llm_client.py + llm_config.py)

**Role:** Narrator only. Never makes decisions. Never computes metrics.
**Constraint:** LLM receives only computed results (scores, flags, PSI values) — never raw JSON.
**Fallback:** Every LLM call has a hardcoded fallback. LLM failure never breaks the app.

Enrichment points:
- `enrich_analyze`: S0 narrative, S9 action enrichment, S6 target narrative
- `enrich_compare`: C0 verdict narrative, C8 PSI narrative, version story (Feature 10)

**I8 and I1/I2/I3 never receive LLM enrichment** — architectural rule, do not change.

Provider config in `llm_config.py`. Active provider: set `ACTIVE_PROVIDER`. Supports anthropic, openai, azure_openai.

---

## 8. Business Insights Layer (business_insights.py) — NEW

**Purpose:** Converts existing computed results into 5 business insight cards for the Decision View.
**Location:** `abt/business_insights.py`
**Called from:** `run_comparison()` — additive, results["business_insights"] = ...

### Design Principles

1. **5 fixed slots** — structure is fixed, content is fully dynamic
2. **Domain-agnostic** — no hardcoded column names or domain assumptions
3. **Signal-driven** — signals from data drive content, not templates
4. **Stage-aware** — stage changes urgency language and action framing only, not logic
5. **Metadata-only** — reads exclusively from existing results dict

### 5 Slots

| Slot | Reads from | Answers |
|------|-----------|---------|
| population_composition | I4, C4, C8, drift_suite | What kind of customer has changed? |
| target_behavior | I5, C5 | Is the outcome we're predicting still the same? |
| pipeline_quality | I9, C3, C6 | Is the data supply healthy? |
| model_scoring_risk | I6, I7, I8, drift_suite | Will the model produce wrong scores? |
| governance_fairness | C2, C8, C10 + privacy flags | Are there regulatory signals? |

### Governance Slot — Correct Approach
**Do NOT hardcode sensitive column name patterns** (age, income, etc.)
**DO use:** `informationPrivacy=private` flag on drifted columns — this is the metadata-driven signal.
Any column marked private that has PSI > 0.10 is a governance checkpoint.

### 3-Layer Card Structure

```
Layer 1 — Headline (always visible)
    Business statement about what changed in the population
    Written in customer/risk language, not column/metric language

Layer 2 — Evidence (on "See more")
    Which columns changed, which quantile, PSI value,
    mean shift, cardinality change — the exact proof points
    Ranked by impact, not by column order

Layer 3 — Impact + Action (same expanded view)
    Model consequence of the change
    Recommended action framed by stage
    LLM narrative if enrichment ran
```

### Revised Flow (planned — not yet implemented)

```
Step 1: Collect all drift signals across C3, C4, C8, C10, drift_suite
Step 2: Classify root cause per signal (shared utility function)
Step 3: Rank signals by impact (PSI magnitude + health score drop + schema severity)
Step 4: Route top signals into the 5 slots
Step 5: Build headline from actual top signal in that slot
Step 6: Build evidence from ranked proof points
Step 7: Apply stage context to action language
Step 8: Attach LLM narrative if available
```

**Current state:** Initial version of business_insights.py delivered. Logic is correct but Step 1–3 (signal collection, root cause classification, ranking) needs to be extracted into a shared utility. Currently each slot function does its own reading — needs to be unified.

### Stage Context

```
development      → "During development" / "Before finalising the feature set"
back_testing     → "In the back-testing sample" / "Before promoting the model"
pre_deployment   → "In the pre-deployment validation data" / "Before deployment sign-off"
production       → "In the live scoring population" / "Immediate action required"
```

---

## 9. Decision View (decision_view.html + app.py routes)

**Entry point:** "✦ Decision View" button on compare_results.html (after breadcrumb div)
**Route:** POST `/compare/decision` → GET `/compare/decision` (stage switcher)
**Back-link:** "View full technical analysis →" links to compare_results.html

New routes in app.py:
- `compare_run_get` — GET version of compare/run for back-links
- `decision_view` — POST + GET, runs comparison + builds business insights

**wiring in compare.py run_comparison:**
```python
# After LLM enrichment block, before return results:
try:
    from .business_insights import build_business_insights
    stage = cfg.stage if cfg and hasattr(cfg, "stage") else "back_testing"
    results["business_insights"] = build_business_insights(results, stage=stage)
except Exception:
    pass
```

---

## 10. Architectural Rules (do not violate)

1. I8 and I1/I2/I3 never receive LLM enrichment
2. `i6b_narrative` and `i6c_narrative` stored at top-level results keys, not inside list items
3. Adding new tier functions must be additive only — never modify existing functions
4. `cfg` parameter must be present in both `run_comparison` and `run_analysis` signatures
5. Template key names must precisely match (`evidence` vs `detail`, `column` and `detail` bindings)
6. Business insights layer reads only from results dict — never imports ABTProfile or raw data
7. LLM narrates and justifies rule-based decisions — it never makes decisions itself
8. Governance slot uses `informationPrivacy=private` flag — never hardcoded column name patterns
9. Stage parameter changes language only — never changes logic or thresholds
10. 5 slots are fixed — content inside is fully dynamic and signal-driven

---

## 11. Known Bugs Fixed (do not reintroduce)

- Broken variable reference in I9
- `_psi_categorical` argument count error
- Center-shift threshold firing on any non-zero float
- S0/S1 verdict contradictions
- I7 model action not respecting BLOCK verdict
- `dataset_readiness_score` silently excluding blocked columns from denominator
- Duplicate `{% block title %}` errors from appended template sections

---

## 12. What We Cannot Do (metadata-only constraint)

- Segment-level fairness (requires demographic splits)
- Prediction drift (requires model output scores)
- True KS test (approximation only from quantiles)
- Feature importance weights (requires model coefficients)
- Real-time alerting (infrastructure layer, out of scope)

These are hard constraints, not gaps. Do not attempt workarounds.

---

## 13. Next Steps

1. Refactor `business_insights.py` — extract shared signal collection + root cause + ranking utility
2. Make governance slot fully privacy-flag driven (remove name pattern matching)
3. Wire stage into LLM prompts in `llm_insights.py` for stage-aware narratives
4. Test with real ABT versions across all 4 stages








IMP::
We need to add a validator too.
Why:
4
Model Scoring Risk
critical
In the live scoring population, the population has shifted enough that the existing model's learned boundaries no longer apply. 1 feature(s) show genuine distribution change. Retraining is required to restore model reliability.

Evidence
Drift impact — income
Cause: center shift. PSI=3.576 (shift). Mean shifted by -19465.0000. WoE bins anchored to old distribution centre are misaligned. Records falling into wrong bins — wrong score assigned.
Chronically unstable features (FSI < 0.40)
income — drifting consistently across all version pairs. Consider dropping from the feature set.
Impact & Recommended Action
Immediate action required: Event rate jumped 11.0pp in a single version — likely a label definition or coding change. Steps: (1) Confirm the label change with the business team. (2) Retrain exclusively on data from after the label change. (3) Do not use pre-change data. (4) Full model validation required. Avoid: Do not attempt threshold recalibration — the label itself is different.

In the above output we can see that it is suggested to drop the income column. But practically this is a wrong recommendation.
If we are building this model for PD, LGD, etc, then income is one of the most imp columns.
Hence, we need an AI validator for all the outputs that we are providing.
We will pass context and the results to the LLM and then filter the outputs. For context, we can use the purpose of the modeling like PD, LGD, Credit risk model and the subject of analysis like finance, etc. Also, we will provide the stage that we are providing in the compare tab. We will design strict prompts to avoid hallucinations and we will add the guardrails concept too.
