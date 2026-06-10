# """
# abt/interpretations.py
# ─────────────────────────────────────────────────────────────────────────────
# Interpretation layer — cross-section synthesis for actionable DS guidance.

# Nine functions. Each reads 2–4 already-computed result dicts and returns
# a structured interpretation: verdict, reason, action. All rule-based.
# No ABTProfile imports. No LLM dependency. Pure dict-in, dict-out.

# Analyze flow:
#   i1_feature_verdicts(s2, s3, s4, s7, s8, row_count)
#   i2_training_readiness(s6, s2, s1)
#   i3_preprocessing_checklist(s3, s5, s7, i1_verdicts)

# Compare flow:
#   i4_population_shift(c1, c4, c9, drift_suite)
#   i5_target_stability(c5, c3, c6)
#   i6_feature_drift_impact(c3, c4, c8, drift_suite)
#   i7_model_action(c0, i5_result, i6_result, drift_suite)
#   i8_pipeline_break_risks(c2, c8, c10, drift_suite)
#   i9_pipeline_health(c3, c6, c9)

# Called from run_analysis() and run_comparison() after all Sx/Cx sections.
# Results stored as results["i1"] … results["i9"].
# ─────────────────────────────────────────────────────────────────────────────
# """

# from __future__ import annotations
# from typing import Dict, List, Optional, Any


# # ─────────────────────────────────────────────────────────────────────────────
# # HELPERS
# # ─────────────────────────────────────────────────────────────────────────────

# def _safe(val, default=0.0):
#     try:
#         if val is None:
#             return default
#         f = float(val)
#         import math
#         return f if math.isfinite(f) else default
#     except (TypeError, ValueError):
#         return default


# def _pct(val, default=0.0):
#     return round(_safe(val, default), 1)


# # ─────────────────────────────────────────────────────────────────────────────
# # I1 · Feature usability verdict
# # Reads: s2 (blockers), s3 (warnings), s4 (governance), s7 (distribution), s8 (health)
# # Answers: which columns can I use, fix, or drop right now?
# # ─────────────────────────────────────────────────────────────────────────────

# def i1_feature_verdicts(s2: List[Dict], s3: List[Dict], s4: List[Dict],
#                          s7: List[Dict], s8: Dict, row_count: int) -> List[Dict]:
#     """
#     Per-column usability verdict.

#     Verdicts:
#       use         — no flags, health ≥ 70, include directly
#       fix_then_use — fixable issue (missing, mismatch, skew), worth effort
#       drop        — zero variance, identifier, or unfixable blocker
#       exclude     — leakage or privacy risk too high for model use
#     """
#     row_count = max(row_count, 1)

#     # Index all sections by column name
#     blocker_cols  = {b["column"]: b for b in s2}
#     warning_cols  = {w["column"]: w for w in s3}
#     gov_cols      = {g["column"]: g for g in s4}
#     dist_cols     = {d["column"]: d for d in s7}

#     # Collect all column names in encounter order
#     seen = {}
#     for items, key in [(s2, "column"), (s3, "column"), (s4, "column"),
#                        (s7, "column")]:
#         for item in items:
#             name = item[key]
#             if name not in seen:
#                 seen[name] = True
#     for name in s8.keys():
#         if name not in seen:
#             seen[name] = True

#     results = []

#     for col_name in seen:
#         health_entry = s8.get(col_name, {})
#         health_score = _safe(health_entry.get("score"), 50.0)

#         blocker = blocker_cols.get(col_name)
#         gov     = gov_cols.get(col_name)
#         warning = warning_cols.get(col_name)
#         dist    = dist_cols.get(col_name)

#         # ── Governance overrides: check LEAKAGE and PRIVACY first ──────────
#         if gov:
#             risk_types = [r["risk_type"] for r in gov.get("risks", [])]

#             if "LEAKAGE" in risk_types:
#                 leakage_risk = next(r for r in gov["risks"] if r["risk_type"] == "LEAKAGE")
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "exclude",
#                     "reason":  (
#                         f"{leakage_risk['detail']}. "
#                         f"Health score is {health_score}/100 but leakage features inflate "
#                         f"AUC/KS by 20–30 points with zero real signal. "
#                         f"A model that appears to score 0.85 AUC may be 0.60 once the leaky "
#                         f"feature is removed."
#                     ),
#                     "action":  "Confirm the data source. If this is a prior model score on the same population, remove before training.",
#                     "effort":  "low",
#                     "ordered_steps": [],
#                 })
#                 continue

#             if "IDENTIFIER" in risk_types:
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "drop",
#                     "reason":  "Unique identifier — no predictive signal. Including it allows the model to memorise training rows.",
#                     "action":  "Remove from feature set entirely. Use only for row-level joins.",
#                     "effort":  "low",
#                     "ordered_steps": [],
#                 })
#                 continue

#             if "PRIVACY" in risk_types:
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "exclude",
#                     "reason":  (
#                         f"Marked as private. In regulated models (GDPR, FCRA, ECOA), "
#                         f"using sensitive attributes requires documented justification and fairness testing."
#                     ),
#                     "action":  "Obtain governance sign-off before including. Run fairness audit if approved.",
#                     "effort":  "high",
#                     "ordered_steps": [],
#                 })
#                 continue

#         # ── Blockers: drop or fix depending on cause ────────────────────────
#         if blocker:
#             rules = [r["rule"] for r in blocker.get("reasons", [])]

#             if "zero_variance" in rules:
#                 card = next((r["detail"] for r in blocker["reasons"]
#                              if r["rule"] == "zero_variance"), "")
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "drop",
#                     "reason":  f"Zero variance — {card}. No predictive signal possible.",
#                     "action":  "Drop before model training. No imputation or transform will fix zero variance.",
#                     "effort":  "low",
#                     "ordered_steps": [],
#                 })
#                 continue

#             if "high_missing" in rules:
#                 comp = _pct(blocker.get("completeness", 0))
#                 missing_n = round((100 - comp) / 100 * row_count)
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "drop",
#                     "reason":  (
#                         f"{100 - comp:.1f}% missing ({missing_n:,}/{row_count:,} rows). "
#                         f"More than half the data is absent — imputation at this rate introduces "
#                         f"more noise than signal."
#                     ),
#                     "action":  "Investigate root cause. If missingness is not informative (MCAR), drop. If informative (MNAR), create a binary missingness indicator feature instead.",
#                     "effort":  "medium",
#                     "ordered_steps": [],
#                 })
#                 continue

#             if "severe_mismatch" in rules:
#                 results.append({
#                     "column":  col_name,
#                     "verdict": "drop",
#                     "reason":  f"Severe format mismatch (>{15}% of rows). Encoding is inconsistent at source.",
#                     "action":  "Fix at data source before any model use. Downstream imputation cannot correct format corruption.",
#                     "effort":  "medium",
#                     "ordered_steps": [],
#                 })
#                 continue

#         # ── Warnings + distribution: fix_then_use ──────────────────────────
#         steps = []

#         if warning:
#             for iss in warning.get("issues", []):
#                 if iss["type"] == "blank_values":
#                     blank_n = int(iss["detail"].split()[0].replace(",", ""))
#                     steps.append({
#                         "type":   "quality_fix",
#                         "instruction": f"Standardise {blank_n:,} blank strings in '{col_name}' to NaN before any other step.",
#                         "reason": "Blank strings are not null — they will silently break label encoders and WoE binning.",
#                     })

#             for iss in warning.get("issues", []):
#                 if iss["type"] == "partial_missing":
#                     comp = _pct(warning.get("completeness", 100))
#                     missing_n = round((100 - comp) / 100 * row_count)
#                     skew = _safe(dist.get("skewness") if dist else None, 0.0) if dist else 0.0
#                     method = "median" if abs(skew) > 0.5 else "mean"
#                     reason = (
#                         f"skewness={skew:.2f} — mean is pulled by the {'right' if skew > 0 else 'left'} tail"
#                         if abs(skew) > 0.5 else "distribution is near-symmetric"
#                     )
#                     steps.append({
#                         "type":   "imputation",
#                         "instruction": f"Impute '{col_name}' with {method} ({missing_n:,} missing values, {100 - comp:.1f}% of rows).",
#                         "reason": f"Use {method} because {reason}.",
#                     })

#                 if iss["type"] == "format_mismatch":
#                     steps.append({
#                         "type":   "quality_fix",
#                         "instruction": f"Standardise encoding in '{col_name}' — case or format inconsistencies detected.",
#                         "reason": "Case variations (e.g. 'Yes'/'yes') inflate cardinality and corrupt WoE bin weights.",
#                     })

#         # Distribution-based steps (only for non-blocked, non-excluded columns)
#         if dist:
#             skew     = _safe(dist.get("skewness"), 0.0)
#             min_val  = dist.get("min")
#             has_out  = dist.get("has_outliers", False)
#             n_out    = int(_safe(dist.get("n_outliers"), 0))
#             skew_lbl = dist.get("skew_label", "")

#             if has_out and n_out > 0:
#                 steps.append({
#                     "type":   "outlier_cap",
#                     "instruction": f"Winsorise '{col_name}' at 1st/99th percentile — {n_out} outlier(s) detected.",
#                     "reason": "Cap outliers before any transformation. Log of a negative number will crash; extreme values distort linear model coefficients.",
#                 })

#             if abs(skew) > 1.0:
#                 if min_val is not None and min_val > 0:
#                     transform = "log"
#                     t_reason  = f"min={min_val} > 0 so log is safe. Reduces skewness from {skew:.2f} toward 0."
#                 elif min_val is not None and min_val == 0:
#                     transform = "log1p"
#                     t_reason  = f"min=0 — log is undefined at zero. log1p(x) = log(1+x) handles zeros safely."
#                 elif min_val is not None and min_val < 0:
#                     transform = "reflect then log1p"
#                     t_reason  = f"min={min_val} < 0 — reflect the column (multiply by -1, shift by |min|+1), then apply log1p."
#                 else:
#                     transform = "log1p"
#                     t_reason  = "min unknown — use log1p as a safe default."

#                 if skew < -1.0:
#                     transform = "reflect then log1p"
#                     t_reason  = f"Left-skewed (skewness={skew:.2f}). Reflect first: subtract column from max value, then apply log1p."

#                 steps.append({
#                     "type":   "transformation",
#                     "instruction": f"Apply {transform} to '{col_name}' after imputation and outlier capping.",
#                     "reason": t_reason + " Not needed for tree-based models.",
#                 })

#         if steps:
#             # Build a plain summary reason
#             issues_summary = []
#             if warning:
#                 for iss in warning.get("issues", []):
#                     issues_summary.append(iss["detail"])
#             if dist and abs(_safe(dist.get("skewness"), 0.0)) > 1.0:
#                 issues_summary.append(f"skewness={_safe(dist.get('skewness'), 0.0):.2f}")

#             results.append({
#                 "column":        col_name,
#                 "verdict":       "fix_then_use",
#                 "reason":        "; ".join(issues_summary) if issues_summary else "Data quality or distribution issues require preprocessing.",
#                 "action":        f"{len(steps)} preprocessing step(s) required before use. See ordered steps below.",
#                 "effort":        "low" if len(steps) == 1 else "medium",
#                 "ordered_steps": steps,
#             })
#             continue

#         # ── Clean column ────────────────────────────────────────────────────
#         results.append({
#             "column":        col_name,
#             "verdict":       "use",
#             "reason":        f"No blockers, governance flags, or significant data quality issues. Health score: {health_score}/100.",
#             "action":        "Include in feature set directly.",
#             "effort":        "none",
#             "ordered_steps": [],
#         })

#     # Sort: exclude and drop first, then fix_then_use, then use
#     order = {"exclude": 0, "drop": 1, "fix_then_use": 2, "use": 3}
#     results.sort(key=lambda x: order.get(x["verdict"], 4))
#     return results


# # ─────────────────────────────────────────────────────────────────────────────
# # I2 · Training readiness
# # Reads: s6 (target), s2 (blockers), s1 (summary)
# # Answers: is this dataset safe to train on — target, imbalance, row count?
# # ─────────────────────────────────────────────────────────────────────────────

# def i2_training_readiness(s6: Optional[Dict], s2: List[Dict], s1: Dict,
#                            s0: Optional[Dict] = None) -> Dict:
#     """
#     Synthesises target health, row count, imbalance, and overall dataset readiness
#     to produce a specific, safe training strategy.

#     s0 is the dataset readiness score dict — used to block training when the
#     overall feature set is too degraded regardless of target quality.
#     """
#     row_count = max(int(_safe(s1.get("row_count"), 1)), 1)
#     blocker_cols = {b["column"] for b in s2}

#     # ── Check overall dataset readiness first ─────────────────────────────
#     # A healthy target cannot compensate for a degraded feature set.
#     if s0 is not None and s0.get("label") == "not_ready":
#         blocked = s0.get("blocked_cols", 0)
#         total   = s0.get("feature_cols", 1)
#         return {
#             "training_ready":      False,
#             "blocker":             (
#                 f"Overall dataset readiness is {s0['score']}/100 (not_ready). "
#                 f"{blocked}/{total} feature columns are blocked. "
#                 f"Training on this dataset will produce an unreliable model."
#             ),
#             "minority_count":      None,
#             "imbalance_strategy":  "Fix blocked feature columns first",
#             "smote_recommended":   False,
#             "primary_eval_metric": None,
#             "secondary_metric":    None,
#             "reason":              (
#                 f"A readiness score below 45 means too many features are unusable. "
#                 f"Fix the blockers listed in S2 before training."
#             ),
#         }

#     # No target found
#     if not s6:
#         return {
#             "training_ready":      False,
#             "blocker":             "No target column detected. Specify target_col on the analysis page.",
#             "minority_count":      None,
#             "imbalance_strategy":  None,
#             "smote_recommended":   False,
#             "primary_eval_metric": None,
#             "secondary_metric":    None,
#             "reason":              "Cannot assess training readiness without a target column.",
#         }

#     # Target has a compute error
#     if s6.get("error"):
#         return {
#             "training_ready":      False,
#             "blocker":             s6["error"],
#             "minority_count":      None,
#             "imbalance_strategy":  None,
#             "smote_recommended":   False,
#             "primary_eval_metric": None,
#             "secondary_metric":    None,
#             "reason":              "Target column statistics are unavailable.",
#         }

