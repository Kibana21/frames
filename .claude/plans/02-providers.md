# 02 — LLM Providers (Gemini + Anthropic + Stub)

## Goal

Implement a provider-agnostic `LLMProvider` protocol and three concrete implementations: **Gemini** (default, real), **Anthropic** (first-class alternate, real), **Stub** (deterministic test double). The rest of the system calls `provider.complete(...)` and never touches an SDK directly.

## Inputs

- PRD §3 US-013; §6.5 prompt caching; §7.1 stack; §7.5 security.
- `SceneGraph` and `SCHEMA_HASH` from [`01-schema-and-registry.md`](./01-schema-and-registry.md).
- Nothing from 03/04 yet — those are consumers.

## Outputs

- Importable: `LLMProvider` (protocol), `ProviderResponse` (dataclass), `make_provider()` factory.
- Three adapters: `framecraft.providers.gemini.GeminiProvider`, `framecraft.providers.anthropic.AnthropicProvider`, `framecraft.providers.stub.StubProvider`.
- Per-provider prompt wrappers under `framecraft/prompts/{gemini,anthropic}/`.

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/providers/base.py` | `LLMProvider` protocol + `ProviderResponse` + error types |
| `framecraft/providers/gemini.py` | Gemini adapter, default |
| `framecraft/providers/anthropic.py` | Anthropic adapter |
| `framecraft/providers/stub.py` | Fixture-backed adapter |
| `framecraft/providers/__init__.py` | `make_provider()` factory, selection logic |
| `framecraft/prompts/gemini/director.md` | JSON-output framing for Gemini Director |
| `framecraft/prompts/gemini/assembler.md` | Gemini Assembler polish framing |
| `framecraft/prompts/anthropic/director.md` | Anthropic Director framing |
| `framecraft/prompts/anthropic/assembler.md` | Anthropic Assembler polish framing |
| `~/.cache/framecraft/gemini-caches.json` | Runtime-managed Gemini cache index (not in repo) |
| `tests/fixtures/llm/gemini/*.json` | Recorded Gemini responses for stub |
| `tests/fixtures/llm/anthropic/*.json` | Recorded Anthropic responses for stub |
| `tests/test_providers.py` | Unit tests across all three providers |

## Dependencies

- 01 — only for `SCHEMA_HASH` (used in cache key) and `ProviderResponse` being exportable from a shared location. No runtime dependency on 01's rich types.

## Implementation steps

1. **`base.py` — protocol and types.**
   ```python
   class Message(TypedDict):
       role: Literal["user", "assistant"]
       content: str

   @dataclass(frozen=True)
   class ProviderResponse:
       text: str
       parsed: dict | None   # populated iff schema was passed
       input_tokens: int
       output_tokens: int
       cache_read_tokens: int
       cache_write_tokens: int
       provider: str
       model: str
       elapsed_ms: int

   class LLMProvider(Protocol):
       name: str
       def complete(
           self,
           messages: list[Message],
           *,
           system: str,
           schema: dict | None = None,
           cache_segments: list[str] | None = None,
           model: str | None = None,  # None → adapter picks director-or-assembler default
       ) -> ProviderResponse: ...

   class ProviderError(Exception): ...
   class ProviderRateLimitError(ProviderError): ...
   class ProviderAuthError(ProviderError): ...
   class ProviderNetworkError(ProviderError): ...
   class CacheMissError(ProviderError): ...  # stub only
   ```

2. **Shared utilities (in `base.py`).**
   - `cache_key(cache_segments, model_id, schema_hash) -> str`: `sha256("\n---\n".join(cache_segments) + model_id + schema_hash)`. Used identically across providers so the stub and real adapters key the same way.
   - `default_model(role: Literal["director", "assembler"], provider: str) -> str`: reads `FRAMECRAFT_{role.upper()}_MODEL` env, else provider default.

3. **`gemini.py` — default provider.**
   - SDK: `google-genai` (`from google import genai`).
   - Client: `genai.Client(api_key=os.environ["GEMINI_API_KEY"] or os.environ["GOOGLE_API_KEY"])`. Missing key → `ProviderAuthError("Set GEMINI_API_KEY or GOOGLE_API_KEY to use the Gemini provider")`.
   - Defaults: director = `gemini-2.5-pro`, assembler = `gemini-2.5-flash`.
   - `complete(...)`:
     1. Compute `key = cache_key(cache_segments, model, SCHEMA_HASH)`.
     2. Look up `key` in `~/.cache/framecraft/gemini-caches.json`. If found and not expired (check `expires_at`, treat expired as miss), reuse the cache name.
     3. If miss: `client.caches.create(model=model, config=types.CreateCachedContentConfig(system_instruction=system, contents=cache_segments_as_parts, ttl="3600s"))`. Persist the returned cache `name` with `expires_at = now() + 3500s` into the JSON.
     4. Call `client.models.generate_content(model=model, contents=messages_as_parts, config=GenerateContentConfig(cached_content=cache_name, response_mime_type="application/json" if schema else None, response_schema=schema, temperature=0))`.
     5. Wrap response into `ProviderResponse`. Populate `cache_read_tokens` from `usage_metadata.cached_content_token_count`.
   - **Warmup asymmetry.** Gemini needs an explicit `caches.create` *before* the first completion. Stash all that behind `complete`; callers never know.
   - Error mapping: `google.genai.errors.ClientError` with 401/403 → `ProviderAuthError`; 429 → `ProviderRateLimitError`; connection errors → `ProviderNetworkError`.
   - Cache-file concurrency: use a simple POSIX advisory lock (`fcntl.flock`) around the read-modify-write; don't invent a database.

4. **`anthropic.py` — alternate provider.**
   - SDK: `anthropic`.
   - Client: `anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])`. Missing key → `ProviderAuthError`.
   - Defaults: director = `claude-opus-4-7`, assembler = `claude-sonnet-4-6`.
   - `complete(...)`:
     1. Build `system` blocks: each element of `cache_segments` becomes a dict `{"type": "text", "text": seg}`, and append `{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}` as the final cached block. This makes the stable prefix cacheable.
     2. `client.messages.create(model=model, system=system_blocks, messages=user_messages, max_tokens=4096, temperature=0)`.
     3. If `schema` was passed: Anthropic has no native JSON-schema mode; inject `"Respond with a single JSON object matching this schema: <schema>. Output only the JSON, no prose."` into the system prompt (in the uncached trailing segment). Parse `response.content[0].text` as JSON.
     4. Wrap. Populate `cache_read_tokens` from `response.usage.cache_read_input_tokens`, `cache_write_tokens` from `response.usage.cache_creation_input_tokens`.
   - Error mapping: `anthropic.AuthenticationError` → `ProviderAuthError`; `anthropic.RateLimitError` → `ProviderRateLimitError`; `anthropic.APIConnectionError` → `ProviderNetworkError`.

5. **`stub.py` — deterministic test double.**
   - `StubProvider(fixture_dir: Path, provider_name: str)`: `fixture_dir` defaults to `tests/fixtures/llm/<provider_name>/`.
   - `complete(...)`:
     1. Compute `fixture_key = sha256((provider_name + system + json.dumps(messages, sort_keys=True) + json.dumps(schema or {}, sort_keys=True)).encode()).hexdigest()[:16]`.
     2. Read `fixture_dir / f"{fixture_key}.json"`. On miss → `CacheMissError(f"No fixture for {fixture_key}. Record with --update-fixtures.")`.
     3. Return the fixture's `ProviderResponse` verbatim. Fixture file format:
        ```json
        {
          "text": "...",
          "parsed": {...},
          "input_tokens": 1234,
          "output_tokens": 456,
          "cache_read_tokens": 1200,
          "cache_write_tokens": 0,
          "provider": "stub:gemini",
          "model": "gemini-2.5-pro",
          "elapsed_ms": 0,
          "request_snapshot": { "system_hash": "...", "messages": [...], "schema_hash": "..." }
        }
        ```
   - `request_snapshot` lets a human audit fixtures without running the system.

6. **`__init__.py` — factory.**
   ```python
   def make_provider(name: str | None = None, *, fixture_dir: Path | None = None) -> LLMProvider:
       name = name or os.environ.get("FRAMECRAFT_PROVIDER", "gemini")
       match name:
           case "gemini": return GeminiProvider()
           case "anthropic": return AnthropicProvider()
           case "stub": return StubProvider(fixture_dir or Path("tests/fixtures/llm/gemini"), "stub:gemini")
           case "stub:anthropic": return StubProvider(Path("tests/fixtures/llm/anthropic"), "stub:anthropic")
           case other: raise ValueError(f"Unknown FRAMECRAFT_PROVIDER={other}")
   ```
   - `compose()` kwarg path passes an explicit instance, bypassing env var — see [`06a-cli-core.md`](./06a-cli-core.md).

7. **Prompt wrapper layering (`framecraft/prompts/`).**
   - `prompts/common/director.md` — shared body: role, archetype list, block registry placeholder, schema placeholder, instructions.
   - `prompts/gemini/director.md` — wraps common with: *"Output only the JSON object. Do not wrap in code fences."* (Gemini with `response_mime_type=application/json` does this already but be explicit for robustness.)
   - `prompts/anthropic/director.md` — wraps common with: *"Respond with only a JSON object matching the schema. No prose, no code fences."*
   - Assembler polish prompts follow the same pattern.
   - At runtime, Director and Assembler select the wrapper by `provider.name.split(":")[-1]`. Fallback to `gemini` wrapper for unknown names.

8. **Record/replay helper (for fixture authoring).**
   - `scripts/record_fixture.py <provider> <fixture_name>`: runs a real provider and writes a stub fixture. Not a plan-blocker; used by 07 for goldens.

9. **Security posture (§7.5).**
   - Never log API keys, even in tracebacks (use `repr(client)` only when `client` has no key attribute; SDK clients already redact).
   - Trace files never include the full `system` prompt verbatim — store `sha256(system)` plus per-segment hashes. The request itself can include the plaintext `user` message since that's user-supplied data they already have locally.
   - `~/.cache/framecraft/gemini-caches.json` stores only cache *names* (Google-side IDs) + hashes — not API keys, not prompt content.

## Testing strategy

- **Unit (`tests/test_providers.py`).**
  - Stub round-trip: fixture hit returns matching `ProviderResponse`; fixture miss raises `CacheMissError`.
  - `cache_key` stable across processes (subprocess run asserts same hash).
  - Gemini adapter with mocked `genai.Client` (record interactions, assert `cached_content` is populated on second call).
  - Anthropic adapter with mocked `anthropic.Anthropic` (assert `cache_control` appears on system blocks).
  - Env-var selection: `FRAMECRAFT_PROVIDER=stub` → `StubProvider` returned by factory.
  - Missing key → `ProviderAuthError` with the correct env var name in the message.
- **Integration (skipped by default, `@pytest.mark.llm`).**
  - One Gemini call with real key — assert `cache_read_tokens > 0` on second call.
  - One Anthropic call with real key — assert `cache_read_input_tokens > 0` on second call.
- **Fixture hygiene.** A CI lint ensures no fixture file is larger than 64 KB (trim response text if needed).

## Acceptance (PRD bullets closed)

- US-013: all AC bullets.
- FR-6 (provider-appropriate caching).
- FR-7 (Gemini default, Anthropic alternate, env-var overrides).
- §7.5 key handling (entirely closed here; 07 cross-links for trace hashes).

## Open questions

- **OQ-F2.1** When the Gemini cache object is ~1h old but the current call is still in the same `compose` invocation, should we proactively recreate to avoid mid-run expiry? *Leaning: yes, if `expires_at - now() < 120s`, recreate before the next call. Cheap insurance.*
- **OQ-F2.2** Anthropic has 4 cache breakpoints available; v1 uses 1 (at the end of stable prefix). Do we pre-partition primer/registry/schema into 3 breakpoints to enable partial invalidation (e.g., registry changes but primer doesn't)? *Leaning: no for v1; overhead > savings until the registry churns weekly.*

## Verification

```bash
# Stub path, no keys needed:
FRAMECRAFT_PROVIDER=stub python -c "
from framecraft.providers import make_provider
p = make_provider()
print(p.name)
# will raise CacheMissError since there's no fixture yet — expected
"

# Real Gemini, end-to-end caching:
GEMINI_API_KEY=... python - <<'PY'
from framecraft.providers import make_provider
p = make_provider("gemini")
r1 = p.complete([{"role": "user", "content": "Reply with {\"ok\": true}"}], system="You are helpful.", cache_segments=["Hyperframes primer v1..."] * 1, schema={"type": "object", "properties": {"ok": {"type": "boolean"}}})
r2 = p.complete([{"role": "user", "content": "Reply with {\"ok\": true}"}], system="You are helpful.", cache_segments=["Hyperframes primer v1..."] * 1, schema={"type": "object", "properties": {"ok": {"type": "boolean"}}})
assert r2.cache_read_tokens > 0, "cache miss on second call — investigate"
print("cache read tokens:", r2.cache_read_tokens)
PY

pytest tests/test_providers.py -v
pytest tests/test_providers.py -v -m llm  # only with live keys
```
