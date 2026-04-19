"""Unit tests for `framecraft preview`. See `.claude/plans/06b-cli-shellouts.md`."""

from __future__ import annotations

import io
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from framecraft.cli import app

runner = CliRunner()


def _make_fake_proc(lines: list[str], returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = io.StringIO("".join(lines))
    proc.wait.return_value = returncode
    proc.send_signal = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_preview_prints_url_banner(tmp_path):
    lines = ["Starting server...\n", "Listening on http://localhost:4000\n"]
    proc = _make_fake_proc(lines)

    with (
        patch("framecraft.cli_preview.shutil.which", return_value="/usr/bin/npx"),
        patch("framecraft.cli_preview.subprocess.Popen", return_value=proc),
    ):
        result = runner.invoke(app, ["preview", "--out", str(tmp_path), "--no-open"])

    proc.wait.assert_called()


def test_preview_detects_localhost_url(tmp_path):
    url = "http://localhost:4000"
    lines = [f"Preview server ready at {url}\n"]
    proc = _make_fake_proc(lines)

    with (
        patch("framecraft.cli_preview.shutil.which", return_value="/usr/bin/npx"),
        patch("framecraft.cli_preview.subprocess.Popen", return_value=proc),
        patch("framecraft.cli_preview.typer.secho") as mock_secho,
    ):
        runner.invoke(app, ["preview", "--out", str(tmp_path), "--no-open"])

    secho_calls = [str(c) for c in mock_secho.call_args_list]
    assert any(url in c for c in secho_calls)


# ---------------------------------------------------------------------------
# EADDRINUSE detection
# ---------------------------------------------------------------------------


def test_preview_eaddrinuse_exits_1(tmp_path):
    lines = ["Error: listen EADDRINUSE: address already in use :::4000\n"]
    proc = _make_fake_proc(lines)

    with (
        patch("framecraft.cli_preview.shutil.which", return_value="/usr/bin/npx"),
        patch("framecraft.cli_preview.subprocess.Popen", return_value=proc),
    ):
        result = runner.invoke(app, ["preview", "--out", str(tmp_path), "--no-open"])

    assert result.exit_code == 1
    assert "4000" in result.output or "in use" in result.output


# ---------------------------------------------------------------------------
# KeyboardInterrupt — clean exit
# ---------------------------------------------------------------------------


def test_preview_keyboard_interrupt_sends_sigint(tmp_path):
    proc = _make_fake_proc([])

    # Make stdout iteration raise KeyboardInterrupt
    mock_stdout = MagicMock()
    mock_stdout.__iter__ = MagicMock(side_effect=KeyboardInterrupt)
    proc.stdout = mock_stdout

    with (
        patch("framecraft.cli_preview.shutil.which", return_value="/usr/bin/npx"),
        patch("framecraft.cli_preview.subprocess.Popen", return_value=proc),
    ):
        result = runner.invoke(app, ["preview", "--out", str(tmp_path), "--no-open"])

    proc.send_signal.assert_called_once_with(signal.SIGINT)


# ---------------------------------------------------------------------------
# npx not found
# ---------------------------------------------------------------------------


def test_preview_no_npx_exits_1(tmp_path):
    with patch("framecraft.cli_preview.shutil.which", return_value=None):
        result = runner.invoke(app, ["preview", "--out", str(tmp_path)])

    assert result.exit_code == 1
    assert "npx not found" in result.output
