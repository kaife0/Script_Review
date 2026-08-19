"""Provider-agnostic LLM client. Swap providers via LLM_PROVIDER env var
with zero changes to callers — every caller only sees complete(system, user) -> str.
"""
import os
from anthropic import Anthropic


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


_PROVIDERS = {"anthropic": AnthropicClient}
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
