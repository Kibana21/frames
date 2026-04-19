"""Unit tests for lint rule classification. See `.claude/plans/05-scaffold-lint-repair.md` §6."""

from __future__ import annotations

import pytest

from framecraft.lint import LintFinding
from framecraft.lint_policy import (
    FRAMECRAFT_BUG_RULES,
    LLM_REPAIRABLE_RULES,
    classify,
)


def _finding(rule: str, severity: str = "error") -> LintFinding:
    return LintFinding(rule=rule, severity=severity, file="compositions/scene-00.html")


# ---------------------------------------------------------------------------
# Every FRAMECRAFT_BUG rule → "framecraft_bug"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", sorted(FRAMECRAFT_BUG_RULES))
def test_framecraft_bug_rules(rule: str) -> None:
    assert classify(_finding(rule)) == "framecraft_bug"


# ---------------------------------------------------------------------------
# Every LLM_REPAIRABLE rule → "llm_repairable"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", sorted(LLM_REPAIRABLE_RULES))
def test_llm_repairable_rules(rule: str) -> None:
    assert classify(_finding(rule)) == "llm_repairable"


# ---------------------------------------------------------------------------
# Unknown rule → "framecraft_bug" (fail loud, §7.6 drift mitigation)
# ---------------------------------------------------------------------------


def test_unknown_rule_classified_as_framecraft_bug() -> None:
    assert classify(_finding("some-future-upstream-rule")) == "framecraft_bug"


def test_another_unknown_rule() -> None:
    assert classify(_finding("x-y-z-new-rule-2025")) == "framecraft_bug"


# ---------------------------------------------------------------------------
# Sets are disjoint — a rule can't be in both
# ---------------------------------------------------------------------------


def test_rule_sets_are_disjoint() -> None:
    overlap = FRAMECRAFT_BUG_RULES & LLM_REPAIRABLE_RULES
    assert not overlap, f"Rules appear in both sets: {overlap}"


# ---------------------------------------------------------------------------
# Warning-severity findings still classified by rule
# ---------------------------------------------------------------------------


def test_warning_severity_classified_by_rule() -> None:
    assert classify(_finding("copy-too-long", severity="warning")) == "llm_repairable"
    assert classify(_finding("duplicate-composition-id", severity="warning")) == "framecraft_bug"
