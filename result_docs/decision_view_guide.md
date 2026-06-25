# Decision View Guide — EDA Dashboard

This guide provides a detailed technical and functional breakdown of the **Decision Intelligence View** page (`templates/decision_view.html`). It explains how the 7 structured business cards are generated, how the AI narrative ranking chain operates, and how the validation rules are enforced.

---

## Page Overview
* **Route**: `/compare/decision` (triggered via POST from `compare_results.html` or GET from the stage selector)
* **Template**: [decision_view.html](templates/decision_view.html)
* **Controller**: `decision_view()` in [app.py](app.py)

---

## 1. Phase 1: The 7 Structured Business Cards

The 7 business cards present an executive-level view of the data drift. The list of cards is built by the function `build_business_insights(results, stage)` inside [abt/business_insights.py](abt/business_insights.py).

Cards are dynamically reordered via `_reorder(insights)` to push critical blocker issues (e.g. target definition breaks, data loss) to the front of the list.

### Slot 1, 2, & 3 · Drift Stories
* **Purpose**: Displays detailed root-cause diagnoses for the top three drifted columns.
* **Logic**: Uses [abt/signal_collector.py](abt/signal_collector.py) to compile metrics (PSI, boundary drift, variance shift). It determines the exact mechanism (e.g., center shift, spread change) and creates a headline, evidence, and suggested action.
* **Backend Function**: `_top_drift_insights()` in [business_insights.py](abt/business_insights.py)

### Slot 4 · Target Behavior
* **Purpose**: Evaluates target event-rate shifts and flags definition changes.
* **Logic**: Translates `i5` target stability results into a business explanation.
* **Backend Function**: `_insight_target()` in [business_insights.py](abt/business_insights.py)

### Slot 5 · Pipeline Quality
* **Purpose**: Reviews missing value profiles and flags quality regressions.
* **Logic**: Evaluates `i9` pipeline health and flags if missingness is growing.
* **Backend Function**: `_insight_pipeline()` in [business_insights.py](abt/business_insights.py)

### Slot 6 · Model Scoring Risk
* **Purpose**: Warns of extrapolation errors and scorecard mismatches.
* **Logic**: Evaluates the model action recommendation (`i7`) and pipeline break risks (`i8`).
* **Backend Function**: `_insight_model_risk()` in [business_insights.py](abt/business_insights.py)

### Slot 7 · Governance & Fairness
* **Purpose**: Flags sensitive/private variables (`informationPrivacy=private`) that are drifting.
* **Logic**: Flags any drifted private column to alert compliance officers.
* **Backend Function**: `_insight_governance()` in [business_insights.py](abt/business_insights.py)

---

## 2. Phase 2: AI-Ranked Drift Signals

When the "Use AI" checkbox is selected, the application processes the comparison results using a **3-call LLM chain** to summarize and rank the drift.

### The 3-Call LLM Chain:
1. **Call 1: Drift Triage (Triage)**
   * **Prompt File**: [abt/llm_drift_narratives.py](abt/llm_drift_narratives.py) (`_triage_prompt`)
   * **Action**: Filters the drifted columns down to the most important ones.
2. **Call 2: Narrative Generation (Write-up)**
   * **Prompt File**: [abt/llm_drift_narratives.py](abt/llm_drift_narratives.py) (`_writeup_prompt`)
   * **Action**: Writes a short, business-focused paragraph for each triaged column explaining what the drift looks like and why it matters.
3. **Call 3: Portfolio Synthesis (Executive Summary)**
   * **Prompt File**: [abt/llm_drift_narratives.py](abt/llm_drift_narratives.py) (`_meta_prompt`)
   * **Action**: Generates a single-sentence overview of the entire dataset shift.

* **Backend Function**: `synthesise_drift_insights_v2()` in [abt/llm_drift_narratives.py](abt/llm_drift_narratives.py) (called during `run_comparison()`)

---

## 3. The Validation Layer (`insight_validator.py`)

To ensure the actions recommended on the 7 business cards are accurate and comply with corporate risk standards, they are processed by **[`abt/insight_validator.py`](abt/insight_validator.py)**.

### Pass 1: Hard Rules (Deterministic)
The validator evaluates 6 rules:
* **Rule 1 (Private Drop Blocked)**: Prevents recommending that a private attribute be dropped. Replaces it with a governance review recommendation.
* **Rule 2 (Data-Loss Blocker)**: If the drift is caused by missing values rather than real population change, it blocks model action recommendations and requires a pipeline fix first.
* **Rule 3 (Drift Story Exceeds I7)**: Downgrades individual card recommendations if they exceed the overall `i7` model action decision level.
* **Rule 4 (Verdict Ceiling)**: Enforces that card actions do not exceed the overall comparison verdict limit.
* **Rule 5 (Governance Prefix)**: Prepends regulatory warnings for sensitive attributes.
* **Rule 6 (BLOCK Verdict Override)**: If the overall verdict is `BLOCK`, all model action recommendations are removed. Only pipeline fixes are allowed.

### Pass 2: LLM Review (Optional Verification)
* If `use_llm` is active and a rule is triggered, the validator formats a prompt with the exact rule and the structured facts, asking the LLM to verify and refine the corrected action.
* The LLM response is strictly parsed as JSON to prevent hallucinated text.

* **Backend Function**: `validate_insights()` in [abt/insight_validator.py](abt/insight_validator.py)