#     target_col   = s6.get("column", "target")
#     event_rate   = _safe(s6.get("event_rate"), 0.0)  # already in percent
#     ratio        = _safe(s6.get("imbalance_ratio"), 1.0)
#     balance_lbl  = s6.get("balance_label", "unknown")
#     completeness = _safe(s6.get("completeness"), 100.0)

#     # Target is itself a blocker
#     if target_col in blocker_cols:
#         return {
#             "training_ready":      False,
#             "blocker":             f"Target column '{target_col}' has a data quality blocker. Fix label quality before any modelling.",
#             "minority_count":      None,
#             "imbalance_strategy":  "Fix target column first",
#             "smote_recommended":   False,
#             "primary_eval_metric": None,
#             "secondary_metric":    None,
#             "reason":              "A model cannot be trained when the label itself is corrupt.",
#         }

#     # Target completeness issue
#     if completeness < 95.0:
#         missing_n = round((100 - completeness) / 100 * row_count)
#         return {
#             "training_ready":      False,
#             "blocker":             f"Target column '{target_col}' is {100 - completeness:.1f}% missing ({missing_n:,} rows). Labels are incomplete.",
#             "minority_count":      None,
#             "imbalance_strategy":  "Resolve missing labels first",
#             "smote_recommended":   False,
#             "primary_eval_metric": None,
#             "secondary_metric":    None,
#             "reason":              "Training on incomplete labels produces a model that is calibrated on a biased sample.",
#         }

#     # Compute absolute minority count — the key number S6 does not produce
#     minority_count = round(event_rate / 100 * row_count)
#     majority_count = row_count - minority_count

#     # Imbalance strategy decision
#     if balance_lbl == "balanced":
#         strategy      = "Standard stratified train/test split. No class weighting needed."
#         smote         = False
#         primary_met   = "ROC-AUC"
#         secondary_met = "F1"
#         reason = (
#             f"Event rate {event_rate}% — balanced distribution. "
#             f"Standard training applies with stratified splits."
#         )

#     elif balance_lbl == "moderate":
#         strategy      = "Use class_weight='balanced' and stratified k-fold cross-validation."
#         smote         = minority_count > 1000
#         primary_met   = "ROC-AUC"
#         secondary_met = "Precision-Recall AUC"
#         reason = (
#             f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). "
#             f"Minority class has {minority_count:,} rows. "
#             + (
#                 "Sufficient for class_weight approach. SMOTE not needed."
#                 if minority_count > 1000
#                 else "Low minority count — SMOTE risky. Prefer class_weight='balanced'."
#             )
#         )

#     else:  # severe
#         if minority_count < 300:
#             strategy    = "class_weight='balanced' only. Do NOT use SMOTE."
#             smote       = False
#             reason = (
#                 f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). "
#                 f"Only {minority_count:,} minority rows across {row_count:,} total. "
#                 f"SMOTE would synthesise in a near-empty high-dimensional space — "
#                 f"synthetic points will not represent real minority patterns. "
#                 f"Prioritise getting more real data."
#             )
#         elif minority_count < 2000:
#             strategy    = "class_weight='balanced' + stratified k-fold. SMOTE with caution — max 200% oversampling ratio."
#             smote       = True
#             reason = (
#                 f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). "
#                 f"{minority_count:,} minority rows — borderline for SMOTE. "
#                 f"If using SMOTE, cap oversampling at 200% of minority size to avoid overfitting on synthetic points."
#             )
#         else:
#             strategy    = "Threshold calibration preferred over SMOTE. Use class_weight='balanced' + stratified k-fold."
#             smote       = False
#             reason = (
#                 f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). "
#                 f"{minority_count:,} minority rows — enough for threshold calibration. "
#                 f"SMOTE adds complexity without benefit at this minority count. "
#                 f"Tune the decision threshold on the validation set instead."
#             )
#         primary_met   = "Precision-Recall AUC"
#         secondary_met = "ROC-AUC (as secondary — misleading at this imbalance ratio)"

#     return {
#         "training_ready":      True,
#         "blocker":             None,
#         "minority_count":      minority_count,
#         "majority_count":      majority_count,
#         "event_rate":          event_rate,
#         "imbalance_ratio":     ratio,
#         "balance_label":       balance_lbl,
#         "imbalance_strategy":  strategy,
#         "smote_recommended":   smote,
#         "primary_eval_metric": primary_met,
#         "secondary_metric":    secondary_met,
#         "reason":              reason,
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # I3 · Preprocessing checklist
# # Reads: s3 (warnings), s5 (readiness), s7 (distribution), i1 (verdicts)
# # Answers: what do I need to do to these columns before fitting — in order?
# # ─────────────────────────────────────────────────────────────────────────────

# def i3_preprocessing_checklist(s3: List[Dict], s5: List[Dict],
#                                  s7: List[Dict], i1_verdicts: List[Dict]) -> List[Dict]:
#     """
#     Ordered preprocessing steps derived from the data.
#     Only includes columns with verdict fix_then_use.
#     Steps are ordered: quality_fix → imputation → outlier_cap → transformation → encoding.
#     """
#     # Only process columns that have fix_then_use verdict
#     fix_cols = {v["column"]: v for v in i1_verdicts if v["verdict"] == "fix_then_use"}

#     # Collect all steps from I1 ordered_steps
#     all_steps = []
#     step_num  = 1

#     # Process in type order so the output reads as a coherent pipeline
#     type_order = ["quality_fix", "imputation", "outlier_cap", "transformation", "encoding"]

#     # Gather and re-order all steps across all fix_then_use columns
#     by_type: Dict[str, List] = {t: [] for t in type_order}

#     for col_name, verdict in fix_cols.items():
#         for step in verdict.get("ordered_steps", []):
#             step_type = step.get("type", "quality_fix")
#             if step_type not in by_type:
#                 by_type[step_type] = []
#             by_type[step_type].append({
#                 "column":      col_name,
#                 "type":        step_type,
#                 "instruction": step["instruction"],
#                 "reason":      step["reason"],
#             })

#     for step_type in type_order:
#         for step in by_type.get(step_type, []):
#             all_steps.append({
#                 "step":        step_num,
#                 "type":        step["type"],
#                 "column":      step["column"],
#                 "instruction": step["instruction"],
#                 "reason":      step["reason"],
#             })
#             step_num += 1

#     return all_steps


# # ─────────────────────────────────────────────────────────────────────────────
# # I4 · Population shift assessment
# # Reads: c1 (version summary), c4 (distribution drift), c9 (health trend), drift_suite
# # Answers: has my training population fundamentally changed?
# # ─────────────────────────────────────────────────────────────────────────────

# def i4_population_shift(c1: Dict, c4: List[Dict], c9: Dict,
#                           drift_suite: Dict) -> Dict:
#     """
#     Determines whether the overall population has shifted, how broadly,
#     and the likely cause (organic / sampling / pipeline).
#     """
#     total_features  = len(c4)
#     if total_features == 0:
#         return {
#             "shift_scope":     "unknown",
#             "likely_cause":    "unknown",
#             "coordinated":     False,
#             "drifted_count":   0,
#             "drifted_features":[],
#             "v1_distance":     "unknown",
#             "row_delta_pct":   None,
#             "interpretation":  "No numeric features available for population shift analysis.",
#             "action":          "Check that numeric columns exist in the dataset.",
#         }

#     drifted = [c for c in c4 if c.get("overall_severity") in ("critical", "notable")]
#     drifted_count   = len(drifted)
#     drifted_pct     = drifted_count / total_features if total_features > 0 else 0.0
#     drifted_names   = [c["column"] for c in drifted]

#     # Row count delta from latest pairwise
#     row_delta_pct = None
#     pairwise = c1.get("pairwise", [])
#     if pairwise:
#         last_pw = pairwise[-1]
#         versions = c1.get("versions", [])
#         if versions:
#             base_rows = next((v["row_count"] for v in versions
#                               if v["name"] == last_pw.get("from")), None)
#             if base_rows and base_rows > 0:
#                 row_delta_pct = round(last_pw.get("row_delta", 0) / base_rows * 100, 1)

#     # Check baseline drift from drift_suite
#     baseline = drift_suite.get("baseline", {})
#     v1_distance = "unknown"
#     if baseline.get("applicable"):
#         shift_labels = []
#         for col_data in baseline.get("columns", {}).values():
#             shift_labels.append(col_data.get("worst_label", "stable"))
#         if shift_labels:
#             shift_count = sum(1 for l in shift_labels if l == "shift")
#             monitor_count = sum(1 for l in shift_labels if l == "monitor")
#             n = len(shift_labels)
#             if shift_count / n > 0.3:
#                 v1_distance = "far"
#             elif (shift_count + monitor_count) / n > 0.3:
#                 v1_distance = "moderate"
#             else:
#                 v1_distance = "close"

#     # Determine if drift is coordinated (features moving in same direction)
#     coordinated = False
#     if len(drifted) >= 3:
#         directions = []
#         for col in drifted:
#             flags = col.get("drift_flags", [])
#             if flags:
#                 last_flag = flags[-1]
#                 delta = _safe(last_flag.get("mean_delta"), 0.0)
#                 if delta != 0:
#                     directions.append("up" if delta > 0 else "down")
#         if len(directions) >= 3:
#             up_count   = directions.count("up")
#             down_count = directions.count("down")
#             # Coordinated = >70% moving same direction
#             coordinated = (up_count / len(directions) > 0.7 or
#                            down_count / len(directions) > 0.7)

#     # Scope classification
#     if drifted_pct > 0.30:
#         shift_scope = "broad"
#     elif drifted_pct > 0.10:
#         shift_scope = "narrow"
#     else:
#         shift_scope = "stable"

#     # Likely cause
#     large_row_delta = row_delta_pct is not None and abs(row_delta_pct) > 20
#     if shift_scope == "stable":
#         likely_cause = "none"
#     elif large_row_delta:
#         likely_cause = "sampling_change"
#     elif coordinated:
#         likely_cause = "organic_population_change"
#     else:
#         likely_cause = "mixed_or_pipeline"

#     # Interpretation text
#     if shift_scope == "stable":
#         interpretation = (
#             f"Population is stable — only {drifted_count}/{total_features} features "
#             f"show notable drift. No evidence of broad population change."
#         )
#         action = "No population-level action required. Monitor individual drifted features."
#     elif likely_cause == "sampling_change":
#         interpretation = (
#             f"{drifted_count}/{total_features} features drifted with a "
#             f"{row_delta_pct:+.1f}% row count change. Large volume change alongside drift "
#             f"indicates a sampling or filter rule change rather than organic population movement."
#         )
#         action = (
#             "Verify whether the population definition or sampling rules changed. "
#             "If intentional, retrain on the new population. "
#             "If unintentional, fix the upstream filter before training."
#         )
#     elif likely_cause == "organic_population_change":
#         interpretation = (
#             f"{drifted_count}/{total_features} features drifted in a coordinated direction "
#             f"(row delta: {row_delta_pct:+.1f}% — stable). "
#             f"Coordinated drift with stable volume = organic population shift. "
#             f"The model trained on the baseline is now scoring a different kind of customer."
#         )
#         action = (
#             "Back-test the current model on the latest version data urgently. "
#             "If Gini/KS has dropped >5 points, schedule a retrain. "
#             f"Baseline distance is {v1_distance} — "
#             + ("model degradation is likely." if v1_distance == "far" else "monitor closely.")
#         )
#     else:
#         interpretation = (
#             f"{drifted_count}/{total_features} features drifted in mixed directions. "
#             f"No single coordinated cause identified — could be pipeline noise or multiple independent changes."
#         )
#         action = (
#             "Investigate drifted features individually (see I6). "
#             "Check for upstream pipeline changes that might affect unrelated features simultaneously."
#         )

#     return {
#         "shift_scope":      shift_scope,
#         "likely_cause":     likely_cause,
#         "coordinated":      coordinated,
#         "drifted_count":    drifted_count,
#         "total_features":   total_features,
#         "drifted_features": drifted_names,
#         "v1_distance":      v1_distance,
#         "row_delta_pct":    row_delta_pct,
#         "interpretation":   interpretation,
#         "action":           action,
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # I5 · Target stability
# # Reads: c5 (target drift), c3 (completeness drift), c6 (quality regression)
# # Answers: is the target still measuring the same thing — and why did it drift?
# # ─────────────────────────────────────────────────────────────────────────────

# def i5_target_stability(c5: Optional[Dict], c3: Dict, c6: List[Dict]) -> Dict:
#     """
#     Distinguishes four drift causes:
#       organic_gradual  — real population change over time
#       organic_jump     — real but sudden shift (policy, product change)
#       data_loss        — missing target data is causing apparent drift
#       label_change     — label definition or coding has changed
#     """
#     if not c5:
#         return {
#             "target_found":         False,
#             "drift_type":           None,
#             "label_change_risk":    False,
#             "data_loss_risk":       False,
#             "model_impact":         "No target column — cannot assess.",
#             "action":               "Specify the target column to enable this analysis.",
#         }

#     target_col  = c5.get("column", "target")
#     vrates      = c5.get("version_rates", [])
#     pairwise    = c5.get("pairwise_drift", [])

#     if not pairwise:
#         return {
#             "target_found":         True,
#             "drift_type":           "stable",
#             "label_change_risk":    False,
#             "data_loss_risk":       False,
#             "event_rate_first":     vrates[0].get("event_rate") if vrates else None,
#             "event_rate_last":      vrates[-1].get("event_rate") if vrates else None,
#             "total_drift_pp":       0.0,
#             "model_impact":         "Target event rate is stable across all versions.",
#             "action":               "No action required on target.",
#         }

#     # Check target missingness pattern from C3
#     target_missing_pattern = None
#     for row in c3.get("rows", []):
#         if row["column"] == target_col:
#             target_missing_pattern = row.get("missing_pattern")
#             break

#     # Check target quality regression from C6
#     target_has_mismatch_regression = False
#     for row in c6:
#         if row["column"] == target_col:
#             if row.get("mismatch_trend") == "regressing":
#                 target_has_mismatch_regression = True
#             break

