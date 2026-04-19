"""03 Director tests — classification, planning, retry, aspect swap, trace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from framecraft import Archetype, Aspect, BlockId, Brief, Director, DirectorError
from framecraft.blocks._spec import BlockSpec
from framecraft.providers.base import (
    LLMProvider,
    Message,
    ProviderNetworkError,
    ProviderResponse,
)
from framecraft.registry import REGISTRY, BlockRegistry
from framecraft.schema import Provenance


# =============================================================================
# Fake provider — queues responses; records every call
# =============================================================================


class FakeProvider:
    """Drop-in LLMProvider that returns queued responses in order.

    Each entry is a dict that's applied as kwargs to ProviderResponse (text
    auto-JSON-encodes if `parsed` is set and text is omitted).
    """

    name: str = "fake"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._queue: list[dict[str, Any]] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict | None = None,
        cache_segments: list[str] | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.calls.append({
            "messages": messages,
            "system": system,
            "schema": schema,
            "cache_segments": cache_segments,
            "model": model,
        })
        if not self._queue:
            raise RuntimeError(
                f"FakeProvider ran out of responses; saw {len(self.calls)} calls. "
                f"Last messages: {messages!r}"
            )
        spec = self._queue.pop(0)
        text = spec.get("text")
        parsed = spec.get("parsed")
        if text is None and parsed is not None:
            text = json.dumps(parsed)
        if text is None:
            text = ""
        return ProviderResponse(
            text=text,
            parsed=parsed,
            input_tokens=spec.get("input_tokens", 100),
            output_tokens=spec.get("output_tokens", 20),
            cache_read_tokens=spec.get("cache_read_tokens", 80),
            cache_write_tokens=spec.get("cache_write_tokens", 0),
            provider=self.name,
            model=model or spec.get("model", "fake-model"),
            elapsed_ms=spec.get("elapsed_ms", 10),
        )


# =============================================================================
# Test helpers
# =============================================================================


def _brief(**kw: Any) -> Brief:
    defaults: dict[str, Any] = {
        "situation": "A test situation for the Director",
        "aspect": Aspect.AR_16_9,
        "duration": 10.0,
    }
    defaults.update(kw)
    return Brief(**defaults)


def _valid_plan_dict(aspect: Aspect = Aspect.AR_16_9, total: float = 10.0) -> dict[str, Any]:
    """A SceneGraph shape the Director's Pass B would validly emit.

    Deliberately tiny (2 scenes) so tests stay readable.
    """
    w, h = aspect.dimensions
    s1 = total * 0.6
    s2 = total - s1
    return {
        "version": 1,
        "brief": {
            "situation": "A test situation for the Director",
            "aspect": aspect.value,
            "duration": total,
            "fps": 30,
            "music_volume": 0.4,
        },
        "archetype": "narrative_scene",
        "aspect": aspect.value,
        "canvas": [w, h],
        "duration": total,
        "scenes": [
            {
                "index": 0,
                "block_id": "title-card",
                "start": 0.0,
                "duration": s1,
                "track_index": 1,
                "block_props": {"headline": "Hello world"},
                "polished": {},
            },
            {
                "index": 1,
                "block_id": "end-card",
                "start": s1,
                "duration": s2,
                "track_index": 1,
                "block_props": {"tagline": "Fin."},
                "polished": {},
            },
        ],
        "transitions": [],
    }


# =============================================================================
# Pass A: classification
# =============================================================================


def test_classify_happy(tmp_path: Path) -> None:
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene", "rationale": "emotional"}},
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    plan = d.plan(_brief(), out_dir=tmp_path)

    assert plan.archetype is Archetype.NARRATIVE_SCENE
    assert len(provider.calls) == 2
    # Pass A got the classification schema (enum present).
    first_schema = provider.calls[0]["schema"]
    assert "archetype" in first_schema["properties"]


def test_user_forced_archetype_skips_pass_a(tmp_path: Path) -> None:
    provider = FakeProvider([
        # Only Pass B expected — Pass A is short-circuited.
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    plan = d.plan(_brief(archetype=Archetype.PRODUCT_PROMO), out_dir=tmp_path)

    assert len(provider.calls) == 1  # only Pass B

    # Trace records source="user" on pass_a.
    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert trace["pass_a"]["source"] == "user"


def test_classification_invalid_enum_retries(tmp_path: Path) -> None:
    provider = FakeProvider([
        # First response: bogus archetype enum value.
        {"parsed": {"archetype": "MYTHIC", "rationale": "nope"}},
        # Retry: valid.
        {"parsed": {"archetype": "narrative_scene", "rationale": "ok"}},
        # Pass B.
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    plan = d.plan(_brief(), out_dir=tmp_path)

    assert plan.archetype is Archetype.NARRATIVE_SCENE
    assert len(provider.calls) == 3  # pass A + retry + pass B
    # Retry message contains the validation error
    retry_msg = provider.calls[1]["messages"][-1]["content"]
    assert "failed validation" in retry_msg


# =============================================================================
# Pass B: scene planning — validation + retry
# =============================================================================


def test_scenegraph_validation_retry_succeeds(tmp_path: Path) -> None:
    bad_plan = _valid_plan_dict()
    bad_plan["duration"] = 99.0  # mismatches scene sum → validator fails

    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene", "rationale": "ok"}},
        {"parsed": bad_plan},
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    d.plan(_brief(), out_dir=tmp_path)

    assert len(provider.calls) == 3  # A, B, B-retry
    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert trace["retry"] is not None
    assert trace["outcome"] == "ok"


def test_scenegraph_validation_double_fail_raises(tmp_path: Path) -> None:
    bad = _valid_plan_dict()
    bad["duration"] = 99.0
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": bad},
        {"parsed": bad},  # retry also fails
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    with pytest.raises(DirectorError):
        d.plan(_brief(), out_dir=tmp_path)

    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert trace["outcome"] == "validation_failed"
    assert "duration" in (trace["error"] or "")


def test_block_props_validation_uses_required_props(tmp_path: Path) -> None:
    """A valid-looking SceneGraph whose block_props fail the per-block
    required_props model should raise."""
    plan_dict = _valid_plan_dict()
    # title-card requires a non-empty headline.
    plan_dict["scenes"][0]["block_props"] = {"headline": ""}
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": plan_dict},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    with pytest.raises(DirectorError):
        d.plan(_brief(), out_dir=tmp_path)


# =============================================================================
# Aspect-safe swap (OQ-2)
# =============================================================================


def _make_narrow_registry() -> BlockRegistry:
    """Build a test registry where title-card only supports 16:9 and falls
    back to end-card. Crucially, we give title-card the SAME prop model as
    end-card so block_props survive the swap — in production, fallback
    targets must be prop-compatible."""
    end_props_model = REGISTRY[BlockId.END_CARD].required_props
    narrow_title = REGISTRY[BlockId.TITLE_CARD].model_copy(update={
        "aspect_preferred": [Aspect.AR_16_9],
        "fallback_block_id": BlockId.END_CARD,
        "required_props": end_props_model,
    })
    blocks = {
        BlockId.TITLE_CARD: narrow_title,
        BlockId.END_CARD: REGISTRY[BlockId.END_CARD],
    }
    return BlockRegistry(blocks, {})


def test_aspect_swap_with_fallback(tmp_path: Path) -> None:
    registry = _make_narrow_registry()
    # Plan for 9:16 — title-card no longer supports this, fallback is end-card.
    # Using `tagline` since both specs share end-card's prop model in this test.
    plan = _valid_plan_dict(aspect=Aspect.AR_9_16)
    plan["scenes"][0]["block_props"] = {"tagline": "Opening line."}
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": plan},
        # No extra LLM call — the swap is static.
    ])
    d = Director(provider, registry)
    result = d.plan(_brief(aspect=Aspect.AR_9_16), out_dir=tmp_path)

    assert len(provider.calls) == 2  # Pass A + Pass B; no corrective call
    assert result.scenes[0].block_id is BlockId.END_CARD
    # Scene 1 was already end-card.
    assert result.scenes[1].block_id is BlockId.END_CARD

    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert len(trace["aspect_swaps"]) == 1
    assert trace["aspect_swaps"][0]["reason"] == "fallback_block_id"


def test_aspect_swap_without_fallback_uses_llm_correction(tmp_path: Path) -> None:
    """Narrow title-card AND give it no fallback → Director asks for a
    correction via a third LLM pass."""
    narrow_title = REGISTRY[BlockId.TITLE_CARD].model_copy(update={
        "aspect_preferred": [Aspect.AR_16_9],
        "fallback_block_id": None,
    })
    # Include a few blocks so the allowed set has alternatives.
    registry = BlockRegistry({
        BlockId.TITLE_CARD: narrow_title,
        BlockId.END_CARD: REGISTRY[BlockId.END_CARD],
        BlockId.GRADIENT_BG: REGISTRY[BlockId.GRADIENT_BG],
        BlockId.GRAIN_OVERLAY: REGISTRY[BlockId.GRAIN_OVERLAY],
        BlockId.LOWER_THIRD: REGISTRY[BlockId.LOWER_THIRD],
    }, {})

    plan = _valid_plan_dict(aspect=Aspect.AR_9_16)
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": plan},
        # Corrective LLM pass picks an aspect-safe alternative.
        {"parsed": {"block_id": "lower-third"}},
    ])
    # Use a richer brief that exercises lower-third props.
    d = Director(provider, registry)

    # lower-third requires `name`; the current block_props are for title-card.
    # The swap re-uses existing block_props which will fail required_props
    # validation — so we expect DirectorError. This is the documented
    # level-3 "hard fail" boundary: no copy massaging.
    with pytest.raises(DirectorError):
        d.plan(_brief(aspect=Aspect.AR_9_16), out_dir=tmp_path)

    # But the swap was attempted and recorded before block-props validation
    # failed.
    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert any(s["reason"] == "llm_correction" for s in trace["aspect_swaps"])


def test_aspect_swap_no_alternatives_raises(tmp_path: Path) -> None:
    """When no block in the archetype's allowed set supports the aspect,
    Director raises immediately."""
    narrow_title = REGISTRY[BlockId.TITLE_CARD].model_copy(update={
        "aspect_preferred": [Aspect.AR_16_9],
        "fallback_block_id": None,
    })
    narrow_end = REGISTRY[BlockId.END_CARD].model_copy(update={
        "aspect_preferred": [Aspect.AR_16_9],
    })
    registry = BlockRegistry({
        BlockId.TITLE_CARD: narrow_title,
        BlockId.END_CARD: narrow_end,
    }, {})

    plan = _valid_plan_dict(aspect=Aspect.AR_9_16)
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": plan},
    ])
    d = Director(provider, registry)
    with pytest.raises(DirectorError, match="No aspect-safe"):
        d.plan(_brief(aspect=Aspect.AR_9_16), out_dir=tmp_path)


# =============================================================================
# Tracing / always-write guarantee
# =============================================================================


def test_trace_written_on_provider_error(tmp_path: Path) -> None:
    class ExplodingProvider(FakeProvider):
        def complete(self, *a, **kw):  # type: ignore[override]
            raise ProviderNetworkError("simulated network blowup")

    d = Director(ExplodingProvider([]), BlockRegistry(REGISTRY, {}))
    with pytest.raises(ProviderNetworkError):
        d.plan(_brief(), out_dir=tmp_path)

    trace_path = tmp_path / ".framecraft" / "director-trace.json"
    assert trace_path.exists(), "FR-11: trace must be written even on provider error"
    trace = json.loads(trace_path.read_text())
    assert trace["outcome"] == "provider_error"
    assert "simulated network blowup" in trace["error"]


def test_trace_records_token_counts(tmp_path: Path) -> None:
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}, "input_tokens": 150, "output_tokens": 30, "cache_read_tokens": 120},
        {"parsed": _valid_plan_dict(), "input_tokens": 200, "output_tokens": 180, "cache_read_tokens": 120},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    d.plan(_brief(), out_dir=tmp_path)

    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    assert trace["pass_a"]["input_tokens"] == 150
    assert trace["pass_a"]["cache_read_tokens"] == 120
    assert trace["pass_b"]["output_tokens"] == 180
    assert trace["elapsed_ms_total"] >= 0


def test_trace_stores_hashes_not_prompts(tmp_path: Path) -> None:
    """§7.5: traces store sha256 of the system prompt and cache segments,
    never the raw text."""
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    d.plan(_brief(), out_dir=tmp_path)

    trace = json.loads((tmp_path / ".framecraft" / "director-trace.json").read_text())
    # Hashes are hex sha256 — 64 chars.
    assert len(trace["system_prompt_sha256"]) == 64
    for seg_hash in trace["cache_segments_sha256"]:
        assert len(seg_hash) == 64
    # The primer starts with "# Hyperframes Primer"; make sure that string
    # is NOT anywhere in the trace.
    trace_text = json.dumps(trace)
    assert "Hyperframes Primer" not in trace_text


def test_plan_without_out_dir_skips_trace(tmp_path: Path) -> None:
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    plan = d.plan(_brief())  # no out_dir

    # No .framecraft directory was even created in tmp_path.
    assert not (tmp_path / ".framecraft").exists()
    assert plan.archetype is Archetype.NARRATIVE_SCENE


# =============================================================================
# Cache segments + serialization
# =============================================================================


def test_cache_segments_are_deterministic() -> None:
    from framecraft.director import _registry_to_llm_json

    j1 = _registry_to_llm_json(BlockRegistry(REGISTRY, {}))
    j2 = _registry_to_llm_json(BlockRegistry(REGISTRY, {}))
    assert j1 == j2
    # Sanity: each known block id appears.
    for bid in REGISTRY:
        assert bid.value in j1


def test_cache_segments_exclude_non_json_fields() -> None:
    """The template Callable must not appear verbatim in the serialized
    registry. Pydantic's JSON Schema `title` field (which echoes the model
    class name) is expected and informative."""
    from framecraft.director import _registry_to_llm_json

    j = _registry_to_llm_json(BlockRegistry(REGISTRY, {}))
    assert "<function" not in j
    # But the schema of the required_props IS included.
    for bid, spec in REGISTRY.items():
        if spec.provenance is Provenance.NATIVE and spec.required_props is not None:
            data = json.loads(j)[bid.value]
            assert data["required_props_schema"] is not None
            assert "properties" in data["required_props_schema"]


def test_call_passes_cache_segments_every_time(tmp_path: Path) -> None:
    """Every provider.complete() call from the Director must pass the same
    cache_segments so the server-side cache stays warm."""
    provider = FakeProvider([
        {"parsed": {"archetype": "narrative_scene"}},
        {"parsed": _valid_plan_dict()},
    ])
    d = Director(provider, BlockRegistry(REGISTRY, {}))
    d.plan(_brief(), out_dir=tmp_path)

    assert len(provider.calls) == 2
    seg1 = provider.calls[0]["cache_segments"]
    seg2 = provider.calls[1]["cache_segments"]
    assert seg1 == seg2, "cache_segments changed between Pass A and Pass B — cache won't warm"
    assert isinstance(seg1, list) and len(seg1) == 4
