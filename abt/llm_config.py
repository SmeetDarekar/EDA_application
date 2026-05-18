"""
abt/llm_config.py
─────────────────────────────────────────────────────────────────────────────
LLM provider configuration. To switch providers, change ACTIVE_PROVIDER
and add a new entry in PROVIDERS.

Each provider entry needs:
  - api_url   : full endpoint URL
  - api_key   : env var name that holds the key
  - model     : model string
  - headers   : callable(api_key) → dict of HTTP headers
  - body      : callable(system, user, max_tokens) → dict payload
  - parse     : callable(response_json) → str (extracts text from response)
"""

import os

# ── Switch provider here ──────────────────────────────────────────────────────
ACTIVE_PROVIDER = "openai"   # options: "anthropic" | "openai" | "azure_openai"

# ── Timeout for LLM calls (seconds) ──────────────────────────────────────────
LLM_TIMEOUT = 30
LLM_MAX_TOKENS = 600   # keep responses concise

# ── Provider definitions ──────────────────────────────────────────────────────
PROVIDERS = {
    "anthropic": {
        "api_url":  "https://api.anthropic.com/v1/messages",
        "api_key":  os.environ.get("ANTHROPIC_API_KEY", ""),
        "model":    "claude-sonnet-4-20250514",
        "headers":  lambda key: {
            "Content-Type":      "application/json",
            "x-api-key":         key,
            "anthropic-version": "2023-06-01",
        },
        "body": lambda system, user, max_tokens, model: {
            "model":      model,
            "max_tokens": max_tokens,
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
        },
        "parse": lambda r: (r.get("content") or [{}])[0].get("text", ""),
    },

    "openai": {
        "api_url":  "https://api.openai.com/v1/chat/completions",
        "api_key":  "",
        "model":    "gpt-4o",
        "headers":  lambda key: {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {key}",
        },
        "body": lambda system, user, max_tokens, model: {
            "model":      model,
            "max_tokens": max_tokens,
            "messages":   [
                {"role": "system",  "content": system},
                {"role": "user",    "content": user},
            ],
        },
        "parse": lambda r: (((r.get("choices") or [{}])[0]).get("message") or {}).get("content", ""),
    },

    "azure_openai": {
        # Set AZURE_OPENAI_ENDPOINT as full URL incl. deployment name
        "api_url":  os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        "api_key":  os.environ.get("AZURE_OPENAI_KEY", ""),
        "model":    os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        "headers":  lambda key: {
            "Content-Type": "application/json",
            "api-key":      key,
        },
        "body": lambda system, user, max_tokens, model: {
            "max_tokens": max_tokens,
            "messages":   [
                {"role": "system",  "content": system},
                {"role": "user",    "content": user},
            ],
        },
        "parse": lambda r: (((r.get("choices") or [{}])[0]).get("message") or {}).get("content", ""),
    },
}


def get_provider() -> dict:
    p = PROVIDERS.get(ACTIVE_PROVIDER)
    if not p:
        raise ValueError(f"Unknown LLM provider: '{ACTIVE_PROVIDER}'. "
                         f"Valid options: {list(PROVIDERS.keys())}")
    return p