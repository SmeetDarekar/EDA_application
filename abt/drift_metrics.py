"""
abt/drift_metrics.py
─────────────────────────────────────────────────────────────────────────────
10 additional drift detection techniques, all computable from metadata only.

Industry alignment:
  - PSI fix: uses union boundaries (same approach as Evidently AI)
  - KS approximation: follows Great Expectations' quantile-based KS
  - FSI: Feature Stability Index as defined in Basel model monitoring guidelines
  - Drift velocity: follows Arize AI / WhyLabs rolling drift pattern
  - Baseline comparison: same as Evidently's "reference dataset" approach

All functions:
  - Accept ColumnProfile objects only (never raw dicts)
  - Return plain JSON-serialisable dicts
  - Handle None fields, division-by-zero, missing quantiles everywhere
  - Are additive — nothing in existing code is touched

Techniques implemented:
  1.  CV Drift                 — relative spread change (std/mean)
  2.  Std Deviation Drift      — absolute spread change
  3.  Quantile Shift           — Q1/Median/Q3 independent movement
  4.  Min/Max Boundary Drift   — range expansion/compression
  5.  Kurtosis Drift           — tail behaviour change
  6.  Entropy Drift            — categorical diversity change
  7.  Feature Stability Index  — longitudinal PSI rolling average
  8.  Drift Velocity           — rate of PSI change per version
  9.  Baseline Drift           — every version vs V1 (anchor comparison)
  10. Approximate KS Statistic — max CDF deviation from quantiles
  +   PSI with union boundaries (fixes existing PSI range problem)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

from .columnProfile import ColumnProfile, ABTProfile
from .insights import _safe, PSI_STABLE, PSI_MONITOR, TARGET_NAMES

# ── Thresholds ────────────────────────────────────────────────────────────────

# CV drift: relative change in coefficient of variation
CV_NOTABLE  = 0.20   # 20% relative change in CV → notable
CV_CRITICAL = 0.50   # 50% relative change → critical

# Std drift: normalized by base std
STD_NOTABLE  = 0.25
STD_CRITICAL = 0.50

# Quantile shift: normalized by base IQR
QUANTILE_NOTABLE  = 0.20
QUANTILE_CRITICAL = 0.50

# Min/Max boundary: relative expansion/compression
BOUNDARY_NOTABLE  = 0.10   # 10% range change
BOUNDARY_CRITICAL = 0.25   # 25% range change

# Kurtosis drift: absolute change in kurtosis
KURTOSIS_NOTABLE  = 1.0
KURTOSIS_CRITICAL = 3.0

# Entropy drift: relative change
ENTROPY_NOTABLE  = 0.15
ENTROPY_CRITICAL = 0.35

# KS approximation: max CDF deviation
KS_NOTABLE  = 0.10   # same as Kolmogorov-Smirnov critical value for n=1000
KS_CRITICAL = 0.20

# FSI: Feature Stability Index (1 - mean_PSI, normalized)
FSI_UNSTABLE = 0.70   # FSI below this → unstable feature

# Drift velocity: PSI increase per version
VELOCITY_NOTABLE  = 0.05
VELOCITY_CRITICAL = 0.15


# ─────────────────────────────────────────────────────────────────────────────
# 1. CV DRIFT
# Coefficient of Variation = std / mean
# Tracks relative spread independently of scale.
# A doubling mean with stable CV = scaling, not drift.
# A stable mean with rising CV = increasing noise/instability.
# Used by: Evidently AI (relative metric drift), Great Expectations (relative stddev)
# ─────────────────────────────────────────────────────────────────────────────

def compute_cv_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    CV = std / |mean|
    CV drift = |CV_new - CV_base| / max(CV_base, 1e-9)
    Not applicable if mean ≈ 0 (division unstable) or column is non-numeric.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# 2. STD DEVIATION DRIFT
# Absolute spread change normalized by base std.
# Catches the case: same mean, wider distribution → model underestimates extremes.
# Used by: Great Expectations (expect_column_stdev_to_be_between),
#          Evidently AI (standard deviation drift test)
# ─────────────────────────────────────────────────────────────────────────────

def compute_std_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    Normalized std drift = |std_new - std_base| / std_base
    Threshold in units of base std (same logic as Cohen's d for means).
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# 3. QUANTILE SHIFT ANALYSIS
# Q1, Median, Q3 tracked independently.
# Tells you WHERE in the distribution the shift is happening.
# Lower tail shift → new low-value segments.
# Upper tail shift → new high-value segments or outliers.
# Median shift → population center movement.
# Critical for WoE bin maintenance in credit scoring.
# Used by: Evidently AI (quantile-based drift), Great Expectations (quantile tests)
# ─────────────────────────────────────────────────────────────────────────────

def compute_quantile_shift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    Shift per quantile normalized by base IQR.
    IQR = Q3 - Q1 of base version (robust scale estimator).
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# 4. MIN/MAX BOUNDARY DRIFT
# Range expansion = model extrapolating beyond training range.
# Range compression = population truncation or filtering change.
# Neither is captured by mean/std drift.
# Used by: Great Expectations (expect_column_min/max_to_be_between),
#          Evidently AI (column range tests)
# ─────────────────────────────────────────────────────────────────────────────

def compute_boundary_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    Relative min/max change normalized by base range.
    Separate signals for lower boundary and upper boundary.
    """
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("boundary_drift", "non-numeric column")

    min_b = _safe(col_base.min_val, None)
    max_b = _safe(col_base.max_val, None)
    min_n = _safe(col_new.min_val, None)
    max_n = _safe(col_new.max_val, None)

    if any(v is None for v in [min_b, max_b, min_n, max_n]):
        return _not_applicable("boundary_drift", "missing min/max")

    base_range = max(abs(max_b - min_b), 1e-9)

    lower_shift = (min_n - min_b) / base_range   # negative = lower bound dropped
    upper_shift = (max_n - max_b) / base_range   # positive = upper bound expanded

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


