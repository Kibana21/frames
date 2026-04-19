"""Deterministic fixture-backed provider for tests and --dry-run. See 02-providers.md."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from framecraft.providers.base import (
    CacheMissError,
    Message,
    ProviderResponse,
)


class StubProvider:
    """Reads pre-recorded responses from a fixture directory.

    Fails loud on cache miss — never falls back to network. Fixture files
    are named `<hash>.json` where hash is a stable function of the request.
    """

    name: str

    def __init__(self, fixture_dir: Path, provider_name: str = "stub") -> None:
        self.fixture_dir = fixture_dir
        self.name = provider_name

    def complete(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict | None = None,
        cache_segments: list[str] | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        key = self._fixture_key(messages=messages, system=system, schema=schema)
        path = self.fixture_dir / f"{key}.json"
        if not path.exists():
            raise CacheMissError(
                f"No fixture for {self.name}:{key} at {path}. "
                "Record with `python scripts/record_fixture.py`."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProviderResponse(
            text=data["text"],
            parsed=data.get("parsed"),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_write_tokens=data.get("cache_write_tokens", 0),
            provider=data.get("provider", self.name),
            model=data.get("model", model or "stub"),
            elapsed_ms=data.get("elapsed_ms", 0),
        )

    def _fixture_key(
        self,
        *,
        messages: list[Message],
        system: str,
        schema: dict | None,
    ) -> str:
        payload = json.dumps(
            {
                "provider": self.name,
                "system": system,
                "messages": messages,
                "schema": schema or {},
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
