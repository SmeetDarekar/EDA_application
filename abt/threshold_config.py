"""
abt/threshold_config.py
─────────────────────────────────────────────────────────────────────────────
Central registry of all tunable thresholds.

Design:
  - ThresholdConfig is a plain dataclass with all defaults baked in.
  - Every analysis/compare function accepts an optional `cfg: ThresholdConfig`.
  - If cfg is None, a default instance is used (same behaviour as before).
  - from_form(form_data) parses user-submitted values, falling back to
    defaults for any field that is blank or invalid.
  - to_dict() / from_dict() enable round-tripping through HTML hidden fields.

Adding a new threshold:
  1. Add a field with a default here.
  2. Add it to THRESHOLD_META so it appears in the UI.
  3. Reference cfg.<field> instead of the module-level constant.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# ── Defaults (mirror the module-level constants in analyze/compare/etc.) ─────

@dataclass
class ThresholdConfig:
    # ── Single-version analysis (analyze.py) ──────────────────────────────
    blocker_completeness: float   = 50.0   # % below which column is blocked
    blocker_mismatch_rate: float  = 0.15   # fraction of rows that triggers blocker
    skew_symmetric: float         = 0.5    # |skewness| below this = symmetric
    leakage_cardinality: int      = 50     # min distinct values to suspect leakage
    imbalance_notable: float      = 1.5    # non-event:event ratio → moderate imbalance
    imbalance_severe: float       = 4.0    # non-event:event ratio → severe imbalance

    # ── Cross-version comparison (compare.py) ─────────────────────────────
    drift_notable: float          = 0.20   # mean shift (in std units) → notable
    drift_severe: float           = 0.50   # mean shift → critical
    completeness_delta_min: float = 5.0    # pp change needed to flag completeness drift
    target_drift_notable: float   = 0.03   # absolute target event-rate Δ → notable
    target_drift_critical: float  = 0.08   # absolute target event-rate Δ → critical

    # ── PSI (insights.py / drift_metrics.py) ──────────────────────────────
    psi_stable: float             = 0.10   # PSI < this → stable
    psi_monitor: float            = 0.25   # PSI ≥ this → significant shift

    # ── Column Health Score weights (insights.py) — must sum to 1.0 ───────
    w_completeness: float         = 0.35
    w_variance: float             = 0.20
    w_mismatch: float             = 0.20
    w_governance: float           = 0.15
    w_distribution: float         = 0.10


# ─────────────────────────────────────────────────────────────────────────────
# UI metadata — drives the config page
# ─────────────────────────────────────────────────────────────────────────────

THRESHOLD_META = [
    # ── Section: Data Quality ────────────────────────────────────────────
    {
        "section": "Data Quality (Single Version)",
        "fields": [
            {
                "key":     "blocker_completeness",
                "label":   "Missing Data Blocker Threshold (%)",
                "default": 50.0,
                "min": 1, "max": 99, "step": 1,
                "type":    "float",
                "hint":    "Columns with completeness below this % are flagged as blockers — "
                           "too much missing data to use safely. Default: 50%.",
            },
            {
                "key":     "blocker_mismatch_rate",
                "label":   "Mismatch Rate Blocker Threshold",
                "default": 0.15,
                "min": 0.01, "max": 0.5, "step": 0.01,
                "type":    "float",
                "hint":    "If more than this fraction of rows have format mismatches, "
                           "the column is blocked. Default: 0.15 (15% of rows).",
            },
            {
                "key":     "completeness_delta_min",
                "label":   "Completeness Change Alert Threshold (pp)",
                "default": 5.0,
                "min": 1, "max": 30, "step": 0.5,
                "type":    "float",
                "hint":    "Minimum percentage-point change in completeness between two versions "
                           "needed to raise a drift alert. Default: 5 pp.",
            },
            {
                "key":     "skew_symmetric",
                "label":   "Skewness Symmetry Threshold",
                "default": 0.5,
                "min": 0.1, "max": 2.0, "step": 0.05,
                "type":    "float",
                "hint":    "Columns with |skewness| below this are considered symmetric. "
                           "Higher values are more tolerant of skew. Default: 0.5.",
            },
        ],
    },
    # ── Section: Target Variable ─────────────────────────────────────────
    {
        "section": "Target Variable & Class Imbalance",
        "fields": [
            {
                "key":     "imbalance_notable",
                "label":   "Notable Imbalance Ratio",
                "default": 1.5,
                "min": 1.0, "max": 10.0, "step": 0.5,
                "type":    "float",
                "hint":    "Non-event : event ratio above this triggers a moderate imbalance warning. "
                           "Default: 1.5 (e.g., 60% non-events vs 40% events).",
            },
            {
                "key":     "imbalance_severe",
                "label":   "Severe Imbalance Ratio",
                "default": 4.0,
                "min": 1.5, "max": 50.0, "step": 0.5,
                "type":    "float",
                "hint":    "Non-event : event ratio above this triggers a severe imbalance warning "
                           "requiring SMOTE or threshold tuning. Default: 4.0.",
            },
            {
                "key":     "target_drift_notable",
                "label":   "Target Drift — Notable Threshold",
                "default": 0.03,
                "min": 0.005, "max": 0.2, "step": 0.005,
                "type":    "float",
                "hint":    "Absolute change in event rate (as a fraction) between versions that "
                           "triggers a notable drift alert. Default: 0.03 (3 percentage points).",
            },
            {
                "key":     "target_drift_critical",
                "label":   "Target Drift — Critical Threshold",
                "default": 0.08,
                "min": 0.01, "max": 0.5, "step": 0.01,
                "type":    "float",
                "hint":    "Absolute change in event rate that triggers a critical drift alert "
                           "and mandatory back-testing. Default: 0.08 (8 percentage points).",
            },
        ],
    },
    # ── Section: Distribution & Feature Drift ────────────────────────────
    {
        "section": "Distribution Drift (Cross-Version)",
        "fields": [
            {
                "key":     "psi_stable",
                "label":   "PSI Stable Threshold",
                "default": 0.10,
                "min": 0.01, "max": 0.24, "step": 0.01,
                "type":    "float",
                "hint":    "Population Stability Index below this value means the feature is stable. "
                           "Industry standard: 0.10. Loosen for noisier datasets.",
            },
            {
                "key":     "psi_monitor",
                "label":   "PSI Shift Threshold",
                "default": 0.25,
                "min": 0.10, "max": 1.0, "step": 0.01,
                "type":    "float",
                "hint":    "PSI above this value signals significant population shift requiring "
                           "model re-training. Industry standard: 0.25.",
            },
            {
                "key":     "drift_notable",
                "label":   "Mean Drift — Notable Threshold (std units)",
                "default": 0.20,
                "min": 0.05, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Mean shift between versions, expressed in units of base standard deviation. "
                           "Above this → notable drift. Default: 0.20 std.",
            },
            {
                "key":     "drift_severe",
                "label":   "Mean Drift — Critical Threshold (std units)",
                "default": 0.50,
                "min": 0.10, "max": 3.0, "step": 0.10,
                "type":    "float",
                "hint":    "Mean shift above this triggers a critical drift flag. "
                           "Default: 0.50 std (half a standard deviation).",
            },
        ],
    },
    # ── Section: Column Health Weights ───────────────────────────────────
    {
        "section": "Column Health Score Weights",
        "fields": [
            {
                "key":     "w_completeness",
                "label":   "Completeness Weight",
                "default": 0.35,
                "min": 0.0, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Weight of missing-data penalty in the 0–100 column health score. "
                           "Default: 0.35 (highest — missing data is the primary risk).",
            },
            {
                "key":     "w_variance",
                "label":   "Variance Weight",
                "default": 0.20,
                "min": 0.0, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Weight of variance/signal quality in the health score. Default: 0.20.",
            },
            {
                "key":     "w_mismatch",
                "label":   "Format Quality Weight",
                "default": 0.20,
                "min": 0.0, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Weight of mismatch/blank-value penalty. Default: 0.20.",
            },
            {
                "key":     "w_governance",
                "label":   "Governance Weight",
                "default": 0.15,
                "min": 0.0, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Weight of governance risk (privacy, leakage, identifier flags). Default: 0.15.",
            },
            {
                "key":     "w_distribution",
                "label":   "Distribution Quality Weight",
                "default": 0.10,
                "min": 0.0, "max": 1.0, "step": 0.05,
                "type":    "float",
                "hint":    "Weight of skewness/outlier penalty. Default: 0.10.",
            },
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULTS = ThresholdConfig()


def _parse_float(value: Any, default: float) -> float:
    """Parse a form value to float, returning default on any failure."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value: Any, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def from_form(form_data: Dict) -> ThresholdConfig:
    """
    Build a ThresholdConfig from a Flask request.form dict.
    Missing or unparseable values fall back to defaults.
    Validates weight sum — if weights don't sum to ~1.0, normalises them.
    """
    d = _DEFAULTS
    cfg = ThresholdConfig(
        blocker_completeness   = _parse_float(form_data.get("blocker_completeness"),   d.blocker_completeness),
        blocker_mismatch_rate  = _parse_float(form_data.get("blocker_mismatch_rate"),  d.blocker_mismatch_rate),
        skew_symmetric         = _parse_float(form_data.get("skew_symmetric"),         d.skew_symmetric),
        leakage_cardinality    = _parse_int  (form_data.get("leakage_cardinality"),    d.leakage_cardinality),
        imbalance_notable      = _parse_float(form_data.get("imbalance_notable"),      d.imbalance_notable),
        imbalance_severe       = _parse_float(form_data.get("imbalance_severe"),       d.imbalance_severe),
        drift_notable          = _parse_float(form_data.get("drift_notable"),          d.drift_notable),
        drift_severe           = _parse_float(form_data.get("drift_severe"),           d.drift_severe),
        completeness_delta_min = _parse_float(form_data.get("completeness_delta_min"), d.completeness_delta_min),
        target_drift_notable   = _parse_float(form_data.get("target_drift_notable"),   d.target_drift_notable),
        target_drift_critical  = _parse_float(form_data.get("target_drift_critical"),  d.target_drift_critical),
        psi_stable             = _parse_float(form_data.get("psi_stable"),             d.psi_stable),
        psi_monitor            = _parse_float(form_data.get("psi_monitor"),            d.psi_monitor),
        w_completeness         = _parse_float(form_data.get("w_completeness"),         d.w_completeness),
        w_variance             = _parse_float(form_data.get("w_variance"),             d.w_variance),
        w_mismatch             = _parse_float(form_data.get("w_mismatch"),             d.w_mismatch),
        w_governance           = _parse_float(form_data.get("w_governance"),           d.w_governance),
        w_distribution         = _parse_float(form_data.get("w_distribution"),         d.w_distribution),
    )

    # Normalise weights if they don't sum to 1.0 (±0.01 tolerance)
    weight_sum = cfg.w_completeness + cfg.w_variance + cfg.w_mismatch + cfg.w_governance + cfg.w_distribution
    if abs(weight_sum - 1.0) > 0.01 and weight_sum > 0:
        cfg.w_completeness  /= weight_sum
        cfg.w_variance      /= weight_sum
        cfg.w_mismatch      /= weight_sum
        cfg.w_governance    /= weight_sum
        cfg.w_distribution  /= weight_sum

    # Ensure monotonicity where required
    cfg.psi_stable  = min(cfg.psi_stable,  cfg.psi_monitor - 0.01)
    cfg.drift_notable = min(cfg.drift_notable, cfg.drift_severe - 0.01)
    cfg.target_drift_notable = min(cfg.target_drift_notable, cfg.target_drift_critical - 0.001)
    cfg.imbalance_notable    = min(cfg.imbalance_notable,    cfg.imbalance_severe - 0.1)

    return cfg


def to_hidden_fields(cfg: ThresholdConfig) -> str:
    """Return HTML hidden input fields encoding all threshold values."""
    parts = []
    for k, v in asdict(cfg).items():
        parts.append(f'<input type="hidden" name="{k}" value="{v}">')
    return "\n".join(parts)


def is_default(cfg: ThresholdConfig) -> bool:
    """True if cfg matches defaults exactly (no user customisation)."""
    return cfg == _DEFAULTS


def from_dict(d: Dict) -> ThresholdConfig:
    """Reconstruct from a plain dict (e.g., from JSON or form hidden fields)."""
    return from_form(d)