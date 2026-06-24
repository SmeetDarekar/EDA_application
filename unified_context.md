# RMEDA — Unified Project Context File

## 1. Problem Statement
In risk modeling workflows, Analytic Base Tables (ABTs) evolve across development and production stages, but there is no unified system to track health, detect drift, and provide actionable decisions.

## 2. Pain Points
- Manual fragmented analysis
- No clear action guidance
- False positives (data loss vs real drift)
- Lack of lifecycle monitoring
- Governance and explainability gaps

---

## 3. Our Solution
RMEDA is a metadata-driven Exploratory Data Analysis (EDA) and drift detection system that serves as a hybrid decision intelligence engine for credit risk modeling. It analyzes pre-computed statistics and metadata from SAS/Information Catalog (without needing access to raw datasets) to provide model risk diagnostics.

The system is structured around four core pillars:
1. **Analyze Module (S0–S9)**: Performs a single-version health check on the dataset, generating a Readiness Score (0-100), warnings, governance risks, and a prioritized action list.
2. **Compare Module (C0–C11)**: Evaluates consecutive versions (baseline vs. current) to detect schema changes, completeness regressions, distribution shifts, target variable drift, and cardinality explosions.
3. **Three-Tier Interpretation Layer**:
   - **Tier A (Rule-Based)**: Runs deterministic logic and checks standard Basel drift thresholds (PSI, CV, Std, KS) to generate initial recommendations.
   - **Tier B (LLM Narration & Synthesis)**: Translates raw mathematical findings into clean, natural language narratives.
   - **Tier C (Hybrid Final Decisions)**: Adds an **AI Validator** layer (`validate_insights`) that reviews automated findings against the model purpose (e.g., Probability of Default - PD, Loss Given Default - LGD) to prevent logical contradictions (such as recommending to drop critical risk predictors like income).

## VERY IMP
4. Decision View Module: This provides final displayable insights based on all the calculations from the analysis & the comparison sections. This is the unified reresentation of th emost important data changes that the business user should be aware of. 
If this service is to be integrated in any existing systems, decision view section is the key section that can be displayed. The results can either be displayed on a dedicated dashboard or can be triggered as alerts whenever the underlying data changes.
The execution pattern remains open for the integration team. They can either trigger this service after clicking on certain compare option in the service or can design a continuous data obersvation pattern.

---

## 4. Key Architectural Updates & Features

### A. Decision View & AI Drift Signals
RMEDA features a **Decision View** dashboard containing a structured business-insight section, which is enriched by a **3-LLM Prompt Chaining** workflow:
- **Triage (Call 1)**: Ranks detected drift themes by business urgency.
- **Card Synthesis (Call 2)**: Writes individual impact cards. The prompt strictly enforces the required business headline structure:
  > *"It is observed that [feature concept] has shifted towards [higher/lower value group]. This indicates that [consequence on scoring/default behavior]."*
  It uses human-friendly business terms (e.g., "average income") instead of raw database column names (e.g., `inc`).
- **Meta Narrative (Call 3)**: Synthesizes all card headlines into a single connecting portfolio overview sentence.

### B. Fail-Safe Error Diagnostics
If the LLM fails to run (e.g., due to an incorrect API endpoint, missing keys, or timeouts), the system:
1. Falls back gracefully to rule-based cards.
2. Dynamically marks `llm_used` as `False`.
3. Propagates the exact diagnostic reason (e.g., `No API key set`) directly to the UI instead of misattributing placeholder text to successful AI generations.

### C. Developer Automation & Setup
- **Decoupled Secrets**: All configuration endpoints, deployment settings, and credentials are externalized into a `.env` file template.
- **Unified Startup Runners**: PowerShell (`run.ps1`) and batch (`run.bat`) runners automatically verify requirements, initialize the virtual environment, install dependencies, copy environment variables, and launch the Flask application (`http://127.0.0.1:5000/`).