#     # Total drift and single-version jump detection
#     valid_pw = [p for p in pairwise if p.get("abs_delta_pp") is not None]
#     total_drift_pp = sum(p.get("delta_pp", 0.0) for p in valid_pw)
#     max_single_jump = max((p.get("abs_delta_pp", 0.0) for p in valid_pw), default=0.0)
#     n_pairs = len(valid_pw)

#     first_er = vrates[0].get("event_rate") if vrates else None
#     last_er  = vrates[-1].get("event_rate") if vrates else None

#     # Cause classification
#     data_loss_risk   = target_missing_pattern in ("growing_missing", "newly_missing", "sparse")
#     label_change_risk = (
#         target_has_mismatch_regression or
#         (n_pairs >= 1 and max_single_jump > 5.0)  # >5pp in a single version = likely label event
#     )

#     if data_loss_risk:
#         drift_type = "data_loss"
#         model_impact = (
#             f"Target missingness pattern is '{target_missing_pattern}'. "
#             f"Apparent event rate drift is caused by missing labels, not real population change. "
#             f"The model is not at fault — the data supply is."
#         )
#         action = (
#             "Fix the data pipeline to restore target completeness before any model action. "
#             "Re-run this comparison after completeness is above 95% — drift will likely disappear."
#         )
#     elif label_change_risk:
#         drift_type = "label_change"
#         model_impact = (
#             f"Event rate jumped {max_single_jump:.1f}pp in a single version "
#             + ("and mismatch count increased — " if target_has_mismatch_regression else "— ")
#             + "this pattern indicates a label definition or coding change, not organic drift. "
#             f"The model trained before this version is now predicting a different outcome."
#         )
#         action = (
#             "Investigate the label logic change with the business team before any model action. "
#             "If the new definition is permanent, full retraining on data after the change is mandatory. "
#             "Do not recalibrate threshold — the label itself is different."
#         )
#     elif n_pairs >= 2 and max_single_jump < 3.0:
#         drift_type = "organic_gradual"
#         model_impact = (
#             f"Event rate drifted {total_drift_pp:+.1f}pp total across {n_pairs} version(s) "
#             f"({first_er}% → {last_er}%). "
#             f"Gradual drift = organic population change. "
#             f"Model rank-ordering is likely still valid but calibration is off."
#         )
#         action = (
#             "Recalibrate the decision threshold on the latest version's validation set. "
#             "Full retraining is not yet required unless performance metrics have dropped "
#             "more than 5 Gini points."
#         )
#     else:
#         drift_type = "organic_jump"
#         model_impact = (
#             f"Event rate shifted {total_drift_pp:+.1f}pp with a max single-version jump "
#             f"of {max_single_jump:.1f}pp. Likely a real but rapid population change "
#             f"(policy, product, economic event)."
#         )
#         action = (
#             "Back-test the current model on the latest version urgently. "
#             "If performance has degraded, retrain — the population has shifted fast enough "
#             "that the old model's learned boundaries may no longer apply."
#         )

#     return {
#         "target_found":         True,
#         "target_column":        target_col,
#         "drift_type":           drift_type,
#         "label_change_risk":    label_change_risk,
#         "data_loss_risk":       data_loss_risk,
#         "event_rate_first":     first_er,
#         "event_rate_last":      last_er,
#         "total_drift_pp":       round(total_drift_pp, 2),
#         "max_single_jump_pp":   round(max_single_jump, 2),
#         "model_impact":         model_impact,
#         "action":               action,
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # I6 · Feature drift impact
# # Reads: c3 (completeness drift), c4 (distribution drift), c8 (PSI), drift_suite
# # Answers: which drifted features will actually hurt model predictions?
# # ─────────────────────────────────────────────────────────────────────────────

# def i6_feature_drift_impact(c3: Dict, c4: List[Dict], c8: Dict,
#                               drift_suite: Dict) -> List[Dict]:
#     """
#     For each column flagged by PSI, determines whether the drift is real
#     or caused by data loss — and what the specific modeling consequence is.

#     Returns only columns with genuine drift impact (data_loss cases filtered).
#     """
#     # Index c3 missingness patterns
#     miss_patterns = {row["column"]: row.get("missing_pattern", "complete")
#                      for row in c3.get("rows", [])}

#     # Index C4 drift flags by column
#     c4_by_col = {c["column"]: c for c in c4}

#     # Index drift_suite quantile shift by column (latest pair)
#     ds_consecutive = drift_suite.get("consecutive", {})

#     results = []

#     for col_entry in c8.get("columns", []):
#         col_name     = col_entry["column"]
#         worst_label  = col_entry.get("worst_label", "stable")

#         if worst_label not in ("shift", "monitor"):
#             continue

#         miss_pattern = miss_patterns.get(col_name, "complete")
#         c4_entry     = c4_by_col.get(col_name)

#         # ── Data loss check: growing/newly missing drives the PSI ───────────
#         is_data_loss = miss_pattern in ("growing_missing", "newly_missing", "sparse")

#         if is_data_loss:
#             results.append({
#                 "column":       col_name,
#                 "psi_label":    worst_label,
#                 "drift_cause":  "data_loss",
#                 "is_real_drift": False,
#                 "evidence":     (
#                     f"PSI is '{worst_label}' but missingness pattern is '{miss_pattern}'. "
#                     f"Distribution shift is caused by data loss, not population change."
#                 ),
#                 "model_impact": "No genuine model impact. PSI will normalise once pipeline is fixed.",
#                 "fix":          (
#                     "Fix the upstream data pipeline to restore completeness. "
#                     "Re-run PSI after fixing — it will likely drop below the monitor threshold."
#                 ),
#             })
#             continue

#         # ── Real drift: classify the cause ──────────────────────────────────
#         drift_cause  = "distribution_shift"
#         model_impact = ""
#         fix          = ""
#         evidence_parts = []

#         # Get latest pair PSI value
#         latest_psi = None
#         for pair in col_entry.get("pairs", []):
#             if pair.get("applicable") and pair.get("psi") is not None:
#                 latest_psi = pair["psi"]

#         if latest_psi is not None:
#             evidence_parts.append(f"PSI={latest_psi:.3f} ({worst_label})")

#         # Check quantile shift from drift_suite
#         col_pairs = ds_consecutive.get(col_name, [])
#         latest_metrics = col_pairs[-1] if col_pairs else None
#         qs = latest_metrics.get("quantile_shift", {}) if latest_metrics else {}
#         bd = latest_metrics.get("boundary_drift", {}) if latest_metrics else {}
#         std_d = latest_metrics.get("std_drift", {}) if latest_metrics else {}

#         # Center shift: median moved
#         qs_shifts = qs.get("shifts", {})
#         median_shift = abs(_safe(qs_shifts.get("Median"), 0.0))
#         q1_shift     = abs(_safe(qs_shifts.get("Q1"), 0.0))
#         q3_shift     = abs(_safe(qs_shifts.get("Q3"), 0.0))

#         # Boundary expansion
#         upper_shift = abs(_safe(bd.get("upper_shift"), 0.0))
#         lower_shift = abs(_safe(bd.get("lower_shift"), 0.0))

#         # Std change
#         std_norm_change = _safe(std_d.get("norm_change"), 0.0)

#         # C4 mean delta
#         c4_flags = c4_entry.get("drift_flags", []) if c4_entry else []
#         mean_delta = _safe(c4_flags[-1].get("mean_delta"), 0.0) if c4_flags else 0.0

#         if median_shift > 0.20 or abs(mean_delta) > 0.10:
#             drift_cause = "center_shift"
#             if mean_delta != 0:
#                 evidence_parts.append(f"Mean shifted by {mean_delta:+.4f}")
#             if median_shift > 0:
#                 evidence_parts.append(f"Median moved {median_shift:.2f}× IQR")
#             model_impact = (
#                 "WoE bins and cutoffs anchored to the old distribution centre are now "
#                 "misaligned. Records that used to fall into one bin are now in the next bin — "
#                 "wrong score assigned."
#             )
#             fix = (
#                 f"Refit WoE bins for '{col_name}' on the latest version data. "
#                 "Do not retrain the full model until rebinning is done and performance re-evaluated."
#             )

#         elif upper_shift > 0.10 or lower_shift > 0.10:
#             drift_cause = "boundary_expansion"
#             if upper_shift > 0.10:
#                 evidence_parts.append(f"Upper boundary expanded {upper_shift*100:.0f}% of base range")
#             if lower_shift > 0.10:
#                 evidence_parts.append(f"Lower boundary shifted {lower_shift*100:.0f}% of base range")
#             model_impact = (
#                 "New values have appeared outside the training range. "
#                 "The model will extrapolate for these records — predictions are unreliable "
#                 "at the extremes."
#             )
#             fix = (
#                 f"Update winsorisation/capping rules for '{col_name}' to cover the new range. "
#                 "Re-score records that fall outside the old boundaries and validate manually."
#             )

#         elif std_norm_change > 0.25:
#             drift_cause = "spread_change"
#             evidence_parts.append(f"Std changed {std_norm_change*100:.0f}% relative to base")
#             model_impact = (
#                 "Distribution spread has changed. Z-score scalers fitted on the old std "
#                 "are now producing incorrect normalised values — affects linear model inputs."
#             )
#             fix = (
#                 f"Refit the scaler for '{col_name}' on the latest version. "
#                 "No model retrain needed — scaling is a preprocessing step."
#             )

#         else:
#             drift_cause = "distribution_shift"
#             model_impact = (
#                 f"Distribution has shifted (PSI={latest_psi:.3f if latest_psi else 'n/a'}). "
#                 "Specific mechanism unclear from available metadata — "
#                 "full data access would be needed to characterise precisely."
#             )
#             fix = (
#                 f"Monitor '{col_name}' in next version. If PSI remains above threshold, "
#                 "refit preprocessing and evaluate model performance on the latest data."
#             )

#         results.append({
#             "column":        col_name,
#             "psi_label":     worst_label,
#             "drift_cause":   drift_cause,
#             "is_real_drift": True,
#             "evidence":      ". ".join(evidence_parts) + "." if evidence_parts else "PSI threshold exceeded.",
#             "model_impact":  model_impact,
#             "fix":           fix,
#         })

#     # Sort: real drift first, then data_loss
#     results.sort(key=lambda x: (0 if x["is_real_drift"] else 1, x["column"]))
#     return results


# # ─────────────────────────────────────────────────────────────────────────────
# # I7 · Model action decision
# # Reads: c0 (verdict), i5_result, i6_result, drift_suite
# # Answers: retrain, recalibrate, rebin, or hold?
# # ─────────────────────────────────────────────────────────────────────────────

# def i7_model_action(c0: Dict, i5_result: Dict, i6_result: List[Dict],
#                      drift_suite: Dict) -> Dict:
#     """
#     Converts the C0 verdict into a specific, justified prescription.
#     Tells the DS not just what to do but what NOT to do.
#     """
#     verdict    = c0.get("verdict", "CLEAR")
#     i5_type    = i5_result.get("drift_type") if i5_result else None
#     target_found = i5_result.get("target_found", False) if i5_result else False

#     # Count genuine (non-data-loss) drifted features
#     real_drifts    = [f for f in i6_result if f.get("is_real_drift", True)]
#     data_loss_only = len(real_drifts) == 0 and len(i6_result) > 0

#     # Drift velocity signal
#     velocity_results = drift_suite.get("velocity", {})
#     accelerating_cols = []
#     for col_name, vel in velocity_results.items():
#         if vel.get("applicable") and _safe(vel.get("velocity"), 0.0) > 0.05:
#             accelerating_cols.append(col_name)

#     # Chronically unstable features (FSI < 0.4)
#     fsi_results = drift_suite.get("fsi", {})
#     unstable_cols = [
#         col for col, fsi in fsi_results.items()
#         if fsi.get("applicable") and _safe(fsi.get("fsi"), 1.0) < 0.40
#     ]

#     # ── Decision logic ──────────────────────────────────────────────────────
#     if verdict == "BLOCK":
#         decision     = "hold"
#         urgency      = "pipeline_fix_first"
#         reason       = (
#             "Dataset readiness has dropped below the minimum threshold. "
#             "Data quality issues must be resolved before any model action is meaningful."
#         )
#         steps        = [
#             "Identify and fix the upstream data quality issues causing the BLOCK verdict.",
#             "Re-run comparison after fixes — re-assess C0 verdict.",
#             "Do not retrain, rebin, or recalibrate until the dataset scores above 45/100.",
#         ]
#         avoid        = (
#             "Do not retrain or recalibrate on degraded data — "
#             "a model trained on this version will perform worse than the existing one."
#         )

#     elif verdict == "CLEAR":
#         decision     = "hold"
#         urgency      = "none"
#         reason       = "No significant drift detected. Current model remains valid."
#         steps        = ["Continue monitoring. Re-run comparison on next version ingestion."]
#         avoid        = None

#     elif data_loss_only:
#         decision     = "hold"
#         urgency      = "pipeline_fix_first"
#         reason       = (
#             f"C0 flagged {verdict} but all PSI flags are driven by data loss "
#             f"(growing/newly missing values), not real population change. "
#             "There is no genuine drift to respond to — the pipeline is the problem."
#         )
#         steps        = [
#             "Fix data pipeline to restore completeness.",
#             "Re-run comparison after completeness is restored.",
#             "Only take model action if PSI flags remain after pipeline fix.",
#         ]
#         avoid        = "Do not retrain or recalibrate — the model is not the problem."

#     elif i5_type == "data_loss":
#         decision     = "hold"
#         urgency      = "pipeline_fix_first"
#         reason       = (
#             "Target drift is caused by missing labels, not a real event rate change. "
#             "Model performance cannot be assessed until label completeness is restored."
#         )
#         steps        = [
#             "Restore target completeness via pipeline fix.",
#             "Re-evaluate after fix — target drift may disappear entirely.",
#         ]
#         avoid        = "Do not retrain or recalibrate until labels are complete."

