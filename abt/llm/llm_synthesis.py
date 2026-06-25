from __future__ import annotations
import json
import re
from typing import Dict, List, Optional, Any

from abt.llm.llm_client import call_llm, LLMError

# Import theme builder helpers
from abt.llm.llm_theme_builder import (
    group_signals_by_theme,
    _theme_to_domain,
)

# Import prompts
from abt.llm.llm_prompts import (
    _SYSTEM_PROMPT,
    _TRIAGE_SYSTEM,
    _CARD_SYSTEM,
    _META_SYSTEM,
)

# Import domain/purpose configurations and collectors
from abt.llm.llm_signal_collector import (
    DOMAIN_LABELS,
    PURPOSE_LABELS,
    collect_all_signals,
)


class _ValidationError(Exception):
    pass

_REQUIRED_CARD_KEYS = {"rank", "domain", "headline", "evidence", "action", "severity", "source"}
_VALID_SEVERITIES   = {"critical", "high", "medium", "low"}
_VALID_DOMAINS      = {"population", "target", "feature", "pipeline", "governance"}


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
        from abt.insights.signal_collector import collect_signals_v2

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
