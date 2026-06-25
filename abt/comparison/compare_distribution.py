"""
abt/compare_distribution.py
─────────────────────────────────────────────────────────────────────────────
Comparative value and distribution drift: completeness, target, readiness, quality and PSI.
"""

from typing import Dict, List, Optional
from abt.analysis.columnProfile import ABTProfile
from abt.analysis.analyze import s5_readiness, s8_column_health_scores, TARGET_NAMES
from abt.insights.insights import _safe
from abt.comparison.drift_metrics import _psi_matrix_union, _union_cols

DRIFT_NOTABLE          = 0.20
DRIFT_SEVERE           = 0.50
COMPLETENESS_DELTA_MIN = 5.0
TARGET_DRIFT_NOTABLE   = 0.03
TARGET_DRIFT_CRITICAL  = 0.08


# ── status helpers ─────────────────────────────────────────────────────────────
def _ord(s: str) -> int:
    return {"ready": 0, "caution": 1, "drop": 2, "absent": -1}.get(s, 0)

def _worsened(a, b): return _ord(b) > _ord(a)
def _improved(a, b): return _ord(b) < _ord(a)


def _trend(vals: List) -> str:
    c = [v for v in vals if v is not None]
    if len(c) < 2: return "unknown"
    if c[-1] > c[0]:   return "regressing"
    if c[-1] < c[0]:   return "improving"
    return "stable"

def _trend_numeric(vals: List) -> str:
    """For health scores: higher is better, so increasing = improving."""
    c = [v for v in vals if v is not None]
    if len(c) < 2: return "unknown"
    if c[-1] > c[0]:   return "improving"
    if c[-1] < c[0]:   return "declining"
    return "stable"


def _classify_missing_pattern(vals: List) -> str:
    present = [v for v in vals if v is not None]
    if not present:                                               return "absent"
    if all(v == 100 for v in present):                           return "complete"
    first, last = present[0], present[-1]
    if all(v < 50 for v in present):                             return "sparse"
    if first == 100 and last < 100:                              return "newly_missing"
    if first < 100 and last > first + 5:                         return "recovering"
    if last < first - 5:                                         return "growing_missing"
    if any(v < 100 for v in present) and abs(last - first) <= 5: return "stable_missing"
    return "fluctuating"


