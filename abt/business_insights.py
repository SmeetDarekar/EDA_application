# This is the new insighst section which we are building after the reviews from Sachin sir.
# This will focus on narrowing down the insights and removing unnecessary and redundant content from the output

"""
abt/business_insights.py
─────────────────────────────────────────────────────────────────────────────
Decision Intelligence Layer — converts existing computed results into
5 business insights with 3-layer structure each.

Reads from: results dict produced by run_comparison()
            (c0–c11, i4–i9, drift_suite all already computed)
Produces:   results["business_insights"] — list of 5 insight dicts

Each insight:
    {
        "slot":             str,   # fixed slot name
        "headline":         str,   # Layer 1 — customer language, always visible
        "severity":         str,   # "critical" | "warning" | "stable" | "info"
        "evidence":         list,  # Layer 2 — metric proof points
        "impact_and_action":str,   # Layer 3 — model consequence + what to do
        "llm_narrative":    str,   # Layer 3 addition — only if LLM ran
    }

Stage parameter changes framing language:
    "development" | "back_testing" | "pre_deployment" | "production"

Rules:
  - Pure dict-in, dict-out. No ABTProfile. No LLM calls.
  - Every slot always renders — "no issue" is also a valid insight.
  - Never breaks if a key is missing — _get() handles all safely.
  - Additive — called after all existing layers, never replaces them.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Any


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _get(d: dict, *keys, default=None):
    """Safe nested dict access."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


# Stage framing — same finding, different urgency language
_STAGE_CONTEXT = {
    "development":      "During development",
    "back_testing":     "In the back-testing sample",
    "pre_deployment":   "In the pre-deployment validation data",
    "production":       "In the live scoring population",
}

_STAGE_ACTION_PREFIX = {
    "development":      "Before finalising the feature set",
    "back_testing":     "Before promoting the model",
    "pre_deployment":   "Before deployment sign-off",
    "production":       "Immediate action required",
}


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 1 — Population Composition Change
# Source: i4, c4, c8, c3
# Question: Has the type of customer changed?
# ─────────────────────────────────────────────────────────────────────────────

