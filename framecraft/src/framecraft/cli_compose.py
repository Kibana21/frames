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

# Built-in style palette for --n-variants. Each seed is a short creative
# directive fed to both Director (block/copy choices) and LLM author
# (motion/color/type). Keep them short — they ride inside every prompt.
_DEFAULT_STYLE_SEEDS: list[str] = [
    "kinetic-bold — oversized type, aggressive letter-by-letter stagger, "
    "back.out/expo easing, punchy accent bars, high-saturation accent color",
    "cinematic-slow — restrained motion, slow camera push, held beats, "
    "gentle power2 easing, subtle vignette or letterbox, muted palette",
    "editorial-minimal — magazine layout with off-center alignment, "
    "thin hairlines, tight kerning reveals, monochrome with one accent",
    "playful-bouncy — back.out(2.5) overshoot, rotation/skew entries, "
    "rounded shapes, warm palette, confetti or emoji-adjacent flourishes",
    "technical-precise — grid-based wipes, monospace accents, dashed "
    "underlines, crisp geometric transitions, cool blues and whites",
]


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
    full_polish: bool = typer.Option(
        False,
        "--full-polish",
        help="Let the LLM author each native scene's HTML end-to-end (richer motion, ~10× tokens). Ignored for --dry-run / stub provider.",
    ),
    n_variants: int = typer.Option(
        1,
        "--n-variants",
        "-n",
        min=1,
        max=10,
        help="Produce N renditions from the same situation (each in its own vK/ subfolder). Picks from a built-in style palette unless --style-seed is also set.",
    ),
    style_seed: str | None = typer.Option(
        None,
        "--style-seed",
        help="Freeform creative directive (overrides the built-in palette). "
             "Repeat with commas for multi-variant, e.g. 'kinetic-bold,cinematic-slow'.",
    ),
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

        base_brief = Brief(
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

        top_dir = (out or _workspace_root() / "output" / _slug(situation)).resolve()

        # Resolve style seeds for the variant loop.
        seeds = _resolve_style_seeds(style_seed, n_variants)

        # Shared pieces that don't need rebuilding per variant.
        registry = default_registry()
        if dry_run:
            provider_obj = _m0_stub_provider()
        else:
            try:
                provider_obj = make_provider(provider)
            except ProviderError as e:
                raise FrameCraftExit(ExitCode.USAGE, str(e)) from e

        if n_variants > 1:
            _console.print(
                f"[bold]Producing {n_variants} variants[/bold] of "
                f"[italic]{situation[:80]}[/italic]"
            )

        for variant_idx in range(n_variants):
            seed = seeds[variant_idx]
            brief = base_brief.model_copy(update={"style_seed": seed})
            variant_dir = (
                top_dir if n_variants == 1 else top_dir / f"v{variant_idx + 1}"
            )
            if n_variants > 1:
                _console.print()
                _console.print(
                    f"[bold cyan]── variant {variant_idx + 1}/{n_variants} ──[/bold cyan]"
                )
                if seed:
                    _console.print(f"  style_seed: {seed[:100]}…" if len(seed) > 100 else f"  style_seed: {seed}")

            _run_one_variant(
                brief=brief,
                out_dir=variant_dir,
                registry=registry,
                provider_obj=provider_obj,
                dry_run=dry_run,
                full_polish=full_polish,
                render=render,
                summary=summary,
                situation=situation,
            )

        if n_variants > 1:
            _console.print()
            _console.print(f"[green]✓ All {n_variants} variants done.[/green] "
                          f"Compare under [bold]{top_dir}[/bold]/v1…v{n_variants}/")

    except ToolchainError as e:
        _fatal(e.message, code=e.code)
    except FrameCraftExit as e:
        _fatal(e.message, code=e.code)
    except ProviderError as e:
        _fatal(str(e), code=int(ExitCode.PROVIDER))
    except Exception as e:
        _fatal(f"unexpected: {e}", code=int(ExitCode.USAGE))


def _resolve_style_seeds(user_seed: str | None, n: int) -> list[str | None]:
    """Pick the list of N style seeds for this run.

    Rules:
      - n == 1 and no user seed  → [None]  (backward-compatible)
      - user seed (comma-split)  → respect it; pad with built-ins; truncate to N
      - otherwise                → first N from the built-in palette (wrapped)
    """
    if user_seed:
        parts = [s.strip() for s in user_seed.split(",") if s.strip()]
        seeds: list[str | None] = list(parts)
        # Pad from the built-in palette if user supplied fewer than N.
        i = 0
        while len(seeds) < n:
            seeds.append(_DEFAULT_STYLE_SEEDS[i % len(_DEFAULT_STYLE_SEEDS)])
            i += 1
        return seeds[:n]

    if n == 1:
        return [None]
    return [_DEFAULT_STYLE_SEEDS[i % len(_DEFAULT_STYLE_SEEDS)] for i in range(n)]


def _run_one_variant(
    *,
    brief: Brief,
    out_dir: Path,
    registry,
    provider_obj,
    dry_run: bool,
    full_polish: bool,
    render: bool,
    summary: bool,
    situation: str,
) -> None:
    """One full compose pipeline (scaffold → plan → assemble → lint → render)."""
    _console.print(f"[1/5] Scaffolding at [bold]{out_dir}[/bold]…")
    hf_version = scaffold_fn(out_dir)
    _console.print(f"      hyperframes {hf_version}")

    if dry_run:
        _console.print("[2/5] Planning scenes (dry-run: hand-written plan)…")
        plan = _handwritten_plan(brief)
    else:
        _console.print("[2/5] Planning scenes via Director…")
        director = Director(provider_obj, registry)
        try:
            plan = director.plan(brief, out_dir=out_dir)
        except DirectorError as e:
            raise FrameCraftExit(ExitCode.PROVIDER, f"Director: {e}") from e

    polish_note = " (full-polish)" if full_polish else ""
    _console.print(f"[3/5] Assembling {len(plan.scenes)} scenes{polish_note}…")
    assembler = Assembler(registry, provider_obj, full_polish=full_polish)
    assembler.assemble(
        plan,
        out_dir,
        project_name=situation[:60],
        project_id=_slug(situation)[:40] or "framecraft-project",
    )

    _console.print("[4/5] Linting + repair…")
    try:
        result = lint_repair(out_dir, assembler, plan)
        if result.repaired:
            _console.print("      repaired 1 pass")
    except (FrameCraftBugError, LintFailedAfterRepairError) as e:
        raise FrameCraftExit(e.code, e.message) from e

    if render:
        _console.print("[5/5] Rendering…")
        invoke_render(out=out_dir)
    else:
        _console.print("[5/5] Done (use --render to generate MP4).")

    show_summary = summary or sys.stdout.isatty()
    if show_summary:
        _print_summary(out_dir, result)

    _console.print()
    _console.print("[green]✓ Done.[/green]")
    _console.print(f"  plan: {out_dir / 'plan.json'}")
    _console.print(f"  root: {out_dir / 'index.html'}")


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


def _workspace_root() -> Path:
    """Walk up from CWD to find the workspace root.

    The workspace root is the directory that *contains* the framecraft package
    (i.e. has a `framecraft/` or `pyproject.toml` subdirectory/file at its
    level). Falls back to CWD if nothing is found.
    """
    current = Path.cwd()
    home = Path.home()
    while current != home and current != current.parent:
        # If we're inside framecraft/, the root is one level up
        if (current / "pyproject.toml").exists() and (current.parent / "output").parent != current:
            # We're inside the framecraft package dir — go up one level
            if current.name == "framecraft":
                return current.parent
            return current
        # If CWD contains a framecraft/ subdirectory, this IS the root
        if (current / "framecraft").is_dir():
            return current
        current = current.parent
    return Path.cwd()


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
