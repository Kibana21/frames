# 06a — CLI Core (`compose`, `from-plan`, `doctor`, `catalog`, config)

## Goal

The CLI skeleton and its three "primary" commands: `compose` (end-to-end), `from-plan` (re-assemble without Director), and `doctor` (environment sanity). Also owns `framecraft catalog`, `framecraft.yaml` loading, branding-flag parsing, and the canonical exit-code surface. Shellout commands (`render`, `preview`, `--music`) live in [`06b-cli-shellouts.md`](./06b-cli-shellouts.md).

## Inputs

- PRD US-001, US-008, US-009; branding portion of US-010; §7.4 exit codes; §10 OQ-4 (yaml location).
- Everything else — calls into 01 (schema), 02 (providers), 03 (Director), 04 (Assembler), 05 (Scaffold + Lint-repair), 08 (primer drift).

## Outputs

- `framecraft` console script.
- `framecraft compose <situation>` → scaffolded, assembled, linted project.
- `framecraft from-plan <path>` → re-assembled project from existing `plan.json`.
- `framecraft doctor` → diagnostics.
- `framecraft catalog [--json]` → registry table or JSON.
- `framecraft.yaml` loader (project-local wins).

## Critical files

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | Console entry point, deps, metadata |
| `framecraft/cli.py` | Typer app, dispatch |
| `framecraft/cli_compose.py` | `compose` command |
| `framecraft/cli_from_plan.py` | `from-plan` command |
| `framecraft/cli_doctor.py` | `doctor` command |
| `framecraft/cli_catalog.py` | `catalog` command |
| `framecraft/config.py` | `framecraft.yaml` loader |
| `framecraft/brand.py` | `--logo`, `--palette`, `--font` parsing → `BrandKit` |
| `framecraft/exit_codes.py` | Exit-code enum referencing 00's canonical table |
| `tests/test_cli_*.py` | Per-command unit tests with Typer's `CliRunner` |

## Dependencies

- 01, 02, 03, 04, 05, 08. This file orchestrates all of them.

## Implementation steps

### Package skeleton

1. **`pyproject.toml`.**
   - Build backend: `hatchling` (simplest for a src layout).
   - Python `>=3.11,<3.13`.
   - Dependencies: `pydantic>=2`, `google-genai`, `anthropic`, `jinja2`, `typer[all]`, `rich`, `httpx`, `beautifulsoup4`, `pyyaml`, `platformdirs`.
   - Dev deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `pytest-recording`.
   - `[project.scripts]`: `framecraft = "framecraft.cli:main"`.
   - `[tool.setuptools.package-data]` or hatch equivalent: include `framecraft/prompts/primer.md`, `framecraft/prompts/primer.lock.json`, `framecraft/prompts/**/*.md`, `framecraft/blocks/**/*.py`.

2. **Source layout.** `src/framecraft/` + `tests/` + `scripts/`. Use `src/` to prevent accidental import of un-installed code.

3. **`ruff` + `mypy` config** in `pyproject.toml`: `mypy --strict` for the package; `pytest` + `@pytest.mark.llm` marker registered.

### CLI framework

4. **`cli.py` — Typer root.**
   ```python
   app = typer.Typer(
       help="FrameCraft — turn situations into Hyperframes projects",
       no_args_is_help=True,
       add_completion=False,
   )
   app.command("compose")(cli_compose.compose)
   app.command("from-plan")(cli_from_plan.from_plan)
   app.command("doctor")(cli_doctor.doctor)
   app.command("catalog")(cli_catalog.catalog)
   app.command("render")(cli_render.render)     # 06b
   app.command("preview")(cli_preview.preview)  # 06b

   def main() -> NoReturn:
       try:
           app()
       except FrameCraftExit as e:
           typer.secho(e.message, err=True, fg="red")
           raise SystemExit(e.code)
       except Exception as e:
           typer.secho(f"unexpected: {e}", err=True, fg="red")
           raise SystemExit(1)
   ```

5. **Exit-code mapping (`exit_codes.py`).**
   ```python
   class ExitCode(IntEnum):
       OK = 0
       USAGE = 1
       LINT = 2
       RENDER = 3
       PROVIDER = 4

   class FrameCraftExit(Exception):
       def __init__(self, code: ExitCode, message: str):
           self.code = int(code); self.message = message
   ```
   - Mapping: `ToolchainError` → `USAGE`. `FrameCraftBugError | LintFailedAfterRepairError` → `LINT`. `ProviderError subclass` → `PROVIDER`. Rendering failures (from 06b) → `RENDER`.

### `compose`

