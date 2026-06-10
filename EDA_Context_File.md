# EDA Service for ABT Monitoring — Context File

## 1. Problem Statement
In risk modeling workflows, Analytic Base Tables (ABTs) evolve across development and production stages, but there is no unified system to track health, detect drift, and provide actionable decisions.

## 2. Pain Points
- Manual fragmented analysis
- No clear action guidance
- False positives (data loss vs real drift)
- Lack of lifecycle monitoring
- Governance and explainability gaps

## 3. Our Solution
A metadata-driven EDA system with:
- Single version analysis (S0–S9)
- Multi-version comparison (C0–C11)
- 3-tier interpretation system:
  - Tier A: Rule-based
  - Tier B: LLM narrative
  - Tier C: Hybrid final decisions

## 4. Architecture
Input: ABT metadata
Processing: Analyze → Compare → Interpretations → Hybrid → LLM
Output: UI dashboard

## 5. Analyze Module (S0–S9)
- S0: Readiness score
- S1: Health summary
- S2: Blockers
- S3: Warnings
- S4: Governance risks
- S5: Readiness
- S6: Target analysis
- S7: Distribution
- S8: Column scores
- S9: Actions

## 6. Compare Module (C0–C11)
- C0: Verdict
- C1: Version summary
- C2: Schema changes
- C3: Missing patterns
- C4: Distribution drift
- C5: Target drift
- C6: Quality regression
- C7: Readiness change
- C8: PSI
- C9: Health trend
- C10: Cardinality drift
- C11: Advanced drift suite

## 7. Interpretation Layers
- Tier A: I4–I9
- Tier B: I4b–I9b
- Tier C: I4c–I9c (final decisions)

## 8. LLM Role
- Only narration
- No computation
- With fallback mechanisms

## 9. UI Design
- Sections → Interpretations → Narratives → Final Decisions

## 10. Key Innovations
- Metadata-only
- Multi-version intelligence
- False positive filtering
- Drift mechanism analysis
- Final decision correction layer

## 11. Value
- Faster analysis
- Clear decisions
- Governance-ready outputs

## 12. Summary
A metadata-driven EDA system that evolves into a hybrid decision intelligence engine for risk modeling.
