"""LLMProvider protocol and response types. See `.claude/plans/02-providers.md`."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

Role = Literal["director", "assembler"]


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    parsed: dict | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    provider: str
    model: str
    elapsed_ms: int


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict | None = None,
        cache_segments: list[str] | None = None,
        model: str | None = None,
    ) -> ProviderResponse: ...


class ProviderError(Exception):
    """Base for all provider-originating failures. CLI maps to exit 4."""


class ProviderAuthError(ProviderError):
    """Missing or invalid API key."""


class ProviderRateLimitError(ProviderError):
    """HTTP 429 or equivalent."""


class ProviderNetworkError(ProviderError):
    """Connection or timeout failure."""


class CacheMissError(ProviderError):
    """Stub provider lacks a recorded fixture for the requested call."""


def cache_key(
    cache_segments: list[str] | None,
    model_id: str,
    schema_hash: str,
) -> str:
    """Stable key across providers and processes."""
    parts = [model_id, schema_hash]
    if cache_segments:
        parts.append("\n---\n".join(cache_segments))
    return hashlib.sha256("\n===\n".join(parts).encode("utf-8")).hexdigest()


# Per-provider model defaults. `provider` here is the short family name
# (everything after a colon, so "stub:gemini" → "gemini"). Add new providers
# here when they land; overrides via FRAMECRAFT_{ROLE}_MODEL env vars.
_DEFAULTS: dict[str, dict[Role, str]] = {
    "gemini": {"director": "gemini-2.5-pro", "assembler": "gemini-2.5-flash"},
    "anthropic": {"director": "claude-opus-4-7", "assembler": "claude-sonnet-4-6"},
    "stub": {"director": "stub-director", "assembler": "stub-assembler"},
}


def default_model(role: Role, provider: str) -> str:
    """Resolve the model id a caller should use for this role+provider.

    Order: FRAMECRAFT_{ROLE}_MODEL env → per-provider default → generic
    placeholder. Unknown providers fall through to the placeholder so custom
    or test-only adapters keep working; real adapters should register their
    defaults in `_DEFAULTS`.
    """
    override = os.environ.get(f"FRAMECRAFT_{role.upper()}_MODEL")
    if override:
        return override
    family = provider.split(":")[-1]
    if family in _DEFAULTS:
        return _DEFAULTS[family][role]
    return f"{family}-{role}-default"
