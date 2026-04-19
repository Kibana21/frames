"""Music validator tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from framecraft.music import MusicValidationError, validate_music


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_silent_audio(path: Path, duration: float) -> None:
    """Synthesize a silent audio file of `duration` seconds at `path`."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
            "-t", f"{duration:g}",
            str(path),
        ],
        check=True,
        timeout=30,
    )


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe required")
def test_happy_path(tmp_path: Path) -> None:
    music = tmp_path / "bed.mp3"
    _make_silent_audio(music, 12.0)
    out = validate_music(music, scene_graph_duration=10.0)
    assert out.is_absolute()


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe required")
def test_wav_allowed(tmp_path: Path) -> None:
    music = tmp_path / "bed.wav"
    _make_silent_audio(music, 12.0)
    assert validate_music(music, scene_graph_duration=5.0).exists()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MusicValidationError, match="not found"):
        validate_music(tmp_path / "nope.mp3", scene_graph_duration=5.0)


def test_bad_extension(tmp_path: Path) -> None:
    p = tmp_path / "thing.flac"
    p.write_bytes(b"\x00\x00\x00\x00")
    with pytest.raises(MusicValidationError, match="not supported"):
        validate_music(p, scene_graph_duration=5.0)


def test_directory_not_file(tmp_path: Path) -> None:
    d = tmp_path / "dir.mp3"
    d.mkdir()
    with pytest.raises(MusicValidationError, match="not a regular file"):
        validate_music(d, scene_graph_duration=5.0)


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe required")
def test_too_short(tmp_path: Path) -> None:
    music = tmp_path / "short.mp3"
    _make_silent_audio(music, 3.0)
    with pytest.raises(MusicValidationError, match="shorter|is .* but"):
        validate_music(music, scene_graph_duration=10.0)
