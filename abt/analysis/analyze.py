"""
abt/analyze.py  —  7-section analysis + 3 insight layers for a single ABT version.
Orchestration hub delegating blocker/warning/governance/readiness rules.
"""

from typing import Dict, List, Optional
from abt.analysis.columnProfile import ABTProfile, ColumnProfile
from abt.insights.insights import _safe

# Re-expose config constants and rules from analyze_rules
from abt.analysis.analyze_rules import (
    BLOCKER_COMPLETENESS,
    BLOCKER_MISMATCH_RATE,
    SKEW_SYMMETRIC,
    LEAKAGE_CARDINALITY,
    IMBALANCE_NOTABLE,
    IMBALANCE_SEVERE,
    TARGET_NAMES,
    s2_blockers,
    s3_warnings,
    s4_governance,
    s5_readiness,
    s8_column_health_scores,
    s9_action_list,
    s0_readiness_score,
)


def s1_health_summary(abt: ABTProfile) -> Dict:
    total           = len(abt.columns)
    fully_complete  = sum(1 for c in abt.columns if c.completeness_percent == 100)
    high_missing    = sum(1 for c in abt.columns if c.completeness_percent < BLOCKER_COMPLETENESS)
    privacy_flagged = sum(1 for c in abt.columns if c.information_privacy == "private")
    zero_variance   = sum(1 for c in abt.columns
                          if c.statistical_scale == "unary" or c.cardinality_count <= 1)
    has_mismatches  = sum(1 for c in abt.columns if c.mismatched_count > 0)
    issues = high_missing + zero_variance + has_mismatches
    health = "healthy" if (issues == 0 and privacy_flagged == 0) else ("caution" if issues <= 3 else "critical")
    return {
        "abt_name": abt.abt_name, "snapshot_date": abt.snapshot_date,
        "row_count": abt.row_count, "version": abt.version,
        "total_columns": total, "fully_complete": fully_complete,
        "high_missing": high_missing, "privacy_flagged": privacy_flagged,
        "zero_variance": zero_variance, "has_mismatches": has_mismatches,
        "overall_health": health,
    }


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


def run_analysis(abt: ABTProfile, target_col: Optional[str] = None,
                  use_llm: bool = True, cfg = None) -> Dict:
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
    # Interpretation layer (I1–I3 for analyze)
    try:
        from abt.interpretations.interpretations import run_interpretations
        results = run_interpretations(results)
    except Exception:
        pass
    if use_llm:
        try:
            from abt.llm.llm_insights import enrich_analyze
            results = enrich_analyze(results)
        except Exception:
            pass  # LLM enrichment is always optional
    return results