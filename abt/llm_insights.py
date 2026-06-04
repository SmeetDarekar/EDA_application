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
    Modifies results dict in-place, returns it.
    """
    results["c0"]["narrative"] = _c0_narrative(results)
    results["c8"]["narrative"] = _c8_narrative(results["c8"])
    results["version_story"]   = _version_story(results)   # Feature 10
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _call_with_fallback(user_prompt: str, fallback: str, max_tokens: int = None) -> str:
    try:
        kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        return call_llm(_SYSTEM, user_prompt, **kwargs)
    except LLMError:
        return fallback


def _parse_numbered_response(text: str, expected: int) -> List[str]:
    """Parse '#1: ...\\n#2: ...' format into a list."""
    import re
    results = [""] * expected
    for match in re.finditer(r"#(\d+):\s*(.+?)(?=\n#\d+:|\Z)", text, re.DOTALL):
        idx = int(match.group(1)) - 1
        if 0 <= idx < expected:
            results[idx] = match.group(2).strip().replace("\n", " ")
    return results