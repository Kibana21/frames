# FrameCraft Implementation Plan — Index

> **Status:** skeleton. Matrices and pinned sections populate after the nine subsystem plans are written.

## 1. Summary

FrameCraft is a Python 3.11+ package that turns a natural-language *situation* into a valid Hyperframes project (HTML compositions + `plan.json`) and optionally renders an MP4. The PRD lives at [`../tasks/prd-framecraft.md`](../tasks/prd-framecraft.md); this directory decomposes it into nine implementation subsystem plans plus this index.

**Core architectural bet:** a typed, human-editable `SceneGraph` artifact (`plan.json`) flows between a **Director** (situation → plan) and an **Assembler** (plan → HTML). Two shipped LLM providers — **Gemini** (default) and **Anthropic** — are swappable via one env var.

## 2. Plan files

| File | Scope | Primary US / FR |
| --- | --- | --- |
| [`01-schema-and-registry.md`](./01-schema-and-registry.md) | Pydantic types, Aspect/Provenance enums, BlockSpec, registry discovery, music validator | US-002, US-003, part of US-016 |
| [`02-providers.md`](./02-providers.md) | `LLMProvider` protocol, Gemini + Anthropic + stub adapters, cache mechanics, key handling | US-013, §7.5 |
| [`03-director.md`](./03-director.md) | Archetype classification + scene planning, retry, fallback swap, trace format | US-004 |
| [`04-assembler.md`](./04-assembler.md) | NATIVE Jinja + CATALOG slots, catalog hash lifecycle, file contract, polish cache, audio injection | US-005, §6.2.5, §6.9, §6.4, part of US-016 |
| [`05-scaffold-lint-repair.md`](./05-scaffold-lint-repair.md) | Scaffold via `npx hyperframes init`, CLI version pin, gitignore, lint rule classification, repair pass | US-006, US-007 |
| [`06a-cli-core.md`](./06a-cli-core.md) | `compose`, `from-plan`, `doctor`, `catalog`, config loading, branding flags, exit codes | US-001, US-008, US-009, part of US-010 |
| [`06b-cli-shellouts.md`](./06b-cli-shellouts.md) | `render`, `preview`, `--music` plumbing | US-011, US-015, CLI part of US-016 |
| [`07-observability-and-goldens.md`](./07-observability-and-goldens.md) | Trace aggregation, golden fixtures per provider, perf harness | US-012, US-014, §7.3 |
| [`08-primer-snapshot.md`](./08-primer-snapshot.md) | Build-time upstream doc snapshot + drift check | FR-16 |

## 3. Build / dependency order

```
01-schema-and-registry
   └─► 02-providers
           └─► 08-primer-snapshot
                   ├─► 03-director  ──┐
                   └─► 05-scaffold   ─┤
                                      └─► 04-assembler
                                              └─► 06a-cli-core
                                                     └─► 06b-cli-shellouts
                                                             └─► 07-observability-and-goldens
```

`03` and `05` parallelize after `08` is done. `07` is last because its golden fixtures bind against the stub format frozen by `02` + the trace schemas emitted by `03`/`04`/`05`.

## 4. Milestone → file deliverables matrix

| Milestone | PRD target (§9) | Plan files involved | Cut-line |
| --- | --- | --- | --- |
| **M0 — Walking skeleton (1–2d)** | `compose --dry-run` emits hand-written plan, runs Assembler with one hard-coded block | 01 (minimal types), 02 (stub only), 06a (typer + dry-run) | Skip Director, skip lint, skip real providers |
| **M1 — MVP (1w)** | Product-promo archetype end-to-end, no branding | Full 01, 02, 08; 03; 04 NATIVE only; full 05; 06a `compose` | Skip CATALOG blocks, skip branding flags, skip `from-plan` |
| **M2 — Archetypes + branding (3–4d)** | All 5 archetypes, all blocks, branding | 04 CATALOG path + all blocks, 03 full archetype coverage, 06a branding plumbing, 06b partial, 07 fixture authoring | Skip perf harness, skip `--update-goldens` |
| **M3 — Polish (2–3d)** | `from-plan`, `render`, `preview`, traces, goldens, docs | 06a `from-plan` closing, 06b `render`/`preview`/`--music`, 07 trace aggregation + `--update-goldens` + perf, 08 drift check finalized | — |

