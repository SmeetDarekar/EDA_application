"""
abt/analyze.py  —  7-section analysis + 3 insight layers for a single ABT version.
Pure logic. Returns structured dicts consumed by Flask templates.

Insight layers:
  S0 : Dataset Readiness Score   (0–100 headline)
  S8 : Column Health Scores      (per-column composite 0–100)
  S9 : Prioritized Action List   (ranked, impact-first)
"""

from typing import Dict, List, Optional
from .columnProfile import ABTProfile, ColumnProfile
from .insights import (column_health_score, dataset_readiness_score,
                        build_action_list, _safe)

BLOCKER_COMPLETENESS   = 50.0
BLOCKER_MISMATCH_RATE  = 0.15
SKEW_SYMMETRIC         = 0.5
LEAKAGE_CARDINALITY    = 50
IMBALANCE_NOTABLE      = 1.5
IMBALANCE_SEVERE       = 4.0
TARGET_NAMES           = {"target", "bad", "default", "event", "flag", "label", "y"}


def s1_health_summary(abt: ABTProfile) -> Dict:
    total           = len(abt.columns)
    fully_complete  = sum(1 for c in abt.columns if c.completeness_percent == 100)
    high_missing    = sum(1 for c in abt.columns if c.completeness_percent < BLOCKER_COMPLETENESS)
    privacy_flagged = sum(1 for c in abt.columns if c.information_privacy == "private")
    zero_variance   = sum(1 for c in abt.columns
                          if c.statistical_scale == "unary" or c.cardinality_count <= 1)
    has_mismatches  = sum(1 for c in abt.columns if c.mismatched_count > 0)
    issues = high_missing + zero_variance + has_mismatches
    health = "healthy" if (issues == 0 and privacy_flagged == 0) else ("caution" if issues <= 2 else "critical")
    return {
        "abt_name": abt.abt_name, "snapshot_date": abt.snapshot_date,
        "row_count": abt.row_count, "version": abt.version,
        "total_columns": total, "fully_complete": fully_complete,
        "high_missing": high_missing, "privacy_flagged": privacy_flagged,
        "zero_variance": zero_variance, "has_mismatches": has_mismatches,
        "overall_health": health,
    }


def s2_blockers(abt: ABTProfile) -> List[Dict]:
    result = []
    row_count = max(abt.row_count, 1)
    for col in abt.columns:
        reasons = []
        if col.completeness_percent < BLOCKER_COMPLETENESS:
            reasons.append({
                "rule": "high_missing",
                "detail": f"{col.completeness_percent:.1f}% complete — {col.missing_count:,} of {row_count:,} rows missing",
                "action": "Determine if missingness is informative before imputing. Do not use as-is.",
            })
        if col.cardinality_count <= 1 or col.statistical_scale == "unary":
            reasons.append({
                "rule": "zero_variance",
                "detail": f"Only {col.cardinality_count} unique value(s) — zero predictive signal",
                "action": "Drop before model training.",
            })
        mm_rate = col.mismatched_count / row_count
        if mm_rate > BLOCKER_MISMATCH_RATE:
            reasons.append({
                "rule": "severe_mismatch",
                "detail": f"{col.mismatched_count:,} mismatched records ({mm_rate*100:.1f}% of rows)",
                "action": "Fix encoding/format inconsistencies at source before use.",
            })
        if reasons:
            result.append({"column": col.name, "scale": col.statistical_scale,
                            "data_type": col.data_type, "completeness": col.completeness_percent,
                            "reasons": reasons})
    return result


def s3_warnings(abt: ABTProfile) -> List[Dict]:
    blockers  = {b["column"] for b in s2_blockers(abt)}
    row_count = max(abt.row_count, 1)
    result    = []
    for col in abt.columns:
        if col.name in blockers:
            continue
        issues = []
        if col.completeness_percent < 100:
            issues.append({"type": "partial_missing",
                           "detail": f"{100 - col.completeness_percent:.1f}% missing ({col.missing_count:,} rows)",
                           "action": "Investigate root cause. Apply appropriate imputation strategy."})
        if col.mismatched_count > 0:
            issues.append({"type": "format_mismatch",
                           "detail": f"{col.mismatched_count:,} mismatched records ({col.mismatched_count/row_count*100:.1f}%)",
                           "action": "Check for case inconsistencies or encoding variations."})
        if col.blank_value_count > 0:
            issues.append({"type": "blank_values",
                           "detail": f"{col.blank_value_count:,} blank (empty-string) values",
                           "action": "Blank strings ≠ NULL. Handle separately in preprocessing."})
        if issues:
            result.append({"column": col.name, "scale": col.statistical_scale,
                            "data_type": col.data_type, "completeness": col.completeness_percent,
                            "issues": issues})
    return result


