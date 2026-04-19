# PRD: FrameCraft — Situation-to-Composition for Hyperframes

> **Tagline.** Describe a moment. Get a rendered video.
> **One-liner.** A Python program that turns a natural-language *situation* into a valid Hyperframes project (HTML compositions + `plan.json`) and, optionally, a rendered MP4.

---

## 1. Introduction / Overview

Hyperframes renders HTML to deterministic video. Authoring those HTML compositions by hand (or by free-form LLM prompting) is fiddly: you must register GSAP timelines, get `data-*` timing attributes right, stitch sub-compositions together, and pass `hyperframes lint` before you dare render. The existing `/hyperframes` Claude Code skill helps *a developer in their editor*, but there is no headless, programmatic path from an idea to a file on disk.

**FrameCraft** closes that gap. Given a one-line *situation* — `"A barista discovers her coffee shop is the last one on Earth"` or `"30-second promo for an AI health insurance app"` — it plans scenes, picks the right blocks from the Hyperframes catalog, fills in copy and timing, writes valid HTML, lints it, and (optionally) hands it to `hyperframes render`. It is a *composer*, not a renderer; Hyperframes remains the runtime.

### The central bet

A single LLM call that outputs HTML is a black box that fails in ways the user cannot debug. FrameCraft instead splits the work into two stages with a **typed, inspectable artifact between them** — a `SceneGraph` saved as `plan.json`. The user can read it, edit it, diff it, re-assemble from it without touching the LLM. That is the whole design.

---

## 2. Goals

- **G1.** Produce a valid Hyperframes project from a single situation string in under 60 seconds (wall-clock, network included).
- **G2.** Pass `npx hyperframes lint` on first try ≥ 85% of the time; pass after one repair pass ≥ 98% of the time.
- **G3.** Ship a `plan.json` that a human can edit and re-assemble from without re-prompting the LLM (`framecraft from-plan`).
- **G4.** Cover four archetypes with hand-picked blocks: narrative scenes, product promos, data explainers, UI walkthroughs, and social cards (1D from clarifying answers).
- **G5.** Be provider-pluggable at runtime: a `LLMProvider` protocol with two shipped implementations — **Gemini** (default) and **Anthropic/Claude** — and a deterministic stub for tests. Switching is one env var: `FRAMECRAFT_PROVIDER=gemini|anthropic|stub`.

### Non-goals (out of scope)

See §5 — large enough to deserve its own section.

---

## 3. User Stories

### US-001: Package skeleton and CLI entry point

**Description:** As a developer, I want `pip install -e .` to give me a working `framecraft` CLI so the rest of the stories have a place to land.

**Acceptance Criteria:**
- [ ] `pyproject.toml` with `framecraft` console-script entry point pointing at `framecraft.cli:main`
- [ ] `framecraft --help` prints top-level commands: `compose` (default), `from-plan`, `catalog`, `doctor`, `render`, `preview`
- [ ] `framecraft doctor` reports presence/version of `node`, `npx`, `npx hyperframes --version`, `ffmpeg`, the active `FRAMECRAFT_PROVIDER`, and presence/absence of the relevant API keys (`GEMINI_API_KEY`/`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`) — never the values
- [ ] `ruff check` and `mypy` pass on the skeleton

### US-002: `SceneGraph` Pydantic schema

**Description:** As the runtime, I need a single source of truth for the artifact that flows between Director and Assembler.

**Acceptance Criteria:**
- [ ] `framecraft/schema.py` defines `Brief`, `SceneGraph`, `Scene`, `TransitionCue`, `Caption`, `BrandKit`, `Palette`, `Typography` as Pydantic v2 models
- [ ] `Aspect`, `Mood`, `Archetype`, `BlockId`, `TransitionId` are typed enums
- [ ] `Aspect` carries a `.dimensions: tuple[int, int]` property: `AR_16_9 → (1920, 1080)`, `AR_9_16 → (1080, 1920)`, `AR_1_1 → (1080, 1080)`. `SceneGraph.canvas: tuple[int, int]` is derived from `aspect.dimensions` and is the single source of truth threaded into every `data-width` / `data-height` the Assembler emits
- [ ] Cross-field validators: `sum(scene.duration) + sum(transition.overlap adjustments) == SceneGraph.duration ± 0.1s`; every `scene.block_id` exists in the registry; `scene.start` values are non-decreasing; `scene.block_props` validates against the block's prop schema; `SceneGraph.canvas == SceneGraph.aspect.dimensions`
- [ ] `SceneGraph.model_json_schema()` round-trips and is stable across runs (for caching)
- [ ] Unit tests cover 6 invalid graphs and assert each validator fires

### US-003: Block Registry (v1 curated subset)

**Description:** As the Director, I need a small, opinionated set of blocks to choose from — not all 50+ Hyperframes catalog items. A wide menu is a hallucination trap.

**Acceptance Criteria:**
- [ ] `framecraft/registry.py` exposes a `REGISTRY: dict[BlockId, BlockSpec]` with 12–16 entries spanning: title/text, background (gradient, grain overlay), logo-outro, app-showcase, data-chart, flowchart, 3D UI reveal, shimmer-sweep, macOS notification, 3 shader transitions (cinematic-zoom, flash-through-white, whip-pan), and one social card (instagram-follow)
- [ ] Each `BlockSpec` has: `id`, `category`, `synopsis` (≤ 140 chars, written for LLM consumption), `required_props` + `optional_props` as Pydantic models, `suggested_duration: tuple[float, float]`, `aspect_preferred: list[Aspect]`, `provenance: Provenance`, `catalog_id: str | None`, `catalog_version: str | None`, `catalog_hash: str | None`, `install_command: str | None`, `template: Callable[[props], str]`, `slots: dict[str, SlotSpec]` (see §6.2.5)
- [ ] `Provenance` is an enum: `NATIVE` (FrameCraft-authored Jinja template) or `CATALOG` (installed via `npx hyperframes add <catalog_id>` and post-processed via `slots`). Every `BlockSpec` declares exactly one; validators enforce the cross-field rules in §6.2.5
- [ ] `BlockRegistry.allowed_for(archetype)` returns the subset a given archetype may pick from
- [ ] `framecraft catalog` prints registry as a table (with a `Provenance` column) and `framecraft catalog --json` emits the full schema
- [ ] Adding a new block requires only a new file under `framecraft/blocks/` — no changes to registry code

