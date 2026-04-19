"""Anthropic / Claude adapter.

Opt-in via `FRAMECRAFT_PROVIDER=anthropic`. Prompt caching uses inline
`cache_control: ephemeral` breakpoints on system blocks — no separate
cache-creation RPC, the cache is written automatically on first call.

See `.claude/plans/02-providers.md` §4.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from framecraft.providers.base import (
    LLMProvider,
    Message,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponse,
    default_model,
)

_MAX_TOKENS = 4096
_JSON_INSTRUCTION = (
    "\n\nRespond with a single JSON object matching the following schema. "
    "Output only the JSON — no prose, no code fences.\n\nSchema:\n"
)


class AnthropicProvider:
    """Claude adapter. Requires `ANTHROPIC_API_KEY`."""

    name: str = "anthropic"

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - install-time check
            raise ProviderAuthError(
                "anthropic SDK is not installed. Run: pip install anthropic"
            ) from e

        self._anthropic = anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderAuthError(
                "Set ANTHROPIC_API_KEY to use the Anthropic provider."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict | None = None,
        cache_segments: list[str] | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        t0 = time.perf_counter()
        chosen_model = model or default_model("director", self.name)

        system_blocks = self._build_system_blocks(system, schema, cache_segments)
        user_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
        ]

        try:
            response = self._client.messages.create(
                model=chosen_model,
                max_tokens=_MAX_TOKENS,
                temperature=0,
                system=system_blocks,
                messages=user_messages,
            )
        except Exception as e:
            self._raise_mapped(e)

        text = self._concat_text(response.content)
        parsed: dict | None = None
        if schema is not None:
            parsed = self._parse_json(text)

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        return ProviderResponse(
            text=text,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            provider=self.name,
            model=chosen_model,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _build_system_blocks(
        system: str,
        schema: dict | None,
        cache_segments: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Construct the `system` field as a list of content blocks.

        Strategy: each `cache_segment` becomes its own text block. The final
        trailing block carries the per-call `system` string (which may contain
        the retry error or schema instruction) and is the ONE that receives
        the `cache_control: ephemeral` breakpoint — so everything up to and
        including that block is cached on first call.

        If `schema` is supplied we append a "Respond with JSON…" tail to the
        last block so Claude emits structured output (no native JSON mode).
        """
        trailing = system
        if schema is not None:
            trailing = trailing + _JSON_INSTRUCTION + json.dumps(schema, ensure_ascii=False)

        blocks: list[dict[str, Any]] = []
        if cache_segments:
            for seg in cache_segments:
                blocks.append({"type": "text", "text": seg})
        blocks.append({
            "type": "text",
            "text": trailing,
            "cache_control": {"type": "ephemeral"},
        })
        return blocks

    @staticmethod
    def _concat_text(content: list[Any]) -> str:
        """Claude returns a list of content blocks; concatenate the text ones."""
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _parse_json(text: str) -> dict:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        return json.loads(stripped)

    def _raise_mapped(self, e: Exception) -> None:
        anthropic = self._anthropic
        if isinstance(e, anthropic.AuthenticationError):
            raise ProviderAuthError(f"Anthropic auth error: {e}") from e
        if isinstance(e, anthropic.RateLimitError):
            raise ProviderRateLimitError(f"Anthropic rate-limited: {e}") from e
        if isinstance(e, anthropic.APIConnectionError):
            raise ProviderNetworkError(f"Anthropic network error: {e}") from e
        # Other SDK errors propagate raw.
        raise
