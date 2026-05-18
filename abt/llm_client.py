"""
abt/llm_client.py
─────────────────────────────────────────────────────────────────────────────
Thin HTTP wrapper around any configured LLM provider.
Business logic NEVER imports this directly — use llm_insights.py instead.
"""

import json
import urllib.request
import urllib.error
from .llm_config import get_provider, LLM_TIMEOUT, LLM_MAX_TOKENS


def call_llm(system: str, user: str, max_tokens: int = LLM_MAX_TOKENS) -> str:
    """
    Call the active LLM provider. Returns text string.
    Raises LLMError on any failure — callers must handle gracefully.
    """
    p = get_provider()

    api_key = p["api_key"]
    if not api_key:
        raise LLMError(f"No API key set for provider. "
                       f"Check the env var referenced in llm_config.py.")

    headers = p["headers"](api_key)
    body    = p["body"](system, user, max_tokens, p["model"])
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        p["api_url"],
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            text = p["parse"](raw)
            if not text:
                raise LLMError("LLM returned empty response")
            return text.strip()

    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise LLMError(f"HTTP {e.code}: {body_text[:200]}")
    except urllib.error.URLError as e:
        raise LLMError(f"Network error: {e.reason}")
    except Exception as e:
        raise LLMError(f"Unexpected error: {e}")


class LLMError(Exception):
    pass