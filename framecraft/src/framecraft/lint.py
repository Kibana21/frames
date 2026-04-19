"""Lint invocation + repair loop. See `.claude/plans/05-scaffold-lint-repair.md`."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from framecraft.exit_codes import ExitCode, FrameCraftExit, ToolchainError
from framecraft.lint_policy import classify
from framecraft.trace import atomic_write

if TYPE_CHECKING:
    from framecraft.assembler import Assembler
    from framecraft.schema import SceneGraph

__all__ = [
    "LintFinding",
    "LintReport",
    "LintResult",
    "FrameCraftBugError",
    "LintFailedAfterRepairError",
    "run_lint",
    "lint_repair",
]


class LintFinding(BaseModel):
    rule: str
    severity: Literal["error", "warning", "info"] = "error"
    file: str = ""
    line: int | None = None
    message: str = ""
    details: dict = Field(default_factory=dict)


class LintReport(BaseModel):
    errors: list[LintFinding] = Field(default_factory=list)
    warnings: list[LintFinding] = Field(default_factory=list)
    info: list[LintFinding] = Field(default_factory=list)


class LintResult(BaseModel):
    passed: bool
    repaired: bool
    report: LintReport


class FrameCraftBugError(FrameCraftExit):
    """Lint found rules classified as FrameCraft template bugs. Exit 2."""

    def __init__(self, findings: list[LintFinding]) -> None:
        rules = ", ".join(sorted({f.rule for f in findings}))
        super().__init__(
            ExitCode.LINT,
            f"lint failed with rule(s) classified as FrameCraft template bugs "
            f"({rules}) — not an LLM issue.\n"
            "Open an issue with `.framecraft/lint-report.json` attached.",
        )
        self.findings = findings


class LintFailedAfterRepairError(FrameCraftExit):
    """Lint still failing after one repair pass. Exit 2."""

    def __init__(self, findings: list[LintFinding]) -> None:
        rules = ", ".join(sorted({f.rule for f in findings}))
        super().__init__(
            ExitCode.LINT,
            f"lint still failing after one repair pass ({rules}). "
            "See `.framecraft/lint-report.json`.",
        )
        self.findings = findings


def run_lint(out_dir: Path) -> LintReport:
    """Run `npx hyperframes lint --json` and parse the result.

    Uses subprocess directly (not run_npx) because hyperframes lint may exit
    non-zero when findings exist — we need the JSON output regardless.
    """
    if shutil.which("npx") is None:
        raise ToolchainError("npx not found. Install Node.js (https://nodejs.org).")

    try:
        result = subprocess.run(
            ["npx", "hyperframes", "lint", "--json"],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=120.0,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolchainError(
            "`npx hyperframes lint` timed out after 120s",
            stderr=(e.stderr or b"").decode(errors="replace"),
        ) from e

    stdout = result.stdout.strip()
    if not stdout:
        raise ToolchainError(
            "`npx hyperframes lint --json` produced no output",
            stderr=result.stderr,
            returncode=result.returncode,
        )
    return LintReport.model_validate_json(stdout)


def lint_repair(
    out_dir: Path,
    assembler: "Assembler",
    plan: "SceneGraph",
) -> LintResult:
    """Run lint; repair LLM_REPAIRABLE errors; re-lint. Return LintResult.

    Exit 2 if findings are FRAMECRAFT_BUG or if repair doesn't clear all errors.
    """
    report = run_lint(out_dir)

    if not report.errors:
        _print_warnings(report.warnings)
        return LintResult(passed=True, repaired=False, report=report)

    framecraft_bugs = [f for f in report.errors if classify(f) == "framecraft_bug"]
    if framecraft_bugs:
        _persist_report(report, out_dir)
        raise FrameCraftBugError(framecraft_bugs)

    # All errors are LLM_REPAIRABLE — one repair pass.
    assembler.repair(out_dir, plan, errors_only=[f.model_dump() for f in report.errors])

    report2 = run_lint(out_dir)
    if report2.errors:
        _persist_report(report2, out_dir)
        raise LintFailedAfterRepairError(report2.errors)

    _print_warnings(report2.warnings)
    return LintResult(passed=True, repaired=True, report=report2)


# --- helpers -----------------------------------------------------------------


def _persist_report(report: LintReport, out_dir: Path) -> None:
    path = out_dir / ".framecraft" / "lint-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(report, path)


def _print_warnings(warnings: list[LintFinding]) -> None:
    for w in warnings:
        loc = f"{w.file}:{w.line}" if w.line else w.file
        print(f"[warn] {w.rule} {loc}: {w.message}", file=sys.stderr)
