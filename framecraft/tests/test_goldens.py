"""Golden snapshot tests. See `.claude/plans/07-observability-and-goldens.md` §6–11.

Tests use `--dry-run` so no LLM fixtures are needed in CI. To run against
recorded stub fixtures use `--provider stub:gemini` and ensure
`tests/fixtures/llm/gemini/` contains fixture files (see scripts/record_fixture.py).

Usage:
  pytest tests/test_goldens.py -v               # compare against committed goldens
  pytest tests/test_goldens.py --update-goldens  # regenerate goldens after code change
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from framecraft.cli import app

runner = CliRunner()

GOLDENS = Path(__file__).parent / "goldens"
SITUATIONS = ["narrative", "product-promo", "data-explainer"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compose_dry_run(situation: str, out_dir: Path) -> None:
    """Invoke compose --dry-run for the given situation into out_dir."""
    result = runner.invoke(
        app,
        [
            "compose",
            situation,
            "--out", str(out_dir),
            "--dry-run",
            "--no-config",
        ],
    )
    if result.exit_code != 0:
        raise AssertionError(
            f"compose --dry-run failed (exit {result.exit_code}):\n{result.output}"
        )


def _collect_tree(root: Path) -> dict[str, str]:
    """Return {relative_path: content} for all files under root (excluding .framecraft/)."""
    files: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        parts = rel.parts
        if parts and parts[0] == ".framecraft":
            continue
        files[str(rel)] = f.read_text(encoding="utf-8", errors="replace")
    return files


def _assert_tree_equal(
    expected_dir: Path,
    actual_dir: Path,
    *,
    update: bool,
    situation: str,
) -> None:
    actual_tree = _collect_tree(actual_dir)
    expected_tree = _collect_tree(expected_dir) if expected_dir.exists() else {}

    mismatches: list[str] = []

    # Files present in actual but missing in expected
    for path in sorted(set(actual_tree) - set(expected_tree)):
        mismatches.append(f"  + {path} (new)")

    # Files present in expected but missing in actual
    for path in sorted(set(expected_tree) - set(actual_tree)):
        mismatches.append(f"  - {path} (removed)")

    # Files present in both but different
    first_diff_output: list[str] = []
    for path in sorted(set(actual_tree) & set(expected_tree)):
        if actual_tree[path] != expected_tree[path]:
            mismatches.append(f"  ≠ {path}")
            if not first_diff_output:
                diff = list(difflib.unified_diff(
                    expected_tree[path].splitlines(keepends=True),
                    actual_tree[path].splitlines(keepends=True),
                    fromfile=f"expected/{path}",
                    tofile=f"actual/{path}",
                    n=3,
                ))
                first_diff_output = diff[:40]

    if not mismatches:
        return  # pass

    # Print diagnostics
    print(f"\n[golden:{situation}] Mismatch ({len(mismatches)} file(s)):")
    for line in mismatches:
        print(line)
    if first_diff_output:
        print(f"\nFirst diff ({min(40, len(first_diff_output))} lines):")
        print("".join(first_diff_output))

    if update:
        # Overwrite expected with actual (still fail so CI doesn't silently accept).
        if expected_dir.exists():
            shutil.rmtree(expected_dir)
        shutil.copytree(actual_dir, expected_dir, ignore=shutil.ignore_patterns(".framecraft"))
        print(f"\n[golden:{situation}] Updated expected/ → commit the changes.")

    pytest.fail(
        f"Golden mismatch for '{situation}' ({len(mismatches)} file(s)). "
        + ("Expected/ updated — re-run without --update-goldens to confirm." if update
           else "Run with --update-goldens to regenerate.")
    )


# ---------------------------------------------------------------------------
# Golden tests
# ---------------------------------------------------------------------------


@pytest.mark.golden
@pytest.mark.parametrize("situation", SITUATIONS)
def test_golden_dry_run(situation: str, tmp_path: Path, update_goldens: bool) -> None:
    """Compose with --dry-run and compare output to committed expected/ tree."""
    situation_file = GOLDENS / situation / "situation.txt"
    assert situation_file.exists(), f"Missing {situation_file}"

    brief_text = situation_file.read_text(encoding="utf-8").strip()
    expected_dir = GOLDENS / situation / "expected"

    _compose_dry_run(brief_text, tmp_path)

    if not expected_dir.exists() and not update_goldens:
        # First run: auto-seed the expected/ tree and pass (not a regression).
        shutil.copytree(tmp_path, expected_dir, ignore=shutil.ignore_patterns(".framecraft"))
        return

    _assert_tree_equal(expected_dir, tmp_path, update=update_goldens, situation=situation)


# ---------------------------------------------------------------------------
# Stub-provider golden tests (require fixture files)
# ---------------------------------------------------------------------------


def _stub_fixture_dir_nonempty(provider: str) -> bool:
    """True if there are at least one fixture json in the stub dir."""
    name = provider.split(":")[-1] if ":" in provider else provider
    d = Path(__file__).parent / "fixtures" / "llm" / name
    return d.exists() and any(d.glob("*.json"))


@pytest.mark.golden
@pytest.mark.parametrize("situation", SITUATIONS)
@pytest.mark.parametrize("provider", ["stub:gemini", "stub:anthropic"])
def test_golden_stub_provider(
    situation: str,
    provider: str,
    tmp_path: Path,
    update_goldens: bool,
) -> None:
    """Compose with stub provider and compare to dry-run golden (provider-agnostic)."""
    if not _stub_fixture_dir_nonempty(provider):
        pytest.skip(f"No fixture files for {provider}. Run scripts/record_fixture.py.")

    situation_file = GOLDENS / situation / "situation.txt"
    brief_text = situation_file.read_text(encoding="utf-8").strip()
    expected_dir = GOLDENS / situation / "expected"

    result = runner.invoke(
        app,
        [
            "compose", brief_text,
            "--out", str(tmp_path),
            "--provider", provider,
            "--no-config",
        ],
    )
    if result.exit_code != 0:
        pytest.fail(f"compose --provider {provider} failed:\n{result.output}")

    if not expected_dir.exists():
        pytest.skip("No expected/ for comparison. Run test_golden_dry_run first.")

    _assert_tree_equal(expected_dir, tmp_path, update=update_goldens, situation=situation)
