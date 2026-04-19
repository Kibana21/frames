"""Provider registry and factory. See `.claude/plans/02-providers.md`."""

from __future__ import annotations

import os
from pathlib import Path

from framecraft.providers.base import (
    CacheMissError,
    LLMProvider,
    Message,
    ProviderAuthError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponse,
    Role,
    cache_key,
    default_model,
)
from framecraft.providers.stub import StubProvider


def make_provider(
    name: str | None = None,
    *,
    fixture_dir: Path | None = None,
) -> LLMProvider:
    """Return a provider instance.

    Order of precedence: explicit `name` > FRAMECRAFT_PROVIDER env > "gemini".
    """
    resolved = name or os.environ.get("FRAMECRAFT_PROVIDER", "gemini")

    if resolved == "gemini":
        from framecraft.providers.gemini import GeminiProvider
        return GeminiProvider()

    if resolved == "anthropic":
        from framecraft.providers.anthropic import AnthropicProvider
        return AnthropicProvider()

    if resolved in {"stub", "stub:gemini"}:
        return StubProvider(
            fixture_dir or Path("tests/fixtures/llm/gemini"),
            "stub:gemini",
        )
    if resolved == "stub:anthropic":
        return StubProvider(
            fixture_dir or Path("tests/fixtures/llm/anthropic"),
            "stub:anthropic",
        )

    raise ValueError(f"Unknown FRAMECRAFT_PROVIDER={resolved!r}")


__all__ = [
    "CacheMissError",
    "LLMProvider",
    "Message",
    "ProviderAuthError",
    "ProviderError",
    "ProviderNetworkError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "Role",
    "StubProvider",
    "cache_key",
    "default_model",
    "make_provider",
]
