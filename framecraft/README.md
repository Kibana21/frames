# FrameCraft

Turn a one-line situation into a **fully-authored, brand-aware motion-graphics video** — end to end, no designer required. FrameCraft plans the film as a narrative, writes the HTML/CSS/GSAP for every scene using an LLM, self-validates against a dozen runtime-safety + aesthetic rules, and hands the result to [Hyperframes](https://hyperframes.dev) to render as MP4.

```
  situation
     │
     ▼
 ┌─────────────────────┐   thesis + pillars + scenes + exhibits + motif
 │  Story Bible (LLM)  │── narrative blueprint for the whole film
 └──────────┬──────────┘
            ▼
 ┌─────────────────────┐   block_id + duration + copy-seeded props per scene
 │ SceneGraph (Director │── OR deterministically derived from the bible
 │   OR bible-derived) │
 └──────────┬──────────┘
            ▼
 ┌─────────────────────┐   for each scene:
 │  Per-scene Author   │    Pass 1 — design brief (20+ elements, exhibits, signature moment)
 │  (3-pass LLM)       │    Pass 2 — HTML/CSS/GSAP  (+ one-shot retry on validation fail)
 │                     │    Pass 3 — elevation pass (adds 2+ elements + new technique)
 └──────────┬──────────┘
            ▼
 ┌─────────────────────┐   strict regex + structural checks:
 │  Validator gate     │    - no `repeat:-1`, `Date.now`, `Math.random`, `fetch`, rAF,
 │                     │      `setTimeout`, `setInterval`
 │                     │    - no emoji fonts, no template-literal selectors
 │                     │    - no overlapping `.clip` on same `data-track-index`
 │                     │    - no `.clip { … }` CSS rules (clobbers layout)
 │                     │    - no long text inside SVG `<text>` or `ribbon-text`/
 │                     │      `shield-label`/`chip-text` (silhouette-clipping trap)
 │                     │    - no `exit-panel`/`cover-panel` full-bleed wipes
 └──────────┬──────────┘
            ▼
 ┌─────────────────────┐   on validation-fail after retry:
 │  Rich-safe fallback │    render a deterministic, bible-fed HTML scene
 │  (if bible present) │    (atmospheric bg + eyebrow + headline + bullets + fade exit)
 └──────────┬──────────┘
            ▼
 ┌─────────────────────┐   npx hyperframes render → MP4
 │  Hyperframes render │
 └─────────────────────┘
```

---

## Prerequisites

| Tool | Why |
|------|-----|
| Python 3.11 / 3.12 | Runtime |
| Node.js 18+ & `npx` | Hyperframes CLI |
| `ffmpeg` | MP4 encoding |
| `poppler` *(optional)* | PDF brand-brochure ingestion for `--style-seed` research |
| Google Gemini (Vertex AI) **or** Anthropic Claude API key | LLM planning + authoring |

Install the Hyperframes CLI once — it auto-installs on first run:

```bash
npx hyperframes --version
```

---

## Installation

Pick whichever you prefer:

### (a) Editable install from `pyproject.toml`

```bash
cd framecraft
pip install -e .
framecraft doctor            # verify toolchain
```

### (b) Classic `requirements.txt`

```bash
cd framecraft
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .             # register the `framecraft` CLI
framecraft doctor
```

Dev extras (pytest / ruff / mypy):

```bash
pip install -r requirements-dev.txt
# or
pip install -e ".[dev]"
```

---

## Authentication

FrameCraft tries these in order and uses the first match:

### 1. Google service account (recommended for Vertex AI / Gemini)

Drop the service-account JSON at the project root:

```
framecraft/
└── key.json          ← Google service account (gitignored)
```

Auto-discovered. No env var needed. Uses Vertex AI via `google-genai`.

### 2. Environment variable

```bash
export GEMINI_API_KEY=your-key-here        # Gemini Developer API
# OR
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
# OR
export ANTHROPIC_API_KEY=your-key-here     # Anthropic Claude
```

### 3. `--provider` flag

```bash
framecraft compose "..." --provider gemini
framecraft compose "..." --provider anthropic
```

---

## Quick start

### Full cinematic pipeline (Story Bible + 3-pass author + render)

```bash
framecraft compose "30-second AIA Health Shield Gold Max brand film — walk viewers through private hospital coverage, zero co-insurance, S\$2M annual limit, 13-month pre/post cover, and lifetime protection" \
  --duration 30 \
  --aspect 16:9 \
  --full-polish \
  --style-seed "AIA premium-editorial — red #D31145 primary, cream #FAF4EE bg, editorial magazine layout, stat grids, comparison tables, first-aid motifs" \
  --render
```

Output lands in `output/<slug>/`.

### Multiple renditions of the same situation

```bash
framecraft compose "launch of a privacy-first browser" \
  --full-polish \
  --n-variants 3 \
  --render
```

Produces `output/<slug>/v1/`, `v2/`, `v3/` — each with a different style-seed from the built-in palette.

### Dry-run (no LLM calls, instant)

```bash
framecraft compose "barista discovers the coffee shop is the last on Earth" --dry-run --duration 10
```

### Preview the HTML in a live browser

```bash
framecraft preview --out output/<slug>
```

### Re-render an existing project without re-authoring

```bash
framecraft render --out output/<slug>
```

---

## What each pipeline stage does

| Stage | Module | What it produces |
|-------|--------|------------------|
| Story Bible | `story_bible.py` | `.framecraft/story-bible.json` — thesis, 2–7 pillars (tier-weighted), exhibits (tables, charts, stat-grids, timelines, checklists), shield-motif arc, per-scene copy ledger with char budgets |
| Scene graph | `director.py` **or** `story_bible.bible_to_plan()` | `plan.json` — flat list of scenes with block_ids and durations derived from the bible |
| Per-scene LLM author | `rendering/llm_author.py` | `compositions/scene-NN-*.html` — bespoke HTML with 20+ animated elements, 25+ GSAP tweens, nested sub-timelines, a signature moment, and a declared exit motif matching the next scene's entry |
| Validator | `rendering/llm_author._validate()` | Blocks a run when the authored HTML violates any of ~14 safety / aesthetic rules — triggers one-shot retry, then rich-safe fallback |
| Rich-safe fallback | `rendering/rich_fallback.py` | Deterministic HTML built straight from the bible's copy ledger + AIA palette when LLM authoring fails validation twice |
| Assembler | `assembler.py` | Writes all scene files, plan.json, hyperframes.json, scaffold |
| Lint + repair | `lint.py`, `lint_policy.py` | Runs `npx hyperframes lint`; auto-repairs LLM_REPAIRABLE errors with a second authoring pass |
| Render | `cli_render.py` → `npx hyperframes render` | MP4 in `renders/` |

---

## Output structure

```
output/<slug>/
├── index.html                 ← root Hyperframes composition
├── plan.json                  ← SceneGraph (durations, block_ids, copy-seeded props)
├── hyperframes.json           ← Hyperframes project config
├── meta.json                  ← project metadata
├── compositions/
│   ├── scene-00-title-card.html
│   ├── scene-01-lower-third.html
│   └── …
├── renders/
│   └── <slug>_<timestamp>.mp4
└── .framecraft/               ← traces (gitignored)
    ├── story-bible.json       ← the narrative blueprint for the whole film
    ├── director-trace.json    ← LLM planning trace
    └── assembler-traces/
        └── scene-00.json      ← per-scene token/cost/timing
```

---

## Command reference

### `compose` — end-to-end pipeline

```
framecraft compose SITUATION [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--out`, `-o` | `output/<slug>/` | Output directory |
| `--aspect` | `16:9` | `16:9`, `9:16`, `1:1` |
| `--duration` | `20.0` | Total seconds |
| `--fps` | `30` | Frames per second |
| `--mood` | auto | `cinematic`, `playful`, `serious`, `technical`, `warm` |
| `--archetype` | `auto` | Scene archetype (see `framecraft catalog`) |
| `--logo` | — | Path to logo image |
| `--palette` | — | Palette name or `"#HEX/#HEX"` |
| `--font` | — | Headline font |
| `--music` | — | Audio bed (mp3/wav/m4a) |
| `--music-volume` | `0.4` | 0.0–1.0 |
| `--render` | off | Render MP4 after assembly |
| `--open` | off | Open browser after render |
| `--provider` | from config | `gemini`, `anthropic`, or `stub` |
| `--dry-run` | off | Skip all LLM calls — hand-written 2-scene plan |
| `--summary` | off | Print token / cost / lint summary |
| `--no-config` | off | Ignore `framecraft.yaml` |
| **`--full-polish`** | off | Enable Story Bible + 3-pass LLM authoring for every scene (richer motion, ~$0.05–0.15 per run) |
| **`--n-variants N`** | `1` | Produce N renditions with different style seeds |
| **`--style-seed STR`** | — | Free-form creative directive (palette, tone, visual motifs) threaded into every LLM prompt. If it contains "AIA", the AIA brand system (palette + facts + tagline) is injected automatically. |

### `from-plan` — re-assemble from an existing `plan.json`

```bash
framecraft from-plan output/my-project/plan.json --render
```

Skips Director + Story Bible — useful for tweaking a plan and re-rendering without new LLM spend.

### `render` — render to video

```bash
framecraft render --out output/my-project --format mp4 --quality standard
```

| Option | Default | Values |
|--------|---------|--------|
| `--format` | `mp4` | `mp4`, `webm`, `mov` |
| `--quality` | `standard` | `draft`, `standard`, `high` |
| `--fps` | inherit | Integer override |

### `preview` — live preview

```bash
framecraft preview --out output/my-project --port 4000
```

Ctrl-C to stop. `--no-open` suppresses browser launch.

### `catalog` — list available scene blocks

```bash
framecraft catalog           # rich table
framecraft catalog --json    # machine-readable
```

### `doctor` — environment check

```bash
framecraft doctor
```

Checks Node/npx, ffmpeg, hyperframes CLI, API-key presence (never prints values).

---

## Story Bible — the narrative blueprint

When `--full-polish` is on, the bible pass runs first and produces a structured plan for the **entire film** (not just per-scene). It captures:

- **Thesis** — the single sentence a viewer must remember
- **Pillars** (2–7) — key messages with a `tier` (`hero` / `support` / `context`), a `weight` (1–5), and an `anchor` flag for the scene that carries the film's signature moment
- **Scenes** (2–8) — each with a `role` (`hook` / `hero-beat` / `evidence` / `proof-point` / `resolution` / `brand-lockup` / `transition`), `duration_s`, `tone`, a full copy ledger (every text element with a `max_chars` budget), and `entry_motif` + `exit_motif` seams for matching across cuts
- **Exhibits** — data objects rendered structurally: `comparison_table`, `line_curve`, `bar_chart`, `stat_grid`, `checklist`, `timeline`
- **Motif arc** — a recurring visual element (shield, logo, icon) whose state progresses across scenes

The bible is validated with pydantic before any HTML is written — orphan hero pillars, duration mismatches, char-budget overflows all fail loudly with a one-shot retry.

---

## Built-in brand system: AIA

The author prompts auto-detect the keyword `"aia"` in the style seed or situation and inject a full brand system with:

- Real palette (primary red `#D31145`, rose pink `#F5DDE0`, cream `#FAF4EE`, orange `#E86C29`, dark ink, beige tape accents)
- Typography voice (bold sans-serif display, ALL-CAPS eyebrow, Inter fallback)
- Brand motifs (first-aid cross, shield outline, heart+cross, ribbon badges, washi-tape cards)
- Brand tagline: `"Healthier, Longer, Better Lives."`
- Verified product facts (S$2M limit, 13-month pre/post, 380+ AQHP specialists, etc.)

Other brands can be added as entries in `rendering/llm_author.py::_BRAND_SYSTEMS`.

---

## Configuration: `framecraft.yaml`

Place in the workspace root to set project-wide defaults:

```yaml
brand:
  logo: assets/logo.png
  palette: "#D31145/#FAF4EE"
  font: Inter

defaults:
  provider: gemini
  mood: cinematic
```

CLI flags always override config.

---

## Development

```bash
pip install -e ".[dev]"

pytest                                 # unit + integration
pytest -m llm                          # real-provider tests (need API key)
pytest --update-goldens                # refresh golden snapshots
pytest --update-fixtures               # refresh recorded LLM fixtures

ruff check src tests
mypy src

python scripts/record_fixture.py       # record a Gemini/Anthropic response as a StubProvider fixture
python scripts/perf_harness.py         # p50/p95 compose timings + regression check
```

---

## Troubleshooting

| Symptom | Likely cause & fix |
|---|---|
| `error: No Gemini credentials found` | Run from a dir whose walk-up tree contains `key.json`, or `export GEMINI_API_KEY=…` |
| Hyperframes render fails with warning about `requestAnimationFrame` | Informational only — the MP4 is usually produced. Running `npx hyperframes render` directly sidesteps the framecraft wrapper's strict exit-code handling. |
| Scenes look blank (no content visible) | Pre-v10 bug: the LLM added `[data-composition-id="…"] .clip { position: absolute; }` which collapsed every element to 0×0. The validator now rejects this. |
| Text inside a shield/ribbon gets clipped | Pre-v10 bug: long phrase placed inside an SVG `<text>` or `<div class="ribbon-text">`. Validator now catches both and forces retry / fallback. |
| White flash between scenes | Pre-v10 bug: full-bleed `exit-panel` wipe. Validator now rejects the class names. |
| `StoryBible: bible validation failed after retry` | LLM emitted an orphan hero pillar or exceeded a char budget by more than 15%. Retry the compose — bibles are non-deterministic. |

---

## Credits

FrameCraft is by Kartik. Renders via [Hyperframes](https://hyperframes.dev). LLM planning + authoring via [Google Gemini](https://ai.google.dev) and [Anthropic Claude](https://www.anthropic.com).
