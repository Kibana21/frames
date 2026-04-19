"""02 provider tests — stub, Gemini (mocked), Anthropic (mocked), defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from framecraft.providers import (
    CacheMissError,
    ProviderAuthError,
    ProviderRateLimitError,
    StubProvider,
    cache_key,
    default_model,
    make_provider,
)


# =============================================================================
# Stub
# =============================================================================


def test_stub_cache_miss_raises(tmp_path: Path) -> None:
    p = StubProvider(tmp_path, "stub")
    with pytest.raises(CacheMissError, match="No fixture"):
        p.complete([{"role": "user", "content": "hello"}], system="sys")


def test_stub_roundtrip(tmp_path: Path) -> None:
    p = StubProvider(tmp_path, "stub")
    key = p._fixture_key(
        messages=[{"role": "user", "content": "hello"}],
        system="sys",
        schema=None,
    )
    (tmp_path / f"{key}.json").write_text(
        json.dumps({
            "text": "hi",
            "parsed": None,
            "input_tokens": 1,
            "output_tokens": 2,
            "provider": "stub",
            "model": "stub-model",
        })
    )
    resp = p.complete([{"role": "user", "content": "hello"}], system="sys")
    assert resp.text == "hi"
    assert resp.input_tokens == 1


# =============================================================================
# Factory + env
# =============================================================================


def test_factory_selects_stub() -> None:
    p = make_provider("stub")
    assert p.name.startswith("stub")


def test_factory_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        make_provider("llama")


def test_factory_gemini_missing_key_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    # Ensure no key.json is discovered from CWD by pointing cwd to an empty dir.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ProviderAuthError):
        make_provider("gemini")


def test_factory_anthropic_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderAuthError, match="ANTHROPIC_API_KEY"):
        make_provider("anthropic")


# =============================================================================
# cache_key + default_model
# =============================================================================


def test_cache_key_stable() -> None:
    k1 = cache_key(["a", "b"], "gemini-2.5-pro", "abc123")
    k2 = cache_key(["a", "b"], "gemini-2.5-pro", "abc123")
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_cache_key_changes_with_any_input() -> None:
    base = cache_key(["a"], "m1", "s1")
    assert base != cache_key(["b"], "m1", "s1")
    assert base != cache_key(["a"], "m2", "s1")
    assert base != cache_key(["a"], "m1", "s2")


def test_default_model_per_provider() -> None:
    assert default_model("director", "gemini") == "gemini-2.5-pro"
    assert default_model("assembler", "gemini") == "gemini-2.5-flash"
    assert default_model("director", "anthropic") == "claude-opus-4-7"
    assert default_model("assembler", "anthropic") == "claude-sonnet-4-6"


def test_default_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAMECRAFT_DIRECTOR_MODEL", "gemini-experimental")
    assert default_model("director", "gemini") == "gemini-experimental"


def test_default_model_family_extracted() -> None:
    # stub:anthropic should resolve to the `anthropic` family
    assert default_model("director", "stub:anthropic") == "claude-opus-4-7"


# =============================================================================
# Gemini — mocked SDK
# =============================================================================


def _make_gemini_provider(monkeypatch: pytest.MonkeyPatch) -> tuple:
    """Return (provider, fake_client) with Gemini SDK mocked."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    fake_client = MagicMock()
    fake_client.caches.create.return_value = SimpleNamespace(name="caches/abc123")
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text='{"archetype": "product_promo"}',
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=50,
            cached_content_token_count=900,
        ),
    )
    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client

    fake_types = MagicMock()
    fake_types.Part.from_text = MagicMock(side_effect=lambda text: {"text": text})
    fake_types.Content = MagicMock(side_effect=lambda role, parts: {"role": role, "parts": parts})
    fake_types.GenerateContentConfig = MagicMock(side_effect=lambda **kw: {"_config": kw})
    fake_types.CreateCachedContentConfig = MagicMock(side_effect=lambda **kw: {"_cache_config": kw})

    mock_google = MagicMock()
    mock_google.genai = fake_genai
    mock_google.genai.types = fake_types

    with patch.dict("sys.modules", {
        "google": mock_google,
        "google.genai": fake_genai,
        "google.genai.types": fake_types,
    }):
        from framecraft.providers.gemini import GeminiProvider
        provider = GeminiProvider()

    return provider, fake_client


def test_gemini_complete_with_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Route cache index into tmp_path so tests don't touch user cache.
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_INDEX", tmp_path / "gemini.json")
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_DIR", tmp_path)

    provider, client = _make_gemini_provider(monkeypatch)

    # Big-enough cache segments to exceed the minimum.
    big_seg = "x" * (4096 * 4)

    resp = provider.complete(
        messages=[{"role": "user", "content": "plan this"}],
        system="you are a director",
        cache_segments=[big_seg],
        schema={"type": "object"},
    )

    assert resp.provider == "gemini"
    assert resp.cache_read_tokens == 900
    assert resp.input_tokens == 1000
    assert resp.output_tokens == 50
    assert resp.parsed == {"archetype": "product_promo"}

    # First call creates a cache object.
    client.caches.create.assert_called_once()
    client.models.generate_content.assert_called_once()


def test_gemini_reuses_cache_across_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_INDEX", tmp_path / "gemini.json")
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_DIR", tmp_path)

    provider, client = _make_gemini_provider(monkeypatch)

    big_seg = "x" * (4096 * 4)
    provider.complete(messages=[{"role": "user", "content": "a"}], system="sys", cache_segments=[big_seg])
    provider.complete(messages=[{"role": "user", "content": "b"}], system="sys", cache_segments=[big_seg])

    # Second call must NOT create another cache — just reuse.
    assert client.caches.create.call_count == 1
    assert client.models.generate_content.call_count == 2


