# FrameCraft

Turn a natural-language situation into a valid Hyperframes project.

```bash
pip install -e .
framecraft compose "30-second promo for ShieldMax, an AI health insurance app" --render
```

See [`.claude/tasks/prd-framecraft.md`](../.claude/tasks/prd-framecraft.md) and [`.claude/plans/`](../.claude/plans/) for the full design.

## M0 walking skeleton (current)

- `framecraft --help` — top-level CLI
- `framecraft doctor` — environment check
- `framecraft compose "<situation>" --dry-run --out <dir>` — emits a hand-written plan and assembles one hard-coded scene

Director (LLM planning), real providers, lint-repair, and catalog blocks arrive in M1–M3.
