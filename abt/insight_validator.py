"""
abt/insight_validator.py
─────────────────────────────────────────────────────────────────────────────
Validator Layer — runs after business_insights.py, before final output.

Two-pass approach:

  Pass 1 — Hard rules (no LLM, always runs)
    Rule 1: "drop" never applied to private/protected columns
    Rule 2: data-loss insights never recommend model action
    Rule 3: drift story action must not exceed I7 decision
    Rule 4: no action stronger than verdict ceiling
    Rule 5: governance insights always require sign-off language
    Rule 6: BLOCK verdict → no model action, pipeline fix only

  Pass 2 — LLM review (optional, only if hard rule fired)
    LLM receives: exact rule that fired + computed facts + allowed action range
    LLM must output: structured JSON with is_correct flag + replacement if wrong
    Falls back to Pass 1 result on any LLM error or malformed response

Anti-hallucination design:
    - LLM receives no free-text context it could embellish
    - LLM receives only computed numbers (PSI, decision, verdict, drift_cause)
    - LLM output is parsed as JSON — free text response is rejected
    - LLM is told exactly what the allowed actions are — no open-ended generation
    - If LLM says is_correct=true, original is kept unchanged
    - If LLM says is_correct=false, replacement field is used verbatim
    - max_tokens=150 — prevents rambling
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import json
from typing import Dict, List, Optional


# ── I7 decision hierarchy ─────────────────────────────────────────────────
_DECISION_RANK = {
    "retrain":     4,
    "rebin":       3,
    "recalibrate": 2,
    "hold":        1,
}

# ── Verdict → maximum allowed model action ────────────────────────────────
_VERDICT_CEILING = {
    "CLEAR":              "hold",
    "MONITOR":            "recalibrate",
    "BACK_TEST_REQUIRED": "rebin",
    "BLOCK":              "hold",   # BLOCK = pipeline fix first, no model action
}

# ── Allowed action text per decision level ────────────────────────────────
_ALLOWED_ACTIONS = {
    "hold":        "Monitor this feature in the next version cycle. No model action required.",
    "recalibrate": "Recalibrate the decision threshold on the latest validation set.",
    "rebin":       "Refit WoE bins for this column on the latest version data. "
                   "Back-test before scoring.",
    "retrain":     "Retrain the model on the latest version data after rebinning. "
                   "Validate on a held-out sample from the latest version.",
}

_PIPELINE_FIX_ACTION = (
    "Fix the data pipeline to restore completeness. "
    "Re-run comparison after the fix — model action may not be required."
)

_GOVERNANCE_PREFIX = (
    "Obtain governance sign-off before any model promotion. "
)


def _g(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _action_rank(text: str) -> int:
    """Infer the strongest action implied by a text string."""
    t = text.lower()
    if any(w in t for w in ("retrain", "full retrain", "train a new model")):
        return 4
    if any(w in t for w in ("rebin", "refit woe", "refit bins")):
        return 3
    if any(w in t for w in ("recalibrate", "recalibration", "threshold")):
        return 2
    return 1


def _has_data_loss(insight: dict) -> bool:
    return any(
        "data loss" in ev.get("detail", "").lower() or
        "pipeline" in ev.get("label",  "").lower() or
        "missing"  in ev.get("detail", "").lower() and
        "pipeline" in ev.get("detail", "").lower()
        for ev in insight.get("evidence", [])
    )


def _is_private_insight(insight: dict) -> bool:
    return any(
        "private" in ev.get("label", "").lower()
        for ev in insight.get("evidence", [])
    )


# ─────────────────────────────────────────────────────────────────────────────
# PASS 1 — HARD RULES
# ─────────────────────────────────────────────────────────────────────────────

def _hard_rule_check(insight: dict, results: dict) -> dict:
    """
    Applies 6 hard rules to one insight.
    Returns insight unchanged if all rules pass.
    Returns shallow copy with corrected impact_and_action + validation_note if any rule fires.
    """
    slot          = insight.get("slot", "")
    impact_action = insight.get("impact_and_action", "")
    is_private    = _is_private_insight(insight)
    has_loss      = _has_data_loss(insight)

    i7_decision = _g(results, "i7", "decision", default="hold")
    c0_verdict  = _g(results, "c0", "verdict",  default="CLEAR")
    max_allowed = _VERDICT_CEILING.get(c0_verdict, "recalibrate")
    max_rank    = _DECISION_RANK.get(max_allowed, 2)
    i7_rank     = _DECISION_RANK.get(i7_decision, 1)
    action_rank = _action_rank(impact_action)

    fired_rules   = []
    new_action    = impact_action

    # ── Rule 1: No "drop" for private columns ─────────────────────────────
    if is_private and "drop" in impact_action.lower():
        new_action = new_action.replace(
            "drop", "review with the governance team before taking any action on"
        )
        fired_rules.append(
            "Rule 1 [PRIVATE_DROP_BLOCKED]: 'drop' replaced with governance review — "
            "private attributes cannot be dropped without regulatory sign-off."
        )

    # ── Rule 2: Data-loss → no model action ───────────────────────────────
    if has_loss and action_rank >= 2:
        new_action = _PIPELINE_FIX_ACTION
        fired_rules.append(
            "Rule 2 [DATA_LOSS_MODEL_ACTION_BLOCKED]: model action removed — "
            "drift is caused by missing data, not population change. "
            "Pipeline fix must precede any model decision."
        )

    # ── Rule 3: Drift story action must not exceed I7 decision ────────────
    if slot.startswith("drift_story") and action_rank > i7_rank:
        new_action = _ALLOWED_ACTIONS.get(i7_decision, _ALLOWED_ACTIONS["hold"])
        fired_rules.append(
            f"Rule 3 [DRIFT_STORY_EXCEEDS_I7]: action downgraded from rank {action_rank} "
            f"to '{i7_decision}' (rank {i7_rank}) — individual drift story cannot recommend "
            f"stronger action than the overall I7 model decision."
        )

    # ── Rule 4: No action stronger than verdict ceiling ───────────────────
    # Re-check after potential Rule 3 correction
    current_rank = _action_rank(new_action)
    if current_rank > max_rank:
        new_action = _ALLOWED_ACTIONS.get(max_allowed, _ALLOWED_ACTIONS["hold"])
        fired_rules.append(
            f"Rule 4 [EXCEEDS_VERDICT_CEILING]: action downgraded to '{max_allowed}' — "
            f"verdict is '{c0_verdict}', maximum allowed action is '{max_allowed}'."
        )

    # ── Rule 5: Governance slot requires sign-off language ────────────────
    if slot == "governance_fairness" and "sign-off" not in new_action.lower():
        new_action = _GOVERNANCE_PREFIX + new_action
        fired_rules.append(
            "Rule 5 [GOVERNANCE_SIGNOFF_MISSING]: sign-off language prepended — "
            "mandatory for all private attribute findings."
        )

    # ── Rule 6: BLOCK verdict → pipeline fix only, no model action ────────
    if c0_verdict == "BLOCK" and action_rank >= 2:
        new_action = (
            "Dataset readiness is below the minimum threshold. "
            "Fix data quality issues first — do not retrain, rebin, or recalibrate "
            "until the dataset scores above 45/100."
        )
        fired_rules.append(
            "Rule 6 [BLOCK_VERDICT_MODEL_ACTION_BLOCKED]: all model actions removed — "
            "BLOCK verdict means dataset is not ready for any model operation."
        )

    if fired_rules:
        insight = dict(insight)
        insight["impact_and_action"] = new_action
        insight["validation_note"]   = " | ".join(fired_rules)
        insight["validation_passed"] = False
    else:
        insight["validation_passed"] = True

    return insight


# ─────────────────────────────────────────────────────────────────────────────
# PASS 2 — LLM REVIEW
#
# Anti-hallucination design:
#   - LLM receives structured facts only — no narrative it can embellish
#   - LLM must respond in strict JSON: {"is_correct": bool, "replacement": str}
#   - "replacement" is only used if is_correct=false
#   - replacement is capped at 2 sentences via max_tokens=150
#   - LLM is given the exact allowed action range — no open-ended generation
#   - Any JSON parse failure or missing fields → fall back to Pass 1 result
# ─────────────────────────────────────────────────────────────────────────────

_VALIDATOR_SYSTEM = """You are a risk model validation engine.
You receive structured facts about a model monitoring insight.
You must decide if the recommended action is correct given the constraints.

