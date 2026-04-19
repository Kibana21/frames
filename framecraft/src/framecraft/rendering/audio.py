"""Audio bed file copy for --music. HTML injection lives in rendering/root.py."""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_audio_asset(music_path: Path, out_dir: Path) -> str:
    """Copy music file to out_dir/assets/music.{ext}. Return the relative src path."""
    ext = music_path.suffix.lstrip(".").lower() or "mp3"
    dest = out_dir / "assets" / f"music.{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != music_path.resolve():
        shutil.copy2(music_path, dest)
    return f"assets/music.{ext}"
