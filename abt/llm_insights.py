"""
abt/llm_insights.py
─────────────────────────────────────────────────────────────────────────────
Business logic layer for LLM enrichment.

Rules:
  1. LLM receives ONLY computed results (scores, flags, PSI) — never raw JSON.
  2. Every function has a hardcoded fallback — LLM failure never breaks the app.
  3. Three enrichment points:
       enrich_analyze(results)  → adds narrative to S0, S9 actions
       enrich_compare(results)  → adds narrative to C0 verdict
  4. The enriched text is ADDED alongside existing fields, never replaces them.
  5. Prompts enforce a "data journalist" tone — concise, fact-first, no fluff.

STORYTELLING SEQUENCE (both analyze and compare):
  Analyze  : Headline (S0) → What's wrong (S2+S3) → Hidden risks (S4) →
             What to do first (S9) → Target health (S6) → Feature quality (S7+S8)
  Compare  : Verdict (C0) → What changed (C1+C2) → Is data drifting (C3+C4+C8) →
             Is the target stable (C5) → Quality over time (C6) → Readiness change (C7+C9)
"""

from typing import Dict, List, Optional
from .llm_client import call_llm, LLMError

# ── System prompt — same for all calls ───────────────────────────────────────
_SYSTEM = """You are a senior data scientist specialising in risk model development.
You receive structured analysis results about a dataset (ABT — Analytic Base Table).
Your job is to write SHORT, precise, actionable commentary — like a senior colleague 
reviewing the data before a model training sprint.

Rules:
- Maximum 3 sentences per response unless told otherwise.
- Lead with the most important finding.
- Use specific numbers from the data provided.
- Never say "it is important to note" or "please ensure" — be direct.
- Never repeat what is already obvious from the numbers.
- If something is fine, say it is fine in one sentence and stop.
"""


# ─────────────────────────────────────────────────────────────────────────────
# ANALYZE enrichment
# ─────────────────────────────────────────────────────────────────────────────

def enrich_analyze(results: Dict) -> Dict:
    """
    Adds LLM-generated narrative to analyze results.
    Modifies results dict in-place, returns it.
    Falls back silently on any LLM error.
    """
    # S0 — headline narrative
    results["s0"]["narrative"] = _s0_narrative(results)

    # S9 — enrich each action's 'why' with LLM insight
    # Batch all actions into one call to save API round-trips
    results["s9"] = _enrich_actions(results["s9"], results["s1"])

    # S6 — target narrative (if target found)
    if results.get("s6") and not results["s6"].get("error"):
        results["s6"]["narrative"] = _s6_narrative(results["s6"])

    return results


def _s0_narrative(results: Dict) -> str:
    s0 = results["s0"]
    s1 = results["s1"]
    s2 = results["s2"]
    s4 = results["s4"]

    blocker_cols  = [b["column"] for b in s2]
    leakage_cols  = [g["column"] for g in s4
                     if any(r["risk_type"] == "LEAKAGE" for r in g["risks"])]
    privacy_cols  = [g["column"] for g in s4
                     if any(r["risk_type"] == "PRIVACY" for r in g["risks"])]

    user_prompt = f"""Dataset: {s1['abt_name']}  |  Version: {s1['version']}  |  Rows: {s1['row_count']:,}
Readiness score: {s0['score']}/100 ({s0['label']})
Total columns: {s1['total_columns']} | Fully complete: {s1['fully_complete']} | High missing: {s1['high_missing']}
Blockers: {len(s2)} columns — {blocker_cols}
Leakage risks: {leakage_cols}
Privacy flags: {privacy_cols}
Overall health: {s1['overall_health']}

Write a 2–3 sentence headline summary of this dataset's readiness for model training.
Start with the readiness score in plain language, then name the single most urgent issue."""

    return _call_with_fallback(
        user_prompt,
        fallback=f"Dataset readiness score is {s0['score']}/100 ({s0['label']}). "
                 f"{len(s2)} column(s) are blocked and must be resolved before training."
    )


def _enrich_actions(actions: List[Dict], s1: Dict) -> List[Dict]:
    if not actions:
        return actions

    # Build a compact batch prompt — one call for all actions
    lines = []
    for a in actions:
        lines.append(f"#{a['rank']} [{a['severity']}] Column='{a['column']}' "
                     f"Action='{a['action']}' "
                     f"CurrentWhy='{a['why'][:120]}'")

    user_prompt = f"""Dataset: {s1['abt_name']} | Rows: {s1['row_count']:,}
Below are the top issues found, in priority order:

{chr(10).join(lines)}

For each issue (#1, #2, etc.), rewrite the 'why' explanation in 1–2 sentences.
Be specific about the modeling consequence if this is NOT fixed.
Format your response exactly as:
#1: <explanation>
#2: <explanation>
... and so on."""

    try:
        response = call_llm(_SYSTEM, user_prompt, max_tokens=800)
        parsed   = _parse_numbered_response(response, len(actions))
        for i, a in enumerate(actions):
            if i < len(parsed) and parsed[i]:
                a["why_llm"] = parsed[i]
            else:
                a["why_llm"] = a["why"]
    except LLMError:
        for a in actions:
            a["why_llm"] = a["why"]

    return actions


