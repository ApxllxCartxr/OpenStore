# OpenStore agents — LLM provider interface (S7.1)
# Model name from config key llm.model.
# NO payment/PSP/signing material here (R0.10).

from __future__ import annotations

import os
from typing import Any

from openstore.config import Settings


class LLMError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


# Provider implementations
_PROVIDERS: dict[str, type[LLMProvider]] = {}


def register_provider(name: str, cls: type[LLMProvider]) -> None:
    _PROVIDERS[name] = cls


class LLMProvider:
    """Base class for LLM providers."""

    def __init__(self, model: str, api_key: str | None = None, **kwargs: Any):
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider (includes OpenAI, Anthropic-compatible, etc.)."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(model, api_key, **kwargs)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        import httpx
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "messages": messages, **kwargs}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
            )
        if resp.status_code != 200:
            raise LLMError(f"OpenAI API error: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise LLMError(f"Unexpected LLM response shape: {type(content).__name__}")
        return content


class DummyProvider(LLMProvider):
    """Dummy provider for testing without network."""

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return '{"answer": "This is a test response from DummyProvider."}'


register_provider("openai", OpenAIProvider)
register_provider("dummy", DummyProvider)


def create_llm(config: Settings) -> LLMProvider:
    """Create LLM provider from config."""
    model = config.llm.model
    provider_name = os.getenv("LLM_PROVIDER", "dummy")
    api_key = os.getenv("OPENAI_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))

    if provider_name in _PROVIDERS:
        return _PROVIDERS[provider_name](model=model, api_key=api_key)

    if provider_name == "openai" or "gpt" in model or "claude" in model:
        return OpenAIProvider(model=model, api_key=api_key)

    return DummyProvider(model=model)


def llm_complete(config: Settings, prompt: str, **kwargs: Any) -> str:
    """Simple completion interface."""
    provider = create_llm(config)
    return provider.complete(prompt, **kwargs)


def llm_chat(config: Settings, messages: list[dict[str, str]], **kwargs: Any) -> str:
    """Chat interface."""
    provider = create_llm(config)
    return provider.chat(messages, **kwargs)
