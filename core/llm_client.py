"""Provider-agnostic LLM client. Swap providers via LLM_PROVIDER env var (anthropic | gemini)
with zero changes to callers — every caller only sees complete(system, user) -> str.
"""
import os
import time
import requests
from anthropic import Anthropic

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMClient:
    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    def __init__(self):
        self._client = Anthropic()
        self._model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")


class GeminiClient(LLMClient):
    def __init__(self):
        self._api_key = os.environ["GEMINI_API_KEY"]
        self._model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

    def complete(self, system: str, user: str) -> str:
        url = GEMINI_URL.format(model=self._model)
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.4},
        }

        last_error = None
        for attempt in range(3):
            response = requests.post(url, params={"key": self._api_key}, json=body, timeout=60)
            if response.status_code not in (429, 500, 502, 503, 504):
                break
            last_error = response
            time.sleep(2 ** attempt * 5)
        else:
            last_error.raise_for_status()

        response.raise_for_status()
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


_PROVIDERS = {"anthropic": AnthropicClient, "gemini": GeminiClient}
_client_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client_instance
    if _client_instance is None:
        provider = os.environ.get("LLM_PROVIDER", "anthropic")
        cls = _PROVIDERS.get(provider)
        if cls is None:
            raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Valid options: {', '.join(_PROVIDERS)}")
        _client_instance = cls()
    return _client_instance
