"""`framecraft doctor` — environment diagnostics."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import platformdirs
import typer
from rich.console import Console
from rich.table import Table

from framecraft._compat import HYPERFRAMES_VERSION_FLOOR

_console = Console()


def doctor(
    snapshot: str | None = typer.Option(
        None, "--snapshot", help="Re-pin catalog block hash (prints line to paste).",
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Clear provider caches.",
    ),
) -> None:
    """Report on toolchain, provider, and API-key presence (never values)."""
    if snapshot:
        _snapshot_block(snapshot)
        return
    if refresh:
        _refresh_caches()
        return
    _run_checks()


def _run_checks() -> None:
    rows: list[tuple[str, str, str]] = []

    node = _bin_version("node", ["--version"])
    rows.append(("node", _ok(node), _summary(node, min_version="v20")))

    npx = shutil.which("npx") or ""
    rows.append(("npx", "✓" if npx else "✗", npx or "not found"))

    hf = _bin_version("npx", ["hyperframes", "--version"])
    rows.append((
        "hyperframes",
        _ok(hf, floor=HYPERFRAMES_VERSION_FLOOR),
        _summary(hf, min_version=HYPERFRAMES_VERSION_FLOOR),
    ))

    ff = _bin_version("ffmpeg", ["-version"])
    rows.append(("ffmpeg", _ok(ff), _summary(ff, first_line_only=True)))

    provider = os.environ.get("FRAMECRAFT_PROVIDER", "gemini (default; M0 requires stub or --dry-run)")
    rows.append(("FRAMECRAFT_PROVIDER", "·", provider))

    rows.append(("GEMINI_API_KEY", _presence("GEMINI_API_KEY", alt="GOOGLE_API_KEY"), "set" if _key("GEMINI_API_KEY", "GOOGLE_API_KEY") else "missing"))
    rows.append(("ANTHROPIC_API_KEY", _presence("ANTHROPIC_API_KEY"), "set" if _key("ANTHROPIC_API_KEY") else "missing"))

    table = Table(title="framecraft doctor", show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status, detail in rows:
        style = "green" if status == "✓" else ("red" if status == "✗" else "yellow")
        table.add_row(name, f"[{style}]{status}[/{style}]", detail)
    _console.print(table)

    # Exit non-zero if any critical row is ✗
    critical = {"node", "npx", "hyperframes", "ffmpeg"}
    failed = [n for n, s, _ in rows if n in critical and s == "✗"]
    if failed:
        raise typer.Exit(code=1)


def _bin_version(cmd: str, args: list[str]) -> str | None:
    if shutil.which(cmd) is None:
        return None
    try:
        result = subprocess.run(
            [cmd, *args], capture_output=True, text=True, timeout=15, check=False
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or result.stderr).strip()
    return raw.splitlines()[0] if raw else None


def _ok(value: str | None, *, floor: str | None = None) -> str:
    if value is None:
        return "✗"
    if floor:
        cur = _numeric_semver(value)
        fl = _numeric_semver(floor)
        if cur < fl:
            return "⚠"
    return "✓"


def _summary(value: str | None, *, min_version: str | None = None, first_line_only: bool = False) -> str:
    if value is None:
        return "not found"
    if first_line_only:
        return value
    if min_version:
        return f"{value} (min {min_version})"
    return value


def _presence(primary: str, *, alt: str | None = None) -> str:
    if _key(primary, alt):
        return "✓"
    return "·"


def _key(primary: str, alt: str | None = None) -> bool:
    if os.environ.get(primary):
        return True
    return bool(alt and os.environ.get(alt))


def _numeric_semver(s: str) -> tuple[int, ...]:
    digits = []
    for chunk in s.lstrip("v").split("."):
        acc = ""
        for ch in chunk:
            if ch.isdigit():
                acc += ch
            else:
                break
        if acc:
            digits.append(int(acc))
    return tuple(digits[:3]) or (0,)


def _snapshot_block(block_id: str) -> None:
    """Install catalog block in a temp dir, compute hash, print the BlockSpec line."""
    if shutil.which("npx") is None:
        _console.print("[red]npx not found — cannot snapshot.[/red]")
        raise typer.Exit(code=1)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        before = set(tmp.rglob("*"))
        try:
            subprocess.run(
                ["npx", "hyperframes", "add", block_id, "--non-interactive"],
                cwd=td,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            _console.print(f"[red]npx hyperframes add {block_id} failed:[/red] {e.stderr}")
            raise typer.Exit(code=1) from e

        after = set(tmp.rglob("*"))
        new_files = sorted(str(p.relative_to(tmp)) for p in (after - before) if p.is_file())

        h = hashlib.sha256()
        for rel in new_files:
            h.update(rel.encode())
            h.update((tmp / rel).read_bytes())
        digest = h.hexdigest()

    _console.print(f"\nFiles installed: {new_files}")
    _console.print(f"\nPaste into BlockSpec for [bold]{block_id}[/bold]:")
    _console.print(f'    catalog_hash="{digest}"')


def _refresh_caches() -> None:
    """Clear provider-level caches."""
    cache_dir = Path(platformdirs.user_cache_dir("framecraft"))
    cleared: list[str] = []
    for name in ("gemini-caches.json", "anthropic-caches.json"):
        target = cache_dir / name
        if target.exists():
            target.unlink()
            cleared.append(name)

    if cleared:
        _console.print(f"Cleared: {', '.join(cleared)}")
    else:
        _console.print("No cache files found to clear.")