def _insight_population(results: dict, stage: str) -> dict:
    i4          = results.get("i4", {})
    c4          = results.get("c4", [])
    c8          = results.get("c8", {})
    c3          = results.get("c3", {})
    stage_ctx   = _STAGE_CONTEXT.get(stage, "In the current data")
    action_pfx  = _STAGE_ACTION_PREFIX.get(stage, "Action required")

    shift_scope     = i4.get("shift_scope", "stable")
    drifted_count   = _safe(i4.get("drifted_count"), 0)
    total_features  = _safe(i4.get("total_features"), 1)
    likely_cause    = i4.get("likely_cause", "none")
    row_delta_pct   = i4.get("row_delta_pct")
    v1_distance     = i4.get("v1_distance", "unknown")
    drifted_names   = i4.get("drifted_features", [])
    coordinated     = i4.get("coordinated", False)

    # Find the most informative drifted numeric feature for the headline
    # Pick the one with the largest mean delta from c4
    top_feature     = None
    top_mean        = None
    top_direction   = None
    top_old_mean    = None
    top_new_mean    = None

    for col in c4:
        if col.get("overall_severity") not in ("critical", "notable"):
            continue
        flags = col.get("drift_flags", [])
        if not flags:
            continue
        last_flag  = flags[-1]
        mean_delta = _safe(last_flag.get("mean_delta"), 0.0)
        vstats     = col.get("version_stats", [])
        if len(vstats) >= 2:
            old_m = _safe(vstats[0].get("mean"))
            new_m = _safe(vstats[-1].get("mean"))
            if top_feature is None or abs(mean_delta) > abs(_safe(top_mean, 0.0)):
                top_feature   = col["column"]
                top_mean      = mean_delta
                top_direction = "higher" if mean_delta > 0 else "lower"
                top_old_mean  = old_m
                top_new_mean  = new_m

    # PSI critical columns
    psi_critical = _get(c8, "summary", "critical_columns", default=[])
    psi_shift_n  = _safe(_get(c8, "summary", "shift_count"), 0)

    # Build headline
    if shift_scope == "stable":
        headline  = (f"{stage_ctx}, the customer population is consistent "
                     f"with the baseline. No significant composition change detected.")
        severity  = "stable"
    elif top_feature and top_direction:
        col_label = top_feature.replace("_", " ")
        headline  = (
            f"{stage_ctx}, a shift in customer profile has been detected. "
            f"The average {col_label} is now {top_direction} "
            f"({top_old_mean:.2f} → {top_new_mean:.2f}), "
            f"affecting {int(drifted_count)}/{int(total_features)} tracked features. "
            + (f"This appears to be an organic population change — "
               f"a different kind of customer is entering the portfolio."
               if likely_cause == "organic_population_change"
               else f"This may reflect a sampling or data pipeline change.")
        )
        severity  = "critical" if shift_scope == "broad" else "warning"
    else:
        headline  = (
            f"{stage_ctx}, {int(drifted_count)} feature(s) show distribution changes "
            f"compared to baseline. Population scope: {shift_scope}."
        )
        severity  = "warning" if shift_scope == "narrow" else "stable"

    # Evidence — specific proof points
    evidence = []

    if top_feature and top_old_mean is not None:
        evidence.append({
            "label":  f"Mean shift — {top_feature}",
            "detail": f"{top_old_mean:.4f} → {top_new_mean:.4f} "
                      f"(Δ {top_mean:+.4f})",
        })

    # Add quantile-level evidence from drift_suite
    ds = results.get("drift_suite", {})
    for col_name in drifted_names[:3]:
        col_pairs = _get(ds, "consecutive", col_name, default=[])
        if not col_pairs:
            continue
        last = col_pairs[-1] if col_pairs else None
        if not last:
            continue
        qs = last.get("quantile_shift", {})
        if qs.get("applicable") and qs.get("severity") in ("notable", "critical"):
            worst_q   = qs.get("worst_quantile", "")
            max_shift = _safe(qs.get("max_shift"), 0.0)
            evidence.append({
                "label":  f"Quantile shift — {col_name}",
                "detail": f"{worst_q} moved {max_shift:.2f}× IQR. "
                          + qs.get("interpretation", ""),
            })

    for col_name in (psi_critical or [])[:3]:
        for col_entry in c8.get("columns", []):
            if col_entry["column"] == col_name:
                pairs = col_entry.get("pairs", [])
                psi_val = next(
                    (p["psi"] for p in pairs if p.get("psi") is not None), None
                )
                if psi_val is not None:
                    evidence.append({
                        "label":  f"PSI — {col_name}",
                        "detail": f"PSI={psi_val:.3f} (significant shift threshold: 0.25). "
                                  f"Distribution has moved materially from baseline.",
                    })
                break

    if row_delta_pct is not None:
        evidence.append({
            "label":  "Volume change",
            "detail": f"Row count changed by {row_delta_pct:+.1f}% from baseline.",
        })

    if not evidence:
        evidence.append({
            "label":  "No significant evidence",
            "detail": "All distribution metrics are within stable thresholds.",
        })

    # Impact and action
    if shift_scope == "stable":
        impact_and_action = (
            "No model action required on population grounds. "
            "Continue monitoring in the next version cycle."
        )
    elif likely_cause == "sampling_change":
        impact_and_action = (
            f"{action_pfx}: verify whether the population definition or sampling rules changed. "
            f"If unintentional, fix the upstream filter — the model was not trained on this mix. "
            f"If intentional, re-evaluate whether the training sample still represents the scoring population."
        )
    elif v1_distance == "far":
        impact_and_action = (
            f"{action_pfx}: the current population has drifted far from the training baseline. "
            f"Back-test the existing model urgently. "
            f"If Gini/KS has dropped more than 5 points, a retrain is required."
        )
    else:
        impact_and_action = (
            f"{action_pfx}: {int(drifted_count)} feature(s) show notable drift. "
            f"Review WoE bins for drifted columns before scoring against this version. "
            f"Baseline distance is {v1_distance} — monitor closely."
        )

    return {
        "slot":              "population_composition",
        "title":             "Population Composition",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     _get(results, "c0", "narrative", default=""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 2 — Target / Outcome Behavior
# Source: i5, c5
# Question: Is the outcome we're predicting still the same?
# ─────────────────────────────────────────────────────────────────────────────

def _insight_target(results: dict, stage: str) -> dict:
    i5         = results.get("i5", {})
    c5         = results.get("c5", {})
    stage_ctx  = _STAGE_CONTEXT.get(stage, "In the current data")
    action_pfx = _STAGE_ACTION_PREFIX.get(stage, "Action required")

    target_found  = i5.get("target_found", False)
    drift_type    = i5.get("drift_type")
    er_first      = i5.get("event_rate_first")
    er_last       = i5.get("event_rate_last")
    total_drift   = _safe(i5.get("total_drift_pp"), 0.0)
    max_jump      = _safe(i5.get("max_single_jump_pp"), 0.0)
    label_risk    = i5.get("label_change_risk", False)
    data_loss     = i5.get("data_loss_risk", False)
    target_col    = i5.get("target_column", "target")

    if not target_found:
        return {
            "slot":              "target_behavior",
            "title":             "Target / Outcome Behavior",
            "headline":          "No target column detected in this dataset.",
            "severity":          "info",
            "evidence":          [],
            "impact_and_action": "Specify the target column to enable outcome monitoring.",
            "llm_narrative":     "",
        }

    # Headline
    if drift_type == "stable":
        headline = (
            f"{stage_ctx}, the default/event rate is stable at {er_last}%. "
            f"The outcome distribution matches the baseline — model calibration should hold."
        )
        severity = "stable"

    elif drift_type == "data_loss":
        headline = (
            f"{stage_ctx}, the apparent shift in default rate ({er_first}% → {er_last}%) "
            f"is caused by missing target labels, not a real change in customer behavior. "
            f"The data pipeline has an issue — this is not a model problem."
        )
        severity = "warning"

    elif drift_type == "label_change":
        headline = (
            f"{stage_ctx}, the default rate jumped {max_jump:.1f}pp in a single version "
            f"({er_first}% → {er_last}%). This pattern indicates the outcome definition "
            f"or coding logic has changed — not a shift in customer behavior. "
            f"The existing model is now predicting a different outcome."
        )
        severity = "critical"

    elif drift_type == "organic_gradual":
        direction = "increasing" if total_drift > 0 else "decreasing"
        headline = (
            f"{stage_ctx}, the default rate is gradually {direction} "
            f"({er_first}% → {er_last}%, {total_drift:+.1f}pp total). "
            f"Customer risk appetite is shifting organically. "
            f"The model's rank-ordering is likely still valid but the cutoff score needs recalibration."
        )
        severity = "warning"

    else:  # organic_jump
        headline = (
            f"{stage_ctx}, the default rate has shifted sharply "
            f"({er_first}% → {er_last}%, {total_drift:+.1f}pp). "
            f"A sudden change in customer risk profile has occurred — "
            f"possibly driven by an external economic event or product policy change."
        )
        severity = "critical"

    # Evidence
    evidence = []
    if er_first is not None and er_last is not None:
        evidence.append({
            "label":  "Event rate change",
            "detail": f"Baseline: {er_first}% → Current: {er_last}% "
                      f"(total shift: {total_drift:+.1f}pp)",
        })
    if max_jump > 0:
        evidence.append({
            "label":  "Largest single-version jump",
            "detail": f"{max_jump:.1f}pp in one version transition — "
                      + ("consistent with a label logic change." if label_risk
                         else "consistent with an external event or policy change."),
        })

    pairwise = c5.get("pairwise_drift", []) if c5 else []
    for pw in pairwise:
        sev = pw.get("severity", "stable")
        if sev in ("notable", "critical"):
            evidence.append({
                "label":  f"Drift: {pw.get('from_ver')} → {pw.get('to_ver')}",
                "detail": f"{pw.get('delta_pp', 0):+.2f}pp ({sev}). "
                          + ("Back-testing required." if pw.get("back_test_required") else ""),
            })

    if data_loss:
        evidence.append({
            "label":  "Data loss flag",
            "detail": "Target column completeness is degrading. "
                      "Apparent drift is driven by missing labels, not real behavior change.",
        })

    if not evidence:
        evidence.append({
            "label":  "Target stability",
            "detail": f"Event rate at {er_last}% — within normal range of baseline.",
        })

    impact_and_action = i5.get("action", "Monitor target stability in next cycle.")
    if action_pfx not in impact_and_action:
        impact_and_action = f"{action_pfx}: {impact_and_action}"

    return {
        "slot":              "target_behavior",
        "title":             "Target / Outcome Behavior",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 3 — Data Pipeline Quality
# Source: i9, c3, c6
# Question: Is the data supply healthy?
# ─────────────────────────────────────────────────────────────────────────────

def _insight_pipeline(results: dict, stage: str) -> dict:
    i9         = results.get("i9", {})
    c3         = results.get("c3", {})
    c6         = results.get("c6", [])
    stage_ctx  = _STAGE_CONTEXT.get(stage, "In the current data")
    action_pfx = _STAGE_ACTION_PREFIX.get(stage, "Action required")

    health        = i9.get("pipeline_health", "stable")
    pattern       = i9.get("pattern", "stable")
    escalate      = i9.get("escalate_to_engineering", False)
    affected      = i9.get("affected_columns", [])
    recovering    = i9.get("recovering_columns", [])
    quality_reg   = i9.get("quality_regressing", [])
    score_delta   = i9.get("health_score_delta")
    likely_cause  = i9.get("likely_cause", "")
    first_seen    = i9.get("first_seen_version")

    # Count problem patterns from c3
    rows          = c3.get("rows", [])
    newly_missing = [r["column"] for r in rows if r.get("missing_pattern") == "newly_missing"]
    growing_miss  = [r["column"] for r in rows if r.get("missing_pattern") == "growing_missing"]
    sparse_cols   = [r["column"] for r in rows if r.get("missing_pattern") == "sparse"]

    if health == "stable" and not quality_reg:
        headline = (
            f"{stage_ctx}, the data pipeline is healthy. "
            f"Completeness and quality metrics are consistent with the baseline."
        )
        severity = "stable"

    elif health == "recovering":
        headline = (
            f"{stage_ctx}, {len(recovering)} column(s) that previously had completeness "
            f"issues are now recovering. Pipeline fixes appear to be taking effect."
        )
        severity = "info"

    elif pattern == "systematic":
        headline = (
            f"{stage_ctx}, a systematic data quality degradation has been detected "
            f"across {len(affected)} column(s). "
            + (f"Columns sharing a common prefix are failing together — "
               f"a single upstream feed is likely affected. "
               if "prefix" in likely_cause.lower()
               else "Multiple unrelated columns are degrading simultaneously. ")
            + ("Engineering escalation is required." if escalate else
               "Investigate the upstream data source.")
        )
        severity = "critical"

    elif pattern == "isolated":
        col_list = ", ".join(affected[:3]) + ("..." if len(affected) > 3 else "")
        headline = (
            f"{stage_ctx}, {len(affected)} column(s) have completeness or quality issues: "
            f"{col_list}. "
            f"These are isolated — no broad pipeline failure detected. "
            f"Investigate the specific source for these columns."
        )
        severity = "warning"

    else:
        headline = (
            f"{stage_ctx}, minor data quality signals detected. "
            f"Overall pipeline health is acceptable but should be monitored."
        )
        severity = "info"

    # Evidence
    evidence = []
    if score_delta is not None:
        evidence.append({
            "label":  "Dataset health score change",
            "detail": f"Overall health score changed by {score_delta:+.1f} points "
                      f"from baseline to current version.",
        })
    if newly_missing:
        evidence.append({
            "label":  "Newly missing columns",
            "detail": f"{len(newly_missing)} column(s) were complete in baseline "
                      f"but now have missing values: {', '.join(newly_missing[:5])}.",
        })
    if growing_miss:
        evidence.append({
            "label":  "Growing missingness",
            "detail": f"{len(growing_miss)} column(s) with progressively increasing "
                      f"missing rates: {', '.join(growing_miss[:5])}.",
        })
    if quality_reg:
        evidence.append({
            "label":  "Format/encoding regressions",
            "detail": f"{len(quality_reg)} column(s) with increasing mismatch or blank counts: "
                      f"{', '.join(quality_reg[:5])}.",
        })
    if sparse_cols:
        evidence.append({
            "label":  "Structurally sparse columns",
            "detail": f"{len(sparse_cols)} column(s) have been below 50% completeness "
                      f"throughout: {', '.join(sparse_cols[:5])}. "
                      f"These are structural — not a new regression.",
        })
    if first_seen:
        evidence.append({
            "label":  "First degradation detected",
            "detail": f"Completeness issues first appeared in version: {first_seen}.",
        })

    if not evidence:
        evidence.append({
            "label":  "Pipeline status",
            "detail": "All completeness and quality checks passed. No issues detected.",
        })

    impact_and_action = i9.get("action", "Continue monitoring.")
    if escalate:
        impact_and_action = (
            f"{action_pfx}: escalate to data engineering immediately. "
            + impact_and_action
        )

    return {
        "slot":              "pipeline_quality",
        "title":             "Data Pipeline Quality",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 4 — Model Scoring Risk
# Source: i6, i7, i8, drift_suite
# Question: Will the existing model produce wrong scores on this data?
# ─────────────────────────────────────────────────────────────────────────────

def _insight_model_risk(results: dict, stage: str) -> dict:
    i6         = results.get("i6", [])
    i7         = results.get("i7", {})
    i8         = results.get("i8", [])
    stage_ctx  = _STAGE_CONTEXT.get(stage, "In the current data")
    action_pfx = _STAGE_ACTION_PREFIX.get(stage, "Action required")

    decision        = i7.get("decision", "hold")
    urgency         = i7.get("urgency", "none")
    real_drifts     = [f for f in i6 if f.get("is_real_drift", True)]
    data_loss_only  = i7.get("data_loss_only", False)
    accelerating    = i7.get("accelerating_cols", [])
    unstable        = i7.get("unstable_cols", [])
    critical_risks  = [r for r in i8 if r.get("severity") == "critical"]
    real_drift_n    = len(real_drifts)

    # Headline by decision
    if decision == "hold" and urgency == "none":
        headline = (
            f"{stage_ctx}, the existing model is expected to perform consistently "
            f"on this data. No significant scoring risk detected."
        )
        severity = "stable"

    elif decision == "hold" and data_loss_only:
        headline = (
            f"{stage_ctx}, distribution flags are present but are caused by data "
            f"loss in the pipeline — not real population change. "
            f"The model itself is not at risk. Fix the pipeline first."
        )
        severity = "warning"

    elif decision == "recalibrate":
        headline = (
            f"{stage_ctx}, the model's rank-ordering is likely still valid "
            f"but the decision threshold is now misaligned with the current default rate. "
            f"Scores are being produced, but the cutoff needs recalibration before use."
        )
        severity = "warning"

    elif decision == "rebin":
        col_list = ", ".join(f["column"] for f in real_drifts[:3])
        headline = (
            f"{stage_ctx}, {real_drift_n} feature(s) have drifted in a way that "
            f"makes their WoE bin boundaries stale: {col_list}. "
            f"Affected records are being assigned incorrect scores — "
            f"not because the model is wrong, but because the bins no longer fit the data."
        )
        severity = "warning"

    elif decision == "retrain":
        headline = (
            f"{stage_ctx}, the population has shifted enough that the existing model's "
            f"learned boundaries no longer apply. "
            f"{real_drift_n} feature(s) show genuine distribution change. "
            + (f"Drift is accelerating in: {', '.join(accelerating[:3])}. "
               if accelerating else "")
            + f"Retraining is required to restore model reliability."
        )
        severity = "critical"

    else:
        headline = (
            f"{stage_ctx}, model scoring risk is present. "
            f"Review the detailed action plan below."
        )
        severity = "warning"

    # Evidence
    evidence = []

    for item in real_drifts[:4]:
        evidence.append({
            "label":  f"Drift impact — {item['column']}",
            "detail": f"Cause: {item.get('drift_cause','').replace('_',' ')}. "
                      + item.get("evidence", "") + " "
                      + item.get("model_impact", ""),
        })

    for risk in critical_risks[:2]:
        evidence.append({
            "label":  f"Pipeline break risk — {risk['column']}",
            "detail": risk.get("detail", ""),
        })

    if accelerating:
        evidence.append({
            "label":  "Drift accelerating",
            "detail": f"PSI is increasing >0.05 per version for: "
                      f"{', '.join(accelerating[:5])}. "
                      f"These features are moving fast — model degradation will accelerate.",
        })

    if unstable:
        evidence.append({
            "label":  "Chronically unstable features (FSI < 0.40)",
            "detail": f"{', '.join(unstable[:5])} — drifting consistently across "
                      f"all version pairs. Consider dropping from the feature set.",
        })

    if not evidence:
        evidence.append({
            "label":  "Model risk status",
            "detail": "No genuine drift detected. Scoring pipeline is expected to "
                      "perform as validated.",
        })

    # Steps from i7
    steps = i7.get("steps", [])
    avoid = i7.get("avoid", "")
    steps_text = " ".join(f"({i+1}) {s}" for i, s in enumerate(steps)) if steps else ""
    impact_and_action = (
        f"{action_pfx}: {i7.get('reason', '')} "
        + (f"Steps: {steps_text} " if steps_text else "")
        + (f"Avoid: {avoid}" if avoid else "")
    ).strip()

    return {
        "slot":              "model_scoring_risk",
        "title":             "Model Scoring Risk",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     _get(results, "c8", "narrative", default=""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 5 — Governance & Fairness Checkpoint
# Source: s4-equivalent flags in c2, c8, c10, privacy columns in drift
# Question: Are there regulatory or fairness signals we need to flag?
# ─────────────────────────────────────────────────────────────────────────────

def _insight_governance(results: dict, stage: str) -> dict:
    c2         = results.get("c2", [])
    c8         = results.get("c8", {})
    c10        = results.get("c10", [])
    c3         = results.get("c3", {})
    stage_ctx  = _STAGE_CONTEXT.get(stage, "In the current data")
    action_pfx = _STAGE_ACTION_PREFIX.get(stage, "Action required")

    flags = []

    # Check drift in any column that was previously flagged as private
    # We detect this by checking column names against known sensitive patterns
    sensitive_patterns = (
        "age", "gender", "race", "income", "religion",
        "nationality", "marital", "disability", "pregnant"
    )

    psi_columns = c8.get("columns", [])
    sensitive_drifted = []
    for col_entry in psi_columns:
        col_name  = col_entry["column"].lower()
        worst     = col_entry.get("worst_label", "stable")
        if any(pat in col_name for pat in sensitive_patterns):
            if worst in ("shift", "monitor"):
                sensitive_drifted.append({
                    "column": col_entry["column"],
                    "label":  worst,
                })

    if sensitive_drifted:
        flags.append({
            "type":    "fairness_signal",
            "columns": sensitive_drifted,
            "detail":  (
                f"{len(sensitive_drifted)} potentially sensitive attribute(s) "
                f"have shifted distribution: "
                f"{', '.join(c['column'] for c in sensitive_drifted)}. "
                f"A model trained on the old distribution may produce disparate "
                f"outcomes for these groups."
            ),
        })

    # Cardinality explosions in sensitive categorical columns
    for item in c10:
        col_name = item["column"].lower()
        if any(pat in col_name for pat in sensitive_patterns):
            flags.append({
                "type":    "new_categories_in_sensitive_column",
                "columns": [{"column": item["column"], "label": "critical"}],
                "detail":  (
                    f"New category values appeared in '{item['column']}'. "
                    f"These records will fall into an 'unknown' bin — "
                    f"they may receive systematically different scores, "
                    f"creating an unintentional fairness gap."
                ),
            })

    # Schema: dropped columns that sound sensitive
    last_schema = c2[-1] if c2 else {}
    for col in last_schema.get("dropped", []):
        col_name = col["column"].lower()
        if any(pat in col_name for pat in sensitive_patterns):
            flags.append({
                "type":    "sensitive_column_dropped",
                "columns": [{"column": col["column"], "label": "warning"}],
                "detail":  (
                    f"Column '{col['column']}' was removed. "
                    f"If this was used for fairness monitoring or adverse action "
                    f"explanations, its removal must be documented."
                ),
            })

    # Type-changed sensitive columns
    for col in last_schema.get("type_changed", []):
        col_name = col["column"].lower()
        if any(pat in col_name for pat in sensitive_patterns):
            flags.append({
                "type":    "sensitive_column_type_changed",
                "columns": [{"column": col["column"], "label": "warning"}],
                "detail":  (
                    f"Column '{col['column']}' changed type: "
                    f"{col.get('from_type')} → {col.get('to_type')}. "
                    f"Preprocessing fitted on the old type may produce "
                    f"incorrect encodings for this attribute."
                ),
            })

    # Headline and severity
    if not flags:
        headline = (
            f"{stage_ctx}, no governance or fairness signals detected. "
            f"No sensitive attribute drift, schema changes to protected columns, "
            f"or fairness-relevant cardinality changes were found."
        )
        severity = "stable"
    else:
        n_fairness = sum(1 for f in flags if "fairness" in f["type"] or "sensitive" in f["type"])
        headline = (
            f"{stage_ctx}, {len(flags)} governance checkpoint(s) require attention. "
            + (f"{n_fairness} involve potentially sensitive attributes whose distributions "
               f"have changed — this may create disparate model outcomes across customer groups. "
               if n_fairness > 0 else "")
            + f"These require sign-off before model promotion."
        )
        severity = "critical" if any("fairness_signal" in f["type"] for f in flags) else "warning"

    # Evidence
    evidence = []
    for flag in flags:
        for col_entry in flag.get("columns", []):
            evidence.append({
                "label":  f"{flag['type'].replace('_', ' ').title()} — {col_entry['column']}",
                "detail": flag["detail"],
            })

    if not evidence:
        evidence.append({
            "label":  "Governance status",
            "detail": "All governance and fairness checks passed for this version transition.",
        })

    if not flags:
        impact_and_action = "No governance action required. Continue standard monitoring."
    else:
        impact_and_action = (
            f"{action_pfx}: obtain governance sign-off for the flagged columns before deployment. "
            f"For sensitive attributes with distribution shift, run a fairness evaluation "
            f"comparing approval rates across affected groups. "
            f"Document all changes for regulatory audit trail."
        )

    return {
        "slot":              "governance_fairness",
        "title":             "Governance & Fairness",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# Called from run_comparison() — additive, never breaks existing results
# ─────────────────────────────────────────────────────────────────────────────

def build_business_insights(results: dict, stage: str = "back_testing") -> List[dict]:
    """
    Produce 5 business insight dicts from the existing results dict.
    Safe to call even if interpretation layer (i4–i9) did not run.

    Args:
        results : full results dict from run_comparison()
        stage   : "development" | "back_testing" | "pre_deployment" | "production"

    Returns:
        List of 5 insight dicts, always exactly 5.
    """
    insights = []
    try:
        insights.append(_insight_population(results, stage))
    except Exception as e:
        insights.append(_fallback_insight("population_composition", "Population Composition", str(e)))

    try:
        insights.append(_insight_target(results, stage))
    except Exception as e:
        insights.append(_fallback_insight("target_behavior", "Target / Outcome Behavior", str(e)))

    try:
        insights.append(_insight_pipeline(results, stage))
    except Exception as e:
        insights.append(_fallback_insight("pipeline_quality", "Data Pipeline Quality", str(e)))

    try:
        insights.append(_insight_model_risk(results, stage))
    except Exception as e:
        insights.append(_fallback_insight("model_scoring_risk", "Model Scoring Risk", str(e)))

    try:
        insights.append(_insight_governance(results, stage))
    except Exception as e:
        insights.append(_fallback_insight("governance_fairness", "Governance & Fairness", str(e)))

    return insights


def _fallback_insight(slot: str, title: str, error: str) -> dict:
    return {
        "slot":              slot,
        "title":             title,
        "headline":          "Analysis unavailable for this section.",
        "severity":          "info",
        "evidence":          [{"label": "Error", "detail": error}],
        "impact_and_action": "Re-run comparison to generate this insight.",
        "llm_narrative":     "",
    }


# Severity ordering for sorting (most urgent first)
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "stable": 3}