# ─────────────────────────────────────────────────────────────────────────────
# 5. KURTOSIS DRIFT
# Kurtosis measures tail weight. Normal distribution = 0 (excess kurtosis).
# Positive kurtosis = heavy tails (more extreme values than expected).
# Negative kurtosis = light tails (fewer extremes, platykurtic).
# A model trained on light tails will assign wrong probabilities to extreme inputs.
# Used by: Evidently AI (distribution tests), SAS Model Manager (moment tracking)
# ─────────────────────────────────────────────────────────────────────────────

def compute_kurtosis_drift(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    Absolute kurtosis change. No normalization — kurtosis is already scale-free.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# 6. ENTROPY DRIFT (categorical)
# Shannon entropy measures category diversity.
# H = -Σ p_i × log2(p_i), range [0, log2(cardinality)]
# We approximate p_i assuming uniform distribution within known cardinality
# and adjusting for uniqueness% as a dominance proxy.
# Rising entropy = more categories being used equally → instability
# Falling entropy = concentration → one category dominating
# Used by: Evidently AI (chi-square categorical drift),
#          Great Expectations (expect_column_proportion_of_unique_values)
# ─────────────────────────────────────────────────────────────────────────────

def compute_entropy_drift(col_base: ColumnProfile, col_new: ColumnProfile,
                           row_base: int = 1000, row_new: int = 1000) -> Dict:
    """
    Approximate Shannon entropy from cardinality + uniqueness%.
    Relative entropy change flagged as drift.
    """
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
    """
    Approximate entropy assuming a Zipf-like concentration.
    uniqueness_pct / 100 ≈ fraction of distinct values per row → proxy for spread.
    When uniqueness is high, distribution is spread (higher entropy).
    When low (one value dominates), entropy approaches 0.
    """
    if cardinality <= 1:
        return 0.0
    # Approximate: dominant category takes (1 - uniqueness_pct/100) of rows
    # Remaining (cardinality-1) categories split the rest uniformly
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


# ─────────────────────────────────────────────────────────────────────────────
# 7. FEATURE STABILITY INDEX (FSI)
# FSI = 1 - mean(PSI across all consecutive pairs), normalized to [0,1].
# FSI < 0.70 → unstable feature across version history.
# This is the longitudinal equivalent of PSI — a single trustworthiness score
# for a feature over its entire observed history.
# Used by: SAS Model Manager, Basel model monitoring requirements,
#          internal credit risk model validation frameworks.
# ─────────────────────────────────────────────────────────────────────────────

def compute_fsi(psi_values: List[Optional[float]]) -> Dict:
    """
    Args:
        psi_values: list of PSI values from consecutive version pairs
                    (None entries skipped)
    Returns FSI score and stability label.
    """
    valid = [v for v in psi_values if v is not None and math.isfinite(v)]
    if not valid:
        return {"metric": "fsi", "fsi": None, "label": "insufficient_data",
                "applicable": False, "mean_psi": None}

    mean_psi = sum(valid) / len(valid)
    # FSI = max(0, 1 - mean_psi / PSI_MONITOR)
    # Normalized so that mean_psi == PSI_MONITOR → FSI == 0.0 (fully unstable)
    fsi = max(0.0, round(1.0 - mean_psi / PSI_MONITOR, 4))

    if fsi >= FSI_UNSTABLE:
        label = "stable"
    elif fsi >= 0.40:
        label = "monitor"
    else:
        label = "unstable"

    return {
        "metric":    "fsi",
        "fsi":       fsi,
        "label":     label,
        "mean_psi":  round(mean_psi, 4),
        "n_pairs":   len(valid),
        "applicable": True,
        "interpretation": (
            f"FSI={fsi:.3f} ({label}). "
            + (f"Feature has been stable across {len(valid)} version pair(s) (mean PSI={mean_psi:.4f})."
               if label == "stable"
               else f"Mean PSI={mean_psi:.4f} across {len(valid)} version(s) — "
                    + ("feature is drifting consistently. Consider re-training."
                       if label == "unstable"
                       else "monitor closely in next ingestion cycle."))
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8. DRIFT VELOCITY
# Rate of PSI change per version step.
# Distinguishes: gradual stable drift (low velocity) vs accelerating drift
# (high velocity — urgent, model may degrade rapidly).
# A PSI=0.30 reached gradually over 10 versions is less alarming than
# PSI=0.30 in 2 versions.
# Used by: Arize AI, WhyLabs (drift velocity monitoring)
# ─────────────────────────────────────────────────────────────────────────────

def compute_drift_velocity(psi_values: List[Optional[float]]) -> Dict:
    """
    Args:
        psi_values: PSI per consecutive version pair (ordered oldest → newest)
    Velocity = slope of PSI over version pairs (least-squares, same as trend slope).
    """
    valid_pts = [(i, v) for i, v in enumerate(psi_values) if v is not None and math.isfinite(v)]
    if len(valid_pts) < 2:
        return {"metric": "drift_velocity", "velocity": None, "label": "insufficient_data",
                "applicable": False}

    n   = len(valid_pts)
    sx  = sum(p[0] for p in valid_pts)
    sy  = sum(p[1] for p in valid_pts)
    sxy = sum(p[0] * p[1] for p in valid_pts)
    sxx = sum(p[0] ** 2 for p in valid_pts)
    denom = n * sxx - sx * sx
    velocity = round((n * sxy - sx * sy) / denom, 4) if denom != 0 else 0.0

    severity = _threshold_label(abs(velocity), VELOCITY_NOTABLE, VELOCITY_CRITICAL)

    return {
        "metric":      "drift_velocity",
        "velocity":    velocity,       # PSI units per version step
        "label":       severity,
        "n_pairs":     n,
        "applicable":  True,
        "interpretation": _velocity_interpretation(velocity, severity),
    }


def _velocity_interpretation(velocity: float, severity: str) -> str:
    if severity == "stable":
        return f"PSI is not accelerating (velocity={velocity:+.4f}/version)."
    direction = "increasing" if velocity > 0 else "decreasing"
    if severity == "critical":
        return (f"Drift is accelerating rapidly (PSI velocity={velocity:+.4f}/version). "
                f"Distribution is {direction} fast — model degradation is imminent. "
                f"Immediate back-testing required.")
    return (f"Drift is {direction} gradually (PSI velocity={velocity:+.4f}/version). "
            f"Monitor — if trend continues, back-testing will be required within next 2–3 versions.")


# ─────────────────────────────────────────────────────────────────────────────
# 9. BASELINE DRIFT (Every version vs V1)
# The most critical comparison for model monitoring.
# Consecutive drift (Vn→Vn+1) is operational signal.
# Baseline drift (Vn vs V1) is model degradation signal.
# A model trained on V1 — which is the typical real-world scenario — needs
# to know how far the current population has drifted from training data.
# Used by: Evidently AI (reference dataset comparison),
#          WhyLabs (baseline profile comparison), MLflow (reference tracking)
# ─────────────────────────────────────────────────────────────────────────────

def compute_baseline_drift(abts: List[ABTProfile]) -> Dict:
    """
    Compares every version against V1 (anchor/baseline).
    Uses PSI with union boundaries for fair comparison.
    Returns per-column baseline PSI for each non-baseline version.

    This answers: "How far has this feature drifted from when the model was trained?"
    """
    if len(abts) < 2:
        return {"applicable": False, "note": "Need at least 2 versions."}

    baseline = abts[0]
    comparisons = {}

    all_cols = []
    seen = set()
    for a in abts:
        for n in a.column_names:
            if n not in seen:
                seen.add(n); all_cols.append(n)

    skip_scales = {"id", "unary"}

    for col_name in all_cols:
        col_b = baseline.get_column(col_name)
        if col_b is None or col_b.statistical_scale in skip_scales:
            continue

        col_comparisons = []
        for abt in abts[1:]:
            col_n = abt.get_column(col_name)
            if col_n is None:
                col_comparisons.append({
                    "version": abt.abt_name,
                    "psi": None, "label": "absent", "applicable": False,
                })
                continue

            psi_result = compute_psi_union(col_b, col_n,
                                            max(baseline.row_count, 1),
                                            max(abt.row_count, 1))
            col_comparisons.append({
                "version":    abt.abt_name,
                "psi":        psi_result.get("psi"),
                "label":      psi_result.get("label", "n/a"),
                "applicable": psi_result.get("applicable", False),
                "note":       psi_result.get("note", ""),
            })

        if col_comparisons:
            # Overall baseline drift severity = worst across all versions
            labels = [c["label"] for c in col_comparisons if c.get("applicable")]
            if "shift" in labels:       worst = "shift"
            elif "monitor" in labels:   worst = "monitor"
            elif labels:                worst = "stable"
            else:                       worst = "not_applicable"

            comparisons[col_name] = {
                "baseline_version": baseline.abt_name,
                "comparisons":      col_comparisons,
                "worst_label":      worst,
            }

    return {
        "applicable":    True,
        "baseline":      baseline.abt_name,
        "columns":       comparisons,
        "note": ("Baseline drift measures how far each version has moved from V1. "
                 "This is the model degradation signal — the model was trained on the baseline."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 10. APPROXIMATE KS STATISTIC
# Kolmogorov-Smirnov: max |CDF_base(x) - CDF_new(x)| across all x.
# Approximated from quantile points: evaluate CDF at each quantile boundary.
# Complements PSI — PSI weights all buckets equally, KS finds the worst point.
# If both PSI and KS flag the same column, confidence in drift is high.
# Used by: Great Expectations (ks_2samp), Evidently AI (KS test),
#          SciPy stats (ks_2samp reference implementation)
# ─────────────────────────────────────────────────────────────────────────────

def compute_ks_approximation(col_base: ColumnProfile, col_new: ColumnProfile) -> Dict:
    """
    Approximate KS statistic from quantile-based CDF reconstruction.
    CDF evaluated at 5 points: min, Q1, Median, Q3, Max of base version.
    KS = max absolute CDF difference across these evaluation points.

    Note: This is an approximation. True KS requires full data.
    Accuracy improves with more quantile points — we have 5 (P0, P25, P50, P75, P100).
    """
    if not col_base.is_numeric() or not col_new.is_numeric():
        return _not_applicable("ks_approx", "non-numeric column")

    # Build evaluation points from base quantiles
    pts_b = _extract_quantile_points(col_base)
    pts_n = _extract_quantile_points(col_new)

    if pts_b is None or pts_n is None:
        return _not_applicable("ks_approx", "insufficient quantile data (need min, Q1, median, Q3, max)")

    # Approximate CDFs at base evaluation points
    # Base CDF at its own quantile points: known exactly (0, 0.25, 0.50, 0.75, 1.0)
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
    """Returns [min, Q1, median, Q3, max] or None if any is missing."""
    pts = [_safe(col.min_val, None), _safe(col.q25, None),
           _safe(col.q50, None),     _safe(col.q75, None),
           _safe(col.max_val, None)]
    return pts if all(v is not None for v in pts) else None


def _interpolate_cdf(base_pts: List[float], new_pts: List[float]) -> List[float]:
    """
    Estimate CDF of new distribution at base quantile points.
    Uses linear interpolation between new quantile boundaries.
    """
    new_cdf = []
    for x in base_pts:
        new_cdf.append(_cdf_at(x, new_pts))
    return new_cdf


def _cdf_at(x: float, quantile_pts: List[float]) -> float:
    """
    Approximate CDF value at x given 5 quantile points [min, Q1, Q2, Q3, max].
    Linear interpolation between known CDF values (0, 0.25, 0.50, 0.75, 1.0).
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# PSI WITH UNION BOUNDARIES (fixes range problem in original PSI)
# Original PSI uses base version's boundaries — if new version has wider range,
# new values fall into "out of range" bucket, artificially inflating PSI.
# Fix: use union of min(min_b, min_n) to max(max_b, max_n) as boundaries.
# Same approach used by Evidently AI and WhyLabs.
# ─────────────────────────────────────────────────────────────────────────────

def compute_psi_union(col_base: ColumnProfile, col_new: ColumnProfile,
                       row_base: int = 1000, row_new: int = 1000, cfg=None) -> Dict:
    """
    PSI computed using union boundaries — correct when ranges differ across versions.
    Bucket boundaries: [union_min, Q1_base, Median_base, Q3_base, union_max]
    Both versions' distributions evaluated against the same boundaries.
    """
    if not col_base.is_numeric() or not col_new.is_numeric():
        # Categorical: delegate to original logic
        from .insights import _psi_categorical
        #return _psi_categorical(col_base, col_new, row_base, row_new, cfg)
        return _psi_categorical(col_base, col_new, row_base, row_new)

    if col_base.statistical_scale in ("binary", "id", "unary"):
        return {"psi": None, "label": "not_applicable",
                "note": f"PSI not computed for scale='{col_base.statistical_scale}'",
                "applicable": False}

    # Require quantile data
    if any(v is None for v in [col_base.q25, col_base.q50, col_base.q75]):
        return {"psi": None, "label": "insufficient_data",
                "note": "Quantile data missing", "applicable": False}

    # Union boundaries
    union_min = min(_safe(col_base.min_val, col_base.q25),
                    _safe(col_new.min_val,  col_new.q25))
    union_max = max(_safe(col_base.max_val, col_base.q75),
                    _safe(col_new.max_val,  col_new.q75))

    boundaries = [union_min,
                  _safe(col_base.q25, 0),
                  _safe(col_base.q50, 0),
                  _safe(col_base.q75, 0),
                  union_max]

    # Estimate fraction of each column's data in each bucket
    base_props = _props_from_quantiles(col_base, boundaries)
    new_props  = _props_from_quantiles(col_new,  boundaries)

    # Adjust for completeness differences
    bc = _safe(col_base.completeness_percent, 100) / 100.0
    nc = _safe(col_new.completeness_percent,  100) / 100.0
    base_props = [p * bc for p in base_props]
    new_props  = [p * nc for p in new_props]

    base_props = _normalise(base_props)
    new_props  = _normalise(new_props)

    psi_total = 0.0
    buckets   = []
    labels    = ["≤Q1", "Q1–Median", "Median–Q3", "Q3–Max", "Out of range"]
    for i, (bp, np_) in enumerate(zip(base_props, new_props)):
        bp  = max(bp,  1e-6)
        np_ = max(np_, 1e-6)
        contrib = (np_ - bp) * math.log(np_ / bp)
        psi_total += contrib
        buckets.append({
            "bucket":   labels[i] if i < len(labels) else f"bucket_{i}",
            "base_pct": round(bp  * 100, 2),
            "new_pct":  round(np_ * 100, 2),
            "contrib":  round(contrib, 4),
        })

    label = _psi_label(psi_total, cfg)
    return {
        "psi":        round(psi_total, 4),
        "label":      label,
        "buckets":    buckets,
        "boundaries": [round(b, 4) for b in boundaries],
        "applicable": True,
        "note":       (f"PSI={psi_total:.4f} using union boundaries "
                       f"[{round(union_min,2)}, {round(union_max,2)}]. "
                       + _psi_note(psi_total, col_base.name, cfg)),
    }

def _union_cols(abts: List[ABTProfile]) -> List[str]:
    seen, order = set(), []
    for a in abts:
        for n in a.column_names:
            if n not in seen:
                seen.add(n); order.append(n)
    return order

def _psi_matrix_union(abts, cfg=None):
    """PSI matrix using union boundaries — replaces insights.psi_matrix."""
    from .drift_metrics import compute_psi_union
    if len(abts) < 2:
        return {}
    all_cols = _union_cols(abts)
    result = {}
    for col_name in all_cols:
        col_results = []
        for i in range(len(abts) - 1):
            a, b = abts[i], abts[i + 1]
            ca, cb = a.get_column(col_name), b.get_column(col_name)
            if ca is None or cb is None:
                col_results.append({"from": a.abt_name, "to": b.abt_name,
                                     "psi": None, "label": "absent",
                                     "note": "Column absent in one version",
                                     "applicable": False})
            else:
                pr = compute_psi_union(ca, cb, max(a.row_count, 1), max(b.row_count, 1), cfg=cfg)
                pr["from"] = a.abt_name
                pr["to"]   = b.abt_name
                col_results.append(pr)
        result[col_name] = col_results
    return result


def _props_from_quantiles(col: ColumnProfile, boundaries: List[float]) -> List[float]:
    """
    Estimate what fraction of a column's data falls in each bucket
    defined by boundaries, using the column's quantile points via CDF interpolation.
    """
    pts = _extract_quantile_points(col)
    if pts is None:
        # Fallback: assume uniform across 4 main buckets
        return [0.25, 0.25, 0.25, 0.25, 0.0]

    props = []
    for i in range(len(boundaries) - 1):
        lo, hi = boundaries[i], boundaries[i + 1]
        cdf_lo = _cdf_at(lo, pts)
        cdf_hi = _cdf_at(hi, pts)
        props.append(max(0.0, cdf_hi - cdf_lo))
    return props


def _normalise(props: List[float]) -> List[float]:
    total = sum(props)
    if total <= 0:
        return [1.0 / len(props)] * len(props)
    return [p / total for p in props]


def _psi_label(psi: float, cfg=None) -> str:
    if cfg is None:
        stable, monitor = PSI_STABLE, PSI_MONITOR
    else:
        stable, monitor = cfg.psi_stable, cfg.psi_monitor
    if psi < stable:  return "stable"
    if psi < monitor: return "monitor"
    return "shift"


def _psi_note(psi: float, col: str, cfg=None) -> str:
    if cfg is None:
        stable, monitor = PSI_STABLE, PSI_MONITOR
    else:
        stable, monitor = cfg.psi_stable, cfg.psi_monitor
    if psi < stable:
        return f"'{col}' distribution is stable."
    if psi < monitor:
        return f"'{col}' shows moderate drift — monitor and re-train if trend continues."
    return (f"'{col}' has significant population shift. "
            f"Model trained on base version will likely underperform.")


# ─────────────────────────────────────────────────────────────────────────────
# MASTER RUNNER — computes all 10 metrics for a column pair
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_drift_metrics(col_base: ColumnProfile, col_new: ColumnProfile,
                               row_base: int = 1000, row_new: int = 1000) -> Dict:
    """
    Compute all applicable drift metrics for a single column pair.
    Returns a dict keyed by metric name.
    Safe to call for any column type — non-applicable metrics return gracefully.
    """
    return {
        "cv_drift":       compute_cv_drift(col_base, col_new),
        "std_drift":      compute_std_drift(col_base, col_new),
        "quantile_shift": compute_quantile_shift(col_base, col_new),
        "boundary_drift": compute_boundary_drift(col_base, col_new),
        "kurtosis_drift": compute_kurtosis_drift(col_base, col_new),
        "entropy_drift":  compute_entropy_drift(col_base, col_new, row_base, row_new),
        "ks_approx":      compute_ks_approximation(col_base, col_new),
        "psi_union":      compute_psi_union(col_base, col_new, row_base, row_new),
    }


def compute_column_drift_suite(abts: List[ABTProfile]) -> Dict:
    """
    Run all pairwise drift metrics across N versions for all columns.
    Also computes FSI, drift velocity, and baseline drift.

    Returns:
        {
          "consecutive": {col_name: [metrics per pair]},
          "baseline":    baseline_drift result,
          "fsi":         {col_name: fsi_result},
          "velocity":    {col_name: velocity_result},
        }
    """
    if len(abts) < 2:
        return {}

    skip_scales = {"id", "unary"}
    all_cols = []
    seen = set()
    for a in abts:
        for n in a.column_names:
            if n not in seen:
                seen.add(n); all_cols.append(n)

    consecutive = {}
    fsi_results      = {}
    velocity_results = {}

    for col_name in all_cols:
        col_base_ref = abts[0].get_column(col_name)
        if col_base_ref is None or col_base_ref.statistical_scale in skip_scales:
            continue
        if col_name.lower() in TARGET_NAMES:
            continue

        pair_metrics = []
        psi_series   = []

        for i in range(len(abts) - 1):
            ca = abts[i].get_column(col_name)
            cb = abts[i + 1].get_column(col_name)
            if ca is None or cb is None:
                pair_metrics.append(None)
                psi_series.append(None)
                continue
            metrics = compute_all_drift_metrics(ca, cb,
                                                 max(abts[i].row_count, 1),
                                                 max(abts[i + 1].row_count, 1))
            pair_metrics.append(metrics)
            psi_val = metrics["psi_union"].get("psi")
            psi_series.append(psi_val)

        consecutive[col_name]    = pair_metrics
        fsi_results[col_name]    = compute_fsi(psi_series)
        velocity_results[col_name] = compute_drift_velocity(psi_series)

    return {
        "consecutive": consecutive,
        "baseline":    compute_baseline_drift(abts),
        "fsi":         fsi_results,
        "velocity":    velocity_results,
        "version_labels": [a.abt_name for a in abts],
    }


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _threshold_label(value: float, notable: float, critical: float) -> str:
    if value >= critical: return "critical"
    if value >= notable:  return "notable"
    return "stable"


def _not_applicable(metric: str, reason: str) -> Dict:
    return {"metric": metric, "applicable": False, "note": reason}