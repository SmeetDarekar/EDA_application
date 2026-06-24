"""
abt/llm_drift_narratives.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 Decision Intelligence — LLM synthesis of ranked drift signals.

Architecture:
  1. collect_all_signals()   — harvests every rule-based signal from i4–i9,
                               c0, c8 into a flat ranked list. Pure Python,
                               no LLM. This is the single source of truth.
  2. synthesise_drift_insights() — passes the ranked signal list + domain
                               context to the LLM. LLM returns structured
                               JSON (3–5 insight cards). Rule-based decisions
                               are anchors; LLM narrates and contextualises.
  3. Full silent fallback    — any LLM failure returns the rule-based signal
                               list formatted as insight cards. App never breaks.

LLM role contract (enforced in prompt):
  - LLM receives pre-computed verdicts, PSI values, event rate deltas, and
    column names as FIXED FACTS. It cannot change severity labels.
  - LLM re-orders signals by business impact for the given domain/purpose.
  - LLM writes the headline and evidence sentences only.
  - LLM never adds columns, metrics, or thresholds not provided.
  - Domain/purpose adjusts language and framing only — never logic.

Output schema (each insight card):
  {
    "rank":       int,          # 1 = most urgent
    "domain":     str,          # population | target | feature | pipeline | governance
    "headline":   str,          # ≤15 words, pure business consequence language
    "evidence":   str,          # 1–2 sentences, specific numbers, column names
    "action":     str,          # 1 sentence, concrete next step
    "severity":   str,          # critical | high | medium | low
    "source":     str           # which rule produced this (e.g. "i5_target_stability")
  }
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import json
import re
from typing import Dict, List, Optional, Any

from .llm_client import call_llm, LLMError

# ── Domain / purpose vocabularies ────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# 1. SIGNAL COLLECTOR
# Extracts every actionable signal from all interpretation results.
# Returns a list of signal dicts, sorted by severity descending.
# Pure Python — no LLM. This is the ground truth the LLM reads from.
# ─────────────────────────────────────────────────────────────────────────────

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

    # ── Privacy / governance signals from c8 columns (informationPrivacy) ─
    # Governance signals come from the original analyze S4 data if passed through.
    # In compare mode, we surface them from c1 schema context if available.
    c1 = results.get("c1", {})
    # (governance signals are currently in analyze; compare mode exposes them via I8 for dropped cols)
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


# ─────────────────────────────────────────────────────────────────────────────
# 2. LLM SYNTHESIS
# Passes ranked signals + domain context to the LLM.
# LLM returns 3–5 structured insight cards as JSON.
# Falls back silently to rule-based cards on any failure.
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a senior model risk analyst reviewing data drift signals for a risk modeling team.

Your role is NARRATOR and RE-RANKER, not decision-maker.

FIXED CONSTRAINTS you must never violate:
- Every signal you receive is a PRE-COMPUTED FACT. You cannot change severity labels, PSI values, column names, or verdict decisions. These come from validated rule-based logic.
- The overall verdict (CLEAR / MONITOR / BACK_TEST_REQUIRED / BLOCK) is fixed. You cannot upgrade or downgrade it.
- You do not invent new signals, columns, or metrics not present in the input.
- Governance flags (informationPrivacy=private) are fixed compliance facts — never minimise them.

YOUR TASKS:
1. Select the 3 to 5 most impactful signals from the input list for the given domain and model purpose.
2. Re-rank them by business urgency for that specific domain/purpose context.
3. Write a headline (≤15 words, pure business consequence — no column names, no metric names, no technical jargon).
4. Write an evidence sentence (1–2 sentences, include specific numbers and column names from the input).
5. Write an action sentence (1 sentence, concrete and specific).

DOMAIN LANGUAGE RULES (adjust framing only — never logic):
- credit_risk / PD: frame around default risk, borrower population, scorecard stability
- credit_risk / LGD: frame around recovery rates, collateral, loss severity
- fraud: frame around detection rate, false positive cost, alert volume
- churn: frame around retention rate, customer lifetime value, campaign targeting
- insurance: frame around claims frequency, underwriting risk, premium adequacy
- Any domain: governance/privacy signals always use regulatory/compliance framing

HEADLINE RULES (strictly enforced):
- Must describe a business consequence, not a technical metric
- No column names, no metric acronyms (PSI, FSI, WoE), no percentages in isolation
- Example good: "Borrower income distribution has shifted — scorecard cutoffs are stale"
- Example bad: "income PSI=0.31 (shift) — WoE bins need refitting"

OUTPUT FORMAT — respond ONLY with a valid JSON array. No preamble, no explanation, no markdown fences:
[
  {
    "rank": 1,
    "domain": "target",
    "headline": "...",
    "evidence": "...",
    "action": "...",
    "severity": "critical",
    "source": "i5_target_stability"
  },
  ...
]"""


