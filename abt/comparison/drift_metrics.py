"""
abt/drift_metrics.py
─────────────────────────────────────────────────────────────────────────────
Drift metrics orchestration hub.
"""

from __future__ import annotations
from typing import Dict, List, Optional

from abt.analysis.columnProfile import ColumnProfile, ABTProfile
from abt.insights.insights import TARGET_NAMES

# Expose thresholds
from abt.comparison.metrics_base import (
    CV_NOTABLE, CV_CRITICAL,
    STD_NOTABLE, STD_CRITICAL,
    QUANTILE_NOTABLE, QUANTILE_CRITICAL,
    BOUNDARY_NOTABLE, BOUNDARY_CRITICAL,
    KURTOSIS_NOTABLE, KURTOSIS_CRITICAL,
    ENTROPY_NOTABLE, ENTROPY_CRITICAL,
    KS_NOTABLE, KS_CRITICAL,
)
from abt.comparison.metrics_drift import (
    FSI_UNSTABLE,
    VELOCITY_NOTABLE, VELOCITY_CRITICAL,
)

# Expose base functions
from abt.comparison.metrics_base import (
    compute_cv_drift,
    compute_std_drift,
    compute_quantile_shift,
    compute_boundary_drift,
    compute_kurtosis_drift,
    compute_entropy_drift,
    compute_ks_approximation,
    _threshold_label,
    _not_applicable,
    _extract_quantile_points,
    _cdf_at,
)

# Expose drift functions
from abt.comparison.metrics_drift import (
    compute_fsi,
    compute_drift_velocity,
    compute_baseline_drift,
    compute_psi_union,
    _psi_matrix_union,
    _union_cols,
)


def compute_all_drift_metrics(col_base: ColumnProfile, col_new: ColumnProfile,
                               row_base: int = 1000, row_new: int = 1000) -> Dict:
    """Compute all applicable drift metrics for a single column pair."""
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
    """Run all pairwise drift metrics across N versions for all columns."""
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