6. **`cli_compose.compose` signature.**
   ```python
   def compose(
       situation: str = typer.Argument(...),
       out: Path = typer.Option(None, "--out", "-o"),
       aspect: Aspect = typer.Option(Aspect.AR_16_9, "--aspect"),
       duration: float = typer.Option(20.0, "--duration"),
       fps: int = typer.Option(30, "--fps"),
       mood: Mood | None = typer.Option(None, "--mood"),
       archetype: str = typer.Option("auto", "--archetype"),  # "auto" or enum string
       logo: Path | None = typer.Option(None, "--logo"),
       palette: str | None = typer.Option(None, "--palette"),
       font: str | None = typer.Option(None, "--font"),
       music: Path | None = typer.Option(None, "--music"),        # plumbing only; injection in 04
       music_volume: float = typer.Option(0.4, "--music-volume"),
       render: bool = typer.Option(False, "--render"),
       open: bool = typer.Option(False, "--open"),
       dry_run: bool = typer.Option(False, "--dry-run"),
       provider: str | None = typer.Option(None, "--provider", envvar="FRAMECRAFT_PROVIDER"),
   ) -> None: ...
   ```

7. **Step log (Rich progress) — no spinners on non-TTY.**
   - `[1/5] Scaffolding project…`
   - `[2/5] Planning scenes…`
   - `[3/5] Assembling N scenes…`
   - `[4/5] Linting + repair…`
   - `[5/5] Rendering…` (only if `--render`)
   - Use `rich.console.Console(force_terminal=sys.stdout.isatty())`; when non-TTY, emit plain lines.

8. **Dispatch.**
   ```python
   1. Load framecraft.yaml defaults (see §10 below), CLI args override.
   2. brand_kit = build_brand_kit(logo, palette, font)
   3. brief = Brief(situation=..., aspect=..., ..., brand_kit=brand_kit, music_path=music)
   4. out = out or Path(slug(situation))  # refuse to overwrite non-empty non-FC dir
   5. scaffold(out)                                      # 05
   6. if dry_run: emit a hand-written plan; skip Director
      else: plan = Director(provider_obj, registry).plan(brief)   # 03
      (out / "plan.json").write_text(plan.model_dump_json(indent=2))
   7. Assembler(registry, provider_obj).assemble(plan, out)       # 04
   8. lint_repair(out, assembler, plan)                            # 05
   9. if render: render_cmd(out, ...)                              # 06b
   10. if open: webbrowser.open(f"file://{out/'index.html'}")
   ```

9. **`--dry-run` shape.**
   - Loads a hand-written plan from `tests/fixtures/plans/<slug>.json` if one matches the situation slug; otherwise emits a minimal 2-scene plan with `title-card` + `end-card`. No Director, no LLM calls at all.
   - Exists for M0 walking skeleton.

10. **Slug generation.**
    - `slug(situation)`: lowercase, replace non-alphanumeric with `-`, collapse repeats, trim, cap at 60 chars. Deterministic — enables re-running the same command without `--out`.

11. **Paths printed on success.**
    - Absolute paths of `plan.json`, `index.html`, and (if rendered) the MP4.

### `from-plan`

12. **`cli_from_plan.from_plan`.**
    ```python
    def from_plan(
        plan_path: Path = typer.Argument(...),
        render: bool = typer.Option(False, "--render"),
        provider: str | None = typer.Option(None, "--provider", envvar="FRAMECRAFT_PROVIDER"),
    ) -> None: ...
    ```
    - Loads `plan.json`, validates against `SceneGraph`. Discovers `out_dir = plan_path.parent`.
    - Diff-style summary vs `out_dir / ".framecraft" / "last-plan.json"` (if present):
      ```
      changed: scenes[2].duration  4.5 → 5.0
      changed: scenes[2].copy.headline  "Save time" → "Save hours"
      swapped: scenes[3].block_id  logo-outro → end-card
      added: scenes[5]
      removed: transitions[2]
      ```
      Simple recursive dict diff is fine; use `rich.console.Console.print`.
    - Runs Assembler + lint-repair. Skips Director.
    - Polish cache behavior: `scene.polished` is already on the loaded plan (persisted from previous run). Assembler re-polishes only fields whose raw input changed.

13. **`from-plan` with user-edited polish.** If the user hand-edited a polished value and forgot to clear the cache, they may get surprising reversion. Mitigation: when `plan.json` is loaded, if `scene.polished[field]` differs from the computed polished value for `scene.block_props[field]`, log a warning: `Warning: scene 2 field `headline` appears hand-edited post-polish — cache left as-is; delete `.polished.headline` to re-polish.` Don't automatically re-polish.

### `doctor`

14. **`cli_doctor.doctor`.**
    ```python
    def doctor(
        snapshot: str | None = typer.Option(None, "--snapshot"),  # block_id to re-pin
        refresh: bool = typer.Option(False, "--refresh"),
    ) -> None:
        if snapshot:
            return _snapshot_block(snapshot)
        _run_checks()
    ```
    - Checks emitted as a Rich table with green checks / red X / yellow warnings:
      - `node` — version, `ok` if ≥ 20.
      - `npx` — found.
      - `npx hyperframes --version` — ≥ floor from `_compat.py`.
      - `ffmpeg` — found.
      - `FRAMECRAFT_PROVIDER` — effective value.
      - `GEMINI_API_KEY` or `GOOGLE_API_KEY` — present/missing (never the value).
      - `ANTHROPIC_API_KEY` — present/missing.
      - `Primer drift` — calls `check_drift()` from 08.
    - `--snapshot <block_id>`: runs `npx hyperframes add <catalog_id> --version <current_pinned>`; computes SHA-256 of installed tree; prints the new hash and the literal line to paste into the `BlockSpec`:
      ```
      catalog_hash="sha256-abc123..."
      ```
      Does NOT modify source files — safer to eyeball the diff.
    - `--refresh`: clears `~/.cache/framecraft/gemini-caches.json` (deletes the file). Handy when provider caches get wedged.

