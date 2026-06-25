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
ACTIVE_PROVIDER = "azure_openai"   # options: "anthropic" | "openai" | "azure_openai"

# ── Timeout for LLM calls (seconds) ──────────────────────────────────────────
LLM_TIMEOUT = 30
LLM_MAX_TOKENS = 600   # keep responses concise

# ── Provider definitions ──────────────────────────────────────────────────────
def _get_azure_url() -> str:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip('"').rstrip("'").rstrip("/")
    if endpoint.endswith("%22"):
        endpoint = endpoint[:-3]
    endpoint = endpoint.rstrip("/")
    if not endpoint:
        return ""
    if "/openai/deployments/" in endpoint:
        return endpoint
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview").strip()
    return f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"


def _get_azure_key() -> str:
    return os.environ.get("AZURE_OPENAI_KEY", "").strip().rstrip('"').rstrip("'")


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
        "api_url":  _get_azure_url(),
        "api_key":  _get_azure_key(),
        "model":    os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o").strip(),
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