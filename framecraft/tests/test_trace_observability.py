"""Tests for trace aggregation and observability helpers.
See `.claude/plans/07-observability-and-goldens.md` §1–5.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from framecraft.cli import app
from framecraft.observability import always_write, hash_for_trace
from framecraft.trace import (
    AssemblerSceneTrace,
    DirectorTrace,
    RenderLog,
    RunSummary,
    TracePass,
    summarize,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# hash_for_trace (§7.5 — never log raw strings)
# ---------------------------------------------------------------------------


def test_hash_for_trace_returns_hex_digest():
    h = hash_for_trace("secret prompt")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_for_trace_deterministic():
    assert hash_for_trace("x") == hash_for_trace("x")


def test_hash_for_trace_different_inputs_differ():
    assert hash_for_trace("a") != hash_for_trace("b")


# ---------------------------------------------------------------------------
# always_write (FR-11)
# ---------------------------------------------------------------------------


def test_always_write_creates_file_before_work(tmp_path):
    trace_path = tmp_path / "trace.json"
    initial = AssemblerSceneTrace(scene_index=0, block_id="title-card", provenance="native")

    seen_before_yield: list[bool] = []

    with always_write(trace_path, initial) as holder:
        seen_before_yield.append(trace_path.exists())

    assert seen_before_yield[0], "trace file must exist inside the context"
    assert trace_path.exists()


def test_always_write_writes_final_on_success(tmp_path):
    trace_path = tmp_path / "trace.json"
    initial = AssemblerSceneTrace(scene_index=0, block_id="title-card", provenance="native")

    with always_write(trace_path, initial) as holder:
        holder["trace"] = initial.model_copy(update={"elapsed_ms": 42})

    data = json.loads(trace_path.read_text())
    assert data["elapsed_ms"] == 42


def test_always_write_writes_final_on_exception(tmp_path):
    trace_path = tmp_path / "trace.json"
    initial = AssemblerSceneTrace(scene_index=0, block_id="title-card", provenance="native")

    with pytest.raises(ValueError):
        with always_write(trace_path, initial) as holder:
            holder["trace"] = initial.model_copy(update={"elapsed_ms": 99})
            raise ValueError("mid-work failure")

    data = json.loads(trace_path.read_text())
    assert data["elapsed_ms"] == 99, "final state written even on exception"


# ---------------------------------------------------------------------------
# summarize()
# ---------------------------------------------------------------------------


def _write_director_trace(fc_dir: Path, outcome: str = "ok") -> None:
    trace = DirectorTrace(
        brief_hash="abc",
        system_prompt_sha256="def",
        provider="stub:gemini",
        director_model="stub-director",
        schema_hash="ghi",
        pass_a=TracePass(model="stub-director", response_sha256="x", response_excerpt=""),
        pass_b=TracePass(
            model="stub-director",
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=50,
            response_sha256="y",
            response_excerpt="",
        ),
        elapsed_ms_total=300,
        outcome=outcome,
    )
    fc_dir.mkdir(parents=True, exist_ok=True)
    (fc_dir / "director-trace.json").write_text(trace.model_dump_json(indent=2))


def _write_scene_trace(traces_dir: Path, idx: int) -> None:
    t = AssemblerSceneTrace(
        scene_index=idx, block_id="title-card", provenance="native",
        polish_cache_hits=0, polish_cache_misses=1, elapsed_ms=25,
    )
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"scene-{idx:02d}.json").write_text(t.model_dump_json(indent=2))


def test_summarize_empty_dir(tmp_path):
    s = summarize(tmp_path)
    assert isinstance(s, RunSummary)
    assert s.director is None
    assert s.assembler_scenes == []
    assert s.lint_passed is None


def test_summarize_reads_director_trace(tmp_path):
    _write_director_trace(tmp_path / ".framecraft")
    s = summarize(tmp_path)
    assert s.director is not None
    assert s.director.outcome == "ok"


def test_summarize_aggregates_token_counts(tmp_path):
    _write_director_trace(tmp_path / ".framecraft")
    s = summarize(tmp_path)
    # pass_b has 100 in + 20 out + 50 cache_read
    assert s.total_input_tokens == 100
    assert s.total_output_tokens == 20
    assert s.total_cache_read_tokens == 50


def test_summarize_reads_scene_traces(tmp_path):
    fc = tmp_path / ".framecraft"
    traces_dir = fc / "assembler-traces"
    _write_scene_trace(traces_dir, 0)
    _write_scene_trace(traces_dir, 1)
    s = summarize(tmp_path)
    assert len(s.assembler_scenes) == 2


def test_summarize_reads_lint_report(tmp_path):
    fc = tmp_path / ".framecraft"
    fc.mkdir(parents=True)
    (fc / "lint-report.json").write_text(json.dumps({"errors": [], "warnings": [], "info": []}))
    s = summarize(tmp_path)
    assert s.lint_passed is True


def test_summarize_lint_failed_when_errors(tmp_path):
    fc = tmp_path / ".framecraft"
    fc.mkdir(parents=True)
    (fc / "lint-report.json").write_text(json.dumps({
        "errors": [{"rule": "copy-too-long", "severity": "error", "file": "f", "line": 1, "message": "x"}],
        "warnings": [], "info": [],
    }))
    s = summarize(tmp_path)
    assert s.lint_passed is False


def test_summarize_reads_render_log(tmp_path):
    fc = tmp_path / ".framecraft"
    fc.mkdir(parents=True)
    (fc / "render-stderr.log").write_text("render error details")
    s = summarize(tmp_path)
    assert s.render is not None
    assert s.render.present is True
    assert "render error" in s.render.content_excerpt


def test_summarize_cost_is_zero_for_stub(tmp_path):
    _write_director_trace(tmp_path / ".framecraft")
    s = summarize(tmp_path)
    # Stub provider has zero rates
    assert s.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# --summary flag on compose
# ---------------------------------------------------------------------------


def test_compose_summary_flag_prints_summary(tmp_path):
    result = runner.invoke(
        app,
        [
            "compose",
            "a product promo for a widget",
            "--out", str(tmp_path),
            "--dry-run",
            "--no-config",
            "--summary",
        ],
    )
    assert result.exit_code == 0
    assert "Scenes:" in result.output or "run summary" in result.output


# ---------------------------------------------------------------------------
# observability re-exports
# ---------------------------------------------------------------------------


def test_observability_exports_always_write():
    from framecraft.observability import always_write
    assert callable(always_write)


def test_observability_exports_hash_for_trace():
    from framecraft.observability import hash_for_trace
    assert callable(hash_for_trace)
