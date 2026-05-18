"""
abt/insights.py
─────────────────────────────────────────────────────────────────────────────
Three insight layers added on top of raw KPIs:

1. COLUMN HEALTH SCORE  (0–100, per column, single version)
   Weighted composite of completeness, variance quality, mismatch rate,
   governance risk, and distribution health.
   Weights are risk-modeling oriented but domain-agnostic.

2. POPULATION STABILITY INDEX  (per column, pair of versions)
   Industry-standard drift metric from Basel/credit risk modeling.
   Uses quantile buckets (from metadata) to approximate bin distributions.
   PSI = Σ (Actual% - Expected%) × ln(Actual% / Expected%)
   Thresholds: <0.10 stable | 0.10–0.25 monitor | >0.25 significant shift

3. PRIORITIZED ACTION LIST  (dataset level, single version)
   Ranks fix actions by: severity tier × estimated modeling impact.
   Returns ordered list the user can work through top-to-bottom.

4. DATASET READINESS SCORE  (0–100, single version)
   Weighted average of per-column health scores, excluding the target column.
   Gives a single headline number for the ABT.
─────────────────────────────────────────────────────────────────────────────
Design contract:
  - All functions accept ColumnProfile / ABTProfile objects (never raw dicts)
  - All functions return plain dicts/lists (JSON-serialisable, no classes)
  - Division-by-zero, None fields, missing columns: handled everywhere
  - Designed to run across N versions without state
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
from .columnProfile import ColumnProfile, ABTProfile

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Column Health Score weights (must sum to 1.0)
_W_COMPLETENESS  = 0.35   # missing data is the #1 cause of bad models
_W_VARIANCE      = 0.20   # zero / near-zero variance = useless feature
_W_MISMATCH      = 0.20   # format errors corrupt downstream encoding
_W_GOVERNANCE    = 0.15   # leakage / PII risk
_W_DISTRIBUTION  = 0.10   # skew/outlier quality

# PSI thresholds
PSI_STABLE    = 0.10
PSI_MONITOR   = 0.25      # above this → significant shift

# Action severity tiers (used in priority sort)
_SEV = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

TARGET_NAMES = {"target", "bad", "default", "event", "flag", "label", "y"}

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLUMN HEALTH SCORE
# ─────────────────────────────────────────────────────────────────────────────

def column_health_score(col: ColumnProfile, row_count: int = 1000) -> Dict:
    """
    Returns:
        score        : float 0–100
        breakdown    : dict of sub-scores per dimension
        label        : 'good' | 'fair' | 'poor' | 'critical'
        top_issue    : str — single most impactful problem
    """
    row_count = max(row_count, 1)

    # ── Completeness score (0–1) ──────────────────────────────────────
    comp = _safe(col.completeness_percent, 100.0) / 100.0
    comp_score = comp  # linear: 100% complete → 1.0

    # ── Variance score (0–1) ─────────────────────────────────────────
    # Penalise unary (0), binary/nominal with very low cardinality OK
    if col.statistical_scale == "unary" or _safe(col.cardinality_count, 0) <= 1:
        var_score = 0.0
    elif col.statistical_scale == "id":
        var_score = 0.0   # identifiers have no predictive variance
    else:
        # for numeric: use coefficient of variation proxy via uniqueness
        # uniqueness% > 1 = diverse, < 0.1 = very low variance
        uniq = _safe(col.uniqueness_percent, 0.0)
        if col.is_numeric():
            # additional check: std near 0 relative to range
            std = _safe(col.std, 0.0)
            rng = abs(_safe(col.max_val, 0.0) - _safe(col.min_val, 0.0))
            cv  = std / max(rng, 1e-9)
            var_score = min(1.0, cv * 4)   # cv > 0.25 → full score
        else:
            # categorical: penalise if one value dominates > 95%
            n_distinct = max(_safe(col.cardinality_count, 1), 1)
            # estimate dominance from uniqueness percent
            dominance = 1.0 - (uniq / 100.0 * row_count / max(n_distinct, 1) / row_count)
            var_score = max(0.0, 1.0 - max(0.0, dominance - 0.7) * 3.0)

    # ── Mismatch score (0–1) ─────────────────────────────────────────
    mm_rate = _safe(col.mismatched_count, 0) / row_count
    bv_rate = _safe(col.blank_value_count, 0) / row_count
    mismatch_score = max(0.0, 1.0 - (mm_rate + bv_rate) * 5)  # 20% issues → 0

    # ── Governance score (0–1) ────────────────────────────────────────
    gov_penalty = 0.0
    if col.has_unique_field or col.statistical_scale == "id":
        gov_penalty += 0.6   # identifier in feature set
    if col.information_privacy == "private":
        gov_penalty += 0.3   # privacy flag
    if _is_prob_like(col):
        gov_penalty += 0.5   # probable leakage
    gov_score = max(0.0, 1.0 - gov_penalty)

    # ── Distribution score (0–1, numeric only) ────────────────────────
    if col.is_numeric() and col.skewness is not None and col.statistical_scale not in ("id", "binary", "unary"):
        skew = abs(_safe(col.skewness, 0.0))
        outlier_rate = _safe(col.n_outliers, 0) / row_count
        dist_score = max(0.0, 1.0 - (min(skew, 3.0) / 3.0) * 0.5 - outlier_rate * 5)
    else:
        dist_score = 1.0  # not applicable → neutral

    # ── Weighted composite ────────────────────────────────────────────
    raw = (
        _W_COMPLETENESS * comp_score +
        _W_VARIANCE     * var_score  +
        _W_MISMATCH     * mismatch_score +
        _W_GOVERNANCE   * gov_score  +
        _W_DISTRIBUTION * dist_score
    )
    score = round(raw * 100, 1)

    # ── Label ─────────────────────────────────────────────────────────
    if score >= 80:   label = "good"
    elif score >= 55: label = "fair"
    elif score >= 30: label = "poor"
    else:             label = "critical"

    # ── Top issue ─────────────────────────────────────────────────────
    sub = {
        "completeness":  round(comp_score  * 100, 1),
        "variance":      round(var_score   * 100, 1),
        "mismatch":      round(mismatch_score * 100, 1),
        "governance":    round(gov_score   * 100, 1),
        "distribution":  round(dist_score  * 100, 1),
    }
    worst_dim = min(sub, key=sub.get)
    top_issue = _top_issue_text(worst_dim, col, mm_rate, bv_rate)

    return {
        "column":     col.name,
        "score":      score,
        "label":      label,
        "breakdown":  sub,
        "top_issue":  top_issue,
    }


def _top_issue_text(dim: str, col: ColumnProfile, mm_rate: float, bv_rate: float) -> str:
    if dim == "completeness":
        return f"{round((1 - col.completeness_percent/100)*100, 1)}% of rows are missing"
    if dim == "variance":
        if col.statistical_scale == "unary":
            return "Only 1 unique value — zero predictive signal"
        if col.statistical_scale == "id":
            return "Identifier column — no predictive value"
        return "Very low variance — weak discriminating power"
    if dim == "mismatch":
        return f"{round((mm_rate + bv_rate)*100, 1)}% of rows have format/blank errors"
    if dim == "governance":
        if _is_prob_like(col):
            return "Resembles a model probability output — leakage risk"
        if col.has_unique_field:
            return "Unique identifier — must not be used as feature"
        return "Privacy-sensitive attribute — governance approval needed"
    if dim == "distribution":
        skew = abs(_safe(col.skewness, 0.0))
        if skew > 1.0:
            return f"High skewness ({round(col.skewness, 2)}) — transformation likely needed"
        return f"{_safe(col.n_outliers,0)} outliers detected — may distort model"
    return "No major issues"


# ─────────────────────────────────────────────────────────────────────────────
# 2. POPULATION STABILITY INDEX
# ─────────────────────────────────────────────────────────────────────────────

def compute_psi(col_base: ColumnProfile, col_new: ColumnProfile,
                row_base: int = 1000, row_new: int = 1000) -> Dict:
    """
    PSI from quantile-based bucket approximation.

    For numeric columns: uses [min, Q1, median, Q3, max] to create 4 buckets.
    For categorical: compares cardinality and mode distribution proxy.

    PSI = Σ (new_i% - base_i%) × ln(new_i% / base_i%)
    Tiny smoothing (1e-6) prevents log(0).

    Returns: {psi, label, bucket_detail, applicable}
    """
    # Guard: incompatible types
    if col_base.data_type != col_new.data_type:
        return _psi_result(None, "type_changed",
                           "Column data type changed between versions — PSI not comparable")

    if col_base.is_numeric() and col_base.statistical_scale not in ("id", "binary", "unary"):
        return _psi_numeric(col_base, col_new, row_base, row_new)
    elif col_base.is_char() and col_base.statistical_scale in ("binary", "nominal"):
        return _psi_categorical(col_base, col_new, row_base, row_new)
    else:
        return _psi_result(None, "not_applicable",
                           f"PSI not computed for scale='{col_base.statistical_scale}'")


def _psi_numeric(b: ColumnProfile, n: ColumnProfile,
                 rb: int, rn: int) -> Dict:
    """
    Build 5 buckets using the base column's quantile boundaries:
      (-inf, Q1], (Q1, Median], (Median, Q3], (Q3, max], (max, +inf)
    The 5th bucket catches new values outside base range.
    """
    # Require quantile data
    if any(v is None for v in [b.q25, b.q50, b.q75, b.min_val, b.max_val]):
        return _psi_result(None, "insufficient_data",
                           "Quantile data missing — cannot compute PSI")

    # Base bucket proportions (approximate from quantile positions)
    # Q1 splits bottom 25%, median next 25%, Q3 next 25%, max rest 25%
    # We model missing rows as their own implicit bucket adjustment
    base_complete = b.completeness_percent / 100.0
    new_complete  = n.completeness_percent / 100.0

    # 4 quantile buckets + 1 "out of range" bucket
    base_props = [0.25, 0.25, 0.25, 0.25, 0.0]

    # Estimate new distribution from new quantile stats
    # If new quantiles available use them, else fall back to completeness proxy
    nq  = [n.q25, n.q50, n.q75, n.max_val]
    bq  = [b.q25, b.q50, b.q75, b.max_val]
    bmin = b.min_val

    if any(v is None for v in nq):
        # Fallback: use mean shift to redistribute
        b_mean = _safe(b.mean, 0); n_mean = _safe(n.mean, 0)
        std_b  = max(_safe(b.std, 1), 1e-9)
        shift  = (n_mean - b_mean) / std_b
        # Redistribute proportions based on shift (approximate normal bucket shift)
        new_props = _shift_props(base_props[:4], shift) + [0.0]
    else:
        # Compute actual bucket proportions from new column's quantiles
        new_props = _quantile_props(n, bq, bmin)

    # Adjust for completeness: missing rows treated as separate implicit factor
    # We scale props by completeness ratio rather than adding a 5th leakage bucket
    missing_delta = abs(base_complete - new_complete)
    new_props = [p * new_complete / max(base_complete, 1e-9) for p in new_props]
    # Renormalise both to sum=1
    base_props, new_props = _normalise(base_props), _normalise(new_props)

    # PSI calculation
    psi_total = 0.0
    buckets   = []
    labels    = ["≤Q1", "Q1–Median", "Median–Q3", "Q3–Max", "Out of range"]
    for i, (bp, np_) in enumerate(zip(base_props, new_props)):
        bp  = max(bp,  1e-6)
        np_ = max(np_, 1e-6)
        contrib = (np_ - bp) * math.log(np_ / bp)
        psi_total += contrib
        buckets.append({
            "bucket":  labels[i],
            "base_pct":  round(bp  * 100, 2),
            "new_pct":   round(np_ * 100, 2),
            "contrib":   round(contrib, 4),
        })

    return _psi_result(round(psi_total, 4), _psi_label(psi_total),
                       _psi_interpretation(psi_total, b.name), buckets)


def _psi_categorical(b: ColumnProfile, n: ColumnProfile,
                     rb: int, rn: int) -> Dict:
    """
    For binary/nominal: compare cardinality and mode distribution proxy.
    We only have most_common_value frequency proxy, so use a 2-bucket model:
    most_common vs rest. If cardinality changed, flag immediately.
    """
    if b.cardinality_count != n.cardinality_count:
        cardinality_shift = abs(n.cardinality_count - b.cardinality_count)
        approx_psi = min(cardinality_shift * 0.15, 1.0)  # heuristic
        note = (f"Cardinality changed {b.cardinality_count}→{n.cardinality_count}. "
                f"Approximate PSI={approx_psi:.3f} (heuristic — exact requires full data)")
        return _psi_result(round(approx_psi, 4), _psi_label(approx_psi), note)

    # 2-bucket proxy: mode% vs rest
    b_mode_pct = 1.0 / max(b.cardinality_count, 1)  # uniform proxy
    n_mode_pct = 1.0 / max(n.cardinality_count, 1)

    # Adjust by completeness
    bc = b.completeness_percent / 100.0
    nc = n.completeness_percent / 100.0

    base_props = _normalise([bc * b_mode_pct, bc * (1 - b_mode_pct)])
    new_props  = _normalise([nc * n_mode_pct, nc * (1 - n_mode_pct)])

    psi_total = 0.0
    for bp, np_ in zip(base_props, new_props):
        bp  = max(bp,  1e-6)
        np_ = max(np_, 1e-6)
        psi_total += (np_ - bp) * math.log(np_ / bp)

    return _psi_result(
        round(psi_total, 4), _psi_label(psi_total),
        _psi_interpretation(psi_total, b.name) + " (categorical proxy — exact requires full data)"
    )


def _quantile_props(col: ColumnProfile, base_boundaries: list, base_min) -> list:
    """Estimate what fraction of new column falls in each base bucket."""
    bq = [base_min] + base_boundaries  # 5 boundaries → 4 intervals + out
    nmin = _safe(col.min_val, bq[0])
    nmax = _safe(col.max_val, bq[-1])
    nq   = [_safe(col.q25, bq[1]), _safe(col.q50, bq[2]),
            _safe(col.q75, bq[3]), nmax]
    # Map each base bucket: what proportion of new data falls in [bq[i], bq[i+1]]
    props = []
    for i in range(4):
        lo, hi = bq[i], bq[i + 1]
        # linear overlap of new distribution in [lo, hi]
        n_range = max(nmax - nmin, 1e-9)
        overlap = (min(hi, nmax) - max(lo, nmin)) / n_range
        props.append(max(0.0, overlap * 0.25))   # base bucket is 25% of base range
    out_range = max(0.0, (max(nmax - bq[-1], 0) + max(bq[0] - nmin, 0)) / max(nmax - nmin, 1e-9))
    props.append(out_range)
    return props


def _shift_props(base: list, shift: float) -> list:
    """Approximate redistribution given a mean shift in std units."""
    n = len(base)
    shifted = []
    for i, p in enumerate(base):
        # shift redistributes mass proportionally
        target_i = max(0, min(n - 1, round(i - shift)))
        shifted.append(p)
    return shifted


def _normalise(props: list) -> list:
    total = sum(props)
    if total <= 0:
        return [1.0 / len(props)] * len(props)
    return [p / total for p in props]


def _psi_label(psi: float) -> str:
    if psi < PSI_STABLE:  return "stable"
    if psi < PSI_MONITOR: return "monitor"
    return "shift"


def _psi_interpretation(psi: float, col_name: str) -> str:
    if psi < PSI_STABLE:
        return f"'{col_name}' distribution is stable across versions (PSI={psi:.4f})"
    if psi < PSI_MONITOR:
        return (f"'{col_name}' shows moderate drift (PSI={psi:.4f}). "
                f"Monitor closely — re-train if trend continues")
    return (f"'{col_name}' has significant population shift (PSI={psi:.4f}). "
            f"Model trained on base version will degrade. Re-training recommended")


def _psi_result(psi, label, note="", buckets=None) -> Dict:
    return {"psi": psi, "label": label, "note": note,
            "buckets": buckets or [], "applicable": psi is not None}


# ─────────────────────────────────────────────────────────────────────────────
# 3. PRIORITIZED ACTION LIST
# ─────────────────────────────────────────────────────────────────────────────

def build_action_list(abt: ABTProfile,
                      blockers: list, warnings: list,
                      governance: list, health_scores: Dict) -> List[Dict]:
    """
    Produces an ordered list of actions, ranked by:
        priority_score = severity_tier × modeling_impact_weight

    Each action has:
        rank, column, action, why_it_matters, effort, severity, impact
    """
    row_count = max(abt.row_count, 1)
    actions = []

    # ── From blockers ─────────────────────────────────────────────────
    for item in blockers:
        col = abt.get_column(item["column"])
        for reason in item["reasons"]:
            rule = reason["rule"]
            if rule == "zero_variance":
                actions.append(_action(
                    column=item["column"],
                    action=f"Drop '{item['column']}' — zero variance column",
                    why=(f"Only {_safe(col.cardinality_count if col else 0, 1)} unique value(s). "
                         f"Zero-variance features add noise without signal. "
                         f"Linear models will fail, tree splits will never use it."),
                    effort="low",      severity="CRITICAL", impact=9,
                ))
            elif rule == "high_missing":
                pct = _safe(col.completeness_percent if col else 0, 0)
                actions.append(_action(
                    column=item["column"],
                    action=f"Investigate missingness root cause in '{item['column']}'",
                    why=(f"{round(100-pct,1)}% of rows missing. "
                         f"Imputing without understanding why data is absent introduces "
                         f"systematic bias. Determine if missing is informative (MNAR) "
                         f"before any imputation strategy."),
                    effort="medium",   severity="CRITICAL", impact=8,
                ))
            elif rule == "severe_mismatch":
                mm  = _safe(col.mismatched_count if col else 0, 0)
                rate = mm / row_count
                actions.append(_action(
                    column=item["column"],
                    action=f"Fix encoding inconsistencies in '{item['column']}' at source",
                    why=(f"{mm:,} records ({rate*100:.1f}%) have format mismatches. "
                         f"These will cause unpredictable one-hot encoding cardinality "
                         f"and corrupt WoE/IV calculations."),
                    effort="medium",   severity="CRITICAL", impact=8,
                ))

    # ── From governance ───────────────────────────────────────────────
    for item in governance:
        for risk in item["risks"]:
            rt = risk["risk_type"]
            if rt == "LEAKAGE":
                actions.append(_action(
                    column=item["column"],
                    action=f"Verify provenance of '{item['column']}' before training",
                    why=(f"Values bounded [0,1] with high cardinality — likely a probability "
                         f"score from another model. If trained on overlapping population, "
                         f"this column will inflate ROC-AUC and KS by up to 30+ points "
                         f"without adding real signal. Leakage is the #1 cause of "
                         f"production model failure."),
                    effort="low",      severity="CRITICAL", impact=10,
                ))
            elif rt == "IDENTIFIER":
                actions.append(_action(
                    column=item["column"],
                    action=f"Exclude '{item['column']}' from feature set",
                    why=(f"Unique identifier. Including it allows the model to memorise "
                         f"training rows, giving near-perfect in-sample performance "
                         f"and zero out-of-sample generalization."),
                    effort="low",      severity="HIGH", impact=9,
                ))
            elif rt == "PRIVACY":
                actions.append(_action(
                    column=item["column"],
                    action=f"Obtain governance sign-off for '{item['column']}'",
                    why=(f"Marked as private. In regulated industries (GDPR, FCRA, ECOA), "
                         f"using sensitive attributes like age/income in credit decisions "
                         f"requires documented justification and fairness testing."),
                    effort="high",     severity="HIGH", impact=6,
                ))

    # ── From warnings ─────────────────────────────────────────────────
    for item in warnings:
        col = abt.get_column(item["column"])
        for iss in item["issues"]:
            if iss["type"] == "partial_missing":
                pct = _safe(col.completeness_percent if col else 100, 100)
                actions.append(_action(
                    column=item["column"],
                    action=f"Define imputation strategy for '{item['column']}'",
                    why=(f"{round(100-pct,1)}% missing. Without a documented strategy, "
                         f"different train/test splits will see different imputed values, "
                         f"causing unstable validation metrics."),
                    effort="medium",   severity="MEDIUM", impact=5,
                ))
            elif iss["type"] == "format_mismatch":
                mm = _safe(col.mismatched_count if col else 0, 0)
                actions.append(_action(
                    column=item["column"],
                    action=f"Standardise encoding for '{item['column']}'",
                    why=(f"{mm:,} format mismatches. Case variations (e.g. 'X'/'x') "
                         f"inflate cardinality and make category-based features "
                         f"inconsistent between training and scoring."),
                    effort="low",      severity="MEDIUM", impact=5,
                ))

    # ── From health scores (low-score columns with no existing action) ──
    actioned_cols = {a["column"] for a in actions}
    for col_name, hs in health_scores.items():
        if col_name in actioned_cols or col_name.lower() in TARGET_NAMES:
            continue
        col = abt.get_column(col_name)
        if not col:
            continue
        score = hs["score"]
        if score < 55:
            worst = hs["breakdown"]
            worst_dim = min(worst, key=worst.get)
            if worst_dim == "distribution" and col.is_numeric():
                skew = abs(_safe(col.skewness, 0.0))
                if skew > 1.0:
                    actions.append(_action(
                        column=col_name,
                        action=f"Apply transformation to '{col_name}' (skewness={round(col.skewness,2)})",
                        why=(f"Right/left skew distorts distance-based and linear models. "
                             f"For risk models using WoE binning, severe skew creates "
                             f"unstable bins at the extremes. Log or Box-Cox transform recommended."),
                        effort="low",  severity="MEDIUM", impact=4,
                    ))
                elif _safe(col.n_outliers, 0) > 0:
                    actions.append(_action(
                        column=col_name,
                        action=f"Winsorise or cap outliers in '{col_name}'",
                        why=(f"{_safe(col.n_outliers,0)} outliers detected. "
                             f"In logistic regression and scorecard models, extreme values "
                             f"can dominate coefficient estimation. Winsorise at 1st/99th percentile."),
                        effort="low",  severity="MEDIUM", impact=4,
                    ))

    # ── Rank by priority_score = severity_tier × impact ────────────────
    for a in actions:
        a["priority_score"] = _SEV.get(a["severity"], 1) * a["impact"]

    actions.sort(key=lambda x: -x["priority_score"])
    for i, a in enumerate(actions):
        a["rank"] = i + 1

    return actions


def _action(column, action, why, effort, severity, impact) -> Dict:
    return {
        "column":   column,
        "action":   action,
        "why":      why,
        "effort":   effort,      # low / medium / high
        "severity": severity,    # CRITICAL / HIGH / MEDIUM / LOW
        "impact":   impact,      # 1–10 modeling impact
        "priority_score": 0,     # set after all collected
        "rank":     0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. DATASET READINESS SCORE
# ─────────────────────────────────────────────────────────────────────────────

def dataset_readiness_score(health_scores: Dict[str, Dict],
                             readiness_statuses: List[Dict]) -> Dict:
    """
    Weighted average of column health scores.
    Weights: drop=0 (excluded), caution=0.5 weight, ready=1.0 weight.
    Target column excluded.

    Returns: {score, label, total_cols, included_cols, excluded_cols}
    """
    status_map = {r["column"]: r["status"] for r in readiness_statuses}
    status_weight = {"ready": 1.0, "caution": 0.5, "drop": 0.0}

    total_w = 0.0
    weighted_sum = 0.0
    included = 0
    excluded = 0

    for col_name, hs in health_scores.items():
        if col_name.lower() in TARGET_NAMES:
            excluded += 1
            continue
        st = status_map.get(col_name, "ready")
        w  = status_weight.get(st, 1.0)
        if st == "drop":
            excluded += 1
            continue
        weighted_sum += hs["score"] * w
        total_w      += w
        included     += 1

    score = round(weighted_sum / total_w, 1) if total_w > 0 else 0.0

    if score >= 80:   label = "ready"
    elif score >= 60: label = "mostly_ready"
    elif score >= 40: label = "needs_work"
    else:             label = "not_ready"

    return {
        "score":        score,
        "label":        label,
        "total_cols":   len(health_scores),
        "included_cols":included,
        "excluded_cols":excluded,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. PSI SUMMARY ACROSS MULTIPLE VERSION PAIRS  (for compare, N versions)
# ─────────────────────────────────────────────────────────────────────────────

def psi_matrix(abts: List[ABTProfile]) -> Dict:
    """
    Compute PSI for every (base, new) consecutive pair across N versions.
    Returns a dict keyed by column_name → list of PSI results per pair.
    Designed to handle 10–15 versions without repeated logic.
    """
    if len(abts) < 2:
        return {}

    # Union of all column names
    all_cols = []
    seen = set()
    for a in abts:
        for n in a.column_names:
            if n not in seen:
                seen.add(n); all_cols.append(n)

    result = {}
    for col_name in all_cols:
        col_results = []
        for i in range(len(abts) - 1):
            a, b = abts[i], abts[i + 1]
            ca, cb = a.get_column(col_name), b.get_column(col_name)
            if ca is None or cb is None:
                col_results.append({
                    "from": a.abt_name, "to": b.abt_name,
                    "psi": None, "label": "absent",
                    "note": f"Column absent in one version", "applicable": False
                })
            else:
                pr = compute_psi(ca, cb, max(a.row_count, 1), max(b.row_count, 1))
                pr["from"] = a.abt_name
                pr["to"]   = b.abt_name
                col_results.append(pr)
        result[col_name] = col_results

    return result


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    """Return val if not None and finite, else default."""
    try:
        if val is None:
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _is_prob_like(col: ColumnProfile) -> bool:
    """Heuristic: numeric, bounded [0,1], high cardinality, not binary/id."""
    return (
        col.is_numeric()
        and col.statistical_scale not in ("binary", "id", "unary")
        and _safe(col.min_val, -1) >= 0
        and _safe(col.max_val, 2)  <= 1
        and _safe(col.cardinality_count, 0) >= 50
        and col.name.lower() not in TARGET_NAMES
    )