### US-004: Director stage — situation to `SceneGraph`

**Description:** As a user, I want the Director to read my situation and emit a structured plan, not HTML.

**Acceptance Criteria:**
- [ ] `framecraft/director.py` exposes `Director(provider: LLMProvider).plan(brief: Brief) -> SceneGraph`
- [ ] System prompt contains: Hyperframes primer, archetype definitions, block registry JSON, `SceneGraph` schema. Everything up to and including the registry is declared as a **stable cache segment** via `cache_segments=[...]`; the provider adapter translates that into Anthropic `cache_control` breakpoints or Gemini `cachedContents` as appropriate (see §6.5)
- [ ] Director first classifies the brief into an `Archetype`, then plans 2–6 scenes using only `registry.allowed_for(archetype)`
- [ ] Output parsed as JSON against `SceneGraph`; Pydantic validation errors trigger one retry with the error text appended
- [ ] Integration test: 3 reference situations (one per archetype) produce valid SceneGraphs from recorded provider responses (VCR-style). Fixtures exist for both the Gemini and Anthropic adapters; `pytest -k golden` runs against the default provider, `pytest -k golden_anthropic` against Claude
- [ ] Director writes `.framecraft/director-trace.json` with prompt, response, token counts, cache hit/miss, provider name + model ID, elapsed ms

### US-005: Assembler stage — `SceneGraph` to HTML

**Description:** As the runtime, I turn the plan into a set of HTML files that Hyperframes can render.

**Acceptance Criteria:**
- [ ] `framecraft/assembler.py` exposes `Assembler(registry, provider).assemble(plan, out_dir)`
- [ ] For each scene, `NATIVE` blocks render via the block's deterministic Jinja template; `CATALOG` blocks are realized by invoking the catalog install (once, at scaffold time) and applying the block's declared `slots` post-processing to inject props into the installed HTML (see §6.2.5). Copy fields that the template or slot marks `llm_polish=True` get one LLM pass per scene via the active provider; everything else is pure template or pure slot substitution
- [ ] Per-scene file: `compositions/scene-<NN>-<block-id>.html`. The file begins with `<template id="scene-<NN>-<block-id>-template">`, contains a single root `<div data-composition-id="scene-<NN>-<block-id>" data-width="{canvas.w}" data-height="{canvas.h}" data-duration="...">` whose internals are produced by the block, and ends with `</template>`. The GSAP timeline for the scene is registered on `window.__timelines["scene-<NN>-<block-id>"]` inside the template's `<script>`. `class="clip"` is applied to every timed element the schema requires it on (everything except `<video>`, `<audio>`, and composition divs)
- [ ] Root `index.html` sets `<meta name="viewport" content="width={canvas.w}, height={canvas.h}">`, a root `<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="{canvas.w}" data-height="{canvas.h}">`, and one placeholder child per scene: `<div data-composition-id="scene-<NN>-<block-id>" data-composition-src="compositions/scene-<NN>-<block-id>.html" data-start="..." data-duration="..." data-track-index="1" data-width="{canvas.w}" data-height="{canvas.h}">`. No scene HTML is inlined
- [ ] `hyperframes.json` and `meta.json` emitted with project metadata
- [ ] Golden-file tests: same `SceneGraph` input always produces byte-identical HTML (no timestamps, deterministic element IDs). `llm_polish` outputs are persisted into `plan.json` (see §6.4) so second and subsequent assemblies of a given plan are fully deterministic even with a live provider

### US-006: Project scaffolder — wrap `npx hyperframes init`

**Description:** As the system, I want to stay aligned with upstream scaffolding rather than reimplementing it.

**Acceptance Criteria:**
- [ ] `framecraft/scaffold.py` runs `npx hyperframes init <out> --example blank --non-interactive --skip-transcribe` via `subprocess.run`, capturing stdout/stderr
- [ ] Failure modes surfaced with actionable errors: `npx` missing, network failure, target dir exists and non-empty, hyperframes CLI version < pinned floor
- [ ] After init, FrameCraft overwrites `index.html`, `compositions/*`, `meta.json` with its own files; leaves `hyperframes.json` and any `.gitignore` unchanged
- [ ] Pinned CLI version floor in `framecraft/_compat.py`; bumped deliberately

### US-007: Lint-repair loop

**Description:** As a user, I want `framecraft` to catch Hyperframes violations before I waste time on a render.

**Acceptance Criteria:**
- [ ] After assembly, run `npx hyperframes lint --json` in the out dir; parse result
- [ ] Each lint rule is classified in `framecraft/lint_policy.py` as either `FRAMECRAFT_BUG` (e.g. duplicate `data-composition-id`, missing `window.__timelines` registration, invalid `data-composition-src` path, duplicate element IDs) or `LLM_REPAIRABLE` (e.g. content-shaped errors: copy length, missing `class="clip"` on a new element an `llm_polish` pass introduced). `FRAMECRAFT_BUG` failures abort with exit code 2 and a message that says "this is a FrameCraft template bug, not an LLM issue — open an issue with `.framecraft/lint-report.json` attached" — **no Claude call is made**
- [ ] `LLM_REPAIRABLE` failures pass the failing HTML file(s) + the lint JSON (errors only, warnings stripped) back to the Assembler for one repair pass; overwrite the file; re-lint
- [ ] If lint still fails after the repair: abort with exit code 2, print the lint JSON to stderr, and write it to `.framecraft/lint-report.json`
- [ ] Warnings never block; they are printed once at the end
- [ ] Integration tests cover both paths: (1) inject a content-shaped error → repair fixes it, second lint passes; (2) inject a duplicate `data-composition-id` → aborts with exit 2 and zero LLM calls (assert via stub provider call count)

