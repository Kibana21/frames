"""`framecraft compose` — end-to-end pipeline."""

from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.console import Console

import sys

from framecraft.assembler import Assembler
from framecraft.brand import build_brand_kit
from framecraft.cli_render import invoke_render
from framecraft.config import load_config
from framecraft.director import Director, DirectorError
from framecraft.exit_codes import ExitCode, FrameCraftExit, ToolchainError
from framecraft.lint import FrameCraftBugError, LintFailedAfterRepairError, lint_repair
from framecraft.music import MusicValidationError, validate_music
from framecraft.providers import make_provider
from framecraft.providers.base import ProviderError
from framecraft.registry import default_registry
from framecraft.scaffold import scaffold as scaffold_fn
from framecraft.schema import (
    Archetype,
    Aspect,
    BlockId,
    Brief,
    Mood,
    Scene,
    SceneGraph,
)

_console = Console()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def compose(
    situation: str = typer.Argument(..., help="One-line situation string."),
    out: Path = typer.Option(None, "--out", "-o", help="Output directory."),
    aspect: Aspect = typer.Option(Aspect.AR_16_9, "--aspect"),
    duration: float = typer.Option(20.0, "--duration"),
    fps: int = typer.Option(30, "--fps"),
    mood: Mood | None = typer.Option(None, "--mood"),
    archetype: str = typer.Option("auto", "--archetype"),
    logo: Path | None = typer.Option(None, "--logo"),
    palette: str | None = typer.Option(None, "--palette"),
    font: str | None = typer.Option(None, "--font"),
    music: Path | None = typer.Option(None, "--music"),
    music_volume: float = typer.Option(0.4, "--music-volume"),
    render: bool = typer.Option(False, "--render"),
    open_browser: bool = typer.Option(False, "--open"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip Director; use a hand-written plan."),
    provider: str | None = typer.Option(None, "--provider", envvar="FRAMECRAFT_PROVIDER"),
    no_config: bool = typer.Option(False, "--no-config", help="Skip framecraft.yaml loading."),
    summary: bool = typer.Option(False, "--summary", help="Print token/cost/lint summary after run."),
) -> None:
    try:
        # Load framecraft.yaml defaults (CLI args override config).
        cfg = load_config(no_config=no_config)
        logo = logo or cfg.brand.logo
        palette = palette or cfg.brand.palette
        font = font or cfg.brand.font
        provider = provider or cfg.defaults.provider
        if mood is None and cfg.defaults.mood is not None:
            mood = cfg.defaults.mood

        brand_kit = build_brand_kit(logo, palette, font)

        if music is not None:
            try:
                music = validate_music(music, duration)
            except MusicValidationError as e:
                raise FrameCraftExit(ExitCode.USAGE, str(e)) from e

        brief = Brief(
            situation=situation,
            aspect=aspect,
            duration=duration,
            fps=fps,
            mood=mood,
            archetype=_parse_archetype(archetype),
            brand_kit=brand_kit,
            music_path=music,
            music_volume=music_volume,
        )

        out_dir = (out or Path.cwd() / _slug(situation)).resolve()

        # --- Step 1: scaffold
        _console.print(f"[1/5] Scaffolding at [bold]{out_dir}[/bold]…")
        hf_version = scaffold_fn(out_dir)
        _console.print(f"      hyperframes {hf_version}")

        # --- Step 2: plan
        registry = default_registry()
        if dry_run:
            _console.print("[2/5] Planning scenes (dry-run: hand-written plan)…")
            plan = _handwritten_plan(brief)
            provider_obj = _m0_stub_provider()
        else:
            _console.print("[2/5] Planning scenes via Director…")
            try:
                provider_obj = make_provider(provider)
            except ProviderError as e:
                raise FrameCraftExit(ExitCode.USAGE, str(e)) from e
            director = Director(provider_obj, registry)
            try:
                plan = director.plan(brief, out_dir=out_dir)
            except DirectorError as e:
                raise FrameCraftExit(ExitCode.PROVIDER, f"Director: {e}") from e

        # --- Step 3: assemble
        _console.print(f"[3/5] Assembling {len(plan.scenes)} scenes…")
        assembler = Assembler(registry, provider_obj)
        assembler.assemble(
            plan,
            out_dir,
            project_name=situation[:60],
            project_id=_slug(situation)[:40] or "framecraft-project",
        )

        # --- Step 4: lint-repair
        _console.print("[4/5] Linting + repair…")
        try:
            result = lint_repair(out_dir, assembler, plan)
            if result.repaired:
                _console.print("      repaired 1 pass")
        except (FrameCraftBugError, LintFailedAfterRepairError) as e:
            raise FrameCraftExit(e.code, e.message) from e

        # --- Step 5: render
        if render:
            _console.print("[5/5] Rendering…")
            invoke_render(out=out_dir)
        else:
            _console.print("[5/5] Done (use --render to generate MP4).")

        # --- Summary
        show_summary = summary or sys.stdout.isatty()
        if show_summary:
            _print_summary(out_dir, result)

        _console.print()
        _console.print("[green]✓ Done.[/green]")
        _console.print(f"  plan: {out_dir / 'plan.json'}")
        _console.print(f"  root: {out_dir / 'index.html'}")

    except ToolchainError as e:
        _fatal(e.message, code=e.code)
    except FrameCraftExit as e:
        _fatal(e.message, code=e.code)
    except ProviderError as e:
        _fatal(str(e), code=int(ExitCode.PROVIDER))
    except Exception as e:
        _fatal(f"unexpected: {e}", code=int(ExitCode.USAGE))


def _m0_stub_provider():
    """A stub provider whose fixture dir doesn't matter in M0 (unused by dry-run)."""
    from framecraft.providers.stub import StubProvider
    return StubProvider(Path("/dev/null"), "stub:m0")


def _handwritten_plan(brief: Brief) -> SceneGraph:
    """Synthesize a minimal 2-scene plan from the situation string.

    Title half-gets the first ~half of the situation as a headline; end card
    gets a generic tagline. Good enough to exercise the pipeline end-to-end.
    """
    headline, subtitle = _split_situation(brief.situation)
    total = brief.duration
    title_dur = min(max(total * 0.6, 3.0), 8.0)
    end_dur = max(total - title_dur, 2.0)
    w, h = brief.aspect.dimensions
    bg = brief.brand_kit.palette.bg if (brief.brand_kit and brief.brand_kit.palette) else "#09090C"
    fg = brief.brand_kit.palette.primary if (brief.brand_kit and brief.brand_kit.palette) else "#FFFFFF"
    font = (
        brief.brand_kit.typography.headline
        if (brief.brand_kit and brief.brand_kit.typography)
        else "Inter"
    )

    scenes = [
        Scene(
            index=0,
            block_id=BlockId.TITLE_CARD,
            start=0.0,
            duration=round(title_dur, 3),
            block_props={"headline": headline, "subtitle": subtitle, "bg": bg, "fg": fg, "font": font},
        ),
        Scene(
            index=1,
            block_id=BlockId.END_CARD,
            start=round(title_dur, 3),
            duration=round(end_dur, 3),
            block_props={"tagline": "Made with FrameCraft.", "bg": bg, "fg": fg, "font": font},
        ),
    ]

    total_recomputed = round(scenes[0].duration + scenes[1].duration, 3)
    return SceneGraph(
        brief=brief,
        archetype=brief.archetype or Archetype.NARRATIVE_SCENE,
        aspect=brief.aspect,
        canvas=(w, h),
        duration=total_recomputed,
        scenes=scenes,
        transitions=[],
        brand_kit=brief.brand_kit,
    )


def _split_situation(s: str) -> tuple[str, str | None]:
    s = s.strip()
    if len(s) <= 60:
        return s, None
    # Split at a sentence boundary or at 60-ish chars.
    for sep in [". ", "; ", ", "]:
        idx = s.find(sep, 20, 80)
        if idx != -1:
            return s[:idx].strip(), s[idx + len(sep):].strip()
    # No natural break; split at whitespace near 60.
    split_at = s.rfind(" ", 40, 80)
    if split_at == -1:
        return s[:60], s[60:]
    return s[:split_at], s[split_at + 1:]


def _parse_archetype(value: str) -> Archetype | None:
    if value == "auto":
        return None
    try:
        return Archetype(value)
    except ValueError as e:
        valid = [a.value for a in Archetype]
        raise FrameCraftExit(
            ExitCode.USAGE, f"--archetype {value!r} invalid. Valid: {valid}"
        ) from e


def _slug(s: str) -> str:
    slug = _SLUG_RE.sub("-", s.lower()).strip("-")
    return slug[:60] or "framecraft-project"


def _print_summary(out_dir: Path, lint_result: object) -> None:
    from framecraft.trace import summarize as _summarize

    try:
        s = _summarize(out_dir)
    except Exception:
        return

    polished = sum(st.polish_cache_misses for st in s.assembler_scenes)
    scene_count = len(s.assembler_scenes)
    lint_str = "pass" if s.lint_passed is not False else "fail"
    render_str = "rendered" if s.render and s.render.present else "—"

    _console.print()
    _console.print("[dim]── run summary ─────────────────────────────[/dim]")
    _console.print(f"  Scenes:    {scene_count} ({polished} polished)")
    _console.print(
        f"  Tokens:    {s.total_input_tokens:,} in / {s.total_output_tokens:,} out"
        f" / {s.total_cache_read_tokens:,} cached"
    )
    _console.print(f"  Est. cost: ${s.estimated_cost_usd:.4f}")
    _console.print(f"  Lint:      {lint_str}")
    _console.print(f"  Render:    {render_str}")
    _console.print("[dim]────────────────────────────────────────────[/dim]")


def _fatal(message: str, *, code: int) -> None:
    _console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)
