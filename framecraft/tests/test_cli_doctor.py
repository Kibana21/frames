"""Unit tests for `framecraft doctor`. See `.claude/plans/06a-cli-core.md` §14."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from framecraft.cli import app

runner = CliRunner()


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Happy path — all tools present
# ---------------------------------------------------------------------------


def test_doctor_runs_and_prints_table():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1)  # 1 is allowed when tools are missing in CI
    assert "framecraft doctor" in result.output


def test_doctor_shows_node_check():
    result = runner.invoke(app, ["doctor"])
    assert "node" in result.output


def test_doctor_shows_hyperframes_check():
    result = runner.invoke(app, ["doctor"])
    assert "hyperframes" in result.output


def test_doctor_shows_provider_row():
    result = runner.invoke(app, ["doctor"])
    assert "FRAMECRAFT_PROVIDER" in result.output


def test_doctor_shows_api_key_rows():
    result = runner.invoke(app, ["doctor"])
    assert "GEMINI_API_KEY" in result.output
    assert "ANTHROPIC_API_KEY" in result.output


# ---------------------------------------------------------------------------
# API key presence (never value)
# ---------------------------------------------------------------------------


def test_doctor_shows_key_set_when_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-value-never-printed")
    result = runner.invoke(app, ["doctor"])
    assert "fake-key-value-never-printed" not in result.output
    assert "GEMINI_API_KEY" in result.output


def test_doctor_shows_missing_when_absent(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert "missing" in result.output or "·" in result.output


# ---------------------------------------------------------------------------
# --snapshot (mocked npx)
# ---------------------------------------------------------------------------


def test_doctor_snapshot_calls_npx_hyperframes_add():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        # Create a fake installed file so hash computation works.
        import tempfile
        cwd = kwargs.get("cwd", ".")
        from pathlib import Path
        (Path(cwd) / "fake.html").write_text("<template></template>", encoding="utf-8")
        return _proc()

    with patch("framecraft.cli_doctor.subprocess.run", side_effect=fake_run):
        with patch("framecraft.cli_doctor.shutil.which", return_value="/usr/bin/npx"):
            result = runner.invoke(app, ["doctor", "--snapshot", "some-block"])

    assert any("hyperframes" in " ".join(c) and "add" in c for c in calls)


def test_doctor_snapshot_no_npx_exits_1():
    with patch("framecraft.cli_doctor.shutil.which", return_value=None):
        result = runner.invoke(app, ["doctor", "--snapshot", "some-block"])
    assert result.exit_code == 1
    assert "npx not found" in result.output


# ---------------------------------------------------------------------------
# --refresh
# ---------------------------------------------------------------------------


def test_doctor_refresh_clears_cache_files(tmp_path):
    cache_file = tmp_path / "gemini-caches.json"
    cache_file.write_text("{}", encoding="utf-8")

    with patch("framecraft.cli_doctor.platformdirs.user_cache_dir", return_value=str(tmp_path)):
        result = runner.invoke(app, ["doctor", "--refresh"])

    assert result.exit_code == 0
    assert not cache_file.exists()
    assert "Cleared" in result.output or "gemini-caches.json" in result.output


def test_doctor_refresh_no_files_reports_none(tmp_path):
    with patch("framecraft.cli_doctor.platformdirs.user_cache_dir", return_value=str(tmp_path)):
        result = runner.invoke(app, ["doctor", "--refresh"])

    assert result.exit_code == 0
    assert "No cache files" in result.output