Output format — respond ONLY with valid JSON, no other text:
{"is_correct": true}
OR
{"is_correct": false, "replacement": "<corrected action in 1-2 sentences>"}

Constraints you must enforce:
- Action must match the I7_DECISION level exactly.
- Action must not exceed VERDICT_CEILING.
- If DRIFT_CAUSE is data_loss: action must be pipeline fix, not model action.
- If IS_PRIVATE is true: action must not say "drop".
- "replacement" must use only these verbs: retrain, rebin, recalibrate, monitor.
- "replacement" must be 1-2 sentences maximum.
- "replacement" must reference the COLUMN_NAME specifically.
- Do not add any explanation, preamble, or markdown."""


def _build_llm_facts(insight: dict, results: dict) -> str:
    """
    Build a structured facts block for the LLM.
    Every field is computed — no free text from insight narrative.
    """
    slot         = insight.get("slot", "")
    i7_decision  = _g(results, "i7", "decision", default="hold")
    c0_verdict   = _g(results, "c0", "verdict",  default="CLEAR")
    max_allowed  = _VERDICT_CEILING.get(c0_verdict, "recalibrate")
    stage        = results.get("_stage", "back_testing")
    val_note     = insight.get("validation_note", "")

    # Extract column name from first evidence row label
    evidence     = insight.get("evidence", [])
    col_name     = "unknown"
    for ev in evidence:
        label = ev.get("label", "")
        if " — " in label:
            col_name = label.split(" — ")[-1].strip()
            break

    # Extract drift cause from evidence
    drift_cause = "unknown"
    for ev in evidence:
        detail = ev.get("detail", "").lower()
        for cause in ("center_shift", "boundary_expansion", "spread_change",
                      "data_loss", "cardinality_explosion", "schema_event",
                      "distribution_shift", "pipeline_issue"):
            if cause.replace("_", " ") in detail or cause in detail:
                drift_cause = cause
                break

    is_private   = _is_private_insight(insight)
    current_action = insight.get("impact_and_action", "")
    allowed_action = _ALLOWED_ACTIONS.get(max_allowed, _ALLOWED_ACTIONS["hold"])

    return f"""SLOT: {slot}