### US-008: `framecraft compose` — end-to-end happy path

**Description:** As a user, I type `framecraft "a situation"` and get a project on disk.

**Acceptance Criteria:**
- [ ] `framecraft compose <situation> [--out ./out] [--aspect 16:9] [--duration 20] [--fps 30] [--mood cinematic] [--archetype auto] [--render] [--open]`
- [ ] `--out` defaults to a slug of the situation; refuses to overwrite a non-empty non-FrameCraft directory
- [ ] Runs: scaffold → Director → Assembler → lint-repair → (optional) render
- [ ] Progress is shown with a step-by-step log: `[1/5] Planning scenes…`, `[2/5] Assembling 4 scenes…`, etc. No spinners on non-TTY
- [ ] Exit codes: `0` success, `1` usage/config error, `2` lint failure after repair, `3` render failure, `4` LLM/provider error
- [ ] On success, prints the paths of `plan.json`, `index.html`, and (if rendered) the MP4

### US-009: `framecraft from-plan <plan.json>` — re-assemble without LLM

**Description:** As a power user, I want to edit `plan.json` and re-run assembly without burning tokens.

**Acceptance Criteria:**
- [ ] Loads and validates `plan.json` against `SceneGraph`; prints a diff-style summary of changes vs. the previous run (if `.framecraft/last-plan.json` exists)
- [ ] Runs Assembler + lint-repair only; skips Director
- [ ] Scene-level templates that had `llm_polish=True` are re-polished *only if the copy field changed*; otherwise reused from the previous run's cache

### US-010: Branding pass — `--logo`, `--palette`, `--font`

**Description:** As a user with brand constraints, I want FrameCraft to respect them without a full brand kit file.

**Acceptance Criteria:**
- [ ] `--logo <path>` copies the file to `assets/logo.{svg,png}` and injects it into the `BrandKit`; Director is told a logo is available
- [ ] `--palette "#0A0A0F,#F5F5F0,#C44536"` parsed into `Palette(primary, bg, accent)`; bad hex fails fast
- [ ] `--font "Inter"` or `--font "Libre Baskerville"` adds the font to `<head>` via Google Fonts with `&display=block` (matches existing project convention in `/frames/kinetic-type/index.html`)
- [ ] Flags also readable from `framecraft.yaml` in the current directory

### US-011: `framecraft render` — shell out to Hyperframes

**Description:** As a user, I want rendering to be one flag or one command.

**Acceptance Criteria:**
- [ ] `framecraft render [--out ./project] [--format mp4|webm|mov] [--quality draft|standard|high] [--fps 30|60]` runs `npx hyperframes render` in the project dir
- [ ] `--render` on `compose` is equivalent to running `render` after `compose`
- [ ] Render output path logged; non-zero exit from Hyperframes surfaces as FrameCraft exit code 3
- [ ] We do **not** reimplement encoding; all flags are pass-through where possible

### US-015: `framecraft preview` — shell out to Hyperframes preview

**Description:** As a user iterating on `plan.json`, I want to see the composition in a browser without rendering.

**Acceptance Criteria:**
- [ ] `framecraft preview [--out ./project] [--port 4000]` runs `npx hyperframes preview` in the project dir
- [ ] Backgrounds the subprocess, prints the URL, streams its stderr
- [ ] Ctrl-C cleanly stops the preview; exit code mirrors the underlying CLI
- [ ] Re-running `framecraft from-plan` while preview is live does not crash preview — Hyperframes already hot-reloads

### US-016: `--music <path>` — optional audio bed (not generated)

**Description:** As a user, I want a user-provided audio track to play under the video without FrameCraft generating anything.

**Acceptance Criteria:**
- [ ] `--music <path>` copies the file to `assets/music.{mp3,wav,m4a}` and adds a single `<audio data-start="0" data-duration="{total}" data-volume="0.4">` to `index.html`
- [ ] No music generation, no beat-matching, no ducking. Volume is a flag (`--music-volume 0.4`, default `0.4`), nothing else
- [ ] If `--music` is omitted, the video is silent (matches NG-3)
- [ ] Validator rejects files that are not `.mp3` / `.wav` / `.m4a`, and rejects durations shorter than `SceneGraph.duration` with an actionable message

### US-012: Observability — traces, plans, lint reports

**Description:** As a user, I want to know what happened and be able to rerun it.

**Acceptance Criteria:**
- [ ] `.framecraft/director-trace.json` — system prompt hash, user message, raw response, token usage, cache hit/miss, seconds
- [ ] `.framecraft/assembler-traces/scene-NN.json` — per-scene polish calls (only when `llm_polish` fired)
- [ ] `.framecraft/lint-report.json` — final lint output (errors and warnings)
- [ ] `plan.json` at project root (not hidden) — this is user-facing
- [ ] All files gitignored by default via the scaffold step

### US-013: `LLMProvider` protocol + Gemini + Anthropic + stub

**Description:** As a maintainer, I want a provider-agnostic interface with two real backends and a deterministic test double.

