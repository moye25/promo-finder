"""One tiny function, many models. Swap the model with --provider/--model.

All providers return the raw text of the model's reply. Anything that speaks
the OpenAI chat-completions format (Gemini, Groq, OpenRouter, Ollama, OpenAI,
or a Kuku-internal gateway) is one entry in PROVIDERS.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

PROVIDERS = {
    # name: (base_url, env var holding the key, default model)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1:8b"),
    # any other OpenAI-compatible endpoint: --provider custom, set LLM_BASE_URL / LLM_API_KEY
    "custom": (os.environ.get("LLM_BASE_URL", ""), "LLM_API_KEY", os.environ.get("LLM_MODEL", "")),
}


class LLM:
    def __init__(self, provider: str, model: str | None = None, temperature: float = 0.2, min_interval: float = 0.0,
                 api_key: str | None = None):
        self.provider = provider
        self.temperature = temperature
        self.min_interval = min_interval  # seconds between calls, to respect free-tier rate limits
        self._last = 0.0
        if provider == "anthropic":
            self.model = model or "claude-sonnet-4-5"
            self.key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        else:
            base, env, default = PROVIDERS[provider]
            self.base, self.model = base.rstrip("/"), model or default
            self.key = api_key or (os.environ.get(env) if env else "none")
        if not self.key:
            raise SystemExit(f"Set the API key env var for provider '{provider}' first.")

    def complete(self, system: str, user: str, retries: int = 5) -> str:
        for attempt in range(retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                return self._call(system, user)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(min(60, 5 * 2 ** attempt))  # back off on rate limits
                    continue
                raise
        raise RuntimeError("unreachable")

    def _call(self, system: str, user: str) -> str:
        if self.provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 4000, "temperature": self.temperature,
                      "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=180,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        r = requests.post(f"{self.base}/chat/completions", headers={"Authorization": f"Bearer {self.key}"},
                          json=body, timeout=180)
        if r.status_code == 400 and "response_format" in r.text:  # some models don't support JSON mode
            body.pop("response_format")
            r = requests.post(f"{self.base}/chat/completions", headers={"Authorization": f"Bearer {self.key}"},
                              json=body, timeout=180)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in ``` fences or add chatter; pull out the object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
