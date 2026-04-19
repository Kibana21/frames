"""`framecraft preview` — launch the Hyperframes browser studio."""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

_console = Console()

_LOCALHOST_RE = re.compile(r"https?://localhost:\d+\S*")
_EADDRINUSE_RE = re.compile(r"EADDRINUSE", re.IGNORECASE)


def preview(
    out: Path = typer.Option(Path("."), "--out", "-o", help="Hyperframes project directory."),
    port: int = typer.Option(4000, "--port", help="Port for the preview server."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser automatically."),
) -> None:
    """Launch the Hyperframes preview server (Ctrl-C to stop)."""
    out = out.resolve()

    if shutil.which("npx") is None:
        _console.print("[red]npx not found — install Node.js (https://nodejs.org).[/red]")
        raise typer.Exit(code=1)

    cmd = ["npx", "hyperframes", "preview", "--port", str(port)]
    if not open_browser:
        cmd.append("--no-open")

    proc = subprocess.Popen(
        cmd,
        cwd=str(out),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            sys.stderr.write(line)
            m = _LOCALHOST_RE.search(line)
            if m:
                typer.secho(f"Preview ready: {m.group(0)}", fg="green")
            if _EADDRINUSE_RE.search(line):
                _console.print(f"[red]Port {port} in use. Try `--port {port + 1}`.[/red]")
                proc.terminate()
                proc.wait(timeout=5)
                raise typer.Exit(code=1)
        rc = proc.wait()
        raise SystemExit(rc)
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise SystemExit(0)
