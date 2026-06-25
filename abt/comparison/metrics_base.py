"""
abt/metrics_base.py
─────────────────────────────────────────────────────────────────────────────
Basic statistical and distribution metrics.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional
from abt.analysis.columnProfile import ColumnProfile
from abt.insights.insights import _safe

# ── Thresholds ────────────────────────────────────────────────────────────────
CV_NOTABLE  = 0.20
CV_CRITICAL = 0.50
STD_NOTABLE  = 0.25
STD_CRITICAL = 0.50
QUANTILE_NOTABLE  = 0.20
QUANTILE_CRITICAL = 0.50
BOUNDARY_NOTABLE  = 0.10
BOUNDARY_CRITICAL = 0.25
KURTOSIS_NOTABLE  = 1.0
KURTOSIS_CRITICAL = 3.0
ENTROPY_NOTABLE  = 0.15
ENTROPY_CRITICAL = 0.35
KS_NOTABLE  = 0.10
KS_CRITICAL = 0.20


def _threshold_label(value: float, notable: float, critical: float) -> str:
    if value >= critical: return "critical"
    if value >= notable:  return "notable"
    return "stable"


def _not_applicable(metric: str, reason: str) -> Dict:
    return {"metric": metric, "applicable": False, "note": reason}


# ── 1. CV DRIFT ─────────────────────────────────────────────────────────────

def compute_cv_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("cv_drift", "non-numeric column")

    mean_b = _safe(col_base.mean, None)
    mean_n = _safe(col_new.mean, None)
    std_b  = _safe(col_base.std, None)
    std_n  = _safe(col_new.std, None)

    if any(v is None for v in [mean_b, mean_n, std_b, std_n]):
        return _not_applicable("cv_drift", "missing mean or std")
    if abs(mean_b) < 1e-6:
        return _not_applicable("cv_drift", "mean near zero — CV undefined")

    cv_base = std_b / abs(mean_b)
    cv_new  = std_n / abs(mean_n) if abs(mean_n) > 1e-6 else None
    if cv_new is None:
        return _not_applicable("cv_drift", "new mean near zero — CV undefined")

    rel_change = abs(cv_new - cv_base) / max(cv_base, 1e-9)
    severity   = _threshold_label(rel_change, CV_NOTABLE, CV_CRITICAL)

    return {
        "metric":        "cv_drift",
        "cv_base":       round(cv_base, 4),
        "cv_new":        round(cv_new, 4),
        "relative_change": round(rel_change, 4),
        "severity":      severity,
        "applicable":    True,
        "interpretation": _cv_interpretation(cv_base, cv_new, rel_change, col_base.name),
    }


def _cv_interpretation(cv_b, cv_n, rel, col: str) -> str:
    if rel < CV_NOTABLE:
        return f"'{col}' relative variability is stable (CV {cv_b:.3f}→{cv_n:.3f})."
    direction = "increased" if cv_n > cv_b else "decreased"
    if rel >= CV_CRITICAL:
        return (f"'{col}' relative variability {direction} significantly "
                f"(CV {cv_b:.3f}→{cv_n:.3f}, {rel*100:.1f}% change). "
                f"Feature noise level has changed — WoE bins and feature scaling need review.")
    return (f"'{col}' relative variability {direction} moderately "
            f"(CV {cv_b:.3f}→{cv_n:.3f}). Monitor across next versions.")


# ── 2. STD DEVIATION DRIFT ───────────────────────────────────────────────────

def compute_std_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("std_drift", "non-numeric column")

    std_b = _safe(col_base.std, None)
    std_n = _safe(col_new.std, None)
    if std_b is None or std_n is None:
        return _not_applicable("std_drift", "missing std")
    if std_b < 1e-9:
        return _not_applicable("std_drift", "base std near zero")

    norm_change = abs(std_n - std_b) / std_b
    severity    = _threshold_label(norm_change, STD_NOTABLE, STD_CRITICAL)
    direction   = "widened" if std_n > std_b else "narrowed"

    return {
        "metric":        "std_drift",
        "std_base":      round(std_b, 4),
        "std_new":       round(std_n, 4),
        "norm_change":   round(norm_change, 4),
        "severity":      severity,
        "applicable":    True,
        "interpretation": _std_interpretation(std_b, std_n, norm_change, direction, col_base.name),
    }


def _std_interpretation(sb, sn, norm, direction, col: str) -> str:
    if norm < STD_NOTABLE:
        return f"'{col}' spread is stable (std {sb:.3f}→{sn:.3f})."
    if norm >= STD_CRITICAL:
        return (f"'{col}' distribution has {direction} significantly "
                f"(std {sb:.3f}→{sn:.3f}, {norm*100:.1f}% change). "
                f"A model trained on the base spread will {'underestimate extremes' if direction=='widened' else 'overestimate variance'} "
                f"at scoring time. Re-calibrate or re-train.")
    return (f"'{col}' distribution has {direction} moderately "
            f"(std {sb:.3f}→{sn:.3f}). Review feature scaling and outlier caps.")


# ── 3. QUANTILE SHIFT ANALYSIS ───────────────────────────────────────────────

def compute_quantile_shift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("quantile_shift", "non-numeric column")

    q25_b = _safe(col_base.q25, None)
    q50_b = _safe(col_base.q50, None)
    q75_b = _safe(col_base.q75, None)
    q25_n = _safe(col_new.q25, None)
    q50_n = _safe(col_new.q50, None)
    q75_n = _safe(col_new.q75, None)

    if any(v is None for v in [q25_b, q50_b, q75_b, q25_n, q50_n, q75_n]):
        return _not_applicable("quantile_shift", "missing quantile data")

    iqr = max(q75_b - q25_b, 1e-9)

    shifts = {
        "Q1":     (q25_n - q25_b) / iqr,
        "Median": (q50_n - q50_b) / iqr,
        "Q3":     (q75_n - q75_b) / iqr,
    }
    max_shift  = max(abs(v) for v in shifts.values())
    severity   = _threshold_label(max_shift, QUANTILE_NOTABLE, QUANTILE_CRITICAL)
    worst_q    = max(shifts, key=lambda k: abs(shifts[k]))

    return {
        "metric":        "quantile_shift",
        "iqr_base":      round(iqr, 4),
        "shifts":        {k: round(v, 4) for k, v in shifts.items()},
        "max_shift":     round(max_shift, 4),
        "worst_quantile":worst_q,
        "severity":      severity,
        "applicable":    True,
        "interpretation": _quantile_interpretation(shifts, max_shift, worst_q, iqr, col_base.name),
    }


def _quantile_interpretation(shifts, max_shift, worst_q, iqr, col: str) -> str:
    if max_shift < QUANTILE_NOTABLE:
        return f"'{col}' quantile positions are stable."
    direction = "upward" if shifts[worst_q] > 0 else "downward"
    location = {
        "Q1":     "lower tail (low-value population)",
        "Median": "distribution center (population majority)",
        "Q3":     "upper tail (high-value population)",
    }[worst_q]
    severity_word = "significantly" if max_shift >= QUANTILE_CRITICAL else "moderately"
    return (f"'{col}' {location} shifted {direction} {severity_word} "
            f"({worst_q} moved {shifts[worst_q]*iqr:+.3f} units, {max_shift:.2f}× IQR). "
            f"WoE bins anchored to base quantiles will produce incorrect scores for this segment.")


# ── 4. MIN/MAX BOUNDARY DRIFT ────────────────────────────────────────────────

def compute_boundary_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("boundary_drift", "non-numeric column")

    min_b = _safe(col_base.min_val, None)
    max_b = _safe(col_base.max_val, None)
    min_n = _safe(col_new.min_val, None)
    max_n = _safe(col_new.max_val, None)

    if any(v is None for v in [min_b, max_b, min_n, max_n]):
        return _not_applicable("boundary_drift", "missing min/max")

    base_range = max(abs(max_b - min_b), 1e-9)

    lower_shift = (min_n - min_b) / base_range
    upper_shift = (max_n - max_b) / base_range

    lower_abs = abs(lower_shift)
    upper_abs = abs(upper_shift)
    max_abs   = max(lower_abs, upper_abs)
    severity  = _threshold_label(max_abs, BOUNDARY_NOTABLE, BOUNDARY_CRITICAL)

    return {
        "metric":        "boundary_drift",
        "min_base":      min_b, "min_new": min_n,
        "max_base":      max_b, "max_new": max_n,
        "lower_shift":   round(lower_shift, 4),
        "upper_shift":   round(upper_shift, 4),
        "severity":      severity,
        "applicable":    True,
        "interpretation": _boundary_interpretation(min_b, max_b, min_n, max_n, lower_shift, upper_shift, col_base.name),
    }


def _boundary_interpretation(mn_b, mx_b, mn_n, mx_n, lower, upper, col: str) -> str:
    parts = []
    if abs(upper) >= BOUNDARY_NOTABLE:
        direction = "expanded" if upper > 0 else "compressed"
        parts.append(
            f"Upper boundary {direction} ({mx_b}→{mx_n}). "
            + ("Model will extrapolate beyond training range for new high values."
               if upper > 0 else "High-value population may have been filtered or capped.")
        )
    if abs(lower) >= BOUNDARY_NOTABLE:
        direction = "dropped" if lower < 0 else "raised"
        parts.append(
            f"Lower boundary {direction} ({mn_b}→{mn_n}). "
            + ("New low-value records appeared — model has not seen this range."
               if lower < 0 else "Low-value population reduced — possible sampling change.")
        )
    if not parts:
        return f"'{col}' value boundaries are stable."
    return f"'{col}': " + " ".join(parts)


# ── 5. KURTOSIS DRIFT ────────────────────────────────────────────────────────

def compute_kurtosis_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("kurtosis_drift", "non-numeric column")

    k_b = _safe(col_base.kurtosis, None)
    k_n = _safe(col_new.kurtosis, None)
    if k_b is None or k_n is None:
        return _not_applicable("kurtosis_drift", "kurtosis not available in metadata")

    delta    = k_n - k_b
    severity = _threshold_label(abs(delta), KURTOSIS_NOTABLE, KURTOSIS_CRITICAL)

    return {
        "metric":        "kurtosis_drift",
        "kurtosis_base": round(k_b, 4),
        "kurtosis_new":  round(k_n, 4),
        "delta":         round(delta, 4),
        "severity":      severity,
        "applicable":    True,
        "interpretation": _kurtosis_interpretation(k_b, k_n, delta, col_base.name),
    }


def _kurtosis_interpretation(k_b, k_n, delta, col: str) -> str:
    if abs(delta) < KURTOSIS_NOTABLE:
        return f"'{col}' tail behaviour is stable (kurtosis {k_b:.2f}→{k_n:.2f})."
    if delta > 0:
        return (f"'{col}' tails became heavier (kurtosis {k_b:.2f}→{k_n:.2f}, Δ={delta:+.2f}). "
                f"More extreme values appearing at scoring time than during training. "
                f"Risk models may underestimate tail probabilities.")
    return (f"'{col}' tails became lighter (kurtosis {k_b:.2f}→{k_n:.2f}, Δ={delta:+.2f}). "
            f"Extreme value population has reduced — population may have been filtered.")


# ── 6. ENTROPY DRIFT ─────────────────────────────────────────────────────────

def compute_entropy_drift(col_base: ColumnProfile, col_new: ColumnProfile,
                           row_base: int = 1000, row_new: int = 1000) -> Dict:
    if col_base.is_numeric() and col_base.statistical_scale not in ("binary",):
        return _not_applicable("entropy_drift", "numeric non-binary column — use quantile metrics instead")

    card_b = _safe(col_base.cardinality_count, 0)
    card_n = _safe(col_new.cardinality_count, 0)
    if card_b <= 0 or card_n <= 0:
        return _not_applicable("entropy_drift", "cardinality unavailable")

    h_b = _approx_entropy(card_b, _safe(col_base.uniqueness_percent, 0), max(row_base, 1))
    h_n = _approx_entropy(card_n, _safe(col_new.uniqueness_percent, 0), max(row_new, 1))

    if h_b < 1e-9:
        return _not_applicable("entropy_drift", "base entropy near zero — single dominant category")

    rel_change = abs(h_n - h_b) / h_b
    severity   = _threshold_label(rel_change, ENTROPY_NOTABLE, ENTROPY_CRITICAL)

    return {
        "metric":           "entropy_drift",
        "entropy_base":     round(h_b, 4),
        "entropy_new":      round(h_n, 4),
        "relative_change":  round(rel_change, 4),
        "cardinality_base": int(card_b),
        "cardinality_new":  int(card_n),
        "severity":         severity,
        "applicable":       True,
        "interpretation":   _entropy_interpretation(h_b, h_n, rel_change, col_base.name),
    }


def _approx_entropy(cardinality: int, uniqueness_pct: float, row_count: int) -> float:
    if cardinality <= 1:
        return 0.0
    dom_frac  = max(0.0, min(1.0, 1.0 - (uniqueness_pct / 100.0)))
    rest_frac = (1.0 - dom_frac) / max(cardinality - 1, 1)
    h = 0.0
    if dom_frac > 1e-9:
        h -= dom_frac * math.log2(dom_frac)
    if rest_frac > 1e-9 and cardinality > 1:
        h -= (cardinality - 1) * rest_frac * math.log2(rest_frac)
    return max(h, 0.0)


def _entropy_interpretation(h_b, h_n, rel, col: str) -> str:
    if rel < ENTROPY_NOTABLE:
        return f"'{col}' category diversity is stable (entropy {h_b:.3f}→{h_n:.3f})."
    if h_n > h_b:
        return (f"'{col}' category diversity increased (entropy {h_b:.3f}→{h_n:.3f}, +{rel*100:.1f}%). "
                f"More categories are appearing with similar frequency — "
                f"one-hot encoding and WoE bins will become less stable.")
    return (f"'{col}' category diversity decreased (entropy {h_b:.3f}→{h_n:.3f}, -{rel*100:.1f}%). "
            f"Distribution is concentrating into fewer categories — "
            f"possible population filtering or category consolidation upstream.")


# ── 10. APPROXIMATE KS STATISTIC ─────────────────────────────────────────────

def compute_ks_approximation(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("ks_approx", "non-numeric column")

    pts_b = _extract_quantile_points(col_base)
    pts_n = _extract_quantile_points(col_new)

    if pts_b is None or pts_n is None:
        return _not_applicable("ks_approx", "insufficient quantile data (need min, Q1, median, Q3, max)")

    base_cdf  = [0.0, 0.25, 0.50, 0.75, 1.0]
    new_cdf   = _interpolate_cdf(pts_b, pts_n)

    diffs = [abs(new_cdf[i] - base_cdf[i]) for i in range(len(base_cdf))]
    ks_stat   = max(diffs)
    worst_idx = diffs.index(ks_stat)
    worst_pt  = ["min", "Q1", "Median", "Q3", "max"][worst_idx]

    severity  = _threshold_label(ks_stat, KS_NOTABLE, KS_CRITICAL)

    return {
        "metric":      "ks_approx",
        "ks_statistic":round(ks_stat, 4),
        "worst_point": worst_pt,
        "cdf_diffs":   {["min","Q1","Median","Q3","max"][i]: round(diffs[i], 4)
                        for i in range(5)},
        "severity":    severity,
        "applicable":  True,
        "interpretation": _ks_interpretation(ks_stat, worst_pt, severity, col_base.name),
    }


def _extract_quantile_points(col: ColumnProfile):
    pts = [_safe(col.min_val, None), _safe(col.q25, None),
           _safe(col.q50, None),     _safe(col.q75, None),
           _safe(col.max_val, None)]
    return pts if all(v is not None for v in pts) else None


def _interpolate_cdf(base_pts: List[float], new_pts: List[float]) -> List[float]:
    new_cdf = []
    for x in base_pts:
        new_cdf.append(_cdf_at(x, new_pts))
    return new_cdf


def _cdf_at(x: float, quantile_pts: List[float]) -> float:
    boundaries = list(zip(quantile_pts, [0.0, 0.25, 0.50, 0.75, 1.0]))

    if x <= quantile_pts[0]:  return 0.0
    if x >= quantile_pts[-1]: return 1.0

    for i in range(len(boundaries) - 1):
        x0, p0 = boundaries[i]
        x1, p1 = boundaries[i + 1]
        if x0 <= x <= x1:
            if abs(x1 - x0) < 1e-9: return p0
            return p0 + (x - x0) / (x1 - x0) * (p1 - p0)
    return 1.0


def _ks_interpretation(ks: float, worst_pt: str, severity: str, col: str) -> str:
    if severity == "stable":
        return f"'{col}' CDF is stable (KS={ks:.4f})."
    location = {
        "min":    "minimum value boundary",
        "Q1":     "lower quartile (bottom 25%)",
        "Median": "distribution center",
        "Q3":     "upper quartile (top 25%)",
        "max":    "maximum value boundary",
    }[worst_pt]
    return (f"'{col}' maximum CDF deviation is {ks:.4f} at the {location}. "
            + ("Significant distribution shift confirmed — corroborates PSI result."
               if severity == "critical"
               else "Moderate CDF deviation — monitor alongside PSI."))