#     elif i5_type == "label_change":
#         decision     = "retrain"
#         urgency      = "immediate"
#         reason       = i5_result.get("model_impact", "Label definition changed.")
#         steps        = [
#             "Confirm the label change with the business team.",
#             "Retrain the model exclusively on data from after the label change.",
#             "Do not use pre-change data — it reflects a different outcome definition.",
#             "Full model validation required before any production deployment.",
#         ]
#         avoid        = (
#             "Do not attempt threshold recalibration — the label itself is different. "
#             "Recalibrating would optimise for the wrong definition."
#         )

#     elif i5_type in ("organic_jump",) or (target_found and _safe(
#             i5_result.get("total_drift_pp"), 0.0) and
#             abs(_safe(i5_result.get("total_drift_pp"), 0.0)) > 5.0):
#         decision     = "retrain"
#         urgency      = "next_cycle"
#         drift_pp     = abs(_safe(i5_result.get("total_drift_pp"), 0.0))
#         reason       = (
#             f"Target event rate shifted {drift_pp:.1f}pp. "
#             + (f"Drift is accelerating ({len(accelerating_cols)} features). "
#                if accelerating_cols else "")
#             + "Population has moved enough that model boundaries need to be relearned."
#         )
#         steps        = [
#             "Back-test current model on latest version data.",
#             "If Gini/KS dropped >5 points, confirm retrain is needed.",
#             f"Refit WoE bins for: {', '.join(c['column'] for c in real_drifts[:5])}." if real_drifts else "Refit preprocessing on latest data.",
#             "Retrain on latest version. Use at least 2 most recent versions as training data.",
#             "Validate on a held-out sample from the latest version only.",
#         ]
#         avoid        = (
#             "Do not retrain on all historical versions — older data reflects a different population. "
#             "Use recency-weighted training or a rolling window."
#         )

#     elif real_drifts and all(d["drift_cause"] in ("center_shift",) for d in real_drifts):
#         decision     = "rebin"
#         urgency      = "next_cycle"
#         drift_cols   = [d["column"] for d in real_drifts]
#         reason       = (
#             f"Target drift is minor (organic_gradual). "
#             f"{len(drift_cols)} feature(s) have stale WoE bins due to centre shift: "
#             f"{', '.join(drift_cols[:5])}."
#             + (" Drift velocity is stable." if not accelerating_cols else
#                f" Note: {len(accelerating_cols)} feature(s) are accelerating.")
#         )
#         steps        = [
#             f"Refit WoE bins for: {', '.join(drift_cols[:5])}{'...' if len(drift_cols) > 5 else ''}.",
#             "Back-test existing model on latest version data after rebinning.",
#             "Recalibrate decision threshold on the latest version's validation set.",
#             "Do not retrain the model — rank-ordering is likely still valid.",
#         ]
#         avoid        = (
#             "Do not perform a full retrain — drift is in binning parameters, not model weights. "
#             "Retraining adds weeks of effort without meaningful gain at this drift level."
#         )

#     elif unstable_cols:
#         decision     = "rebin"
#         urgency      = "next_cycle"
#         reason       = (
#             f"Drift is present but manageable. "
#             f"{len(unstable_cols)} feature(s) are chronically unstable (FSI < 0.40): "
#             f"{', '.join(unstable_cols[:5])}. "
#             "Dropping these features is a better fix than retraining with them."
#         )
#         steps        = [
#             f"Consider dropping chronically unstable features: {', '.join(unstable_cols[:5])}.",
#             "Refit preprocessing on remaining features using latest version data.",
#             "Back-test and recalibrate threshold.",
#         ]
#         avoid        = (
#             "Do not retrain while chronically unstable features remain in the feature set — "
#             "they will introduce variance into the new model too."
#         )

#     else:
#         decision     = "recalibrate"
#         urgency      = "next_cycle"
#         total_pp     = abs(_safe(i5_result.get("total_drift_pp"), 0.0)) if i5_result else 0.0
#         reason       = (
#             f"Drift detected but manageable: target drifted {total_pp:.1f}pp organically and gradually. "
#             + (f"{len(real_drifts)} feature(s) with notable PSI but no severe centre shift. "
#                if real_drifts else "")
#             + "Model rank-ordering likely still valid."
#         )
#         steps        = [
#             "Back-test current model on latest version data.",
#             "Recalibrate the decision threshold (cutoff score) on the latest validation set.",
#             "If PR-AUC has dropped >3 points, escalate to rebin.",
#         ]
#         avoid        = (
#             "Do not retrain — drift is not deep enough to justify the effort. "
#             "Threshold recalibration takes hours, retraining takes weeks."
#         )

#     return {
#         "decision":           decision,
#         "urgency":            urgency,
#         "reason":             reason,
#         "steps":              steps,
#         "avoid":              avoid,
#         "accelerating_cols":  accelerating_cols,
#         "unstable_cols":      unstable_cols,
#         "real_drift_count":   len(real_drifts),
#         "data_loss_only":     data_loss_only,
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # I8 · Scoring pipeline break risks
# # Reads: c2 (schema changes), c8 (PSI), c10 (cardinality drift), drift_suite
# # Answers: what will silently produce wrong scores when I deploy against new data?
# # ─────────────────────────────────────────────────────────────────────────────

# def i8_pipeline_break_risks(c2: List[Dict], c8: Dict,
#                               c10: List[Dict], drift_suite: Dict) -> List[Dict]:
#     """
#     Detects silent failures — things that won't throw an error but
#     will produce wrong model scores when deployed against new data.
#     Only examines the most recent version transition.
#     """
#     risks = []

#     # ── Pattern 1: Dropped model feature ───────────────────────────────────
#     # Check only the last schema change pair
#     last_schema = c2[-1] if c2 else {}
#     for col in last_schema.get("dropped", []):
#         risks.append({
#             "risk":     "dropped_feature",
#             "severity": "critical",
#             "column":   col["column"],
#             "detail":   (
#                 f"Column '{col['column']}' was dropped in the latest version "
#                 f"(last completeness: {col.get('last_completeness', '?')}%). "
#                 f"If this column is in the current model's feature set, the scoring pipeline "
#                 f"will fill it with NaN or 0 silently — no error will be raised."
#             ),
#             "fix": (
#                 f"Check the deployed model's feature list for '{col['column']}'. "
#                 "If present, update the pipeline before scoring against this version. "
#                 "Either source the column from an alternative feed or retrain without it."
#             ),
#         })

#     # ── Pattern 2: Type-changed feature ────────────────────────────────────
#     for col in last_schema.get("type_changed", []):
#         risks.append({
#             "risk":     "type_mismatch",
#             "severity": "critical",
#             "column":   col["column"],
#             "detail":   (
#                 f"Column '{col['column']}' changed type: {col['from_type']} → {col['to_type']}. "
#                 f"Preprocessing steps (encoders, scalers, WoE) fitted on the old type "
#                 f"will produce incorrect outputs on the new type silently."
#             ),
#             "fix": (
#                 "Refit all preprocessing steps for this column on the new type. "
#                 "Validate that encoded values are still in the expected range before scoring."
#             ),
#         })

#     # ── Pattern 3: Cardinality explosion (unseen categories) ───────────────
#     for item in c10:
#         # Only flag the most recent explosion
#         explosions = item.get("explosions", [])
#         if not explosions:
#             continue
#         last_exp = explosions[-1]
#         if last_exp.get("severity") not in ("critical", "notable"):
#             continue

#         col_name    = item["column"]
#         from_card   = last_exp.get("from_card")
#         to_card     = last_exp.get("to_card")
#         ratio       = last_exp.get("ratio")

#         # Check PSI to determine severity
#         psi_label = "stable"
#         for col_entry in c8.get("columns", []):
#             if col_entry["column"] == col_name:
#                 psi_label = col_entry.get("worst_label", "stable")
#                 break

#         if psi_label in ("shift", "monitor") or last_exp.get("severity") == "critical":
#             severity = "critical"
#             detail = (
#                 f"Cardinality grew from {from_card} to {to_card} categories "
#                 f"(×{ratio:.1f}x). {to_card - from_card if to_card and from_card else '?'} new category values "
#                 f"have no WoE weight or one-hot encoding column from training. "
#                 f"These records will be assigned the 'unknown' bin weight, "
#                 f"which was fitted on a tiny fraction of training data."
#             )
#         else:
#             severity = "notable"
#             detail = (
#                 f"Cardinality grew from {from_card} to {to_card} categories (×{ratio:.1f}x). "
#                 f"New category values will fall into the 'unknown' bin. "
#                 f"PSI is stable — impact may be limited if new categories are rare."
#             )

#         risks.append({
#             "risk":     "unseen_category",
#             "severity": severity,
#             "column":   col_name,
#             "detail":   detail,
#             "fix": (
#                 f"Refit WoE bins or encoder for '{col_name}' on the latest version. "
#                 "Add an explicit 'other' or 'unknown' bucket during binning to handle "
#                 "future unseen values safely."
#             ),
#         })

#     # ── Pattern 4: Boundary expansion (extrapolation risk) ─────────────────
#     ds_consecutive = drift_suite.get("consecutive", {})
#     for col_name, pair_metrics in ds_consecutive.items():
#         if not pair_metrics:
#             continue
#         last_pm = pair_metrics[-1]
#         if not last_pm:
#             continue
#         bd = last_pm.get("boundary_drift", {})
#         if not bd.get("applicable"):
#             continue
#         if bd.get("severity") != "critical":
#             continue

#         upper = _safe(bd.get("upper_shift"), 0.0)
#         lower = _safe(bd.get("lower_shift"), 0.0)
#         max_b  = bd.get("max_new")
#         min_b  = bd.get("min_new")
#         max_old= bd.get("max_base")
#         min_old= bd.get("min_base")

#         parts = []
#         if abs(upper) >= 0.25:
#             parts.append(f"upper boundary expanded: {max_old} → {max_b}")
#         if abs(lower) >= 0.25:
#             parts.append(f"lower boundary shifted: {min_old} → {min_b}")

#         if not parts:
#             continue

#         risks.append({
#             "risk":     "range_extrapolation",
#             "severity": "notable",
#             "column":   col_name,
#             "detail":   (
#                 f"Value range has expanded significantly: {'; '.join(parts)}. "
#                 f"The model will extrapolate for records outside its training range. "
#                 f"Scores are computed but unreliable at the extremes — no error is raised."
#             ),
#             "fix": (
#                 f"Update the capping/winsorisation rule for '{col_name}' to cover the new range. "
#                 "Inspect records at the new extremes manually to validate score reasonableness."
#             ),
#         })

#     # Sort: critical first
#     risks.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, x["column"]))
#     return risks


# # ─────────────────────────────────────────────────────────────────────────────
# # I9 · Pipeline health
# # Reads: c3 (completeness drift), c6 (quality regression), c9 (health trend)
# # Answers: is my data pipeline getting better or worse — and should I escalate?
# # ─────────────────────────────────────────────────────────────────────────────

# def i9_pipeline_health(c3: Dict, c6: List[Dict], c9: Dict) -> Dict:
#     """
#     Distinguishes systematic pipeline degradation (escalate to engineering)
#     from isolated column issues (investigate yourself) and normal variation.
#     """
#     version_labels = c3.get("version_labels", [])
#     n_versions     = len(version_labels)
#     rows           = c3.get("rows", [])
#     total_cols     = len(rows)

#     if total_cols == 0:
#         return {
#             "pipeline_health":          "unknown",
#             "pattern":                  "unknown",
#             "escalate_to_engineering":  False,
#             "affected_columns":         [],
#             "first_seen_version":       None,
#             "health_score_delta":       None,
#             "likely_cause":             "No completeness data available.",
#             "action":                   "Ingest data and re-run comparison.",
#         }

#     # Count missingness patterns
#     pattern_counts = {}
#     for row in rows:
#         p = row.get("missing_pattern", "complete")
#         pattern_counts[p] = pattern_counts.get(p, 0) + 1

#     degrading_patterns = ("growing_missing", "newly_missing")
#     degrading_cols     = [r["column"] for r in rows
#                           if r.get("missing_pattern") in degrading_patterns]
#     sparse_cols        = [r["column"] for r in rows
#                           if r.get("missing_pattern") == "sparse"]
#     recovering_cols    = [r["column"] for r in rows
#                           if r.get("missing_pattern") == "recovering"]

#     # Quality regression from C6
#     regressing_quality_cols = [
#         r["column"] for r in c6
#         if r.get("mismatch_trend") == "regressing" or r.get("blank_trend") == "regressing"
#     ]

#     # Health score delta from C9
#     ds_scores = c9.get("dataset_scores", [])
#     health_score_delta = None
#     if len(ds_scores) >= 2:
#         first_score = _safe(ds_scores[0].get("score"), 0.0)
#         last_score  = _safe(ds_scores[-1].get("score"), 0.0)
#         health_score_delta = round(last_score - first_score, 1)

#     # Degradation breadth
#     degrading_pct = len(degrading_cols) / max(total_cols, 1)

#     # Find first version where degradation started
#     first_seen = None
#     if degrading_cols and n_versions >= 2:
#         for row in rows:
#             if row["column"] in degrading_cols:
#                 vals = row.get("values", [])
#                 for i, val in enumerate(vals):
#                     if val is not None and val < 100:
#                         if first_seen is None and i < len(version_labels):
#                             first_seen = version_labels[i]
#                         break

#     # ── Systematic vs isolated classification ───────────────────────────────
#     # Systematic: >20% of columns degrading across 2+ versions
#     is_systematic = (
#         degrading_pct > 0.20 or
#         (len(degrading_cols) >= 3 and n_versions >= 3) or
#         (len(regressing_quality_cols) >= 3)
#     )

#     # Try to infer likely cause from which columns are degrading together
#     likely_cause = "Unknown — investigate data sources for affected columns."
#     if degrading_cols:
#         # Simple heuristic: group by common prefix/suffix
#         prefixes = {}
#         for col in degrading_cols:
#             parts = col.lower().split("_")
#             if len(parts) >= 2:
#                 prefix = parts[0]
#                 prefixes[prefix] = prefixes.get(prefix, 0) + 1
#         dominant = sorted(prefixes.items(), key=lambda x: -x[1])
#         if dominant and dominant[0][1] >= 2:
#             likely_cause = (
#                 f"Multiple columns sharing '{dominant[0][0]}_*' prefix are degrading together — "
#                 f"likely a single upstream feed or source table is affected."
#             )

