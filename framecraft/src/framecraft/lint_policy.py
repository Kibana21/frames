"""Rule classification for lint findings. See `.claude/plans/05-scaffold-lint-repair.md` §6."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from framecraft.lint import LintFinding

FRAMECRAFT_BUG_RULES: frozenset[str] = frozenset({
    "duplicate-composition-id",
    "missing-timeline-registration",
    "invalid-composition-src-path",
    "duplicate-element-id",
    "missing-template-wrapper",
    "missing-data-width",
    "missing-data-height",
    "clip-class-on-video",
    "clip-class-on-audio",
})

LLM_REPAIRABLE_RULES: frozenset[str] = frozenset({
    "copy-too-long",
    "missing-clip-class",
    "inconsistent-data-start-reference",
    "unknown-media-path",
})


def classify(finding: "LintFinding") -> Literal["framecraft_bug", "llm_repairable", "unknown"]:
    """Return the class of a lint finding.

    Unknown rules are treated like framecraft_bug (fail loud) so new upstream
    rules don't silently become LLM-repairable. See §7.6 upstream drift risk.
    """
    rule = finding.rule
    if rule in LLM_REPAIRABLE_RULES:
        return "llm_repairable"
    # Both FRAMECRAFT_BUG_RULES and unknown rules → framecraft_bug.
    return "framecraft_bug"
