# EDA — In-Depth Data and Code Flow Diagrams

This document provides a detailed walkthrough of the final codebase architecture, mapping exactly how data flows from initial metadata ingestion to the final AI-validated Decision View cards.

![System Architecture Diagram](C:/Users/ad50425/.gemini/antigravity-ide/brain/c60282fe-0688-462f-b30e-d1fd86bfc1b6/system_architecture_diagram_1782370642533.png)


---

## 1. End-to-End System Dataflow

Below is the complete data lifecycle of a table comparison.

```mermaid
flowchart TD
    subgraph Ingestion Layer
        A1[SAS Information Catalog Metadata] -->|POST JSON| A2[API Endpoint /api/ingest in app.py]
        A2 -->|Table name & payload| A3[Ingest Catalog abt/analysis/registry.py]
        A3 -->|Save snapshot JSON| A4[(Metadata Registry datadump/)]
    end

    subgraph Core Analysis & Comparison
        A4 -->|Load ABTs| B1[Load Profile abt/analysis/columnProfile.py]
        B1 -->|ABTProfile Object| B2[Run Analysis abt/analysis/analyze.py]
        B1 -->|Compare consecutive versions| B3[Run Comparison abt/comparison/compare.py]
    end

    subgraph Interpretations & Rules
        B2 -->|Tier A Heuristics| C1[Interpret S0-S9 abt/analysis/analyze_rules.py]
        B3 -->|Tier A Heuristics| C2[Interpret C0-C10 abt/comparison/compare_distribution.py]
        C1 -->|Readiness, blockers, tasks| C3[Interpretations Hub abt/interpretations/interpretations.py]
        C2 -->|Schema shifts, PSI matrices| C3
        C3 -->|15 critical/warning/info signals| C4[Final Signal pool]
    end

    subgraph Decision View Pipeline
        C4 -->|Structured metrics & signals| D1[Business Insights abt/insights/business_insights.py]
        D1 -->|Dynamic card slots| D2[Structured Slots abt/insights/business_slots.py]
        C4 -->|Ranked drift columns| D3[Theme Grouping abt/llm/llm_theme_builder.py]
        D3 -->|Drift theme facts| D4[3-LLM Synthesis abt/llm/llm_synthesis.py]
        D4 -->|Call 1: Triage| D5[Triage ranked themes]
        D5 -->|Call 2: Card Synthesis| D6[Narrated drift cards]
        D6 -->|Call 3: Meta Narrative| D7[Single connecting portfolio sentence]
        D2 -->|Combine slots & AI cards| D8[Final 7-card pool]
        D7 -->|Executive summary narrative| D8
        D8 -->|Rule-based checks + LLM review| D9[AI Validator abt/insights/insight_validator.py]
    end

    subgraph Presentation UI
        D9 -->|Enriched validated cards| E1[Render decision_view.html]
    end
```

---

## 2. Code Flow of Individual Modules

### A. Ingestion & Ingest Pipeline
1. **API Ingest**: `app.py` receives a request at `/api/ingest` containing the `table_name` and standard SAS column catalog metadata.
2. **Deterministic Versioning**: `abt/analysis/registry.py` runs `_hash_items` on the columns.
   * If the table is new, it initializes as **Version 1**.
   * If the table exists and the hash matches an existing version, it updates `last_seen` timestamp (reusing the existing version number).
   * If the table exists but the hash is different, it increments to a new version (e.g. **Version 2**).
3. **Data Storage**: Ingested JSON payloads are stored under `datadump/<safe_table_name>/v<version>.json`, and the central `registry.json` is updated.

---

### B. Single-Version Analysis Module (S0–S9)
Called via `run_analysis(abt_profile, target_col, use_llm)` in `abt/analysis/analyze.py`:

```
[Start run_analysis]
   │
   ├── s2_blockers() ──────> Evaluates critical blockers (missing target, completeness < 80%)
   ├── s3_warnings() ──────> Evaluates moderate warnings (completeness 80-95%, high skew > 2)
   ├── s4_governance() ────> Flags columns labeled 'private' in metadata
   ├── s5_readiness() ─────> Runs readiness score assertions
   ├── s8_health() ────────> Computes per-column score (100 minus severity penalty)
   ├── s0_score() ─────────> Calculates overall readiness score (0-100)
   ├── s1_health() ────────> Summarizes total counts (unary, privacy, missingness)
   ├── s6_target() ────────> Evaluates target class imbalance ratio and skewness
   ├── s7_dist_health() ───> Suggests linear transforms (log/sqrt) for numeric skews
   └── s9_action_list() ───> Forms prioritized tasks checklist (blockers first, then warnings)
```

---

