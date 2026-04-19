# 05 — Scaffold + Lint-Repair

## Goal

Two tightly related subsystems with opposite shapes. **Scaffolder** wraps `npx hyperframes init` to produce a blank project directory and patches it for FrameCraft (exit 1 on failure). **Lint-repair** runs `npx hyperframes lint --json`, classifies errors, and performs at most one Assembler repair pass on content-shaped errors (exit 2 on abort). Sharing a plan because they share subprocess plumbing, error model, and CLI-version-floor checks.

## Inputs

- PRD US-006, US-007; §7.6 upstream CLI drift risk; §7.4 exit codes 1 and 2.
- `SceneGraph`, `BlockRegistry` from [`01-schema-and-registry.md`](./01-schema-and-registry.md).
- `LLMProvider` from [`02-providers.md`](./02-providers.md) (lint-repair's LLM_REPAIRABLE branch calls into Assembler which calls the provider).
- Hyperframes CLI output format — empirically observed from the three existing projects.

## Outputs

- `framecraft/scaffold.py` — `scaffold(out_dir: Path) -> None`.
- `framecraft/lint.py` — `lint_repair(out_dir: Path, assembler: Assembler) -> LintResult`.
- `framecraft/lint_policy.py` — rule classification table (FRAMECRAFT_BUG vs LLM_REPAIRABLE).
- `framecraft/_compat.py` — pinned CLI version floor and expected-files list.

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/scaffold.py` | Wrap `npx hyperframes init` |
| `framecraft/lint.py` | Lint invocation + repair loop |
| `framecraft/lint_policy.py` | Rule classification |
| `framecraft/_compat.py` | CLI version floor, expected-init files |
| `framecraft/subprocess_helpers.py` | Shared `run_npx(args, cwd)` with `ProviderError`-like mapping |
| `tests/test_scaffold.py` | Unit tests: mocked `subprocess.run` |
| `tests/test_lint_policy.py` | Unit tests: every rule classified |
| `tests/test_lint_repair.py` | Integration: inject broken fixtures, assert repair outcomes |

## Dependencies

- 01 (for `SceneGraph.duration` reference during file-patch step, and `REGISTRY` for consistency checks).
- 02 (indirectly — Assembler's `llm_polish` uses the provider; 05 itself doesn't).
- 04 — **circular** at first glance: lint-repair calls Assembler; Assembler doesn't call lint. Break the cycle by having `lint_repair` accept an `assembler: Assembler` parameter injected by the CLI orchestrator in 06a.

## Implementation steps

### Scaffolder

1. **Subprocess helper (`subprocess_helpers.py`).**
   ```python
   def run_npx(args: list[str], *, cwd: Path, timeout: float = 180) -> CompletedProcess[str]:
       """Run `npx <args>` capturing stdout/stderr as text. Raises ToolchainError on
       binary missing or non-zero exit, with stderr as message."""
   ```
   - If `shutil.which("npx") is None` → `ToolchainError("npx not found. Install Node.js.")` → caller maps to exit 1.
   - Non-zero exit → `ToolchainError(cmd=..., returncode=..., stderr=...)`.
   - Exit 1 artifact: stderr only, per §7.4.

2. **Version floor (`_compat.py`).**
   ```python
   HYPERFRAMES_VERSION_FLOOR = "0.8.0"  # bumped deliberately
   EXPECTED_INIT_FILES = {"index.html", "hyperframes.json", "meta.json", ".gitignore"}
   EXPECTED_INIT_DIRS = {"compositions"}
   ```
   - `check_cli_version(run_npx) -> str`: runs `npx hyperframes --version`, parses semver, fails if below floor.

3. **Scaffold function.**
   ```python
   def scaffold(out_dir: Path) -> None:
       if out_dir.exists() and any(out_dir.iterdir()):
           # allow if it looks like an existing FrameCraft project (has .framecraft/)
           if not (out_dir / ".framecraft").exists():
               raise ToolchainError(f"{out_dir} exists and is not empty")
       out_dir.parent.mkdir(parents=True, exist_ok=True)
       check_cli_version(run_npx)
       run_npx(["hyperframes", "init", str(out_dir), "--example", "blank", "--non-interactive", "--skip-transcribe"], cwd=out_dir.parent)
       _verify_init_output(out_dir)
       _patch_gitignore(out_dir)
   ```
   - `_verify_init_output`: diff actual `out_dir` contents against `EXPECTED_INIT_FILES` + `EXPECTED_INIT_DIRS`. Extra files fine (upstream may add). Missing files → `ToolchainError` mentioning upstream CLI drift.
   - `_patch_gitignore`: append `.framecraft/` and `renders/` if not already present. Idempotent.

4. **What scaffold does NOT do.**
   - Does not overwrite `index.html` or `compositions/*` — those belong to 04's Assembler.
   - Does not write `meta.json` content — Assembler replaces it.
   - Does not emit `hyperframes.json` — FrameCraft never does (FR-8).

### Lint-repair

5. **Lint invocation (`lint.py`).**
   ```python
   def run_lint(out_dir: Path) -> LintReport:
       p = run_npx(["hyperframes", "lint", "--json"], cwd=out_dir)
       return LintReport.model_validate_json(p.stdout)
   ```
   - `LintReport` is a Pydantic model mirroring the CLI's JSON output. Fields observed from `docs troubleshooting`:
     ```python
     class LintFinding(BaseModel):
         rule: str           # e.g. "duplicate-composition-id"
         severity: Literal["error", "warning", "info"]
         file: str
         line: int | None
         message: str
         details: dict = {}

     class LintReport(BaseModel):
         errors: list[LintFinding]
         warnings: list[LintFinding]
         info: list[LintFinding] = []
     ```

6. **Rule classification (`lint_policy.py`).**
   ```python
   FRAMECRAFT_BUG_RULES: set[str] = {
       "duplicate-composition-id",
       "missing-timeline-registration",
       "invalid-composition-src-path",
       "duplicate-element-id",
       "missing-template-wrapper",       # our FR-15 violation
       "missing-data-width",              # our FR-14 violation
       "missing-data-height",
       "clip-class-on-video",             # schema violation per html-schema.md
       "clip-class-on-audio",
   }

   LLM_REPAIRABLE_RULES: set[str] = {
       "copy-too-long",
       "missing-clip-class",               # content introduced missing class
       "inconsistent-data-start-reference",
       "unknown-media-path",                # asset path introduced by polish
   }

   def classify(finding: LintFinding) -> Literal["framecraft_bug", "llm_repairable", "unknown"]:
       ...
   ```
   - `unknown` = rule not in either set → treat as `framecraft_bug` (fail loud). Forces us to update the table when upstream adds a rule.

7. **Repair loop.**
   ```python
   def lint_repair(out_dir: Path, assembler: Assembler, plan: SceneGraph) -> LintResult:
       report = run_lint(out_dir)
       if not report.errors:
           _print_warnings_once(report.warnings)
           return LintResult(passed=True, repaired=False, report=report)

       framecraft_bugs = [f for f in report.errors if classify(f) == "framecraft_bug"]
       if framecraft_bugs:
           _persist(report, out_dir)
           raise FrameCraftBugError(framecraft_bugs)  # CLI → exit 2

       assembler.repair(out_dir, plan, errors_only=report.errors)
       report2 = run_lint(out_dir)
       if report2.errors:
           _persist(report2, out_dir)
           raise LintFailedAfterRepairError(report2.errors)  # CLI → exit 2

       _print_warnings_once(report2.warnings)
       return LintResult(passed=True, repaired=True, report=report2)
   ```
   - `_persist`: writes `.framecraft/lint-report.json` atomically. FR-11 requires this on failure.
   - `_print_warnings_once`: stdlib `print` to stderr; one-liner per warning with file/rule/message.

8. **Bug vs repair messaging.**
   - `FrameCraftBugError` stderr: *"lint failed with rule(s) classified as FrameCraft template bugs — not an LLM issue.\nOpen an issue with `.framecraft/lint-report.json` attached."*
   - `LintFailedAfterRepairError` stderr: *"lint still failing after one repair pass. See `.framecraft/lint-report.json`."*
   - Both → exit 2.

9. **No LLM calls on FRAMECRAFT_BUG.** Explicit: the Assembler is never invoked on that path. US-007 AC demands proof via stub-provider call count in tests.

### Cross-cutting

10. **Exit code ownership.** Scaffold failures → exit 1. Lint failures (bug class or post-repair) → exit 2. Both surface up to `compose` in 06a.

11. **Upstream drift mitigation (§7.6).** Scaffold's `_verify_init_output` checks expected files; lint-repair's `unknown` classification ensures new lint rules don't silently get treated as LLM-repairable.

12. **`--verbose` propagation.** Scaffold invocation does not use `--verbose`; lint invocation uses `--json` (not `--verbose --json`, to keep output parseable). Warnings come through `--json` already.

## Testing strategy

- **Unit (`tests/test_scaffold.py`).**
  - Mock `subprocess.run`; assert correct argv for `init`.
  - Non-zero return → `ToolchainError`.
  - Missing files post-init → `ToolchainError` mentioning drift.
  - Existing FrameCraft dir (has `.framecraft/`) → scaffold succeeds without error.
- **Unit (`tests/test_lint_policy.py`).** For every rule in `FRAMECRAFT_BUG_RULES ∪ LLM_REPAIRABLE_RULES`, assert `classify()` returns expected class. For an unknown rule, assert `framecraft_bug` classification.
- **Integration (`tests/test_lint_repair.py`).**
  - Scenario A — inject a content-shaped error (e.g. `copy-too-long`) into a known-good project fixture; use `StubAssembler` that deterministically fixes it; assert `lint_repair` returns `repaired=True` and second lint passes.
  - Scenario B — inject duplicate `data-composition-id`; assert `FrameCraftBugError`, exit-code-2 expectation, and **zero calls** to `assembler.repair` (counter on the stub).
  - Scenario C — first lint has warnings only; assert pass-through, warnings printed once.

## Acceptance (PRD bullets closed)

- US-006: all AC.
- US-007: all AC.
- FR-4 (lint ≤1 repair pass).
- FR-8 (scaffold delegates to `npx hyperframes init`).
- §7.6 upstream drift mitigation.

## Open questions

- **OQ-F5.1** Where do we cache the result of `check_cli_version` within a single `compose` run? Currently called on every `scaffold`; if `from-plan` later runs lint, it'd call again. *Leaning: cache in a module-level dict keyed by `cwd`, invalidate on explicit `doctor --refresh`.*
- **OQ-F5.2** Should `lint_repair` offer a `--dry-run` that skips the repair call and just reports the classification? Useful for debugging. *Leaning: yes, trivial to add; ship in M3.*

## Verification

```bash
# Scaffold into an empty dir:
rm -rf /tmp/fc-test && python -c "from framecraft.scaffold import scaffold; scaffold(__import__('pathlib').Path('/tmp/fc-test'))"
ls /tmp/fc-test
# → index.html compositions hyperframes.json meta.json .gitignore

# Scaffold refuses non-empty non-FrameCraft:
mkdir -p /tmp/fc-existing && touch /tmp/fc-existing/unrelated.txt
python -c "from framecraft.scaffold import scaffold; scaffold(__import__('pathlib').Path('/tmp/fc-existing'))"
# → ToolchainError

# Lint happy path (with 04 written):
python -c "
from pathlib import Path
from framecraft.lint import lint_repair
# result = lint_repair(Path('/tmp/fc-test'), assembler, plan) — requires 04
"

pytest tests/test_scaffold.py tests/test_lint_policy.py tests/test_lint_repair.py -v
```
