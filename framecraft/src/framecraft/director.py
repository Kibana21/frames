"""Director stage — Brief → SceneGraph via a two-pass LLM call.

See `.claude/plans/03-director.md`. The Director:
  1. Classifies the brief into an Archetype (Pass A, skipped if user forced).
  2. Plans 2–6 scenes from `registry.allowed_for(archetype)` (Pass B).
  3. Validates the plan; on Pydantic error, retries ONCE with the error
     appended to the conversation.
  4. Applies OQ-2 aspect-safe swap: if any scene's block excludes the plan
     aspect, swap via `spec.fallback_block_id`, else one corrective LLM pass,
     else DirectorError.
  5. Emits `.framecraft/director-trace.json` on every run (FR-11).

No filesystem I/O except the trace file. Pure function of
(provider, registry, brief).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from framecraft.blocks._spec import BlockSpec
from framecraft.prompts import load_common, load_primer, load_provider_framing
from framecraft.providers.base import (
    LLMProvider,
    Message,
    ProviderError,
    ProviderResponse,
    default_model,
)
from framecraft.registry import BlockRegistry
from framecraft.schema import (
    SCHEMA_HASH,
    Archetype,
    BlockId,
    Brief,
    Scene,
    SceneGraph,
)
from framecraft.trace import (
    AspectSwapRecord,
    DirectorTrace,
    TracePass,
    always_write,
    excerpt,
    hash_for_trace,
)

TRACE_FILENAME = "director-trace.json"
DEFAULT_MAX_SCENES = 6


class DirectorError(Exception):
    """Directed planning failed after all retry/fallback paths. Maps to exit 4."""


# --- Pass A schema (archetype classification) ------------------------------


class _ClassificationOut(BaseModel):
    archetype: Archetype
    rationale: str = ""


_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "archetype": {"enum": [a.value for a in Archetype]},
        "rationale": {"type": "string"},
    },
    "required": ["archetype"],
    "additionalProperties": False,
}


# --- Director --------------------------------------------------------------


class Director:
    def __init__(self, provider: LLMProvider, registry: BlockRegistry) -> None:
        self.provider = provider
        self.registry = registry

    # --- public ------------------------------------------------------------

    def plan(self, brief: Brief, *, out_dir: Path | None = None) -> SceneGraph:
        """Return a validated SceneGraph. Writes a trace under `out_dir/.framecraft/`
        if out_dir is provided; otherwise traces are in-memory only.
        """
        director_model = default_model("director", self.provider.name)
        cache_segments = self._cache_segments()
        system = load_provider_framing(self.provider.name, "director")

        initial = DirectorTrace(
            brief_hash=hash_for_trace(brief.model_dump_json()),
            system_prompt_sha256=hash_for_trace(system),
            cache_segments_sha256=[hash_for_trace(s) for s in cache_segments],
            provider=self.provider.name,
            director_model=director_model,
            schema_hash=SCHEMA_HASH,
        )

        if out_dir is not None:
            trace_path = out_dir / ".framecraft" / TRACE_FILENAME
            ctx = always_write(trace_path, initial)
        else:
            ctx = _NoopTraceCtx(initial)

        t0 = time.perf_counter()
        with ctx as holder:
            trace: DirectorTrace = holder["trace"]
            try:
                # Pass A — classify
                archetype, pass_a = self._classify(brief, cache_segments, system, director_model)
                trace = trace.model_copy(update={"pass_a": pass_a})

                # Pass B — plan scenes (with one retry on validation error)
                plan, pass_b, retry = self._plan_scenes(
                    brief, archetype, cache_segments, system, director_model
                )
                trace = trace.model_copy(update={"pass_b": pass_b, "retry": retry})

                # Aspect-safe swap (OQ-2)
                plan, swaps = self._apply_aspect_swap(
                    plan, brief, archetype, cache_segments, system, director_model
                )
                trace = trace.model_copy(update={
                    "aspect_swaps": [AspectSwapRecord(**s) for s in swaps],
                })

                # Canonicalize block_props via the registry's required_props models.
                plan = plan.validate_block_props_against(self.registry)

                trace = trace.model_copy(update={
                    "outcome": "ok",
                    "elapsed_ms_total": int((time.perf_counter() - t0) * 1000),
                })
                holder["trace"] = trace
                return plan

            except ValidationError as e:
                holder["trace"] = trace.model_copy(update={
                    "outcome": "validation_failed",
                    "error": str(e),
                    "elapsed_ms_total": int((time.perf_counter() - t0) * 1000),
                })
                raise DirectorError(f"plan failed validation after retry: {e}") from e
            except ProviderError as e:
                holder["trace"] = trace.model_copy(update={
                    "outcome": "provider_error",
                    "error": str(e),
                    "elapsed_ms_total": int((time.perf_counter() - t0) * 1000),
                })
                raise

    # --- Pass A: classification --------------------------------------------

    def _classify(
        self,
        brief: Brief,
        cache_segments: list[str],
        system: str,
        model: str,
    ) -> tuple[Archetype, TracePass]:
        # User-forced archetype short-circuits the LLM call.
        if brief.archetype is not None:
            return brief.archetype, TracePass(
                model="(user-forced)",
                response_sha256=hash_for_trace(brief.archetype.value),
                response_excerpt=brief.archetype.value,
                source="user",
            )

        user_msg = (
            f"Brief:\n  situation: {brief.situation!r}\n"
            f"  aspect: {brief.aspect.value}\n"
            f"  duration: {brief.duration:g}s\n"
            f"  mood: {brief.mood.value if brief.mood else 'unspecified'}\n\n"
            "Classify the situation into exactly one Archetype. "
            'Emit JSON: {"archetype": <enum>, "rationale": "..."}'
        )
        resp, trace_pass = self._call(
            [{"role": "user", "content": user_msg}],
            system=system,
            schema=_CLASSIFICATION_SCHEMA,
            cache_segments=cache_segments,
            model=model,
        )

        try:
            parsed = _ClassificationOut.model_validate(resp.parsed or {})
        except ValidationError as e:
            # One retry with the error appended.
            retry_msg = (
                f"Your previous response failed validation:\n{e}\n\n"
                'Return a corrected JSON object: {"archetype": <enum>, "rationale": "..."}'
            )
            resp2, trace_pass2 = self._call(
                [
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": resp.text},
                    {"role": "user", "content": retry_msg},
                ],
                system=system,
                schema=_CLASSIFICATION_SCHEMA,
                cache_segments=cache_segments,
                model=model,
            )
            parsed = _ClassificationOut.model_validate(resp2.parsed or {})
            # Combined trace pass — record the retry's usage since it's the
            # one we ultimately used.
            trace_pass = TracePass(
                model=trace_pass2.model,
                input_tokens=trace_pass.input_tokens + trace_pass2.input_tokens,
                output_tokens=trace_pass.output_tokens + trace_pass2.output_tokens,
                cache_read_tokens=trace_pass.cache_read_tokens + trace_pass2.cache_read_tokens,
                cache_write_tokens=trace_pass.cache_write_tokens + trace_pass2.cache_write_tokens,
                elapsed_ms=trace_pass.elapsed_ms + trace_pass2.elapsed_ms,
                response_sha256=trace_pass2.response_sha256,
                response_excerpt=trace_pass2.response_excerpt,
                source="llm",
            )

        return parsed.archetype, trace_pass

    # --- Pass B: scene planning --------------------------------------------

    def _plan_scenes(
        self,
        brief: Brief,
        archetype: Archetype,
        cache_segments: list[str],
        system: str,
        model: str,
    ) -> tuple[SceneGraph, TracePass, TracePass | None]:
        allowed_blocks = self.registry.allowed_for(archetype)
        allowed_transitions = self.registry.transitions_allowed()

        user_msg = (
            f"Brief:\n  situation: {brief.situation!r}\n"
            f"  aspect: {brief.aspect.value} → canvas {list(brief.aspect.dimensions)}\n"
            f"  duration: {brief.duration:g}s (±0.1s)\n"
            f"  fps: {brief.fps}\n"
            f"  mood: {brief.mood.value if brief.mood else 'unspecified'}\n"
            f"  archetype: {archetype.value}\n"
            f"  brand_kit: {self._brand_kit_summary(brief)}\n\n"
            f"Allowed block_ids: {[b.value for b in allowed_blocks]}\n"
            f"Allowed transition_ids: {[t.value for t in allowed_transitions]}\n"
            f"Max scenes: {DEFAULT_MAX_SCENES}\n\n"
            "Emit a valid SceneGraph as JSON. Remember:\n"
            f"- canvas must equal {list(brief.aspect.dimensions)}\n"
            "- scene.start values must be non-decreasing\n"
            "- sum(scene.duration) - sum(transition.overlap) must match duration ±0.1s\n"
            "- use only block_ids from the allowed list\n"
            "- prefer blocks whose aspect_preferred includes the brief aspect\n"
            "- leave scene.polished as {}\n"
        )

        messages: list[Message] = [{"role": "user", "content": user_msg}]
        scene_graph_schema = SceneGraph.model_json_schema()

        resp, pass_b = self._call(
            messages,
            system=system,
            schema=scene_graph_schema,
            cache_segments=cache_segments,
            model=model,
        )

        retry_trace: TracePass | None = None
        try:
            plan = SceneGraph.model_validate(resp.parsed or {})
        except ValidationError as e:
            retry_msg = (
                f"Your SceneGraph failed validation:\n{e}\n\n"
                "Return a corrected SceneGraph JSON. Keep the same archetype."
            )
            resp2, retry_trace = self._call(
                [
                    *messages,
                    {"role": "assistant", "content": resp.text},
                    {"role": "user", "content": retry_msg},
                ],
                system=system,
                schema=scene_graph_schema,
                cache_segments=cache_segments,
                model=model,
            )
            plan = SceneGraph.model_validate(resp2.parsed or {})  # may raise, caught in plan()

        return plan, pass_b, retry_trace

    # --- Aspect-safe swap (OQ-2) ------------------------------------------

    def _apply_aspect_swap(
        self,
        plan: SceneGraph,
        brief: Brief,
        archetype: Archetype,
        cache_segments: list[str],
        system: str,
        model: str,
    ) -> tuple[SceneGraph, list[dict]]:
        swaps: list[dict] = []
        new_scenes: list[Scene] = []

        for scene in plan.scenes:
            spec = self.registry.resolve(scene.block_id)
            if brief.aspect in spec.aspect_preferred:
                new_scenes.append(scene)
                continue

            # Level 1: static fallback on the spec
            fb_id = spec.fallback_block_id
            if fb_id is not None and brief.aspect in self.registry.resolve(fb_id).aspect_preferred:
                new_scenes.append(scene.model_copy(update={"block_id": fb_id}))
                swaps.append({
                    "scene_index": scene.index,
                    "from_block": scene.block_id.value,
                    "to_block": fb_id.value,
                    "reason": "fallback_block_id",
                })
                continue

            # Level 2: corrective LLM pass.
            aspect_safe = [
                bid for bid in self.registry.allowed_for(archetype)
                if brief.aspect in self.registry.resolve(bid).aspect_preferred
                and bid != scene.block_id
            ]
            if not aspect_safe:
                raise DirectorError(
                    f"No aspect-safe block for scene {scene.index} "
                    f"(aspect {brief.aspect.value}). Tried {scene.block_id.value} "
                    "and no alternatives remain in the archetype's allowed set."
                )

            swap_msg = (
                f"Scene {scene.index} uses block {scene.block_id.value!r}, "
                f"which doesn't support aspect {brief.aspect.value}. "
                f"Choose a different block_id from {[b.value for b in aspect_safe]} "
                "that keeps the copy intent. "
                'Return JSON: {"block_id": "<id>"}'
            )
            resp, _ = self._call(
                [{"role": "user", "content": swap_msg}],
                system=system,
                schema={
                    "type": "object",
                    "properties": {"block_id": {"enum": [b.value for b in aspect_safe]}},
                    "required": ["block_id"],
                    "additionalProperties": False,
                },
                cache_segments=cache_segments,
                model=model,
            )
            try:
                picked = BlockId((resp.parsed or {})["block_id"])
            except (KeyError, ValueError) as e:
                raise DirectorError(
                    f"Aspect-swap correction for scene {scene.index} produced an invalid "
                    f"block_id: {resp.text!r}"
                ) from e

            new_scenes.append(scene.model_copy(update={"block_id": picked}))
            swaps.append({
                "scene_index": scene.index,
                "from_block": scene.block_id.value,
                "to_block": picked.value,
                "reason": "llm_correction",
            })

        if swaps:
            return plan.model_copy(update={"scenes": new_scenes}), swaps
        return plan, swaps

    # --- helpers -----------------------------------------------------------

    def _call(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict,
        cache_segments: list[str],
        model: str,
    ) -> tuple[ProviderResponse, TracePass]:
        resp = self.provider.complete(
            messages,
            system=system,
            schema=schema,
            cache_segments=cache_segments,
            model=model,
        )
        return resp, TracePass(
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cache_read_tokens=resp.cache_read_tokens,
            cache_write_tokens=resp.cache_write_tokens,
            elapsed_ms=resp.elapsed_ms,
            response_sha256=hash_for_trace(resp.text),
            response_excerpt=excerpt(resp.text),
        )

    def _cache_segments(self) -> list[str]:
        """Stable prefix shared across every Director call in a session.

        Must be deterministic: sort_keys=True, fixed indent, no ctime.
        Changes here invalidate every user's provider cache — do it only on
        schema or registry bumps.
        """
        return [
            load_primer(),
            load_common("director"),
            _registry_to_llm_json(self.registry),
            json.dumps(
                SceneGraph.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False
            ),
        ]

    @staticmethod
    def _brand_kit_summary(brief: Brief) -> str:
        bk = brief.brand_kit
        if bk is None:
            return "none"
        pieces: list[str] = []
        if bk.logo_path:
            pieces.append(f"logo@{bk.logo_path.name}")
        if bk.palette:
            pieces.append(
                f"palette=[primary {bk.palette.primary}, bg {bk.palette.bg}, "
                f"accent {bk.palette.accent}]"
            )
        if bk.typography:
            pieces.append(f"font={bk.typography.headline!r}")
        return "{" + ", ".join(pieces) + "}"


# --- Serialization helpers ------------------------------------------------


def _registry_to_llm_json(registry: BlockRegistry) -> str:
    """Serialize the registry for the Director's system prompt.

    Excludes non-JSONable fields (`template`, Python-object `required_props`).
    Includes `required_props_schema` — the JSON schema of each block's prop
    model — so the Director can emit correctly-shaped `block_props`.
    """
    blocks: dict[str, dict[str, Any]] = {}
    for bid, spec in registry.all().items():
        blocks[bid.value] = _spec_to_llm_dict(spec)
    return json.dumps(blocks, indent=2, sort_keys=True, ensure_ascii=False)


def _spec_to_llm_dict(spec: BlockSpec) -> dict[str, Any]:
    return {
        "id": spec.id.value,
        "category": spec.category.value,
        "provenance": spec.provenance.value,
        "synopsis": spec.synopsis,
        "suggested_duration": list(spec.suggested_duration),
        "aspect_preferred": sorted(a.value for a in spec.aspect_preferred),
        "fallback_block_id": spec.fallback_block_id.value if spec.fallback_block_id else None,
        "required_props_schema": (
            spec.required_props.model_json_schema() if spec.required_props else None
        ),
    }


# --- Noop trace context (used when out_dir is None) -----------------------


class _NoopTraceCtx:
    def __init__(self, initial: DirectorTrace) -> None:
        self.holder = {"trace": initial}

    def __enter__(self) -> dict[str, Any]:
        return self.holder

    def __exit__(self, *exc: Any) -> None:
        return None


__all__ = ["Director", "DirectorError", "TRACE_FILENAME"]
