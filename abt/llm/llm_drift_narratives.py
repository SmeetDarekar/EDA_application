"""
abt/llm_drift_narratives.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 Decision Intelligence — LLM synthesis of ranked drift signals.
Orchestration hub that delegates to llm_signal_collector and llm_synthesis.
"""

# Re-expose from signal collector
from abt.llm.llm_signal_collector import (
    DOMAIN_LABELS,
    PURPOSE_LABELS,
    collect_all_signals,
)

# Re-expose from synthesis engine
from abt.llm.llm_synthesis import (
    synthesise_drift_insights,
    synthesise_drift_insights_v2,
    _fallback,
)