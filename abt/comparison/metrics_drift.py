"""
abt/metrics_drift.py
─────────────────────────────────────────────────────────────────────────────
Comparative and longitudinal drift metrics.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional
from abt.analysis.columnProfile import ColumnProfile, ABTProfile
from abt.insights.insights import _safe, PSI_STABLE, PSI_MONITOR
from abt.comparison.metrics_base import _extract_quantile_points, _cdf_at, _threshold_label, _not_applicable

FSI_UNSTABLE = 0.70
VELOCITY_NOTABLE  = 0.05
VELOCITY_CRITICAL = 0.15


# ── 7. FEATURE STABILITY INDEX (FSI) ─────────────────────────────────────────

def compute_fsi(psi_values: List[Optional[float]]) -> Dict:
    valid = [v for v in psi_values if v is not None and math.isfinite(v)]
    if not valid:
        return {"metric": "fsi", "fsi": None, "label": "insufficient_data",
                "applicable": False, "mean_psi": None}

    mean_psi = sum(valid) / len(valid)
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


# ── 8. DRIFT VELOCITY ────────────────────────────────────────────────────────

def compute_drift_velocity(psi_values: List[Optional[float]]) -> Dict:
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
        "velocity":    velocity,
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


# ── 9. BASELINE DRIFT ────────────────────────────────────────────────────────

def compute_baseline_drift(abts: List[ABTProfile]) -> Dict:
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


# ── PSI WITH UNION BOUNDARIES ────────────────────────────────────────────────

def compute_psi_union(col_base: ColumnProfile, col_new: ColumnProfile,
                       row_base: int = 1000, row_new: int = 1000, cfg=None) -> Dict:
    if not col_base.is_numeric() or not col_new.is_numeric():
        from abt.insights.insights import _psi_categorical
        return _psi_categorical(col_base, col_new, row_base, row_new)

    if col_base.statistical_scale in ("binary", "id", "unary"):
        return {"psi": None, "label": "not_applicable",
                "note": f"PSI not computed for scale='{col_base.statistical_scale}'",
                "applicable": False}

    if any(v is None for v in [col_base.q25, col_base.q50, col_base.q75]):
        return {"psi": None, "label": "insufficient_data",
                "note": "Quantile data missing", "applicable": False}

    union_min = min(_safe(col_base.min_val, col_base.q25),
                    _safe(col_new.min_val,  col_new.q25))
    union_max = max(_safe(col_base.max_val, col_base.q75),
                    _safe(col_new.max_val,  col_new.q75))

    boundaries = [union_min,
                  _safe(col_base.q25, 0),
                  _safe(col_base.q50, 0),
                  _safe(col_base.q75, 0),
                  union_max]

    base_props = _props_from_quantiles(col_base, boundaries)
    new_props  = _props_from_quantiles(col_new,  boundaries)

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
    pts = _extract_quantile_points(col)
    if pts is None:
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
