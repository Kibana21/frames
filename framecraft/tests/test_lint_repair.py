"""Integration tests for lint_repair. See `.claude/plans/05-scaffold-lint-repair.md` §Testing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from framecraft.assembler import Assembler
from framecraft.lint import (
    FrameCraftBugError,
    LintFailedAfterRepairError,
    LintFinding,
    LintReport,
    LintResult,
    lint_repair,
)
from framecraft.registry import default_registry
from framecraft.schema import SceneGraph

FIXTURE_PLAN = Path(__file__).parent / "fixtures" / "plans" / "product-promo.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plan() -> SceneGraph:
    return SceneGraph.model_validate_json(FIXTURE_PLAN.read_text())


@pytest.fixture
def assembled_out(tmp_path, plan) -> Path:
    """A real assembled project dir (no npx required)."""
    from unittest.mock import MagicMock

    out = tmp_path / "out"
    out.mkdir()
    provider = MagicMock()
    provider.name = "stub"
    assembler = Assembler(default_registry(), provider)
    assembler.assemble(plan, out, project_name="Test", project_id="test")
    return out


class _StubAssembler:
    """Tracks repair calls without doing anything (or does a controlled fix)."""

    def __init__(self, *, fix: bool = False):
        self.repair_calls: list[list[dict]] = []
        self._fix = fix

    def repair(self, out_dir: Path, plan: SceneGraph, errors_only: list[dict]) -> None:
        self.repair_calls.append(list(errors_only))


def _report(errors: list[tuple[str, str]] = (), warnings: list[tuple[str, str]] = ()) -> LintReport:
    def _f(rule, severity):
        return LintFinding(rule=rule, severity=severity, file="compositions/x.html")

    return LintReport(
        errors=[_f(r, s) for r, s in errors],
        warnings=[_f(r, s) for r, s in warnings],
    )


# ---------------------------------------------------------------------------
# Scenario A — LLM_REPAIRABLE error → repair → passes
# ---------------------------------------------------------------------------


def test_llm_repairable_error_triggers_repair_and_passes(assembled_out, plan):
    stub = _StubAssembler()
    reports = iter([
        _report(errors=[("copy-too-long", "error")]),
        _report(),  # clean after repair
    ])

    with patch("framecraft.lint.run_lint", side_effect=lambda _: next(reports)):
        result = lint_repair(assembled_out, stub, plan)

    assert result.passed is True
    assert result.repaired is True
    assert len(stub.repair_calls) == 1
    assert stub.repair_calls[0][0]["rule"] == "copy-too-long"


def test_repair_receives_all_llm_repairable_errors(assembled_out, plan):
    stub = _StubAssembler()
    reports = iter([
        _report(errors=[
            ("copy-too-long", "error"),
            ("missing-clip-class", "error"),
        ]),
        _report(),
    ])

    with patch("framecraft.lint.run_lint", side_effect=lambda _: next(reports)):
        lint_repair(assembled_out, stub, plan)

    assert len(stub.repair_calls[0]) == 2


# ---------------------------------------------------------------------------
# Scenario B — FRAMECRAFT_BUG error → FrameCraftBugError, repair NOT called
# ---------------------------------------------------------------------------


def test_framecraft_bug_raises_and_no_repair(assembled_out, plan):
    stub = _StubAssembler()
    report = _report(errors=[("duplicate-composition-id", "error")])

    with patch("framecraft.lint.run_lint", return_value=report):
        with pytest.raises(FrameCraftBugError) as exc_info:
            lint_repair(assembled_out, stub, plan)

    assert len(stub.repair_calls) == 0
    assert "duplicate-composition-id" in str(exc_info.value)
    assert exc_info.value.code == 2


def test_framecraft_bug_persists_lint_report(assembled_out, plan):
    stub = _StubAssembler()
    report = _report(errors=[("missing-template-wrapper", "error")])

    with patch("framecraft.lint.run_lint", return_value=report):
        with pytest.raises(FrameCraftBugError):
            lint_repair(assembled_out, stub, plan)

    report_path = assembled_out / ".framecraft" / "lint-report.json"
    assert report_path.exists()
    saved = json.loads(report_path.read_text())
    assert len(saved["errors"]) == 1


def test_unknown_rule_treated_as_framecraft_bug(assembled_out, plan):
    stub = _StubAssembler()
    report = _report(errors=[("some-new-upstream-rule", "error")])

    with patch("framecraft.lint.run_lint", return_value=report):
        with pytest.raises(FrameCraftBugError):
            lint_repair(assembled_out, stub, plan)

    assert len(stub.repair_calls) == 0


# ---------------------------------------------------------------------------
# Scenario C — warnings only → pass-through, no repair
# ---------------------------------------------------------------------------


def test_warnings_only_passes_without_repair(assembled_out, plan, capsys):
    stub = _StubAssembler()
    report = _report(warnings=[("copy-too-long", "warning")])

    with patch("framecraft.lint.run_lint", return_value=report):
        result = lint_repair(assembled_out, stub, plan)

    assert result.passed is True
    assert result.repaired is False
    assert len(stub.repair_calls) == 0

    captured = capsys.readouterr()
    assert "copy-too-long" in captured.err


def test_clean_lint_passes_without_repair(assembled_out, plan):
    stub = _StubAssembler()

    with patch("framecraft.lint.run_lint", return_value=_report()):
        result = lint_repair(assembled_out, stub, plan)

    assert result.passed is True
    assert result.repaired is False
    assert len(stub.repair_calls) == 0


# ---------------------------------------------------------------------------
# Post-repair lint still fails → LintFailedAfterRepairError
# ---------------------------------------------------------------------------


def test_lint_still_failing_after_repair_raises(assembled_out, plan):
    stub = _StubAssembler()
    reports = iter([
        _report(errors=[("copy-too-long", "error")]),
        _report(errors=[("copy-too-long", "error")]),  # repair didn't fix it
    ])

    with patch("framecraft.lint.run_lint", side_effect=lambda _: next(reports)):
        with pytest.raises(LintFailedAfterRepairError) as exc_info:
            lint_repair(assembled_out, stub, plan)

    assert exc_info.value.code == 2
    report_path = assembled_out / ".framecraft" / "lint-report.json"
    assert report_path.exists()


# ---------------------------------------------------------------------------
# Mixed errors: both FRAMECRAFT_BUG and LLM_REPAIRABLE → bug takes precedence
# ---------------------------------------------------------------------------


def test_mixed_errors_framecraft_bug_takes_precedence(assembled_out, plan):
    stub = _StubAssembler()
    report = _report(errors=[
        ("copy-too-long", "error"),
        ("duplicate-composition-id", "error"),
    ])

    with patch("framecraft.lint.run_lint", return_value=report):
        with pytest.raises(FrameCraftBugError) as exc_info:
            lint_repair(assembled_out, stub, plan)

    assert len(stub.repair_calls) == 0
    assert "duplicate-composition-id" in str(exc_info.value)
