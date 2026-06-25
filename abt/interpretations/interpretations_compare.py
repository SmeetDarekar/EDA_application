"""
abt/interpretations_compare.py
─────────────────────────────────────────────────────────────────────────────
Comparison-level drift interpretations (Tier A, Tier B, Tier C).
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any


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

        risks.append({
            "risk": "cardinality_explosion", "severity": last_exp.get("severity"), "column": col_name,
            "detail": (f"Cardinality exploded {ratio:.1f}x: {from_card} → {to_card} unique values. "
                       "One-hot encoding this version will break downstream matrix shapes or inflate memory usage 10x."),
            "fix": (f"Apply target encoding, binning, or group rare levels for '{col_name}' "
                    "before scoring or retraining."),
        })

    consecutive = drift_suite.get("consecutive", {})
    for col_name, pair_metrics in consecutive.items():
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


# ═════════════════════════════════════════════════════════════════════════════
# TIER B — ENHANCED INTERPRETATIONS WITH LLM NARRATIVE PLACEHOLDER
# ═════════════════════════════════════════════════════════════════════════════

def i4b_population_shift(c1: Dict, c4: List[Dict], c9: Dict, drift_suite: Dict) -> Dict:
    """Enhanced I4 with full N-version trajectory and LLM narrative placeholder."""
    base = i4_population_shift(c1, c4, c9, drift_suite)

    ds_scores     = c9.get("dataset_scores", [])
    version_labels= c9.get("version_labels", [c1.get("versions", [{}])[i].get("name", f"v{i+1}")
                            for i in range(len(c1.get("versions", [])))])

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


# ═════════════════════════════════════════════════════════════════════════════
# TIER C — HYBRID INTERPRETATIONS
# ═════════════════════════════════════════════════════════════════════════════

def i4c_population_shift(i4: Dict, c1: Dict, c4: List[Dict], drift_suite: Dict) -> Dict:
    base = dict(i4)

    total_features = max(i4.get("total_features", 1), 1)
    drifted_count  = i4.get("drifted_count", 0)
    drifted_pct    = drifted_count / total_features

    row_delta_pct = i4.get("row_delta_pct")
    sampling_flag = row_delta_pct is not None and abs(row_delta_pct) > 20

    coordinated = i4.get("coordinated", False)
    v1_distance = i4.get("v1_distance", "unknown")

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


def i5c_target_stability(i5: Dict) -> Dict:
    base = dict(i5)

    drift_type = i5.get("drift_type")
    max_jump   = _safe(i5.get("max_single_jump_pp"), 0.0)
    total_pp   = _safe(i5.get("total_drift_pp"), 0.0)

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


def i7c_model_action(i7: Dict, i5c: Dict, i6c: List[Dict],
                    drift_suite: Dict) -> Dict:
    base = dict(i7)
    decision = i7.get("decision")

    if i5c.get("refined_drift_type") == "label_definition_change":
        decision = "retrain"

    if all(f.get("drift_mechanism") == "data_loss" for f in i6c):
        decision = "hold"

    velocity = drift_suite.get("velocity", {})
    accel = any(_safe(v.get("velocity"), 0.0) > 0.05 for v in velocity.values() if v.get("applicable"))

    if decision == "recalibrate" and accel:
        decision = "rebin"

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


def i8c_pipeline_break_risks(i8: List[Dict]) -> List[Dict]:
    enhanced = []
    for r in i8:
        base = dict(r)
        if r["risk"] in ("type_mismatch", "dropped_feature"):
            base["priority"] = "immediate_fix"
        elif r["severity"] == "critical":
            base["priority"] = "urgent"
        else:
            base["priority"] = "monitor"
        enhanced.append(base)
    return enhanced


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
