# 03 — Director (Situation → `SceneGraph`)

## Goal

Convert a `Brief` into a validated `SceneGraph` via a two-pass LLM call (classify archetype, then plan scenes from the allowed block subset). Emit a structured trace. Retry once on validation failure. Apply OQ-2 fallback swap when the Director picks a block whose `aspect_preferred` excludes the plan's aspect.

## Inputs

- PRD US-004; §6.3 archetypes; §10 OQ-1 (polish scope), OQ-2 (aspect swap).
- `Brief`, `SceneGraph`, `REGISTRY`, `BlockRegistry` from [`01-schema-and-registry.md`](./01-schema-and-registry.md).
- `LLMProvider` from [`02-providers.md`](./02-providers.md).
- `load_primer()` from [`08-primer-snapshot.md`](./08-primer-snapshot.md).

## Outputs

- `Director(provider: LLMProvider).plan(brief: Brief) -> SceneGraph`.
- `.framecraft/director-trace.json` with fixed schema (frozen in M1, consumed by 07).

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/director.py` | `Director` class, two-pass logic, fallback swap |
| `framecraft/prompts/common/director.md` | Shared Director prompt body |
| `framecraft/prompts/gemini/director.md` | Gemini wrapper |
| `framecraft/prompts/anthropic/director.md` | Anthropic wrapper |
| `framecraft/trace.py` | Shared trace writer used by 03/04/05 (owned here since 03 is first to emit) |
| `tests/test_director.py` | Unit tests with stub provider |
| `tests/fixtures/llm/{gemini,anthropic}/director_<archetype>.json` | Recorded fixtures for three archetypes |

## Dependencies

- 01 (types + registry), 02 (provider), 08 (primer).

## Implementation steps

1. **`Director` class.**
   ```python
   class Director:
       def __init__(self, provider: LLMProvider, registry: BlockRegistry):
           self.provider = provider
           self.registry = registry

       def plan(self, brief: Brief) -> SceneGraph: ...
   ```
   - Pure function of `(provider, registry, brief)`. No filesystem I/O except `trace.write`.

2. **Prompt assembly.**
   - System prompt = concatenation of:
     1. `load_primer()` (from 08).
     2. Archetype definitions block — the §6.3 table as Markdown, including **signal phrases** per archetype so the classifier has an explicit grounding.
     3. Block registry JSON: `json.dumps({id: spec.model_dump(exclude={"template"}) for id, spec in REGISTRY.items()})`.
     4. `SceneGraph` JSON schema: `json.dumps(SceneGraph.model_json_schema())`.
     5. The per-provider trailing instruction (from `prompts/{provider}/director.md`).
   - `cache_segments = [primer, archetypes_block, registry_json, schema_json]`. The trailing provider-specific instruction is **not** cached — it's small and may carry the user-message-specific retry error.

3. **Two-pass flow.**
   - **Pass A — classification.**
     ```
     user message: "Situation: <situation>\n\nClassify as one Archetype. Output JSON: {\"archetype\": <ENUM>, \"rationale\": \"...\"}."
     ```
     Schema: `{"type": "object", "properties": {"archetype": {"enum": [a.value for a in Archetype]}, "rationale": {"type": "string"}}, "required": ["archetype"]}`.
     `brief.archetype is not None` short-circuits Pass A (user-forced); still record in trace for auditability.
   - **Pass B — scene planning.**
     ```
     user message:
       "Situation: <situation>\n"
       "Archetype: <chosen>\n"
       "Duration target: <brief.duration>s, aspect <brief.aspect>, mood <brief.mood or 'unspecified'>\n"
       "BrandKit: <brand_kit dumped or 'none'>\n"
       "Allowed blocks: <registry.allowed_for(archetype)>\n"
       "Allowed transitions: <registry.transitions_allowed()>\n"
       "Emit a valid SceneGraph as JSON. Prefer blocks whose aspect_preferred contains <brief.aspect>."
     ```
     Schema: `SceneGraph.model_json_schema()`.

4. **Validation + one retry.**
   - Parse Pass B output: `SceneGraph.model_validate(response.parsed)`. `ValidationError` → build a retry user-message: `"The previous plan failed validation:\n<error>\n\nReturn a corrected SceneGraph JSON."` Append to conversation, re-call once.
   - Second failure → raise `DirectorError` mapped to exit code 4 by caller.
   - **Crucially:** the retry uses the same `cache_segments` so cache hits remain warm.

5. **OQ-2 fallback swap.**
   - After Pydantic passes, iterate each scene: if `brief.aspect not in REGISTRY[scene.block_id].aspect_preferred`:
     1. If `spec.fallback_block_id` is defined → swap the `block_id`, re-run `block_props` validation against the new spec's `required_props`. If props don't fit, go to (2).
     2. Else → one more LLM pass with `user message: "Scene <N> uses block <id> which doesn't support aspect <aspect>. Choose a different block from <allowed_for(archetype)> whose aspect_preferred includes <aspect>, keeping the copy intent."`. Apply and re-validate.
     3. Else → raise `DirectorError("No aspect-safe block for scene <N>")`, exit 4.
   - Swap path logs to trace under `aspect_swaps: list[{scene_index, from, to, reason}]`.

6. **Brief → Archetype short-circuit + `brief.archetype`.**
   - If user passed `--archetype auto` (default) → Pass A runs.
   - If user passed explicit archetype (e.g. `--archetype product_promo`) → skip Pass A, classification entry in trace records `{source: "user"}`.

7. **`OQ-1` polish scope.**
   - The Director does not itself polish copy — it marks scene fields via template defaults. Actual polishing is the Assembler's job (04).
   - Proposed resolution for OQ-1 (awaiting confirmation): *opt-in per field via `llm_polish=True` on block template; not configurable by Director.*

8. **Trace (`framecraft/trace.py`).**
   - `class DirectorTrace(BaseModel)` with fields:
     ```
     version: Literal[1] = 1
     brief_hash: str              # sha256 of brief.model_dump_json()
     system_prompt_sha256: str    # hash only
     cache_segments_sha256: list[str]
     provider: str
     director_model: str
     schema_hash: str             # from 01 SCHEMA_HASH
     pass_a: TracePass | None     # None if user-forced
     pass_b: TracePass
     retry: TracePass | None
     aspect_swaps: list[dict]
     elapsed_ms_total: int
     outcome: Literal["ok", "validation_failed", "provider_error"]
     error: str | None
     ```
     `TracePass` captures: `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `elapsed_ms`, `response_sha256`, `response_excerpt` (first 2 KB of response text).
   - Full response text lives in `assembler-traces/` if needed; `director-trace.json` is intentionally small.
   - `write(trace, out_dir: Path)` puts the file at `out_dir / ".framecraft" / "director-trace.json"`. Atomic write (tempfile + rename).

