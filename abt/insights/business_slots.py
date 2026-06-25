"""
abt/business_slots.py
─────────────────────────────────────────────────────────────────────────────
Structured card slots: Target, Data Pipeline, Model Risk, Governance.
"""

from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 4: TARGET BEHAVIOR  (structured)
# ─────────────────────────────────────────────────────────────────────────────

def _insight_target(results: dict, stage: str) -> dict:
    from abt.insights.business_insights import _safe, _CTX, _ACT

    i5         = results.get("i5", {})
    c5         = results.get("c5") or {}
    stage_ctx  = _CTX.get(stage, "In the current data")
    action_pfx = _ACT.get(stage, "Action required")

    if not i5.get("target_found", False):
        return {
            "slot":              "target_behavior",
            "title":             "Target / Outcome Behavior",
            "headline":          "No target column detected in this dataset.",
            "severity":          "stable",
            "evidence":          [{"label": "Status",
                                   "detail": "Specify target_col to enable outcome monitoring."}],
            "impact_and_action": "Cannot assess training readiness without a target column.",
            "llm_narrative":     "",
        }

    drift_type  = i5.get("drift_type", "stable")
    er_first    = i5.get("event_rate_first")
    er_last     = i5.get("event_rate_last")
    total_drift = _safe(i5.get("total_drift_pp"), 0.0)
    max_jump    = _safe(i5.get("max_single_jump_pp"), 0.0)
    label_risk  = i5.get("label_change_risk", False)
    data_loss   = i5.get("data_loss_risk", False)
    model_impact = i5.get("model_impact", "")

    sev_map = {
        "stable":          "stable",
        "organic_gradual": "warning",
        "organic_jump":    "critical",
        "label_change":    "critical",
        "data_loss":       "warning",
    }
    severity = sev_map.get(drift_type, "stable")  # RAG only

    headlines = {
        "stable": (
            f"{stage_ctx}, the outcome rate is stable at {er_last}%. "
            f"Model calibration should hold."
        ),
        "data_loss": (
            f"{stage_ctx}, the apparent outcome rate shift ({er_first}% → {er_last}%) "
            f"is driven by missing labels in the pipeline — not real behavioral change."
        ),
        "label_change": (
            f"{stage_ctx}, the outcome rate jumped {max_jump:.1f}pp in a single version "
            f"({er_first}% → {er_last}%). This is consistent with a label definition or "
            f"coding change — the model is now being asked to predict a different outcome."
        ),
        "organic_gradual": (
            f"{stage_ctx}, the outcome rate is gradually shifting "
            f"({er_first}% → {er_last}%, {total_drift:+.1f}pp total). "
            f"Rank-ordering is likely still valid but the decision threshold needs recalibration."
        ),
        "organic_jump": (
            f"{stage_ctx}, the outcome rate has shifted sharply "
            f"({er_first}% → {er_last}%, {total_drift:+.1f}pp). "
            f"A rapid change in customer risk profile has occurred — "
            f"possible external event, policy change, or product mix shift."
        ),
    }
    headline = headlines.get(drift_type,
                             f"{stage_ctx}, target drift type detected: {drift_type}.")

    evidence = []
    if er_first is not None and er_last is not None:
        evidence.append({
            "label":  "Event rate change",
            "detail": f"Baseline: {er_first}% → Current: {er_last}% ({total_drift:+.1f}pp total).",
        })
    if max_jump > 0:
        evidence.append({
            "label":  "Largest single-version jump",
            "detail": f"{max_jump:.1f}pp — "
                      + ("pattern consistent with label coding change."
                         if label_risk else "pattern consistent with external event."),
        })
    for pw in (c5.get("pairwise_drift") or []):
        if pw.get("severity") in ("notable", "critical"):
            evidence.append({
                "label":  f"Pairwise drift: {pw.get('from_ver')} → {pw.get('to_ver')}",
                "detail": f"{pw.get('delta_pp', 0):+.2f}pp ({pw.get('severity')})"
                          + (" — back-test required." if pw.get("back_test_required") else "."),
            })
    if data_loss:
        evidence.append({
            "label":  "Data loss flag",
            "detail": "Target completeness is degrading — apparent drift is from missing labels.",
        })
    if model_impact:
        evidence.append({
            "label":  "Model impact",
            "detail": model_impact,
        })
    if not evidence:
        evidence.append({"label": "Target stability",
                         "detail": f"Event rate at {er_last}% — within normal range."})

    action = i5.get("action", "Monitor target stability in next cycle.")
    impact_and_action = f"{action_pfx}: {action}" if action_pfx not in action else action

    return {
        "slot":              "target_behavior",
        "title":             "Target / Outcome Behavior",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 5: PIPELINE QUALITY  (structured)
# ─────────────────────────────────────────────────────────────────────────────

def _insight_pipeline(results: dict, signals: List[dict], stage: str) -> dict:
    from abt.insights.business_insights import _analyse_root_cause, _safe, _CTX, _ACT

    i9         = results.get("i9", {})
    stage_ctx  = _CTX.get(stage, "In the current data")
    action_pfx = _ACT.get(stage, "Action required")

    health       = i9.get("pipeline_health", "stable")
    pattern      = i9.get("pattern", "stable")
    escalate     = i9.get("escalate_to_engineering", False)
    affected     = i9.get("affected_columns", [])
    recovering   = i9.get("recovering_columns", [])
    quality_reg  = i9.get("quality_regressing", [])
    score_delta  = i9.get("health_score_delta")
    likely_cause = i9.get("likely_cause", "")

    pipe_sigs = [s for s in signals if s.get("slot_hint") == "pipeline"]

    sev_map = {
        "stable":    "stable",
        "recovering":"stable",
        "improving": "stable",
        "degrading": "critical" if pattern == "systematic" else "warning",
    }
    severity = sev_map.get(health, "stable")  # RAG only

    if health == "stable" and not quality_reg:
        headline = (
            f"{stage_ctx}, the data pipeline is healthy. "
            f"Completeness and quality are consistent with the baseline."
        )
    elif health == "recovering":
        headline = (
            f"{stage_ctx}, {len(recovering)} column(s) that had completeness "
            f"issues are now recovering. Pipeline fixes appear to be taking effect."
        )
    elif pattern == "systematic":
        headline = (
            f"{stage_ctx}, systematic data quality degradation across "
            f"{len(affected)} column(s). "
            + (f"Columns appear to share a common upstream source. "
               if "prefix" in likely_cause.lower() else "")
            + ("Engineering escalation required." if escalate else
               "Investigate the upstream data source.")
        )
    elif pattern == "isolated":
        col_list = ", ".join(affected[:3]) + ("…" if len(affected) > 3 else "")
        headline = (
            f"{stage_ctx}, {len(affected)} column(s) have isolated completeness "
            f"issues: {col_list}. No broad pipeline failure — "
            f"investigate specific sources."
        )
    else:
        headline = (
            f"{stage_ctx}, minor data quality signals detected. "
            f"Overall pipeline health is acceptable."
        )

    evidence = []
    if score_delta is not None:
        evidence.append({
            "label":  "Dataset health score change",
            "detail": f"{score_delta:+.1f} points from baseline to current version.",
        })
    # Use RCA on pipeline signals for specific evidence
    for sig in pipe_sigs[:4]:
        rca = _analyse_root_cause(sig, results)
        evidence.append({
            "label":  f"Completeness — {sig['column']}",
            "detail": rca.get("cause_summary", ""),
        })
    if quality_reg:
        evidence.append({
            "label":  "Format / encoding regressions",
            "detail": f"{', '.join(quality_reg[:5])} — mismatch or blank counts increasing.",
        })
    if not evidence:
        evidence.append({"label": "Pipeline status",
                         "detail": "All completeness and quality checks passed."})

    action = i9.get("action", "Continue monitoring.")
    impact_and_action = (
        f"{action_pfx}: escalate to data engineering. " + action
        if escalate else action
    )

    return {
        "slot":              "pipeline_quality",
        "title":             "Data Pipeline Quality",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 6: MODEL SCORING RISK  (structured)
# ─────────────────────────────────────────────────────────────────────────────

def _insight_model_risk(results: dict, signals: List[dict], stage: str) -> dict:
    from abt.insights.business_insights import _analyse_root_cause, _CTX, _ACT, _g

    i6         = results.get("i6", [])
    i7         = results.get("i7", {})
    stage_ctx  = _CTX.get(stage, "In the current data")
    action_pfx = _ACT.get(stage, "Action required")

    decision       = i7.get("decision", "hold")
    real_drifts    = [f for f in i6 if f.get("is_real_drift", True)]
    data_loss_only = i7.get("data_loss_only", False)
    accelerating   = i7.get("accelerating_cols", [])
    unstable       = i7.get("unstable_cols", [])

    dec_sev = {
        "retrain":     "critical",
        "rebin":       "warning",
        "recalibrate": "warning",
        "hold":        "stable",
    }
    severity = dec_sev.get(decision, "stable")  # RAG only
    if data_loss_only:
        severity = "warning"

    if data_loss_only:
        headline = (
            f"{stage_ctx}, distribution flags are present but driven by pipeline "
            f"data loss — not a real population change. The model is not at risk."
        )
    elif decision == "hold":
        headline = (
            f"{stage_ctx}, the existing model is expected to perform consistently. "
            f"No significant scoring risk detected."
        )
    elif decision == "recalibrate":
        headline = (
            f"{stage_ctx}, the model's rank-ordering is likely still valid but "
            f"the decision threshold is misaligned with the current outcome rate. "
            f"Recalibration is needed before use."
        )
    elif decision == "rebin":
        cols = ", ".join(f["column"] for f in real_drifts[:3])
        headline = (
            f"{stage_ctx}, {len(real_drifts)} feature(s) have stale WoE bin "
            f"boundaries due to distribution shift: {cols}. "
            f"Affected records are receiving wrong risk buckets."
        )
    else:  # retrain
        headline = (
            f"{stage_ctx}, the population has shifted enough that the model's "
            f"learned boundaries no longer apply. {len(real_drifts)} feature(s) "
            f"show genuine distribution change. Retraining is required."
        )

    # Pull top drift column names — already shown in drift stories, don't repeat
    signals_list = results.get("signals", [])
    drift_story_cols = {
        s["column"] for s in signals_list
        if s.get("slot_hint") == "population"
    }

    evidence = []
    # I8 pipeline break risks — these are NOT in drift stories (schema events)
    i8 = results.get("i8", [])
    for risk in i8[:4]:
        evidence.append({
            "label":  f"Scoring break risk — {risk.get('risk','unknown').replace('_',' ')}",
            "detail": risk.get("detail", ""),
        })
    # I6 real drifts NOT already shown in drift stories
    for item in real_drifts:
        if item["column"] not in drift_story_cols:
            evidence.append({
                "label":  f"Additional drift — {item['column']}",
                "detail": (
                    f"Cause: {item.get('drift_cause','').replace('_',' ')}. "
                    + item.get("model_impact", "")
                ).strip(),
            })
    if accelerating:
        evidence.append({
            "label":  "Drift velocity — accelerating features",
            "detail": f"{', '.join(accelerating[:5])} — PSI increasing > 0.05/version. "
                      f"These features are moving fast — model degradation will accelerate.",
        })
    if unstable:
        evidence.append({
            "label":  "Chronically unstable features (FSI < 0.40)",
            "detail": f"{', '.join(unstable[:5])} — drifting consistently across all "
                      f"version pairs. Consider dropping from the feature set.",
        })
    if not evidence:
        evidence.append({"label": "Model risk status",
                         "detail": "No scoring pipeline risks detected. Model is stable."})

    steps = i7.get("steps", [])
    avoid = i7.get("avoid", "")
    steps_str = " ".join(f"({i+1}) {s}" for i, s in enumerate(steps)) if steps else ""
    impact_and_action = (
        f"{action_pfx}: {i7.get('reason', '')} "
        + (f"Steps: {steps_str} " if steps_str else "")
        + (f"Do not: {avoid}" if avoid else "")
    ).strip()

    return {
        "slot":              "model_scoring_risk",
        "title":             "Model Scoring Risk",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative":     _g(results, "c8", "narrative", default=""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT 7: GOVERNANCE & FAIRNESS  (structured, privacy-flag only)
# ─────────────────────────────────────────────────────────────────────────────

def _insight_governance(results: dict, signals: List[dict], stage: str) -> dict:
    from abt.insights.business_insights import _analyse_root_cause, _CTX, _ACT

    stage_ctx  = _CTX.get(stage, "In the current data")
    action_pfx = _ACT.get(stage, "Action required")

    # ONLY private-flag signals — no name pattern matching
    gov_sigs = [s for s in signals if s.get("slot_hint") == "governance"]

    drifted_private  = [s for s in gov_sigs
                        if s["metric_type"] in ("psi", "mean_shift", "quantile_shift",
                                                 "boundary", "std", "kurtosis", "entropy")]
    schema_private   = [s for s in gov_sigs if s["metric_type"] == "schema"]
    cardinality_priv = [s for s in gov_sigs if s["metric_type"] == "cardinality"]

    if not gov_sigs:
        return {
            "slot":              "governance_fairness",
            "title":             "Governance & Fairness",
            "headline":          (
                f"{stage_ctx}, no governance signals detected. "
                f"No private-attribute drift or schema changes to protected columns found."
            ),
            "severity":          "stable",   # RAG: green = clear
            "evidence":          [{"label": "Governance status",
                                   "detail": "All governance checks passed."}],
            "impact_and_action": "No governance action required. Continue standard monitoring.",
            "llm_narrative":     "",
        }

    severity = "critical" if drifted_private else "warning"
    headline = (
        f"{stage_ctx}, {len(gov_sigs)} governance checkpoint(s) require attention. "
        + (f"{len(drifted_private)} private attribute(s) have shifted distribution — "
           f"this may produce disparate model outcomes across groups. "
           if drifted_private else "")
        + (f"{len(cardinality_priv)} private attribute(s) have new category values. "
           if cardinality_priv else "")
        + (f"{len(schema_private)} schema change(s) affect private attributes. "
           if schema_private else "")
        + "Governance sign-off required before model promotion."
    )

    evidence = []
    for sig in gov_sigs:
        rca = _analyse_root_cause(sig, results)
        evidence.append({
            "label":  f"Private attribute — {sig['column']} ({sig['metric_type']})",
            "detail": rca.get("cause_summary", ""),
        })
        evidence.append({
            "label":  "Fairness risk",
            "detail": rca.get("model_impact", ""),
        })

    impact_and_action = (
        f"{action_pfx}: obtain governance sign-off for all flagged private attributes. "
        f"For attributes with distribution shift, run a fairness evaluation comparing "
        f"outcome rates across affected groups before deployment. "
        f"Document all changes for regulatory audit trail (GDPR, FCRA, ECOA)."
    )

    return {
        "slot":              "governance_fairness",
        "title":             "Governance & Fairness",
        "headline":          headline,
        "severity":          severity,
        "evidence":          evidence,
        "impact_and_action": impact_and_action,
        "llm_narrative"    : "",
    }
