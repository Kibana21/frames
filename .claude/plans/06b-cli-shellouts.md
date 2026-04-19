# 06b — CLI Shellouts (`render`, `preview`, `--music`)

## Goal

Three thin wrappers around `npx hyperframes`. They do no semantic work — they delegate. `render` produces an MP4; `preview` launches the browser studio; `--music` surfaces the flag that plumbs into 01 (validator) and 04 (injection).

## Inputs

- PRD US-011, US-015; CLI portion of US-016; §7.4 exit code 3.
- Shell-out helper `run_npx` from [`05-scaffold-lint-repair.md`](./05-scaffold-lint-repair.md).

## Outputs

- `framecraft render` — executes `npx hyperframes render`, maps errors to exit 3.
- `framecraft preview` — launches `npx hyperframes preview` in a subprocess.
- `--music` / `--music-volume` wired through `compose` and `from-plan` into `Brief.music_path` / `Brief.music_volume`.

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/cli_render.py` | `render` command |
| `framecraft/cli_preview.py` | `preview` command |
| `framecraft/subprocess_helpers.py` | `run_npx_streaming` — adds non-capturing variant for interactive subprocesses |
| `tests/test_cli_render.py` | Unit tests (mocked subprocess) |
| `tests/test_cli_preview.py` | Unit tests (mocked subprocess + signal handling) |

## Dependencies

- 05 for `run_npx`.
- 06a for the Typer app into which these commands are registered.
- 04 for the `<audio>` injection (already owned there); this plan only contributes the CLI flag parsing.

## Implementation steps

### `render`

1. **Command signature.**
   ```python
   def render(
       out: Path = typer.Option(Path("."), "--out", "-o"),
       format: Literal["mp4", "webm", "mov"] = typer.Option("mp4", "--format"),
       quality: Literal["draft", "standard", "high"] = typer.Option("standard", "--quality"),
       fps: int | None = typer.Option(None, "--fps"),  # None → inherit from project
   ) -> None: ...
   ```

2. **Invocation.**
   ```python
   args = ["hyperframes", "render", "--format", format, "--quality", quality]
   if fps is not None:
       args += ["--fps", str(fps)]
   try:
       run_npx(args, cwd=out)
   except ToolchainError as e:
       _write_render_log(out, e.stderr)
       raise FrameCraftExit(ExitCode.RENDER, f"render failed: {e.stderr.splitlines()[0] if e.stderr else 'see .framecraft/render-stderr.log'}")
   _print_render_path(out)
   ```

3. **Render path printing.**
   - After success, scan `out/renders/` for files newer than invocation time; print the newest one. If Hyperframes prints the path on stdout, capture and forward it (preferred).

4. **No re-implementation.** FrameCraft does no encoding, no post-processing, no ffmpeg invocations. Pass-through only.

### `preview`

5. **Command signature.**
   ```python
   def preview(
       out: Path = typer.Option(Path("."), "--out", "-o"),
       port: int = typer.Option(4000, "--port"),
   ) -> None: ...
   ```

6. **Subprocess invocation — streaming, interactive.**
   ```python
   import signal, subprocess
   proc = subprocess.Popen(
       ["npx", "hyperframes", "preview", "--port", str(port)],
       cwd=out, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
   )
   try:
       for line in proc.stdout:
           # Forward stderr lines verbatim; detect "http://localhost:XXXX" and print a clean banner.
           sys.stderr.write(line)
           m = LOCALHOST_RE.match(line)
           if m:
               typer.secho(f"Preview ready: {m.group(0)}", fg="green")
       rc = proc.wait()
       raise SystemExit(rc)
   except KeyboardInterrupt:
       proc.send_signal(signal.SIGINT)
       proc.wait(timeout=5)
       raise SystemExit(0)
   ```
   - Ctrl-C propagates cleanly. No process leaks.
   - Reloading is Hyperframes' job — we just run the preview server.

7. **Port-in-use detection.** If Hyperframes emits a well-known error ("EADDRINUSE" or similar), print a one-liner: *"Port {N} in use. Try `--port {N+1}`."* and exit 1.

### `--music` / `--music-volume` wiring

8. **Flag plumbing.**
   - `compose` (in 06a): `--music` option already declared. Here we confirm it flows into `Brief.music_path`; `--music-volume` into `brief.music_volume` (needs to be added to `Brief` in 01; this plan notes the dependency).
   - `from-plan` (in 06a): same flags supported; override whatever is in `plan.json` for that run.
   - Injection happens in 04's `rendering/audio.py`; this plan does not replicate that logic.

9. **`--music` without `--render`.** The audio bed is baked into `index.html` by the Assembler; actual rendering to MP4 depends on `--render`. `preview` will play audio in the browser. Document this in `--help`.

10. **Validation.** Before dispatching to Assembler, call `validate_music(path, plan.duration)` from 01. Failure → `FrameCraftExit(USAGE, ...)`.

### `render-stderr.log` writer

11. **`_write_render_log(out, stderr)`.**
    - Writes `out / ".framecraft" / "render-stderr.log"` atomically. Per §7.4, this is the exit-3 artifact.

## Testing strategy

- **Unit (`tests/test_cli_render.py`).**
  - Mock `run_npx`; assert correct argv for each combination of `format`/`quality`/`fps`.
  - Non-zero exit → `FrameCraftExit(ExitCode.RENDER)` and log file written.
- **Unit (`tests/test_cli_preview.py`).**
  - Mock `subprocess.Popen`; feed fake stdout lines including a `http://localhost:4000` line; assert banner printed.
  - Simulate `KeyboardInterrupt` during stdout iteration; assert `SIGINT` sent to child, clean exit 0.
- **Integration — manual.** `framecraft compose "..." --render` on a laptop; expect MP4 in under 2 minutes. `framecraft preview` on a scaffolded project; open browser.

## Acceptance (PRD bullets closed)

- US-011 (all AC), US-015 (all AC), US-016 CLI-flag portion.
- FR-9 (CLI surface — remaining commands after 06a).

## Open questions

- **OQ-F6b.1** Should `preview` auto-open the browser after printing the URL? *Leaning: yes, gated by `--open/--no-open`, default on TTY, off non-TTY.*
- **OQ-F6b.2** Should `render` default to `--render-on-save` semantics (watch for plan.json changes and re-render)? *Leaning: no — explicit scope creep.*

## Verification

```bash
# Render (requires a previously composed project):
framecraft render --out /tmp/fc-shieldmax --quality draft
# → creates /tmp/fc-shieldmax/renders/*.mp4

# Preview:
framecraft preview --out /tmp/fc-shieldmax --port 4000
# → prints "Preview ready: http://localhost:4000" and streams stderr. Ctrl-C stops cleanly.

# Music flag:
framecraft compose "product promo for X" --music ./bed.mp3 --music-volume 0.3 --render
# → MP4 plays bed.mp3 at 0.3 volume

pytest tests/test_cli_render.py tests/test_cli_preview.py -v
```
