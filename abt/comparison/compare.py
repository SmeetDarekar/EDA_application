"""
abt/compare.py  —  7-section comparison + insight layers for N ABT versions.
Orchestration hub delegating compare metrics and schema changes to sub-modules.
"""

from typing import Dict, List, Optional
from abt.analysis.columnProfile import ABTProfile
from abt.comparison.drift_metrics import compute_column_drift_suite

# Re-expose from sub-modules
from abt.comparison.compare_schema import (
    c1_version_summary,
    c2_schema_changes,
    c10_cardinality_drift,
)

from abt.comparison.compare_distribution import (
    c3_completeness_drift,
    c4_distribution_drift,
    c5_target_drift,
    c6_quality_regression,
    c7_readiness_change,
    c8_psi_matrix,
    c9_health_score_trend,
    c0_compare_verdict,
)


def run_comparison(abts: List[ABTProfile], use_llm: bool = True,
                    domain: str = "credit_risk", abt_purpose: str = "pd",
                    stage: str = "back_testing", cfg=None) -> Dict:
    """Run all compare sections for N versions."""
    c1 = c1_version_summary(abts)
    c5 = c5_target_drift(abts)
    c8 = c8_psi_matrix(abts)
    c9 = c9_health_score_trend(abts)
    c10 = c10_cardinality_drift(abts)
    try:
        drift_suite = compute_column_drift_suite(abts)
    except Exception:
        drift_suite = {}
    results = {
        "c0":  c0_compare_verdict(c5, c8, c9, c1),
        "c1":  c1,
        "c2":  c2_schema_changes(abts),
        "c3":  c3_completeness_drift(abts),
        "c4":  c4_distribution_drift(abts),
        "c5":  c5,
        "c6":  c6_quality_regression(abts),
        "c7":  c7_readiness_change(abts),
        "c8":  c8,
        "c9":  c9,
        "c10": c10,
        "drift_suite": drift_suite,
        # Inject context early so LLM block and signal_collector_v2 can read them
        "domain":      domain,
        "abt_purpose": abt_purpose,
        "stage":       stage,
    }
    # ── Interpretation layer (i4–i9) ────────────────────────────────────────
    try:
        from abt.interpretations.interpretations import (
            i4_population_shift,
            i5_target_stability,
            i6_feature_drift_impact,
            i7_model_action,
            i8_pipeline_break_risks,
            i9_pipeline_health,
        )
        # i5 and i6 must run before i7 — i7 reads their output
        i5 = i5_target_stability(results["c5"], results["c3"], results["c6"])
        i6 = i6_feature_drift_impact(results["c3"], results["c4"], results["c8"], drift_suite)
        i4 = i4_population_shift(results["c1"], results["c4"], results["c9"], drift_suite)
        i7 = i7_model_action(results["c0"], i5, i6, drift_suite)
        i8 = i8_pipeline_break_risks(results["c2"], results["c8"], results["c10"], drift_suite)
        i9 = i9_pipeline_health(results["c3"], results["c6"], results["c9"])
        results["i4"] = i4
        results["i5"] = i5
        results["i6"] = i6
        results["i7"] = i7
        results["i8"] = i8
        results["i9"] = i9
    except Exception:
        pass  # interpretation layer is always optional, never breaks existing results

    if use_llm:
        # ── Phase B: prompt-chained drift insight cards (v2) ────────────────
        try:
            from abt.llm.llm_drift_narratives import synthesise_drift_insights_v2
            results["drift_insights"] = synthesise_drift_insights_v2(
                results,
                domain      = domain,
                abt_purpose = abt_purpose,
                stage       = stage,
            )
        except Exception:
            # v1 fallback — original single-call approach preserved
            try:
                from abt.llm.llm_drift_narratives import collect_all_signals, _fallback
                from abt.llm.llm_drift_narratives import DOMAIN_LABELS, PURPOSE_LABELS
                signals = collect_all_signals(results)
                results["drift_insights"] = _fallback(
                    signals, domain, abt_purpose,
                    DOMAIN_LABELS.get(domain, domain),
                    PURPOSE_LABELS.get(abt_purpose, abt_purpose),
                    reason="LLM enrichment skipped",
                    max_cards=5,
                )
            except Exception:
                pass

        # ── Existing narrative enrichment (C0/C8/version story) ─────────────
        try:
            from abt.llm.llm_insights import enrich_compare
            results = enrich_compare(results)
        except Exception:
            pass  # LLM enrichment is always optional

    else:
        # Even without LLM, build rule-based fallback cards
        try:
            from abt.llm.llm_drift_narratives import collect_all_signals, _fallback
            from abt.llm.llm_drift_narratives import DOMAIN_LABELS, PURPOSE_LABELS
            signals = collect_all_signals(results)
            results["drift_insights"] = _fallback(
                signals, domain, abt_purpose,
                DOMAIN_LABELS.get(domain, domain),
                PURPOSE_LABELS.get(abt_purpose, abt_purpose),
                reason="LLM not requested",
                max_cards=5,
            )
        except Exception:
            pass

    return results