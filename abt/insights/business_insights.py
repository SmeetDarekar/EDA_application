"""
abt/business_insights.py  (v3 — I6-style root cause per column)
─────────────────────────────────────────────────────────────────────────────
Decision Intelligence Layer orchestration hub.
Key change from v2:
  Root cause is now diagnosed per-column the same way I6 does it.
Delegates target, pipeline, model risk and governance slots to business_slots.py.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional

# Import slot-specific functions
from abt.insights.business_slots import (
    _insight_target,
    _insight_pipeline,
    _insight_model_risk,
    _insight_governance,
)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS (exported/used by business_slots)
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _g(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


# Stage language
_CTX = {
    "development":      "During development",
    "back_testing":     "In the back-testing sample",
    "pre_deployment":   "In the pre-deployment validation data",
    "production":       "In the live scoring population",
}
_ACT = {
    "development":      "Before finalising the feature set",
    "back_testing":     "Before promoting the model",
    "pre_deployment":   "Before deployment sign-off",
    "production":       "Immediate action required",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "stable": 3}


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE ANALYSER  (I6-style, per column)
# ─────────────────────────────────────────────────────────────────────────────

def _analyse_root_cause(sig: dict, results: dict) -> dict:
    """
    Diagnose root cause for one signal using the same logic as I6.
    Reads drift_suite, c4, c3 for the specific column.
    """
    col_name    = sig["column"]
    metric_type = sig["metric_type"]
    miss_pattern = _g(sig, "evidence", "miss_pattern") or \
                   _g(sig, "evidence", "pattern")

    # Schema events — fast path
    if metric_type == "schema":
        ct = _g(sig, "evidence", "change_type", default="changed")
        if ct == "dropped":
            return {
                "drift_cause":   "schema_event",
                "is_real_drift": True,
                "cause_summary": (
                    f"'{col_name}' was removed from the dataset. "
                    f"If this column exists in the deployed model's feature list, "
                    f"the scoring pipeline will receive NaN silently."
                ),
                "model_impact":  (
                    "Silent scoring failure — no error raised. "
                    "Model receives missing value for a feature it was trained on."
                ),
                "fix": (
                    f"Check the deployed model's feature list for '{col_name}'. "
                    "If present, either source the column from an alternative feed "
                    "or retrain without it."
                ),
                "quantitative": {"change_type": "dropped",
                                 "last_completeness": _g(sig, "evidence", "last_completeness")},
            }
        else:
            return {
                "drift_cause":   "schema_event",
                "is_real_drift": True,
                "cause_summary": (
                    f"'{col_name}' changed type: "
                    f"{_g(sig, 'evidence', 'from_type')} → {_g(sig, 'evidence', 'to_type')}. "
                    f"All preprocessing fitted on the old type will produce incorrect outputs."
                ),
                "model_impact":  (
                    "Encoders, scalers, and WoE mappings fitted on the old type "
                    "will silently produce wrong values."
                ),
                "fix": (
                    f"Refit all preprocessing steps for '{col_name}' on the new type. "
                    "Validate output range before scoring."
                ),
                "quantitative": {"from_type": _g(sig, "evidence", "from_type"),
                                 "to_type":   _g(sig, "evidence", "to_type")},
            }

    # Cardinality explosion — fast path
    if metric_type == "cardinality":
        explosions = _g(sig, "evidence", "explosions", default=[])
        e = explosions[-1] if explosions else {}
        return {
            "drift_cause":   "cardinality_explosion",
            "is_real_drift": True,
            "cause_summary": (
                f"New category values appeared in '{col_name}' "
                f"(cardinality: {e.get('from_card')} → {e.get('to_card')}, "
                f"×{e.get('ratio', '?')} increase). "
                f"Records with unseen categories have no WoE weight — "
                f"they fall into an 'unknown' bin."
            ),
            "model_impact":  (
                "Records with new categories receive the 'unknown' bin weight, "
                "which was fitted on a tiny fraction of training data. "
                "Their scores are unreliable."
            ),
            "fix": (
                f"Refit WoE bins or encoder for '{col_name}' on the latest version. "
                "Add an explicit 'other' bucket during binning to handle future unseen values."
            ),
            "quantitative": {"from_card": e.get("from_card"),
                             "to_card":   e.get("to_card"),
                             "ratio":     e.get("ratio")},
        }

    # Step 1: Data loss check (same logic as I6)
    if miss_pattern in ("growing_missing", "newly_missing", "sparse"):
        return {
            "drift_cause":   "data_loss",
            "is_real_drift": False,
            "cause_summary": (
                f"PSI flagged for '{col_name}' but missingness pattern is "
                f"'{miss_pattern}'. The distribution shift is caused by data "
                f"loss, not a real population change."
            ),
            "model_impact":  (
                "No genuine model impact. PSI will normalise once the pipeline "
                "is fixed."
            ),
            "fix": (
                "Fix the upstream data pipeline to restore completeness. "
                "Re-run PSI after fixing — it will likely drop below threshold."
            ),
            "quantitative": {"miss_pattern": miss_pattern},
        }

    # Pull granular metrics from drift_suite for this column
    ds_consecutive = _g(results, "drift_suite", "consecutive", default={})
    col_pairs      = ds_consecutive.get(col_name, [])
    latest_metrics = col_pairs[-1] if col_pairs else {}
    if not latest_metrics:
        latest_metrics = {}

    qs    = latest_metrics.get("quantile_shift", {}) or {}
    bd    = latest_metrics.get("boundary_drift",  {}) or {}
    std_d = latest_metrics.get("std_drift",       {}) or {}

    qs_shifts    = qs.get("shifts", {}) or {}
    median_shift = abs(_safe(qs_shifts.get("Median"), 0.0))
    q1_shift     = abs(_safe(qs_shifts.get("Q1"),     0.0))
    q3_shift     = abs(_safe(qs_shifts.get("Q3"),     0.0))
    upper_shift  = abs(_safe(bd.get("upper_shift"),   0.0))
    lower_shift  = abs(_safe(bd.get("lower_shift"),   0.0))
    std_norm     = _safe(std_d.get("norm_change"),    0.0)

    # Pull mean delta from C4
    c4_list   = results.get("c4", [])
    c4_entry  = next((c for c in c4_list if c["column"] == col_name), None)
    c4_flags  = c4_entry.get("drift_flags", []) if c4_entry else []
    mean_delta = _safe(c4_flags[-1].get("mean_delta"), 0.0) if c4_flags else 0.0

    psi_val = _safe(_g(sig, "evidence", "psi"), 0.0)

    # Step 2: Centre shift (median or mean moved)
    if median_shift > 0.20 or abs(mean_delta) > 0.10:
        quant_parts = []
        if mean_delta != 0:
            quant_parts.append(f"mean shifted {mean_delta:+.4f}")
        if median_shift > 0:
            quant_parts.append(f"median moved {median_shift:.2f}× IQR")
        if q1_shift > 0.10:
            quant_parts.append(f"Q1 shifted {q1_shift:.2f}× IQR")
        if q3_shift > 0.10:
            quant_parts.append(f"Q3 shifted {q3_shift:.2f}× IQR")

        direction = "upward" if mean_delta > 0 else "downward"
        return {
            "drift_cause":   "center_shift",
            "is_real_drift": True,
            "cause_summary": (
                f"The distribution centre of '{col_name}' has moved {direction} "
                f"({', '.join(quant_parts)}). PSI = {psi_val:.3f}. "
                f"WoE bins anchored to the old centre are now assigning records "
                f"to the wrong bin."
            ),
            "model_impact":  (
                "WoE bins and decision cutoffs anchored to the old distribution "
                "centre are now misaligned. Records that used to fall into one bin "
                "are now in the adjacent bin — wrong score assigned."
            ),
            "fix": (
                f"Refit WoE bins for '{col_name}' on the latest version data. "
                "Do not retrain the full model until rebinning is done and "
                "performance re-evaluated on the new validation set."
            ),
            "quantitative": {
                "mean_delta":    round(mean_delta, 4),
                "median_shift":  round(median_shift, 4),
                "q1_shift":      round(q1_shift, 4),
                "q3_shift":      round(q3_shift, 4),
                "psi":           round(psi_val, 4),
            },
        }

    # Step 3: Boundary expansion (new values outside training range)
    if upper_shift > 0.10 or lower_shift > 0.10:
        parts = []
        if upper_shift > 0.10:
            parts.append(
                f"upper boundary expanded {upper_shift*100:.0f}% of base range "
                f"({bd.get('max_base')} → {bd.get('max_new')})"
            )
        if lower_shift > 0.10:
            parts.append(
                f"lower boundary shifted {lower_shift*100:.0f}% of base range "
                f"({bd.get('min_base')} → {bd.get('min_new')})"
            )
        return {
            "drift_cause":   "boundary_expansion",
            "is_real_drift": True,
            "cause_summary": (
                f"The value range of '{col_name}' has expanded: {'; '.join(parts)}. "
                f"New records fall outside the range the model was trained on."
            ),
            "model_impact":  (
                "The model will extrapolate for records outside its training range. "
                "Scores are computed but unreliable at the extremes — no error is raised."
            ),
            "fix": (
                f"Update the capping/winsorisation rule for '{col_name}' to cover "
                "the new range. Inspect records at the new extremes and validate "
                "score reasonableness manually."
            ),
            "quantitative": {
                "upper_shift": round(upper_shift, 4),
                "lower_shift": round(lower_shift, 4),
                "max_base":    bd.get("max_base"),
                "max_new":     bd.get("max_new"),
                "min_base":    bd.get("min_base"),
                "min_new":     bd.get("min_new"),
            },
        }

    # Step 4: Spread change (std changed, centre stable)
    if std_norm > 0.25:
        direction = "widened" if _safe(std_d.get("std_new"), 0) > _safe(std_d.get("std_base"), 0) else "narrowed"
        return {
            "drift_cause":   "spread_change",
            "is_real_drift": True,
            "cause_summary": (
                f"The spread of '{col_name}' has {direction} by {std_norm*100:.0f}% "
                f"(std: {_safe(std_d.get('std_base'), 0):.4f} → "
                f"{_safe(std_d.get('std_new'), 0):.4f}). "
                f"The distribution centre is relatively stable but the variance has changed."
            ),
            "model_impact":  (
                f"Z-score scalers fitted on the old std are now producing incorrect "
                f"normalised values. A {'wider' if direction == 'widened' else 'narrower'} "
                f"distribution means model will "
                f"{'underestimate extreme values' if direction == 'widened' else 'overestimate variance'} "
                f"at scoring time."
            ),
            "fix": (
                f"Refit the scaler for '{col_name}' on the latest version. "
                "No model retrain needed — scaling is a preprocessing step."
            ),
            "quantitative": {
                "std_base":   round(_safe(std_d.get("std_base"), 0), 4),
                "std_new":    round(_safe(std_d.get("std_new"),  0), 4),
                "norm_change":round(std_norm, 4),
                "direction":  direction,
            },
        }

    # Step 5: Fallback — PSI flagged but specific mechanism unclear
    return {
        "drift_cause":   "distribution_shift",
        "is_real_drift": True,
        "cause_summary": (
            f"'{col_name}' has a PSI of {psi_val:.3f} ({_g(sig, 'evidence', 'worst_label', default='shift')}). "
            f"The specific mechanism (centre, spread, or tail) cannot be precisely "
            f"determined from available metadata — full data access would be needed."
        ),
        "model_impact":  (
            "Distribution has shifted materially from the training baseline. "
            "Model predictions for this feature's range may be unreliable."
        ),
        "fix": (
            f"Monitor '{col_name}' in the next version. If PSI remains above "
            "threshold, refit preprocessing and evaluate model performance on "
            "the latest data before scoring."
        ),
        "quantitative": {"psi": round(psi_val, 4)},
    }


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_evidence(sig: dict, rca: dict) -> List[dict]:
    """
    Build ordered evidence rows from signal + root cause analysis.
    Order: primary metric → quantitative proof → supporting metrics → root cause
    """
    rows = []
    col  = sig["column"]
    mt   = sig["metric_type"]
    ev   = sig.get("evidence", {})
    quant = rca.get("quantitative", {})

    # Row 1: Primary metric value
    if mt == "psi":
        psi_val = _safe(ev.get("psi"), 0.0)
        rows.append({
            "label":  f"PSI — {col}",
            "detail": (
                f"PSI = {psi_val:.3f} "
                f"({ev.get('worst_label', 'shift')}). "
                f"Threshold: stable < 0.10 | monitor 0.10–0.25 | shift > 0.25."
            ),
        })
        # If this column also has mean_shift in all_metrics, surface it in Layer 2
        all_m = sig.get("all_metrics", {})
        ms = all_m.get("mean_shift", {})
        if ms and ms.get("old_mean") is not None:
            old_m = _safe(ms.get("old_mean"), 0.0)
            new_m = _safe(ms.get("new_mean"), 0.0)
            delta = _safe(ms.get("mean_delta"), 0.0)
            rows.append({
                "label":  f"Distribution centre — {col}",
                "detail": (
                    f"Mean: {old_m:.4f} → {new_m:.4f} (Δ {delta:+.4f}). "
                    f"Drift score: {_safe(ms.get('drift_score'), 0):.3f} standard deviations."
                    + (" Skewness pattern also flipped." if ms.get("skew_flip") else "")
                ),
            })
    elif mt == "mean_shift":
        old_m = ev.get("old_mean")
        new_m = ev.get("new_mean")
        delta = _safe(ev.get("mean_delta"), 0.0)
        rows.append({
            "label":  f"Mean shift — {col}",
            "detail": (
                f"Mean: {old_m:.4f} → {new_m:.4f} (Δ {delta:+.4f}). "
                f"Drift score: {_safe(ev.get('drift_score'), 0):.3f} standard deviations."
                + (" Skewness pattern also flipped." if ev.get("skew_flip") else "")
            ) if old_m is not None else f"Mean shifted {_safe(ev.get('mean_delta'), 0):+.4f}.",
        })
    elif mt == "missingness":
        rows.append({
            "label":  f"Completeness — {col}",
            "detail": (
                f"Pattern: {ev.get('pattern', 'unknown').replace('_', ' ')}. "
                + (f"Net change: {ev.get('net_delta'):+.1f}pp from baseline. "
                   if ev.get("net_delta") is not None else "")
                + f"Values across versions: {ev.get('values', [])}."
            ),
        })
    elif mt == "cardinality":
        e = (ev.get("explosions") or [{}])[-1]
        rows.append({
            "label":  f"Cardinality explosion — {col}",
            "detail": (
                f"Categories: {e.get('from_card')} → {e.get('to_card')} "
                f"(×{e.get('ratio', '?')} increase). "
                f"Severity: {e.get('severity')}."
            ),
        })
    elif mt == "schema":
        ct = ev.get("change_type", "changed")
        rows.append({
            "label":  f"Schema change — {col}",
            "detail": (
                f"Dropped (last completeness: {ev.get('last_completeness', '?')}%)"
                if ct == "dropped"
                else f"Type changed: {ev.get('from_type')} → {ev.get('to_type')}."
            ),
        })

    # Row 2: Quantitative proof from RCA
    drift_cause = rca.get("drift_cause", "")

    if drift_cause == "center_shift" and quant:
        parts = []
        if quant.get("mean_delta") is not None:
            parts.append(f"mean Δ {_safe(quant['mean_delta']):+.4f}")
        if quant.get("median_shift", 0) > 0:
            parts.append(f"median {quant['median_shift']:.2f}× IQR")
        if quant.get("q1_shift", 0) > 0.10:
            parts.append(f"Q1 {quant['q1_shift']:.2f}× IQR")
        if quant.get("q3_shift", 0) > 0.10:
            parts.append(f"Q3 {quant['q3_shift']:.2f}× IQR")
        if parts:
            rows.append({
                "label":  "Where in the distribution",
                "detail": "Shift detected at: " + ", ".join(parts) + ".",
            })

    elif drift_cause == "boundary_expansion" and quant:
        rows.append({
            "label":  "Range expansion detail",
            "detail": (
                f"Upper: {quant.get('max_base')} → {quant.get('max_new')} "
                f"(+{quant.get('upper_shift', 0)*100:.0f}% of base range). "
                f"Lower: {quant.get('min_base')} → {quant.get('min_new')}."
            ),
        })

    elif drift_cause == "spread_change" and quant:
        rows.append({
            "label":  "Spread change detail",
            "detail": (
                f"Std {quant.get('direction', 'changed')}: "
                f"{quant.get('std_base')} → {quant.get('std_new')} "
                f"({quant.get('norm_change', 0)*100:.0f}% relative change)."
            ),
        })

    # Row 3: Root cause statement
    rows.append({
        "label":  "Root cause",
        "detail": rca.get("cause_summary", ""),
    })

    # Row 4: Model impact
    rows.append({
        "label":  "Model impact",
        "detail": rca.get("model_impact", ""),
    })

    # Row 5: Supporting signals (other metrics for same column, max 2)
    for supp in sig.get("supporting", [])[:2]:
        smt   = supp["metric_type"]
        sev   = supp.get("severity", "")
        interp = (
            _g(supp, "evidence", "metric_detail", "interpretation") or
            _g(supp, "evidence", "interpretation") or ""
        )
        rows.append({
            "label":  f"Also detected — {smt.replace('_', ' ')} ({sev})",
            "detail": interp or f"Severity: {sev}.",
        })

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# HEADLINE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_headline(sig: dict, rca: dict, stage_ctx: str) -> str:
    """
    Layer 1 — pure business consequence language.
    """
    drift_cause = rca.get("drift_cause", "distribution_shift")
    is_real     = rca.get("is_real_drift", True)
    sev         = sig.get("severity", "notable")
    urgency     = "materially" if sev == "critical" else "noticeably"
    quant       = rca.get("quantitative", {})
    root_cause  = sig.get("root_cause", "unknown")

    # Map root cause to plain business explanation of why
    cause_reason = {
        "organic_shift":    "consistent with a change in the customer segment entering the portfolio",
        "sampling_change":  "possibly driven by a change in how the population was selected",
        "pipeline_issue":   "driven by a data supply problem, not a real customer change",
        "schema_event":     "caused by a structural change in the data",
        "unknown":          "the underlying cause requires further investigation",
    }.get(root_cause, "the underlying cause requires further investigation")

    # Data loss — never a real population change
    if not is_real:
        return (
            f"{stage_ctx}, apparent changes in part of the customer profile are caused "
            f"by missing data arriving from the source system. "
            f"This is a data supply problem — no real shift in the customer portfolio "
            f"has occurred and no model action is needed until the pipeline is fixed."
        )

    # Center shift — the typical customer has changed
    if drift_cause == "center_shift":
        mean_delta = _safe(quant.get("mean_delta"), 0.0)
        direction  = "higher" if mean_delta > 0 else "lower"
        return (
            f"{stage_ctx}, the typical customer profile has shifted {urgency} "
            f"toward {direction} values in one of the key scoring features — "
            f"{cause_reason}. "
            f"Customers in this segment may be assigned the wrong risk band by the "
            f"existing model."
        )

    # Boundary expansion — entirely new customer profiles
    if drift_cause == "boundary_expansion":
        return (
            f"{stage_ctx}, a segment of customers with extreme values in one of "
            f"the scoring features has appeared that the model has never been scored "
            f"against. "
            f"Their risk assessments are extrapolations outside the model's learned "
            f"range and cannot be trusted for credit decisions."
        )

    # Spread change — same typical customer, wider or narrower band
    if drift_cause == "spread_change":
        direction   = quant.get("direction", "changed")
        spread_word = "more spread out" if direction == "widened" else "more concentrated"
        return (
            f"{stage_ctx}, the customer population has become {spread_word} "
            f"in one of the key scoring features compared to when the model was built. "
            f"Customers at the edges of this range will receive scores calibrated for "
            f"a different level of variability."
        )

    # Cardinality explosion — new customer types appeared
    if drift_cause == "cardinality_explosion":
        from_c = quant.get("from_card")
        to_c   = quant.get("to_card")
        new_n  = (to_c - from_c) if (from_c and to_c) else "several"
        return (
            f"{stage_ctx}, {new_n} new customer segments have appeared in the "
            f"portfolio that were not present when the model was trained. "
            f"Customers in these new segments will receive a generic fallback "
            f"risk score — not a learned risk assessment."
        )

    # Schema event — feature removed or retyped
    if drift_cause == "schema_event":
        ct = _g(sig, "evidence", "change_type", default="changed")
        if ct == "dropped":
            return (
                f"{stage_ctx}, a feature that the model relies on for scoring "
                f"has been removed from the incoming data. "
                f"Every customer scored against this version will receive an "
                f"unreliable score — the model is missing a required input."
            )
        return (
            f"{stage_ctx}, the format of a feature used in model scoring has changed "
            f"in a way that is incompatible with the trained model. "
            f"Scores produced from this version require validation before use "
            f"in any credit decision."
        )

    # Pipeline issue — data quality, not customers
    if drift_cause == "pipeline_issue":
        return (
            f"{stage_ctx}, data for part of the customer profile is arriving "
            f"incomplete or degraded from the source system. "
            f"This is a data pipeline problem — do not adjust the model until "
            f"the completeness issue is resolved upstream."
        )

    # distribution_shift — generic fallback, still business language
    return (
        f"{stage_ctx}, the customer profile for one of the key scoring features "
        f"has shifted {urgency} from the population the model was built on. "
        f"The model's risk assessments for affected customers may no longer "
        f"reflect their true credit risk."
    )


def _top_drift_insights(signals: List[dict], results: dict, stage: str) -> List[dict]:
    stage_ctx  = _CTX.get(stage, "In the current data")
    action_pfx = _ACT.get(stage, "Action required")

    pop_signals = [s for s in signals if s.get("slot_hint") == "population"]
    insights    = []

    for rank, sig in enumerate(pop_signals[:3]):
        slot_name = f"drift_story_{rank + 1}"
        title     = f"Top Drift Finding {rank + 1}"

        rca = _analyse_root_cause(sig, results)

        headline = _build_headline(sig, rca, stage_ctx)
        evidence = _build_evidence(sig, rca)

        cause   = rca.get("drift_cause", "distribution_shift")
        is_real = rca.get("is_real_drift", True)
        fix     = rca.get("fix", "")

        if not is_real:
            impact_and_action = (
                f"{action_pfx}: this is a data pipeline issue, not a population change. "
                + fix
            )
        elif cause == "center_shift":
            impact_and_action = (
                f"{action_pfx}: {fix} "
                f"This is a rebinning task, not a full retrain — "
                f"rank-ordering of the model is likely still valid."
            )
        elif cause == "boundary_expansion":
            impact_and_action = (
                f"{action_pfx}: {fix} "
                f"Back-test on records in the new range specifically — "
                f"validate that scores are reasonable before promoting."
            )
        elif cause == "spread_change":
            impact_and_action = (
                f"{action_pfx}: {fix} "
                f"This is a preprocessing fix only — no model retrain required."
            )
        elif cause == "cardinality_explosion":
            impact_and_action = (
                f"{action_pfx}: {fix} "
                f"Check all downstream encoders and WoE mappings for this column."
            )
        elif cause == "schema_event":
            impact_and_action = (
                f"{action_pfx}: {fix} "
                f"This must be resolved before any scoring run against this version."
            )
        else:
            impact_and_action = f"{action_pfx}: {fix}"

        sev = sig.get("severity", "notable")
        rag = "critical" if sev == "critical" else "warning" if sev == "notable" else "stable"

        insights.append({
            "slot":              slot_name,
            "title":             title,
            "headline":          headline,
            "severity":          rag,
            "evidence":          evidence,
            "impact_and_action": impact_and_action,
            "llm_narrative":     "",
        })

    return insights


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC ORDERING
# ─────────────────────────────────────────────────────────────────────────────

def _reorder(drift_insights: List[dict], structured: List[dict]) -> List[dict]:
    """
    Order: drift stories first, then structured slots.
    Dynamic adjustment: critical target or pipeline slots move to front.
    """
    target   = next((s for s in structured if s["slot"] == "target_behavior"), None)
    pipeline = next((s for s in structured if s["slot"] == "pipeline_quality"), None)

    pinned  = []
    rem_str = list(structured)

    # Pin critical structured slots to front — replace a drift story slot
    if target and target["severity"] == "critical":
        pinned.append(target)
        rem_str = [s for s in rem_str if s["slot"] != "target_behavior"]

    if pipeline and pipeline["severity"] == "critical":
        pinned.append(pipeline)
        rem_str = [s for s in rem_str if s["slot"] != "pipeline_quality"]

    # Trim drift stories by number of pinned items
    trim    = min(len(pinned), len(drift_insights))
    ordered = pinned + drift_insights[trim:]

    # Append remaining structured slots (no duplicates)
    seen = {i["slot"] for i in ordered}
    for s in rem_str:
        if s["slot"] not in seen:
            ordered.append(s)

    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def build_business_insights(results: dict, stage: str = "back_testing") -> List[dict]:
    """
    Build 7 business insight dicts from the full results dict.
    Always returns exactly 7 — never raises.
    """
    # Ensure signal pool
    signals = results.get("signals")
    if not signals:
        try:
            from abt.insights.signal_collector import collect_signals
            signals = collect_signals(results)
            results["signals"] = signals
        except Exception:
            signals = []

    # Drift stories 1–3
    try:
        drift_insights = _top_drift_insights(signals, results, stage)
    except Exception as e:
        drift_insights = [
            _fallback_card(f"drift_story_{i}", f"Top Drift Finding {i}", str(e) if i == 1 else "")
            for i in range(1, 4)
        ]

    # Structured slots 4–7
    structured = []
    for slot, fn_args in [
        ("target_behavior",    (results, stage)),
        ("pipeline_quality",   (results, signals, stage)),
        ("model_scoring_risk", (results, signals, stage)),
        ("governance_fairness",(results, signals, stage)),
    ]:
        try:
            if slot == "target_behavior":
                structured.append(_insight_target(*fn_args))
            elif slot == "pipeline_quality":
                structured.append(_insight_pipeline(*fn_args))
            elif slot == "model_scoring_risk":
                structured.append(_insight_model_risk(*fn_args))
            elif slot == "governance_fairness":
                structured.append(_insight_governance(*fn_args))
        except Exception as e:
            structured.append(_fallback_card(slot, slot.replace("_", " ").title(), str(e)))

    # Reorder
    try:
        return _reorder(drift_insights, structured)
    except Exception:
        return (drift_insights + structured)[:7]


def _fallback_card(slot: str, title: str, error: str = "") -> dict:
    return {
        "slot":              slot,
        "title":             title,
        "headline":          "Analysis unavailable for this section.",
        "severity":          "stable",
        "evidence":          [{"label": "Note",
                               "detail": error or "Re-run comparison to generate."}],
        "impact_and_action": "Re-run comparison to generate this insight.",
        "llm_narrative":     "",
    }