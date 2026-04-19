# 07 — Observability + Golden Tests + Performance Harness

## Goal

Three closely-related concerns that all *observe* the system rather than implement new features: (a) aggregate and assert on traces emitted by 03/04/05/06b (US-012), (b) author and maintain golden tests across both providers (US-014), and (c) a performance harness that enforces §7.3 targets. This plan also owns the FR-11 "always-write" guarantee at the CLI boundary and the `OQ-5` resolution owner.

## Inputs

- PRD US-012, US-014; §7.3 perf; §7.5 security sentence; §10 OQ-5.
- Trace shapes emitted by 03 (`director-trace.json`), 04 (`assembler-traces/scene-NN.json`), 05 (`lint-report.json`), 06b (`render-stderr.log`).
- Stub provider fixtures from 02.

## Outputs

- `framecraft/trace.py` (already started in 03) — extended with aggregator and schema versioning.
- Golden fixtures under `tests/goldens/{narrative,product-promo,data-explainer}/`.
- LLM fixture recorder `scripts/record_fixture.py`.
- `scripts/perf_harness.py` — measures §7.3 targets, writes a JSON report.
- CI workflow for weekly primer drift + monthly perf regression check.

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/trace.py` | Trace model definitions + writer + aggregator |
| `framecraft/observability.py` | Helpers for always-write (FR-11) and log redaction (§7.5) |
| `tests/goldens/<situation>/situation.txt` | The input string for a fixture |
| `tests/goldens/<situation>/plan.json` | Golden SceneGraph |
| `tests/goldens/<situation>/expected/index.html` | Golden root composition |
| `tests/goldens/<situation>/expected/compositions/*.html` | Golden per-scene files |
| `tests/fixtures/llm/{gemini,anthropic}/<fixture_hash>.json` | Recorded provider responses |
| `tests/test_goldens.py` | Golden test runner |
| `tests/conftest.py` | `--update-goldens` flag, `stub_provider` fixture |
| `scripts/record_fixture.py` | Record real provider response into stub fixture |
| `scripts/perf_harness.py` | Timing + token-cost measurements |
| `.github/workflows/primer-drift.yml` | Weekly drift check (calls 08) |
| `.github/workflows/perf-regression.yml` | Monthly perf check |

## Dependencies

- 02 (stub format + fixture layout), 03 (trace schema), 04 (assembler traces), 05 (lint report), 06a (compose orchestration — testing surface), 06b (render log), 08 (primer for drift CI).

## Implementation steps

### Trace aggregation

1. **Schema versioning.**
   - Every trace file has `version: int` as the first key. Aggregator rejects unknown versions with an actionable error ("regenerate traces with `framecraft --refresh`").
   - Current version: `1` across all trace types.

2. **Aggregator (`framecraft/trace.py`).**
   ```python
   class RunSummary(BaseModel):
       director: DirectorTrace | None
       assembler_scenes: list[AssemblerSceneTrace]
       lint: LintReport | None
       render: RenderLog | None
       total_elapsed_ms: int
       total_input_tokens: int
       total_output_tokens: int
       total_cache_read_tokens: int
       estimated_cost_usd: float     # computed from token counts × published rates per model

   def summarize(out_dir: Path) -> RunSummary: ...
   ```
   - `summarize` walks `out_dir / ".framecraft"` and parses each trace. Missing files → fields are `None`.
   - Published rates live in a small table `framecraft/trace_rates.py` keyed by `(provider, model)`; update on price changes.

3. **`--summary` flag on `compose`.** Prints a 6-line summary at the end:
   ```
   Archetype: product_promo
   Scenes: 5 (4 polished)
   Tokens: 12,340 in / 890 out / 10,200 cached
   Est. cost: $0.018
   Lint: pass (0 repair passes)
   Render: 00:01:23
   ```
   - Default: off. Enabled automatically when `stdout.isatty()`.

### Always-write guarantee (FR-11)

4. **`observability.always_write`.** Context manager used by each emitter:
   ```python
   @contextmanager
   def always_write(trace_path: Path, initial: BaseModel):
       tmp = trace_path.with_suffix(".tmp")
       tmp.parent.mkdir(parents=True, exist_ok=True)
       tmp.write_text(initial.model_dump_json(indent=2))
       tmp.replace(trace_path)  # atomic initial write
       holder = {"trace": initial}
       try:
           yield holder
       finally:
           tmp2 = trace_path.with_suffix(".tmp2")
           tmp2.write_text(holder["trace"].model_dump_json(indent=2))
           tmp2.replace(trace_path)
   ```
   - Initial write happens *before* any risky work; failure still leaves a parseable trace with `outcome=unknown`.
   - Final write reflects the completed state.

5. **Security sentence (§7.5).** Trace writers hash any `system` or `messages[*].content` that might contain secrets. `observability.hash_for_trace(s: str) -> str` returns `sha256(s).hexdigest()`; traces store hashes in dedicated `*_sha256` fields, never raw prompts. Cross-links to 02 for the same rule.

### Golden tests (US-014)

6. **Three reference situations.**
   - `narrative`: `"a barista discovers the coffee shop is the last on Earth"`.
   - `product-promo`: `"30-second promo for an AI health insurance app called ShieldMax"`.
   - `data-explainer`: `"15-second explainer of how our energy usage dropped 40% after the switch to solar"`.

7. **Fixture layout.**
   ```
   tests/goldens/<situation>/
     situation.txt                  # 1 line, the input
     plan.json                      # golden SceneGraph
     expected/
       index.html
       compositions/*.html
       meta.json
   tests/fixtures/llm/gemini/<hash>.json
   tests/fixtures/llm/anthropic/<hash>.json
   ```

8. **Golden runner (`tests/test_goldens.py`).**
   ```python
   @pytest.mark.parametrize("situation", ["narrative", "product-promo", "data-explainer"])
   @pytest.mark.parametrize("provider", ["stub:gemini", "stub:anthropic"])
   def test_golden(situation, provider, tmp_path, update_goldens):
       brief_text = (GOLDENS / situation / "situation.txt").read_text().strip()
       # Compose into tmp_path.
       cli_runner.invoke(app, ["compose", brief_text, "--out", str(tmp_path), "--provider", provider, "--no-config"])
       # Compare plan.json and HTML tree byte-for-byte.
       _assert_tree_equal(GOLDENS / situation / "expected", tmp_path, update_goldens=update_goldens)
   ```
   - **Key invariant:** goldens are provider-agnostic. If the recorded Gemini and Anthropic responses parse to the same `SceneGraph`, the HTML must match byte-for-byte. This is the cleanest way to verify determinism.
   - Response records are independent *inputs*; when you re-record, both providers get new fixture hashes but if their parsed `SceneGraph` is identical, the golden HTML is unchanged.

9. **`--update-goldens` flag (`tests/conftest.py`).**
   ```python
   def pytest_addoption(parser):
       parser.addoption("--update-goldens", action="store_true")
       parser.addoption("--update-fixtures", action="store_true")
   ```
   - `--update-goldens`: on assertion failure, write the actual tree as the new golden, print a diff, still fail the test so CI never silently accepts. Rerun with `--update-goldens` in a clean branch to commit.
   - `--update-fixtures`: regenerate stub fixtures by hitting real providers (requires keys, guarded by `@pytest.mark.llm`).

10. **`scripts/record_fixture.py`.**
    ```
    python scripts/record_fixture.py --provider gemini --situation narrative
    # → calls Director with real provider, records responses to tests/fixtures/llm/gemini/
    ```
    - Requires API keys. Tagged with `@pytest.mark.llm` (or runs standalone from CLI).

11. **Diff on failure.** When a golden test fails, print:
    - File path of the first diverging file.
    - Unified diff (first 40 lines) via `difflib.unified_diff`.
    - Size delta if HTML trees differ in shape.

### Performance harness (§7.3)

12. **`scripts/perf_harness.py`.**
    - Runs `compose` against the three golden situations, each 10 times, with stub provider (to isolate Python-side cost from network).
    - Records per-phase timings: Director, Assembler (per-scene and total), Lint.
    - Writes JSON report: `.perf/report-<timestamp>.json`.
    - Compares against baseline in `.perf/baseline.json`; fails CI if p50 regresses >20% on any phase.
    - Targets (from PRD §7.3): Director ≤8s p50 ≤20s p95; Assembler ≤1.5s per polish scene ≤50ms pure template; total ≤30s for 4-scene 20s promo before render.
    - Real-provider perf runs are manual (require keys); CI uses stub.

### CI workflows

13. **`.github/workflows/primer-drift.yml`.** Weekly: runs `python scripts/snapshot_primer.py --check`; on drift, opens an issue with `--diff` output pasted into body.

14. **`.github/workflows/perf-regression.yml`.** Monthly: runs `perf_harness.py`; compares to baseline; on regression, opens an issue with the report attached.

15. **`.github/workflows/test.yml`.** On every PR:
    - `pytest` — no LLM markers (stub only).
    - `mypy --strict src/framecraft`.
    - `ruff check`.
    - Golden tests included by default (fast, stub-driven).

### OQ-5 resolution

16. **Proposed resolution.** Stub-backed goldens are sufficient. Revisit only if:
    - Fixtures go flaky (rare — stub is deterministic by definition).
    - We need to test Director behavior under no provider at all (hand-written-plan Director). This would be a new subsystem plan, not a v1 concern.

## Acceptance (PRD bullets closed)

- US-012 (all), US-014 (all).
- FR-11 (trace files always written).
- §7.3 (perf harness asserts targets).
- §7.5 (trace redaction + cross-link to 02 for keys).

## Open questions

- **OQ-5 (PRD) — proposed resolution** (awaiting confirmation): stub is sufficient; revisit only on flake or new Director-less code paths.
- **OQ-F7.1** Should we emit an OpenTelemetry span per phase in addition to JSON trace files? Useful if FrameCraft ever runs inside a larger pipeline. *Leaning: post-v1. For now, JSON-on-disk is enough.*
- **OQ-F7.2** Should cost estimation include cache-write tokens (Anthropic's creation tokens cost 25% more)? *Leaning: yes — update `trace_rates.py` to encode both read and write multipliers per provider.*

## Verification

```bash
# Golden tests:
pytest tests/test_goldens.py -v
pytest tests/test_goldens.py -v --update-goldens   # revisit manually in a clean branch

# Aggregated summary for a run:
framecraft compose "..." --out /tmp/fc-demo --summary

python -c "
from pathlib import Path
from framecraft.trace import summarize
print(summarize(Path('/tmp/fc-demo')).model_dump_json(indent=2))
"

# Perf harness:
python scripts/perf_harness.py
cat .perf/report-*.json | jq '.phases | .[] | {name, p50_ms, p95_ms}'

# Fixture re-record (requires keys):
pytest --update-fixtures -m llm -k director

# Drift CI (manual):
python scripts/snapshot_primer.py --check
```