COLUMN_NAME: {col_name}
DRIFT_CAUSE: {drift_cause}
IS_PRIVATE: {is_private}
I7_DECISION: {i7_decision}
VERDICT_CEILING: {max_allowed}
STAGE: {stage}
RULE_FIRED: {val_note}
CURRENT_ACTION: {current_action}
ALLOWED_ACTION_FOR_CEILING: {allowed_action}

Is CURRENT_ACTION correct given these constraints?"""


def _llm_review(insight: dict, results: dict) -> dict:
    """
    Ask LLM to verify and optionally replace the action.
    Structured JSON response only — no free text accepted.
    Falls back to Pass 1 result on any error or malformed response.
    """
    try:
        from .llm_client import call_llm, LLMError

        facts    = _build_llm_facts(insight, results)
        response = call_llm(
            _VALIDATOR_SYSTEM,
            facts,
            max_tokens=150,
        )

        if not response:
            return insight

        # Strip any markdown fences if LLM added them despite instructions
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        parsed = json.loads(clean)

        is_correct  = parsed.get("is_correct", True)
        replacement = parsed.get("replacement", "").strip()

        if is_correct:
            # LLM agrees — keep Pass 1 result unchanged
            return insight

        if not replacement:
            # LLM flagged as wrong but gave no replacement — keep Pass 1
            return insight

        # Sanity check: replacement must not be longer than 2 sentences
        sentences = [s.strip() for s in replacement.split(".") if s.strip()]
        if len(sentences) > 3:
            # Truncate to 2 sentences
            replacement = ". ".join(sentences[:2]) + "."

        insight = dict(insight)
        insight["impact_and_action"] = replacement
        insight["validation_note"]   = (
            insight.get("validation_note", "") +
            " | LLM flagged as incorrect and provided replacement."
        )
        insight["llm_reviewed"] = True

    except (json.JSONDecodeError, KeyError, TypeError):
        # Malformed JSON from LLM — keep Pass 1 result
        pass
    except Exception:
        # Any other error — keep Pass 1 result
        pass

    return insight


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def validate_insights(
    insights: List[dict],
    results:  dict,
    use_llm:  bool = False,
) -> List[dict]:
    """
    Validate and correct a list of 7 business insights.

    Pass 1 (hard rules) always runs — fast, deterministic.
    Pass 2 (LLM review) runs only when:
        - use_llm is True
        - AND that insight failed at least one hard rule (validation_passed=False)

    Args:
        insights : output of build_business_insights()
        results  : full results dict (requires i7, c0 keys)
        use_llm  : whether to apply LLM review on flagged insights

    Returns:
        Same-length list. Corrected insights carry:
            validation_passed : False
            validation_note   : which rules fired and why
            llm_reviewed      : True if LLM also reviewed (optional field)
    """
    validated = []

    for insight in insights:
        # Pass 1 — always
        checked = _hard_rule_check(insight, results)

        # Pass 2 — only on failures, only if LLM enabled
        if use_llm and not checked.get("validation_passed", True):
            checked = _llm_review(checked, results)

        validated.append(checked)

    return validated