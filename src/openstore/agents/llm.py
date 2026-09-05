# OpenStore agents — LLM provider interface (S7.1)
# Model name from config key llm.model.
# NO payment/PSP/signing material here (R0.10).

from __future__ import annotations

import os
from typing import Any

from dotenv import find_dotenv, load_dotenv

from openstore.config import Settings

# LLM provider keys live in .env.llm, deliberately separate from Settings'
# own .env: Settings is a pydantic-settings BaseSettings with extra="forbid"
# (R0.3), and any unmatched flat key in .env breaks every direct Settings(...)
# construction (not just load_config's YAML path) with a validation error.
# Loaded at import time so a bare os.getenv() below sees it; no-op (returns
# False) if the file doesn't exist — LLM_PROVIDER stays "dummy" by default.
load_dotenv(find_dotenv(".env.llm", usecwd=True))


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

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
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


class AnthropicProvider(LLMProvider):
    """Native Anthropic Messages API provider (distinct request/response shape from OpenAI)."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
        super().__init__(model, api_key, **kwargs)
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        import httpx

        system_text = None
        turns = []
        for msg in messages:
            if msg.get("role") == "system" and system_text is None:
                system_text = msg["content"]
            else:
                turns.append(msg)

        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.pop("max_tokens", 1024),
            "messages": turns,
            **kwargs,
        }
        if system_text is not None:
            body["system"] = system_text

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        if resp.status_code != 200:
            raise LLMError(f"Anthropic API error: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        try:
            content = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected Anthropic response shape: {e}") from e
        if not isinstance(content, str):
            raise LLMError(f"Unexpected LLM response shape: {type(content).__name__}")
        return content


class GroqProvider(OpenAIProvider):
    """Groq's chat completions API is OpenAI-compatible — same request/response
    shape as OpenAIProvider, different base_url + key."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
        super().__init__(
            model,
            api_key=api_key or os.getenv("GROQ_API_KEY"),
            base_url=base_url or os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            **kwargs,
        )


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter's chat completions API is OpenAI-compatible."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
        super().__init__(
            model,
            api_key=api_key or os.getenv("OPENROUTER_API_KEY"),
            base_url=base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            **kwargs,
        )


class GeminiProvider(OpenAIProvider):
    """Google's Gemini OpenAI-compatibility endpoint — same request/response
    shape as OpenAIProvider, different base_url + key (native Gemini
    generateContent has a different shape entirely; this uses the
    OpenAI-compatible surface Google publishes instead of a third shape)."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
        super().__init__(
            model,
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
            base_url=base_url
            or os.getenv(
                "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
            ),
            **kwargs,
        )


class NvidiaNIMProvider(OpenAIProvider):
    """NVIDIA NIM's hosted inference API (integrate.api.nvidia.com) is
    OpenAI-compatible — same request/response shape as OpenAIProvider,
    different base_url + key. Model ids are vendor-prefixed, e.g.
    "nvidia/nemotron-nano-3-30b-a3b", "deepseek-ai/deepseek-r1" (check
    build.nvidia.com for current ids — NIM's catalog turns over fast)."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None, **kwargs: Any
    ):
        super().__init__(
            model,
            api_key=api_key or os.getenv("NVIDIA_API_KEY"),
            base_url=base_url
            or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            **kwargs,
        )


class DummyProvider(LLMProvider):
    """Dummy provider for testing without network."""

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return '{"answer": "This is a test response from DummyProvider."}'


class AllProvidersFailedError(LLMError):
    """Every provider in a FailoverProvider chain failed."""


