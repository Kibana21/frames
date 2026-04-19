# FrameCraft

Turn a one-line situation into a polished [Hyperframes](https://hyperframes.dev) animation project — and optionally render it straight to MP4.

```
situation ──► Director (LLM) ──► SceneGraph ──► Assembler ──► HTML ──► Render
```

---

## Prerequisites

| Tool | Why |
|------|-----|
| Python 3.11 / 3.12 | Runtime |
| Node.js 18+ & `npx` | Hyperframes CLI |
| `ffmpeg` | MP4 rendering |
| Google Gemini or Anthropic API key | LLM planning (not needed for `--dry-run`) |

Install the Hyperframes CLI once:

```bash
npx hyperframes --version   # auto-installs on first run
```

---

## Installation

```bash
cd framecraft
pip install -e .
framecraft doctor            # verify toolchain
```

---

## Authentication

FrameCraft supports three auth strategies, tried in this order:

### 1. Google Service Account (recommended for Vertex AI)

Place your `key.json` in the `framecraft/` project directory (already in `.gitignore`):

```
framecraft/
└── key.json          ← Google service account JSON
```

FrameCraft auto-discovers it. No env var needed.

### 2. Environment variable

```bash
export GEMINI_API_KEY=your-key-here          # Gemini Developer API
# or
export ANTHROPIC_API_KEY=your-key-here       # Anthropic Claude
```

### 3. `framecraft.yaml` / `--provider` flag

```bash
framecraft compose "..." --provider gemini
framecraft compose "..." --provider anthropic
```

---

## Quick Start

### Full pipeline (LLM → HTML → MP4)

```bash
framecraft compose "30-second promo for ShieldMax, an AI health insurance app" \
  --duration 30 \
  --aspect 16:9 \
  --render
```

Output lands in `output/<slug>/` inside the workspace root.

### Dry-run (no LLM, instant)

```bash
framecraft compose "barista discovers the coffee shop is the last on Earth" \
  --dry-run \
  --duration 10
```

### Preview in browser

```bash
framecraft preview --out output/barista-discovers-the-coffee-shop
```

### Render existing project to MP4

```bash
framecraft render --out output/barista-discovers-the-coffee-shop
```

---

## Output Structure

```
output/<slug>/
├── index.html            ← root Hyperframes composition
├── plan.json             ← scene graph (LLM output)
├── hyperframes.json      ← Hyperframes project config
├── meta.json             ← project metadata
├── compositions/
│   ├── scene-00-title-card.html
│   ├── scene-01-...html
│   └── ...
└── .framecraft/          ← trace logs (gitignored)
    ├── director.json
    ├── scene-00.json
    └── ...
```

---

## Commands

### `compose` — full pipeline

```
framecraft compose SITUATION [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--out`, `-o` | `output/<slug>/` | Output directory |
| `--aspect` | `16:9` | Canvas aspect (`16:9`, `9:16`, `1:1`) |
| `--duration` | `20.0` | Total duration in seconds |
| `--fps` | `30` | Frames per second |
| `--mood` | auto | `cinematic`, `playful`, `serious`, `technical`, `warm` |
| `--archetype` | `auto` | Scene archetype (see `framecraft catalog`) |
| `--logo` | — | Path to logo image |
| `--palette` | — | Colour palette name or hex pair |
| `--font` | — | Headline font name |
| `--music` | — | Audio bed (mp3/wav/m4a) |
| `--music-volume` | `0.4` | Audio bed volume (0.0–1.0) |
| `--render` | off | Render to MP4 after assembly |
| `--open` | off | Open browser after render |
| `--provider` | from config | `gemini`, `anthropic`, or `stub` |
| `--dry-run` | off | Skip LLM; use a hand-written 2-scene plan |
| `--summary` | off | Print token/cost/lint summary |
| `--no-config` | off | Ignore `framecraft.yaml` |

### `from-plan` — re-assemble from existing plan

```bash
framecraft from-plan output/my-project/plan.json --render
```

Useful for tweaking a plan and re-rendering without a new LLM call.

### `render` — render to video

```bash
framecraft render --out output/my-project --format mp4 --quality standard
```

| Option | Default | Values |
|--------|---------|--------|
| `--format` | `mp4` | `mp4`, `webm`, `mov` |
| `--quality` | `standard` | `draft`, `standard`, `high` |
| `--fps` | from project | Integer override |

### `preview` — live preview server

```bash
framecraft preview --out output/my-project --port 4000
```

Press `Ctrl-C` to stop. Pass `--no-open` to suppress automatic browser launch.

### `catalog` — list available scene blocks

```bash
framecraft catalog            # human-readable table
framecraft catalog --json     # JSON output
```

### `doctor` — environment check

```bash
framecraft doctor
```

Verifies Node/npx, ffmpeg, hyperframes CLI, and API key presence (never prints values).

---

## Configuration: `framecraft.yaml`

Place in the workspace root to set project-wide defaults:

```yaml
brand:
  logo: assets/logo.png
  palette: "#09090C/#FFFFFF"
  font: Inter

defaults:
  provider: gemini
  mood: cinematic
```

CLI flags always override config values.

---

## Development

```bash
pip install -e ".[dev]"
pytest                        # unit + integration tests
pytest -m llm                 # tests that call the real LLM (need API key)
pytest --update-goldens       # refresh golden HTML snapshots
```

Run the linter:

```bash
ruff check src tests
mypy src
```
