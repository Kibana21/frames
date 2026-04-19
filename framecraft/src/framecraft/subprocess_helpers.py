"""Subprocess wrappers with consistent error mapping."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from framecraft.exit_codes import ToolchainError


def run_npx(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    """Run `npx <args>` capturing stdout/stderr as text.

    Raises ToolchainError on binary missing or non-zero exit.
    """
    if shutil.which("npx") is None:
        raise ToolchainError("npx not found. Install Node.js (https://nodejs.org).")

    cwd.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["npx", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolchainError(
            f"`npx {' '.join(args)}` timed out after {timeout}s",
            stderr=(e.stderr or b"").decode(errors="replace"),
        ) from e
    except FileNotFoundError as e:
        raise ToolchainError("npx not found while running subprocess.") from e

    if result.returncode != 0:
        raise ToolchainError(
            f"`npx {' '.join(args)}` failed with exit code {result.returncode}",
            stderr=result.stderr,
            returncode=result.returncode,
        )
    return result


def check_hyperframes_version(floor: str) -> str:
    """Returns the installed hyperframes version string, or raises ToolchainError."""
    from pathlib import Path as _P

    result = run_npx(["hyperframes", "--version"], cwd=_P.cwd())
    version = (result.stdout or result.stderr).strip().splitlines()[-1].strip()
    # Strip any leading "v" and noise; accept semver-ish leading token.
    version = version.lstrip("v")

    def tup(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split(".")[:3] if p.isdigit())

    if tup(version) < tup(floor):
        raise ToolchainError(
            f"hyperframes CLI {version} is below pinned floor {floor}. "
            f"Run `npm i -g hyperframes` to update or bump _compat.HYPERFRAMES_VERSION_FLOOR."
        )
    return version