def synthesise_drift_insights(
    results: Dict,
    domain: str = "credit_risk",
    abt_purpose: str = "pd",
    max_cards: int = 5,
) -> Dict:
    """
    Main entry point. Returns:
      {
        "cards":         List[Dict],   # 3–5 insight cards (LLM or fallback)
        "all_signals":   List[Dict],   # full ranked signal list (always rule-based)
        "llm_used":      bool,
        "domain":        str,
        "abt_purpose":   str,
        "fallback_reason": str | None,
      }
    """
    signals = collect_all_signals(results)
    domain_label   = DOMAIN_LABELS.get(domain, "risk modeling")
    purpose_label  = PURPOSE_LABELS.get(abt_purpose, "predictive model")

    # ── Build the user prompt ─────────────────────────────────────────────
    # Cap at top-15 signals to stay within token budget
    top_signals = signals[:15]

    signal_lines = []
    for sig in top_signals:
        signal_lines.append(
            f"[RANK {sig['rank']} | {sig['severity'].upper()} | {sig['domain']} | source={sig['source']}]\n"
            f"  Headline: {sig['headline']}\n"
            f"  Evidence: {sig['evidence']}\n"
            f"  Cause: {sig['cause']}\n"
            f"  Model impact: {sig['model_impact']}\n"
            f"  Recommended action: {sig['action']}"
            + (f"\n  PSI: {sig['psi']:.4f}" if sig.get('psi') is not None else "")
        )

    c0          = results.get("c0", {})
    c9          = results.get("c9", {})
    ds_scores   = c9.get("dataset_scores", [])
    score_line  = " → ".join(f"{d['score']}/100" for d in ds_scores) if ds_scores else "N/A"
    n_versions  = len(ds_scores)
    verdict     = c0.get("verdict", "CLEAR")

    user_prompt = f"""CONTEXT
Domain: {domain_label}
Model purpose: {purpose_label}
Overall verdict (FIXED — do not change): {verdict}
Versions compared: {n_versions}
Dataset readiness scores: {score_line}
Total signals detected: {len(signals)}

SIGNAL LIST (pre-ranked by rule-based severity — re-rank by business urgency for {purpose_label}):
{chr(10).join(signal_lines)}

TASK
Select {min(max_cards, len(top_signals))} signals that matter most for a {domain_label} {purpose_label} team.
Re-rank by business urgency in their context.
Write headline, evidence, and action per the rules in your system prompt.

Return ONLY the JSON array. No other text."""

    # ── Call LLM ──────────────────────────────────────────────────────────
    try:
        raw_response = call_llm(_SYSTEM_PROMPT, user_prompt, max_tokens=1200)
        cards = _parse_and_validate(raw_response, signals, max_cards)
        return {
            "cards":           cards,
            "all_signals":     signals,
            "llm_used":        True,
            "domain":          domain,
            "abt_purpose":     abt_purpose,
            "domain_label":    domain_label,
            "purpose_label":   purpose_label,
            "fallback_reason": None,
        }
    except LLMError as e:
        return _fallback(signals, domain, abt_purpose, domain_label, purpose_label,
                         reason=f"LLM unavailable: {e}", max_cards=max_cards)
    except _ValidationError as e:
        return _fallback(signals, domain, abt_purpose, domain_label, purpose_label,
                         reason=f"LLM response invalid: {e}", max_cards=max_cards)
    except Exception as e:
        return _fallback(signals, domain, abt_purpose, domain_label, purpose_label,
                         reason=f"Unexpected error: {e}", max_cards=max_cards)


