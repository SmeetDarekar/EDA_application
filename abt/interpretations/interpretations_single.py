"""
abt/interpretations_single.py
─────────────────────────────────────────────────────────────────────────────
Single-version interpretations: feature usability, readiness, preprocessing.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any


def _safe(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        import math
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct(val, default=0.0):
    return round(_safe(val, default), 1)


# ─────────────────────────────────────────────────────────────────────────────
# I1 · Feature usability verdict
# ─────────────────────────────────────────────────────────────────────────────

def i1_feature_verdicts(s2: List[Dict], s3: List[Dict], s4: List[Dict],
                         s7: List[Dict], s8: Dict, row_count: int) -> List[Dict]:
    row_count = max(row_count, 1)
    blocker_cols  = {b["column"]: b for b in s2}
    warning_cols  = {w["column"]: w for w in s3}
    gov_cols      = {g["column"]: g for g in s4}
    dist_cols     = {d["column"]: d for d in s7}

    seen = {}
    for items, key in [(s2, "column"), (s3, "column"), (s4, "column"), (s7, "column")]:
        for item in items:
            name = item[key]
            if name not in seen:
                seen[name] = True
    for name in s8.keys():
        if name not in seen:
            seen[name] = True

    results = []

    for col_name in seen:
        health_entry = s8.get(col_name, {})
        health_score = _safe(health_entry.get("score"), 50.0)
        blocker = blocker_cols.get(col_name)
        gov     = gov_cols.get(col_name)
        warning = warning_cols.get(col_name)
        dist    = dist_cols.get(col_name)

        if gov:
            risk_types = [r["risk_type"] for r in gov.get("risks", [])]
            if "LEAKAGE" in risk_types:
                leakage_risk = next(r for r in gov["risks"] if r["risk_type"] == "LEAKAGE")
                results.append({
                    "column": col_name, "verdict": "exclude",
                    "reason": (f"{leakage_risk['detail']}. Health score is {health_score}/100 but "
                               f"leakage features inflate AUC/KS by 20-30 points with zero real signal."),
                    "action": "Confirm the data source. If this is a prior model score on the same population, remove before training.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "IDENTIFIER" in risk_types:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Unique identifier — no predictive signal. Including it allows the model to memorise training rows.",
                    "action": "Remove from feature set entirely. Use only for row-level joins.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "PRIVACY" in risk_types:
                results.append({
                    "column": col_name, "verdict": "exclude",
                    "reason": "Marked as private. In regulated models (GDPR, FCRA, ECOA), using sensitive attributes requires documented justification and fairness testing.",
                    "action": "Obtain governance sign-off before including. Run fairness audit if approved.",
                    "effort": "high", "ordered_steps": [],
                })
                continue

        if blocker:
            rules = [r["rule"] for r in blocker.get("reasons", [])]
            if "zero_variance" in rules:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Zero variance — no predictive signal possible.",
                    "action": "Drop before model training. No imputation or transform will fix zero variance.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "high_missing" in rules:
                comp = _pct(blocker.get("completeness", 0))
                missing_n = round((100 - comp) / 100 * row_count)
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": (f"{100 - comp:.1f}% missing ({missing_n:,}/{row_count:,} rows). "
                               f"More than half the data is absent."),
                    "action": "Investigate root cause. If MCAR, drop. If MNAR, create a binary missingness indicator instead.",
                    "effort": "medium", "ordered_steps": [],
                })
                continue
            if "severe_mismatch" in rules:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Severe format mismatch (>15% of rows). Encoding is inconsistent at source.",
                    "action": "Fix at data source before any model use.",
                    "effort": "medium", "ordered_steps": [],
                })
                continue

        steps = []
        if warning:
            for iss in warning.get("issues", []):
                if iss["type"] == "blank_values":
                    blank_n = int(iss["detail"].split()[0].replace(",", ""))
                    steps.append({
                        "type": "quality_fix",
                        "instruction": f"Standardise {blank_n:,} blank strings in '{col_name}' to NaN before any other step.",
                        "reason": "Blank strings are not null — they silently break label encoders and WoE binning.",
                    })
            for iss in warning.get("issues", []):
                if iss["type"] == "partial_missing":
                    comp = _pct(warning.get("completeness", 100))
                    missing_n = round((100 - comp) / 100 * row_count)
                    skew = _safe(dist.get("skewness") if dist else None, 0.0)
                    method = "median" if abs(skew) > 0.5 else "mean"
                    reason = (f"skewness={skew:.2f} — mean is pulled by the {'right' if skew > 0 else 'left'} tail"
                              if abs(skew) > 0.5 else "distribution is near-symmetric")
                    steps.append({
                        "type": "imputation",
                        "instruction": f"Impute '{col_name}' with {method} ({missing_n:,} missing values, {100 - comp:.1f}% of rows).",
                        "reason": f"Use {method} because {reason}.",
                    })
                if iss["type"] == "format_mismatch":
                    steps.append({
                        "type": "quality_fix",
                        "instruction": f"Standardise encoding in '{col_name}' — case or format inconsistencies detected.",
                        "reason": "Case variations inflate cardinality and corrupt WoE bin weights.",
                    })

        if dist:
            skew    = _safe(dist.get("skewness"), 0.0)
            min_val = dist.get("min")
            has_out = dist.get("has_outliers", False)
            n_out   = int(_safe(dist.get("n_outliers"), 0))
            if has_out and n_out > 0:
                steps.append({
                    "type": "outlier_cap",
                    "instruction": f"Winsorise '{col_name}' at 1st/99th percentile — {n_out} outlier(s) detected.",
                    "reason": "Cap outliers before any transformation.",
                })
            if abs(skew) > 1.0:
                if min_val is not None and min_val > 0:
                    transform, t_reason = "log", f"min={min_val} > 0 so log is safe."
                elif min_val is not None and min_val == 0:
                    transform, t_reason = "log1p", "min=0 — log1p(x) handles zeros safely."
                elif min_val is not None and min_val < 0:
                    transform, t_reason = "reflect then log1p", f"min={min_val} < 0 — reflect then log1p."
                else:
                    transform, t_reason = "log1p", "min unknown — use log1p as a safe default."
                if skew < -1.0:
                    transform, t_reason = "reflect then log1p", f"Left-skewed (skewness={skew:.2f}). Reflect first."
                steps.append({
                    "type": "transformation",
                    "instruction": f"Apply {transform} to '{col_name}' after imputation and outlier capping.",
                    "reason": t_reason + " Not needed for tree-based models.",
                })

        if steps:
            issues_summary = []
            if warning:
                for iss in warning.get("issues", []):
                    issues_summary.append(iss["detail"])
            if dist and abs(_safe(dist.get("skewness"), 0.0)) > 1.0:
                issues_summary.append(f"skewness={_safe(dist.get('skewness'), 0.0):.2f}")
            results.append({
                "column": col_name, "verdict": "fix_then_use",
                "reason": "; ".join(issues_summary) if issues_summary else "Data quality or distribution issues require preprocessing.",
                "action": f"{len(steps)} preprocessing step(s) required before use. See ordered steps below.",
                "effort": "low" if len(steps) == 1 else "medium",
                "ordered_steps": steps,
            })
            continue

        results.append({
            "column": col_name, "verdict": "use",
            "reason": f"No blockers, governance flags, or significant data quality issues. Health score: {health_score}/100.",
            "action": "Include in feature set directly.",
            "effort": "none", "ordered_steps": [],
        })

    order = {"exclude": 0, "drop": 1, "fix_then_use": 2, "use": 3}
    results.sort(key=lambda x: order.get(x["verdict"], 4))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# I2 · Training readiness
# ─────────────────────────────────────────────────────────────────────────────

def i2_training_readiness(s6: Optional[Dict], s2: List[Dict], s1: Dict,
                           s0: Optional[Dict] = None) -> Dict:
    row_count    = max(int(_safe(s1.get("row_count"), 1)), 1)
    blocker_cols = {b["column"] for b in s2}

    if s0 is not None and s0.get("label") == "not_ready":
        return {
            "training_ready": False,
            "blocker": f"Overall dataset readiness is {s0['score']}/100 (not_ready). Fix blocked feature columns first.",
            "minority_count": None, "imbalance_strategy": "Fix blocked feature columns first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "A readiness score below 45 means too many features are unusable.",
        }

    if not s6:
        return {
            "training_ready": False,
            "blocker": "No target column detected. Specify target_col on the analysis page.",
            "minority_count": None, "imbalance_strategy": None,
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Cannot assess training readiness without a target column.",
        }

    if s6.get("error"):
        return {
            "training_ready": False, "blocker": s6["error"],
            "minority_count": None, "imbalance_strategy": None,
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Target column statistics are unavailable.",
        }

    target_col   = s6.get("column", "target")
    event_rate   = _safe(s6.get("event_rate"), 0.0)
    ratio        = _safe(s6.get("imbalance_ratio"), 1.0)
    balance_lbl  = s6.get("balance_label", "unknown")
    completeness = _safe(s6.get("completeness"), 100.0)

    if target_col in blocker_cols:
        return {
            "training_ready": False,
            "blocker": f"Target column '{target_col}' has a data quality blocker.",
            "minority_count": None, "imbalance_strategy": "Fix target column first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "A model cannot be trained when the label itself is corrupt.",
        }

    if completeness < 95.0:
        missing_n = round((100 - completeness) / 100 * row_count)
        return {
            "training_ready": False,
            "blocker": f"Target column '{target_col}' is {100 - completeness:.1f}% missing ({missing_n:,} rows).",
            "minority_count": None, "imbalance_strategy": "Resolve missing labels first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Training on incomplete labels produces a model calibrated on a biased sample.",
        }

    minority_count = round(event_rate / 100 * row_count)
    majority_count = row_count - minority_count

    if balance_lbl == "balanced":
        strategy, smote = "Standard stratified train/test split. No class weighting needed.", False
        primary_met, secondary_met = "ROC-AUC", "F1"
        reason = f"Event rate {event_rate}% — balanced distribution. Standard training applies."
    elif balance_lbl == "moderate":
        strategy = "Use class_weight='balanced' and stratified k-fold cross-validation."
        smote = minority_count > 1000
        primary_met, secondary_met = "ROC-AUC", "Precision-Recall AUC"
        reason = (f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). Minority class has {minority_count:,} rows. "
                  + ("Sufficient for class_weight approach." if minority_count > 1000
                     else "Low minority count — prefer class_weight='balanced'."))
    else:
        if minority_count < 300:
            strategy, smote = "class_weight='balanced' only. Do NOT use SMOTE.", False
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). Only {minority_count:,} minority rows. "
                      f"SMOTE would synthesise in a near-empty space. Prioritise getting more real data.")
        elif minority_count < 2000:
            strategy = "class_weight='balanced' + stratified k-fold. SMOTE with caution — max 200% oversampling."
            smote = True
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). {minority_count:,} minority rows — "
                      f"borderline for SMOTE. Cap oversampling at 200% to avoid overfitting.")
        else:
            strategy, smote = "Threshold calibration preferred over SMOTE. Use class_weight='balanced' + stratified k-fold.", False
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). {minority_count:,} minority rows — "
                      f"enough for threshold calibration. SMOTE adds complexity without benefit.")
        primary_met   = "Precision-Recall AUC"
        secondary_met = "ROC-AUC (as secondary — misleading at this imbalance ratio)"

    return {
        "training_ready": True, "blocker": None,
        "minority_count": minority_count, "majority_count": majority_count,
        "event_rate": event_rate, "imbalance_ratio": ratio, "balance_label": balance_lbl,
        "imbalance_strategy": strategy, "smote_recommended": smote,
        "primary_eval_metric": primary_met, "secondary_metric": secondary_met,
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# I3 · Preprocessing checklist
# ─────────────────────────────────────────────────────────────────────────────

def i3_preprocessing_checklist(s3: List[Dict], s5: List[Dict],
                                 s7: List[Dict], i1_verdicts: List[Dict]) -> List[Dict]:
    fix_cols  = {v["column"]: v for v in i1_verdicts if v["verdict"] == "fix_then_use"}
    type_order = ["quality_fix", "imputation", "outlier_cap", "transformation", "encoding"]
    by_type: Dict[str, List] = {t: [] for t in type_order}

    for col_name, verdict in fix_cols.items():
        for step in verdict.get("ordered_steps", []):
            step_type = step.get("type", "quality_fix")
            if step_type not in by_type:
                by_type[step_type] = []
            by_type[step_type].append({
                "column": col_name, "type": step_type,
                "instruction": step["instruction"], "reason": step["reason"],
            })

    all_steps = []
    step_num  = 1
    for step_type in type_order:
        for step in by_type.get(step_type, []):
            all_steps.append({
                "step": step_num, "type": step["type"], "column": step["column"],
                "instruction": step["instruction"], "reason": step["reason"],
            })
            step_num += 1
    return all_steps
