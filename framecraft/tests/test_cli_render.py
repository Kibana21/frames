"""Unit tests for `framecraft render`. See `.claude/plans/06b-cli-shellouts.md`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from framecraft.cli import app
from framecraft.cli_render import invoke_render
from framecraft.exit_codes import ExitCode, ToolchainError

runner = CliRunner()


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Argument construction
# ---------------------------------------------------------------------------


def test_render_default_args(tmp_path):
    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, **kwargs):
        calls.append(list(args))
        return _completed()

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        result = runner.invoke(app, ["render", "--out", str(tmp_path)])

    assert result.exit_code == 0
    assert calls == [["hyperframes", "render", "--format", "mp4", "--quality", "standard"]]


def test_render_custom_format_and_quality(tmp_path):
    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, **kwargs):
        calls.append(list(args))
        return _completed()

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        result = runner.invoke(app, ["render", "--out", str(tmp_path), "--format", "webm", "--quality", "draft"])

    assert result.exit_code == 0
    assert "--format" in calls[0]
    assert "webm" in calls[0]
    assert "--quality" in calls[0]
    assert "draft" in calls[0]


def test_render_passes_fps_when_given(tmp_path):
    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, **kwargs):
        calls.append(list(args))
        return _completed()

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        result = runner.invoke(app, ["render", "--out", str(tmp_path), "--fps", "60"])

    assert result.exit_code == 0
    assert "--fps" in calls[0]
    assert "60" in calls[0]


def test_render_no_fps_arg_when_not_given(tmp_path):
    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, **kwargs):
        calls.append(list(args))
        return _completed()

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        result = runner.invoke(app, ["render", "--out", str(tmp_path)])

    assert "--fps" not in calls[0]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_render_toolchain_error_exits_3(tmp_path):
    def fake_run_npx(args, *, cwd, **kwargs):
        raise ToolchainError("npx failed", stderr="some error text", returncode=1)

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        result = runner.invoke(app, ["render", "--out", str(tmp_path)])

    assert result.exit_code == ExitCode.RENDER


def test_render_writes_log_on_failure(tmp_path):
    def fake_run_npx(args, *, cwd, **kwargs):
        raise ToolchainError("npx failed", stderr="detailed error", returncode=1)

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        runner.invoke(app, ["render", "--out", str(tmp_path)])

    log = tmp_path / ".framecraft" / "render-stderr.log"
    assert log.exists()
    assert "detailed error" in log.read_text()


def test_render_prints_output_path_when_renders_dir_exists(tmp_path):
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    (renders_dir / "output.mp4").write_text("fake")

    with patch("framecraft.cli_render.run_npx", return_value=_completed()):
        result = runner.invoke(app, ["render", "--out", str(tmp_path)])

    assert result.exit_code == 0
    assert "output.mp4" in result.output or "Rendered" in result.output


# ---------------------------------------------------------------------------
# invoke_render helper
# ---------------------------------------------------------------------------


def test_invoke_render_raises_framecraftexit_on_failure(tmp_path):
    from framecraft.exit_codes import FrameCraftExit

    def fake_run_npx(args, *, cwd, **kwargs):
        raise ToolchainError("fail", stderr="oops", returncode=1)

    with patch("framecraft.cli_render.run_npx", side_effect=fake_run_npx):
        with pytest.raises(FrameCraftExit) as exc_info:
            invoke_render(out=tmp_path)

    assert exc_info.value.code == ExitCode.RENDER
