# Monday 
"""
abt/signal_collector.py  (v2 — column-wise collection)
─────────────────────────────────────────────────────────────────────────────
Refactored to iterate COLUMNS first, METRICS second.

Old approach: one function per source section (C4, C8, C10...) → duplication
New approach: for each column, gather ALL available metrics from all sources,
              score each metric, keep best as primary, rest as supporting.

Changes from v1:
  - collect_signals() builds a column index first, then scores per column
  - No blank padding — top_population_signals() returns only real signals
  - _build_column_index() gathers raw data per column from all sections
  - _score_column() converts raw data into a ranked Signal dict
  - Schema signals still collected separately (no column profile exists)
  - Slot helpers unchanged — same API for business_insights.py

Output contract unchanged:
  collect_signals(results) → List[Signal]  (ranked, deduplicated, no padding)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

_TARGET_NAMES = {"target", "bad", "default", "event", "flag", "label", "y"}

_SEV_W = {
    "critical": 1.0, "notable": 0.6,
    "shift":    1.0, "monitor": 0.5,
    "stable":   0.05,
}

_MET_W = {
    "schema":         0.95,
    "psi":            1.00,
    "mean_shift":     0.85,
    "missingness":    0.80,
    "quantile_shift": 0.75,
    "cardinality":    0.70,
    "boundary":       0.65,
    "std":            0.55,
    "kurtosis":       0.45,
    "entropy":        0.40,
}

_TIE_ORDER = {
    "schema": 0, "psi": 1, "mean_shift": 2,
    "missingness": 3, "cardinality": 4, "quantile_shift": 5,
    "boundary": 6, "std": 7, "kurtosis": 8, "entropy": 9,
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _g(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _is_target(col_name: str) -> bool:
    return col_name.lower() in _TARGET_NAMES


def _magnitude(severity: str, metric_type: str, is_private: bool = False) -> float:
    sw    = _SEV_W.get(severity, 0.1)
    mw    = _MET_W.get(metric_type, 0.5)
    bonus = 0.30 if is_private else 0.0
    return round(min(1.0, sw * mw * (1.0 + bonus)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE COLUMN INDEX
# ─────────────────────────────────────────────────────────────────────────────

def _build_private_index(results: dict) -> set:
    private_cols = set()
    for item in results.get("s4", []):
        for risk in item.get("risks", []):
            if risk.get("risk_type") == "PRIVACY":
                private_cols.add(item["column"])
    return private_cols


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE CLASSIFIER (per column)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_root_cause(
    miss_pattern: Optional[str],
    row_delta_pct: Optional[float],
    i4_likely_cause: Optional[str],
    metric_type: str,
    is_schema: bool = False,
) -> str:
    if is_schema:
        return "schema_event"
    if metric_type == "missingness" or miss_pattern in ("growing_missing", "newly_missing", "sparse"):
        return "pipeline_issue"
    if row_delta_pct is not None and abs(row_delta_pct) > 20:
        return "sampling_change"
    if i4_likely_cause in ("organic_population_change", "organic_shift"):
        return "organic_shift"
    if i4_likely_cause == "sampling_change":
        return "sampling_change"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# SLOT ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def _assign_slot(metric_type: str, root_cause: str, is_private: bool) -> str:
    """
    Routes a signal to its primary insight slot.
    Rules (in priority order):
    1. Schema signals → governance (private) or model_risk (public)
    2. Private columns → governance
    3. Pipeline root cause or missingness metric → pipeline
    4. Everything else → population (top drift stories)
       Cardinality is also population — new categories = real population change
       I6 handles model_risk separately; slot assignment is independent
    """
    if metric_type == "schema":
        return "governance" if is_private else "model_risk"
    if is_private:
        return "governance"
    if root_cause == "pipeline_issue" or metric_type == "missingness":
        return "pipeline"
    return "population"


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN INDEX BUILDER
# Collects raw metric data per column from all result sections.
# Returns: {col_name: {metric_type: raw_data_dict}}
# ─────────────────────────────────────────────────────────────────────────────

def _build_column_index(
    c3: dict,
    c4: list,
    c8: dict,
    c10: list,
    drift_suite: dict,
    miss_patterns: dict,
) -> Dict[str, Dict[str, dict]]:
    """
    For each column, gather raw metric data from every available source.
    Schema signals are handled separately — they have no column profile.
    """
    index: Dict[str, Dict[str, dict]] = {}

    def _ensure(col_name: str):
        if col_name not in index:
            index[col_name] = {}

    # ── From C8: PSI per column ───────────────────────────────────────────
    for col_entry in c8.get("columns", []):
        col_name    = col_entry["column"]
        worst_label = col_entry.get("worst_label", "stable")
        if _is_target(col_name) or worst_label not in ("shift", "monitor"):
            continue
        psi_val = None
        for pair in col_entry.get("pairs", []):
            if pair.get("applicable") and pair.get("psi") is not None:
                psi_val = max(psi_val, pair["psi"]) if psi_val is not None else pair["psi"]
        _ensure(col_name)
        index[col_name]["psi"] = {
            "psi":          psi_val,
            "worst_label":  worst_label,
            "severity":     "critical" if worst_label == "shift" else "notable",
            "pairs":        col_entry.get("pairs", []),
            "miss_pattern": miss_patterns.get(col_name),
        }

    # ── From C4: mean shift per column ────────────────────────────────────
    for col in c4:
        col_name = col["column"]
        overall  = col.get("overall_severity", "stable")
        if _is_target(col_name) or overall not in ("critical", "notable"):
            continue
        flags  = col.get("drift_flags", [])
        vstats = col.get("version_stats", [])
        if not flags:
            continue
        last_flag = flags[-1]
        _ensure(col_name)
        index[col_name]["mean_shift"] = {
            "severity":    overall,
            "mean_delta":  _safe(last_flag.get("mean_delta"), 0.0),
            "drift_score": _safe(last_flag.get("drift_score"), 0.0),
            "skew_flip":   last_flag.get("skew_flip", False),
            "old_mean":    _safe(vstats[0].get("mean")) if vstats else None,
            "new_mean":    _safe(vstats[-1].get("mean")) if len(vstats) > 1 else None,
            "scale":       col.get("scale", "interval"),
            "miss_pattern":miss_patterns.get(col_name),
        }

    # ── From C3: missingness per column ───────────────────────────────────
    bad_patterns = {"growing_missing", "newly_missing", "sparse"}
    for row in c3.get("rows", []):
        col_name = row["column"]
        pattern  = row.get("missing_pattern", "complete")
        if _is_target(col_name) or pattern not in bad_patterns:
            continue
        _ensure(col_name)
        index[col_name]["missingness"] = {
            "severity":  "critical" if pattern in ("growing_missing", "newly_missing")
                         else "notable",
            "pattern":   pattern,
            "net_delta": row.get("net_delta"),
            "values":    row.get("values", []),
            "changes":   row.get("changes", []),
        }

    # ── From C10: cardinality per column ──────────────────────────────────
    for item in c10:
        col_name   = item["column"]
        explosions = item.get("explosions", [])
        if not explosions:
            continue
        worst_exp = max(
            explosions,
            key=lambda e: 0 if e.get("severity") == "dropped"
                          else (2 if e.get("severity") == "critical" else 1)
        )
        sev = worst_exp.get("severity", "notable")
        if sev == "dropped":
            sev = "notable"
        _ensure(col_name)
        index[col_name]["cardinality"] = {
            "severity":      sev,
            "explosions":    explosions,
            "cardinalities": item.get("cardinalities", []),
            "scale":         item.get("scale", "nominal"),
        }

    # ── From drift_suite: advanced metrics per column ─────────────────────
    consec = _g(drift_suite, "consecutive", default={})
    for col_name, pair_list in consec.items():
        if _is_target(col_name) or not pair_list:
            continue
        last_pm = pair_list[-1]
        if not last_pm:
            continue
        _ensure(col_name)
        for metric_type, metric_key in [
            ("quantile_shift", "quantile_shift"),
            ("boundary",       "boundary_drift"),
            ("std",            "std_drift"),
            ("kurtosis",       "kurtosis_drift"),
        ]:
            m   = last_pm.get(metric_key) or {}
            sev = m.get("severity", "stable")
            if not m.get("applicable", False) or sev not in ("notable", "critical"):
                continue
            # Only store if not already covered by a higher-weight metric
            if metric_type not in index[col_name]:
                index[col_name][metric_type] = {
                    "severity":      sev,
                    "metric_detail": m,
                    "interpretation":m.get("interpretation", ""),
                }

    return index


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN SCORER
# Converts one column's raw metric dict into a ranked Signal.
# Best metric becomes primary; rest become supporting evidence.
# ─────────────────────────────────────────────────────────────────────────────

def _score_column(
    col_name: str,
    metrics: Dict[str, dict],
    is_private: bool,
    miss_patterns: dict,
    row_delta_pct: Optional[float],
    i4_likely_cause: Optional[str],
) -> Optional[dict]:
    """
    Given all metrics collected for one column, produce a single Signal dict.
    Returns None if no metric exceeds stable threshold.
    """
    # Get PSI value for this column if available — anchors the ranking
    # psi_val=None means PSI was not collected for this column (no C8 entry)
    # psi_val=0.0 means PSI was computed but is genuinely zero (no shift)
    psi_raw   = metrics.get("psi", {})
    has_psi   = "psi" in metrics
    psi_val   = psi_raw.get("psi")   # keep None distinct from 0.0
    psi_float = _safe(psi_val, 0.0)  # safe float for arithmetic

    scored = []
    for metric_type, raw in metrics.items():
        sev = raw.get("severity", "stable")
        if sev == "stable":
            continue
        mag = _magnitude(sev, metric_type, is_private)

        if metric_type == "psi":
            if psi_val is not None and psi_float > 0.10:
                # Real PSI — boost proportional to magnitude
                # PSI=0.25 → 1.0× | PSI=1.0 → 4× (capped at 1.0 final)
                psi_boost = min(4.0, psi_float / 0.25)
                mag = round(min(1.0, mag * psi_boost), 4)
            else:
                # PSI=0 or near-zero — demote this metric heavily
                mag = round(mag * 0.1, 4)
        elif has_psi and psi_val is not None and psi_float <= 0.10:
            # Column has PSI data but PSI is low (≤0.10 = stable)
            # Non-PSI metrics flagged on this column are likely noise
            mag = round(mag * 0.25, 4)
        # If no PSI data at all: use magnitude as-is — no penalty, no boost

        scored.append((mag, metric_type, raw, sev))

    if not scored:
        return None

    # Sort best first by adjusted magnitude
    scored.sort(key=lambda x: (-x[0], _TIE_ORDER.get(x[1], 99)))

    best_mag, best_mt, best_raw, best_sev = scored[0]
    miss_pattern = miss_patterns.get(col_name)

    root_cause = _classify_root_cause(
        miss_pattern, row_delta_pct, i4_likely_cause, best_mt
    )
    slot = _assign_slot(best_mt, root_cause, is_private)

    # Direction
    direction = _infer_direction(best_mt, best_raw)

    # Build supporting list from remaining metrics
    supporting = []
    for mag, mt, raw, sev in scored[1:]:
        supporting.append({
            "metric_type": mt,
            "severity":    sev,
            "magnitude":   mag,
            "evidence":    raw,
        })

    return {
        "column":      col_name,
        "metric_type": best_mt,
        "severity":    best_sev,
        "magnitude":   best_mag,
        "root_cause":  root_cause,
        "direction":   direction,
        "evidence":    best_raw,
        "is_private":  is_private,
        "slot_hint":   slot,
        "supporting":  supporting,
        # Convenience: all_metrics for LLM prompt building
        "all_metrics": {mt: raw for _, mt, raw, _ in scored},
    }


def _infer_direction(metric_type: str, raw: dict) -> str:
    if metric_type == "mean_shift":
        delta = _safe(raw.get("mean_delta"), 0.0)
        return "up" if delta > 0 else "down"
    if metric_type == "psi":
        return "shift"
    if metric_type == "missingness":
        return "down"
    if metric_type == "cardinality":
        explosions = raw.get("explosions", [])
        if explosions and explosions[-1].get("severity") == "dropped":
            return "dropped"
        return "expand"
    if metric_type == "boundary":
        md = raw.get("metric_detail", {})
        upper = _safe(md.get("upper_shift"), 0.0)
        lower = _safe(md.get("lower_shift"), 0.0)
        return "expand" if upper > 0 or lower < 0 else "contract"
    if metric_type == "std":
        md  = raw.get("metric_detail", {})
        sb  = _safe(md.get("std_base"), 0.0)
        sn  = _safe(md.get("std_new"),  0.0)
        return "expand" if sn > sb else "contract"
    return "mixed"


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA SIGNALS (separate — no column profile exists for dropped columns)
# ─────────────────────────────────────────────────────────────────────────────

def _schema_signals(c2: list, private_cols: set) -> List[dict]:
    signals     = []
    last_schema = c2[-1] if c2 else {}

    for col in last_schema.get("dropped", []):
        col_name   = col["column"]
        is_private = col_name in private_cols
        signals.append({
            "column":      col_name,
            "metric_type": "schema",
            "severity":    "critical",
            "magnitude":   _magnitude("critical", "schema", is_private),
            "root_cause":  "schema_event",
            "direction":   "dropped",
            "evidence": {
                "change_type":       "dropped",
                "last_completeness": col.get("last_completeness"),
                "note":              col.get("note", ""),
            },
            "is_private":  is_private,
            "slot_hint":   "governance" if is_private else "model_risk",
            "supporting":  [],
            "all_metrics": {},
        })

    for col in last_schema.get("type_changed", []):
        col_name   = col["column"]
        is_private = col_name in private_cols
        signals.append({
            "column":      col_name,
            "metric_type": "schema",
            "severity":    "critical",
            "magnitude":   _magnitude("critical", "schema", is_private),
            "root_cause":  "schema_event",
            "direction":   "mixed",
            "evidence": {
                "change_type": "type_changed",
                "from_type":   col.get("from_type"),
                "to_type":     col.get("to_type"),
                "note":        col.get("note", ""),
            },
            "is_private":  is_private,
            "slot_hint":   "governance" if is_private else "model_risk",
            "supporting":  [],
            "all_metrics": {},
        })

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def collect_signals(results: dict) -> List[dict]:
    """
    Build unified ranked signal pool — column-wise approach.

    For each column:
      1. Gather all available metrics from C3/C4/C8/C10/drift_suite
      2. Score each metric by magnitude
      3. Best metric = primary signal; rest = supporting evidence
      4. One Signal dict per column

    Schema signals added separately (no column profile for dropped columns).
    Returns ranked list — no padding, no blank entries.
    """
    c2          = results.get("c2", [])
    c3          = results.get("c3", {})
    c4          = results.get("c4", [])
    c8          = results.get("c8", {})
    c10         = results.get("c10", [])
    drift_suite = results.get("drift_suite", {})
    i4          = results.get("i4", {})

    miss_patterns: Dict[str, str] = {
        row["column"]: row.get("missing_pattern", "complete")
        for row in c3.get("rows", [])
    }

    row_delta_pct   = i4.get("row_delta_pct")
    i4_likely_cause = i4.get("likely_cause")
    private_cols    = _build_private_index(results)

    # Step 1: Build column index
    col_index = _build_column_index(c3, c4, c8, c10, drift_suite, miss_patterns)

    # Step 2: Score each column → one Signal
    signals: List[dict] = []
    for col_name, metrics in col_index.items():
        sig = _score_column(
            col_name, metrics, col_name in private_cols,
            miss_patterns, row_delta_pct, i4_likely_cause,
        )
        if sig is not None:
            signals.append(sig)

    # Step 3: Add schema signals
    signals += _schema_signals(c2, private_cols)

    # Step 4: Rank by magnitude descending
    signals.sort(key=lambda s: (-s["magnitude"], _TIE_ORDER.get(s["metric_type"], 99)))

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# SLOT FILTER HELPERS  (unchanged API — business_insights.py uses these)
# ─────────────────────────────────────────────────────────────────────────────

def signals_for_slot(signals: List[dict], slot: str) -> List[dict]:
    """Return signals whose slot_hint matches, ranked."""
    return [s for s in signals if s.get("slot_hint") == slot]


def top_population_signals(signals: List[dict], n: int = 3) -> List[dict]:
    """
    Return up to N population-slot signals.
    Returns fewer than N if fewer real signals exist — NO padding.
    """
    pop = [s for s in signals if s.get("slot_hint") == "population"]
    return pop[:n]


def has_severity(signals: List[dict], slot: str, level: str = "critical") -> bool:
    return any(
        s.get("severity") == level
        for s in signals
        if s.get("slot_hint") == slot
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE B — RAW FACT EXTRACTION
# Goal   : give LLM actual computed numbers, not pre-rendered prose
# Rule   : latest pair for mean/quantile/boundary | worst pair for PSI
# ─────────────────────────────────────────────────────────────────────────────

def _extract_raw_facts(
    col_name:         str,
    metrics_last_pair: dict,          # drift_suite consecutive[-1] for this col
    fsi_result:        dict,          # drift_suite fsi[col_name]
    velocity_result:   dict,          # drift_suite velocity[col_name]
    baseline_result:   dict,          # drift_suite baseline
    c4_vstats:         list,          # c4 version_stats for this col (list of dicts)
    c3_values:         list,          # c3 completeness values list for this col
    worst_psi_pair:    dict,          # C8 pair with highest PSI for this col
) -> dict:
    """
    Extract raw numeric facts for one column from all available sources.
    No computation beyond safe arithmetic for mean_delta_pct.
    Every value is a float, int, str, or None — nothing rendered.

    Sources:
        drift_suite consecutive[-1]  → quantile, boundary, std, cv, psi (latest pair)
        C8 worst pair                → PSI (worst pair across all pairs)
        drift_suite fsi/velocity     → longitudinal stability
        drift_suite baseline         → distance from V1
        C4 version_stats[0/-1]       → mean before/after (immediately preceding pair)
        C3 values[0/-1]              → completeness before/after
    """
    if not metrics_last_pair:
        metrics_last_pair = {}

    psi_m    = metrics_last_pair.get("psi_union",      {}) or {}
    quant    = metrics_last_pair.get("quantile_shift",  {}) or {}
    boundary = metrics_last_pair.get("boundary_drift",  {}) or {}
    std_m    = metrics_last_pair.get("std_drift",       {}) or {}
    cv_m     = metrics_last_pair.get("cv_drift",        {}) or {}

    # ── PSI: worst pair (from C8) ─────────────────────────────────────────
    worst_psi       = worst_psi_pair.get("psi")   if worst_psi_pair else psi_m.get("psi")
    worst_psi_label = worst_psi_pair.get("label") if worst_psi_pair else psi_m.get("label")
    worst_psi_pair_name = (
        f"{worst_psi_pair.get('from', '')}→{worst_psi_pair.get('to', '')}"
        if worst_psi_pair else None
    )

    # ── Mean before/after: latest pair from C4 version_stats ─────────────
    mean_before = mean_after = mean_delta = mean_delta_pct = None
    if c4_vstats and len(c4_vstats) >= 2:
        # latest pair = last two entries
        prev_stat = c4_vstats[-2]
        curr_stat = c4_vstats[-1]
        mean_before = prev_stat.get("mean")
        mean_after  = curr_stat.get("mean")
        if mean_before is not None and mean_after is not None:
            mean_delta = round(_safe(mean_after) - _safe(mean_before), 4)
            if abs(_safe(mean_before)) > 1e-9:
                mean_delta_pct = round(
                    ((_safe(mean_after) - _safe(mean_before)) / abs(_safe(mean_before))) * 100,
                    2
                )

    # ── Completeness before/after: latest pair from C3 ───────────────────
    completeness_before = completeness_after = None
    if c3_values and len(c3_values) >= 2:
        completeness_before = c3_values[-2]
        completeness_after  = c3_values[-1]

    # ── Baseline drift vs V1 ──────────────────────────────────────────────
    baseline_psi = baseline_label = None
    baseline_col = (baseline_result or {}).get("columns", {}).get(col_name, {})
    baseline_comparisons = baseline_col.get("comparisons", [])
    if baseline_comparisons:
        last_base      = baseline_comparisons[-1]
        baseline_psi   = last_base.get("psi")
        baseline_label = last_base.get("label")

    # ── Quantile shifts (latest pair, IQR-normalized) ────────────────────
    shifts       = quant.get("shifts") or {}
    q1_shift     = shifts.get("Q1")
    median_shift = shifts.get("Median")
    q3_shift     = shifts.get("Q3")
    iqr_base     = quant.get("iqr_base")
    worst_q      = quant.get("worst_quantile")

    # ── Boundary (latest pair) ────────────────────────────────────────────
    min_base    = boundary.get("min_base")
    min_new     = boundary.get("min_new")
    max_base    = boundary.get("max_base")
    max_new     = boundary.get("max_new")
    upper_shift = boundary.get("upper_shift")
    lower_shift = boundary.get("lower_shift")

    # ── Std / CV (latest pair) ────────────────────────────────────────────
    std_base      = std_m.get("std_base")
    std_new       = std_m.get("std_new")
    std_norm      = std_m.get("norm_change")
    cv_base       = cv_m.get("cv_base")
    cv_new        = cv_m.get("cv_new")
    cv_rel_change = cv_m.get("relative_change")

    # ── FSI + velocity ────────────────────────────────────────────────────
    fsi       = (fsi_result      or {}).get("fsi")
    fsi_label = (fsi_result      or {}).get("label")
    fsi_mean_psi = (fsi_result   or {}).get("mean_psi")
    velocity  = (velocity_result or {}).get("velocity")
    vel_label = (velocity_result or {}).get("label")

    return {
        # ── PSI (worst pair) ──────────────────────────────────────────────
        "psi":                 worst_psi,
        "psi_label":           worst_psi_label,
        "psi_worst_pair":      worst_psi_pair_name,

        # ── Mean (latest pair) ────────────────────────────────────────────
        "mean_before":         mean_before,
        "mean_after":          mean_after,
        "mean_delta":          mean_delta,
        "mean_delta_pct":      mean_delta_pct,   # e.g. -12.6 means dropped 12.6%

        # ── Completeness (latest pair) ────────────────────────────────────
        "completeness_before": completeness_before,
        "completeness_after":  completeness_after,

        # ── Quantile shifts (latest pair, IQR-normalized) ─────────────────
        "q1_shift_iqr":        q1_shift,
        "median_shift_iqr":    median_shift,
        "q3_shift_iqr":        q3_shift,
        "iqr_base":            iqr_base,
        "worst_quantile":      worst_q,

        # ── Boundary (latest pair) ────────────────────────────────────────
        "min_base":            min_base,
        "min_new":             min_new,
        "max_base":            max_base,
        "max_new":             max_new,
        "upper_shift":         upper_shift,   # fraction of base range
        "lower_shift":         lower_shift,

        # ── Std / CV (latest pair) ────────────────────────────────────────
        "std_base":            std_base,
        "std_new":             std_new,
        "std_norm_change":     std_norm,      # e.g. 0.35 = 35% relative change
        "cv_base":             cv_base,
        "cv_new":              cv_new,
        "cv_rel_change":       cv_rel_change,

        # ── Baseline vs V1 ────────────────────────────────────────────────
        "baseline_psi":        baseline_psi,
        "baseline_label":      baseline_label,

        # ── Longitudinal ──────────────────────────────────────────────────
        "fsi":                 fsi,
        "fsi_label":           fsi_label,
        "fsi_mean_psi":        fsi_mean_psi,   # mean PSI across all pairs
        "velocity":            velocity,        # PSI units/version (+ = accelerating)
        "velocity_label":      vel_label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE B — COMPOSITE IMPORTANCE SCORING
# Replaces PSI-only boost with multi-metric weighted score.
# ─────────────────────────────────────────────────────────────────────────────

def _compute_composite_importance(raw_facts: dict, is_private: bool) -> float:
    """
    Multi-metric importance score 0.0–1.0.

    Weights:
        PSI            50%  — primary drift signal
        mean_delta_pct 30%  — distribution centre movement
        quantile_shift 20%  — where in the distribution the shift is

    Normalization denominators (chosen so "severe" maps to ~1.0):
        PSI:            0.50  (severe shift threshold)
        mean_delta_pct: 30%   (30% mean change = fully important)
        quantile_shift: 1.0×  (1.0× IQR = large shift)

    Privacy bonus: +30% on top (governance escalation).
    Falls back to 0.0 for any missing value (never raises).
    """
    # PSI component
    psi_val  = _safe(raw_facts.get("psi"), 0.0)
    psi_norm = min(psi_val / 0.50, 1.0)

    # Mean shift component (absolute %)
    mean_pct  = abs(_safe(raw_facts.get("mean_delta_pct"), 0.0))
    mean_norm = min(mean_pct / 30.0, 1.0)

    # Quantile shift component (max across Q1/Median/Q3)
    q_shift = max(
        abs(_safe(raw_facts.get("q1_shift_iqr"),     0.0)),
        abs(_safe(raw_facts.get("median_shift_iqr"), 0.0)),
        abs(_safe(raw_facts.get("q3_shift_iqr"),     0.0)),
    )
    quant_norm = min(q_shift / 1.0, 1.0)

    importance = 0.50 * psi_norm + 0.30 * mean_norm + 0.20 * quant_norm
    bonus      = 0.30 if is_private else 0.0
    return round(min(1.0, importance * (1.0 + bonus)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE B — COLUMN INDEX V2
# Same structure as _build_column_index() but attaches raw_facts to every
# metric entry and exposes them at the column level for the LLM fact sheet.
# ─────────────────────────────────────────────────────────────────────────────

def _build_column_index_v2(
    c3: dict,
    c4: list,
    c8: dict,
    c10: list,
    drift_suite: dict,
    miss_patterns: dict,
) -> Dict[str, Dict[str, dict]]:
    """
    Extended version of _build_column_index().
    Adds raw_facts dict to each column entry, sourced from drift_suite,
    C4 version_stats, C3 completeness values, and C8 worst PSI pair.

    Returns: {col_name: {metric_type: ..., "raw_facts": {...}}}
    """
    # ── Build base index (identical logic to v1) ──────────────────────────
    index = _build_column_index(c3, c4, c8, c10, drift_suite, miss_patterns)

    # ── Build fast-lookup indices ─────────────────────────────────────────
    # C4: {col_name: version_stats list}
    c4_vstats_by_col: Dict[str, list] = {
        col["column"]: col.get("version_stats", [])
        for col in c4
    }

    # C3: {col_name: completeness values list}
    c3_values_by_col: Dict[str, list] = {
        row["column"]: row.get("values", [])
        for row in c3.get("rows", [])
    }

    # C8: {col_name: pair with highest PSI}
    c8_worst_pair_by_col: Dict[str, dict] = {}
    for col_entry in c8.get("columns", []):
        col_name = col_entry["column"]
        best_pair = None
        best_psi  = -1.0
        for pair in col_entry.get("pairs", []):
            if pair.get("applicable") and pair.get("psi") is not None:
                if pair["psi"] > best_psi:
                    best_psi  = pair["psi"]
                    best_pair = pair
        if best_pair:
            c8_worst_pair_by_col[col_name] = best_pair

    # drift_suite sub-dicts
    consec_by_col = (drift_suite.get("consecutive") or {})
    fsi_by_col    = (drift_suite.get("fsi")         or {})
    vel_by_col    = (drift_suite.get("velocity")     or {})
    baseline      = (drift_suite.get("baseline")     or {})

    # ── Attach raw_facts to each column entry ─────────────────────────────
    for col_name, metrics in index.items():
        pair_list    = consec_by_col.get(col_name) or []
        last_pm      = pair_list[-1] if pair_list else {}

        raw_facts = _extract_raw_facts(
            col_name          = col_name,
            metrics_last_pair = last_pm,
            fsi_result        = fsi_by_col.get(col_name, {}),
            velocity_result   = vel_by_col.get(col_name, {}),
            baseline_result   = baseline,
            c4_vstats         = c4_vstats_by_col.get(col_name, []),
            c3_values         = c3_values_by_col.get(col_name, []),
            worst_psi_pair    = c8_worst_pair_by_col.get(col_name),
        )

        # Attach to the column-level entry (accessible by any metric consumer)
        index[col_name]["_raw_facts"] = raw_facts

    return index


# ─────────────────────────────────────────────────────────────────────────────
# PHASE B — SCORE COLUMN V2
# Uses composite importance when raw_facts are available.
# Falls back to original _magnitude() for schema/cardinality (no quantile data).
# ─────────────────────────────────────────────────────────────────────────────

def _score_column_v2(
    col_name:        str,
    metrics:         Dict[str, dict],
    is_private:      bool,
    miss_patterns:   dict,
    row_delta_pct:   Optional[float],
    i4_likely_cause: Optional[str],
) -> Optional[dict]:
    """
    Extended version of _score_column().
    Uses _compute_composite_importance() instead of PSI-only boost.
    Passes raw_facts through to the final signal dict.
    """
    raw_facts = metrics.get("_raw_facts", {})

    psi_raw   = metrics.get("psi", {})
    has_psi   = "psi" in metrics
    psi_val   = psi_raw.get("psi")
    psi_float = _safe(psi_val, 0.0)

    scored = []
    for metric_type, raw in metrics.items():
        # Skip the internal raw_facts entry
        if metric_type == "_raw_facts":
            continue

        sev = raw.get("severity", "stable")
        if sev == "stable":
            continue

        # Use composite importance when raw_facts available and metric is numeric
        if raw_facts and metric_type in ("psi", "mean_shift", "quantile_shift",
                                          "boundary", "std", "cv"):
            mag = _compute_composite_importance(raw_facts, is_private)
        else:
            # Schema / cardinality / missingness: keep original magnitude
            mag = _magnitude(sev, metric_type, is_private)

            # Still apply PSI penalty for non-PSI metrics on stable-PSI columns
            if has_psi and psi_val is not None and psi_float <= 0.10:
                mag = round(mag * 0.25, 4)

        scored.append((mag, metric_type, raw, sev))

    if not scored:
        return None

    scored.sort(key=lambda x: (-x[0], _TIE_ORDER.get(x[1], 99)))

    best_mag, best_mt, best_raw, best_sev = scored[0]
    miss_pattern = miss_patterns.get(col_name)

    root_cause = _classify_root_cause(
        miss_pattern, row_delta_pct, i4_likely_cause, best_mt
    )
    slot      = _assign_slot(best_mt, root_cause, is_private)
    direction = _infer_direction(best_mt, best_raw)

    supporting = []
    for mag, mt, raw, sev in scored[1:]:
        if mt == "_raw_facts":
            continue
        supporting.append({
            "metric_type": mt,
            "severity":    sev,
            "magnitude":   mag,
            "evidence":    raw,
        })

    return {
        "column":      col_name,
        "metric_type": best_mt,
        "severity":    best_sev,
        "magnitude":   best_mag,
        "root_cause":  root_cause,
        "direction":   direction,
        "evidence":    best_raw,
        "raw_facts":   raw_facts,       # ← NEW: flat numeric fact sheet
        "is_private":  is_private,
        "slot_hint":   slot,
        "supporting":  supporting,
        "all_metrics": {mt: raw for _, mt, raw, _ in scored if mt != "_raw_facts"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE B — MASTER FUNCTION V2
# Drop-in replacement for collect_signals().
# Returns same shape — additional "raw_facts" key on each signal.
# ─────────────────────────────────────────────────────────────────────────────

def collect_signals_v2(results: dict) -> List[dict]:
    """
    Extended version of collect_signals().

    Changes vs v1:
      - Uses _build_column_index_v2() → attaches raw numeric facts per column
      - Uses _score_column_v2()       → composite importance scoring
      - Each signal carries "raw_facts" dict with actual floats for LLM

    Output shape is identical to collect_signals() — same keys, same sort order.
    Phase B LLM calls read signal["raw_facts"] to build the fact sheet.
    Falls back to collect_signals() on any exception (never breaks the app).
    """
    try:
        c2          = results.get("c2", [])
        c3          = results.get("c3", {})
        c4          = results.get("c4", [])
        c8          = results.get("c8", {})
        c10         = results.get("c10", [])
        drift_suite = results.get("drift_suite", {})
        i4          = results.get("i4", {})

        miss_patterns: Dict[str, str] = {
            row["column"]: row.get("missing_pattern", "complete")
            for row in c3.get("rows", [])
        }

        row_delta_pct   = i4.get("row_delta_pct")
        i4_likely_cause = i4.get("likely_cause")
        private_cols    = _build_private_index(results)

        # Step 1: Build extended column index with raw_facts
        col_index = _build_column_index_v2(c3, c4, c8, c10, drift_suite, miss_patterns)

        # Step 2: Score each column using composite importance
        signals: List[dict] = []
        for col_name, metrics in col_index.items():
            sig = _score_column_v2(
                col_name, metrics, col_name in private_cols,
                miss_patterns, row_delta_pct, i4_likely_cause,
            )
            if sig is not None:
                signals.append(sig)

        # Step 3: Schema signals (no raw_facts — no drift_suite entry for dropped cols)
        signals += _schema_signals(c2, private_cols)

        # Step 4: Rank by composite magnitude descending
        signals.sort(key=lambda s: (-s["magnitude"], _TIE_ORDER.get(s["metric_type"], 99)))

        return signals

    except Exception:
        # Hard fallback — never break the app
        return collect_signals(results)