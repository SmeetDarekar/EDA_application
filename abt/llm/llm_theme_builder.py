"""
abt/llm_theme_builder.py
─────────────────────────────────────────────────────────────────────────────
Theme grouping and composite facts builder for LLM narrative framing.
"""

from typing import Dict, List, Optional

_THEME_LABELS = {
    "center_shift_down":  "Typical customer profile has shifted toward lower values",
    "center_shift_up":    "Typical customer profile has shifted toward higher values",
    "boundary_expansion": "New extreme values appeared outside the training range",
    "spread_change":      "Distribution variability has changed with stable centre",
    "new_segments":       "New categories or customer segments appeared",
    "pipeline_failure":   "Data supply is degraded — completeness or schema issue",
    "volatility":         "Feature is accelerating or chronically unstable",
}


def _assign_theme(sig: dict) -> str:
    """
    Assign one theme to a signal based on primary metric_type and direction.
    Rules applied in priority order — first match wins.
    """
    mt        = sig.get("metric_type", "")
    direction = sig.get("direction", "")
    root      = sig.get("root_cause", "")

    # Pipeline / schema always override
    if mt in ("schema", "missingness") or root == "pipeline_issue":
        return "pipeline_failure"

    if mt == "cardinality":
        return "new_segments"

    # Volatility: accelerating PSI or chronic FSI instability
    rf = sig.get("raw_facts", {})
    velocity = rf.get("velocity")
    fsi      = rf.get("fsi")
    if velocity is not None and abs(velocity) > 0.10:
        return "volatility"
    if fsi is not None and fsi < 0.40:
        return "volatility"

    if mt in ("boundary",):
        return "boundary_expansion"

    if mt in ("std", "cv"):
        return "spread_change"

    # PSI or mean_shift — direction determines up/down
    if direction == "down":
        return "center_shift_down"
    if direction == "up":
        return "center_shift_up"

    # PSI shift with no clear direction — use mean_delta_pct sign from raw_facts
    mean_pct = rf.get("mean_delta_pct")
    if mean_pct is not None:
        return "center_shift_down" if mean_pct < 0 else "center_shift_up"

    return "center_shift_down"   # safe default for unclear PSI-only signals


def group_signals_by_theme(signals: List[Dict]) -> List[Dict]:
    """
    Group ranked column signals into business themes.

    Each column belongs to exactly one theme (its primary metric_type wins).
    Themes are ordered by max signal importance within the group.

    Each theme dict contains:
        theme_id        : str
        theme_label     : str  (human-readable)
        columns         : List[str]
        signals         : List[dict]  (full signal dicts for all cols in theme)
        max_importance  : float  (highest magnitude in group — used for ranking)
        composite_facts : dict   (merged worst-case numbers across all columns)
    """
    # Group signals by theme
    buckets: Dict[str, List[dict]] = {}
    for sig in signals:
        theme_id = _assign_theme(sig)
        buckets.setdefault(theme_id, []).append(sig)

    themes: List[Dict] = []
    for theme_id, theme_sigs in buckets.items():
        # Sort within theme by magnitude descending
        theme_sigs.sort(key=lambda s: -s.get("magnitude", 0.0))
        worst_sig  = theme_sigs[0]   # highest importance in this group

        # Build composite_facts: worst-case numbers across all columns
        composite_facts = _build_composite_facts(theme_sigs)

        themes.append({
            "theme_id":        theme_id,
            "theme_label":     _THEME_LABELS.get(theme_id, theme_id),
            "columns":         [s["column"] for s in theme_sigs],
            "signals":         theme_sigs,
            "max_importance":  worst_sig.get("magnitude", 0.0),
            "worst_column":    worst_sig["column"],
            "composite_facts": composite_facts,
        })

    # Order themes by max_importance descending
    themes.sort(key=lambda t: -t["max_importance"])
    return themes