### `catalog`

15. **`cli_catalog.catalog`.**
    - Default: Rich table with columns `id | category | provenance | archetypes | aspects | duration | synopsis`.
    - `--json`: `json.dumps({id: spec.model_dump(exclude={"template"}) for id, spec in REGISTRY.items()}, indent=2, default=str)` to stdout.
    - Used by LLM-adjacent tooling and by humans browsing what's available.

### Configuration — `framecraft.yaml` (OQ-4)

16. **Two-location search (proposed OQ-4 resolution).**
    - Project-local: `./framecraft.yaml`.
    - User-global: `{platformdirs.user_config_dir('framecraft')}/config.yaml`.
    - Merge order (lowest priority first): user-global → project-local → CLI args. CLI always wins.

17. **Schema.** Pydantic `FrameCraftConfig`:
    ```yaml
    defaults:
      aspect: 16:9
      duration: 20
      fps: 30
      mood: cinematic
      provider: gemini
    brand:
      logo: ./brand/logo.svg
      palette: "#0A0A0F,#F5F5F0,#C44536"
      font: Inter
    ```
    - Unknown keys logged as warnings (forward-compat).

18. **`--no-config` escape hatch.** Skips both yaml files. Useful for tests.

### Branding parsing (`brand.py`)

19. **`build_brand_kit(logo, palette, font) -> BrandKit | None`.**
    - `logo`: copy to `assets/logo.<ext>` during scaffold (done in step 5 `compose` after scaffold completes). Fail fast if path not readable.
    - `palette`: `#0A0A0F,#F5F5F0,#C44536` → `Palette(primary=..., bg=..., accent=...)`. Bad hex → `FrameCraftExit(USAGE, ...)`.
    - `font`: passed verbatim; `<link>` injection happens in 04 (root renderer).
    - All three together optional; if none provided, `BrandKit` is `None`.

### Windows & macOS

20. **`rich` progress off by default on CI (`CI=1` env)** — avoid ANSI litter in logs. Still emits step messages.

## Testing strategy

- **Unit — `tests/test_cli_compose.py`.**
  - Use Typer's `CliRunner`. Mock `scaffold`, `Director.plan`, `Assembler.assemble`, `lint_repair` to assert correct orchestration order and argument flow.
  - Bad `--palette` → exit 1.
  - Missing `ANTHROPIC_API_KEY` with `--provider anthropic` → exit 1 with actionable message.
  - `--dry-run` → no Director invocation.
- **Unit — `tests/test_cli_from_plan.py`.** Load fixture plan, mock Assembler, assert diff summary and no Director call.
- **Unit — `tests/test_cli_doctor.py`.** Mock `subprocess.run` for `node --version`, etc.; assert each check reports correctly.
- **Unit — `tests/test_config.py`.** Project-local overrides global; CLI overrides both; unknown key warns.

## Acceptance (PRD bullets closed)

- US-001 all, US-008 all, US-009 all, US-010 flag-parsing half (template honoring in 04).
- FR-1 (project dir shape assembled end-to-end).
- FR-5 (reproducible from-plan).
- FR-9 (CLI surface — split with 06b).
- FR-11 (always-write traces — compose drives `from-plan` and `doctor` flows to always emit traces).

## Open questions

- **OQ-4 (PRD) — proposed resolution** (awaiting confirmation): both locations, project-local wins over user-global, CLI wins over both. `--no-config` opts out.
- **OQ-F6a.1** Should `compose` without `--out` refuse to overwrite even an existing FrameCraft project by default? Current design: allow if `.framecraft/` exists. *Leaning: add `--force` flag; without it, refuse.*
- **OQ-F6a.2** For `from-plan`, should we print the diff *before* running the pipeline (user confirms) or *after* (informational)? *Leaning: before, with `--yes` to skip the confirmation.*

## Verification

```bash
pip install -e .
framecraft --help
# → top-level commands listed

framecraft doctor
# → Rich table of checks

framecraft catalog
# → table of blocks

framecraft compose "30-second promo for an AI health insurance app called ShieldMax" \
  --aspect 9:16 --duration 30 --palette "#0A0A0F,#F5F5F0,#C44536" --font Inter \
  --out /tmp/fc-shieldmax --render
# → creates /tmp/fc-shieldmax/{index.html,compositions/...,plan.json,renders/*.mp4}

$EDITOR /tmp/fc-shieldmax/plan.json
framecraft from-plan /tmp/fc-shieldmax/plan.json
# → diff summary, re-assembles

pytest tests/test_cli_*.py -v
```
