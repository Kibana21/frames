"""`framecraft from-plan` — re-assemble from an existing plan.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from framecraft.assembler import Assembler
from framecraft.cli_render import invoke_render
from framecraft.exit_codes import ExitCode, FrameCraftExit, ToolchainError
from framecraft.lint import FrameCraftBugError, LintFailedAfterRepairError, lint_repair
from framecraft.music import MusicValidationError, validate_music
from framecraft.providers import make_provider
from framecraft.providers.base import ProviderError
from framecraft.registry import default_registry
from framecraft.schema import SceneGraph

_console = Console()
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def from_plan(
    plan_path: Path = typer.Argument(..., help="Path to plan.json to re-assemble."),
    render: bool = typer.Option(False, "--render", help="Render to MP4 after assembly."),
    music: Path | None = typer.Option(None, "--music", help="Audio bed to mix in (mp3/wav/m4a)."),
    music_volume: float = typer.Option(0.4, "--music-volume", help="Audio bed volume 0.0–1.0."),
    provider: str | None = typer.Option(
        None, "--provider", envvar="FRAMECRAFT_PROVIDER",
        help="LLM provider for polish (gemini|anthropic|stub).",
    ),
) -> None:
    """Re-assemble a Hyperframes project from an existing plan.json (no Director)."""
    try:
        if not plan_path.exists():
            raise FrameCraftExit(ExitCode.USAGE, f"plan.json not found: {plan_path}")

        plan_text = plan_path.read_text(encoding="utf-8")
        try:
            plan = SceneGraph.model_validate_json(plan_text)
        except Exception as e:
            raise FrameCraftExit(ExitCode.USAGE, f"Invalid plan.json: {e}") from e

        out_dir = plan_path.parent

        if music is not None:
            try:
                music = validate_music(music, plan.duration)
            except MusicValidationError as e:
                raise FrameCraftExit(ExitCode.USAGE, str(e)) from e
            plan = plan.model_copy(update={"brief": plan.brief.model_copy(update={"music_path": music, "music_volume": music_volume})})

        # --- Diff summary vs previous assembly
        last_path = out_dir / ".framecraft" / "last-plan.json"
        if last_path.exists():
            try:
                last = json.loads(last_path.read_text(encoding="utf-8"))
                current = json.loads(plan_text)
                diffs = _diff_plans(last, current)
                if diffs:
                    _console.print("[bold]Changes since last assembly:[/bold]")
                    for line in diffs:
                        _console.print(f"  {line}")
                else:
                    _console.print("No changes detected since last assembly.")
            except Exception:
                pass  # non-fatal

        # --- Provider
        try:
            provider_obj = make_provider(provider)
        except ProviderError as e:
            raise FrameCraftExit(ExitCode.USAGE, str(e)) from e

        registry = default_registry()

        # --- Assemble (no Director)
        _console.print(f"[1/3] Assembling {len(plan.scenes)} scenes…")
        assembler = Assembler(registry, provider_obj)
        assembler.assemble(
            plan,
            out_dir,
            project_name=plan.brief.situation[:60],
            project_id=_slug(plan.brief.situation)[:40] or "framecraft-project",
        )

        # --- Lint-repair
        _console.print("[2/3] Linting + repair…")
        try:
            result = lint_repair(out_dir, assembler, plan)
            if result.repaired:
                _console.print("      repaired 1 pass")
        except (FrameCraftBugError, LintFailedAfterRepairError) as e:
            raise FrameCraftExit(e.code, e.message) from e

        # --- Render
        if render:
            _console.print("[3/3] Rendering…")
            invoke_render(out=out_dir)
        else:
            _console.print("[3/3] Done (use --render to generate MP4).")

        _console.print()
        _console.print("[green]✓ Done.[/green]")
        _console.print(f"  plan: {plan_path.resolve()}")
        _console.print(f"  root: {(out_dir / 'index.html').resolve()}")

    except ToolchainError as e:
        _fatal(e.message, code=e.code)
    except FrameCraftExit as e:
        _fatal(e.message, code=e.code)
    except ProviderError as e:
        _fatal(str(e), code=int(ExitCode.PROVIDER))
    except Exception as e:
        _fatal(f"unexpected: {e}", code=int(ExitCode.USAGE))


# ---------------------------------------------------------------------------
# Plan diff
# ---------------------------------------------------------------------------


def _diff_plans(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    _diff_recursive("", old, new, lines)
    return lines


def _diff_recursive(path: str, old: Any, new: Any, out: list[str]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            child = f"{path}.{k}" if path else k
            if k not in old:
                out.append(f"added:   {child}")
            elif k not in new:
                out.append(f"removed: {child}")
            else:
                _diff_recursive(child, old[k], new[k], out)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            child = f"{path}[{i}]"
            if i >= len(old):
                out.append(f"added:   {child}")
            elif i >= len(new):
                out.append(f"removed: {child}")
            else:
                _diff_recursive(child, old[i], new[i], out)
    else:
        if old != new:
            out.append(f"changed: {path}  {old!r} → {new!r}")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-")[:60] or "framecraft-project"


def _fatal(message: str, *, code: int) -> None:
    _console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)