**Subtle point:** trace *format* is frozen in M1 (03/04/05 emit defined JSON shapes). Trace *aggregation and perf assertions* land in M3 (via 07). Reading the plan tree this looks like a split, but it isn't — format is a contract, aggregation is tooling over it.

## 5. Determinism contract (pinned)

Byte-identical HTML for the same `plan.json`. Enforced as follows:

| Invariant | Enforced in | How |
| --- | --- | --- |
| No `Date.now()`, no `uuid4()`, no `Math.random()` in generated HTML | **04** | Template authorship rule; linter catches regressions |
| Element IDs derived from scene index + block-local counters (`scene-02-chart-bar-3`) | **04** | Jinja helper `fc_id(scene_idx, local)` |
| `SceneGraph → HTML` is a pure function | **04** | NATIVE rendering is pure Jinja; CATALOG slot injection is a deterministic HTML walk |
| `llm_polish` outputs persisted to `scene.polished` on first assembly | **04** writes; **01** types; **06a** re-reads in `from-plan` | Second and subsequent assemblies read from cache, never re-call LLM unless input changed |
| Golden tests use stub provider, so even first assembly is deterministic in CI | **07** | Stub reads recorded fixtures |
| `hash(system, registry, schema)` stable across runs | **01** (`model_json_schema()` round-trips) | Pydantic v2 deterministic JSON schema output |

**Consequence:** the *first* live-provider run of a fresh plan is non-deterministic; every run after that is byte-identical. Goldens are stable in CI because they force the stub.

## 6. Error model (pinned)

Canonical exit-code table — other plans link here.

| Exit | Meaning | Origin plan | Artifact on disk |
| --- | --- | --- | --- |
| `0` | Success | all | `plan.json`, `index.html`, optional MP4 |
| `1` | Usage / config error (missing `npx`, missing API key, dirty out dir, CLI version floor, bad `--palette`, `catalog_hash` mismatch) | 05, 06a, 04 | stderr only |
| `2` | Lint failed after repair, or FRAMECRAFT_BUG class on first lint | 05 | `.framecraft/lint-report.json` |
| `3` | Render failed | 06b | `.framecraft/render-stderr.log` |
| `4` | LLM / provider error (rate limit, network, credits) | 02, 03, 04 | `.framecraft/director-trace.json` if reached |

**Rule:** a plan file that introduces a new failure mode must map it to an existing exit code in this table. New codes require updating this table *and* §7.4 of the PRD, not a drive-by in one plan file.

## 7. US × plan coverage matrix

| US | Plan file(s) | Notes |
| --- | --- | --- |
| US-001 Package skeleton | 06a | pyproject, typer wiring, `doctor` |
| US-002 `SceneGraph` schema | 01 | Pydantic v2 + validators |
| US-003 Block registry | 01 | BlockSpec + SlotSpec + Provenance + discovery |
| US-004 Director | 03 | Two-pass prompt, trace |
| US-005 Assembler | 04 | NATIVE + CATALOG, polish cache |
| US-006 Scaffolder | 05 | `npx hyperframes init` wrapper |
| US-007 Lint-repair | 05 | FRAMECRAFT_BUG vs LLM_REPAIRABLE |
| US-008 `compose` | 06a | Happy path orchestration |
| US-009 `from-plan` | 06a | Skip Director, use polish cache |
| US-010 Branding | 06a (flag parsing) + 04 (template honoring) | Split |
| US-011 `render` | 06b | Pass-through |
| US-012 Observability | 03, 04, 05, 06b emit; 07 aggregates | Format defined by emitters; aggregation owned by 07 |
| US-013 `LLMProvider` | 02 | Protocol + 2 live adapters + stub |
| US-014 Golden tests | 07 | Three fixtures × two providers |
| US-015 `preview` | 06b | Pass-through |
| US-016 `--music` | 01 (validator) + 04 (injection) + 06b (flag) | Split across three |