9. **Always-write guarantee (FR-11).**
   - `plan()` wraps its work in a try/finally that writes a partial trace even on exception. Trace `outcome="provider_error"` with `error=str(e)` for post-mortem.

10. **Archetype signal table embed (§6.3).**
    - The per-archetype "Signals" column from §6.3 is turned into structured bullets in `prompts/common/director.md`:
      ```
      NARRATIVE_SCENE — trigger phrases: characters, emotional beats, a twist, literary phrasing
      PRODUCT_PROMO — trigger phrases: product name, feature list, brand words, "X seconds"
      DATA_EXPLAINER — trigger phrases: numbers, "X went up by Y%", datasets, "explain"
      UI_WALKTHROUGH — trigger phrases: "app", "flow", "user goes through"
      SOCIAL_CARD — trigger phrases: "Instagram post", "tweet", "follow banner"
      ```
    - Keep this list in one place (common prompt) — not duplicated in docs.

## Testing strategy

- **Unit (`tests/test_director.py`).**
  - `test_classification_happy` — stub provider returns valid archetype; assert passed through.
  - `test_classification_invalid_enum` — stub returns `{"archetype": "MYTHIC"}` → retry fires with correction message.
  - `test_scenegraph_validation_retry` — stub first returns an invalid plan (sum of durations off); retry returns valid. Assert one retry.
  - `test_scenegraph_validation_double_fail` — both attempts invalid → `DirectorError`, trace written with `outcome="validation_failed"`.
  - `test_aspect_swap_with_fallback` — block A's aspect excludes 9:16 but has `fallback_block_id=B`; assert swap to B without a new LLM call.
  - `test_aspect_swap_without_fallback` — requires extra LLM call; stub returns a valid alternative.
  - `test_user_forced_archetype` — `brief.archetype=PRODUCT_PROMO` → Pass A skipped; trace records `source="user"`.
  - `test_always_writes_trace_on_exception` — force provider error; assert `.framecraft/director-trace.json` exists with `outcome="provider_error"`.
- **Fixture recording.** For three reference situations (narrative / product promo / data explainer), record Gemini and Anthropic responses via `scripts/record_fixture.py`. Stored under `tests/fixtures/llm/{provider}/director_<archetype>.json`. Used by 07 golden tests.

## Acceptance (PRD bullets closed)

- US-004: all AC bullets.
- FR-2 (archetype classification).
- FR-3 (block subset enforced — Pydantic + retry make hallucination impossible).
- FR-11 (director-trace always written — shared with 05's lint report).

## Open questions

- **OQ-1 (PRD) — proposed resolution** (awaiting confirmation): Assembler polish is opt-in per field via `llm_polish=True`; Director does not itself rewrite copy. Director may put "raw" copy into `block_props`; Assembler polishes only flagged fields.
- **OQ-2 (PRD) — proposed resolution** (awaiting confirmation): auto-swap via `fallback_block_id`, else one corrective LLM pass, else fail with exit 4.
- **OQ-F3.1** Should Pass A's archetype classification be implicit (folded into Pass B) rather than explicit? One call vs two. *Leaning: two — classification with a narrow schema is dramatically cheaper to retry than a full SceneGraph; keeping them split makes failures diagnosable.*
- **OQ-F3.2** Do we let `brief.duration` be a hint and allow Director to return `SceneGraph.duration` that differs by up to 10%? *Leaning: no for v1 — cross-field validator enforces ±0.1s against the Director's emitted total. Director's job is to hit the target.*

## Verification

```bash
# With stub provider (no key needed), given fixtures recorded:
FRAMECRAFT_PROVIDER=stub python - <<'PY'
from framecraft import Brief, Aspect
from framecraft.providers import make_provider
from framecraft.registry import REGISTRY, BlockRegistry
from framecraft.director import Director
d = Director(make_provider("stub"), BlockRegistry(REGISTRY, {}))
plan = d.plan(Brief(situation="a barista discovers the coffee shop is the last one on Earth", aspect=Aspect.AR_16_9, duration=20))
print(plan.archetype, len(plan.scenes), sum(s.duration for s in plan.scenes))
PY

cat .framecraft/director-trace.json | jq .outcome
# → "ok"

pytest tests/test_director.py -v
```