# ─────────────────────────────────────────────────────────────────────────────
# 3. RESPONSE VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class _ValidationError(Exception):
    pass

_REQUIRED_CARD_KEYS = {"rank", "domain", "headline", "evidence", "action", "severity", "source"}
_VALID_SEVERITIES   = {"critical", "high", "medium", "low"}
_VALID_DOMAINS      = {"population", "target", "feature", "pipeline", "governance"}


def _parse_and_validate(raw: str, signals: List[Dict], max_cards: int) -> List[Dict]:
    """
    Parse JSON from LLM response. Validate schema. Raise _ValidationError on failure.
    Also fills in 'source' from original signals if LLM omitted it.
    """
    # Strip any accidental markdown fences
    clean = re.sub(r"```(?:json)?", "", raw).strip()
    # Extract the JSON array if surrounded by other text
    m = re.search(r"\[.*\]", clean, re.DOTALL)
    if not m:
        raise _ValidationError("No JSON array found in LLM response")

    try:
        cards = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise _ValidationError(f"JSON parse error: {e}")

    if not isinstance(cards, list) or len(cards) == 0:
        raise _ValidationError("LLM returned empty list")

    validated = []
    signal_source_map = {sig["source"]: sig for sig in signals}

    for i, card in enumerate(cards[:max_cards]):
        if not isinstance(card, dict):
            raise _ValidationError(f"Card {i} is not a dict")

        missing = _REQUIRED_CARD_KEYS - card.keys()
        if missing:
            raise _ValidationError(f"Card {i} missing keys: {missing}")

        # Validate severity — must be one of the fixed set
        if card.get("severity") not in _VALID_SEVERITIES:
            raise _ValidationError(f"Card {i} has invalid severity: {card.get('severity')}")

        # Enforce minimum content length — blank headlines are useless
        if not card.get("headline", "").strip():
            raise _ValidationError(f"Card {i} has empty headline")
        if not card.get("evidence", "").strip():
            raise _ValidationError(f"Card {i} has empty evidence")
        if not card.get("action", "").strip():
            raise _ValidationError(f"Card {i} has empty action")

        # Re-assign rank sequentially (LLM sometimes gets ranks wrong)
        card["rank"] = i + 1

        # Normalise domain to known set; default to 'population' if invalid
        if card.get("domain") not in _VALID_DOMAINS:
            card["domain"] = "population"

        # Attach the original signal's PSI for potential UI use
        orig = signal_source_map.get(card.get("source", ""))
        card["psi"] = orig.get("psi") if orig else None

        validated.append(card)

    if not validated:
        raise _ValidationError("No valid cards after validation")

    return validated


# ─────────────────────────────────────────────────────────────────────────────
# 4. FALLBACK — rule-based cards when LLM is unavailable
# ─────────────────────────────────────────────────────────────────────────────

