"""Unit tests for `framecraft from-plan`. See `.claude/plans/06a-cli-core.md` §12–13."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from framecraft.cli import app
from framecraft.schema import SceneGraph

runner = CliRunner()

FIXTURE_PLAN = Path(__file__).parent / "fixtures" / "plans" / "product-promo.json"


@pytest.fixture
def assembled_dir(tmp_path) -> Path:
    """A directory with a valid plan.json (no npx required)."""
    plan_text = FIXTURE_PLAN.read_text()
    plan = SceneGraph.model_validate_json(plan_text)
    (tmp_path / ".framecraft").mkdir()
    (tmp_path / "plan.json").write_text(plan_text, encoding="utf-8")
    return tmp_path


def _mock_assemble(plan, out_dir, **kw):
    """Stub assemble — creates the minimal expected files."""
    (out_dir / "compositions").mkdir(exist_ok=True)
    (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (out_dir / "meta.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_from_plan_no_director_called(assembled_dir):
    with (
        patch("framecraft.cli_from_plan.Assembler") as MockAssembler,
        patch("framecraft.cli_from_plan.lint_repair") as mock_lint,
        patch("framecraft.cli_from_plan.make_provider") as mock_provider,
    ):
        mock_provider.return_value = MagicMock(name="stub")
        instance = MagicMock()
        instance.assemble.side_effect = lambda plan, out_dir, **kw: _mock_assemble(plan, out_dir, **kw)
        MockAssembler.return_value = instance
        mock_lint.return_value = MagicMock(passed=True, repaired=False)

        result = runner.invoke(app, ["from-plan", str(assembled_dir / "plan.json")])

    assert result.exit_code == 0, result.output
    instance.assemble.assert_called_once()
    mock_lint.assert_called_once()
    # Director was never imported or called
    assert "Director" not in str(result.output)


def test_from_plan_success_prints_paths(assembled_dir):
    with (
        patch("framecraft.cli_from_plan.Assembler") as MockAssembler,
        patch("framecraft.cli_from_plan.lint_repair") as mock_lint,
        patch("framecraft.cli_from_plan.make_provider") as mock_provider,
    ):
        mock_provider.return_value = MagicMock(name="stub")
        instance = MagicMock()
        instance.assemble.side_effect = lambda plan, out_dir, **kw: _mock_assemble(plan, out_dir, **kw)
        MockAssembler.return_value = instance
        mock_lint.return_value = MagicMock(passed=True, repaired=False)

        result = runner.invoke(app, ["from-plan", str(assembled_dir / "plan.json")])

    assert "plan.json" in result.output
    assert "index.html" in result.output


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_from_plan_missing_file_exits_1(tmp_path):
    result = runner.invoke(app, ["from-plan", str(tmp_path / "nonexistent.json")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_from_plan_invalid_json_exits_1(tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text('{"version": 1, "bad": true}', encoding="utf-8")
    result = runner.invoke(app, ["from-plan", str(bad)])
    assert result.exit_code == 1
    assert "Invalid plan.json" in result.output


# ---------------------------------------------------------------------------
# Diff output
# ---------------------------------------------------------------------------


def test_from_plan_shows_diff_when_last_plan_exists(assembled_dir):
    plan_text = (assembled_dir / "plan.json").read_text()
    plan_data = json.loads(plan_text)

    # Mutate duration in last-plan.json to trigger a diff.
    old_data = json.loads(plan_text)
    old_data["duration"] = 999.0
    (assembled_dir / ".framecraft" / "last-plan.json").write_text(
        json.dumps(old_data), encoding="utf-8"
    )

    with (
        patch("framecraft.cli_from_plan.Assembler") as MockAssembler,
        patch("framecraft.cli_from_plan.lint_repair") as mock_lint,
        patch("framecraft.cli_from_plan.make_provider") as mock_provider,
    ):
        mock_provider.return_value = MagicMock(name="stub")
        instance = MagicMock()
        instance.assemble.side_effect = lambda plan, out_dir, **kw: _mock_assemble(plan, out_dir, **kw)
        MockAssembler.return_value = instance
        mock_lint.return_value = MagicMock(passed=True, repaired=False)

        result = runner.invoke(app, ["from-plan", str(assembled_dir / "plan.json")])

    assert "changed" in result.output or "Changes" in result.output


def test_from_plan_no_diff_when_no_last_plan(assembled_dir):
    with (
        patch("framecraft.cli_from_plan.Assembler") as MockAssembler,
        patch("framecraft.cli_from_plan.lint_repair") as mock_lint,
        patch("framecraft.cli_from_plan.make_provider") as mock_provider,
    ):
        mock_provider.return_value = MagicMock(name="stub")
        instance = MagicMock()
        instance.assemble.side_effect = lambda plan, out_dir, **kw: _mock_assemble(plan, out_dir, **kw)
        MockAssembler.return_value = instance
        mock_lint.return_value = MagicMock(passed=True, repaired=False)

        result = runner.invoke(app, ["from-plan", str(assembled_dir / "plan.json")])

    # No "Changes since" header when there's no last-plan.json
    assert "Changes since last assembly" not in result.output


# ---------------------------------------------------------------------------
# Repaired = True shows message
# ---------------------------------------------------------------------------


def test_from_plan_shows_repaired_message(assembled_dir):
    with (
        patch("framecraft.cli_from_plan.Assembler") as MockAssembler,
        patch("framecraft.cli_from_plan.lint_repair") as mock_lint,
        patch("framecraft.cli_from_plan.make_provider") as mock_provider,
    ):
        mock_provider.return_value = MagicMock(name="stub")
        instance = MagicMock()
        instance.assemble.side_effect = lambda plan, out_dir, **kw: _mock_assemble(plan, out_dir, **kw)
        MockAssembler.return_value = instance
        mock_lint.return_value = MagicMock(passed=True, repaired=True)

        result = runner.invoke(app, ["from-plan", str(assembled_dir / "plan.json")])

    assert "repaired" in result.output
