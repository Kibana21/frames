"""`framecraft render` — render a Hyperframes project to MP4."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from framecraft.exit_codes import ExitCode, FrameCraftExit, ToolchainError
from framecraft.subprocess_helpers import run_npx

_console = Console()


def render(
    out: Path = typer.Option(Path("."), "--out", "-o", help="Hyperframes project directory."),
    format: Literal["mp4", "webm", "mov"] = typer.Option("mp4", "--format"),
    quality: Literal["draft", "standard", "high"] = typer.Option("standard", "--quality"),
    fps: int | None = typer.Option(None, "--fps", help="Override FPS (default: inherit from project)."),
) -> None:
    """Render a Hyperframes project to video."""
    try:
        invoke_render(out=out, format=format, quality=quality, fps=fps)
    except FrameCraftExit as e:
        _console.print(f"[red]error:[/red] {e.message}")
        raise typer.Exit(code=e.code)


def invoke_render(
    *,
    out: Path,
    format: str = "mp4",
    quality: str = "standard",
    fps: int | None = None,
) -> None:
    """Call npx hyperframes render; raise FrameCraftExit(RENDER) on failure."""
    out = out.resolve()
    args = ["hyperframes", "render", "--format", format, "--quality", quality]
    if fps is not None:
        args += ["--fps", str(fps)]

    try:
        result = run_npx(args, cwd=out)
    except ToolchainError as e:
        _write_render_log(out, e.stderr or "")
        first = (e.stderr or "").splitlines()[0] if e.stderr else "see .framecraft/render-stderr.log"
        raise FrameCraftExit(ExitCode.RENDER, f"render failed: {first}") from e

    _print_render_path(out, result.stdout)


def _print_render_path(out: Path, npx_stdout: str) -> None:
    renders_dir = out / "renders"
    if not renders_dir.exists():
        return
    # Prefer newest file in renders/
    files = sorted(renders_dir.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if files:
        _console.print(f"[green]✓ Rendered:[/green] {files[0]}")
    else:
        _console.print("[green]✓ Render complete.[/green]")


def _write_render_log(out: Path, stderr: str) -> None:
    log_dir = out / ".framecraft"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "render-stderr.log").write_text(stderr, encoding="utf-8")