**Acceptance Criteria:**
- [ ] `framecraft/providers/base.py` defines `LLMProvider` protocol with `complete(messages, *, system, schema=None, cache_segments=None) -> ProviderResponse`. `ProviderResponse` carries `text`, `parsed` (if `schema` given), `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `provider`, `model`, `elapsed_ms`
- [ ] `framecraft/providers/gemini.py` — **default provider**. Gemini 2.5 Pro as Director, Gemini 2.5 Flash as Assembler (env-overridable via `FRAMECRAFT_DIRECTOR_MODEL` / `FRAMECRAFT_ASSEMBLER_MODEL`). Structured output via `response_mime_type="application/json"` + `response_schema`. Caching: when `cache_segments` is supplied, the adapter hashes the stable prefix and lazily creates a `cachedContents` entry keyed by that hash; subsequent calls pass the cache ID. Cache objects TTL 1h, recreated on demand
- [ ] `framecraft/providers/anthropic.py` — Claude Opus 4.7 as Director, Claude Sonnet 4.6 as Assembler (env-overridable). Caching: maps `cache_segments` to inline `cache_control: {type: "ephemeral"}` breakpoints on the system-block boundaries
- [ ] `framecraft/providers/stub.py` — reads responses from `tests/fixtures/llm/<provider>/*.json` keyed by a hash of `(provider, system, messages)`; fails loudly on cache miss (no fallback to network)
- [ ] Provider selection: `FRAMECRAFT_PROVIDER` env var (`gemini` | `anthropic` | `stub`), default `gemini`. Also `compose(provider=...)` kwarg for library use. `framecraft doctor` prints which provider is active and whether its API key is present
- [ ] API keys: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for Gemini; `ANTHROPIC_API_KEY` for Claude. Missing key when that provider is selected → exit 1 with actionable message naming the correct env var
- [ ] Each provider has its own prompt-wrapper layer (`framecraft/prompts/gemini/` vs `framecraft/prompts/anthropic/`) that adapts the shared primer to the provider's preferred structured-output conventions. Primer body is shared; only the "how to emit JSON" framing differs

### US-014: Golden tests — three reference situations

**Description:** As a maintainer, I want CI to catch regressions in planning or assembly.

**Acceptance Criteria:**
- [ ] Three fixtures: narrative (`"a barista discovers the coffee shop is the last on Earth"`), product promo (`"30-second promo for an AI health insurance app called ShieldMax"`), data explainer (`"15-second explainer of how our energy usage dropped 40% after the switch to solar"`)
- [ ] Each fixture has recorded provider responses (stub) for **both** Gemini and Anthropic under `tests/fixtures/llm/<provider>/`, plus a golden `plan.json` + golden HTML tree. Goldens are provider-agnostic: the same `SceneGraph` input must produce the same HTML regardless of which adapter generated the plan
- [ ] `pytest -k golden` passes; a `--update-goldens` flag regenerates them
- [ ] Golden HTML is byte-compared; divergence fails the test with a diff

---

## 4. Functional Requirements

- **FR-1.** FrameCraft shall accept a situation as a single positional string and produce a Hyperframes project directory containing `index.html`, `compositions/`, `hyperframes.json`, `meta.json`, and `plan.json`.
- **FR-2.** The Director shall classify every brief into exactly one of: `NARRATIVE_SCENE`, `PRODUCT_PROMO`, `DATA_EXPLAINER`, `UI_WALKTHROUGH`, `SOCIAL_CARD`.
- **FR-3.** The Director shall choose blocks only from the subset returned by `BlockRegistry.allowed_for(archetype)`. Hallucinated block IDs shall fail Pydantic validation before any HTML is written.
- **FR-4.** Every generated composition shall pass `npx hyperframes lint` after at most one Assembler repair pass. Failure aborts with exit code 2.
- **FR-5.** The SceneGraph shall be serialized as `plan.json` at the project root on every successful run. Assembly shall be reproducible: `framecraft from-plan plan.json` produces byte-identical HTML given identical inputs.
- **FR-6.** All LLM calls shall use provider-appropriate prompt caching keyed on the stable prefix (Hyperframes primer + block registry + JSON schema). The `LLMProvider` protocol exposes `cache_segments`; each adapter maps it to its native mechanism — Anthropic `cache_control: ephemeral` breakpoints, Gemini `cachedContents` entries keyed by prefix hash. Warmup cost shall be paid once per schema bump, not per run.
- **FR-7.** The default provider shall be **Gemini** (Director: Gemini 2.5 Pro, Assembler: Gemini 2.5 Flash). The **Anthropic** provider shall be a first-class alternative (Director: Claude Opus 4.7, Assembler: Claude Sonnet 4.6). Provider and both model IDs are overridable by env var (`FRAMECRAFT_PROVIDER`, `FRAMECRAFT_DIRECTOR_MODEL`, `FRAMECRAFT_ASSEMBLER_MODEL`).
- **FR-8.** Project scaffolding shall delegate to `npx hyperframes init --example blank --non-interactive`. FrameCraft shall not emit `hyperframes.json` itself.
- **FR-9.** The CLI shall expose: `compose` (default), `from-plan`, `catalog`, `doctor`, `render`, `preview`. Exit codes are standardized (see US-008 AC).
- **FR-10.** When `--logo`, `--palette`, or `--font` flags are supplied, the values shall flow into the `BrandKit` field of the SceneGraph and be honoured by every block template that supports branding.
- **FR-11.** The system shall write `.framecraft/director-trace.json` and `.framecraft/lint-report.json` on every run regardless of success/failure, for post-mortem.
- **FR-12.** Element IDs, class suffixes, and anything else in generated HTML shall be deterministic — derived from scene index or content hash, never timestamps or `uuid4()`. `llm_polish` copy is cached into `plan.json` on first assembly and reused verbatim thereafter, so repeated assemblies of the same plan produce byte-identical HTML.
- **FR-13.** Every `BlockSpec` shall declare a `provenance` of either `NATIVE` or `CATALOG`. `NATIVE` blocks carry a FrameCraft-owned Jinja template. `CATALOG` blocks carry a `catalog_id`, a pinned `catalog_version`, a `catalog_hash` (SHA-256 of the installed block's tree), and a typed `slots` map describing how FrameCraft injects props into the installed HTML. `framecraft doctor` shall verify the installed hash matches the pinned hash and surface a re-pin instruction on mismatch (see §6.2.5).
- **FR-14.** The `Aspect` enum shall carry canonical `(width, height)` pixel dimensions: `16:9 → (1920, 1080)`, `9:16 → (1080, 1920)`, `1:1 → (1080, 1080)`. Every root composition, every sub-composition root `<div>`, and every root-level placeholder in `index.html` shall set `data-width` and `data-height` from this single source.
- **FR-15.** Every file under `compositions/` shall be a sub-composition wrapped in `<template id="<composition-id>-template">…</template>`, with a single root `<div data-composition-id="<composition-id>" data-width data-height data-duration>` as the only child. `index.html` shall reference sub-compositions exclusively via `data-composition-src` placeholders — no scene HTML is inlined.
- **FR-16.** The Hyperframes primer embedded in the Director's cached system prompt shall be a build-time snapshot of upstream documentation, generated by `scripts/snapshot_primer.py` and shipped as `framecraft/prompts/primer.md` with a matching `primer.lock.json` manifest of source URLs and SHA-256 hashes. `framecraft doctor` shall verify the manifest against live upstream and warn on drift. The runtime never fetches the primer over the network.

---

## 5. Non-Goals (Out of Scope for v1)

- **NG-1.** Reimplementing Hyperframes rendering, encoding, or preview. We shell out.
- **NG-2.** Hosting, SaaS, web UI, REST API. Library + CLI only.
- **NG-3.** Music and sound-effect *generation*. Audio defaults to silence. A user-provided file can be attached as a single audio bed via `--music <path>` (US-016); FrameCraft does not synthesize, beat-match, or duck audio in v1.
- **NG-4.** Narration / TTS. Stretch — would wrap `npx hyperframes tts` (Kokoro-82M on-device). Explicitly not in v1 scope.
- **NG-5.** Caption generation via Whisper. Stretch — would wrap `npx hyperframes transcribe`. Not v1.
- **NG-6.** Stock asset sourcing from Unsplash / Pexels. Stretch; risks licence complexity we don't want to own in v1.
- **NG-7.** Multi-language. Director is English-only in v1.
- **NG-8.** More than one repair pass on lint failure. Diminishing returns; two passes usually means the block template is broken and should be fixed in source.
- **NG-9.** Arbitrary user-supplied blocks. Registry is closed in v1 — extension is a code change, not runtime config.
- **NG-10.** LLM providers beyond Gemini and Anthropic. The `LLMProvider` protocol admits more adapters, but v1 ships only Gemini (default) and Anthropic. No OpenAI, Vertex, Bedrock, Mistral, or local-model adapters.
- **NG-11.** Supporting the full 50+ Hyperframes catalog. Curated 12–16 in v1; each added block is a deliberate product decision.

---

## 6. Design Considerations

### 6.1 The two-stage pipeline — and why

```
situation ──► [Director]  ──► SceneGraph (plan.json) ──► [Assembler] ──► HTML + lint-repair
                 │                    ▲                       │
                 │                    │                       │
           Gemini 2.5 Pro      editable by human       Jinja templates
           (or Claude Opus 4.7) & re-assemblable       + targeted Flash/Sonnet polish
```

The `plan.json` in the middle is the whole point. A user who gets a weird render doesn't have to re-prompt — they open `plan.json`, change `scenes[2].copy.headline`, bump `duration`, swap a `block_id`, and rerun `framecraft from-plan`. This is the difference between a toy and a tool.

### 6.2 Block Registry as the Director's tool surface

The registry is a narrow, typed menu rather than the Hyperframes universe. The Director cannot pick a block that doesn't exist because the enum doesn't contain it, and Pydantic rejects the plan before any HTML is emitted. v1 ships roughly these categories:

| Category              | v1 blocks                                                       | Archetypes                 |
| --------------------- | --------------------------------------------------------------- | -------------------------- |
| Title / text          | `title-card`, `lower-third`, `end-card`                         | all                        |
| Background            | `gradient-bg`, `grain-overlay`                                  | all                        |
| Branding / identity   | `logo-outro`                                                    | promo                      |
| Product / UI          | `app-showcase`, `3d-ui-reveal`                                  | promo, UI walkthrough      |
| Data                  | `data-chart`, `flowchart`                                       | data explainer             |
| Social                | `instagram-follow`                                              | social card                |
| Notification          | `macos-notification`                                            | UI walkthrough             |
| Transition (sub-comp) | `cinematic-zoom`, `flash-through-white`, `whip-pan`             | all                        |

Each one is a file under `framecraft/blocks/` with a `BlockSpec` and a Jinja template. Adding `instagram-follow` required zero registry edits — it drops in.

### 6.2.5 Registry ↔ Hyperframes catalog relationship

The Hyperframes catalog ships ~40 real, installable blocks (`npx hyperframes add <id>` drops HTML + assets into a project). FrameCraft's registry is not a parallel universe — it is a curated *interface* onto the catalog plus a few FrameCraft-native primitives. Each `BlockSpec` declares exactly one `provenance`:

| Provenance | What it is | When we use it | Prop injection |
| --- | --- | --- | --- |
| `NATIVE` | Jinja template authored inside `framecraft/blocks/` | Small primitives where we want full control over layout and timing: `title-card`, `lower-third`, `end-card`, `gradient-bg`, `grain-overlay` | Jinja rendering |
| `CATALOG` | The installed output of `npx hyperframes add <catalog_id>` | Everything non-trivial: `app-showcase`, `3d-ui-reveal`, `data-chart`, `flowchart`, `shimmer-sweep`, `macos-notification`, `logo-outro`, `instagram-follow`, and the three shader transitions | Declarative `slots` post-processing on the installed HTML |

**`CATALOG` mechanics.** At scaffold time the Assembler calls `npx hyperframes add <catalog_id>` for every `CATALOG` block the plan uses. The installed file is hashed and compared against the `BlockSpec`'s pinned `catalog_hash` — mismatch aborts with exit 1 and an actionable "re-pin with `framecraft doctor --snapshot <block-id>`" message. The block's `slots: dict[str, SlotSpec]` declares the typed injection points:

```python
class SlotSpec(BaseModel):
    kind: Literal["text", "css_var", "attr", "asset_path"]
    selector: str        # CSS selector scoped to the installed file
    target: str          # "textContent" | "--primary-color" | "src" | etc.
    llm_polish: bool = False
```

Injection is a single, deterministic HTML walk over the installed file — no regex string replacement, no LLM rewrites of framework-authored markup. This bounds what the LLM is allowed to change on catalog blocks to the values of declared slots, which is the whole point.

**Why not reimplement catalog blocks as Jinja?** Upstream-owned blocks contain tuned GSAP timelines, shader code, and SVG assets we don't want to maintain. `CATALOG` provenance delegates that maintenance upstream and takes a pinned snapshot for determinism. When upstream ships a new version, we re-pin deliberately — the same way we already pin the CLI version floor in `framecraft/_compat.py`.

### 6.3 Situation archetypes

Five archetypes. The Director classifies first, plans second. Clean separation keeps the block menu small for any single plan and makes the system behave predictably.

| Archetype         | Signals                                                            | Typical shape                                          |
| ----------------- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| Narrative scene   | characters, emotional beats, a twist, literary phrasing            | 3–5 text-over-background scenes with shader cuts       |
| Product promo     | product name, feature list, brand words, "X seconds"               | logo intro → feature scenes → logo outro               |
| Data explainer    | numbers, "X went up by Y%", datasets, "explain"                    | title → 2–3 data-chart / flowchart scenes → end card   |
| UI walkthrough    | "app", "flow", "user goes through"                                 | app-showcase → 3D UI reveals → callout notification    |
| Social card       | "Instagram post", "tweet", "follow banner"                         | single-block card composition, vertical aspect         |

### 6.4 Determinism

Byte-identical HTML for identical inputs. No `Date.now()`, no `uuid4()` in generated files. Element IDs are `scene-01-title`, `scene-02-chart-bar-3` — derived from scene index and block-local counters. This lets golden tests work and lets users `git diff` rendered HTML meaningfully.

**`llm_polish` and the polish cache.** Any field marked `llm_polish=True` (on a block template or a catalog slot) calls Claude at assembly time, which is non-deterministic even at temperature 0. To keep the determinism promise honest, the Assembler persists polished values back into the `SceneGraph` under `scene.polished: dict[str, str]` and re-serializes `plan.json` at the end of every run. Subsequent assemblies read from `scene.polished[field]` and only re-polish a field when its pre-polish input changes. Consequence: the *first* assembly of a fresh plan is non-deterministic; every assembly after that is byte-identical. Golden tests use the stub provider, so they see deterministic output on the first run too.

### 6.5 Prompt caching

The cached prefix contains (in order): Hyperframes primer (static), block registry JSON (changes only when we ship new blocks), SceneGraph JSON schema (changes only on schema bump). The cache boundary sits after these three; the user message is never cached. This keeps per-call cost roughly at "output tokens + cache read" after warmup.

Each provider translates the shared `cache_segments=[primer, registry, schema]` hint into its native mechanism:

| Provider | Mechanism | Shape |
| --- | --- | --- |
| **Anthropic** | Inline `cache_control: {type: "ephemeral"}` breakpoint on the final stable system block | Per-call, ephemeral (5-min TTL); auto-refreshes on each cache read |
| **Gemini** | `cachedContents` — a pre-created cache object | Created lazily via `client.caches.create(model, system_instruction=..., contents=...)`, keyed by `sha256(primer + registry + schema + model_id)`. Cache ID reused across calls; 1h TTL; FrameCraft re-creates on expiry |

The **warmup cost asymmetry matters**: Anthropic caches form on the first call (a normal API request writes the cache); Gemini requires an explicit `caches.create()` step *before* the first completion. FrameCraft hides this behind `provider.complete(..., cache_segments=...)` — the Gemini adapter checks a local `~/.cache/framecraft/gemini-caches.json` for a live cache ID and creates one if absent or expired. Net effect from the caller's perspective: caching "just works" on either provider.

**Primer maintenance.** The "Hyperframes primer" is not hand-written prose that drifts from upstream. A build-time script (`scripts/snapshot_primer.py`) fetches a fixed set of pages from `https://hyperframes.heygen.com/llms.txt` — at minimum `concepts/compositions.md`, `concepts/data-attributes.md`, `reference/html-schema.md`, `guides/gsap-animation.md`, and `guides/common-mistakes.md` — concatenates them into `framecraft/prompts/primer.md`, and records a manifest with each source URL and its SHA-256 in `framecraft/prompts/primer.lock.json`. `framecraft doctor` re-fetches the manifest URLs and flags any hash drift with an "upstream docs changed, re-snapshot" warning. The primer is shipped inside the Python wheel — offline runs use the snapshotted copy, never the network.

### 6.6 File layout FrameCraft produces

```
<out>/
├── index.html                      # root composition (FrameCraft-written)
├── hyperframes.json                # from `hyperframes init`, untouched
├── meta.json                       # FrameCraft-written
├── plan.json                       # the SceneGraph — user-facing, edit this
├── compositions/
│   ├── scene-01-title-card.html
│   ├── scene-02-data-chart.html
│   └── transitions/
│       └── t-01-cinematic-zoom.html
├── assets/
│   └── logo.svg                    # only if --logo was given
├── renders/                        # created by `hyperframes render`
└── .framecraft/
    ├── director-trace.json
    ├── assembler-traces/
    │   └── scene-02.json
    ├── lint-report.json
    └── last-plan.json
```

This matches the convention already in `/Users/kartik/Documents/Work/Projects/craft/frames/{kinetic-type,product-promo,aia-health-shield}/` — same shape, plus `plan.json` and `.framecraft/`.

### 6.7 UX examples

```bash
# 1) Cold start
framecraft "a barista discovers the coffee shop is the last one on Earth" \
  --duration 20 --mood cinematic --render

# 2) Brand-constrained promo
framecraft "30-second promo for ShieldMax, an AI health insurance app" \
  --aspect 9:16 --logo ./shieldmax.svg --palette "#0A0A0F,#F5F5F0,#C44536" \
  --font "Inter" --out ./shieldmax-promo --render

# 3) Iterate on the plan, not the prompt
$EDITOR ./shieldmax-promo/plan.json
framecraft from-plan ./shieldmax-promo/plan.json

# 4) Inspect what’s available
framecraft catalog
framecraft doctor
```

### 6.8 Aspect → canvas dimensions

Hyperframes requires explicit pixel dimensions (`data-width`, `data-height`) on every composition root. FrameCraft's `Aspect` enum is therefore a typed pair, not a string:

| Aspect  | Dimensions (w × h) | Typical use               |
| ------- | ------------------ | ------------------------- |
| `16:9`  | 1920 × 1080        | promo, narrative, explainer (default) |
| `9:16`  | 1080 × 1920        | social card, vertical promo |
| `1:1`   | 1080 × 1080        | social card               |

`SceneGraph.canvas: tuple[int, int]` is derived from `aspect.dimensions` at plan time and is the only place pixel dimensions live. Every piece of HTML the Assembler emits — root `index.html`, each `compositions/*.html` template, each sub-composition placeholder — reads from this single field. Changing aspect means changing `plan.json` and re-assembling; no HTML-level edits.

A `BlockSpec` whose `aspect_preferred` excludes the plan's aspect triggers Open Question 2's fallback path: the Director is re-prompted with the aspect constraint, or the block is swapped for its declared `fallback_block_id` if present.

### 6.9 Generated file contract

Every file under `compositions/` — native or catalog — conforms to this exact shape, and the linter confirms it:

```html
<!-- compositions/scene-02-data-chart.html -->
<template id="scene-02-data-chart-template">
  <div
    data-composition-id="scene-02-data-chart"
    data-width="1920"
    data-height="1080"
    data-duration="4.5"
  >
    <style>/* scoped via [data-composition-id="scene-02-data-chart"] */</style>
    <!-- block-rendered body: timed elements carry class="clip",
         data-start, data-duration, data-track-index -->
    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      /* ... */
      window.__timelines["scene-02-data-chart"] = tl;
    </script>
  </div>