def _fallback(signals, domain, abt_purpose, domain_label, purpose_label,
               reason, max_cards) -> Dict:
    """
    Convert top-N rule-based signals directly into insight cards.
    Used when LLM fails. Format matches LLM output exactly so
    the template renders identically either way.
    """
    cards = []
    for sig in signals[:max_cards]:
        cards.append({
            "rank":     sig["rank"],
            "domain":   sig["domain"],
            "headline": sig["headline"],
            "evidence": sig["evidence"],
            "action":   sig["action"],
            "severity": sig["severity"],
            "source":   sig["source"],
            "psi":      sig.get("psi"),
        })
    return {
        "cards":           cards,
        "all_signals":     signals,
        "llm_used":        False,
        "domain":          domain,
        "abt_purpose":     abt_purpose,
        "domain_label":    domain_label,
        "purpose_label":   purpose_label,
        "fallback_reason": reason,
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE B — PROMPT CHAINING
# Three focused LLM calls, each with one job.
# Pure additions — nothing above this line is touched.
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# THEME DEFINITIONS
# Each signal's primary metric_type + direction maps to one theme.
# Column belongs to exactly one theme (primary metric wins).
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# CALL 1 — TRIAGE
# Input : theme names + importance scores only (no details yet)
# Job   : rank themes by business urgency for this domain/purpose
# Output: ordered list with triage_reason per theme
# Tokens: 200 max — small, fast, cheap
# ─────────────────────────────────────────────────────────────────────────────

_TRIAGE_SYSTEM = """You are a model risk analyst.
Rank the given drift themes by business urgency for the stated domain and model purpose.
Output ONLY valid JSON — no preamble, no markdown fences.
Format: [{"theme_id": "...", "rank": 1, "triage_reason": "one sentence"}]
Rules:
- triage_reason must be one sentence explaining why this theme ranks here
- Do not change theme_ids
- Do not add themes not in the input"""


def _llm_call_1_triage(
    themes:      List[dict],
    domain:      str,
    abt_purpose: str,
    c0:          dict,
) -> List[dict]:
    """
    Rank themes by business urgency via a small focused LLM call.
    Returns themes reordered by LLM rank.
    Falls back to importance-score order on any failure.
    """
    domain_label   = DOMAIN_LABELS.get(domain, domain)
    purpose_label  = PURPOSE_LABELS.get(abt_purpose, abt_purpose)
    verdict        = c0.get("verdict", "CLEAR")

    theme_lines = "\n".join(
        f"{i+1}. {t['theme_id']} — {t['theme_label']} "
        f"(columns: {', '.join(t['columns'][:3])}{'...' if len(t['columns']) > 3 else ''}, "
        f"importance: {t['max_importance']:.2f})"
        for i, t in enumerate(themes)
    )

    user_prompt = (
        f"Domain: {domain_label}\n"
        f"Model purpose: {purpose_label}\n"
        f"Overall verdict: {verdict}\n\n"
        f"Themes detected:\n{theme_lines}\n\n"
        f"Rank by business urgency for a {domain_label} {purpose_label} team.\n"
        f"Return JSON array only."
    )

    try:
        raw = call_llm(_TRIAGE_SYSTEM, user_prompt, max_tokens=200)
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        m = re.search(r"\[.*\]", clean, re.DOTALL)
        if not m:
            return themes

        ranked = json.loads(m.group(0))
        # Reorder original themes by LLM rank
        rank_map = {r["theme_id"]: r.get("rank", 99) for r in ranked}
        # Attach triage_reason to each theme
        reason_map = {r["theme_id"]: r.get("triage_reason", "") for r in ranked}
        for t in themes:
            t["triage_rank"]   = rank_map.get(t["theme_id"], 99)
            t["triage_reason"] = reason_map.get(t["theme_id"], "")

        themes.sort(key=lambda t: t.get("triage_rank", 99))
        return themes

    except Exception:
        return themes   # fall back to importance-score order


# ─────────────────────────────────────────────────────────────────────────────
# CALL 2 — CARD SYNTHESIS (one call per theme)
# Input : one theme's composite_facts (actual numbers) + fixed anchors
# Job   : write headline + evidence + action for one card
# Output: one card dict
# Tokens: 250 max
# ─────────────────────────────────────────────────────────────────────────────

# _CARD_SYSTEM = """You are a model risk analyst writing one insight card for a risk modeling team.

# STRICT RULES:
# 1. Headline: ≤12 words. Pure business consequence. No column names. No metric acronyms (PSI, IQR, FSI, WoE). No standalone numbers. The headline should highlight the exact impact area for non-technical users. For example, it is observed that the average income has increased/decreased/stable which implies that target behaviour could be affected in a certain way. 
# 2. Evidence: exactly 2 sentences.
#    - Sentence 1: cite at least 2 specific numbers from the FACT SHEET. Use the exact numbers provided — do not round differently or invent others.
#    - Sentence 2: state the scoring consequence in plain language.
# 3. Action: 1 sentence. Must match I7_DECISION exactly. Never recommend stronger action than I7_DECISION.
# 4. Severity: copy exactly from INPUT_SEVERITY — do not change it.
# 5. Output ONLY valid JSON: {"headline": "...", "evidence": "...", "action": "...", "severity": "..."}
#    No preamble. No markdown."""


_CARD_SYSTEM = """
You are a model risk analyst writing one insight card for a risk modeling team.

Your goal is to translate statistical changes into clear business risk impact for non-technical stakeholders.

STRICT RULES:

1. Headline: MUST strictly follow this exact template structure:
"It is observed that [feature concept] has shifted towards [higher/lower value group]. This indicates that [consequence on scoring/default behavior]."

Placeholders guidelines:
- [feature concept]: A human-friendly business description of the underlying feature or customer attributes (e.g. "average income", "borrower debt burden", "repayment stability", "historical credit utilisation"), NOT raw database column names like "dti" or "inc".
- [higher/lower value group]: The group or direction of the shift based on the FACT SHEET (e.g., "higher values", "lower income brackets", "elevated debt levels", "lower credit tiers").
- [consequence on scoring/default behavior]: The business risk impact or consequence on the model's default predictions or score accuracy (e.g., "overall default risk may be underestimated", "repayment scores might be artificially inflated", "scorecard stability could be compromised").

STRICT:
- No raw database column names (e.g. do NOT use "dti", "inc") or acronyms (like PSI, FSI, WoE). Use the human-friendly business descriptions instead.
- If the headline does NOT follow the exact template above, it is incorrect.

2. Evidence: exactly 2 sentences.
   - Sentence 1: Report the numerical changes from FACT SHEET (at least 2 values exactly as given).
   - Sentence 2: Translate those changes into:
        Population shift → Expected risk behavior → Scoring impact
     (e.g., “This indicates more high-income customers entering the dataset, who typically have lower default rates, potentially leading to underestimation of risk in existing scorecards.”)

3. Action: 1 sentence.
   - Must match I7_DECISION exactly.
   - Must align with the level of risk implied in the headline.
   - Never exaggerate beyond I7_DECISION.

4. Severity: copy exactly from INPUT_SEVERITY.

5. Output ONLY valid JSON:
{"headline": "...", "evidence": "...", "action": "...", "severity": "..."}

No preamble. No markdown.
"""


def _format_fact_sheet(cf: dict, theme_id: str) -> str:
    """
    Render composite_facts as a clean numbered fact sheet string for the prompt.
    Only includes fields that have non-None values.
    Numbers are formatted for clarity — no extra decimals.
    """
    lines = [f"THEME: {theme_id}"]
    lines.append(f"Columns affected: {', '.join(cf.get('column_names', []))}")
    lines.append(f"Worst column: {cf.get('worst_column', '?')}")

    psi = cf.get("worst_psi")
    if psi is not None:
        lines.append(
            f"PSI: {psi:.3f} ({cf.get('worst_psi_label', '?')}) "
            f"— threshold: stable<0.10 | monitor 0.10–0.25 | shift>0.25"
            + (f" — worst in pair {cf.get('worst_psi_pair')}" if cf.get("worst_psi_pair") else "")
        )

    mb = cf.get("mean_before")
    ma = cf.get("mean_after")
    mp = cf.get("worst_mean_delta_pct")
    if mb is not None and ma is not None:
        lines.append(f"Mean (worst column): {mb:.4g} → {ma:.4g}  ({mp:+.1f}% change)")

    ms = cf.get("worst_median_shift_iqr")
    if ms is not None:
        lines.append(f"Median shift: {ms:+.3f}× IQR")

    q1 = cf.get("worst_q1_shift_iqr")
    q3 = cf.get("worst_q3_shift_iqr")
    if q1 is not None:
        lines.append(f"Q1 shift: {q1:+.3f}× IQR")
    if q3 is not None:
        lines.append(f"Q3 shift: {q3:+.3f}× IQR")

    max_b = cf.get("max_base")
    max_n = cf.get("max_new")
    min_b = cf.get("min_base")
    min_n = cf.get("min_new")
    if max_b is not None and max_n is not None:
        lines.append(f"Value range: [{min_b:.4g}, {max_b:.4g}] → [{min_n:.4g}, {max_n:.4g}]")

    sc = cf.get("worst_std_norm_change")
    sb = cf.get("std_base")
    sn = cf.get("std_new")
    if sc is not None and sb is not None:
        lines.append(f"Std deviation: {sb:.4g} → {sn:.4g}  ({sc*100:+.1f}% change)")

    cb = cf.get("completeness_before")
    ca = cf.get("completeness_after")
    if cb is not None and ca is not None:
        lines.append(f"Completeness: {cb:.1f}% → {ca:.1f}%"
                     + ("  (stable — PSI not driven by data loss)" if abs(cb - ca) < 2 else ""))

    bp = cf.get("baseline_psi")
    bl = cf.get("baseline_label")
    if bp is not None:
        lines.append(f"Distance from V1 training baseline: PSI={bp:.3f} ({bl})")

    vel = cf.get("worst_velocity")
    if vel is not None:
        lines.append(f"Drift velocity: {vel:+.4f} PSI units/version"
                     + ("  (accelerating)" if vel > 0.05 else ""))

    fsi = cf.get("worst_fsi")
    fl  = cf.get("fsi_label")
    if fsi is not None:
        lines.append(f"Feature stability (FSI): {fsi:.3f} ({fl})")

    return "\n".join(lines)


def _llm_call_2_card(
    theme:       dict,
    domain:      str,
    abt_purpose: str,
    i7:          dict,
    c0:          dict,
    stage:       str = "back_testing",
) -> dict:
    """
    Synthesise one insight card for one theme.
    The fact sheet contains only actual computed numbers.
    Fixed anchors (I7_DECISION, C0_VERDICT) are explicitly labelled
    so the LLM knows they cannot be changed.
    """
    cf             = theme.get("composite_facts", {})
    domain_label   = DOMAIN_LABELS.get(domain, domain)
    purpose_label  = PURPOSE_LABELS.get(abt_purpose, abt_purpose)
    i7_decision    = (i7 or {}).get("decision", "hold")
    verdict        = (c0 or {}).get("verdict", "CLEAR")
    theme_id       = theme["theme_id"]

    # Severity: map signal severity to card severity
    worst_sev = theme["signals"][0].get("severity", "notable") if theme["signals"] else "notable"
    sev_map   = {"critical": "critical", "notable": "high", "stable": "low"}
    input_sev = sev_map.get(worst_sev, "medium")

    fact_sheet = _format_fact_sheet(cf, theme_id)

    user_prompt = (
        f"FACT SHEET (use these exact numbers — do not invent others):\n"
        f"{fact_sheet}\n\n"
        f"FIXED ANCHORS (do not change these in your output):\n"
        f"I7_DECISION: {i7_decision}\n"
        f"C0_VERDICT: {verdict}\n"
        f"INPUT_SEVERITY: {input_sev}\n"
        f"DOMAIN: {domain_label}\n"
        f"PURPOSE: {purpose_label}\n"
        f"STAGE: {stage}\n\n"
        f"Write the insight card JSON now."
    )

    try:
        raw = call_llm(_CARD_SYSTEM, user_prompt, max_tokens=250)
        clean = re.sub(r"```(?:json)?", "", raw).strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if not m:
            raise _ValidationError("No JSON object in response")

        card = json.loads(m.group(0))

        # Validate required keys
        for key in ("headline", "evidence", "action", "severity"):
            if not card.get(key, "").strip():
                raise _ValidationError(f"Missing or empty field: {key}")

        # Enforce severity anchor — LLM must not change it
        card["severity"] = input_sev

        # Attach metadata
        card["theme_id"]      = theme_id
        card["domain"]        = _theme_to_domain(theme_id)
        card["source"]        = f"theme_{theme_id}"
        card["columns"]       = cf.get("column_names", [])
        card["psi"]           = cf.get("worst_psi")
        card["triage_reason"] = theme.get("triage_reason", "")
        return card

    except Exception as e:
        return _rule_based_card_for_theme(theme, domain_label, purpose_label,
                                           i7_decision, input_sev, error=str(e))


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


def _rule_based_card_for_theme(
    theme:         dict,
    domain_label:  str = "",
    purpose_label: str = "",
    i7_decision:   str = "hold",
    severity:      str = "medium",
    error:         str = "",
) -> dict:
    """
    Fallback card when LLM call 2 fails for a theme.
    Uses composite_facts numbers directly — accurate but not narrated.
    """
    cf      = theme.get("composite_facts", {})
    cols    = cf.get("column_names", [])
    col_str = ", ".join(cols[:3]) + ("..." if len(cols) > 3 else "")
    psi     = cf.get("worst_psi")
    mp      = cf.get("worst_mean_delta_pct")
    ms      = cf.get("worst_median_shift_iqr")

    evidence_parts = []
    if psi is not None:
        evidence_parts.append(f"PSI={psi:.3f} ({cf.get('worst_psi_label', 'shift')})")
    if mp is not None:
        evidence_parts.append(f"mean changed {mp:+.1f}%")
    if ms is not None:
        evidence_parts.append(f"median shifted {ms:+.3f}× IQR")

    evidence_str = (
        f"{col_str}: {', '.join(evidence_parts)}. "
        f"Distribution has shifted from the training baseline."
    ) if evidence_parts else f"Distribution shift detected in {col_str}."

    action_map = {
        "retrain":     f"Retrain the model on latest version data.",
        "rebin":       f"Refit WoE bins for affected columns on latest version.",
        "recalibrate": f"Recalibrate the decision threshold on latest validation set.",
        "hold":        f"Monitor in next version cycle.",
    }

    return {
        "headline": f"{theme.get('theme_label', 'Distribution shift detected')}",
        "evidence": evidence_str,
        "action":   action_map.get(i7_decision, "Monitor closely."),
        "severity": severity,
        "theme_id": theme.get("theme_id", "unknown"),
        "domain":   _theme_to_domain(theme.get("theme_id", "")),
        "source":   f"theme_{theme.get('theme_id', 'unknown')}_fallback",
        "columns":  cols,
        "psi":      psi,
        "error":    error,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CALL 3 — META NARRATIVE (optional, one call for all cards together)
# Input : all card headlines + I7 decision + verdict
# Job   : one connecting sentence explaining how findings relate
# Output: str (≤25 words) or None on failure
# Tokens: 60 max — intentionally tiny to force tight synthesis
# ─────────────────────────────────────────────────────────────────────────────

_META_SYSTEM = """You are a senior model risk analyst.
Write ONE sentence (≤25 words) connecting the given drift findings into a single portfolio narrative.
Plain text only. No bullet points. No JSON. No markdown."""


def _llm_call_3_meta(
    cards:  List[dict],
    i7:     dict,
    c0:     dict,
    domain: str,
) -> Optional[str]:
    """
    One connecting sentence linking all cards into a single portfolio event.
    Returns None on any failure — this call is optional.
    """
    if len(cards) < 2:
        return None

    headlines = "\n".join(
        f"{i+1}. {c.get('headline', '')}" for i, c in enumerate(cards)
    )
    i7_decision = (i7 or {}).get("decision", "hold")
    verdict     = (c0 or {}).get("verdict", "CLEAR")
    domain_label = DOMAIN_LABELS.get(domain, domain)

    user_prompt = (
        f"Findings ({domain_label} model):\n{headlines}\n\n"
        f"Overall verdict: {verdict}  |  Model action: {i7_decision}\n\n"
        f"One connecting sentence (≤25 words):"
    )

    try:
        raw = call_llm(_META_SYSTEM, user_prompt, max_tokens=60)
        sentence = raw.strip().strip('"').strip("'")
        # Sanity: must be a single sentence, not JSON, not a list
        if not sentence or sentence.startswith("[") or sentence.startswith("{"):
            return None
        return sentence
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SYNTHESISE V2 — ORCHESTRATOR
# Replaces synthesise_drift_insights() for Phase B callers.
# synthesise_drift_insights() stays untouched as hard fallback.
# ─────────────────────────────────────────────────────────────────────────────

def synthesise_drift_insights_v2(
    results:     Dict,
    domain:      str = "credit_risk",
    abt_purpose: str = "pd",
    max_cards:   int = 5,
    stage:       str = "back_testing",
) -> Dict:
    """
    3-call prompt chain producing 3–5 accurate insight cards.

    Call 1 (triage)   : rank themes by domain urgency            — 200 tokens
    Call 2 (card)     : one card per theme, numbers from facts   — 250 tokens each
    Call 3 (meta)     : one connecting sentence across all cards — 60 tokens

    Every number in a card's evidence is traceable to composite_facts.
    Falls back to synthesise_drift_insights() on total failure.
    """
    try:
        from .signal_collector import collect_signals_v2

        # Step 0: collect signals with raw_facts attached
        signals = collect_signals_v2(results)
        if not signals:
            return _fallback([], domain, abt_purpose,
                             DOMAIN_LABELS.get(domain, domain),
                             PURPOSE_LABELS.get(abt_purpose, abt_purpose),
                             reason="No signals detected", max_cards=max_cards)

        # Step 0b: group into themes
        themes = group_signals_by_theme(signals)
        if not themes:
            return _fallback(signals, domain, abt_purpose,
                             DOMAIN_LABELS.get(domain, domain),
                             PURPOSE_LABELS.get(abt_purpose, abt_purpose),
                             reason="No themes formed", max_cards=max_cards)

        c0 = results.get("c0", {})
        i7 = results.get("i7", {})

        # Step 1: triage — rank themes by domain urgency
        try:
            themes = _llm_call_1_triage(themes, domain, abt_purpose, c0)
        except Exception:
            pass  # keep importance-score order

        # Step 2: card synthesis — one LLM call per theme
        cards: List[dict] = []
        errors = []
        for i, theme in enumerate(themes[:max_cards]):
            card = _llm_call_2_card(theme, domain, abt_purpose, i7, c0, stage)
            card["rank"] = i + 1
            if card.get("error"):
                errors.append(card["error"])
            cards.append(card)

        # If all cards failed to generate via LLM, treat it as LLM failed
        llm_used = True
        fallback_reason = None
        if len(cards) > 0 and len(errors) == len(cards):
            llm_used = False
            fallback_reason = f"LLM card generation failed: {errors[0]}"

        # Step 3: meta narrative (optional)
        meta_narrative = _llm_call_3_meta(cards, i7, c0, domain) if llm_used else None

        return {
            "cards":           cards,
            "meta_narrative":  meta_narrative,
            "all_signals":     signals,
            "themes":          themes,
            "llm_used":        llm_used,
            "domain":          domain,
            "abt_purpose":     abt_purpose,
            "domain_label":    DOMAIN_LABELS.get(domain, domain),
            "purpose_label":   PURPOSE_LABELS.get(abt_purpose, abt_purpose),
            "fallback_reason": fallback_reason,
        }

    except Exception as e:
        # Hard fallback to v1
        try:
            return synthesise_drift_insights(results, domain, abt_purpose, max_cards)
        except Exception:
            return _fallback([], domain, abt_purpose,
                             DOMAIN_LABELS.get(domain, domain),
                             PURPOSE_LABELS.get(abt_purpose, abt_purpose),
                             reason=f"Full fallback: {e}", max_cards=max_cards)