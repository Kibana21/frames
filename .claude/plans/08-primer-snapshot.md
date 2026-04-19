# 08 — Primer Snapshot (Build-time Tooling)

## Goal

Build and maintain the Hyperframes "primer" — the cached stable-prefix text embedded in every Director call's system prompt. Snapshot upstream documentation at build time, ship inside the wheel, verify against upstream on demand. **Runtime never fetches the primer over the network** (FR-16).

## Inputs

- PRD FR-16; §6.5 prompt caching.
- Upstream docs index: `https://hyperframes.heygen.com/llms.txt`.
- Five minimum source pages:
  - `concepts/compositions.md`
  - `concepts/data-attributes.md`
  - `reference/html-schema.md`
  - `guides/gsap-animation.md`
  - `guides/common-mistakes.md`

## Outputs

- `framecraft/prompts/primer.md` — concatenated upstream content, shipped in wheel.
- `framecraft/prompts/primer.lock.json` — manifest of `{url, sha256, fetched_at}` per source, shipped in wheel.
- `scripts/snapshot_primer.py` — build-time script, *not* shipped.
- Drift check wired into `framecraft doctor` (06a).

## Critical files

| Path | Purpose |
| --- | --- |
| `scripts/snapshot_primer.py` | Build-time fetcher/concatenator |
| `framecraft/prompts/primer.md` | Committed snapshot |
| `framecraft/prompts/primer.lock.json` | Committed manifest |
| `framecraft/prompts/__init__.py` | `load_primer() -> str` helper |
| `framecraft/prompts/_drift.py` | Drift-check function imported by `doctor` |
| `tests/test_primer.py` | Asserts `load_primer()` is non-empty and matches lockfile hashes |

## Dependencies

- 02 — primer becomes a `cache_segment` in Director calls; schema agreed on (plain string segment is enough, no contract needed).

## Implementation steps

1. **Source manifest** (`scripts/snapshot_primer.py`, top of file).
   ```python
   SOURCES = [
       "https://hyperframes.heygen.com/concepts/compositions.md",
       "https://hyperframes.heygen.com/concepts/data-attributes.md",
       "https://hyperframes.heygen.com/reference/html-schema.md",
       "https://hyperframes.heygen.com/guides/gsap-animation.md",
       "https://hyperframes.heygen.com/guides/common-mistakes.md",
   ]
   ```
   A single constant; reviewers diff this list to decide what enters the primer.

2. **Fetcher.**
   - Use `httpx` (already in PRD §7.1 deps). Timeout 15s per URL.
   - Fail hard on any non-200 — the snapshot script must be reproducible or not run at all.
   - Do not strip formatting — Markdown round-trips into the system prompt unmodified.

3. **Concatenation and framing.**
   - Each source prefixed with:
     ```
     ## [<url>]
     <body>

     ---
     ```
   - Final file header: `# Hyperframes Primer (snapshot)` + `> Generated from upstream docs; do not edit by hand. Re-run \`python scripts/snapshot_primer.py\`.`
   - Trailing footer with the total SHA-256 of the concatenated body for quick eyeball.

4. **Lockfile.**
   ```json
   {
     "generated_at": "2026-04-18T12:00:00Z",
     "schema": 1,
     "sources": [
       {
         "url": "https://hyperframes.heygen.com/concepts/compositions.md",
         "sha256": "abcd...",
         "bytes": 12345,
         "fetched_at": "2026-04-18T12:00:00Z"
       },
       ...
     ],
     "primer_sha256": "ef12..."
   }
   ```
   - `primer_sha256` is the hash of the generated `primer.md`. `doctor` uses this to detect local tampering.

5. **Script CLI.**
   - `python scripts/snapshot_primer.py` — writes primer + lock, non-zero exit on any fetch failure.
   - `python scripts/snapshot_primer.py --check` — re-fetches, compares hashes against the lockfile, exits 0 on no drift, 1 on drift. Used by `doctor` and CI.
   - `python scripts/snapshot_primer.py --diff` — shows per-source unified diff against local. Human-facing.