#     # Overall health verdict — evaluated top-to-bottom, first match wins
#     pipeline_health = "stable"
#     pattern         = "stable"
#     escalate        = False
#     action          = "No action required. Pipeline data quality is stable across versions."

#     if is_systematic and (health_score_delta is not None and health_score_delta < -5):
#         pipeline_health = "degrading"
#         pattern         = "systematic"
#         escalate        = True
#         action = (
#             "Escalate to data engineering. Multiple columns are degrading across versions "
#             "and overall dataset health is declining. Do not train on current data until "
#             "completeness is restored above 95% for affected columns. "
#             + likely_cause
#         )
#     elif len(degrading_cols) > 0 and not is_systematic:
#         pipeline_health = "degrading"
#         pattern         = "isolated"
#         escalate        = False
#         action = (
#             f"Investigate the {len(degrading_cols)} affected column(s) directly: "
#             f"{', '.join(degrading_cols[:5])}. "
#             "Check the specific upstream source for these columns. "
#             "No broad engineering escalation needed yet."
#         )
#     elif len(degrading_cols) == 0 and len(recovering_cols) > 0:
#         pipeline_health = "recovering"
#         pattern         = "recovering"
#         escalate        = False
#         action = (
#             f"{len(recovering_cols)} column(s) are recovering. "
#             "Verify completeness is above 90% in the next version before resuming training."
#         )
#     elif health_score_delta is not None and health_score_delta > 3:
#         pipeline_health = "improving"
#         pattern         = "improving"
#         escalate        = False
#         action = (
#             f"Dataset health improved {health_score_delta:+.1f} points. "
#             "Pipeline fixes are taking effect. Continue monitoring."
#         )

#     return {
#         "pipeline_health":         pipeline_health,
#         "pattern":                 pattern,
#         "escalate_to_engineering": escalate,
#         "affected_columns":        degrading_cols,
#         "sparse_columns":          sparse_cols,
#         "recovering_columns":      recovering_cols,
#         "quality_regressing":      regressing_quality_cols,
#         "first_seen_version":      first_seen,
#         "health_score_delta":      health_score_delta,
#         "likely_cause":            likely_cause,
#         "action":                  action,
#         "version_labels":          version_labels,
#     }











































































































