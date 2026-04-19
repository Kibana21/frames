"""Unit tests for scaffold. See `.claude/plans/05-scaffold-lint-repair.md` §Testing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from framecraft.exit_codes import ToolchainError
from framecraft.scaffold import _patch_gitignore, _verify_init_output, scaffold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Subprocess helper: correct argv
# ---------------------------------------------------------------------------


def test_scaffold_calls_init_with_correct_argv(tmp_path):
    out_dir = tmp_path / "project"
    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, timeout=180.0):
        calls.append(list(args))
        if "init" in args:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "hyperframes.json").write_text("{}", encoding="utf-8")
        return _make_completed(stdout="0.9.0\n")

    with (
        patch("framecraft.scaffold.run_npx", side_effect=fake_run_npx),
        patch("framecraft.scaffold.check_hyperframes_version", return_value="0.9.0"),
    ):
        scaffold(out_dir)

    init_call = next((c for c in calls if "init" in c), None)
    assert init_call is not None
    assert "hyperframes" in init_call
    assert "init" in init_call
    assert str(out_dir) in init_call
    assert "--non-interactive" in init_call
    assert "--example" in init_call


def test_scaffold_skips_init_if_framecraft_dir_exists(tmp_path):
    out_dir = tmp_path / "project"
    out_dir.mkdir()
    (out_dir / ".framecraft").mkdir()
    (out_dir / "hyperframes.json").write_text("{}", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run_npx(args, *, cwd, timeout=180.0):
        calls.append(list(args))
        return _make_completed(stdout="0.9.0\n")

    with (
        patch("framecraft.scaffold.run_npx", side_effect=fake_run_npx),
        patch("framecraft.scaffold.check_hyperframes_version", return_value="0.9.0"),
    ):
        scaffold(out_dir)

    assert not any("init" in c for c in calls)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_scaffold_raises_on_nonzero_exit(tmp_path):
    out_dir = tmp_path / "project"

    with (
        patch("framecraft.scaffold.check_hyperframes_version", return_value="0.9.0"),
        patch(
            "framecraft.scaffold.run_npx",
            side_effect=ToolchainError("`npx hyperframes init` failed", returncode=1),
        ),
    ):
        with pytest.raises(ToolchainError, match="failed"):
            scaffold(out_dir)


def test_scaffold_raises_on_non_framecraft_existing_dir(tmp_path):
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    (out_dir / "unrelated.txt").write_text("data", encoding="utf-8")

    with pytest.raises(ToolchainError, match="not a FrameCraft project"):
        scaffold(out_dir)


def test_scaffold_raises_on_missing_expected_files(tmp_path):
    out_dir = tmp_path / "project"
    out_dir.mkdir()
    # Do NOT create hyperframes.json — simulate init drift.

    with pytest.raises(ToolchainError, match="incomplete|drift"):
        _verify_init_output(out_dir)


# ---------------------------------------------------------------------------
# _patch_gitignore
# ---------------------------------------------------------------------------


def test_patch_gitignore_adds_entries(tmp_path):
    _patch_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert ".framecraft/" in content
    assert "renders/" in content


def test_patch_gitignore_is_idempotent(tmp_path):
    _patch_gitignore(tmp_path)
    content_first = (tmp_path / ".gitignore").read_text()
    _patch_gitignore(tmp_path)
    content_second = (tmp_path / ".gitignore").read_text()
    assert content_first == content_second
    assert content_first.count(".framecraft/") == 1


def test_patch_gitignore_preserves_existing_lines(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")
    _patch_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert "node_modules/" in content
    assert ".env" in content
    assert ".framecraft/" in content


# ---------------------------------------------------------------------------
# Existing FrameCraft project reuse
# ---------------------------------------------------------------------------


def test_scaffold_existing_framecraft_dir_succeeds(tmp_path):
    out_dir = tmp_path / "project"
    out_dir.mkdir()
    (out_dir / ".framecraft").mkdir()
    (out_dir / "hyperframes.json").write_text("{}", encoding="utf-8")

    with (
        patch("framecraft.scaffold.run_npx", return_value=_make_completed(stdout="0.9.0\n")),
        patch("framecraft.scaffold.check_hyperframes_version", return_value="0.9.0"),
    ):
        version = scaffold(out_dir)

    assert version == "0.9.0"