def test_gemini_skips_cache_for_tiny_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_INDEX", tmp_path / "gemini.json")
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_DIR", tmp_path)

    provider, client = _make_gemini_provider(monkeypatch)
    provider.complete(
        messages=[{"role": "user", "content": "a"}],
        system="sys",
        cache_segments=["short"],  # below minimum
    )
    # No cache created for short segments.
    client.caches.create.assert_not_called()


def test_gemini_error_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_INDEX", tmp_path / "gemini.json")
    monkeypatch.setattr("framecraft.providers.gemini._CACHE_DIR", tmp_path)

    provider, client = _make_gemini_provider(monkeypatch)
    client.models.generate_content.side_effect = Exception("429 rate limit exceeded")
    with pytest.raises(ProviderRateLimitError):
        provider.complete(
            messages=[{"role": "user", "content": "a"}],
            system="sys",
            cache_segments=["x" * 20000],
        )


# =============================================================================
# Anthropic — mocked SDK
# =============================================================================


def _make_anthropic_provider(monkeypatch: pytest.MonkeyPatch) -> tuple:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    # Build a fake SDK with the exception classes our adapter uses.
    class _Auth(Exception):
        pass

    class _Rate(Exception):
        pass

    class _Conn(Exception):
        pass

    fake_anthropic = SimpleNamespace()
    fake_anthropic.AuthenticationError = _Auth
    fake_anthropic.RateLimitError = _Rate
    fake_anthropic.APIConnectionError = _Conn

    fake_client = MagicMock()
    fake_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text='{"archetype": "product_promo"}')],
        usage=SimpleNamespace(
            input_tokens=1200,
            output_tokens=60,
            cache_read_input_tokens=1000,
            cache_creation_input_tokens=200,
        ),
    )
    fake_anthropic.Anthropic = MagicMock(return_value=fake_client)

    with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
        from framecraft.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider()

    return provider, fake_client, fake_anthropic


def test_anthropic_cache_control_on_trailing_block(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, client, _ = _make_anthropic_provider(monkeypatch)

    provider.complete(
        messages=[{"role": "user", "content": "plan this"}],
        system="director role",
        cache_segments=["primer", "registry-json"],
        schema={"type": "object"},
    )

    kwargs = client.messages.create.call_args.kwargs
    blocks = kwargs["system"]
    # Three blocks: two cache segments + the trailing "director role" block.
    assert len(blocks) == 3
    # Only the last block carries cache_control.
    assert "cache_control" not in blocks[0]
    assert "cache_control" not in blocks[1]
    assert blocks[2]["cache_control"] == {"type": "ephemeral"}
    # Schema tail was appended to the final block.
    assert "Respond with a single JSON object" in blocks[2]["text"]


def test_anthropic_populates_cache_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _, _ = _make_anthropic_provider(monkeypatch)

    resp = provider.complete(
        messages=[{"role": "user", "content": "plan this"}],
        system="director role",
        cache_segments=["primer"],
        schema={"type": "object"},
    )
    assert resp.cache_read_tokens == 1000
    assert resp.cache_write_tokens == 200
    assert resp.parsed == {"archetype": "product_promo"}


def test_anthropic_error_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, client, fake_sdk = _make_anthropic_provider(monkeypatch)
    client.messages.create.side_effect = fake_sdk.RateLimitError("slow down")
    with pytest.raises(ProviderRateLimitError):
        provider.complete(
            messages=[{"role": "user", "content": "a"}],
            system="sys",
        )


# =============================================================================
# Prompt wrappers
# =============================================================================


def test_prompt_wrappers_exist() -> None:
    from framecraft.prompts import load_common, load_provider_framing

    # Shared bodies exist for both roles.
    assert "Director" in load_common("director")
    assert "Assembler" in load_common("assembler")

    # Per-provider framings exist and differ.
    g = load_provider_framing("gemini", "director")
    a = load_provider_framing("anthropic", "director")
    assert g != a
    assert "JSON" in g
    assert "JSON" in a


def test_prompt_wrapper_unknown_provider_falls_back() -> None:
    """Unknown provider falls back to Gemini framing instead of raising."""
    from framecraft.prompts import load_provider_framing
    g = load_provider_framing("gemini", "director")
    fallback = load_provider_framing("mystery-provider", "director")
    assert fallback == g


# =============================================================================
# Integration (skipped by default — require live keys)
# =============================================================================


@pytest.mark.llm
@pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
    reason="no Gemini key",
)
def test_gemini_live_cache_warms() -> None:  # pragma: no cover - opt-in
    provider = make_provider("gemini")
    big_seg = "You are a helpful assistant. " * 1000  # ~25k chars, ≥1024 tokens
    r1 = provider.complete(
        messages=[{"role": "user", "content": "Say hi."}],
        system="Short helpful assistant.",
        cache_segments=[big_seg],
    )
    r2 = provider.complete(
        messages=[{"role": "user", "content": "Say hello."}],
        system="Short helpful assistant.",
        cache_segments=[big_seg],
    )
    assert r2.cache_read_tokens > 0, f"cache miss on second call: {r2}"


@pytest.mark.llm
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no Anthropic key")
def test_anthropic_live_cache_warms() -> None:  # pragma: no cover - opt-in
    provider = make_provider("anthropic")
    big_seg = "You are a helpful assistant. " * 1000
    r1 = provider.complete(
        messages=[{"role": "user", "content": "Say hi."}],
        system="Short helpful assistant.",
        cache_segments=[big_seg],
    )
    r2 = provider.complete(
        messages=[{"role": "user", "content": "Say hello."}],
        system="Short helpful assistant.",
        cache_segments=[big_seg],
    )
    assert r2.cache_read_tokens > 0