def _s6_narrative(s6: Dict) -> str:
    user_prompt = f"""Target column: '{s6['column']}'
Event rate: {s6['event_rate']}% | Non-event rate: {s6['non_event_rate']}%
Imbalance ratio: {s6['imbalance_ratio']}:1 | Balance label: {s6['balance_label']}
Skewness: {s6['skewness']}

Write 1–2 sentences on what this event rate means for model training strategy.
Be specific about whether SMOTE, class weights, or stratification applies here."""

    return _call_with_fallback(
        user_prompt,
        fallback=f"Event rate is {s6['event_rate']}% with a {s6['imbalance_ratio']}:1 imbalance ratio ({s6['balance_label']})."
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMPARE enrichment
# ─────────────────────────────────────────────────────────────────────────────

def enrich_compare(results: Dict) -> Dict:
    """
    Adds LLM-generated narrative to compare results.
    Enriches C-sections (c0, c8, version_story) and
    Tier B interpretation sections (i4b–i9b).
    """
    # ── Existing C-section enrichment ────────────────────────────────────────
    results["c0"]["narrative"] = _c0_narrative(results)
    results["c8"]["narrative"] = _c8_narrative(results["c8"])
    results["version_story"]   = _version_story(results)   # Feature 10

    # ── Tier B interpretation enrichment ─────────────────────────────────────
    # Each call receives the rule-based decision as a FIXED anchor.
    # The LLM narrates and justifies — never decides.
    if results.get("i4b"):
        results["i4b"]["narrative"] = _i4b_narrative(results["i4b"], results.get("c9", {}))

    if results.get("i5b") and results["i5b"].get("target_found"):
        results["i5b"]["narrative"] = _i5b_narrative(
            results["i5b"], results.get("c3", {}), results.get("c6", []))

    if results.get("i6b"):
        results["i6b_narrative"] = _i6b_narrative(
            results["i6b"], results.get("c8", {}))

    if results.get("i7b"):
        results["i7b"]["narrative"] = _i7b_narrative(
            results["i7b"], results.get("c9", {}))

    if results.get("i9b"):
        results["i9b"]["narrative"] = _i9b_narrative(
            results["i9b"], results.get("c9", {}))

    return results


def _version_story(results: Dict) -> str:
    """
    Feature 10: Single LLM-generated executive narrative across ALL compare sections.
    Reads like a data quality sprint review — what happened, why it matters, what to do.
    This is the one output a non-technical stakeholder reads first.
    """
    c0  = results["c0"]
    c1  = results["c1"]
    c5  = results.get("c5")
    c8  = results["c8"]
    c9  = results["c9"]
    c3  = results["c3"]
    c10 = results.get("c10", [])

    versions    = [v["name"] for v in c1["versions"]]
    ds_scores   = c9.get("dataset_scores", [])
    score_line  = " → ".join(f"{d['score']}/100" for d in ds_scores)
    trend_note  = c9.get("trend_note", "")
    verdict     = c0["verdict"]

    # Missingness patterns — only surface actionable ones
    bad_patterns = [r["column"] for r in c3.get("rows", [])
                    if r.get("missing_pattern") in ("growing_missing", "newly_missing", "sparse")]

    # Cardinality explosions
    card_explosions = [f"{e['column']} ({e['explosions'][0]['from_card']}→{e['explosions'][0]['to_card']})"
                       for e in c10 if e.get("explosions")]

    # Target drift summary
    target_line = "No target column tracked."
    if c5:
        drifts = c5.get("pairwise_drift", [])
        if drifts:
            worst = max(drifts, key=lambda d: d.get("abs_delta_pp", 0))
            target_line = (f"Target event rate: worst shift was {worst['delta_pp']:+.1f}pp "
                           f"({worst['from_ver']}→{worst['to_ver']}), severity={worst['severity']}")

    # PSI
    shift_cols = c8.get("summary", {}).get("critical_columns", [])

    user_prompt = f"""You are writing a data quality sprint review note for a risk modeling team.
Comparing {len(versions)} versions: {versions}
Overall verdict: {verdict}
Dataset readiness scores: {score_line}
Trend: {trend_note}
{target_line}
PSI critical shifts: {shift_cols if shift_cols else "none"}
Missingness problems (growing or newly absent): {bad_patterns if bad_patterns else "none"}
Cardinality explosions (new categories appeared): {card_explosions if card_explosions else "none"}

Write a 4-5 sentence version story — like a lead data scientist briefing the team before a model release decision.
Structure: (1) Overall health verdict. (2) Most critical finding with specific numbers. (3) What improved. (4) What still needs fixing. (5) Recommended next action (train / hold / back-test).
Be direct. Use column names and numbers. No filler phrases."""

    return _call_with_fallback(
        user_prompt,
        max_tokens=400,
        fallback=(f"Comparing {len(versions)} versions ({' → '.join(versions)}). "
                  f"Overall verdict: {verdict}. "
                  f"Dataset readiness: {score_line}. "
                  + (f"Target drift detected — back-testing required. " if c5 and any(d.get("back_test_required") for d in c5.get("pairwise_drift",[])) else "")
                  + (f"PSI critical columns: {', '.join(shift_cols)}. " if shift_cols else "")
                  + "Review section-by-section results below for full detail.")
    )


def _c0_narrative(results: Dict) -> str:
    c0  = results["c0"]
    c1  = results["c1"]
    c5  = results.get("c5")
    c8  = results["c8"]
    c9  = results["c9"]

    versions   = [v["name"] for v in c1["versions"]]
    ds_scores  = c9.get("dataset_scores", [])
    score_line = " → ".join(f"{d['score']}/100" for d in ds_scores) if ds_scores else "N/A"
    shift_cols = c8.get("summary", {}).get("critical_columns", [])

    target_line = "No target column found."
    if c5:
        drifts = c5.get("pairwise_drift", [])
        if drifts:
            d = drifts[-1]
            target_line = (f"Target event rate shifted {d['delta_pp']:+.1f}pp "
                           f"(severity: {d['severity']}, back-test: {d['back_test_required']})")

    pairwise_summary = []
    for pw in c1.get("pairwise", []):
        pairwise_summary.append(
            f"{pw['from']}→{pw['to']}: "
            f"+{len(pw['added_columns'])} cols added, "
            f"-{len(pw['dropped_columns'])} dropped, "
            f"{len(pw['worsened'])} readiness worsened, "
            f"{len(pw['improved'])} improved"
        )

    user_prompt = f"""Comparing {len(versions)} dataset versions: {versions}
Overall verdict: {c0['verdict']}
Dataset readiness scores across versions: {score_line}
{target_line}
PSI shifts (columns with significant population shift): {shift_cols if shift_cols else 'none'}
Version-to-version changes: {'; '.join(pairwise_summary)}
Existing issues list: {c0['issues']}

Write a 3–4 sentence executive summary of what changed across these versions.
Lead with whether it is safe to train/promote a model, then explain the key drivers.
Be specific with column names and numbers."""

    return _call_with_fallback(
        user_prompt,
        fallback=c0["message"]
    )


def _c8_narrative(c8: Dict) -> str:
    summary = c8.get("summary", {})
    shift_cols   = summary.get("critical_columns", [])
    monitor_cols = [c["column"] for c in c8.get("columns", [])
                    if c.get("worst_label") == "monitor"]

    if not shift_cols and not monitor_cols:
        return "All numeric features are stable across versions — no significant population shift detected."

    user_prompt = f"""PSI (Population Stability Index) analysis results:
Columns with significant shift (PSI > 0.25): {shift_cols}
Columns to monitor (PSI 0.10–0.25): {monitor_cols}

PSI < 0.10 = stable, 0.10–0.25 = monitor, > 0.25 = significant shift requiring model re-training.

Write 2 sentences explaining what these PSI results mean for the existing model's performance
on the new data. Name specific columns."""

    return _call_with_fallback(
        user_prompt,
        fallback=(f"{len(shift_cols)} column(s) show significant population shift (PSI > 0.25): "
                  f"{shift_cols}. Model performance on new data may have degraded for these features.")
        if shift_cols else
        f"{len(monitor_cols)} column(s) show moderate drift and should be monitored."
    )



# ─────────────────────────────────────────────────────────────────────────────
# TIER B INTERPRETATION ENRICHMENT  (i4b–i9b)
# Rule decision is passed as a FIXED anchor. LLM narrates only.
# ─────────────────────────────────────────────────────────────────────────────

def _i4b_narrative(i4b: Dict, c9: Dict) -> str:
    """I4b: Population shift — 3-sentence multi-version trajectory narrative."""
    version_labels = i4b.get("version_labels", [])
    n              = len(version_labels)
    shift_scope    = i4b.get("shift_scope", "stable")
    drifted_count  = i4b.get("drifted_count", 0)
    total_features = i4b.get("total_features", 0)
    drifted_names  = i4b.get("drifted_features", [])
    likely_cause   = i4b.get("likely_cause", "undetermined")
    coordinated    = i4b.get("coordinated", False)
    v1_distance    = i4b.get("v1_distance", "unknown")
    row_delta_pct  = i4b.get("row_delta_pct")
    ds_scores      = i4b.get("dataset_scores", c9.get("dataset_scores", []))
    score_line     = " → ".join(f"{d['abt']}={d['score']}/100" for d in ds_scores) if ds_scores else "N/A"
    trend_note     = i4b.get("trend_note", c9.get("trend_note", ""))
    v1_label       = version_labels[0] if version_labels else "V1"
    vn_label       = version_labels[-1] if version_labels else "latest"
    row_str        = (f"Row count changed {row_delta_pct:+.1f}% from {v1_label} to {vn_label}."
                      if row_delta_pct is not None else "Row count stable.")
    coord_str      = ("coordinated shift across features" if coordinated else "independent feature movements")

    user_prompt = f"""You are a senior credit risk model validator reviewing an Analytic Base Table
tracked across {n} versions: {version_labels}.

The rule-based system has determined — these are fixed facts:
- Shift scope: {shift_scope} ({drifted_count}/{total_features} features shifted, {coord_str})
- Likely cause: {likely_cause}
- Distance from V1 (training baseline): {v1_distance}
- {row_str}
- Features that shifted: {drifted_names}
- Dataset readiness scores: {score_line}
- Trend: {trend_note}

Write exactly 3 sentences:
1. Describe what happened to the population across all {n} versions. Use version names and numbers.
2. What this means for a model trained on {v1_label}.
3. The single most important action right now.

Do not invent numbers. Use only the values above."""

    fallback = (f"Across {n} versions, {drifted_count}/{total_features} features show {shift_scope} "
                f"population shift driven by {likely_cause.replace('_',' ')}. "
                f"A model trained on {v1_label} is {v1_distance} from the current population. "
                f"{'Back-test immediately.' if v1_distance == 'far' else 'Monitor and review binning.'}")
    return _call_with_fallback(user_prompt, fallback=fallback, max_tokens=300)


def _i5b_narrative(i5b: Dict, c3: Dict, c6: List[Dict]) -> str:
    """I5b: Target stability — 2-sentence trajectory + drift type confirmation."""
    drift_type    = i5b.get("drift_type", "stable")
    model_impact  = i5b.get("model_impact", "")
    action        = i5b.get("action", "none")
    rate_series   = i5b.get("rate_series", [])
    total_pp      = i5b.get("total_drift_pp", 0.0)
    max_jump      = i5b.get("max_single_jump_pp", 0.0)
    data_loss     = i5b.get("data_loss_risk", False)
    label_change  = i5b.get("label_change_risk", False)
    version_labels= i5b.get("version_labels", [])
    n             = len(version_labels)
    rates_str     = "; ".join(f"{ver}={rate:.1f}%" for ver, rate in rate_series if rate is not None)
    v1_label      = version_labels[0] if version_labels else "V1"

    if not rates_str:
        return model_impact or "Target data insufficient for narrative."

    user_prompt = f"""You are a senior credit risk model validator.

FIXED facts (do not change or contradict):
- Drift type: {drift_type}
- Model impact: {model_impact}
- Required action: {action}

Context across all {n} versions:
- Event rates: {rates_str}
- Total drift: {total_pp:.2f}pp | Largest single-version jump: {max_jump:.2f}pp
- Data loss risk: {data_loss} | Label change risk: {label_change}

Write exactly 2 sentences:
1. Describe the event rate trajectory across all {n} versions using specific numbers and version names. State whether the curve is gradual, stepped, or sudden.
2. Explain why {drift_type} is the correct classification and what the consequence is for the deployed model.

Do not suggest a different action. Use only the numbers above."""

    fallback = (f"Event rate moved from {rate_series[0][1]:.1f}% in {rate_series[0][0]} to "
                f"{rate_series[-1][1]:.1f}% in {rate_series[-1][0]} ({total_pp:+.1f}pp total, "
                f"max jump {max_jump:.1f}pp) — classified as {drift_type}. {model_impact}"
                if len(rate_series) >= 2 else model_impact)
    return _call_with_fallback(user_prompt, fallback=fallback, max_tokens=250)


def _i6b_narrative(i6b: List[Dict], c8: Dict) -> str:
    """I6b: Feature drift impact — 3-sentence cross-feature correlation narrative."""
    genuine   = [f for f in i6b if f.get("is_real_drift", True)]
    false_pos = [f for f in i6b if not f.get("is_real_drift", True)]
    version_labels = c8.get("version_labels", [])
    n = len(version_labels)

    if not genuine:
        fp_names = [f["column"] for f in false_pos]
        return (f"No genuine feature drift detected. "
                + (f"{len(false_pos)} apparent shift(s) ({fp_names}) are data-loss false positives." if false_pos else ""))

    genuine_lines = []
    for g in genuine[:6]:
        psi_vals = "; ".join(f"{p['from']}→{p['to']}: {p['psi']:.3f}" for p in g.get("psi_series", []) if p.get("psi"))
        genuine_lines.append(f"  {g['column']}: {g['drift_cause']}, PSI=[{psi_vals}]")

    fp_lines = [f"  {f['column']}: data_loss (pipeline)" for f in false_pos[:3]]
    coord = any(g.get("drift_cause") == "center_shift" for g in genuine)

    user_prompt = f"""You are a senior credit risk model validator reviewing feature drift across {n} versions.

FIXED classifications:
Genuine drift (require action):
{chr(10).join(genuine_lines) or "  (none)"}
Data-loss false positives (pipeline, not model):
{chr(10).join(fp_lines) or "  (none)"}

Write exactly 3 sentences:
1. Are the genuinely drifted features moving together or independently? Name them and state whether drift is correlated.
2. What does the combined pattern suggest about the likely business or pipeline cause?
3. Should these features be fixed together or independently? Name which ones should be grouped.

Do not repeat per-column fix instructions. Use feature names and PSI values from the input."""

    fallback = (f"{len(genuine)} feature(s) show genuine drift: {[g['column'] for g in genuine]}. "
                f"{'Drift appears correlated — likely a single population-level cause.' if coord else 'Features drift independently.'} "
                f"{len(false_pos)} apparent shift(s) are data-loss false positives.")
    return _call_with_fallback(user_prompt, fallback=fallback, max_tokens=300)


def _i7b_narrative(i7b: Dict, c9: Dict) -> str:
    """I7b: Model action — 3-sentence governance committee narrative."""
    decision       = i7b.get("decision", "hold")
    urgency        = i7b.get("urgency", "low")
    reason         = i7b.get("reason", "")
    avoid          = i7b.get("avoid", "")
    genuine_count  = i7b.get("genuine_count", 0)
    accelerating   = i7b.get("accelerating_cols", [])
    unstable_cols  = i7b.get("unstable_cols", [])
    ds_scores      = i7b.get("dataset_scores", c9.get("dataset_scores", []))
    version_labels = i7b.get("version_labels", [d.get("abt","") for d in ds_scores])
    n              = len(version_labels)
    score_line     = " → ".join(f"{d['abt']}={d['score']}/100" for d in ds_scores) if ds_scores else "N/A"
    drift_type     = i7b.get("i5_drift_type", "stable")
    total_pp       = i7b.get("i5_total_drift_pp", 0.0)
    v1_label       = version_labels[0] if version_labels else "V1"
    vn_label       = version_labels[-1] if version_labels else "latest"

    user_prompt = f"""You are a senior credit risk model validator writing a governance note.

FIXED — do not change or contradict:
- Decision: {decision}
- Urgency: {urgency}
- Reason: {reason}
- Avoid: {avoid}

Evidence across {n} versions ({version_labels}):
- Target drift: {drift_type}, {total_pp:.1f}pp total
- Features with genuine drift: {genuine_count}
- Accelerating features (PSI velocity >0.05/ver): {accelerating if accelerating else "none"}
- Chronically unstable (FSI <0.40): {unstable_cols if unstable_cols else "none"}
- Dataset readiness: {score_line}

Write exactly 3 sentences for a risk governance committee:
1. State the decision ({decision}) and urgency in plain business language, referencing {v1_label} to {vn_label}.
2. Justify why this action (not more or less aggressive) is correct given the evidence.
3. State the consequence of NOT taking this action within the current scoring cycle.

No technical jargon. Do not suggest any action other than {decision}. Use version names and numbers."""

    fallback = (f"Based on {n} versions from {v1_label} to {vn_label}, the decision is {decision} "
                f"(urgency: {urgency}). {reason} Avoid: {avoid}")
    return _call_with_fallback(user_prompt, fallback=fallback, max_tokens=300)


def _i9b_narrative(i9b: Dict, c9: Dict) -> str:
    """I9b: Pipeline health — 2-sentence degradation trajectory + escalation message."""
    pipeline_health = i9b.get("pipeline_health", "healthy")
    pattern         = i9b.get("pattern", "stable")
    escalate        = i9b.get("escalate_to_engineering", False)
    affected_cols   = i9b.get("affected_columns", [])
    likely_cause    = i9b.get("likely_cause", "undetermined")
    health_delta    = i9b.get("health_score_delta")
    first_seen      = i9b.get("first_seen_version")
    recovering_cols = i9b.get("recovering_columns", [])
    version_labels  = i9b.get("version_labels", [])
    n               = len(version_labels)
    ds_scores       = i9b.get("dataset_scores", c9.get("dataset_scores", []))
    score_line      = " → ".join(f"{d['abt']}={d['score']}/100" for d in ds_scores) if ds_scores else "N/A"
    delta_str       = (f"Health delta: {health_delta:+.1f} pts." if health_delta is not None else "")
    first_str       = (f"Degradation first seen in {first_seen}." if first_seen else "")

    user_prompt = f"""You are a senior data engineer reviewing pipeline health across {n} versions: {version_labels}.

FIXED facts:
- Pipeline health: {pipeline_health} ({pattern} pattern)
- Escalate to engineering: {escalate}
- Affected columns: {affected_cols if affected_cols else "none"}
- Likely cause: {likely_cause}
- {first_str} {delta_str}
- Recovering columns: {recovering_cols if recovering_cols else "none"}
- Dataset health scores: {score_line}

{"ESCALATION REQUIRED: your second sentence must contain the specific engineering message naming columns and cause." if escalate else "No escalation required."}

Write exactly 2 sentences:
1. Describe what happened to data supply across all {n} versions. State when degradation started and how long it has been accumulating.
2. {"State the consequence of training without fixing the pipeline and write the specific escalation message to engineering." if escalate else "State the current status and required monitoring action."}

Be direct. Use version names and column names. {"Do not soften the escalation." if escalate else ""}"""

    fallback = (f"Pipeline health is {pipeline_health} ({pattern}) across {n} versions "
                f"({' → '.join(version_labels)}). "
                + (f"Degradation since {first_seen} affecting {affected_cols} — escalate to engineering: {likely_cause.replace('_',' ')}."
                   if escalate else
                   f"{'No pipeline issues.' if pipeline_health == 'healthy' else f'Monitor: {affected_cols}.'}"))
    return _call_with_fallback(user_prompt, fallback=fallback, max_tokens=250)



# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# def _call_with_fallback(user_prompt: str, fallback: str, max_tokens: int = None) -> str:
#     try:
#         kwargs = {"max_tokens": max_tokens} if max_tokens else {}
#         return call_llm(_SYSTEM, user_prompt, **kwargs)
#     except LLMError:
#         return fallback


def _parse_numbered_response(text: str, expected: int) -> List[str]:
    """Parse '#1: ...\\n#2: ...' format into a list."""
    import re
    results = [""] * expected
    for match in re.finditer(r"#(\d+):\s*(.+?)(?=\n#\d+:|\Z)", text, re.DOTALL):
        idx = int(match.group(1)) - 1
        if 0 <= idx < expected:
            results[idx] = match.group(2).strip().replace("\n", " ")
    return results


































"""
ADDITION TO abt/llm_insights.py
─────────────────────────────────────────────────────────────────────────────
Append these functions to the bottom of llm_insights.py.
Also update enrich_compare() to call enrich_drift_stories().

New function added:
  enrich_drift_stories(insights, results, stage) → insights

Called from build_business_insights() after all 7 insights are built:

    if use_llm:
        try:
            from .llm_insights import enrich_drift_stories
            insights = enrich_drift_stories(insights, results, stage)
        except Exception:
            pass

Each drift story (slots drift_story_1/2/3) gets one LLM call.
LLM receives only computed numbers — no prose it can embellish.
Output is exactly 2 sentences in business/customer language.
Fallback is the rule-based headline already present in the insight.

Prompt design rules (anti-hallucination):
  - LLM receives structured key-value facts, not narrative
  - DRIFT_CAUSE is the I6-style diagnosis — exact mechanism
  - Numbers passed are only what was computed (PSI, mean delta, quantile)
  - LLM is told the stage — changes urgency framing only
  - LLM must write about CUSTOMERS, not COLUMNS
  - max_tokens=120 per call — 2 sentences maximum
  - Fallback = existing rule-based headline (already good, LLM is additive)
─────────────────────────────────────────────────────────────────────────────
"""


# ── Separate system prompt for drift story narration ─────────────────────────
# Deliberately different from _SYSTEM — this one speaks to a business audience,
# not a data science colleague.

_DRIFT_STORY_SYSTEM = """You are a risk model monitoring analyst writing 
business-level insight summaries for a credit risk team.

You receive structured drift facts about one feature in a dataset comparison.
Write exactly 2 sentences explaining what this means for the customer portfolio.

Rules:
- Write about CUSTOMERS or PORTFOLIO, not about columns or metrics.
- Use the specific numbers provided — do not invent or round differently.
- Sentence 1: what changed in the customer population.
- Sentence 2: what risk this creates for the model or business decision.
- Do not use: "it is important", "please note", "we should", "it seems".
- Do not repeat the column name more than once.
- Do not explain what PSI means — the audience knows.
- Maximum 2 sentences. Hard limit."""


_STAGE_FRAMING = {
    "development":      "This is a development dataset being prepared for model training.",
    "back_testing":     "This is a back-testing sample being validated before model promotion.",
    "pre_deployment":   "This is a pre-deployment validation dataset being reviewed for sign-off.",
    "production":       "This is live production scoring data being monitored.",
}

_STAGE_RISK_FRAMING = {
    "development":      "If unaddressed, the trained model will not generalise to real data.",
    "back_testing":     "If unaddressed, the model should not be promoted to production.",
    "pre_deployment":   "If unaddressed, deployment sign-off must be withheld.",
    "production":       "The model is currently scoring this population — impact is live.",
}


def _build_drift_story_facts(insight: dict, stage: str) -> str:
    """
    Extract computed facts from a drift story insight and format as
    a structured key-value block for the LLM.

    Only passes numbers that were computed — never passes narrative strings
    that the LLM could just rephrase (that would be circular).
    """
    evidence     = insight.get("evidence", [])
    col_name     = "unknown"
    drift_cause  = "unknown"
    psi_val      = None
    mean_old     = None
    mean_new     = None
    mean_delta   = None
    median_shift = None
    q1_shift     = None
    q3_shift     = None
    upper_shift  = None
    lower_shift  = None
    std_base     = None
    std_new      = None
    from_card    = None
    to_card      = None
    miss_pattern = None
    severity     = insight.get("severity", "notable")

    # Extract column name from first labelled evidence row
    for ev in evidence:
        label = ev.get("label", "")
        if " — " in label:
            col_name = label.split(" — ")[-1].strip()
            break

    # Parse computed numbers from evidence rows
    for ev in evidence:
        label  = ev.get("label", "").lower()
        detail = ev.get("detail", "")

        if "root cause" in label:
            # Extract drift_cause from cause_summary in evidence
            for cause in ("center_shift", "boundary_expansion", "spread_change",
                          "data_loss", "cardinality_explosion", "schema_event",
                          "pipeline_issue", "distribution_shift"):
                if cause.replace("_", " ") in detail.lower() or cause in detail.lower():
                    drift_cause = cause
                    break

        if "psi" in label and "—" in label:
            # "PSI = 0.312 (shift)..."
            import re
            m = re.search(r"PSI\s*=\s*([\d.]+)", detail)
            if m:
                psi_val = float(m.group(1))

        if "distribution centre" in label or "mean shift" in label:
            import re
            m = re.search(r"([\d.]+)\s*→\s*([\d.]+)\s*\(Δ\s*([+-]?[\d.]+)\)", detail)
            if m:
                mean_old   = float(m.group(1))
                mean_new   = float(m.group(2))
                mean_delta = float(m.group(3))

        if "where in the distribution" in label:
            import re
            # "Shift detected at: mean Δ +0.1234, median 0.45× IQR, Q1 0.12× IQR"
            m = re.search(r"mean\s*[Δδ]\s*([+-]?[\d.]+)", detail)
            if m:
                mean_delta = float(m.group(1))
            m = re.search(r"median\s*([\d.]+)×\s*IQR", detail)
            if m:
                median_shift = float(m.group(1))
            m = re.search(r"Q1\s*([\d.]+)×\s*IQR", detail)
            if m:
                q1_shift = float(m.group(1))
            m = re.search(r"Q3\s*([\d.]+)×\s*IQR", detail)
            if m:
                q3_shift = float(m.group(1))

        if "range expansion" in label or "value range" in label:
            import re
            m = re.search(r"upper.*?(\d+)%", detail)
            if m:
                upper_shift = int(m.group(1))
            m = re.search(r"lower.*?(\d+)%", detail)
            if m:
                lower_shift = int(m.group(1))

        if "spread change" in label:
            import re
            m = re.search(r"([\d.]+)\s*→\s*([\d.]+)", detail)
            if m:
                std_base = float(m.group(1))
                std_new  = float(m.group(2))

        if "cardinality" in label:
            import re
            m = re.search(r"(\d+)\s*→\s*(\d+)", detail)
            if m:
                from_card = int(m.group(1))
                to_card   = int(m.group(2))

        if "completeness" in label:
            import re
            m = re.search(r"pattern:\s*([\w_]+)", detail, re.IGNORECASE)
            if m:
                miss_pattern = m.group(1)

    # Build facts block — only include lines where we have actual values
    lines = [
        f"COLUMN: {col_name}",
        f"DRIFT_CAUSE: {drift_cause}",
        f"SEVERITY: {severity}",
        f"STAGE_CONTEXT: {_STAGE_FRAMING.get(stage, '')}",
        f"STAGE_RISK: {_STAGE_RISK_FRAMING.get(stage, '')}",
    ]

    if psi_val is not None:
        lines.append(f"PSI: {psi_val:.3f}")
    if mean_old is not None and mean_new is not None:
        lines.append(f"MEAN_SHIFT: {mean_old:.4f} → {mean_new:.4f} (Δ {mean_delta:+.4f})")
    if median_shift is not None:
        lines.append(f"MEDIAN_SHIFT_IQR: {median_shift:.2f}× IQR")
    if q1_shift is not None:
        lines.append(f"Q1_SHIFT_IQR: {q1_shift:.2f}× IQR")
    if q3_shift is not None:
        lines.append(f"Q3_SHIFT_IQR: {q3_shift:.2f}× IQR")
    if upper_shift is not None:
        lines.append(f"UPPER_BOUNDARY_EXPANSION: {upper_shift}% of base range")
    if lower_shift is not None:
        lines.append(f"LOWER_BOUNDARY_SHIFT: {lower_shift}% of base range")
    if std_base is not None and std_new is not None:
        lines.append(f"STD_CHANGE: {std_base:.4f} → {std_new:.4f}")
    if from_card is not None and to_card is not None:
        lines.append(f"CARDINALITY_CHANGE: {from_card} → {to_card} distinct values")
    if miss_pattern:
        lines.append(f"MISSINGNESS_PATTERN: {miss_pattern}")

    lines.append("")
    lines.append("Write 2 sentences about what this means for the customer portfolio.")

    return "\n".join(lines)


def _narrative_fallback(insight: dict) -> str:
    """
    Fallback = the rule-based headline already computed in business_insights.py.
    This is already a good sentence — LLM is additive, not replacing it.
    """
    return insight.get("headline", "")


def _drift_story_narrative(insight: dict, results: dict, stage: str) -> str:
    """
    One LLM call per drift story insight.
    Returns a 2-sentence business narrative or fallback on any failure.
    """
    # Skip if no real drift — data loss insights don't need business narration
    for ev in insight.get("evidence", []):
        if "data loss" in ev.get("detail", "").lower():
            return _narrative_fallback(insight)

    facts    = _build_drift_story_facts(insight, stage)
    fallback = _narrative_fallback(insight)

    return _call_with_fallback(
        user_prompt=facts,
        fallback=fallback,
        max_tokens=120,
        system_override=_DRIFT_STORY_SYSTEM,
    )


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED _call_with_fallback — add system_override parameter
# Replace the existing _call_with_fallback in llm_insights.py with this one
# ─────────────────────────────────────────────────────────────────────────────

def _call_with_fallback(
    user_prompt: str,
    fallback: str,
    max_tokens: int = None,
    system_override: str = None,
) -> str:
    """
    Call LLM with optional system prompt override and max_tokens cap.
    Falls back silently on any LLMError.
    """
    try:
        system = system_override if system_override else _SYSTEM
        kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        return call_llm(system, user_prompt, **kwargs)
    except LLMError:
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# Called from build_business_insights() after all 7 insights are built
# ─────────────────────────────────────────────────────────────────────────────

def enrich_drift_stories(
    insights: List[Dict],
    results:  Dict,
    stage:    str = "back_testing",
) -> List[Dict]:
    """
    Enrich the llm_narrative field of the 3 top drift story insights.
    One LLM call per drift story. All other slots are skipped.

    Each call is in its own try/except — one failure does not affect others.

    Args:
        insights : list of 7 insight dicts from build_business_insights()
        results  : full results dict (used for context only, not passed to LLM)
        stage    : stage string — changes urgency framing in prompt

    Returns:
        Same list with llm_narrative set on drift story insights.
        Empty string remains if LLM fails or insight has no real drift.
    """
    for insight in insights:
        slot = insight.get("slot", "")
        if not slot.startswith("drift_story"):
            continue
        # Skip stable placeholders
        if insight.get("severity") == "stable":
            continue
        try:
            narrative = _drift_story_narrative(insight, results, stage)
            insight["llm_narrative"] = narrative
        except Exception:
            pass   # keep empty string — never breaks the view

    return insights