"""
abt/interpretations.py
─────────────────────────────────────────────────────────────────────────────
Interpretation layer — cross-section synthesis for actionable DS guidance.

Two tiers:
  Tier A (original, rule-based only):
    i1_feature_verdicts, i2_training_readiness, i3_preprocessing_checklist
    i4_population_shift, i5_target_stability, i6_feature_drift_impact
    i7_model_action, i8_pipeline_break_risks, i9_pipeline_health

  Tier B (enhanced, with LLM narrative placeholder):
    i4b_population_shift, i5b_target_stability, i6b_feature_drift_impact
    i7b_model_action, i8b_pipeline_break_risks, i9b_pipeline_health

run_interpretations()          → adds i1–i9 to results dict (analyze + compare)
run_interpretations_enhanced() → adds i4b–i9b to results dict (compare only)

Called from run_analysis() and run_comparison().
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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


# ═════════════════════════════════════════════════════════════════════════════
# TIER A — ORIGINAL RULE-BASED INTERPRETATIONS (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# I1 · Feature usability verdict
# ─────────────────────────────────────────────────────────────────────────────

def i1_feature_verdicts(s2: List[Dict], s3: List[Dict], s4: List[Dict],
                         s7: List[Dict], s8: Dict, row_count: int) -> List[Dict]:
    row_count = max(row_count, 1)
    blocker_cols  = {b["column"]: b for b in s2}
    warning_cols  = {w["column"]: w for w in s3}
    gov_cols      = {g["column"]: g for g in s4}
    dist_cols     = {d["column"]: d for d in s7}

    seen = {}
    for items, key in [(s2, "column"), (s3, "column"), (s4, "column"), (s7, "column")]:
        for item in items:
            name = item[key]
            if name not in seen:
                seen[name] = True
    for name in s8.keys():
        if name not in seen:
            seen[name] = True

    results = []

    for col_name in seen:
        health_entry = s8.get(col_name, {})
        health_score = _safe(health_entry.get("score"), 50.0)
        blocker = blocker_cols.get(col_name)
        gov     = gov_cols.get(col_name)
        warning = warning_cols.get(col_name)
        dist    = dist_cols.get(col_name)

        if gov:
            risk_types = [r["risk_type"] for r in gov.get("risks", [])]
            if "LEAKAGE" in risk_types:
                leakage_risk = next(r for r in gov["risks"] if r["risk_type"] == "LEAKAGE")
                results.append({
                    "column": col_name, "verdict": "exclude",
                    "reason": (f"{leakage_risk['detail']}. Health score is {health_score}/100 but "
                               f"leakage features inflate AUC/KS by 20-30 points with zero real signal."),
                    "action": "Confirm the data source. If this is a prior model score on the same population, remove before training.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "IDENTIFIER" in risk_types:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Unique identifier — no predictive signal. Including it allows the model to memorise training rows.",
                    "action": "Remove from feature set entirely. Use only for row-level joins.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "PRIVACY" in risk_types:
                results.append({
                    "column": col_name, "verdict": "exclude",
                    "reason": "Marked as private. In regulated models (GDPR, FCRA, ECOA), using sensitive attributes requires documented justification and fairness testing.",
                    "action": "Obtain governance sign-off before including. Run fairness audit if approved.",
                    "effort": "high", "ordered_steps": [],
                })
                continue

        if blocker:
            rules = [r["rule"] for r in blocker.get("reasons", [])]
            if "zero_variance" in rules:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Zero variance — no predictive signal possible.",
                    "action": "Drop before model training. No imputation or transform will fix zero variance.",
                    "effort": "low", "ordered_steps": [],
                })
                continue
            if "high_missing" in rules:
                comp = _pct(blocker.get("completeness", 0))
                missing_n = round((100 - comp) / 100 * row_count)
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": (f"{100 - comp:.1f}% missing ({missing_n:,}/{row_count:,} rows). "
                               f"More than half the data is absent."),
                    "action": "Investigate root cause. If MCAR, drop. If MNAR, create a binary missingness indicator instead.",
                    "effort": "medium", "ordered_steps": [],
                })
                continue
            if "severe_mismatch" in rules:
                results.append({
                    "column": col_name, "verdict": "drop",
                    "reason": "Severe format mismatch (>15% of rows). Encoding is inconsistent at source.",
                    "action": "Fix at data source before any model use.",
                    "effort": "medium", "ordered_steps": [],
                })
                continue

        steps = []
        if warning:
            for iss in warning.get("issues", []):
                if iss["type"] == "blank_values":
                    blank_n = int(iss["detail"].split()[0].replace(",", ""))
                    steps.append({
                        "type": "quality_fix",
                        "instruction": f"Standardise {blank_n:,} blank strings in '{col_name}' to NaN before any other step.",
                        "reason": "Blank strings are not null — they silently break label encoders and WoE binning.",
                    })
            for iss in warning.get("issues", []):
                if iss["type"] == "partial_missing":
                    comp = _pct(warning.get("completeness", 100))
                    missing_n = round((100 - comp) / 100 * row_count)
                    skew = _safe(dist.get("skewness") if dist else None, 0.0)
                    method = "median" if abs(skew) > 0.5 else "mean"
                    reason = (f"skewness={skew:.2f} — mean is pulled by the {'right' if skew > 0 else 'left'} tail"
                              if abs(skew) > 0.5 else "distribution is near-symmetric")
                    steps.append({
                        "type": "imputation",
                        "instruction": f"Impute '{col_name}' with {method} ({missing_n:,} missing values, {100 - comp:.1f}% of rows).",
                        "reason": f"Use {method} because {reason}.",
                    })
                if iss["type"] == "format_mismatch":
                    steps.append({
                        "type": "quality_fix",
                        "instruction": f"Standardise encoding in '{col_name}' — case or format inconsistencies detected.",
                        "reason": "Case variations inflate cardinality and corrupt WoE bin weights.",
                    })

        if dist:
            skew    = _safe(dist.get("skewness"), 0.0)
            min_val = dist.get("min")
            has_out = dist.get("has_outliers", False)
            n_out   = int(_safe(dist.get("n_outliers"), 0))
            if has_out and n_out > 0:
                steps.append({
                    "type": "outlier_cap",
                    "instruction": f"Winsorise '{col_name}' at 1st/99th percentile — {n_out} outlier(s) detected.",
                    "reason": "Cap outliers before any transformation.",
                })
            if abs(skew) > 1.0:
                if min_val is not None and min_val > 0:
                    transform, t_reason = "log", f"min={min_val} > 0 so log is safe."
                elif min_val is not None and min_val == 0:
                    transform, t_reason = "log1p", "min=0 — log1p(x) handles zeros safely."
                elif min_val is not None and min_val < 0:
                    transform, t_reason = "reflect then log1p", f"min={min_val} < 0 — reflect then log1p."
                else:
                    transform, t_reason = "log1p", "min unknown — use log1p as a safe default."
                if skew < -1.0:
                    transform, t_reason = "reflect then log1p", f"Left-skewed (skewness={skew:.2f}). Reflect first."
                steps.append({
                    "type": "transformation",
                    "instruction": f"Apply {transform} to '{col_name}' after imputation and outlier capping.",
                    "reason": t_reason + " Not needed for tree-based models.",
                })

        if steps:
            issues_summary = []
            if warning:
                for iss in warning.get("issues", []):
                    issues_summary.append(iss["detail"])
            if dist and abs(_safe(dist.get("skewness"), 0.0)) > 1.0:
                issues_summary.append(f"skewness={_safe(dist.get('skewness'), 0.0):.2f}")
            results.append({
                "column": col_name, "verdict": "fix_then_use",
                "reason": "; ".join(issues_summary) if issues_summary else "Data quality or distribution issues require preprocessing.",
                "action": f"{len(steps)} preprocessing step(s) required before use. See ordered steps below.",
                "effort": "low" if len(steps) == 1 else "medium",
                "ordered_steps": steps,
            })
            continue

        results.append({
            "column": col_name, "verdict": "use",
            "reason": f"No blockers, governance flags, or significant data quality issues. Health score: {health_score}/100.",
            "action": "Include in feature set directly.",
            "effort": "none", "ordered_steps": [],
        })

    order = {"exclude": 0, "drop": 1, "fix_then_use": 2, "use": 3}
    results.sort(key=lambda x: order.get(x["verdict"], 4))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# I2 · Training readiness
# ─────────────────────────────────────────────────────────────────────────────

def i2_training_readiness(s6: Optional[Dict], s2: List[Dict], s1: Dict,
                           s0: Optional[Dict] = None) -> Dict:
    row_count    = max(int(_safe(s1.get("row_count"), 1)), 1)
    blocker_cols = {b["column"] for b in s2}

    if s0 is not None and s0.get("label") == "not_ready":
        return {
            "training_ready": False,
            "blocker": f"Overall dataset readiness is {s0['score']}/100 (not_ready). Fix blocked feature columns first.",
            "minority_count": None, "imbalance_strategy": "Fix blocked feature columns first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "A readiness score below 45 means too many features are unusable.",
        }

    if not s6:
        return {
            "training_ready": False,
            "blocker": "No target column detected. Specify target_col on the analysis page.",
            "minority_count": None, "imbalance_strategy": None,
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Cannot assess training readiness without a target column.",
        }

    if s6.get("error"):
        return {
            "training_ready": False, "blocker": s6["error"],
            "minority_count": None, "imbalance_strategy": None,
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Target column statistics are unavailable.",
        }

    target_col   = s6.get("column", "target")
    event_rate   = _safe(s6.get("event_rate"), 0.0)
    ratio        = _safe(s6.get("imbalance_ratio"), 1.0)
    balance_lbl  = s6.get("balance_label", "unknown")
    completeness = _safe(s6.get("completeness"), 100.0)

    if target_col in blocker_cols:
        return {
            "training_ready": False,
            "blocker": f"Target column '{target_col}' has a data quality blocker.",
            "minority_count": None, "imbalance_strategy": "Fix target column first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "A model cannot be trained when the label itself is corrupt.",
        }

    if completeness < 95.0:
        missing_n = round((100 - completeness) / 100 * row_count)
        return {
            "training_ready": False,
            "blocker": f"Target column '{target_col}' is {100 - completeness:.1f}% missing ({missing_n:,} rows).",
            "minority_count": None, "imbalance_strategy": "Resolve missing labels first",
            "smote_recommended": False, "primary_eval_metric": None, "secondary_metric": None,
            "reason": "Training on incomplete labels produces a model calibrated on a biased sample.",
        }

    minority_count = round(event_rate / 100 * row_count)
    majority_count = row_count - minority_count

    if balance_lbl == "balanced":
        strategy, smote = "Standard stratified train/test split. No class weighting needed.", False
        primary_met, secondary_met = "ROC-AUC", "F1"
        reason = f"Event rate {event_rate}% — balanced distribution. Standard training applies."
    elif balance_lbl == "moderate":
        strategy = "Use class_weight='balanced' and stratified k-fold cross-validation."
        smote = minority_count > 1000
        primary_met, secondary_met = "ROC-AUC", "Precision-Recall AUC"
        reason = (f"Event rate {event_rate}% ({ratio:.1f}:1 imbalance). Minority class has {minority_count:,} rows. "
                  + ("Sufficient for class_weight approach." if minority_count > 1000
                     else "Low minority count — prefer class_weight='balanced'."))
    else:
        if minority_count < 300:
            strategy, smote = "class_weight='balanced' only. Do NOT use SMOTE.", False
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). Only {minority_count:,} minority rows. "
                      f"SMOTE would synthesise in a near-empty space. Prioritise getting more real data.")
        elif minority_count < 2000:
            strategy = "class_weight='balanced' + stratified k-fold. SMOTE with caution — max 200% oversampling."
            smote = True
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). {minority_count:,} minority rows — "
                      f"borderline for SMOTE. Cap oversampling at 200% to avoid overfitting.")
        else:
            strategy, smote = "Threshold calibration preferred over SMOTE. Use class_weight='balanced' + stratified k-fold.", False
            reason = (f"Event rate {event_rate}% ({ratio:.1f}:1). {minority_count:,} minority rows — "
                      f"enough for threshold calibration. SMOTE adds complexity without benefit.")
        primary_met   = "Precision-Recall AUC"
        secondary_met = "ROC-AUC (as secondary — misleading at this imbalance ratio)"

    return {
        "training_ready": True, "blocker": None,
        "minority_count": minority_count, "majority_count": majority_count,
        "event_rate": event_rate, "imbalance_ratio": ratio, "balance_label": balance_lbl,
        "imbalance_strategy": strategy, "smote_recommended": smote,
        "primary_eval_metric": primary_met, "secondary_metric": secondary_met,
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# I3 · Preprocessing checklist
# ─────────────────────────────────────────────────────────────────────────────

def i3_preprocessing_checklist(s3: List[Dict], s5: List[Dict],
                                 s7: List[Dict], i1_verdicts: List[Dict]) -> List[Dict]:
    fix_cols  = {v["column"]: v for v in i1_verdicts if v["verdict"] == "fix_then_use"}
    type_order = ["quality_fix", "imputation", "outlier_cap", "transformation", "encoding"]
    by_type: Dict[str, List] = {t: [] for t in type_order}

    for col_name, verdict in fix_cols.items():
        for step in verdict.get("ordered_steps", []):
            step_type = step.get("type", "quality_fix")
            if step_type not in by_type:
                by_type[step_type] = []
            by_type[step_type].append({
                "column": col_name, "type": step_type,
                "instruction": step["instruction"], "reason": step["reason"],
            })

    all_steps = []
    step_num  = 1
    for step_type in type_order:
        for step in by_type.get(step_type, []):
            all_steps.append({
                "step": step_num, "type": step["type"], "column": step["column"],
                "instruction": step["instruction"], "reason": step["reason"],
            })
            step_num += 1
    return all_steps


# ─────────────────────────────────────────────────────────────────────────────
# I4 · Population shift (original)
# ─────────────────────────────────────────────────────────────────────────────

def i4_population_shift(c1: Dict, c4: List[Dict], c9: Dict, drift_suite: Dict) -> Dict:
    total_features = len(c4)
    if total_features == 0:
        return {
            "shift_scope": "unknown", "likely_cause": "unknown", "coordinated": False,
            "drifted_count": 0, "drifted_features": [], "v1_distance": "unknown",
            "row_delta_pct": None,
            "interpretation": "No numeric features available for population shift analysis.",
            "action": "Check that numeric columns exist in the dataset.",
        }

    drifted       = [c for c in c4 if c.get("overall_severity") in ("critical", "notable")]
    drifted_count = len(drifted)
    drifted_pct   = drifted_count / total_features if total_features > 0 else 0.0
    drifted_names = [c["column"] for c in drifted]

    row_delta_pct = None
    pairwise = c1.get("pairwise", [])
    if pairwise:
        last_pw  = pairwise[-1]
        versions = c1.get("versions", [])
        if versions:
            base_rows = next((v["row_count"] for v in versions
                              if v["name"] == last_pw.get("from")), None)
            if base_rows and base_rows > 0:
                row_delta_pct = round(last_pw.get("row_delta", 0) / base_rows * 100, 1)

    v1_distance = "unknown"
    baseline = drift_suite.get("baseline", {})
    if baseline.get("applicable"):
        shift_labels = [col_data.get("worst_label", "stable")
                        for col_data in baseline.get("columns", {}).values()]
        if shift_labels:
            n = len(shift_labels)
            shift_count   = sum(1 for l in shift_labels if l == "shift")
            monitor_count = sum(1 for l in shift_labels if l == "monitor")
            v1_distance   = ("far" if shift_count / n > 0.3
                             else "moderate" if (shift_count + monitor_count) / n > 0.3
                             else "close")

    coordinated = False
    if len(drifted) >= 3:
        directions = []
        for col in drifted:
            flags = col.get("drift_flags", [])
            if flags:
                delta = _safe(flags[-1].get("mean_delta"), 0.0)
                if delta != 0:
                    directions.append("up" if delta > 0 else "down")
        if len(directions) >= 3:
            up_count   = directions.count("up")
            down_count = directions.count("down")
            coordinated = (up_count / len(directions) > 0.7 or
                           down_count / len(directions) > 0.7)

    shift_scope = ("broad" if drifted_pct > 0.30 else
                   "narrow" if drifted_pct > 0.10 else "stable")

    large_row_delta = row_delta_pct is not None and abs(row_delta_pct) > 20
    likely_cause = ("none" if shift_scope == "stable"
                    else "sampling_change" if large_row_delta
                    else "organic_population_change" if coordinated
                    else "mixed_or_pipeline")

    if shift_scope == "stable":
        interpretation = (f"Population is stable — only {drifted_count}/{total_features} features "
                          f"show notable drift. No evidence of broad population change.")
        action = "No population-level action required. Monitor individual drifted features."
    elif likely_cause == "sampling_change":
        interpretation = (f"{drifted_count}/{total_features} features drifted with a "
                          f"{row_delta_pct:+.1f}% row count change. Indicates sampling or filter change.")
        action = ("Verify whether the population definition or sampling rules changed. "
                  "If intentional, retrain on the new population.")
    elif likely_cause == "organic_population_change":
        interpretation = (f"{drifted_count}/{total_features} features drifted in a coordinated direction. "
                          f"Organic population shift. The model is scoring a different kind of customer.")
        action = (f"Back-test the current model on the latest version data urgently. "
                  f"Baseline distance is {v1_distance}.")
    else:
        interpretation = (f"{drifted_count}/{total_features} features drifted in mixed directions. "
                          f"No single coordinated cause identified.")
        action = "Investigate drifted features individually (see I6)."

    return {
        "shift_scope": shift_scope, "likely_cause": likely_cause,
        "coordinated": coordinated, "drifted_count": drifted_count,
        "total_features": total_features, "drifted_features": drifted_names,
        "v1_distance": v1_distance, "row_delta_pct": row_delta_pct,
        "interpretation": interpretation, "action": action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# I5 · Target stability (original)
# ─────────────────────────────────────────────────────────────────────────────

def i5_target_stability(c5: Optional[Dict], c3: Dict, c6: List[Dict]) -> Dict:
    if not c5:
        return {
            "target_found": False, "drift_type": None, "label_change_risk": False,
            "data_loss_risk": False, "model_impact": "No target column — cannot assess.",
            "action": "Specify the target column to enable this analysis.",
        }

    target_col = c5.get("column", "target")
    vrates     = c5.get("version_rates", [])
    pairwise   = c5.get("pairwise_drift", [])

    if not pairwise:
        return {
            "target_found": True, "drift_type": "stable",
            "label_change_risk": False, "data_loss_risk": False,
            "event_rate_first": vrates[0].get("event_rate") if vrates else None,
            "event_rate_last":  vrates[-1].get("event_rate") if vrates else None,
            "total_drift_pp": 0.0,
            "model_impact": "Target event rate is stable across all versions.",
            "action": "No action required on target.",
        }

    target_missing_pattern = None
    for row in c3.get("rows", []):
        if row["column"] == target_col:
            target_missing_pattern = row.get("missing_pattern")
            break

    target_has_mismatch_regression = any(
        r["column"] == target_col and r.get("mismatch_trend") == "regressing"
        for r in c6
    )

    valid_pw        = [p for p in pairwise if p.get("abs_delta_pp") is not None]
    total_drift_pp  = sum(p.get("delta_pp", 0.0) for p in valid_pw)
    max_single_jump = max((p.get("abs_delta_pp", 0.0) for p in valid_pw), default=0.0)
    n_pairs         = len(valid_pw)

    first_er = vrates[0].get("event_rate") if vrates else None
    last_er  = vrates[-1].get("event_rate") if vrates else None

    data_loss_risk    = target_missing_pattern in ("growing_missing", "newly_missing", "sparse")
    label_change_risk = (target_has_mismatch_regression or
                         (n_pairs >= 1 and max_single_jump > 5.0))

    if data_loss_risk:
        drift_type   = "data_loss"
        model_impact = (f"Target missingness pattern is '{target_missing_pattern}'. "
                        f"Apparent drift is caused by missing labels, not real population change.")
        action = ("Fix the data pipeline to restore target completeness. "
                  "Re-run comparison after fixing.")
    elif label_change_risk:
        drift_type   = "label_change"
        model_impact = (f"Event rate jumped {max_single_jump:.1f}pp in a single version — "
                        "likely a label definition or coding change.")
        action = ("Investigate the label logic change before any model action. "
                  "If permanent, full retraining on data after the change is mandatory.")
    elif n_pairs >= 2 and max_single_jump < 3.0:
        drift_type   = "organic_gradual"
        model_impact = (f"Event rate drifted {total_drift_pp:+.1f}pp total across {n_pairs} version(s) "
                        f"({first_er}% → {last_er}%). Rank-ordering likely still valid but calibration is off.")
        action = ("Recalibrate the decision threshold on the latest version's validation set. "
                  "Full retraining not yet required unless Gini dropped >5 points.")
    else:
        drift_type   = "organic_jump"
        model_impact = (f"Event rate shifted {total_drift_pp:+.1f}pp with max single-version jump "
                        f"of {max_single_jump:.1f}pp. Likely a rapid real population change.")
        action = ("Back-test the current model on the latest version urgently. "
                  "If performance has degraded, retrain.")

    return {
        "target_found": True, "target_column": target_col, "drift_type": drift_type,
        "label_change_risk": label_change_risk, "data_loss_risk": data_loss_risk,
        "event_rate_first": first_er, "event_rate_last": last_er,
        "total_drift_pp": round(total_drift_pp, 2), "max_single_jump_pp": round(max_single_jump, 2),
        "model_impact": model_impact, "action": action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# I6 · Feature drift impact (original)
# ─────────────────────────────────────────────────────────────────────────────

def i6_feature_drift_impact(c3: Dict, c4: List[Dict], c8: Dict,
                              drift_suite: Dict) -> List[Dict]:
    miss_patterns  = {row["column"]: row.get("missing_pattern", "complete")
                      for row in c3.get("rows", [])}
    c4_by_col      = {c["column"]: c for c in c4}
    ds_consecutive = drift_suite.get("consecutive", {})
    results        = []

    for col_entry in c8.get("columns", []):
        col_name    = col_entry["column"]
        worst_label = col_entry.get("worst_label", "stable")
        if worst_label not in ("shift", "monitor"):
            continue

        miss_pattern = miss_patterns.get(col_name, "complete")
        c4_entry     = c4_by_col.get(col_name)
        is_data_loss = miss_pattern in ("growing_missing", "newly_missing", "sparse")

        if is_data_loss:
            results.append({
                "column": col_name, "psi_label": worst_label,
                "drift_cause": "data_loss", "is_real_drift": False,
                "evidence": (f"PSI is '{worst_label}' but missingness pattern is '{miss_pattern}'. "
                             f"Distribution shift caused by data loss, not population change."),
                "model_impact": "No genuine model impact. PSI will normalise once pipeline is fixed.",
                "fix": "Fix the upstream data pipeline to restore completeness.",
            })
            continue

        evidence_parts = []
        latest_psi = None
        for pair in col_entry.get("pairs", []):
            if pair.get("applicable") and pair.get("psi") is not None:
                latest_psi = pair["psi"]
        if latest_psi is not None:
            evidence_parts.append(f"PSI={latest_psi:.3f} ({worst_label})")

        col_pairs      = ds_consecutive.get(col_name, [])
        latest_metrics = col_pairs[-1] if col_pairs else None
        qs   = latest_metrics.get("quantile_shift", {}) if latest_metrics else {}
        bd   = latest_metrics.get("boundary_drift", {}) if latest_metrics else {}
        std_d= latest_metrics.get("std_drift", {}) if latest_metrics else {}

        qs_shifts    = qs.get("shifts", {})
        median_shift = abs(_safe(qs_shifts.get("Median"), 0.0))
        upper_shift  = abs(_safe(bd.get("upper_shift"), 0.0))
        lower_shift  = abs(_safe(bd.get("lower_shift"), 0.0))
        std_norm     = _safe(std_d.get("norm_change"), 0.0)
        c4_flags     = c4_entry.get("drift_flags", []) if c4_entry else []
        mean_delta   = _safe(c4_flags[-1].get("mean_delta"), 0.0) if c4_flags else 0.0

        if median_shift > 0.20 or abs(mean_delta) > 0.10:
            drift_cause  = "center_shift"
            if mean_delta != 0:
                evidence_parts.append(f"Mean shifted by {mean_delta:+.4f}")
            model_impact = ("WoE bins anchored to old distribution centre are misaligned. "
                            "Records falling into wrong bins — wrong score assigned.")
            fix          = f"Refit WoE bins for '{col_name}' on the latest version data."
        elif upper_shift > 0.10 or lower_shift > 0.10:
            drift_cause  = "boundary_expansion"
            if upper_shift > 0.10:
                evidence_parts.append(f"Upper boundary expanded {upper_shift*100:.0f}% of base range")
            model_impact = ("New values outside training range. Model extrapolates for these records.")
            fix          = f"Update winsorisation/capping rules for '{col_name}' to cover the new range."
        elif std_norm > 0.25:
            drift_cause  = "spread_change"
            evidence_parts.append(f"Std changed {std_norm*100:.0f}% relative to base")
            model_impact = ("Distribution spread changed. Z-score scalers fitted on old std produce incorrect values.")
            fix          = f"Refit the scaler for '{col_name}' on the latest version."
        else:
            drift_cause  = "distribution_shift"
            model_impact = f"Distribution shifted (PSI={latest_psi:.3f if latest_psi else 'n/a'}). Specific mechanism unclear."
            fix          = f"Monitor '{col_name}'. If PSI remains above threshold, refit preprocessing."

        results.append({
            "column": col_name, "psi_label": worst_label,
            "drift_cause": drift_cause, "is_real_drift": True,
            "evidence": ". ".join(evidence_parts) + "." if evidence_parts else "PSI threshold exceeded.",
            "model_impact": model_impact, "fix": fix,
        })

    results.sort(key=lambda x: (0 if x["is_real_drift"] else 1, x["column"]))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# I7 · Model action decision (original)
# ─────────────────────────────────────────────────────────────────────────────

def i7_model_action(c0: Dict, i5_result: Dict, i6_result: List[Dict],
                     drift_suite: Dict) -> Dict:
    verdict      = c0.get("verdict", "CLEAR")
    i5_type      = i5_result.get("drift_type") if i5_result else None
    target_found = i5_result.get("target_found", False) if i5_result else False

    real_drifts    = [f for f in i6_result if f.get("is_real_drift", True)]
    data_loss_only = len(real_drifts) == 0 and len(i6_result) > 0

    velocity_results  = drift_suite.get("velocity", {})
    accelerating_cols = [col for col, vel in velocity_results.items()
                         if vel.get("applicable") and _safe(vel.get("velocity"), 0.0) > 0.05]

    fsi_results  = drift_suite.get("fsi", {})
    unstable_cols = [col for col, fsi in fsi_results.items()
                     if fsi.get("applicable") and _safe(fsi.get("fsi"), 1.0) < 0.40]

    if verdict == "BLOCK":
        decision, urgency = "hold", "pipeline_fix_first"
        reason = "Dataset readiness has dropped below the minimum threshold. Fix data quality issues first."
        steps  = ["Fix upstream data quality issues.", "Re-run comparison after fixes.",
                  "Do not retrain until dataset scores above 45/100."]
        avoid  = "Do not retrain or recalibrate on degraded data."

    elif verdict == "CLEAR":
        decision, urgency = "hold", "none"
        reason = "No significant drift detected. Current model remains valid."
        steps  = ["Continue monitoring. Re-run comparison on next version ingestion."]
        avoid  = None

    elif data_loss_only:
        decision, urgency = "hold", "pipeline_fix_first"
        reason = ("All PSI flags are driven by data loss, not real population change. "
                  "There is no genuine drift to respond to.")
        steps  = ["Fix data pipeline.", "Re-run comparison after fix.",
                  "Only take model action if PSI flags remain after pipeline fix."]
        avoid  = "Do not retrain or recalibrate — the model is not the problem."

    elif i5_type == "data_loss":
        decision, urgency = "hold", "pipeline_fix_first"
        reason = "Target drift is caused by missing labels, not a real event rate change."
        steps  = ["Restore target completeness via pipeline fix.", "Re-evaluate after fix."]
        avoid  = "Do not retrain or recalibrate until labels are complete."

    elif i5_type == "label_change":
        decision, urgency = "retrain", "immediate"
        reason = i5_result.get("model_impact", "Label definition changed.")
        steps  = ["Confirm the label change with the business team.",
                  "Retrain exclusively on data from after the label change.",
                  "Do not use pre-change data.", "Full model validation required."]
        avoid  = "Do not attempt threshold recalibration — the label itself is different."

    elif (i5_type in ("organic_jump",) or
          (target_found and abs(_safe(i5_result.get("total_drift_pp"), 0.0)) > 5.0)):
        decision, urgency = "retrain", "next_cycle"
        drift_pp = abs(_safe(i5_result.get("total_drift_pp"), 0.0))
        reason   = (f"Target event rate shifted {drift_pp:.1f}pp. "
                    + (f"Drift is accelerating ({len(accelerating_cols)} features). "
                       if accelerating_cols else "")
                    + "Population has moved enough that model boundaries need relearning.")
        steps    = ["Back-test current model on latest version data.",
                    "If Gini/KS dropped >5 points, confirm retrain is needed.",
                    f"Refit WoE bins for: {', '.join(c['column'] for c in real_drifts[:5])}." if real_drifts else "Refit preprocessing.",
                    "Retrain on latest version.", "Validate on held-out sample from latest version only."]
        avoid    = "Do not retrain on all historical versions — older data reflects a different population."

    elif real_drifts and all(d["drift_cause"] in ("center_shift",) for d in real_drifts):
        decision, urgency = "rebin", "next_cycle"
        drift_cols = [d["column"] for d in real_drifts]
        reason     = (f"Target drift is minor. {len(drift_cols)} feature(s) have stale WoE bins "
                      f"due to centre shift: {', '.join(drift_cols[:5])}.")
        steps      = [f"Refit WoE bins for: {', '.join(drift_cols[:5])}{'...' if len(drift_cols) > 5 else ''}.",
                      "Back-test existing model after rebinning.",
                      "Recalibrate decision threshold on latest validation set.",
                      "Do not retrain the model."]
        avoid      = "Do not perform a full retrain — drift is in binning parameters only."

    elif real_drifts and any(d["drift_cause"] == "boundary_expansion" for d in real_drifts):
        decision, urgency = "rebin", "next_cycle"
        drift_cols = [d["column"] for d in real_drifts]
        reason     = (f"{len(drift_cols)} feature(s) require rebinning. "
                      f"Boundary expansion in: {[d['column'] for d in real_drifts if d['drift_cause'] == 'boundary_expansion']}.")
        steps      = [f"Rebin WoE/IV bins for: {drift_cols}.",
                      "Recalibrate decision threshold after rebinning.",
                      "Back-test rebinned scorecard on holdout before promotion."]
        avoid      = "Do not retrain the full model — only binning and calibration need updating."

    elif unstable_cols:
        decision, urgency = "rebin", "next_cycle"
        reason = (f"{len(unstable_cols)} feature(s) are chronically unstable (FSI < 0.40): "
                  f"{', '.join(unstable_cols[:5])}.")
        steps  = [f"Consider dropping chronically unstable features: {', '.join(unstable_cols[:5])}.",
                  "Refit preprocessing on remaining features.", "Back-test and recalibrate threshold."]
        avoid  = "Do not retrain while chronically unstable features remain in the feature set."

    else:
        decision, urgency = "recalibrate", "next_cycle"
        total_pp = abs(_safe(i5_result.get("total_drift_pp"), 0.0)) if i5_result else 0.0
        reason   = (f"Drift detected but manageable: target drifted {total_pp:.1f}pp organically. "
                    + (f"{len(real_drifts)} feature(s) with notable PSI. " if real_drifts else "")
                    + "Model rank-ordering likely still valid.")
        steps    = ["Back-test current model on latest version data.",
                    "Recalibrate the decision threshold on the latest validation set.",
                    "If PR-AUC dropped >3 points, escalate to rebin."]
        avoid    = "Do not retrain — drift is not deep enough to justify the effort."

    return {
        "decision": decision, "urgency": urgency, "reason": reason,
        "steps": steps, "avoid": avoid,
        "accelerating_cols": accelerating_cols, "unstable_cols": unstable_cols,
        "real_drift_count": len(real_drifts), "data_loss_only": data_loss_only,
    }


# ─────────────────────────────────────────────────────────────────────────────
# I8 · Scoring pipeline break risks (original)
# ─────────────────────────────────────────────────────────────────────────────

def i8_pipeline_break_risks(c2: List[Dict], c8: Dict,
                              c10: List[Dict], drift_suite: Dict) -> List[Dict]:
    risks        = []
    last_schema  = c2[-1] if c2 else {}

    for col in last_schema.get("dropped", []):
        risks.append({
            "risk": "dropped_feature", "severity": "critical", "column": col["column"],
            "detail": (f"Column '{col['column']}' was dropped in the latest version. "
                       f"If in the current model's feature set, the scoring pipeline will fill it with NaN silently."),
            "fix": (f"Check the deployed model's feature list for '{col['column']}'. "
                    "If present, update the pipeline before scoring against this version."),
        })

    for col in last_schema.get("type_changed", []):
        risks.append({
            "risk": "type_mismatch", "severity": "critical", "column": col["column"],
            "detail": (f"Column '{col['column']}' changed type: {col['from_type']} → {col['to_type']}. "
                       "Preprocessing steps fitted on the old type will produce incorrect outputs silently."),
            "fix": "Refit all preprocessing steps for this column on the new type.",
        })

    for item in c10:
        explosions = item.get("explosions", [])
        if not explosions:
            continue
        last_exp = explosions[-1]
        if last_exp.get("severity") not in ("critical", "notable"):
            continue
        col_name  = item["column"]
        from_card = last_exp.get("from_card")
        to_card   = last_exp.get("to_card")
        ratio     = last_exp.get("ratio")
        psi_label = "stable"
        for col_entry in c8.get("columns", []):
            if col_entry["column"] == col_name:
                psi_label = col_entry.get("worst_label", "stable")
                break
        severity = "critical" if (psi_label in ("shift", "monitor") or
                                   last_exp.get("severity") == "critical") else "notable"
        risks.append({
            "risk": "unseen_category", "severity": severity, "column": col_name,
            "detail": (f"Cardinality grew from {from_card} to {to_card} categories (×{ratio:.1f}x). "
                       f"New category values have no WoE weight or one-hot encoding column from training."),
            "fix": (f"Refit WoE bins or encoder for '{col_name}' on the latest version. "
                    "Add an explicit 'other' bucket to handle future unseen values."),
        })

    ds_consecutive = drift_suite.get("consecutive", {})
    for col_name, pair_metrics in ds_consecutive.items():
        if not pair_metrics:
            continue
        last_pm = pair_metrics[-1]
        if not last_pm:
            continue
        bd = last_pm.get("boundary_drift", {})
        if not bd.get("applicable") or bd.get("severity") != "critical":
            continue
        upper = _safe(bd.get("upper_shift"), 0.0)
        lower = _safe(bd.get("lower_shift"), 0.0)
        parts = []
        if abs(upper) >= 0.25:
            parts.append(f"upper boundary expanded: {bd.get('max_base')} → {bd.get('max_new')}")
        if abs(lower) >= 0.25:
            parts.append(f"lower boundary shifted: {bd.get('min_base')} → {bd.get('min_new')}")
        if not parts:
            continue
        risks.append({
            "risk": "range_extrapolation", "severity": "notable", "column": col_name,
            "detail": (f"Value range expanded significantly: {'; '.join(parts)}. "
                       "Model extrapolates for records outside training range — no error is raised."),
            "fix": (f"Update capping/winsorisation rule for '{col_name}' to cover the new range."),
        })

    risks.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, x["column"]))
    return risks


# ─────────────────────────────────────────────────────────────────────────────
# I9 · Pipeline health (original)
# ─────────────────────────────────────────────────────────────────────────────

def i9_pipeline_health(c3: Dict, c6: List[Dict], c9: Dict) -> Dict:
    version_labels = c3.get("version_labels", [])
    n_versions     = len(version_labels)
    rows           = c3.get("rows", [])
    total_cols     = len(rows)

    if total_cols == 0:
        return {
            "pipeline_health": "unknown", "pattern": "unknown",
            "escalate_to_engineering": False, "affected_columns": [],
            "first_seen_version": None, "health_score_delta": None,
            "likely_cause": "No completeness data available.",
            "action": "Ingest data and re-run comparison.", "version_labels": version_labels,
        }

    degrading_patterns = ("growing_missing", "newly_missing")
    degrading_cols  = [r["column"] for r in rows if r.get("missing_pattern") in degrading_patterns]
    sparse_cols     = [r["column"] for r in rows if r.get("missing_pattern") == "sparse"]
    recovering_cols = [r["column"] for r in rows if r.get("missing_pattern") == "recovering"]

    regressing_quality_cols = [r["column"] for r in c6
                                if r.get("mismatch_trend") == "regressing"
                                or r.get("blank_trend") == "regressing"]

    ds_scores = c9.get("dataset_scores", [])
    health_score_delta = None
    if len(ds_scores) >= 2:
        health_score_delta = round(_safe(ds_scores[-1].get("score"), 0.0) -
                                   _safe(ds_scores[0].get("score"), 0.0), 1)

    degrading_pct = len(degrading_cols) / max(total_cols, 1)

    first_seen = None
    if degrading_cols and n_versions >= 2:
        for row in rows:
            if row["column"] in degrading_cols:
                for i, val in enumerate(row.get("values", [])):
                    if val is not None and val < 100:
                        if first_seen is None and i < len(version_labels):
                            first_seen = version_labels[i]
                        break

    is_systematic = (degrading_pct > 0.20 or
                     (len(degrading_cols) >= 3 and n_versions >= 3) or
                     len(regressing_quality_cols) >= 3)

    likely_cause = "Unknown — investigate data sources for affected columns."
    if degrading_cols:
        prefixes = {}
        for col in degrading_cols:
            parts = col.lower().split("_")
            if len(parts) >= 2:
                prefix = parts[0]
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
        dominant = sorted(prefixes.items(), key=lambda x: -x[1])
        if dominant and dominant[0][1] >= 2:
            likely_cause = (f"Multiple columns sharing '{dominant[0][0]}_*' prefix are degrading — "
                            f"likely a single upstream feed is affected.")

    pipeline_health = "stable"
    pattern         = "stable"
    escalate        = False
    action          = "No action required. Pipeline data quality is stable across versions."

    if is_systematic and (health_score_delta is not None and health_score_delta < -5):
        pipeline_health, pattern, escalate = "degrading", "systematic", True
        action = ("Escalate to data engineering. Multiple columns degrading across versions. "
                  "Do not train until completeness is restored above 95%. " + likely_cause)
    elif len(degrading_cols) > 0 and not is_systematic:
        pipeline_health, pattern = "degrading", "isolated"
        action = (f"Investigate {len(degrading_cols)} affected column(s) directly: "
                  f"{', '.join(degrading_cols[:5])}. Check the specific upstream source.")
    elif len(degrading_cols) == 0 and len(recovering_cols) > 0:
        pipeline_health, pattern = "recovering", "recovering"
        action = (f"{len(recovering_cols)} column(s) are recovering. "
                  "Verify completeness above 90% in the next version before resuming training.")
    elif health_score_delta is not None and health_score_delta > 3:
        pipeline_health, pattern = "improving", "improving"
        action = f"Dataset health improved {health_score_delta:+.1f} points. Continue monitoring."

    return {
        "pipeline_health": pipeline_health, "pattern": pattern,
        "escalate_to_engineering": escalate, "affected_columns": degrading_cols,
        "sparse_columns": sparse_cols, "recovering_columns": recovering_cols,
        "quality_regressing": regressing_quality_cols, "first_seen_version": first_seen,
        "health_score_delta": health_score_delta, "likely_cause": likely_cause,
        "action": action, "version_labels": version_labels,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tier A runner — called from run_analysis() and run_comparison()
# ─────────────────────────────────────────────────────────────────────────────

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
        row_count = int(_safe(s1.get("row_count"), 1000))

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


# ═════════════════════════════════════════════════════════════════════════════
# TIER B — ENHANCED INTERPRETATIONS WITH LLM NARRATIVE PLACEHOLDER
# Separate functions named i4b–i9b. Stored as results["i4b"]–results["i9b"].
# The 'narrative' key is None here; llm_insights.py fills it in.
# ═════════════════════════════════════════════════════════════════════════════

def i4b_population_shift(c1: Dict, c4: List[Dict], c9: Dict, drift_suite: Dict) -> Dict:
    """Enhanced I4 with full N-version trajectory and LLM narrative placeholder."""
    base = i4_population_shift(c1, c4, c9, drift_suite)

    # Add full version-by-version trajectory for LLM prompt
    ds_scores     = c9.get("dataset_scores", [])
    version_labels= c9.get("version_labels", [c1.get("versions", [{}])[i].get("name", f"v{i+1}")
                            for i in range(len(c1.get("versions", [])))])

    # PSI per drifted column per pair
    psi_history = {}
    for col_entry in c9.get("columns", []):
        col_name = col_entry.get("column")
        if col_name in base["drifted_features"]:
            psi_history[col_name] = col_entry.get("scores", [])

    base.update({
        "version_labels":  version_labels,
        "dataset_scores":  ds_scores,
        "trend_note":      c9.get("trend_note", ""),
        "trend_direction": c9.get("trend_direction", ""),
        "psi_history":     psi_history,
        "narrative":       None,
    })
    return base


def i5b_target_stability(c5: Optional[Dict], c3: Dict, c6: List[Dict]) -> Dict:
    """Enhanced I5 with full event-rate series and LLM narrative placeholder."""
    base = i5_target_stability(c5, c3, c6)
    if not c5:
        base["narrative"] = None
        return base

    # Full event-rate trajectory for LLM prompt
    version_rates = c5.get("version_rates", [])
    rate_series   = [(vr.get("abt", ""), vr.get("event_rate")) for vr in version_rates]

    base.update({
        "rate_series":    rate_series,
        "version_labels": [vr.get("abt", "") for vr in version_rates],
        "pairwise_drift": c5.get("pairwise_drift", []),
        "narrative":      None,
    })
    return base


def i6b_feature_drift_impact(c3: Dict, c4: List[Dict], c8: Dict,
                               drift_suite: Dict) -> List[Dict]:
    """Enhanced I6: each item gets a full PSI history series and narrative placeholder."""
    base_list = i6_feature_drift_impact(c3, c4, c8, drift_suite)

    # Attach full PSI pair history to each item
    col_to_pairs = {col_entry["column"]: col_entry.get("pairs", [])
                    for col_entry in c8.get("columns", [])}

    enhanced = []
    for item in base_list:
        col_name  = item["column"]
        pairs     = col_to_pairs.get(col_name, [])
        psi_series= [{"from": p.get("from", ""), "to": p.get("to", ""),
                      "psi": p.get("psi"), "label": p.get("label", "")}
                     for p in pairs if p.get("applicable")]
        new_item  = dict(item)
        new_item["psi_series"]  = psi_series
        new_item["narrative"]   = None
        enhanced.append(new_item)

    return enhanced


def i7b_model_action(c0: Dict, i5_result: Dict, i6_result: List[Dict],
                      drift_suite: Dict, c9: Dict) -> Dict:
    """Enhanced I7 with full dataset score history and LLM narrative placeholder."""
    base = i7_model_action(c0, i5_result, i6_result, drift_suite)

    ds_scores     = c9.get("dataset_scores", [])
    version_labels= [d.get("abt", "") for d in ds_scores]

    base.update({
        "version_labels":    version_labels,
        "dataset_scores":    ds_scores,
        "i5_drift_type":     i5_result.get("drift_type", "stable") if i5_result else "stable",
        "i5_total_drift_pp": i5_result.get("total_drift_pp", 0.0) if i5_result else 0.0,
        "genuine_count":     base.get("real_drift_count", 0),
        "narrative":         None,
    })
    return base


def i8b_pipeline_break_risks(c2: List[Dict], c8: Dict,
                               c10: List[Dict], drift_suite: Dict) -> List[Dict]:
    """
    Enhanced I8: same as original (no LLM enrichment by design).
    Returned as a plain list — no narrative key added.
    Hard facts only.
    """
    return i8_pipeline_break_risks(c2, c8, c10, drift_suite)


def i9b_pipeline_health(c3: Dict, c6: List[Dict], c9: Dict) -> Dict:
    """Enhanced I9 with full health score trajectory and LLM narrative placeholder."""
    base = i9_pipeline_health(c3, c6, c9)

    ds_scores = c9.get("dataset_scores", [])
    base.update({
        "dataset_scores": ds_scores,
        "narrative":      None,
    })
    return base


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

    # ✅ ✅ USE HYBRID OUTPUTS FIRST
    i4_base = results.get("i4c") or results.get("i4")
    i5_base = results.get("i5c") or results.get("i5")
    i6_base = results.get("i6c") or results.get("i6")

    # ─────────────────────────────────────────────────────────
    # I4b (population shift) — inject refined i4c signals
    # ─────────────────────────────────────────────────────────
    results["i4b"] = i4b_population_shift(c1, c4, c9, ds)

    if i4_base:
        results["i4b"].update({
            "refined_cause":        i4_base.get("refined_cause"),
            "sampling_signal":      i4_base.get("sampling_signal"),
            "coordination_signal": i4_base.get("coordination_signal"),
            "distance_signal":      i4_base.get("distance_signal"),
        })

    # ─────────────────────────────────────────────────────────
    # I5b (target stability) — merge i5c classification
    # ─────────────────────────────────────────────────────────
    results["i5b"] = i5b_target_stability(c5, c3, c6)

    if i5_base:
        results["i5b"].update({
            "refined_drift_type": i5_base.get("refined_drift_type")
        })

    # ─────────────────────────────────────────────────────────
    # I6b (feature drift) — inject mechanisms from i6c
    # ─────────────────────────────────────────────────────────
    results["i6b"] = i6b_feature_drift_impact(c3, c4, c8, ds)

    i6c_map = {f["column"]: f for f in (i6_base or [])}

    for item in results["i6b"]:
        col = item["column"]
        if col in i6c_map:
            item.update({
                "drift_mechanism": i6c_map[col].get("drift_mechanism"),
                "signal_summary":  i6c_map[col].get("signal_summary")
            })

    # ─────────────────────────────────────────────────────────
    # I7b (model action) — already using hybrid ✅
    # ─────────────────────────────────────────────────────────
    results["i7b"] = i7b_model_action(c0, i5_base, i6_base, ds, c9)

    # ─────────────────────────────────────────────────────────
    # I8b (unchanged)
    # ─────────────────────────────────────────────────────────
    results["i8b"] = i8b_pipeline_break_risks(c2, c8, c10, ds)

    # ─────────────────────────────────────────────────────────
    # I9b (unchanged structure, can include hybrid if needed)
    # ─────────────────────────────────────────────────────────
    results["i9b"] = i9b_pipeline_health(c3, c6, c9)

    return results





# ─────────────────────────────────────────────────────────────────────────────
# I4c · Population shift (hybrid — stronger cause detection)
# ─────────────────────────────────────────────────────────────────────────────

def i4c_population_shift(i4: Dict, c1: Dict, c4: List[Dict], drift_suite: Dict) -> Dict:
    base = dict(i4)

    total_features = max(i4.get("total_features", 1), 1)
    drifted_count  = i4.get("drifted_count", 0)
    drifted_pct    = drifted_count / total_features

    # ✅ Row-based signal
    row_delta_pct = i4.get("row_delta_pct")
    sampling_flag = row_delta_pct is not None and abs(row_delta_pct) > 20

    # ✅ Coordination signal (already computed, reuse)
    coordinated = i4.get("coordinated", False)

    # ✅ Baseline distance (reuse stronger weighting)
    v1_distance = i4.get("v1_distance", "unknown")

    # ✅ Refined cause logic
    if drifted_pct > 0.3 and sampling_flag:
        refined_cause = "sampling_shift_confirmed"
    elif coordinated and drifted_pct > 0.3:
        refined_cause = "true_population_shift"
    elif drifted_pct < 0.1:
        refined_cause = "noise_or_local"
    else:
        refined_cause = "mixed_shift"

    base.update({
        "refined_cause": refined_cause,
        "sampling_signal": sampling_flag,
        "coordination_signal": coordinated,
        "distance_signal": v1_distance
    })

    return base


# ─────────────────────────────────────────────────────────────────────────────
# I5c · Target stability (hybrid — robust classification)
# ─────────────────────────────────────────────────────────────────────────────

def i5c_target_stability(i5: Dict) -> Dict:
    base = dict(i5)

    drift_type = i5.get("drift_type")
    max_jump   = _safe(i5.get("max_single_jump_pp"), 0.0)
    total_pp   = _safe(i5.get("total_drift_pp"), 0.0)

    # ✅ Stronger override logic
    if i5.get("data_loss_risk"):
        refined = "data_loss_confirmed"
    elif max_jump > 5:
        refined = "label_definition_change"
    elif abs(total_pp) > 5:
        refined = "real_population_shift"
    else:
        refined = drift_type

    base["refined_drift_type"] = refined
    return base

# ─────────────────────────────────────────────────────────────────────────────
# I6c · Feature drift impact (hybrid — multi-signal reasoning)
# ─────────────────────────────────────────────────────────────────────────────

def i6c_feature_drift_impact(i6: List[Dict], c4: List[Dict],
                            drift_suite: Dict) -> List[Dict]:

    c4_map = {c["column"]: c for c in c4}
    ds_consecutive = drift_suite.get("consecutive", {})

    enhanced = []

    for item in i6:
        col = item["column"]
        base = dict(item)

        if not item.get("is_real_drift", True):
            base["drift_mechanism"] = "data_loss"
            enhanced.append(base)
            continue

        # ✅ pull metrics
        pair_metrics = ds_consecutive.get(col, [])
        latest = pair_metrics[-1] if pair_metrics else {}

        qs  = latest.get("quantile_shift", {}).get("shifts", {})
        bd  = latest.get("boundary_drift", {})
        std = latest.get("std_drift", {})

        median_shift = abs(_safe(qs.get("Median"), 0.0))
        upper_shift  = abs(_safe(bd.get("upper_shift"), 0.0))
        std_change   = _safe(std.get("norm_change"), 0.0)

        c4_flags = c4_map.get(col, {}).get("drift_flags", [])
        mean_delta = _safe(c4_flags[-1].get("mean_delta"), 0.0) if c4_flags else 0.0

        # ✅ hybrid classification
        signals = []

        if median_shift > 0.2 or abs(mean_delta) > 0.1:
            signals.append("center_shift")
        if upper_shift > 0.1:
            signals.append("boundary_expansion")
        if std_change > 0.25:
            signals.append("spread_change")

        mechanism = "+".join(signals) if signals else "weak_shift"

        base["drift_mechanism"] = mechanism
        base["signal_summary"] = {
            "median_shift": median_shift,
            "mean_delta": mean_delta,
            "boundary_shift": upper_shift,
            "std_change": std_change
        }

        enhanced.append(base)

    return enhanced


# ─────────────────────────────────────────────────────────────────────────────
# I7c · Model action (hybrid — decision intelligence)
# ─────────────────────────────────────────────────────────────────────────────

def i7c_model_action(i7: Dict, i5c: Dict, i6c: List[Dict],
                    drift_suite: Dict) -> Dict:

    base = dict(i7)

    decision = i7.get("decision")

    # ✅ overrides
    if i5c.get("refined_drift_type") == "label_definition_change":
        decision = "retrain"

    if all(f.get("drift_mechanism") == "data_loss" for f in i6c):
        decision = "hold"

    # ✅ velocity escalation
    velocity = drift_suite.get("velocity", {})
    accel = any(_safe(v.get("velocity"), 0.0) > 0.05 for v in velocity.values() if v.get("applicable"))

    if decision == "recalibrate" and accel:
        decision = "rebin"

    # ✅ FSI adjustment
    fsi = drift_suite.get("fsi", {})
    unstable = [k for k, v in fsi.items()
                if v.get("applicable") and _safe(v.get("fsi"), 1.0) < 0.4]

    if unstable and decision == "retrain":
        decision = "rebin"

    base.update({
        "refined_decision": decision,
        "velocity_flag": accel,
        "fsi_unstable_cols": unstable
    })

    return base


# ─────────────────────────────────────────────────────────────────────────────
# I8c · Pipeline risks (hybrid — prioritisation layer)
# ─────────────────────────────────────────────────────────────────────────────

def i8c_pipeline_break_risks(i8: List[Dict]) -> List[Dict]:
    enhanced = []

    for r in i8:
        base = dict(r)

        # ✅ elevate severity if both schema + drift
        if r["risk"] in ("type_mismatch", "dropped_feature"):
            base["priority"] = "immediate_fix"
        elif r["severity"] == "critical":
            base["priority"] = "urgent"
        else:
            base["priority"] = "monitor"

        enhanced.append(base)

    return enhanced

    # ─────────────────────────────────────────────────────────────────────────────
# I9c · Pipeline health (hybrid — system detection)
# ─────────────────────────────────────────────────────────────────────────────

def i9c_pipeline_health(i9: Dict) -> Dict:
    base = dict(i9)

    degrading_cols = i9.get("affected_columns", [])
    total_cols     = max(len(degrading_cols), 1)

    systematic = len(degrading_cols) > 0.2 * total_cols

    if systematic:
        severity = "systemic_failure"
    elif degrading_cols:
        severity = "localized_failure"
    else:
        severity = "healthy"

    base["failure_mode"] = severity
    return base


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