No orphans.

## 8. FR × plan coverage matrix

| FR | Plan file | FR | Plan file |
| --- | --- | --- | --- |
| FR-1 produce project dir | 04, 05 | FR-9 CLI surface | 06a, 06b |
| FR-2 archetype classification | 03 | FR-10 brand flags honored | 06a + 04 |
| FR-3 block subset per archetype | 03 | FR-11 always-write traces | 07 (FR); emitters 03/04/05 |
| FR-4 lint ≤1 repair pass | 05 | FR-12 deterministic IDs + polish cache | 04 + 01 |
| FR-5 reproducible from-plan | 06a + 04 | FR-13 provenance + catalog hash lifecycle | 04 (mechanism) + 01 (schema) + 06a (`doctor --snapshot`) |
| FR-6 provider-appropriate caching | 02 | FR-14 Aspect → pixel dimensions | 01 + 04 (emission) |
| FR-7 default Gemini, alternate Claude | 02 | FR-15 `<template>` wrapping contract | 04 |
| FR-8 scaffold via `hyperframes init` | 05 | FR-16 primer snapshot | 08 |

No orphans.

## 9. Open Questions — routing

| OQ | Status | Owning plan | Proposed resolution |
| --- | --- | --- | --- |
| OQ-1 Copy polish scope | Open | 03 (Director marks fields), 04 (Assembler executes) | Opt-in per field via `llm_polish=True`; revisit defaults under Gemini Flash pricing |
| OQ-2 Aspect-ratio pivot | Open | 03 (swap logic), 01 (`fallback_block_id` field) | Auto-swap when `aspect_preferred` excludes plan aspect and a fallback is declared; else re-prompt Director; else hard-fail |
| OQ-3 Transition semantics | **Resolved 2026-04-18** | 04 implements | `transitions: list[TransitionCue]`; absolute `data-start` emission |
| OQ-4 `framecraft.yaml` location | Open | 06a | Both — project-local wins over user-global |
| OQ-5 Offline CI determinism | Open | 07 | Stub provider is sufficient; revisit only if fixtures go flaky |
| OQ-6 Multi-composition variants | Open (post-v1) | — | Defer to post-v1 `--variants 16:9,9:16,1:1` |

## 10. Cross-cutting invariants — canonical home

| Concern | Canonical location | Cross-referenced from |
| --- | --- | --- |
| Determinism contract | §5 of this file | 01, 03, 04, 07 |
| Error model | §6 of this file | 05, 06a, 06b, 07 |
| Security / key handling | 02-providers.md | 07 (one sentence) |
| Performance targets (§7.3 PRD) | 07-observability-and-goldens.md | — |
| Provenance mechanics | 04-assembler.md | 01 (schema reference) |
| Generated file contract (§6.9 PRD) | 04-assembler.md | 00 (§5), 05 (lint policy) |
| Prompt caching warmup asymmetry | 02-providers.md | 08 (primer segment) |

## 11. Tooling vs runtime

- **Runtime (ships in wheel):** everything under `framecraft/`. Never makes network calls for docs, primers, or catalogs during `compose`. Catalog blocks installed at scaffold time via `npx` and hash-verified; LLM calls are the only inherent network dependency and they're the configured provider's API only.
- **Build-time (not shipped, in `scripts/`):** `snapshot_primer.py` fetches upstream docs; `doctor` re-fetches on demand to check drift. See [`08-primer-snapshot.md`](./08-primer-snapshot.md).

## 12. How to read this directory

- Pick a subsystem → open the matching `NN-*.md`.
- Looking at a milestone cut → §4 table.
- Chasing a US or FR → §7 or §8 matrix.
- Looking for cross-cutting invariants → §5, §6, §10.
- Never duplicate pinned content. If you find yourself restating §5 or §6 in a subsystem plan, cross-link instead.