def s4_governance(abt: ABTProfile) -> List[Dict]:
    result = []
    for col in abt.columns:
        risks = []
        if col.has_unique_field or col.statistical_scale == "id" or col.uniqueness_percent >= 99.9:
            risks.append({"risk_type": "IDENTIFIER",
                          "detail": f"100% unique ({col.cardinality_count:,} distinct values) — surrogate key",
                          "action": "Use only for row-level joins. Never include as a model feature."})
        if col.information_privacy == "private":
            risks.append({"risk_type": "PRIVACY",
                          "detail": f"informationPrivacy='{col.information_privacy}'",
                          "action": "Requires governance sign-off. Check fairness and explainability obligations."})
        if (col.is_numeric()
                and col.min_val is not None and col.max_val is not None
                and col.min_val >= 0 and col.max_val <= 1
                and col.cardinality_count >= LEAKAGE_CARDINALITY
                and col.statistical_scale not in ("binary", "id", "unary")
                and col.name.lower() not in TARGET_NAMES):
            risks.append({"risk_type": "LEAKAGE",
                          "detail": f"Values bounded [0,1] with {col.cardinality_count} distinct values — resembles probability score",
                          "action": "Verify provenance. If from another model on overlapping data, exclude to prevent leakage."})
        if risks:
            result.append({"column": col.name, "scale": col.statistical_scale,
                            "data_type": col.data_type, "risks": risks})
    return result


def s5_readiness(abt: ABTProfile) -> List[Dict]:
    blockers   = {b["column"] for b in s2_blockers(abt)}
    warnings   = {w["column"] for w in s3_warnings(abt)}
    governance = {g["column"] for g in s4_governance(abt)}
    result     = []
    for col in sorted(abt.columns, key=lambda c: c.ordinal_position):
        if col.name in blockers:
            status = "drop"
            reason = "Blocked: high missing data, zero variance, or severe quality issue."
        elif col.name in governance or col.name in warnings:
            status = "caution"
            parts  = []
            if col.name in governance: parts.append("governance/leakage risk")
            if col.name in warnings:   parts.append("data quality warning")
            reason = "Review required: " + " and ".join(parts) + "."
        else:
            status = "ready"
            reason = "No issues detected — ready for model training."
        result.append({"column": col.name, "data_type": col.data_type,
                        "scale": col.statistical_scale, "completeness": col.completeness_percent,
                        "cardinality": col.cardinality_count, "status": status, "reason": reason})
    return result


def s6_target_analysis(abt: ABTProfile, target_col: Optional[str] = None) -> Optional[Dict]:
    col = None
    if target_col:
        col = abt.get_column(target_col)
    if col is None:
        for name in TARGET_NAMES:
            col = abt.get_column(name)
            if col: break
    if col is None:
        return None
    if not col.is_numeric() or col.mean is None:
        return {"column": col.name, "error": "Statistics not available for target column."}
    er    = _safe(col.mean, 0.0)
    ratio = round((1 - er) / er, 2) if er > 0 else None
    if ratio is None:               balance = "unknown"
    elif ratio < IMBALANCE_NOTABLE: balance = "balanced"
    elif ratio < IMBALANCE_SEVERE:  balance = "moderate"
    else:                           balance = "severe"
    recs = {
        "balanced": ["Class distribution is balanced. Standard training applies."],
        "moderate": ["Use stratified train/test splits.",
                     "Consider class_weight='balanced' if minority recall matters."],
        "severe":   ["Severe imbalance — stratified splits are mandatory.",
                     "Consider SMOTE or threshold tuning.",
                     "Use Precision-Recall AUC rather than ROC-AUC alone."],
        "unknown":  ["Could not determine balance — check target column values."],
    }.get(balance, [])
    return {
        "column": col.name, "cardinality": col.cardinality_count, "scale": col.statistical_scale,
        "event_rate": round(er * 100, 2), "non_event_rate": round((1 - er) * 100, 2),
        "imbalance_ratio": ratio, "balance_label": balance,
        "skewness": round(_safe(col.skewness, 0.0), 4),
        "completeness": col.completeness_percent, "recommendations": recs,
    }


