from __future__ import annotations
from typing import Dict, List

DOMAIN_LABELS = {
    "credit_risk":     "credit risk",
    "fraud":           "fraud detection",
    "churn":           "customer churn",
    "marketing":       "marketing propensity",
    "insurance":       "insurance underwriting",
    "collections":     "collections scoring",
    "operational_risk":"operational risk",
    "other":           "risk modeling",
}

PURPOSE_LABELS = {
    "pd":   "Probability of Default (PD)",
    "lgd":  "Loss Given Default (LGD)",
    "ead":  "Exposure at Default (EAD)",
    "fraud":"Fraud detection",
    "churn":"Churn prediction",
    "propensity": "Propensity scoring",
    "segmentation": "Customer segmentation",
    "other": "predictive model",
}

# Severity rank for sorting (higher = more urgent)
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def collect_all_signals(results: Dict) -> List[Dict]:
    """
    Harvest all rule-based signals from i4–i9, c0, c8 into a flat list.

    Each signal has:
      source      : which interpretation produced it
      domain      : population | target | feature | pipeline | governance
      severity    : critical | high | medium | low
      headline    : short description (rule-generated, not LLM)
      evidence    : specific numbers, column names
      cause       : why this is happening (rule-inferred)
      model_impact: what breaks in the model if ignored
      action      : rule-generated recommended step
      psi         : float or None (for PSI-based signals, enables ranking)
    """
    signals: List[Dict] = []

    # ── C0: Overall verdict issues ────────────────────────────────────────
    c0 = results.get("c0", {})
    verdict = c0.get("verdict", "CLEAR")
    if verdict in ("BACK_TEST_REQUIRED", "BLOCK"):
        for issue in c0.get("issues", []):
            signals.append(_signal(
                source="c0_verdict",
                domain="population",
                severity="critical" if verdict == "BLOCK" else "high",
                headline=f"Overall verdict: {verdict.replace('_', ' ')}",
                evidence=issue,
                cause="multiple_signals",
                model_impact=(
                    "Model cannot be promoted to production without back-testing."
                    if verdict == "BACK_TEST_REQUIRED"
                    else "Dataset quality is below minimum threshold — do not train."
                ),
                action=(
                    "Run back-test on latest version data before any promotion decision."
                    if verdict == "BACK_TEST_REQUIRED"
                    else "Resolve dataset quality blockers before any model action."
                ),
                psi=None,
            ))

    # ── I7: Model action decision ─────────────────────────────────────────
    i7 = results.get("i7", {})
    if i7:
        decision = i7.get("decision", "hold")
        urgency  = i7.get("urgency", "none")
        if decision != "hold" or urgency == "pipeline_fix_first":
            sev = "critical" if urgency == "immediate" else (
                  "high"     if urgency == "next_cycle" else
                  "medium"   if urgency == "pipeline_fix_first" else "low")
            signals.append(_signal(
                source="i7_model_action",
                domain="population",
                severity=sev,
                headline=f"Model action required: {decision.replace('_', ' ')}",
                evidence=i7.get("reason", ""),
                cause="drift_or_label_change",
                model_impact=(
                    "Deployed model will produce stale scores on the new population."
                    if decision in ("retrain", "rebin")
                    else "Decision threshold is misaligned — minority recall will degrade."
                ),
                action=i7.get("steps", ["Review model and back-test on latest data."])[0],
                psi=None,
            ))
        # Accelerating features — always high priority
        for col in i7.get("accelerating_cols", []):
            signals.append(_signal(
                source="i7_drift_velocity",
                domain="feature",
                severity="high",
                headline=f"Drift accelerating in '{col}'",
                evidence=f"PSI velocity >0.05/version — distribution moving faster each cycle.",
                cause="accelerating_drift",
                model_impact="WoE bins will become stale faster than the validation cycle can catch.",
                action=f"Refit WoE bins for '{col}' before next model cycle.",
                psi=None,
            ))
        # Chronically unstable features
        for col in i7.get("unstable_cols", []):
            signals.append(_signal(
                source="i7_fsi_unstable",
                domain="feature",
                severity="medium",
                headline=f"'{col}' chronically unstable (FSI < 0.40)",
                evidence=f"Feature has been drifting consistently across all version pairs.",
                cause="chronic_instability",
                model_impact="Including this feature adds variance to every retrain without improving rank-ordering.",
                action=f"Consider dropping '{col}' from the feature set entirely.",
                psi=None,
            ))

    # ── I5: Target stability ──────────────────────────────────────────────
    i5 = results.get("i5", {})
    if i5 and i5.get("target_found"):
        drift_type = i5.get("drift_type", "stable")
        if drift_type != "stable":
            drift_pp = abs(i5.get("total_drift_pp") or 0.0)
            jump_pp  = i5.get("max_single_jump_pp") or 0.0
            sev = ("critical" if drift_type == "label_change" else
                   "high"     if drift_type in ("organic_jump", "data_loss") else
                   "medium")
            signals.append(_signal(
                source="i5_target_stability",
                domain="target",
                severity=sev,
                headline=f"Target event rate drifted {drift_pp:.1f}pp — {drift_type.replace('_', ' ')}",
                evidence=(
                    f"Event rate moved from {i5.get('event_rate_first')}% to "
                    f"{i5.get('event_rate_last')}% (total {drift_pp:+.1f}pp). "
                    + (f"Largest single-version jump: {jump_pp:.1f}pp. " if jump_pp > 0 else "")
                    + i5.get("model_impact", "")
                ),
                cause=drift_type,
                model_impact=i5.get("model_impact", ""),
                action=i5.get("action", ""),
                psi=None,
            ))

    # ── I4: Population shift ──────────────────────────────────────────────
    i4 = results.get("i4", {})
    if i4 and i4.get("shift_scope") in ("broad", "narrow"):
        n_drifted = i4.get("drifted_count", 0)
        total_f   = i4.get("total_features", 1)
        v1_dist   = i4.get("v1_distance", "unknown")
        sev = "critical" if i4.get("shift_scope") == "broad" and v1_dist == "far" else (
              "high"     if i4.get("shift_scope") == "broad" else "medium")
        signals.append(_signal(
            source="i4_population_shift",
            domain="population",
            severity=sev,
            headline=f"{n_drifted}/{total_f} features drifted — {i4.get('likely_cause', 'unknown').replace('_', ' ')}",
            evidence=(
                f"{n_drifted} of {total_f} numeric features show notable or critical drift. "
                f"Distance from V1 training baseline: {v1_dist}. "
                + (f"Row count delta: {i4.get('row_delta_pct'):+.1f}%. " if i4.get('row_delta_pct') is not None else "")
                + ("Features are moving in a coordinated direction. " if i4.get("coordinated") else "")
            ),
            cause=i4.get("likely_cause", "unknown"),
            model_impact=(
                "Model trained on baseline will score a meaningfully different population."
                if v1_dist == "far" else
                "Model scores may have drifted — back-test before any promotion."
            ),
            action=i4.get("action", ""),
            psi=None,
        ))

    # ── I6: Feature drift impact — only genuine (non-data-loss) entries ───
    i6 = results.get("i6", [])
    for item in i6:
        if not item.get("is_real_drift"):
            continue
        psi_label = item.get("psi_label", "stable")
        sev = "critical" if psi_label == "shift" else "medium"
        signals.append(_signal(
            source="i6_feature_drift",
            domain="feature",
            severity=sev,
            headline=f"'{item['column']}' — {item.get('drift_cause', 'distribution shift').replace('_', ' ')}",
            evidence=item.get("evidence", ""),
            cause=item.get("drift_cause", "distribution_shift"),
            model_impact=item.get("model_impact", ""),
            action=item.get("fix", ""),
            psi=None,
        ))
    # Data-loss false positives — medium, pipeline domain
    for item in i6:
        if item.get("is_real_drift"):
            continue
        signals.append(_signal(
            source="i6_data_loss_psi",
            domain="pipeline",
            severity="medium",
            headline=f"'{item['column']}' PSI flag caused by data loss, not drift",
            evidence=item.get("evidence", ""),
            cause="data_loss",
            model_impact="PSI will normalise once pipeline is fixed — no model action needed.",
            action=item.get("fix", "Fix data pipeline to restore completeness."),
            psi=None,
        ))

    # ── C8: PSI matrix — surface shift columns not already in I6 ─────────
    i6_cols = {item["column"] for item in i6}
    c8 = results.get("c8", {})
    for col_entry in c8.get("columns", []):
        col_name = col_entry["column"]
        worst    = col_entry.get("worst_label", "stable")
        if worst not in ("shift", "monitor") or col_name in i6_cols:
            continue
        # Get the worst PSI value across pairs
        worst_psi = max(
            (p["psi"] for p in col_entry.get("pairs", [])
             if p.get("applicable") and p.get("psi") is not None),
            default=None,
        )
        sev = "high" if worst == "shift" else "medium"
        signals.append(_signal(
            source="c8_psi_matrix",
            domain="feature",
            severity=sev,
            headline=f"'{col_name}' distribution shifted (PSI={worst_psi:.3f if worst_psi else '?'})",
            evidence=(
                f"PSI worst label: {worst}. "
                + (f"PSI={worst_psi:.4f}. " if worst_psi else "")
                + "Exceeds monitoring threshold — model trained on older distribution may produce incorrect scores."
            ),
            cause="distribution_shift",
            model_impact="WoE bins or scaler fitted on base version will misalign with new distribution.",
            action=f"Refit preprocessing for '{col_name}'. Back-test model performance before next scoring run.",
            psi=worst_psi,
        ))

    # ── I8: Pipeline break risks ──────────────────────────────────────────
    i8 = results.get("i8", [])
    for risk in i8:
        risk_type = risk.get("risk", "unknown")
        sev = "critical" if risk.get("severity") == "critical" else "high"
        signals.append(_signal(
            source="i8_pipeline_break",
            domain="pipeline",
            severity=sev,
            headline=f"Silent scoring failure: {risk_type.replace('_', ' ')} in '{risk['column']}'",
            evidence=risk.get("detail", ""),
            cause=risk_type,
            model_impact="Scoring pipeline will not raise an error but will produce incorrect predictions.",
            action=risk.get("fix", ""),
            psi=None,
        ))

    # ── I9: Pipeline health ───────────────────────────────────────────────
    i9 = results.get("i9", {})
    if i9 and i9.get("pipeline_health") in ("degrading",):
        pattern = i9.get("pattern", "isolated")
        escalate = i9.get("escalate_to_engineering", False)
        aff_cols = i9.get("affected_columns", [])
        sev = "critical" if (pattern == "systematic" and escalate) else "high"
        signals.append(_signal(
            source="i9_pipeline_health",
            domain="pipeline",
            severity=sev,
            headline=f"Data pipeline {'systematically' if pattern == 'systematic' else 'partially'} degrading — {len(aff_cols)} column(s) affected",
            evidence=(
                f"Completeness degrading in: {', '.join(aff_cols[:5])}{'...' if len(aff_cols) > 5 else ''}. "
                + i9.get("likely_cause", "")
                + (" Engineering escalation required." if escalate else "")
            ),
            cause="pipeline_degradation",
            model_impact="Training on incomplete data introduces systematic bias into the model.",
            action=i9.get("action", ""),
            psi=None,
        ))

    # If signal list is empty, add a CLEAR signal so the LLM always has something to narrate
    if not signals:
        signals.append(_signal(
            source="c0_clear",
            domain="population",
            severity="low",
            headline="No significant drift detected — dataset is stable",
            evidence=c0.get("message", "All metrics are within acceptable thresholds."),
            cause="none",
            model_impact="Current model remains valid on this dataset version.",
            action="Continue monitoring. Re-run comparison on next version ingestion.",
            psi=None,
        ))

    # Sort: severity descending, then PSI descending for equal severity
    signals.sort(key=lambda s: (
        -_SEV_RANK.get(s["severity"], 0),
        -(s.get("psi") or 0.0),
    ))

    # Assign rule-based rank
    for i, sig in enumerate(signals):
        sig["rank"] = i + 1

    return signals


def _signal(source, domain, severity, headline, evidence, cause,
             model_impact, action, psi) -> Dict:
    return {
        "source":       source,
        "domain":       domain,
        "severity":     severity,
        "headline":     headline,
        "evidence":     evidence,
        "cause":        cause,
        "model_impact": model_impact,
        "action":       action,
        "psi":          psi,
        "rank":         0,  # filled after sort
    }
