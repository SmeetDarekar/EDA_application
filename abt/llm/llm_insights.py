"""
abt/llm_insights.py
─────────────────────────────────────────────────────────────────────────────
Business logic layer for LLM enrichment orchestration.
Re-exposes entry points and delegates narrative generation to sub-modules.
"""

from typing import Dict, List, Optional
from abt.llm.llm_client import call_llm, LLMError

# ── Shared system prompt ────────────────────────────────────────────────────
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


def _parse_numbered_response(text: str, expected: int) -> List[str]:
    """Parse '#1: ...\n#2: ...' format into a list."""
    import re
    results = [""] * expected
    for match in re.finditer(r"#(\d+):\s*(.+?)(?=\n#\d+:|\Z)", text, re.DOTALL):
        idx = int(match.group(1)) - 1
        if 0 <= idx < expected:
            results[idx] = match.group(2).strip().replace("\n", " ")
    return results


# ── Entry Points — Delegating to Sub-modules ───────────────────────────────

def enrich_analyze(results: Dict) -> Dict:
    """
    Adds LLM-generated narrative to analyze results.
    Modifies results dict in-place, returns it.
    """
    from abt.llm.llm_insights_analyze import enrich_analyze as _enrich
    return _enrich(results)


def enrich_compare(results: Dict) -> Dict:
    """
    Adds LLM-generated narrative to compare results.
    Modifies results dict in-place, returns it.
    """
    from abt.llm.llm_insights_compare import enrich_compare as _enrich
    return _enrich(results)


def enrich_drift_stories(
    insights: List[Dict],
    results:  Dict,
    stage:    str = "back_testing",
) -> List[Dict]:
    """
    Enrich the llm_narrative field of the 3 top drift story insights.
    """
    from abt.llm.llm_insights_stories import enrich_drift_stories as _enrich
    return _enrich(insights, results, stage)