def s7_distribution_health(abt: ABTProfile) -> List[Dict]:
    blockers    = {b["column"] for b in s2_blockers(abt)}
    skip_scales = {"id", "binary", "unary"}
    result      = []
    for col in abt.get_numeric_columns():
        if col.name in blockers or col.statistical_scale in skip_scales or col.skewness is None:
            continue
        skew = _safe(col.skewness, 0.0)
        if abs(skew) < SKEW_SYMMETRIC:
            skew_label, transform = "symmetric", "No transformation needed."
        elif skew > 1.0:
            skew_label, transform = "right-skewed", "Consider log or sqrt transform before linear models."
        elif skew < -1.0:
            skew_label, transform = "left-skewed", "Consider reflect+log transform, or check for data quality issues."
        else:
            skew_label, transform = "mild skew", "Monitor — may need transformation depending on model type."
        prob_like = (col.min_val is not None and col.max_val is not None
                     and col.min_val >= 0 and col.max_val <= 1
                     and col.cardinality_count >= LEAKAGE_CARDINALITY)
        iqr_sym = None
        if None not in (_safe(col.q25), _safe(col.q75), _safe(col.median)):
            lo = _safe(col.median, 0) - _safe(col.q25, 0)
            hi = _safe(col.q75, 0) - _safe(col.median, 0)
            denom = min(lo, hi) if min(lo, hi) > 0 else 0.0001
            iqr_sym = (max(lo, hi) / denom) < 1.5
        result.append({
            "column": col.name, "scale": col.statistical_scale,
            "mean": round(_safe(col.mean, 0), 4), "std": round(_safe(col.std, 0), 4),
            "min": col.min_val, "max": col.max_val,
            "skewness": round(skew, 4), "skew_label": skew_label,
            "has_outliers": col.has_outliers, "n_outliers": col.n_outliers,
            "iqr_symmetric": iqr_sym, "prob_like": prob_like, "transform": transform,
            "q25": col.q25, "q50": col.q50, "q75": col.q75,
        })
    return result


def s8_column_health_scores(abt: ABTProfile) -> Dict[str, Dict]:
    """Column Health Score (0–100) for every column. Covers all columns."""
    row_count = max(abt.row_count, 1)
    return {col.name: column_health_score(col, row_count) for col in abt.columns}


def s9_action_list(abt: ABTProfile, health_scores: Dict,
                    blockers: list, warnings: list, governance: list) -> List[Dict]:
    """Prioritised action list — ranked by severity × modeling impact."""
    return build_action_list(abt, blockers, warnings, governance, health_scores)


def s0_readiness_score(health_scores: Dict, readiness_statuses: List[Dict]) -> Dict:
    """Single dataset-level readiness score (0–100)."""
    return dataset_readiness_score(health_scores, readiness_statuses)


def run_analysis(abt: ABTProfile, target_col: Optional[str] = None,
                  use_llm: bool = True) -> Dict:
    s2 = s2_blockers(abt)
    s3 = s3_warnings(abt)
    s4 = s4_governance(abt)
    s5 = s5_readiness(abt)
    s8 = s8_column_health_scores(abt)
    results = {
        "s0":  s0_readiness_score(s8, s5),
        "s1":  s1_health_summary(abt),
        "s2":  s2,
        "s3":  s3,
        "s4":  s4,
        "s5":  s5,
        "s6":  s6_target_analysis(abt, target_col),
        "s7":  s7_distribution_health(abt),
        "s8":  s8,
        "s9":  s9_action_list(abt, s8, s2, s3, s4),
    }
    if use_llm:
        try:
            from .llm_insights import enrich_analyze
            results = enrich_analyze(results)
        except Exception:
            pass  # LLM enrichment is always optional
    return results