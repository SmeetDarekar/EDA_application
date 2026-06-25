from typing import Dict, List
from abt.llm.llm_insights import _SYSTEM, _call_with_fallback, _parse_numbered_response

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
        from abt.llm.llm_client import call_llm
        response = call_llm(_SYSTEM, user_prompt, max_tokens=800)
        parsed   = _parse_numbered_response(response, len(actions))
        for i, a in enumerate(actions):
            if i < len(parsed) and parsed[i]:
                a["why_llm"] = parsed[i]
            else:
                a["why_llm"] = a["why"]
    except Exception:
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