6. **Runtime loader (`framecraft/prompts/__init__.py`).**
   ```python
   _PRIMER: str | None = None

   def load_primer() -> str:
       global _PRIMER
       if _PRIMER is None:
           path = Path(__file__).parent / "primer.md"
           _PRIMER = path.read_text(encoding="utf-8")
       return _PRIMER
   ```
   - `lru_cache` alternative rejected — explicit sentinel is clearer.
   - Never touches network; no `httpx` import in this module.

7. **Drift check (`framecraft/prompts/_drift.py`).**
   ```python
   def check_drift(timeout: float = 15.0) -> DriftReport:
       """Re-fetch manifest URLs, compare hashes to lockfile.
       Returns a report suitable for `framecraft doctor` rendering."""
   ```
   - Offline (no network)? Return `DriftReport(status="offline", sources_checked=0)`. Don't fail `doctor`.
   - Online but a URL 4xx/5xx? Report per-source error; don't abort whole check.
   - Drift detected? Report `sources_changed: list[{url, old_sha256, new_sha256}]` with instruction `Run: python scripts/snapshot_primer.py`.

8. **CI wiring.**
   - A scheduled GitHub Action (weekly) runs `python scripts/snapshot_primer.py --check`; non-zero opens an issue with the diff. Details in 07 (CI section) but the script lives here.

9. **Wheel inclusion.**
   - `pyproject.toml` `[tool.setuptools.package-data]` includes `framecraft/prompts/primer.md` and `framecraft/prompts/primer.lock.json`. Owner: 06a, but reviewer should check that 06a adds these explicitly.

10. **Size budget.**
    - Primer should fit in 12k tokens (≈ 48 KB Markdown). If upstream balloons, trim in `snapshot_primer.py` via a per-source `max_bytes` allowlist — do not just concatenate indefinitely. Add logging when any single source exceeds 16 KB.

## Testing strategy

- **Unit (`tests/test_primer.py`).**
  - `load_primer()` returns non-empty string containing `Hyperframes Primer`.
  - Computed `sha256(load_primer())` equals `lock.primer_sha256`. This is the wheel's integrity self-check.
  - Per-source bytes recorded in lock match actual substring lengths in primer (sanity).
- **Unit (`tests/test_primer_drift.py`).** Mock `httpx.get` → assert `check_drift` reports no drift when hashes match, reports drift when they don't, reports offline when `httpx` raises `ConnectError`.
- **No network in test suite.** All tests use mocks; the live-network check is a separate scripted CI job.

## Acceptance (PRD bullets closed)

- FR-16 (all of it).

## Open questions

- **OQ-F8.1** Should the primer be compiled into a Python string constant at wheel-build time (zero filesystem I/O at runtime) rather than read from disk on first use? *Leaning: no — disk read is fast, and keeping it as a file makes `doctor` drift reports and human inspection trivial.*
- **OQ-F8.2** Do we want an escape hatch `FRAMECRAFT_PRIMER_PATH` env var for local primer overrides (useful for testing upstream doc changes before merging)? *Leaning: yes — gated with a `WARNING: using override primer` log line so it can't silently affect cost metrics.*

## Verification

```bash
# Generate:
python scripts/snapshot_primer.py
ls framecraft/prompts/
# → primer.md  primer.lock.json  (plus directories)

# Integrity:
python -c "
import hashlib, json, pathlib
root = pathlib.Path('framecraft/prompts')
lock = json.loads((root / 'primer.lock.json').read_text())
actual = hashlib.sha256((root / 'primer.md').read_bytes()).hexdigest()
assert actual == lock['primer_sha256'], 'wheel integrity: primer_sha256 mismatch'
print('ok')
"

# Drift check:
python scripts/snapshot_primer.py --check
echo $?  # 0 if up to date, 1 otherwise

# Diff (human):
python scripts/snapshot_primer.py --diff | less

# Offline loader:
python -c "from framecraft.prompts import load_primer; print(load_primer()[:200])"

pytest tests/test_primer.py tests/test_primer_drift.py -v
```
