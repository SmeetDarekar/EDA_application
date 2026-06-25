"""
abt/analyze_rules.py
─────────────────────────────────────────────────────────────────────────────
Data quality, warning, governance, and readiness rules for a single version.
"""

from typing import Dict, List, Optional
from abt.analysis.columnProfile import ABTProfile, ColumnProfile
from abt.insights.insights import (column_health_score, dataset_readiness_score,
                        build_action_list, _safe)

BLOCKER_COMPLETENESS   = 50.0
BLOCKER_MISMATCH_RATE  = 0.15
SKEW_SYMMETRIC         = 0.5
LEAKAGE_CARDINALITY    = 50
IMBALANCE_NOTABLE      = 1.5
IMBALANCE_SEVERE       = 4.0
TARGET_NAMES           = {"target", "bad", "default", "event", "flag", "label", "y"}


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


def s8_column_health_scores(abt: ABTProfile) -> Dict[str, Dict]:
    row_count = max(abt.row_count, 1)
    return {col.name: column_health_score(col, row_count) for col in abt.columns}


def s9_action_list(abt: ABTProfile, health_scores: Dict,
                    blockers: list, warnings: list, governance: list) -> List[Dict]:
    return build_action_list(abt, blockers, warnings, governance, health_scores)


def s0_readiness_score(health_scores: Dict, readiness_statuses: List[Dict]) -> Dict:
    return dataset_readiness_score(health_scores, readiness_statuses)