class FailoverProvider(LLMProvider):
    """Tries each provider in order, moving to the next on LLMError (provider
    down, rate-limited, transport failure, malformed response). Does NOT catch
    anything else — a bug inside a provider still fails loud immediately
    rather than being silently routed around. If every provider fails, raises
    AllProvidersFailedError carrying each provider's failure so the operator
    can tell which backends are down, not just that "the LLM" failed."""

    def __init__(self, providers: list[LLMProvider]):
        if not providers:
            raise ValueError("FailoverProvider requires at least one provider")
        super().__init__(model=providers[0].model)
        self.providers = providers

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        failures: list[str] = []
        for provider in self.providers:
            try:
                return provider.chat(messages, **kwargs)
            except LLMError as e:
                failures.append(f"{type(provider).__name__}({provider.model}): {e}")
        raise AllProvidersFailedError(
            f"All {len(self.providers)} provider(s) in chain failed: " + " | ".join(failures)
        )


register_provider("openai", OpenAIProvider)
register_provider("anthropic", AnthropicProvider)
register_provider("groq", GroqProvider)
register_provider("openrouter", OpenRouterProvider)
register_provider("gemini", GeminiProvider)
register_provider("nvidia", NvidiaNIMProvider)
register_provider("dummy", DummyProvider)

# Env var each registered provider reads its API key from, when create_llm
# doesn't pass one explicitly (only openai/anthropic are special-cased below;
# every other registered provider self-resolves its key via its own
# os.getenv(...) fallback in __init__, so an empty lookup here is harmless).
_PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


def _build_single_provider(provider_name: str, model: str) -> LLMProvider:
    """Construct one named provider. Raises ValueError for an unrecognized
    name — a typo in LLM_PROVIDER/LLM_PROVIDER_CHAIN is a config bug, not a
    runtime provider failure, so it must not silently fall through to Dummy."""
    if provider_name in _PROVIDERS:
        key_env = _PROVIDER_KEY_ENV.get(provider_name)
        api_key = os.getenv(key_env, "") if key_env else None
        return _PROVIDERS[provider_name](model=model, api_key=api_key)
    raise ValueError(f"Unknown LLM provider: {provider_name!r}")


def _parse_provider_chain(chain_spec: str, default_model: str) -> list[LLMProvider]:
    """Parses LLM_PROVIDER_CHAIN, e.g.
    "groq:llama-3.3-70b-versatile,openrouter,gemini:gemini-2.0-flash" — each
    entry is "provider" or "provider:model"; a bare "provider" uses
    config.llm.model."""
    providers = []
    for entry in chain_spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, model = entry.partition(":")
        providers.append(_build_single_provider(name.strip(), model.strip() or default_model))
    return providers


def create_llm(config: Settings) -> LLMProvider:
    """Create LLM provider from config.

    If LLM_PROVIDER_CHAIN is set (comma-separated "provider[:model]" entries),
    returns a FailoverProvider trying each in order — useful for routing
    around a provider that's down, out of quota, or rate-limiting, without
    silently falling back to non-LLM logic (R0.5's fail-loud requirement
    still applies once every provider in the chain has failed).

    Otherwise falls back to the single-provider LLM_PROVIDER selection
    (unchanged from before), defaulting to DummyProvider — no live calls
    without opting in.
    """
    model = config.llm.model

    chain_spec = os.getenv("LLM_PROVIDER_CHAIN", "")
    if chain_spec.strip():
        return FailoverProvider(_parse_provider_chain(chain_spec, model))

    provider_name = os.getenv("LLM_PROVIDER", "dummy")

    if provider_name in _PROVIDERS:
        return _build_single_provider(provider_name, model)

    if "claude" in model:
        return AnthropicProvider(model=model, api_key=os.getenv("ANTHROPIC_API_KEY", ""))

    if "gpt" in model:
        return OpenAIProvider(model=model, api_key=os.getenv("OPENAI_API_KEY", ""))

    return DummyProvider(model=model)


def llm_complete(config: Settings, prompt: str, **kwargs: Any) -> str:
    """Simple completion interface."""
    kwargs.setdefault("temperature", config.llm.temperature)
    provider = create_llm(config)
    return provider.complete(prompt, **kwargs)


def llm_chat(config: Settings, messages: list[dict[str, str]], **kwargs: Any) -> str:
    """Chat interface."""
    kwargs.setdefault("temperature", config.llm.temperature)
    provider = create_llm(config)
    return provider.chat(messages, **kwargs)
