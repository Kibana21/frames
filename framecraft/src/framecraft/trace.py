"""Structured trace writer used by Director (03), Assembler (04),
Lint-repair (05), and Render (06b).

Owned by 03 because the Director is the first emitter; 07 is the first
*consumer* (aggregator + asserts). Format contracts frozen in M1; schema
bumps require `version: int` discriminator updates and a migration note.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field

TRACE_SCHEMA_VERSION = 1


def hash_for_trace(s: str) -> str:
    """SHA-256 hex of `s`. Never log raw prompts in traces — store this instead."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def excerpt(s: str, limit: int = 2048) -> str:
    """First `limit` bytes of `s`, for debugging without bloating traces."""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… [truncated {len(s) - limit} chars]"


# --- Per-pass -------------------------------------------------------------


class TracePass(BaseModel):
    """One provider.complete() call. Used by Director Pass A/B/retry and per
    Assembler scene polish. Extend only by appending fields; don't break
    existing consumers.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    elapsed_ms: int = 0
    response_sha256: str
    response_excerpt: str
    # Optional: source of the decision if not an LLM call (e.g. user-forced archetype).
    source: Literal["llm", "user", "fallback"] | None = "llm"


# --- Director trace ------------------------------------------------------


class AspectSwapRecord(BaseModel):
    scene_index: int
    from_block: str
    to_block: str
    reason: Literal["fallback_block_id", "llm_correction"]


class DirectorTrace(BaseModel):
    version: Literal[1] = TRACE_SCHEMA_VERSION
    brief_hash: str
    system_prompt_sha256: str
    cache_segments_sha256: list[str] = Field(default_factory=list)
    provider: str
    director_model: str
    schema_hash: str
    pass_a: TracePass | None = None
    pass_b: TracePass | None = None
    retry: TracePass | None = None
    aspect_swaps: list[AspectSwapRecord] = Field(default_factory=list)
    elapsed_ms_total: int = 0
    outcome: Literal["ok", "validation_failed", "provider_error", "unknown"] = "unknown"
    error: str | None = None


# --- Assembler scene trace -----------------------------------------------


class AssemblerSceneTrace(BaseModel):
    """Per-scene trace written by the Assembler when the provider is called."""

    version: Literal[1] = TRACE_SCHEMA_VERSION
    scene_index: int
    block_id: str
    provenance: str
    polish_cache_hits: int = 0
    polish_cache_misses: int = 0
    elapsed_ms: int = 0


# --- Render log ----------------------------------------------------------


class RenderLog(BaseModel):
    """Presence/content record for .framecraft/render-stderr.log."""

    present: bool
    content_excerpt: str = ""


# --- Run summary (aggregated) --------------------------------------------


class RunSummary(BaseModel):
    """Aggregated view of all trace files in a single compose/from-plan run."""

    director: DirectorTrace | None = None
    assembler_scenes: list[AssemblerSceneTrace] = Field(default_factory=list)
    lint_passed: bool | None = None
    render: RenderLog | None = None
    total_elapsed_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    estimated_cost_usd: float = 0.0


def summarize(out_dir: Path) -> RunSummary:
    """Walk `out_dir/.framecraft` and aggregate all trace files into RunSummary."""
    from framecraft.trace_rates import cost_usd  # avoid circular

    fc = out_dir / ".framecraft"

    # --- Director trace
    director: DirectorTrace | None = None
    d_path = fc / "director-trace.json"
    if d_path.exists():
        try:
            director = DirectorTrace.model_validate_json(d_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # --- Assembler scene traces
    assembler_scenes: list[AssemblerSceneTrace] = []
    traces_dir = fc / "assembler-traces"
    if traces_dir.exists():
        for f in sorted(traces_dir.glob("scene-*.json")):
            try:
                assembler_scenes.append(
                    AssemblerSceneTrace.model_validate_json(f.read_text(encoding="utf-8"))
                )
            except Exception:
                pass

    # --- Lint report (just pass/fail)
    lint_passed: bool | None = None
    lint_path = fc / "lint-report.json"
    if lint_path.exists():
        try:
            data = json.loads(lint_path.read_text(encoding="utf-8"))
            errors = data.get("errors", [])
            lint_passed = len(errors) == 0
        except Exception:
            pass

    # --- Render log
    render: RenderLog | None = None
    rlog = fc / "render-stderr.log"
    if rlog.exists():
        content = rlog.read_text(encoding="utf-8")
        render = RenderLog(present=True, content_excerpt=excerpt(content, 512))
    else:
        render = None

    # --- Token aggregation
    total_in = total_out = total_cr = total_cw = 0
    total_ms = 0
    total_cost = 0.0

    def _add_pass(p: TracePass | None, prov: str) -> None:
        nonlocal total_in, total_out, total_cr, total_cw, total_ms, total_cost
        if p is None:
            return
        total_in += p.input_tokens
        total_out += p.output_tokens
        total_cr += p.cache_read_tokens
        total_cw += p.cache_write_tokens
        total_ms += p.elapsed_ms
        total_cost += cost_usd(prov, p.model, p.input_tokens, p.output_tokens,
                               p.cache_read_tokens, p.cache_write_tokens)

    if director:
        prov = director.provider
        _add_pass(director.pass_a, prov)
        _add_pass(director.pass_b, prov)
        _add_pass(director.retry, prov)
        total_ms = max(total_ms, director.elapsed_ms_total)

    for st in assembler_scenes:
        total_ms += st.elapsed_ms

    return RunSummary(
        director=director,
        assembler_scenes=assembler_scenes,
        lint_passed=lint_passed,
        render=render,
        total_elapsed_ms=total_ms,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cache_read_tokens=total_cr,
        total_cache_write_tokens=total_cw,
        estimated_cost_usd=round(total_cost, 6),
    )


# --- Atomic write + always-write context manager -------------------------


def atomic_write(model: BaseModel, path: Path) -> None:
    """Write `model.model_dump_json(...)` to `path` via tempfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def always_write(path: Path, initial: BaseModel) -> Iterator[dict[str, Any]]:
    """Guarantee `path` exists with a parseable trace on both success and
    exception paths (FR-11).

    The caller mutates `holder["trace"]` as it learns more; the final value
    is written on exit (including from an exception).

    Usage:
        with always_write(trace_path, DirectorTrace(...)) as holder:
            ... do work ...
            holder["trace"] = holder["trace"].model_copy(update={...})
    """
    # Initial write happens before any risky work so a crash still leaves a
    # file. It will be overwritten on a clean exit.
    atomic_write(initial, path)
    holder: dict[str, Any] = {"trace": initial}
    try:
        yield holder
    finally:
        atomic_write(holder["trace"], path)


__all__ = [
    "AspectSwapRecord",
    "AssemblerSceneTrace",
    "DirectorTrace",
    "RenderLog",
    "RunSummary",
    "TRACE_SCHEMA_VERSION",
    "TracePass",
    "always_write",
    "atomic_write",
    "excerpt",
    "hash_for_trace",
    "summarize",
]
