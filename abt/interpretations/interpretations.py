"""
abt/interpretations.py
─────────────────────────────────────────────────────────────────────────────
Interpretation layer orchestration hub. Delegates to single/compare sub-modules.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any

# Expose helpers
def _safe(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        import math
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct(val, default=0.0):
    return round(_safe(val, default), 1)


# Import single-version rule-based interpretations
from abt.interpretations.interpretations_single import (
    i1_feature_verdicts,
    i2_training_readiness,
    i3_preprocessing_checklist,
)

# Import comparison/drift interpretations
from abt.interpretations.interpretations_compare import (
    i4_population_shift,
    i5_target_stability,
    i6_feature_drift_impact,
    i7_model_action,
    i8_pipeline_break_risks,
    i9_pipeline_health,
    # Enhanced (Tier B)
    i4b_population_shift,
    i5b_target_stability,
    i6b_feature_drift_impact,
    i7b_model_action,
    i8b_pipeline_break_risks,
    i9b_pipeline_health,
    # Hybrid (Tier C)
    i4c_population_shift,
    i5c_target_stability,
    i6c_feature_drift_impact,
    i7c_model_action,
    i8c_pipeline_break_risks,
    i9c_pipeline_health,
)


def run_interpretations(results: Dict) -> Dict:
    """Adds i1–i9 (Tier A) to the results dict. Safe to call for both analyze and compare."""

    # Analyze-only sections (need s-keys)
    if "s2" in results:
        s2 = results.get("s2", [])
        s3 = results.get("s3", [])
        s4 = results.get("s4", [])
        s6 = results.get("s6")
        s7 = results.get("s7", [])
        s8 = results.get("s8", {})
        s1 = results.get("s1", {})
        s0 = results.get("s0")
        row_count = int(_safe(results["s1"].get("row_count"), 1000)) if "s1" in results else 1000

        i1 = i1_feature_verdicts(s2, s3, s4, s7, s8, row_count)
        results["i1"] = i1
        results["i2"] = i2_training_readiness(s6, s2, s1, s0)
        results["i3"] = i3_preprocessing_checklist(s3, results.get("s5", []), s7, i1)

    # Compare-only sections (need c-keys)
    if "c4" in results:
        c1 = results.get("c1", {})
        c3 = results.get("c3", {})
        c4 = results.get("c4", [])
        c5 = results.get("c5")
        c6 = results.get("c6", [])
        c8 = results.get("c8", {})
        c9 = results.get("c9", {})
        c0 = results.get("c0", {})
        c2 = results.get("c2", [])
        c10= results.get("c10", [])
        ds = results.get("drift_suite", {})

        i4 = i4_population_shift(c1, c4, c9, ds)
        i5 = i5_target_stability(c5, c3, c6)
        i6 = i6_feature_drift_impact(c3, c4, c8, ds)
        results["i4"] = i4
        results["i5"] = i5
        results["i6"] = i6
        results["i7"] = i7_model_action(c0, i5, i6, ds)
        results["i8"] = i8_pipeline_break_risks(c2, c8, c10, ds)
        results["i9"] = i9_pipeline_health(c3, c6, c9)

    return results


def run_interpretations_enhanced(results: Dict) -> Dict:
    """
    Adds i4b–i9b (Tier B) to the results dict.
    Tier B ALWAYS consumes final hybrid outputs (i4c–i9c) when available.
    """

    if "c4" not in results:
        return results

    c1  = results.get("c1", {})
    c2  = results.get("c2", [])
    c3  = results.get("c3", {})
    c4  = results.get("c4", [])
    c5  = results.get("c5")
    c6  = results.get("c6", [])
    c8  = results.get("c8", {})
    c9  = results.get("c9", {})
    c0  = results.get("c0", {})
    c10 = results.get("c10", [])
    ds  = results.get("drift_suite", {})

    # USE HYBRID OUTPUTS FIRST
    i4_base = results.get("i4c") or results.get("i4")
    i5_base = results.get("i5c") or results.get("i5")
    i6_base = results.get("i6c") or results.get("i6")

    # I4b (population shift) — inject refined i4c signals
    results["i4b"] = i4b_population_shift(c1, c4, c9, ds)

    if i4_base:
        results["i4b"].update({
            "refined_cause":        i4_base.get("refined_cause"),
            "sampling_signal":      i4_base.get("sampling_signal"),
            "coordination_signal": i4_base.get("coordination_signal"),
            "distance_signal":      i4_base.get("distance_signal"),
        })

    # I5b (target stability) — merge i5c classification
    results["i5b"] = i5b_target_stability(c5, c3, c6)

    if i5_base:
        results["i5b"].update({
            "refined_drift_type": i5_base.get("refined_drift_type")
        })

    # I6b (feature drift) — inject mechanisms from i6c
    results["i6b"] = i6b_feature_drift_impact(c3, c4, c8, ds)

    i6c_map = {f["column"]: f for f in (i6_base or [])}

    for item in results["i6b"]:
        col = item["column"]
        if col in i6c_map:
            item.update({
                "drift_mechanism": i6c_map[col].get("drift_mechanism"),
                "signal_summary":  i6c_map[col].get("signal_summary")
            })

    # I7b (model action) — already using hybrid
    results["i7b"] = i7b_model_action(c0, i5_base, i6_base, ds, c9)

    # I8b (unchanged)
    results["i8b"] = i8b_pipeline_break_risks(c2, c8, c10, ds)

    # I9b (unchanged structure, can include hybrid if needed)
    results["i9b"] = i9b_pipeline_health(c3, c6, c9)

    return results


def run_interpretations_hybrid(results: Dict) -> Dict:

    if "i4" in results:
        results["i4c"] = i4c_population_shift(results["i4"],
                                              results.get("c1", {}),
                                              results.get("c4", []),
                                              results.get("drift_suite", {}))

    if "i5" in results:
        results["i5c"] = i5c_target_stability(results["i5"])

    if "i6" in results:
        results["i6c"] = i6c_feature_drift_impact(results["i6"],
                                                 results.get("c4", []),
                                                 results.get("drift_suite", {}))

    if "i7" in results:
        results["i7c"] = i7c_model_action(results["i7"],
                                         results.get("i5c", {}),
                                         results.get("i6c", []),
                                         results.get("drift_suite", {}))

    if "i8" in results:
        results["i8c"] = i8c_pipeline_break_risks(results["i8"])

    if "i9" in results:
        results["i9c"] = i9c_pipeline_health(results["i9"])

    return results