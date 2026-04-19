# 04 — Assembler (`SceneGraph` → HTML)

## Goal

Turn a validated `SceneGraph` into a Hyperframes-compliant project directory: per-scene `<template>`-wrapped HTML under `compositions/`, a root `index.html`, `meta.json`, and a polish-cache-updated `plan.json`. Support both **NATIVE** blocks (Jinja templates) and **CATALOG** blocks (installed via `npx hyperframes add` with typed slot injection). Enforce the generated-file contract (§6.9, FR-15) and the determinism contract (§5 of [`00-plan-index.md`](./00-plan-index.md)).

## Inputs

- PRD US-005; §6.2.5 catalog mechanics; §6.4 polish cache; §6.8 canvas; §6.9 file contract; §10 OQ-3 (resolved — absolute `data-start`).
- `SceneGraph`, `BlockSpec`, `SlotSpec`, `REGISTRY` from [`01-schema-and-registry.md`](./01-schema-and-registry.md).
- `LLMProvider` from [`02-providers.md`](./02-providers.md) for `llm_polish=True` fields.
- Scaffolded output dir from [`05-scaffold-lint-repair.md`](./05-scaffold-lint-repair.md).

## Outputs

- Overwritten `index.html`, `meta.json`, `compositions/*.html`, `compositions/transitions/*.html`.
- Updated `plan.json` (with `scene.polished` cache).
- `.framecraft/assembler-traces/scene-NN.json` per scene that invoked a provider.
- `.framecraft/last-plan.json` snapshot for `from-plan` diff comparison (06a reads this).

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/assembler.py` | `Assembler` class — orchestrator |
| `framecraft/rendering/native.py` | Jinja rendering for NATIVE blocks |
| `framecraft/rendering/catalog.py` | `npx hyperframes add` + slot injection for CATALOG blocks |
| `framecraft/rendering/root.py` | `index.html` + `meta.json` emission |
| `framecraft/rendering/audio.py` | `--music` audio-bed injection (US-016 split) |
| `framecraft/rendering/html_walker.py` | Deterministic HTML DOM manipulation via `bs4` (stable ordering) |
| `framecraft/rendering/ids.py` | Deterministic ID helpers (`fc_id`, `scene_slug`) |
| `framecraft/blocks/<block_id>.py` | Per-block `SPEC` and (for NATIVE) Jinja template |
| `tests/test_assembler.py` | Unit tests per scene type |
| `tests/test_determinism.py` | Golden-free determinism test (twice-run produces identical bytes) |

## Dependencies

- 01 (types), 02 (provider), 05 (scaffold runs first to give us a dir to write into), 08 (primer indirectly via 03 — Assembler doesn't use primer directly).

## Implementation steps

### Orchestration

1. **`Assembler` class.**
   ```python
   class Assembler:
       def __init__(self, registry: BlockRegistry, provider: LLMProvider):
           self.registry = registry
           self.provider = provider
           self._polish_cache_hits = 0
           self._polish_cache_misses = 0

       def assemble(self, plan: SceneGraph, out_dir: Path) -> None: ...
       def repair(self, out_dir: Path, plan: SceneGraph, errors_only: list[LintFinding]) -> None: ...
   ```
   - `assemble`: scaffold has already run; this step populates HTML.
   - `repair`: called by `lint_repair` in 05 with a filtered list of `LLM_REPAIRABLE` findings. Re-renders only the affected files; no full re-assembly.

2. **High-level `assemble` flow.**
   ```
   1. Copy plan.json → .framecraft/last-plan.json (pre-existing value, if any).
   2. For each scene in plan.scenes:
      a. Resolve BlockSpec.
      b. Apply polish cache (read scene.polished, decide which fields to re-polish).
      c. If NATIVE: render Jinja. If CATALOG: install (if needed) + inject slots.
      d. Write compositions/scene-NN-<id>.html.
      e. Persist polished values back into scene.polished.
   3. For each transition: render transition sub-comp under compositions/transitions/.
   4. Emit index.html with scene placeholders + transition placeholders on higher track.
   5. Emit meta.json.
   6. If plan.brief.music_path: inject audio element into index.html.
   7. Re-serialize plan.json with scene.polished populated.
   ```

### Generated file contract (§6.9, FR-15)

3. **Per-scene file.** Canonical shape emitted by every block:
   ```html
   <template id="scene-{NN}-{block_id}-template">
     <div
       data-composition-id="scene-{NN}-{block_id}"
       data-width="{canvas.w}"
       data-height="{canvas.h}"
       data-duration="{duration}"
     >
       <style>/* scoped via [data-composition-id="scene-{NN}-{block_id}"] */ ...</style>
       <!-- body: class="clip" on timed elements, ... -->
       <script>
         window.__timelines = window.__timelines || {};
         const tl = gsap.timeline({ paused: true });
         // ...
         window.__timelines["scene-{NN}-{block_id}"] = tl;
       </script>
     </div>
   </template>
   ```
   - `NN` is zero-padded 2-digit scene index (`01`, `02`, ...).
   - `duration` is always serialized to 3 decimal places (avoid float-representation jitter across platforms).

4. **Root `index.html`.**
   ```html
   <!doctype html>
   <html lang="en">
     <head>
       <meta charset="UTF-8">
       <meta name="viewport" content="width={W}, height={H}">
       <title>{meta.name}</title>
       <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
       {brand_font_link}
       <style>
         * { margin: 0; padding: 0; box-sizing: border-box; }
         html, body { width: {W}px; height: {H}px; overflow: hidden; background: {brand.bg or "#09090C"}; font-family: "{brand.headline or "Inter"}", sans-serif; }
         .scene { position: absolute; inset: 0; }
       </style>
     </head>
     <body>
       <div id="root" data-composition-id="main"
            data-start="0" data-duration="{total}"
            data-width="{W}" data-height="{H}">
         {scene_placeholders}
         {transition_placeholders}
         {audio_bed_if_any}
       </div>
     </body>
   </html>
   ```
   - `scene_placeholders`: one per scene, order-matching, `data-start` absolute. See §OQ-3-resolved algorithm below.
   - `transition_placeholders`: one per `TransitionCue`, `data-track-index=10` (above scenes at 1).

### Timing (OQ-3 resolution)

5. **Absolute `data-start` computation.**
   ```
   scene_starts = []
   t = 0.0
   for i, scene in enumerate(plan.scenes):
       scene_starts.append(round(t, 3))
       t += scene.duration
       # apply overlap reduction for the transition that overlaps into the NEXT scene
       cue = next((c for c in plan.transitions if c.from_scene == i), None)
       if cue:
           t -= cue.overlap
   # per-transition start = scene_starts[cue.to_scene] - cue.overlap
   ```
   - Emit absolute `data-start="3.500"` on placeholders. Relative syntax supported by Hyperframes but not used by default path per OQ-3 resolution.

### NATIVE rendering

6. **Jinja environment (`rendering/native.py`).**
   ```python
   env = Environment(
       loader=BaseLoader(),  # templates come inline from BlockSpec.template
       autoescape=True,
       keep_trailing_newline=True,
       trim_blocks=True,
       lstrip_blocks=True,
       undefined=StrictUndefined,
   )
   env.globals["fc_id"] = fc_id           # e.g. fc_id(scene_idx=2, local="chart-bar-3") → "scene-02-chart-bar-3"
   env.globals["canvas_w"] = ...
   env.globals["canvas_h"] = ...
   ```
   - `StrictUndefined` turns any missing prop into a template-authoring bug (fails loud in dev).

7. **Block template contract.**
   - Each `framecraft/blocks/<id>.py` exports:
     ```python
     SPEC = BlockSpec(
         id=BlockId.TITLE_CARD,
         category=Category.TITLE,
         provenance=Provenance.NATIVE,
         synopsis="Full-screen title with optional subtitle and fade-in.",
         aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
         suggested_duration=(2.0, 6.0),
         required_props=TitleCardProps,
         template=lambda props: env.from_string(TEMPLATE).render(props=props, ...)
     )
     TEMPLATE = """..."""
     ```
   - Templates MUST emit exactly the §6.9 shape. A helper `scene_template(inner: str, timeline_js: str)` wraps user-written inner body in the `<template>/<div>/<style>/<script>` envelope so block authors focus on content.

### CATALOG rendering

8. **Install step (`rendering/catalog.py`).**
   - At the start of `assemble`, collect unique `catalog_id` across all CATALOG scenes + transitions.
   - For each, check `out_dir / ".framecraft" / "installed" / {catalog_id}.sha256` exists and matches `spec.catalog_hash` → skip install.
   - Else: `run_npx(["hyperframes", "add", catalog_id, "--version", spec.catalog_version, "--non-interactive"], cwd=out_dir)`. Hyperframes drops files into some location (typically `compositions/<catalog_id>.html` or similar); the adapter captures where by diffing the tree before/after.
   - Compute SHA-256 over sorted (path, content_bytes) of all added files → compare to `spec.catalog_hash`. Mismatch → exit 1: *"Catalog block `<id>` hash mismatch: expected `<pinned>`, got `<actual>`. Re-pin with `framecraft doctor --snapshot <id>`."*
   - Stash hash + manifest at `.framecraft/installed/<id>.json` for idempotency.

9. **Slot injection.**
   ```python
   def inject_slots(installed_html: str, slots: dict[str, SlotSpec], props: BaseModel, provider: LLMProvider, polish_cache: dict[str, str]) -> str:
       soup = BeautifulSoup(installed_html, "html.parser")
       for slot_name, spec in slots.items():
           node = soup.select_one(spec.selector)
           if node is None:
               raise CatalogSlotError(f"slot `{slot_name}` selector `{spec.selector}` matched no element")
           raw = getattr(props, slot_name)
           value = _polish_if_needed(raw, spec, provider, polish_cache, slot_name)
           match spec.kind:
               case "text":     node.string = value
               case "css_var":  node["style"] = _merge_style(node.get("style", ""), {spec.target: value})
               case "attr":     node[spec.target] = value
               case "asset_path": _copy_asset(value, node, spec.target)
       return str(soup)
   ```
   - No regex. Walker operates on a parsed tree.
   - Multiple slots per catalog block are common (e.g. `app-showcase` has `title`, `subtitle`, `primary_color`, `screenshot`).

### Polish cache (§6.4)

10. **Cache read/write.**
    ```python
    def _polish_if_needed(raw: str, spec: SlotSpec | FieldPolishMark, provider: LLMProvider, cache: dict[str, str], field_name: str) -> str:
        if not getattr(spec, "llm_polish", False):
            return raw
        cache_key = f"{field_name}::{sha256(raw.encode()).hexdigest()[:12]}"
        if cache_key in cache:
            self._polish_cache_hits += 1
            return cache[cache_key]
        polished = _call_polish(raw, provider)
        cache[cache_key] = polished
        self._polish_cache_misses += 1
        return polished
    ```
    - Cache key includes field name AND hash of raw input — if either changes, re-polish.
    - Cache is `scene.polished` on the `Scene` model; persists into `plan.json`.

11. **Polish call (`_call_polish`).**
    - Uses the Assembler prompt from `framecraft/prompts/{provider}/assembler.md`.
    - `cache_segments=[load_primer(), tone_prompt]` — primer shared with Director, so cache warms across both.
    - Very low `max_tokens` (≤256); temperature 0.
    - One scene trace file `.framecraft/assembler-traces/scene-NN.json` per scene that invokes polish.

### Audio bed (US-016 split)

12. **Injection (`rendering/audio.py`).**
    - Only runs if `plan.brief.music_path` is set and `validate_music` (from 01) succeeded.
    - Copies file to `out_dir / "assets" / f"music.{ext}"`.
    - Adds to `index.html` root div:
      ```html
      <audio id="music-bed"
             src="assets/music.mp3"
             data-start="0"
             data-duration="{total}"
             data-track-index="20"
             data-volume="{brief.music_volume or 0.4}"></audio>
      ```
    - No `class="clip"` (per schema: audio must not have it).

### Repair path

13. **`repair(errors_only)`.**
    - Inputs: the lint findings classified `LLM_REPAIRABLE` by 05.
    - For each unique `file` in findings: re-render that scene with the findings message appended as a constraint in the polish prompt (for content-shape errors) OR regenerate from `block_props` freshly if non-polish (e.g. missing `class="clip"`).
    - **Does not** call Director.
    - Re-writes only the affected file(s); root `index.html` untouched.
    - Writes a `.framecraft/assembler-traces/repair-<timestamp>.json` trace.

### Plan.json write-back

14. **At end of `assemble`:**
    ```python
    plan_out = plan.model_copy(deep=True)
    for s_out, s_new in zip(plan_out.scenes, updated_scenes):
        s_out.polished = s_new.polished
    (out_dir / "plan.json").write_text(plan_out.model_dump_json(indent=2))
    (out_dir / ".framecraft" / "last-plan.json").write_text(plan_out.model_dump_json(indent=2))
    ```

## Testing strategy

- **Unit (`tests/test_assembler.py`).**
  - NATIVE happy path: render `title-card` → assert output matches a fixed golden.
  - Missing slot selector → `CatalogSlotError`.
  - Catalog hash mismatch (inject wrong `catalog_hash` in a test `BlockSpec`) → exit-1-equivalent raise.
  - Polish cache hit → no provider call on second assembly.
  - Polish cache miss when input changed → provider called once.
- **Determinism (`tests/test_determinism.py`).**
  - Assemble the same plan twice to different dirs; compare file trees byte-for-byte. Both must match.
  - Use stub provider; `scene.polished` populated deterministically.
- **Contract tests.** Every block's template emission passes a mini-lint that enforces §6.9 (presence of `<template>` wrapper, matching `data-composition-id`, `class="clip"` on timed elements excepting video/audio/composition divs).
- **Integration with 05.** `tests/test_assembler_integration.py`: scaffold → assemble a 2-scene promo plan → run real `npx hyperframes lint` → assert zero errors.

## Acceptance (PRD bullets closed)

- US-005: all AC.
- US-016 audio-injection portion.
- FR-1 (project dir structure), FR-5 (reproducible from-plan assembly), FR-10 (brand honoring in templates), FR-12 (determinism), FR-13 (catalog hash lifecycle), FR-14 (dimensions), FR-15 (template wrapping).

## Open questions

- **OQ-F4.1** Should we vendor a pinned GSAP version inside `assets/gsap/` rather than linking CDN? Determinism argument is real; bloat is 130 KB. *Leaning: keep CDN for v1 (matches existing projects); optional `--offline` mode is M3+.*
- **OQ-F4.2** When a CATALOG block ships a `<script>` that registers its own timeline, do we still emit our `window.__timelines[...] = tl` wrapper, or trust the installed file? *Leaning: trust the installed file; our slot injection must not break its script. Add a contract test: after slot injection, the installed file's original timeline registration line still exists verbatim.*
- **OQ-F4.3** Do we emit a `<base href="...">` in `index.html` to help preview pick up `compositions/` paths? *Leaning: no — existing projects use relative paths and work fine.*

## Verification

```bash
# End-to-end with stub provider (requires 05 scaffold + fixtures):
FRAMECRAFT_PROVIDER=stub python - <<'PY'
from pathlib import Path
from framecraft.scaffold import scaffold
from framecraft.assembler import Assembler
from framecraft.providers import make_provider
from framecraft.registry import REGISTRY, BlockRegistry
from framecraft import SceneGraph
# Load a fixture plan.json:
plan = SceneGraph.model_validate_json(Path("tests/fixtures/plans/product-promo.json").read_text())
out = Path("/tmp/fc-assemble-check")
scaffold(out)
Assembler(BlockRegistry(REGISTRY, {}), make_provider("stub")).assemble(plan, out)
print(sorted(p.name for p in (out / "compositions").iterdir()))
PY

# Lint the output (requires 05 plumbed):
cd /tmp/fc-assemble-check && npx hyperframes lint
# → 0 errors

# Determinism proof:
pytest tests/test_determinism.py -v

pytest tests/test_assembler.py -v
```
