from typing import Dict, List
from abt.llm.llm_insights import _call_with_fallback

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