def _build_composite_facts(theme_sigs: List[dict]) -> dict:
    """
    Merge raw_facts across all signals in a theme.
    For each numeric field: take the most extreme (worst-case) value.
    For string fields: take from the worst signal (index 0).

    This gives the LLM the most informative numbers in one flat dict.
    """
    if not theme_sigs:
        return {}

    # String fields from worst signal
    worst_rf = theme_sigs[0].get("raw_facts", {})

    # Numeric fields: worst-case across all signals
    def _worst(field: str, fn=max, abs_compare=False) -> Optional[float]:
        vals = []
        for s in theme_sigs:
            v = s.get("raw_facts", {}).get(field)
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        if abs_compare:
            return max(vals, key=abs)
        return fn(vals)

    def _first(field: str):
        for s in theme_sigs:
            v = s.get("raw_facts", {}).get(field)
            if v is not None:
                return v
        return None

    # Mean delta pct: most extreme (largest absolute change)
    mean_delta_pcts = [
        s.get("raw_facts", {}).get("mean_delta_pct")
        for s in theme_sigs
        if s.get("raw_facts", {}).get("mean_delta_pct") is not None
    ]
    worst_mean_delta_pct = (
        max(mean_delta_pcts, key=abs) if mean_delta_pcts else None
    )

    # For mean_before/after: use the column with worst_mean_delta_pct
    mean_before = mean_after = None
    for s in theme_sigs:
        rf = s.get("raw_facts", {})
        if rf.get("mean_delta_pct") == worst_mean_delta_pct:
            mean_before = rf.get("mean_before")
            mean_after  = rf.get("mean_after")
            break

    return {
        # Column coverage
        "n_columns":              len(theme_sigs),
        "column_names":           [s["column"] for s in theme_sigs],
        "worst_column":           theme_sigs[0]["column"],

        # PSI (worst pair, worst column)
        "worst_psi":              _worst("psi", max),
        "worst_psi_label":        worst_rf.get("psi_label"),
        "worst_psi_pair":         worst_rf.get("psi_worst_pair"),

        # Mean shift
        "worst_mean_delta_pct":   worst_mean_delta_pct,
        "mean_before":            mean_before,
        "mean_after":             mean_after,

        # Quantile shifts (most extreme)
        "worst_median_shift_iqr": _worst("median_shift_iqr", abs_compare=True),
        "worst_q1_shift_iqr":     _worst("q1_shift_iqr",     abs_compare=True),
        "worst_q3_shift_iqr":     _worst("q3_shift_iqr",     abs_compare=True),
        "iqr_base":               _first("iqr_base"),

        # Boundary
        "min_base":               worst_rf.get("min_base"),
        "min_new":                worst_rf.get("min_new"),
        "max_base":               worst_rf.get("max_base"),
        "max_new":                worst_rf.get("max_new"),

        # Std
        "worst_std_norm_change":  _worst("std_norm_change", abs_compare=True),
        "std_base":               worst_rf.get("std_base"),
        "std_new":                worst_rf.get("std_new"),

        # Completeness (latest pair for worst column)
        "completeness_before":    worst_rf.get("completeness_before"),
        "completeness_after":     worst_rf.get("completeness_after"),

        # Baseline distance from V1
        "baseline_psi":           _worst("baseline_psi", max),
        "baseline_label":         worst_rf.get("baseline_label"),

        # Longitudinal
        "worst_velocity":         _worst("velocity", abs_compare=True),
        "worst_fsi":              _worst("fsi", min),   # lower FSI = more unstable
        "fsi_label":              worst_rf.get("fsi_label"),
    }


def _theme_to_domain(theme_id: str) -> str:
    """Map theme_id to insight card domain field."""
    mapping = {
        "center_shift_down":  "population",
        "center_shift_up":    "population",
        "boundary_expansion": "feature",
        "spread_change":      "feature",
        "new_segments":       "feature",
        "pipeline_failure":   "pipeline",
        "volatility":         "feature",
    }
    return mapping.get(theme_id, "population")