</template>
```

And `index.html` references it exclusively via a placeholder:

```html
<div id="root" data-composition-id="main"
     data-start="0" data-duration="20"
     data-width="1920" data-height="1080">
  <div data-composition-id="scene-02-data-chart"
       data-composition-src="compositions/scene-02-data-chart.html"
       data-start="3.5" data-duration="4.5" data-track-index="1"
       data-width="1920" data-height="1080"></div>
  <!-- one placeholder per scene, in order -->
</div>
```

This matches the convention already used by `aia-health-shield-gold-max/compositions/*.html` and their `index.html`. FrameCraft does not invent a new shape — it produces the same shape a human author would write, every time.

---

## 7. Technical Considerations

### 7.1 Stack

- **Language:** Python 3.11+ (Pydantic v2 perf, `StrEnum`, structural pattern matching).
- **Dependencies:** `pydantic>=2`, `google-genai`, `anthropic`, `jinja2`, `typer` (CLI), `rich` (progress), `httpx` (for any stretch stock-media fetch). Both LLM SDKs are hard deps — the adapter layer imports lazily so missing SDK errors only surface if that provider is actually selected.
- **Dev:** `pytest`, `ruff`, `mypy --strict`, `pytest-recording` for LLM VCR-style fixtures.

### 7.2 Integration points

- **`npx hyperframes`** — scaffolding, linting, rendering. We subprocess it. Pinned minimum version checked in `framecraft doctor`.
- **Gemini SDK (`google-genai`)** — default Director and Assembler calls. Prompt caching via `cachedContents` is a first-class design constraint.
- **Anthropic SDK (`anthropic`)** — opt-in alternate provider (`FRAMECRAFT_PROVIDER=anthropic`). Prompt caching via `cache_control: ephemeral` breakpoints.
- **Filesystem** — we write a project; we don't hold it in memory. This makes failures debuggable.

### 7.3 Performance targets

- Director: ≤ 8 s p50, ≤ 20 s p95 (one Opus call).
- Assembler: ≤ 1.5 s per `llm_polish` scene (Sonnet); pure-template scenes ≤ 50 ms each.
- Lint + repair: ≤ 5 s typical.
- **Total wall-clock for a 4-scene 20-second promo:** target ≤ 30 s before render; ≤ 2 min including a `--quality standard` render.

### 7.4 Error model

Every failure mode the user can hit has a stable exit code and a written artifact:

| Exit | Meaning                        | Artifact                                   |
| ---- | ------------------------------ | ------------------------------------------ |
| 0    | Success                        | `plan.json`, `index.html`, optional MP4    |
| 1    | Usage / config error           | stderr only                                |
| 2    | Lint failed after repair       | `.framecraft/lint-report.json`             |
| 3    | Render failed                  | `.framecraft/render-stderr.log`            |
| 4    | LLM / provider error           | `.framecraft/director-trace.json` if reached |

### 7.5 Security / secrets

- API keys (`GEMINI_API_KEY` / `GOOGLE_API_KEY` for Gemini, `ANTHROPIC_API_KEY` for Claude) read from env only; never logged, never written into any trace file. Trace files record *hashes* of the system prompt, not the key. `framecraft doctor` reports key presence without values.
- Generated HTML is static — no `Date.now()`, no `fetch`, no network calls. This is a Hyperframes rule (see `/kinetic-type/CLAUDE.md`, "Only deterministic logic — no `Date.now()`, no `Math.random()`, no network fetches") and FrameCraft enforces it at template-authoring time.

### 7.6 Risks and mitigations

| Risk                                                                     | Mitigation                                                                                                         |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| Director hallucinates block IDs or prop names                            | Pydantic validation of `SceneGraph` against registry; invalid plan → one retry with the validation error appended  |
| Director under/over-shoots total duration                                | Cross-field validator on `SceneGraph` enforces `sum(durations) ≈ total`; Director is re-prompted with the delta    |
| Assembler produces HTML that fails lint                                  | One automatic repair pass with the failing lint JSON; abort cleanly if still failing                                |
| Upstream Hyperframes CLI changes break scaffolding                       | `framecraft doctor` checks pinned version floor; scaffolder diffs `hyperframes init` output against expected files |
| LLM non-determinism breaks golden tests                                  | Stub provider for tests (per-provider fixtures); real LLM calls only in `@pytest.mark.llm` tests skipped by default |
| User runs out of provider credits / rate limits mid-project              | Graceful exit 4 with the trace so far; `from-plan` still works from `plan.json` without any LLM. Switching providers is one env var, so a blown Gemini quota doesn't block a run if an Anthropic key is configured |
| Brittle HTML stitching as block count grows                              | Each block is a self-contained Jinja template + GSAP timeline; root `index.html` wiring is code-generated, not authored |

---

## 8. Success Metrics

- **M1. Time-to-first-render.** A new user installs FrameCraft, runs one `framecraft "..."` command, and gets an MP4 in under 2 minutes on a laptop. Target: 90% of first runs.
- **M2. Lint pass rate.** ≥ 85% of `compose` runs pass lint on the first attempt; ≥ 98% after the one repair pass. Measured from trace telemetry on the author's own projects over 50 runs.
- **M3. Edit-without-reprompt.** At least half of real-world re-runs are `from-plan`, not `compose`. This is the signal that `plan.json` is useful.
- **M4. Cost.** Median per-run spend ≤ $0.03 on the default provider (Gemini 2.5 Pro + Flash, caching on) and ≤ $0.08 on the Anthropic provider. Measured from `director-trace.json` token counts × published per-token rates.
- **M5. Determinism.** Golden tests remain byte-stable across CI runs; zero flakes in a week.

---

## 9. Milestones

- **M0 — Walking skeleton (1–2 days).** US-001, US-002 (minimal), US-013 (stub only). `framecraft compose --dry-run` emits a hand-written `plan.json` and runs Assembler with one hard-coded block.
- **M1 — MVP (1 week).** US-003 (8 blocks), US-004, US-005 (pure-template path), US-006, US-007, US-008. End-to-end happy path for one archetype (product promo) with no branding.
- **M2 — All archetypes + branding (3–4 days).** Remaining blocks, US-010, archetype coverage for all five. Director classifies reliably across the three golden fixtures.
- **M3 — Polish (2–3 days).** US-009 (`from-plan`), US-011 (`render`), US-012 (traces), US-014 (goldens). Docs and examples.
- **Stretch — beyond v1.** TTS, captions, stock assets, multi-language. Each a separate PRD.

---

## 10. Open Questions

1. **Copy polish scope.** Should the Assembler invoke the LLM for *every* copy field, or only the ones templates mark `llm_polish=True`? Leaning toward opt-in per field to keep cost predictable — but some templates (`end-card`, `title-card`) probably benefit from always-on polish. Decide during M1. Gemini 2.5 Flash makes always-on cheaper than it was under Sonnet pricing; revisit the default with Gemini-priced token counts.
2. **Aspect-ratio pivot.** If the user says `--aspect 9:16` but the Director picks a block that only ships 16:9 (`app-showcase` today), do we (a) auto-swap to a 9:16-safe block, (b) warn and proceed, or (c) hard-fail? Leaning (a). Needs a `fallback_block_id` field on `BlockSpec`. See §6.8 — pixel dimensions now live on the `Aspect` enum, so the swap has a clean place to hook.
3. **Transition semantics in `SceneGraph`.** ~~Are transitions a special `Scene` with `category=TRANSITION` and a fixed 0.5–1.5s duration overlap with adjacent scenes, or a separate `transitions: list[TransitionCue]` field with explicit `from_scene` / `to_scene`?~~ **Resolved (2026-04-18):** `SceneGraph.transitions: list[TransitionCue]`, each with `from_scene: int`, `to_scene: int`, `block_id: TransitionId`, `overlap: float` (0.3–1.5s). Scenes keep integer, non-overlapping track placement; the Assembler computes absolute `data-start` on every scene placeholder from `sum(scene[i].duration) - sum(transitions.overlap up to i)` and emits the transition as its own `compositions/transitions/t-NN-<id>.html` sub-composition on a higher `data-track-index`. Absolute timing matches the existing convention in `aia-health-shield-gold-max/index.html`; relative syntax (`data-start="<id> + 2"`) is allowed by the schema but not used by the default code path — simpler to diff, simpler to reason about.
4. **Where does `framecraft.yaml` belong?** Project-local (next to `plan.json`) so defaults travel with the project, or user-global (`~/.config/framecraft/`) for brand defaults? Probably both, with project-local winning.
5. **Offline determinism for CI.** Is a VCR-style recorded-response stub sufficient, or do we also need a fully offline "template-only" Director that short-circuits with a hand-written plan for a known situation? Second is belt-and-suspenders but zero-network.
6. **Multi-composition projects.** Today a run produces one project. A user producing a social campaign may want `framecraft` to generate 1:1, 9:16, and 16:9 variants from the same situation in a single call. v1 or post-v1? Leaning post-v1 — add `--variants 16:9,9:16,1:1` later without schema churn.

---

*End of PRD.*