### C. Multi-Version Comparison Module (C0–C10)
Called via `run_comparison(abt_profiles_list, use_llm, domain, abt_purpose, stage)` in `abt/comparison/compare.py`:

```
[Start run_comparison]
   │
   ├── c1_version_summary() ───> Compiles compared versions table and metadata
   ├── c2_schema_changes() ────> Detects added, dropped, or changed datatypes
   ├── c3_completeness() ──────> Analyzes missingness patterns (newly_missing, sparse)
   ├── c4_distribution() ──────> Calculates mean shifts and standard deviation changes
   ├── c5_target_drift() ──────> Computes event rate percentage-point shifts
   ├── c10_cardinality() ──────> Flags value category counts increases (cardinality expansions)
   ├── c8_psi_matrix() ────────> Calculates full pairwise version PSI matrices
   ├── c9_health_score() ──────> Tracks readiness score trajectory
   └── c0_verdict() ───────────> Issuing final verdict:
                                   * BLOCK (if readiness score drops > 15 points or blockers found)
                                   * BACK_TEST_REQUIRED (if target rate shifts or critical feature PSI > 0.25)
                                   * CLEAR (if stable)
```

---

### D. Decision View Section (Deep Dive)
This is the core decision intelligence module called in `app.py` after `run_comparison` has completed. It runs the **3-LLM Prompt Chaining** engine to synthesize drift themes:

```mermaid
sequenceDiagram
    autonumber
    participant app as app.py
    participant bi as business_insights.py
    participant ts as llm_synthesis.py
    participant client as llm_client.py
    participant val as insight_validator.py

    app->>bi: build_business_insights(results, stage)
    bi->>bi: collect_signals(results)
    bi->>ts: synthesise_drift_insights_v2(results, domain, purpose, stage)
    Note over ts: Group signals into themes (llm_theme_builder.py)
    
    ts->>client: LLM Call 1 (Triage System Prompt)
    client-->>ts: Urgent ranked theme IDs (JSON)
    
    loop Per Theme (Up to 5)
        ts->>client: LLM Call 2 (Card System Prompt)
        Note over client: Enforces exact structure: "It is observed that [feature concept] has shifted towards [precise business shift]. This indicates that [specific business consequence]."
        client-->>ts: Individual drift card JSON
    end
    
    ts->>client: LLM Call 3 (Meta System Prompt)
    client-->>ts: 1-sentence executive summary
    
    ts-->>bi: Enriched cards and meta summary
    bi->>bi: Combine with structured slots (target, pipeline, model risk, governance)
    bi->>bi: Run _reorder() (Critical target/pipeline cards pinned to front)
    
    bi->>val: validate_insights(insights, results, use_llm)
    Note over val: Pass 1 (Hard Rules): Checks logical alignment (e.g. if decision is retrain, alert must reflect it)
    Note over val: Pass 2 (LLM Review): Double-checks cards for compliance, corrects inconsistencies
    
    val-->>app: Validated 7 business cards
    app->>app: Render decision_view.html template
```

#### Detailed Explanation of the 3-LLM Chain:
1. **Triage Call**: Receives the full signal pool grouped into logical themes (e.g., population shift, completeness degradation) and returns an ordered list of theme IDs sorted by business urgency for the model purpose.
2. **Card Call**: Initiates a dedicated call per theme. The prompt restricts the headline to an exact business template, utilizing a human-friendly description of the feature concept (e.g. *"average borrower income"*) and translating the numerical shift into a precise customer demographic behavior (e.g. *"lower-income brackets"*). It then custom-tailors the consequence to the model purpose (e.g. PD default underestimation).
3. **Meta Call**: Receives all completed headlines and returns a single portfolio overview sentence summarizing the overall credit risk exposure.
4. **AI Validator**: Inspects completed cards, checking for logical contradictions (e.g., if a feature like `income` is flagged as critical, it ensures the validation card advises re-binning or monitoring rather than dropping the feature entirely).

---

## 5. Environment & Startup Flow

```mermaid
flowchart TD
    A[Double-click run.bat] --> B[Execute run.ps1]
    B --> C{Python Installed?}
    C -->|No| D[Terminate / Throw Error]
    C -->|Yes| E{Virtual Env exists?}
    E -->|No| F[Create venv via python -m venv]
    E -->|Yes| G[Activate venv]
    F --> G
    G --> H{requirements.txt modified?}
    H -->|Yes| I[Run pip install -r requirements.txt]
    H -->|No| J{Check .env file?}
    I --> J
    J -->|Absent| K[Copy .env.example to .env]
    J -->|Present| L[Start Flask Server: python app.py]
    K --> M[Prompt User to configure .env]
```
