"""Scaffold wrapping `npx hyperframes init`. See `.claude/plans/05-scaffold-lint-repair.md`."""

from __future__ import annotations

from pathlib import Path

from framecraft._compat import (
    EXPECTED_INIT_DIRS,
    EXPECTED_INIT_FILES,
    HYPERFRAMES_VERSION_FLOOR,
)
from framecraft.exit_codes import ToolchainError
from framecraft.subprocess_helpers import check_hyperframes_version, run_npx


def scaffold(out_dir: Path) -> str:
    """Create a blank Hyperframes project at `out_dir`.

    Returns the detected hyperframes CLI version on success.
    Idempotent: if `out_dir` already contains a `.framecraft/` directory
    (previous FrameCraft run), we skip init and just ensure scaffolding
    artifacts still look healthy.
    """
    is_existing = out_dir.exists() and any(out_dir.iterdir())
    is_framecraft_project = (out_dir / ".framecraft").exists()

    if is_existing and not is_framecraft_project:
        raise ToolchainError(
            f"{out_dir} exists and is not a FrameCraft project. "
            "Remove it or pick another --out path."
        )

    version = check_hyperframes_version(HYPERFRAMES_VERSION_FLOOR)

    if not is_framecraft_project:
        # `npx hyperframes init` creates the directory itself; pass the parent as cwd
        # so relative paths work, and pass the target dir as the argument.
        parent = out_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        run_npx(
            [
                "hyperframes",
                "init",
                str(out_dir),
                "--example", "blank",
                "--non-interactive",
                "--skip-transcribe",
            ],
            cwd=parent,
        )

    _verify_init_output(out_dir)
    _patch_gitignore(out_dir)
    return version


def _verify_init_output(out_dir: Path) -> None:
    missing_files = [f for f in EXPECTED_INIT_FILES if not (out_dir / f).is_file()]
    missing_dirs = [d for d in EXPECTED_INIT_DIRS if not (out_dir / d).is_dir()]
    if missing_files or missing_dirs:
        raise ToolchainError(
            "Hyperframes init output looks incomplete. "
            f"Missing files: {missing_files}; missing dirs: {missing_dirs}. "
            "Possibly an upstream CLI change — check framecraft/_compat.py."
        )


def _patch_gitignore(out_dir: Path) -> None:
    path = out_dir / ".gitignore"
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    needed = [".framecraft/", "renders/"]
    changed = False
    for entry in needed:
        if entry not in lines:
            lines.append(entry)
            changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
