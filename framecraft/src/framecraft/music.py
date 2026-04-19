"""Audio-bed validator for `--music` (US-016 split).

Called by the CLI before the Assembler injects an `<audio>` element. Fails
fast with an actionable message on extension mismatch, missing file, or
duration shorter than the scene graph.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a"})


class MusicValidationError(ValueError):
    """Raised when a --music path can't be used as an audio bed."""


def validate_music(path: Path, scene_graph_duration: float) -> Path:
    """Return a canonical, absolute path on success; raise on failure.

    Checks:
      1. File exists and is a regular file.
      2. Extension is in ALLOWED_EXTENSIONS (case-insensitive).
      3. Duration (via `ffprobe`) is at least `scene_graph_duration`.
         If ffprobe is not installed, the duration check is skipped with a
         clear warning-shaped error so the user knows to install it (the
         assumption: we don't want to silently accept a too-short file).
    """
    if not path.exists():
        raise MusicValidationError(f"--music path not found: {path}")
    if not path.is_file():
        raise MusicValidationError(f"--music path is not a regular file: {path}")

    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise MusicValidationError(
            f"--music extension {ext!r} not supported. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    duration = _probe_duration_seconds(path)
    if duration is None:
        raise MusicValidationError(
            "ffprobe not available; cannot verify music duration. "
            "Install ffmpeg (brew install ffmpeg) or remove --music."
        )
    if duration + 0.1 < scene_graph_duration:
        raise MusicValidationError(
            f"--music file is {duration:.2f}s but SceneGraph duration is "
            f"{scene_graph_duration:.2f}s. Trim the scene graph, loop the "
            "music externally, or provide a longer file."
        )

    return path.resolve()


def _probe_duration_seconds(path: Path) -> float | None:
    """Return duration in seconds, or None if ffprobe isn't available."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None