def _trend_slope(values: List) -> Optional[float]:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 3:
        return None
    n   = len(pts)
    sx  = sum(p[0] for p in pts)
    sy  = sum(p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0
    return round((n * sxy - sx * sy) / denom, 3)


def c3_completeness_drift(abts: List[ABTProfile]) -> Dict:
    all_cols = _union_cols(abts)
    rows = []
    for col_name in all_cols:
        vals = [(a.get_column(col_name).completeness_percent
                 if a.get_column(col_name) else None) for a in abts]
        changes = []
        for i in range(len(vals) - 1):
            v1, v2 = vals[i], vals[i + 1]
            if v1 is not None and v2 is not None and abs(v2 - v1) >= COMPLETENESS_DELTA_MIN:
                changes.append({"from_ver": abts[i].abt_name, "to_ver": abts[i+1].abt_name,
                                 "delta": round(v2 - v1, 1),
                                 "direction": "improved" if v2 > v1 else "regressed"})
        rows.append({
            "column": col_name, "values": vals, "changes": changes,
            "net_delta": round((vals[-1] or 0) - (vals[0] or 0), 1)
                         if vals[0] is not None and vals[-1] is not None else None,
            "missing_pattern": _classify_missing_pattern(vals),
        })
    return {"version_labels": [a.abt_name for a in abts], "rows": rows}


def c4_distribution_drift(abts: List[ABTProfile]) -> List[Dict]:
    skip = {"id", "binary", "unary"}
    base_cols = [c for c in abts[0].get_numeric_columns()
                 if c.statistical_scale not in skip and c.name.lower() not in TARGET_NAMES]
    results = []

    for col in base_cols:
        vstats = []
        for abt in abts:
            c = abt.get_column(col.name)
            if c and c.is_numeric():
                vstats.append({
                    "abt": abt.abt_name, "mean": c.mean, "std": c.std,
                    "skewness": c.skewness, "has_outliers": c.has_outliers,
                    "n_outliers": c.n_outliers, "min": c.min_val, "max": c.max_val,
                })
            else:
                vstats.append({"abt": abt.abt_name if abt else "?", "mean": None})

        flags = []
        for i in range(len(vstats) - 1):
            s1, s2 = vstats[i], vstats[i + 1]
            if s1.get("mean") is None or s2.get("mean") is None:
                continue
            std1      = max(_safe(s1.get("std"), 1), 1e-9)
            mean_drift = abs((_safe(s2["mean"]) - _safe(s1["mean"])) / std1)
            sk1, sk2  = s1.get("skewness"), s2.get("skewness")
            skew_flip  = (sk1 is not None and sk2 is not None and abs(_safe(sk2) - _safe(sk1)) > 0.5)
            outlier_new= s2.get("has_outliers") and not s1.get("has_outliers")
            if mean_drift > DRIFT_SEVERE or skew_flip:   sev = "critical"
            elif mean_drift > DRIFT_NOTABLE or outlier_new: sev = "notable"
            else:                                         sev = "stable"
            flags.append({
                "from_ver": s1["abt"], "to_ver": s2["abt"],
                "mean_delta": round(_safe(s2["mean"]) - _safe(s1["mean"]), 4),
                "drift_score": round(mean_drift, 3),
                "skew_flip": skew_flip, "outlier_new": outlier_new, "severity": sev,
            })

        sev_all = "stable"
        for f in flags:
            if f["severity"] == "critical": sev_all = "critical"; break
            if f["severity"] == "notable":  sev_all = "notable"

        results.append({
            "column": col.name, "scale": col.statistical_scale,
            "version_stats": vstats, "drift_flags": flags,
            "overall_severity": sev_all,
        })
    return results


def c5_target_drift(abts: List[ABTProfile]) -> Optional[Dict]:
    target_name = None
    for name in TARGET_NAMES:
        if abts[0].get_column(name):
            target_name = name; break
    if not target_name:
        return None

    vrates = []
    for abt in abts:
        col = abt.get_column(target_name)
        if col and col.mean is not None:
            er = _safe(col.mean, 0.0)
            vrates.append({
                "abt": abt.abt_name, "version": abt.version,
                "snapshot_date": abt.snapshot_date,
                "event_rate": round(er * 100, 2),
                "imbalance_ratio": round((1 - er) / er, 2) if er > 0 else None,
                "skewness": round(_safe(col.skewness, 0.0), 4),
            })
        else:
            vrates.append({"abt": abt.abt_name, "event_rate": None})

    pairwise = []
    for i in range(len(vrates) - 1):
        r1, r2 = vrates[i], vrates[i + 1]
        if r1.get("event_rate") is None or r2.get("event_rate") is None:
            continue
        delta = (r2["event_rate"] - r1["event_rate"]) / 100
        ad = abs(delta)
        if ad >= TARGET_DRIFT_CRITICAL:   sev, bt = "critical", True
        elif ad >= TARGET_DRIFT_NOTABLE:  sev, bt = "notable",  True
        else:                             sev, bt = "stable",   False
        pairwise.append({
            "from_ver": r1["abt"], "to_ver": r2["abt"],
            "delta_pp": round(delta * 100, 2), "abs_delta_pp": round(ad * 100, 2),
            "severity": sev, "back_test_required": bt,
        })

    return {"column": target_name, "version_rates": vrates, "pairwise_drift": pairwise}


def c6_quality_regression(abts: List[ABTProfile]) -> List[Dict]:
    all_cols = _union_cols(abts)
    results  = []
    for col_name in all_cols:
        mm = [(a.get_column(col_name).mismatched_count if a.get_column(col_name) else None) for a in abts]
        bv = [(a.get_column(col_name).blank_value_count if a.get_column(col_name) else None) for a in abts]
        if not any(v and v > 0 for v in mm + bv):
            continue
        results.append({
            "column": col_name, "mismatch_values": mm, "blank_values": bv,
            "mismatch_trend": _trend(mm), "blank_trend": _trend(bv),
            "version_labels": [a.abt_name for a in abts],
        })
    return results


def c7_readiness_change(abts: List[ABTProfile]) -> List[Dict]:
    all_cols    = _union_cols(abts)
    rd_per_ver  = [{r["column"]: r["status"] for r in s5_readiness(a)} for a in abts]
    results     = []
    for col_name in all_cols:
        statuses = [rd.get(col_name, "absent") for rd in rd_per_ver]
        present  = [s for s in statuses if s != "absent"]
        if not present: continue
        if statuses[0] == "absent":                    ctype = "new"
        elif statuses[-1] == "absent":                 ctype = "dropped"
        elif _worsened(statuses[0], statuses[-1]):     ctype = "worsened"
        elif _improved(statuses[0], statuses[-1]):     ctype = "improved"
        else:                                          ctype = "stable"
        results.append({
            "column": col_name, "statuses": statuses,
            "version_labels": [a.abt_name for a in abts],
            "change_type": ctype,       
            "highlight": ctype in ("worsened", "dropped"),
        })
    return sorted(results, key=lambda x: (0 if x["highlight"] else 1, x["column"]))


def c8_psi_matrix(abts: List[ABTProfile]) -> Dict:
    raw = _psi_matrix_union(abts)
    columns = []
    stable_count = monitor_count = shift_count = 0
    critical_columns = []

    for col_name, pair_results in raw.items():
        labels = [p.get("label", "not_applicable") for p in pair_results
                  if p.get("applicable")]
        if not labels:
            worst_label = "not_applicable"
        elif "shift" in labels:
            worst_label = "shift"; shift_count += 1; critical_columns.append(col_name)
        elif "monitor" in labels:
            worst_label = "monitor"; monitor_count += 1
        else:
            worst_label = "stable"; stable_count += 1

        pairs_summary = []
        for p in pair_results:
            pairs_summary.append({
                "from":       p.get("from", ""),
                "to":         p.get("to", ""),
                "psi":        p.get("psi"),
                "label":      p.get("label", "n/a"),
                "applicable": p.get("applicable", False),
                "note":       p.get("note", ""),
                "buckets":    p.get("buckets", []),
            })

        columns.append({
            "column":      col_name,
            "worst_label": worst_label,
            "pairs":       pairs_summary,
        })

    order = {"shift": 0, "monitor": 1, "stable": 2, "not_applicable": 3}
    columns.sort(key=lambda x: order.get(x["worst_label"], 3))

    return {
        "version_labels":   [a.abt_name for a in abts],
        "columns":          columns,
        "summary": {
            "stable_count":    stable_count,
            "monitor_count":   monitor_count,
            "shift_count":     shift_count,
            "critical_columns":critical_columns,
        },
    }


def c9_health_score_trend(abts: List[ABTProfile]) -> Dict:
    all_cols = _union_cols(abts)

    per_version_scores = []
    per_version_readiness = []
    for abt in abts:
        hs = s8_column_health_scores(abt)
        rs = s5_readiness(abt)
        per_version_scores.append(hs)
        per_version_readiness.append(rs)

    from abt.insights.insights import dataset_readiness_score
    dataset_scores = []
    for i, abt in enumerate(abts):
        dr = dataset_readiness_score(per_version_scores[i], per_version_readiness[i])
        dataset_scores.append({"abt": abt.abt_name, "score": dr["score"], "label": dr["label"]})

    columns = []
    for col_name in all_cols:
        scores = [
            per_version_scores[i].get(col_name, {}).get("score")
            for i in range(len(abts))
        ]
        present_scores = [s for s in scores if s is not None]
        trend = _trend_numeric(scores)
        delta = round(present_scores[-1] - present_scores[0], 1) if len(present_scores) >= 2 else None
        worst = min(present_scores) if present_scores else None

        columns.append({
            "column":      col_name,
            "scores":      scores,
            "trend":       trend,
            "delta":       delta,
            "worst_score": worst,
            "highlight":   (worst is not None and worst < 40) or trend == "declining",
        })

    columns.sort(key=lambda x: (0 if x["highlight"] else 1, (x["worst_score"] or 100)))

    ds_score_vals = [d["score"] for d in dataset_scores]
    ds_slope = _trend_slope(ds_score_vals)
    if ds_slope is not None:
        if ds_slope > 1.0:      ds_trend_dir = "strongly_improving"
        elif ds_slope > 0.2:    ds_trend_dir = "improving"
        elif ds_slope < -1.0:   ds_trend_dir = "strongly_declining"
        elif ds_slope < -0.2:   ds_trend_dir = "declining"
        else:                   ds_trend_dir = "stable"
    else:
        ds_trend_dir = "insufficient_data"

    return {
        "version_labels":     [a.abt_name for a in abts],
        "columns":            columns,
        "dataset_scores":     dataset_scores,
        "trend_slope":        ds_slope,
        "trend_direction":    ds_trend_dir,
        "trend_note": (
            f"Dataset readiness is {ds_trend_dir.replace('_', ' ')} "
            f"(slope {ds_slope:+.2f} pts/version)" if ds_slope is not None
            else "Not enough versions for trend analysis (need ≥ 3)"
        ),
    }


def c0_compare_verdict(c5_result: Optional[Dict], c8_result: Dict,
                         c9_result: Dict, c1_result: Dict) -> Dict:
    issues = []
    back_test_required = False
    block = False

    if c5_result:
        for pw in c5_result.get("pairwise_drift", []):
            if pw["severity"] == "critical":
                back_test_required = True
                issues.append(f"Target event rate shifted {pw['delta_pp']:+.1f}pp "
                               f"({pw['from_ver']}→{pw['to_ver']}) — critical drift")
            elif pw["severity"] == "notable":
                back_test_required = True
                issues.append(f"Target event rate shifted {pw['delta_pp']:+.1f}pp "
                               f"({pw['from_ver']}→{pw['to_ver']}) — notable drift")

    shift_cols = c8_result.get("summary", {}).get("critical_columns", [])
    if shift_cols:
        back_test_required = True
        issues.append(f"{len(shift_cols)} feature(s) with PSI > 0.25: {', '.join(shift_cols[:5])}"
                      + (" ..." if len(shift_cols) > 5 else ""))

    for pw in c1_result.get("pairwise", []):
        if len(pw.get("worsened", [])) >= 2:
            back_test_required = True
            issues.append(f"{len(pw['worsened'])} columns degraded in readiness "
                          f"({pw['from']}→{pw['to']})")

    ds_scores = c9_result.get("dataset_scores", [])
    if len(ds_scores) >= 2:
        first_s = ds_scores[0]["score"]
        last_s  = ds_scores[-1]["score"]
        if last_s < 40:
            block = True
            issues.append(f"Dataset readiness dropped to {last_s}/100 — not ready for training")
        elif last_s < first_s - 15:
            back_test_required = True
            issues.append(f"Dataset readiness declined {first_s:.0f}→{last_s:.0f} across versions")
        elif last_s < first_s - 8:
            if not back_test_required:
                issues.append(f"Dataset readiness declined {first_s:.0f}→{last_s:.0f} — monitor")

    if block:
        verdict = "BLOCK"
        color   = "red"
        message = "Dataset quality has degraded significantly. Do not proceed to model training."
    elif back_test_required:
        verdict = "BACK_TEST_REQUIRED"
        color   = "amber"
        message = "Significant drift detected. Back-testing the existing model on new data is mandatory before any promotion decision."
    elif issues:
        verdict = "MONITOR"
        color   = "blue"
        message = "Minor changes detected. Monitor closely across next versions."
    else:
        verdict = "CLEAR"
        color   = "green"
        message = "No significant drift detected. Dataset is stable across compared versions."

    return {
        "verdict": verdict, "color": color,
        "message": message, "issues": issues,
        "back_test_required": back_test_required,
    }
